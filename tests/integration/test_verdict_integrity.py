"""Orchestrator referential-integrity guard at the API boundary (BO'SHLIQ-4).

``POST /v1/verdict`` runs ``validate_referential_integrity`` after the generator
returns. A VLM that hallucinates a ``detection_id`` it was never given — or
crosses scans — must be rejected with **502** (the VLM's fault), never served as
a 200. This is the structural guard that keeps a fabricated finding off the
operator console.

We exercise this through the real FastAPI app (not a direct function call) by
overriding the ``provide_verdict_generator`` seam with a stub generator that
returns a verdict referencing an unknown detection. No DB / GPU needed: in stub
mode the ScanStore is the null store and the audit sink is the logging stub.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.deps import provide_verdict_generator
from contracts.v1 import OperatorVerdict, RiskBand, VerdictRequest
from contracts.v1.verdict import DetectionVerdict, Locale
from tests.fixtures.builders import make_detection_result, make_provenance

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Stub generators
# ---------------------------------------------------------------------------
class _HallucinatingGenerator:
    """Returns a verdict referencing a detection_id that was never in the request."""

    async def generate(self, request: VerdictRequest) -> OperatorVerdict:
        return OperatorVerdict(
            schema_version="1.0",
            verdict_id=uuid4(),
            scan_id=request.scan_id,
            locale=Locale.UZ_LATN,
            overall_risk=RiskBand.HIGH,
            summary_uz="Soxta aniqlangan hudud — bu detection berilmagan edi tekshiring.",
            per_detection=[
                DetectionVerdict(
                    detection_id=uuid4(),  # hallucinated — not in request.detection
                    category=request.detection.detections[0].category,
                    rationale_uz="Bu hudud aslida detektor tomonidan berilmagan, soxta.",
                    confidence=0.9,
                )
            ],
            model=make_provenance("qwen3-vl"),
            generated_at=request.emitted_at,
        )


class _CrossScanGenerator:
    """Returns a verdict whose scan_id does not match the request (scan crossing)."""

    async def generate(self, request: VerdictRequest) -> OperatorVerdict:
        return OperatorVerdict(
            schema_version="1.0",
            verdict_id=uuid4(),
            scan_id=uuid4(),  # WRONG scan — does not match request.scan_id
            locale=Locale.UZ_LATN,
            overall_risk=RiskBand.CLEAR,
            summary_uz="Boshqa skan uchun xulosa — bu so'rovga tegishli emas albatta.",
            per_detection=[],
            model=make_provenance("qwen3-vl"),
            generated_at=request.emitted_at,
        )


class _HonestGenerator:
    """Returns a CLEAR verdict that references no detections (always valid)."""

    async def generate(self, request: VerdictRequest) -> OperatorVerdict:
        return OperatorVerdict(
            schema_version="1.0",
            verdict_id=uuid4(),
            scan_id=request.scan_id,
            locale=Locale.UZ_LATN,
            overall_risk=RiskBand.CLEAR,
            summary_uz="Avtomatik tahlil shubhali buyum aniqlamadi, qaror operatorda.",
            per_detection=[],
            model=make_provenance("qwen3-vl"),
            generated_at=request.emitted_at,
        )


def _verdict_payload() -> dict:
    det = make_detection_result()  # has one real detection
    req = VerdictRequest(
        schema_version="1.0",
        scan_id=det.scan_id,
        detection=det,
        locale=Locale.UZ_LATN,
        emitted_at=det.emitted_at,
    )
    return req.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestVerdictReferentialIntegrity:
    @pytest.mark.asyncio
    async def test_hallucinated_detection_id_rejected_502(self, app, client, auth_headers):
        app.dependency_overrides[provide_verdict_generator] = lambda: _HallucinatingGenerator()
        try:
            resp = await client.post("/v1/verdict", json=_verdict_payload(), headers=auth_headers)
        finally:
            app.dependency_overrides.pop(provide_verdict_generator, None)
        assert resp.status_code == 502, (
            f"A hallucinated detection_id must yield 502, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_cross_scan_verdict_rejected_502(self, app, client, auth_headers):
        app.dependency_overrides[provide_verdict_generator] = lambda: _CrossScanGenerator()
        try:
            resp = await client.post("/v1/verdict", json=_verdict_payload(), headers=auth_headers)
        finally:
            app.dependency_overrides.pop(provide_verdict_generator, None)
        assert resp.status_code == 502, (
            f"A scan_id mismatch must yield 502, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_honest_verdict_is_served_200(self, app, client, auth_headers):
        # Control: a generator that respects the contract returns 200, proving the
        # 502s above are caused by the integrity guard and not an unrelated error.
        app.dependency_overrides[provide_verdict_generator] = lambda: _HonestGenerator()
        try:
            resp = await client.post("/v1/verdict", json=_verdict_payload(), headers=auth_headers)
        finally:
            app.dependency_overrides.pop(provide_verdict_generator, None)
        assert resp.status_code == 200, f"Honest verdict should be 200: {resp.text}"
        body = resp.json()
        assert body["overall_risk"] == "clear"
        assert body["decision_support_only"] is True

    @pytest.mark.asyncio
    async def test_default_stub_generator_returns_501(self, client, auth_headers):
        # With no generator wired the seam stays the honest 501 stub — it never
        # fabricates a verdict. (Note: /v1/verdict is an internal serving
        # boundary and carries no auth dependency by design.)
        resp = await client.post("/v1/verdict", json=_verdict_payload(), headers=auth_headers)
        assert resp.status_code == 501
