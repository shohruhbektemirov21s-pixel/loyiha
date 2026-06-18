"""Composition root for the detector serving seam.

This is the one place that assembles the concrete ``WeaponsDetector`` from a
flat config and hands it to the FastAPI app (``app.main`` wires it in the
lifespan via ``dependency_overrides``). Keeping the assembly here — not in
``app`` — means the API package never imports the ML stack: this module only
*constructs* ``UltralyticsPredictor`` (whose ultralytics import is deferred to
``__init__``), so importing ``detector.serving.composition`` on a box without a
GPU/torch is safe. The heavy import only fires when ``build_detector`` actually
runs, which only happens when ``XRAY_DETECTOR_ENABLED`` is set — i.e. on the
serving box.

Discipline encoded here:

* **Provenance = what actually ran.** ``weights_sha256`` is hashed from the
  bytes on disk at load time, never trusted from config. The audit log can then
  prove which exact artifact produced a finding (mirrors the frame-integrity
  check in ``image_store``).
* **One source of truth for the emission net.** ``conf``/``iou`` default to
  ``taxonomy.NET_CONF``/``NET_IOU`` so serving cannot silently disagree with
  training and the latency profiler.
* **imgsz is load-bearing.** The static ONNX export fixes the input size; we
  warn loudly if config drifts from it.
* **Honest calibration.** No params file => ``IdentityCalibrator`` (the adapter
  marks ``calibrated=false``); we never claim a calibration we didn't fit.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from contracts.v1 import ModelProvenance

from detector.taxonomy import NET_CONF, NET_IOU
from detector.serving.adapter import WeaponsDetector

log = logging.getLogger("xray.detector.composition")

# Train/export contract: the ONNX is exported static at this size (see
# UltralyticsPredictor.imgsz and detector/README). Drift here moves geometry.
EXPORT_IMGSZ = 1024

# weights extension -> serving runtime label for provenance.
_RUNTIME_BY_EXT = {
    ".onnx": "onnxruntime",
    ".engine": "tensorrt",
    ".plan": "tensorrt",
    ".pt": "pytorch-ultralytics",
    ".pth": "pytorch-ultralytics",
}


@dataclass(frozen=True)
class DetectorConfig:
    """Flat, serializable view of the detector serving config (from Settings)."""

    weights: str
    device: str | None = None
    imgsz: int = EXPORT_IMGSZ
    conf: float | None = None          # None => NET_CONF
    iou: float | None = None           # None => NET_IOU
    name: str = "xray-weapons-yolo11m"
    version: str = "0.1.0"
    runtime: str | None = None         # None => inferred from extension
    calibration: str | None = None     # path to Platt params JSON, or None
    verify_sha256: bool = True


def _hash_weights(path: Path) -> str:
    """SHA-256 of the weights file, streamed. Ground truth for provenance."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _infer_runtime(path: Path) -> str:
    return _RUNTIME_BY_EXT.get(path.suffix.lower(), "ultralytics")


def _load_calibrator(path: str | None):
    """Build a PlattCalibrator from a JSON params file, or None (=> identity).

    File format: ``{"gun": [a, b], "knife": [a, b], ...}`` — exactly what
    ``PlattCalibrator.params`` serializes to. numpy is imported lazily inside
    calibration.py; importing it is fine on this box, but we only touch it when
    a path is actually configured.
    """
    if not path:
        return None
    from detector.eval.calibration import PlattCalibrator  # numpy under the hood

    raw = json.loads(Path(path).read_text())
    params = {label: (float(ab[0]), float(ab[1])) for label, ab in raw.items()}
    log.info("loaded Platt calibration for %d classes from %s", len(params), path)
    return PlattCalibrator(params=params)


def build_detector(cfg: DetectorConfig) -> WeaponsDetector:
    """Assemble the production ``WeaponsDetector``. Fail-closed: anything wrong
    here raises, so a misconfigured serving box aborts startup instead of booting
    into a silent 501. Heavy deps (ultralytics, cv2) load lazily at this point.
    """
    weights_path = Path(cfg.weights)
    if not weights_path.is_file():
        raise FileNotFoundError(
            f"detector weights not found: {weights_path} "
            "(set XRAY_DETECTOR_WEIGHTS to the exported artifact on this box)"
        )

    if cfg.imgsz != EXPORT_IMGSZ:
        log.warning(
            "detector imgsz=%d != export imgsz=%d — input size is fixed by the "
            "static ONNX export; this WILL shift geometry/recall. Re-export if "
            "you truly mean to change it.", cfg.imgsz, EXPORT_IMGSZ,
        )

    conf = NET_CONF if cfg.conf is None else cfg.conf
    iou = NET_IOU if cfg.iou is None else cfg.iou
    runtime = cfg.runtime or _infer_runtime(weights_path)
    weights_sha = _hash_weights(weights_path)

    # Lazy: this is the line that pulls in the ML stack. Deferred until here so
    # the module imports on the API/contract box.
    from detector.serving.predictor import UltralyticsPredictor
    from detector.serving.image_store import ObjectStoreLoader

    predictor = UltralyticsPredictor(
        str(weights_path), conf=conf, iou=iou, imgsz=cfg.imgsz, device=cfg.device,
    )
    loader = ObjectStoreLoader(verify_sha256=cfg.verify_sha256)
    calibrator = _load_calibrator(cfg.calibration)

    provenance = ModelProvenance(
        name=cfg.name,
        version=cfg.version,
        weights_sha256=weights_sha,
        runtime=runtime,
    )

    log.info(
        "detector wired: name=%s v=%s sha=%s… runtime=%s imgsz=%d device=%s "
        "conf=%.3f iou=%.3f calibrated=%s verify_sha256=%s",
        cfg.name, cfg.version, weights_sha[:12], runtime, cfg.imgsz,
        cfg.device or "auto", conf, iou, "yes" if calibrator else "no",
        cfg.verify_sha256,
    )

    return WeaponsDetector(
        predictor=predictor,
        loader=loader,
        provenance=provenance,
        calibrator=calibrator,
    )


__all__ = ["DetectorConfig", "build_detector", "EXPORT_IMGSZ"]
