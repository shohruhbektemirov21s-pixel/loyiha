"""Executable proof the serving skeleton wires up correctly.

Run:  python -m app._smoke_api
Exercises health, OpenAPI generation, the 501 stubs, 422 validation, and the
502 fail-closed guardrail (a hallucinating VLM is rejected at the boundary).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from contracts.v1 import (
    Detection, DetectionResult, DetectionStatus, DetectionVerdict, ImageFrame,
    ImageModality, Locale, ModelProvenance, OperatorVerdict, PixelBox, RiskBand,
    ScanSubject, StorageRef, ThreatCategory, VerdictRequest,
)
from contracts.v1.acquisition import AcquisitionResult

from app.deps import VerdictGenerator, provide_verdict_generator
from app.main import create_app

NOW = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _frame(fid="high"):
    return ImageFrame(frame_id=fid, width_px=2048, height_px=1024,
                      image=StorageRef(uri=f"s3://s/{fid}.tiff", sha256=SHA, size_bytes=1_000_000))


def _detection_result(scan_id, det_id):
    return DetectionResult(
        scan_id=scan_id, status=DetectionStatus.COMPLETED, emitted_at=NOW,
        model=ModelProvenance(name="xray-detector", version="0.3.1"), frames=[_frame()],
        detections=[Detection(detection_id=det_id, frame_id="high",
                              box=PixelBox(x=10, y=10, width=50, height=50),
                              native_label="organic_mass", category=ThreatCategory.NARCOTICS, score=0.8)],
    )


def main() -> None:
    app = create_app()
    client = TestClient(app)
    JSON = {"content-type": "application/json"}

    # Health + OpenAPI generate cleanly (the live integration contract).
    assert client.get("/health").json()["contract_version"] == "1.0"
    spec = client.get("/openapi.json").json()
    assert "/v1/detect" in spec["paths"] and "/v1/verdict" in spec["paths"]

    scan_id, det_id = uuid4(), uuid4()
    acq = AcquisitionResult(scan_id=scan_id, scanner_id="SCN-07", subject=ScanSubject.VEHICLE,
                            modality=ImageModality.DUAL_ENERGY, captured_at=NOW, emitted_at=NOW,
                            frames=[_frame()])

    # Unimplemented seams fail loud: 501, never a faked result.
    r = client.post("/v1/detect", content=acq.model_dump_json(), headers=JSON)
    assert r.status_code == 501, r.status_code

    req = VerdictRequest(scan_id=scan_id, detection=_detection_result(scan_id, det_id),
                         locale=Locale.UZ_LATN, emitted_at=NOW)
    assert client.post("/v1/verdict", content=req.model_dump_json(), headers=JSON).status_code == 501

    # Malformed payload rejected by the contract before any handler logic: 422.
    assert client.post("/v1/detect", json={"scan_id": "not-a-uuid"}).status_code == 422

    # A hallucinating VLM is rejected at the boundary: 502, fail-closed.
    class Hallucinator:
        async def generate(self, request: VerdictRequest) -> OperatorVerdict:
            return OperatorVerdict(
                verdict_id=uuid4(), scan_id=request.scan_id, locale=request.locale,
                overall_risk=RiskBand.HIGH, summary_uz="x",
                per_detection=[DetectionVerdict(detection_id=uuid4(),  # id never given!
                              category=ThreatCategory.UNKNOWN, rationale_uz="x", confidence=0.5)],
                model=ModelProvenance(name="qwen3-vl", version="1"), generated_at=NOW)

    app.dependency_overrides[provide_verdict_generator] = lambda: Hallucinator()
    r = client.post("/v1/verdict", content=req.model_dump_json(), headers=JSON)
    assert r.status_code == 502, r.status_code
    app.dependency_overrides.clear()

    # A well-behaved VLM passes through: 200.
    class GoodVlm:
        async def generate(self, request: VerdictRequest) -> OperatorVerdict:
            return OperatorVerdict(
                verdict_id=uuid4(), scan_id=request.scan_id, locale=request.locale,
                overall_risk=RiskBand.HIGH, summary_uz="Organik massa aniqlandi.",
                per_detection=[DetectionVerdict(detection_id=det_id,
                              category=ThreatCategory.NARCOTICS, rationale_uz="Zichligi yuqori.", confidence=0.7)],
                model=ModelProvenance(name="qwen3-vl", version="1"), generated_at=NOW)

    app.dependency_overrides[provide_verdict_generator] = lambda: GoodVlm()
    r = client.post("/v1/verdict", content=req.model_dump_json(), headers=JSON)
    assert r.status_code == 200, r.status_code
    assert r.json()["decision_support_only"] is True
    app.dependency_overrides.clear()

    print("OK: serving skeleton — health, OpenAPI, 501 stubs, 422 validation, 502 guardrail, 200 happy-path.")


if __name__ == "__main__":
    main()
