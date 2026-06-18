"""Inference backends + the seams the adapter depends on.

The adapter (``adapter.py``) deliberately does **not** import torch, cv2, or
ultralytics. It depends only on two narrow Protocols defined here:

    FrameLoader   StorageRef  -> pixels   (bytes resolution + integrity)
    Predictor     pixels      -> [RawDetection]   (the actual model)

Concrete implementations carry the heavy deps and import them *lazily* (inside
__init__ / methods), so importing this module on a machine without a GPU stack
(e.g. the API/contract box) succeeds. The fakes at the bottom let the whole
serving path be tested with zero ML dependencies — see tests/.

``RawDetection`` is the boundary type: axis-aligned box in **pixel xyxy** of the
frame the model was run on, a native label, and the model's raw score. Turning
that into a contract ``Detection`` (clamping, taxonomy, calibration, ids) is the
adapter's job, kept separate so the model code stays dumb.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from detector.taxonomy import NET_CONF, NET_IOU  # light: contracts + stdlib only

if TYPE_CHECKING:  # type-only; never imported at runtime on the API box
    import numpy as np

    from contracts.v1 import StorageRef


@dataclass(frozen=True)
class RawDetection:
    """One model output, before any contract normalization.

    Coordinates are pixels in the frame the model ran on, corner form
    ``(x1, y1, x2, y2)`` with x2>x1, y2>y1. Score is the model's native
    confidence in [0, 1] (pre-calibration).
    """

    x1: float
    y1: float
    x2: float
    y2: float
    native_label: str
    score: float
    attributes: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class FrameLoader(Protocol):
    """Resolve a ``StorageRef`` to pixels (and verify integrity)."""

    def load(self, ref: "StorageRef") -> "np.ndarray": ...


@runtime_checkable
class Predictor(Protocol):
    """Run the detection model over one frame's pixels."""

    def predict_frame(self, image: "np.ndarray") -> list[RawDetection]: ...


# ---------------------------------------------------------------------------
# Real backend: Ultralytics (YOLO11 / RT-DETR — both load through YOLO()).
# ---------------------------------------------------------------------------
class UltralyticsPredictor:
    """Wraps an Ultralytics weights file (.pt or exported .onnx).

    Heavy imports are deferred to construction so this module imports on a box
    without the ML stack. ``conf`` is set low on purpose: the model should
    *emit* generously and let the adapter apply the calibrated, per-class
    operating threshold. Recall is won here — anything filtered out at the model
    is a candidate false negative we can never recover downstream.
    """

    def __init__(
        self,
        weights: str,
        *,
        conf: float = NET_CONF,   # low net: recall-first; adapter does real thresholding
        iou: float = NET_IOU,     # NMS IoU (ignored by RT-DETR, which is NMS-free)
        imgsz: int = 1024,    # MUST match train/export imgsz: weapons are small/thin,
                              # and the ONNX is exported static (dynamic=False) at 1024,
                              # so the input size is not a free runtime choice.
        device: str | None = None,
    ) -> None:
        from ultralytics import YOLO  # lazy

        self._model = YOLO(weights)
        self._conf = conf
        self._iou = iou
        self._imgsz = imgsz
        self._device = device
        # names: {class_id: native_label} — the model's own dialect.
        self._names: dict[int, str] = dict(self._model.names)

    def predict_frame(self, image: "np.ndarray") -> list[RawDetection]:
        res = self._model.predict(
            image, conf=self._conf, iou=self._iou, imgsz=self._imgsz,
            device=self._device, verbose=False,
        )[0]
        out: list[RawDetection] = []
        if res.boxes is None:
            return out
        xyxy = res.boxes.xyxy.cpu().numpy()
        scores = res.boxes.conf.cpu().numpy()
        cls = res.boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), s, c in zip(xyxy, scores, cls):
            out.append(RawDetection(
                x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2),
                native_label=self._names.get(int(c), str(int(c))),
                score=float(s),
            ))
        return out


# ---------------------------------------------------------------------------
# Test doubles (zero ML deps) — let the serving path be proven without a GPU.
# ---------------------------------------------------------------------------
class StaticPredictor:
    """Returns a scripted list of detections, ignoring the pixels.

    Used by the contract test to drive every adapter branch deterministically.
    """

    def __init__(self, detections: list[RawDetection]) -> None:
        self._detections = detections

    def predict_frame(self, image: "np.ndarray") -> list[RawDetection]:
        return list(self._detections)


class RaisingPredictor:
    """Simulates a model that blows up mid-inference (OOM, corrupt frame, ...).

    Lets the test assert the adapter's fail-closed path (-> FAILED, never a
    silent 'no findings')."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("inference backend exploded")

    def predict_frame(self, image: "np.ndarray") -> list[RawDetection]:
        raise self._exc


class ConstantLoader:
    """A FrameLoader that returns a fixed array without touching the store.

    The adapter clamps boxes to the *frame descriptor* (authoritative per the
    contract), so the actual pixel content is irrelevant to contract logic.
    """

    def __init__(self, array: "np.ndarray | None" = None) -> None:
        if array is None:
            import numpy as np  # lazy: tests have numpy, API box does too
            array = np.zeros((1, 1, 3), dtype="uint8")
        self._array = array

    def load(self, ref: "StorageRef") -> "np.ndarray":
        return self._array


__all__ = [
    "RawDetection",
    "FrameLoader",
    "Predictor",
    "UltralyticsPredictor",
    "StaticPredictor",
    "RaisingPredictor",
    "ConstantLoader",
]
