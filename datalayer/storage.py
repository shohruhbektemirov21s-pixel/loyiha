"""Secure, content-addressed object store behind ``StorageRef``.

The sensitive-data boundary of the whole system. A scan's bytes are multi-MB and
classified; they must (a) never travel inline in a message, (b) never leave the
secure environment, and (c) be provably the same bytes the model analyzed. This
module is the one place those three guarantees are implemented, so the rest of
the data layer manipulates ``StorageRef`` handles and never raw classified bytes.

The guarantees, each enforced in code rather than documented and hoped for:

* **Content-addressed by plaintext SHA-256.** The address *is* the hash of the
  analyzed bytes — the same hash carried on the wire in ``StorageRef.sha256``.
  Two writes of identical bytes collapse to one blob (natural dedup), and the
  audit log can prove which bytes any hop saw.
* **Encrypted at rest.** Blobs are written through an ``Encryptor`` seam. The
  production path is AES-256-GCM (authenticated) with the key supplied out of
  band (env/KMS), never in code or in the repo.
* **Fail-closed on read.** ``get`` re-hashes the decrypted plaintext and refuses
  to return bytes whose hash or length disagree with the ``StorageRef``. A
  corrupted, swapped, or tampered blob raises — it is never silently returned.
* **No egress, at the type level.** A ``StorageRef`` whose URI is not a local
  ``file://`` (or bare path) into this store's root is rejected. You cannot ask
  this store to fetch ``s3://`` / ``http://`` — the air gap is structural.

This box runs ``DevPassthroughEncryptor`` (loudly non-production: it does NOT
encrypt) so the data layer is exercisable without the crypto stack. The GPU/data
box, which holds real scans, must inject ``AesGcmEncryptor`` — the store does not
default to plaintext-at-rest on its own.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import unquote, urlparse

from contracts.v1 import StorageRef

log = logging.getLogger("xray.datalayer.storage")

# Blob filename suffix. The on-disk name is the content hash; the suffix marks
# "this is a store blob, possibly ciphertext", never a hint about contents.
_BLOB_SUFFIX = ".blob"


class StoreIntegrityError(RuntimeError):
    """Bytes read back do not match their ``StorageRef`` (hash/size/auth).

    Fail-closed: the caller gets this exception, never the suspect bytes.
    """


class EgressRefused(ValueError):
    """A ``StorageRef`` pointed outside the local store — refused, not fetched."""


# ---------------------------------------------------------------------------
# Encryption seam
# ---------------------------------------------------------------------------
@runtime_checkable
class Encryptor(Protocol):
    """At-rest encryption boundary. Production = authenticated AES-GCM."""

    def encrypt(self, plaintext: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...


class DevPassthroughEncryptor:
    """NOT ENCRYPTION. Identity transform for this contract+numpy box only.

    It exists so the data-layer logic (content addressing, fail-closed reads,
    queue extraction) is testable without the crypto stack. It writes plaintext
    to disk and says so, loudly, on construction. Wiring this on a box that holds
    real scans is a misconfiguration — ``SecureImageStore`` requires an explicit
    encryptor precisely so this choice is never made by default.
    """

    def __init__(self) -> None:
        log.warning(
            "DevPassthroughEncryptor active — blobs are NOT encrypted at rest. "
            "Dev/contract box only; inject AesGcmEncryptor where real scans live."
        )

    def encrypt(self, plaintext: bytes) -> bytes:
        return plaintext

    def decrypt(self, ciphertext: bytes) -> bytes:
        return ciphertext


class AesGcmEncryptor:
    """AES-256-GCM at rest. Authenticated: tampering fails ``decrypt`` outright.

    The ``cryptography`` dependency is imported lazily so this module stays
    importable on a box that has neither the wheel nor any real scans to protect.
    The 256-bit key comes from the environment (``from_env``) or is injected — it
    is never read from the repo. Each blob carries a fresh random 96-bit nonce,
    prepended to the ciphertext.
    """

    _NONCE_BYTES = 12

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key.")
        # Lazy import: keep the air-gapped contract box free of the crypto stack.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._aesgcm = AESGCM(key)

    @classmethod
    def from_env(cls, var: str = "XRAY_STORE_KEY") -> "AesGcmEncryptor":
        """Build from a base64-encoded 32-byte key in ``var``. Fail-closed."""
        import base64

        raw = os.environ.get(var)
        if not raw:
            raise ValueError(
                f"{var} is unset — refusing to start the encrypted store without a key."
            )
        return cls(base64.b64decode(raw))

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(self._NONCE_BYTES)
        return nonce + self._aesgcm.encrypt(nonce, plaintext, None)

    def decrypt(self, ciphertext: bytes) -> bytes:
        nonce, body = ciphertext[: self._NONCE_BYTES], ciphertext[self._NONCE_BYTES :]
        return self._aesgcm.decrypt(nonce, body, None)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------
def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 — the content address and the ``StorageRef`` hash."""
    return hashlib.sha256(data).hexdigest()


class SecureImageStore:
    """Local, encrypted, content-addressed blob store. The sensitive-data vault.

    ``put`` returns a ``StorageRef`` (the wire handle). ``get`` is the only way
    bytes come back out, and it is fail-closed. There is deliberately no "list
    all" or "fetch by URL" surface: you can only retrieve bytes you already hold a
    valid, integrity-checked reference to.
    """

    def __init__(self, root: str | Path, encryptor: Encryptor) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._enc = encryptor

    # -- layout ------------------------------------------------------------
    def _blob_path(self, sha256: str) -> Path:
        # Fan out by the first byte so a directory never holds millions of files.
        return self._root / sha256[:2] / f"{sha256}{_BLOB_SUFFIX}"

    def _uri(self, sha256: str) -> str:
        return self._blob_path(sha256).as_uri()

    # -- write -------------------------------------------------------------
    def put(self, plaintext: bytes, *, media_type: str = "image/tiff") -> StorageRef:
        """Persist bytes; return their wire handle. Idempotent by content hash."""
        if not plaintext:
            raise ValueError("Refusing to store empty bytes.")
        digest = sha256_hex(plaintext)
        path = self._blob_path(digest)
        if not path.exists():  # identical content already stored => dedup
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(self._enc.encrypt(plaintext))
            os.replace(tmp, path)  # atomic publish; a half-written blob never appears
        return StorageRef(
            uri=self._uri(digest),
            media_type=media_type,
            sha256=digest,
            size_bytes=len(plaintext),
        )

    # -- read --------------------------------------------------------------
    def _resolve_local(self, ref: StorageRef) -> Path:
        """Map a ``StorageRef`` to an in-root path, or refuse (no egress)."""
        parsed = urlparse(ref.uri)
        if parsed.scheme in ("", "file"):
            raw = unquote(parsed.path) if parsed.scheme == "file" else ref.uri
            path = Path(raw).resolve()
        else:
            raise EgressRefused(
                f"StorageRef URI scheme {parsed.scheme!r} is not local — egress refused."
            )
        # Defense in depth: the resolved path must live under our root.
        if self._root not in path.parents and path != self._root:
            raise EgressRefused(f"StorageRef path escapes the store root: {path}")
        return path

    def exists(self, ref: StorageRef) -> bool:
        try:
            return self._resolve_local(ref).exists()
        except EgressRefused:
            return False

    def get(self, ref: StorageRef) -> bytes:
        """Return the original plaintext for ``ref``, or raise. Fail-closed.

        Verifies decrypted length and SHA-256 against the reference before
        returning a single byte. A mismatch (corruption, swap, tamper) raises
        ``StoreIntegrityError`` — the suspect bytes are never handed back.
        """
        path = self._resolve_local(ref)
        if not path.exists():
            raise StoreIntegrityError(f"Blob for {ref.sha256} is missing at {path}.")
        try:
            plaintext = self._enc.decrypt(path.read_bytes())
        except Exception as exc:  # AES-GCM auth failure included
            raise StoreIntegrityError(f"Decrypt/auth failed for {ref.sha256}: {exc}") from exc
        if len(plaintext) != ref.size_bytes:
            raise StoreIntegrityError(
                f"Size mismatch for {ref.sha256}: ref={ref.size_bytes} got={len(plaintext)}."
            )
        actual = sha256_hex(plaintext)
        if actual != ref.sha256:
            raise StoreIntegrityError(
                f"Hash mismatch: ref says {ref.sha256}, bytes hash to {actual}."
            )
        return plaintext


__all__ = [
    "Encryptor",
    "DevPassthroughEncryptor",
    "AesGcmEncryptor",
    "SecureImageStore",
    "StoreIntegrityError",
    "EgressRefused",
    "sha256_hex",
]
