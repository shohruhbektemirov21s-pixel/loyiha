"""Open-vocabulary screening detector — zero-shot, no training data.

The closed-set YOLO (adapter.py) only finds classes it was trained on. This
module is the complementary layer: a **prompt-driven** detector (YOLO-World)
that attempts arbitrary categories — firearms, narcotics, explosives — by their
TEXT NAME, with no labelled examples. It implements the same ``app.deps.Detector``
seam and emits the same contract ``DetectionResult``.

WHAT IT IS GOOD FOR
    A screening aid that surfaces candidate regions the trained model can't
    (new threat classes, before you have labelled X-ray data for them).

HONEST LIMITS (read before trusting a number)
    * YOLO-World is trained on NATURAL photos. X-ray pseudo-colour images are a
      large domain shift — recall on real X-ray will be far below its COCO
      numbers until fine-tuned on X-ray. Treat low-confidence hits as "look
      here", not "this is a gun".
    * NARCOTICS in X-ray are an amorphous mass with no silhouette; they are
      separated by dual-energy MATERIAL density, not shape. No RGB/shape model
      (this one included) detects them reliably. We therefore DO NOT emit a
      narcotics signal from this detector at all — the shape-proxy prompts and
      the CLIP drug label were removed (they only faked confidence). Real
      narcotics detection needs the dual-energy material channel; until that
      exists this layer stays silent on narcotics rather than fabricating a hit.

So this is decision-support screening, deliberately higher-threshold and
clearly provenanced as ``yolo-world-openvocab`` so nobody mistakes a zero-shot
hit for a calibrated detection.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from contracts.v1 import (
    AcquisitionResult, Detection, DetectionResult, DetectionStatus,
    ImageFrame, ModelProvenance, PixelBox, ThreatCategory,
)
from detector.serving.predictor import RawDetection

log = logging.getLogger("xray.detector.openvocab")

# Prompt -> shared taxonomy. Each prompt is a free-text class handed to
# YOLO-World; the key order is the model's class-index order.
PROMPT_TO_CATEGORY: dict[str, ThreatCategory] = {
    # --- firearms ---
    "handgun": ThreatCategory.FIREARM,
    "pistol": ThreatCategory.FIREARM,
    "revolver": ThreatCategory.FIREARM,
    "rifle": ThreatCategory.FIREARM,
    "gun": ThreatCategory.FIREARM,
    # --- bladed ---
    "knife": ThreatCategory.BLADED_WEAPON,
    "dagger": ThreatCategory.BLADED_WEAPON,
    "blade": ThreatCategory.BLADED_WEAPON,
    "scissors": ThreatCategory.BLADED_WEAPON,
    # --- explosives ---
    "grenade": ThreatCategory.EXPLOSIVE,
    "explosive device": ThreatCategory.EXPLOSIVE,
    "dynamite stick": ThreatCategory.EXPLOSIVE,
    # --- other contraband ---
    "stack of banknotes": ThreatCategory.CURRENCY,
    "bottle of liquid": ThreatCategory.CONTRABAND_OTHER,
}

# REMOVED: narcotics shape-proxy prompts ("bag of white powder", "drug package",
# "pills"). On a SINGLE-ENERGY X-ray, narcotics are an amorphous mass with no
# silhouette — a shape/RGB model (YOLO-World + CLIP) cannot tell them from any
# other powder/package, so these prompts only manufactured FALSE POSITIVES that
# then drove a NARCOTICS category and inflated the risk band. Real narcotics
# detection needs the dual-energy MATERIAL-density channel, which this detector
# does not have. Until that channel exists we do NOT emit a narcotics signal here
# rather than fake one. (See ClipReclassifier note below for the same reason.)

DEFAULT_PROMPTS: list[str] = list(PROMPT_TO_CATEGORY)

# Open-vocab is noisier than a calibrated closed-set model, so screen at a
# higher floor than adapter.DEFAULT_THRESHOLDS and never auto-clear on it.
OPENVOCAB_THRESHOLD: float = 0.10


class YoloWorldPredictor:
    """Prompt-driven predictor implementing the ``Predictor`` protocol.

    Heavy deps (ultralytics + CLIP) are imported lazily so the module stays
    importable on the contract box.
    """

    def __init__(
        self,
        weights: str = "yolov8s-worldv2.pt",
        *,
        prompts: list[str] | None = None,
        conf: float = OPENVOCAB_THRESHOLD,
        imgsz: int = 640,
        device: str | None = None,
    ) -> None:
        from ultralytics import YOLOWorld  # lazy

        self._prompts = prompts or DEFAULT_PROMPTS
        self._model = YOLOWorld(weights)
        self._model.set_classes(self._prompts)
        self._conf = conf
        self._imgsz = imgsz
        self._device = device

    def predict_frame(self, image):  # -> list[RawDetection]
        res = self._model.predict(
            image, conf=self._conf, imgsz=self._imgsz,
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
                native_label=self._prompts[int(c)] if int(c) < len(self._prompts) else str(int(c)),
                score=float(s),
            ))
        return out


# CLIP re-classification labels (prompt -> category). A None category means
# "not a threat" — the region is dropped, which suppresses the loose proposals
# YOLO-World makes. This is the stage that fixes open-vocab's class confusion
# (it localizes well but mislabels firearm<->knife); CLIP re-reads the crop.
CLIP_LABELS: list[tuple[str, ThreatCategory | None]] = [
    ("a firearm or handgun", ThreatCategory.FIREARM),
    ("a knife or sharp blade", ThreatCategory.BLADED_WEAPON),
    ("a pair of scissors", ThreatCategory.BLADED_WEAPON),
    ("an explosive device or grenade", ThreatCategory.EXPLOSIVE),
    # REMOVED: ("a bag of illegal drugs or narcotics", NARCOTICS). CLIP reads
    # natural-photo appearance; on a single-energy X-ray it cannot distinguish
    # narcotics from any other dense package, so this label produced a confident
    # but baseless NARCOTICS verdict. We keep a NEUTRAL "dense package or powder"
    # distractor mapped to None so such regions are DROPPED, not mislabelled — it
    # absorbs the proposals the drug label used to grab, without raising risk.
    ("a dense package or bag of powder", None),
    ("a stack of banknotes or currency", ThreatCategory.CURRENCY),
    ("a harmless everyday object", None),
]


class ClipReclassifier:
    """Zero-shot crop classifier: re-reads a proposed region with CLIP and
    returns the most likely threat category (or None = not a threat)."""

    def __init__(self, model_name: str = "ViT-B/32", device: str = "cpu",
                 min_prob: float = 0.60) -> None:
        # min_prob raised 0.45 -> 0.60. CLIP softmax over a handful of labels is
        # over-confident on X-ray (a large domain shift from its natural-photo
        # training), so a 0.45 floor cleared too many loose proposals into the
        # taxonomy. 0.60 keeps only the crops CLIP is firmly sure about; the rest
        # are dropped (None) — the recall cost is acceptable because YOLO-World
        # already over-proposes and this is a screening AID, not the primary
        # detector.
        import clip  # lazy
        import torch
        torch.set_num_threads(2)
        self._torch = torch
        self._model, self._preprocess = clip.load(model_name, device=device)
        self._device = device
        self._min_prob = min_prob
        self._text = clip.tokenize([t for t, _ in CLIP_LABELS]).to(device)
        self._cats = [c for _, c in CLIP_LABELS]

    def classify(self, crop_rgb) -> tuple[ThreatCategory | None, float]:
        from PIL import Image
        img = self._preprocess(Image.fromarray(crop_rgb)).unsqueeze(0).to(self._device)
        with self._torch.no_grad():
            logits, _ = self._model(img, self._text)
            probs = logits.softmax(-1)[0].tolist()
        i = max(range(len(probs)), key=lambda k: probs[k])
        if probs[i] < self._min_prob:
            return None, probs[i]
        return self._cats[i], probs[i]


class OpenVocabDetector:
    """Zero-shot ``app.deps.Detector``. Two-stage: YOLO-World proposes regions,
    an optional CLIP reclassifier re-labels each crop into the threat taxonomy."""

    def __init__(self, *, predictor: YoloWorldPredictor, loader, provenance: ModelProvenance,
                 threshold: float = OPENVOCAB_THRESHOLD,
                 reclassifier: "ClipReclassifier | None" = None) -> None:
        self._predictor = predictor
        self._loader = loader
        self._provenance = provenance
        self._threshold = threshold
        self._reclassifier = reclassifier

    async def detect(self, acquisition: AcquisitionResult) -> DetectionResult:
        try:
            dets = await asyncio.to_thread(self._detect_sync, acquisition)
        except Exception as exc:  # noqa: BLE001 — fail-closed
            log.exception("open-vocab detection failed for scan %s", acquisition.scan_id)
            return DetectionResult(
                scan_id=acquisition.scan_id, status=DetectionStatus.FAILED,
                emitted_at=datetime.now(timezone.utc), model=self._provenance,
                frames=list(acquisition.frames), detections=[],
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
        status = DetectionStatus.COMPLETED if dets else DetectionStatus.COMPLETED_NO_FINDINGS
        return DetectionResult(
            scan_id=acquisition.scan_id, status=status,
            emitted_at=datetime.now(timezone.utc), model=self._provenance,
            frames=list(acquisition.frames), detections=dets,
        )

    def _detect_sync(self, acquisition: AcquisitionResult) -> list[Detection]:
        out: list[Detection] = []
        for frame in acquisition.frames:
            image = self._loader.load(frame.image)
            for raw in self._predictor.predict_frame(image):
                if raw.score < self._threshold:
                    continue
                box = self._legal_box(raw, frame)
                if box is None:
                    continue
                category = PROMPT_TO_CATEGORY.get(raw.native_label.lower(), ThreatCategory.UNKNOWN)
                score = _clip_unit(raw.score)
                native = raw.native_label
                attrs = {"detector": "open-vocab", "yolo_world_label": raw.native_label,
                         "yolo_world_score": f"{raw.score:.4f}"}
                # Stage 2: CLIP re-reads the crop to fix the class label.
                if self._reclassifier is not None:
                    crop = image[box.y:box.y + box.height, box.x:box.x + box.width]
                    if crop.size:
                        clip_cat, clip_prob = self._reclassifier.classify(crop[:, :, ::-1])  # BGR->RGB
                        attrs["clip_prob"] = f"{clip_prob:.4f}"
                        if clip_cat is None:
                            continue  # CLIP says not a threat -> drop the loose proposal
                        category = clip_cat
                        native = f"clip:{clip_cat.value}"
                        score = _clip_unit(clip_prob)
                out.append(Detection(
                    detection_id=uuid4(), frame_id=frame.frame_id, box=box,
                    native_label=native[:128], category=category,
                    score=score, attributes=attrs,
                ))
        return out

    @staticmethod
    def _legal_box(raw: RawDetection, frame: ImageFrame) -> PixelBox | None:
        x1 = max(0, int(round(min(raw.x1, raw.x2))))
        y1 = max(0, int(round(min(raw.y1, raw.y2))))
        x2 = min(frame.width_px, int(round(max(raw.x1, raw.x2))))
        y2 = min(frame.height_px, int(round(max(raw.y1, raw.y2))))
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0 or x1 >= frame.width_px or y1 >= frame.height_px:
            return None
        return PixelBox(x=x1, y=y1, width=w, height=h)


def _clip_unit(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


__all__ = ["YoloWorldPredictor", "OpenVocabDetector", "ClipReclassifier",
           "PROMPT_TO_CATEGORY", "DEFAULT_PROMPTS", "CLIP_LABELS"]
