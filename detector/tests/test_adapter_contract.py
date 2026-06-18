"""Proof the WeaponsDetector satisfies the Hop-2 contract — no GPU/torch/cv2.

Run:  python -m detector.tests.test_adapter_contract

Drives every adapter branch with fake backends and asserts the output is a
contract-valid ``DetectionResult`` (Pydantic validates on construction, so a
returned object is *by definition* legal — these tests assert the harder
semantics: mapping, clamping, recall-first thresholding, and fail-closed).
Also exercises the live FastAPI seam end-to-end via dependency_overrides.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.deps import provide_detector
from app.main import create_app
from contracts.v1 import (
    AcquisitionResult,
    ImageFrame,
    ImageModality,
    ModelProvenance,
    ScanSubject,
    StorageRef,
    ThreatCategory,
)
from contracts.v1.detection import DetectionStatus
from detector.serving.adapter import WeaponsDetector
from detector.serving.predictor import (
    ConstantLoader,
    RaisingPredictor,
    RawDetection,
    StaticPredictor,
)

NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
SHA = "b" * 64
W, H = 2048, 1024


def _frame(fid="high"):
    return ImageFrame(frame_id=fid, width_px=W, height_px=H,
                      image=StorageRef(uri=f"file:///scans/{fid}.tiff", sha256=SHA, size_bytes=10_000))


def _acq(frames):
    return AcquisitionResult(
        scan_id=uuid4(), scanner_id="SCN-07", subject=ScanSubject.BAGGAGE,
        modality=ImageModality.SINGLE_ENERGY, captured_at=NOW, emitted_at=NOW, frames=frames,
    )


def _detector(predictor):
    return WeaponsDetector(
        predictor=predictor,
        loader=ConstantLoader(),
        provenance=ModelProvenance(name="xray-weapons-yolo11m", version="0.1.0", weights_sha256="c" * 64,
                                   runtime="onnxruntime"),
        clock=lambda: NOW,  # deterministic emitted_at
    )


async def _run(det, acq):
    return await det.detect(acq)


def _await(coro):
    import asyncio
    return asyncio.run(coro)


def test_mapping_and_status():
    # A gun and a knife, both above threshold -> COMPLETED with mapped categories.
    preds = StaticPredictor([
        RawDetection(100, 100, 300, 260, "gun", 0.91),
        RawDetection(900, 400, 980, 700, "folding_knife", 0.55),  # OPIXray-style native label
    ])
    res = _await(_run(_detector(preds), _acq([_frame()])))
    assert res.status == DetectionStatus.COMPLETED
    cats = sorted(d.category.value for d in res.detections)
    assert cats == ["bladed_weapon", "firearm"], cats
    # native label preserved; raw score retained in attributes for audit
    knife = next(d for d in res.detections if d.category == ThreatCategory.BLADED_WEAPON)
    assert knife.native_label == "folding_knife"
    assert knife.attributes["raw_score"] == "0.5500"
    assert res.emitted_at == NOW
    assert res.model.weights_sha256 == "c" * 64


def test_box_clamped_to_frame():
    # Box spills past the right/bottom edge — contract would reject it raw.
    preds = StaticPredictor([RawDetection(2000, 1000, 2200, 1200, "gun", 0.9)])
    res = _await(_run(_detector(preds), _acq([_frame()])))
    assert len(res.detections) == 1
    b = res.detections[0].box
    assert b.x + b.width <= W and b.y + b.height <= H, (b.x, b.y, b.width, b.height)
    assert b.fits_within(_frame())


def test_fully_out_of_frame_dropped():
    preds = StaticPredictor([RawDetection(5000, 5000, 5100, 5100, "gun", 0.9)])
    res = _await(_run(_detector(preds), _acq([_frame()])))
    assert res.status == DetectionStatus.COMPLETED_NO_FINDINGS
    assert res.detections == []


def test_recall_first_threshold():
    # Weapon just under the 0.20 firearm operating point is filtered; a tool at
    # the same score is also filtered (tools sit higher). Empty -> NO_FINDINGS.
    preds = StaticPredictor([
        RawDetection(10, 10, 50, 50, "gun", 0.18),
        RawDetection(60, 60, 90, 90, "wrench", 0.30),
    ])
    res = _await(_run(_detector(preds), _acq([_frame()])))
    assert res.status == DetectionStatus.COMPLETED_NO_FINDINGS, [d.score for d in res.detections]


def test_failed_is_fail_closed():
    # Backend raises -> FAILED, an error message, and zero detections (contract
    # forbids FAILED-with-findings; we never degrade an error to 'clean').
    res = _await(_run(_detector(RaisingPredictor()), _acq([_frame()])))
    assert res.status == DetectionStatus.FAILED
    assert res.detections == []
    assert res.error and "exploded" in res.error


def test_multi_frame_referential_integrity():
    # Two frames; each detection must reference a real frame and fit it. The
    # DetectionResult validator enforces this — construction would raise if the
    # adapter mislabeled frame_id, so a clean return IS the assertion.
    preds = StaticPredictor([RawDetection(0, 0, 40, 40, "knife", 0.8)])
    res = _await(_run(_detector(preds), _acq([_frame("high"), _frame("low")])))
    assert {d.frame_id for d in res.detections} <= {"high", "low"}
    assert len(res.detections) == 2  # one per frame (StaticPredictor fires on both)


def test_end_to_end_via_fastapi():
    app = create_app()
    preds = StaticPredictor([RawDetection(100, 100, 300, 260, "pistol", 0.88)])
    app.dependency_overrides[provide_detector] = lambda: _detector(preds)
    client = TestClient(app)
    acq = _acq([_frame()])
    r = client.post("/v1/detect", content=acq.model_dump_json(), headers={"content-type": "application/json"})
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert body["status"] == "completed"
    assert body["detections"][0]["category"] == "firearm"
    assert body["model"]["runtime"] == "onnxruntime"
    app.dependency_overrides.clear()


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"OK: {len(tests)} contract conformance tests passed (no ML deps).")


if __name__ == "__main__":
    main()
