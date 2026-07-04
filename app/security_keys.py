"""Normalise a configured secret string into raw key bytes.

Two secret formats reach this box in the wild:

  * ``secrets.token_hex(32)`` — 64 hex chars, the format the runbooks tell an
    operator to generate. Historically the audit sink and JWT layer assumed
    *only* this and did ``bytes.fromhex(raw)``.
  * A managed secret from a platform generator — e.g. Render's
    ``generateValue`` / most k8s secret managers emit a **base64** 256-bit
    value. ``bytes.fromhex`` raises ``ValueError`` on that, which used to abort
    boot (a silent deploy footgun: the value *looks* fine, the app just won't
    start).

``normalise_key_bytes`` accepts either:

  * a valid, even-length hex string decodes as-is — byte-identical to the old
    ``bytes.fromhex`` path, so existing hex-keyed deployments and their audit
    chains are unaffected;
  * anything else (base64, a passphrase, …) is hashed to a stable 32-byte key
    with SHA-256 — the same secret always yields the same key.

Because every consumer (settings validator, audit sink, admin verify endpoint)
routes through this one function, they all derive the *same* bytes, so an audit
chain written under one always verifies under the others.
"""

from __future__ import annotations

import hashlib

# Minimum raw-secret length. A token_hex(16) key is 32 chars; a base64 256-bit
# value is ~44 chars — both clear this. Anything shorter is a misconfiguration.
MIN_SECRET_CHARS = 32


def normalise_key_bytes(raw: str, *, min_chars: int = MIN_SECRET_CHARS) -> bytes:
    """Return raw key bytes for ``raw``. Raises ``ValueError`` if too short."""
    raw = raw.strip()
    if len(raw) < min_chars:
        raise ValueError(
            f"secret is too short ({len(raw)} chars); need >= {min_chars}. "
            'Generate with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    # Back-compat: a clean even-length hex string decodes exactly as before.
    if len(raw) % 2 == 0:
        try:
            decoded = bytes.fromhex(raw)
        except ValueError:
            decoded = None
        if decoded is not None and len(decoded) >= 16:
            return decoded
    # Otherwise derive a stable 32-byte key from the high-entropy secret.
    return hashlib.sha256(raw.encode()).digest()


__all__ = ["normalise_key_bytes", "MIN_SECRET_CHARS"]
