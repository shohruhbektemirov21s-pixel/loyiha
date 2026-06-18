"""``FrameLoader`` over the local encrypted object store.

The contract never ships image bytes inline — frames carry a ``StorageRef``
(uri + sha256 + size). This resolver turns that reference into pixels and, when
a hash is present, **verifies it**: the audit log must be able to prove which
exact bytes the model saw. A hash mismatch is fail-closed (raise), not a
warning — a frame that doesn't match its manifest is not a frame we score.

cv2 is imported lazily so the module loads on the contract/API box.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    import numpy as np

    from contracts.v1 import StorageRef


class IntegrityError(RuntimeError):
    """Resolved bytes did not match the StorageRef sha256. Fail-closed."""


class ObjectStoreLoader:
    """Resolve ``file://`` (and, when wired, ``s3://``) refs to BGR pixels.

    Parameters
    ----------
    verify_sha256:
        When True (default) and the ref carries a hash, recompute and compare.
        Cheap insurance against a truncated/corrupt object silently scoring as a
        blank frame.
    """

    def __init__(self, *, verify_sha256: bool = True) -> None:
        self._verify = verify_sha256

    def load(self, ref: "StorageRef") -> "np.ndarray":
        import cv2  # lazy
        import numpy as np

        raw = self._read_bytes(ref)

        if self._verify and ref.sha256:
            actual = hashlib.sha256(raw).hexdigest()
            if actual != ref.sha256:
                raise IntegrityError(
                    f"sha256 mismatch for {ref.uri}: manifest {ref.sha256[:12]}…, "
                    f"actual {actual[:12]}…"
                )

        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise IntegrityError(f"could not decode image bytes at {ref.uri}")
        return img

    def _read_bytes(self, ref: "StorageRef") -> bytes:
        parsed = urlparse(ref.uri)
        scheme = parsed.scheme or "file"
        if scheme == "file":
            # file:///var/lib/xray/...  -> /var/lib/xray/...
            path = parsed.path
            with open(path, "rb") as fh:
                return fh.read()
        if scheme == "s3":
            # MinIO/S3 in the air-gapped deployment. Wire boto3/minio here.
            raise NotImplementedError(
                f"s3:// resolution not wired yet ({ref.uri}). "
                "Inject an S3-backed FrameLoader in the composition root."
            )
        raise ValueError(f"unsupported StorageRef scheme {scheme!r} in {ref.uri}")


__all__ = ["ObjectStoreLoader", "IntegrityError"]
