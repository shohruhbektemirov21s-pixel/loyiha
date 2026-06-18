"""Continuous-stream fail-safe message building (BO'SHLIQ-7, unit level).

The new continuous camera loop (``camera/stream.py``) must NEVER emit a silent
fake "clear" when the detector is unavailable. When the detector is a stub
(ServiceNotImplemented) or errors, the analysis message must carry
``risk_band == "unavailable"`` and an explicit Uzbek "tahlil mavjud emas"
summary — fail-safe, not fail-open.

These tests drive ``ContinuousAnalyzer._build_message`` directly with built
detection/verdict contract objects, so they need no cv2, no camera, no GPU.
``ContinuousAnalyzer`` is constructed with a dummy capture (we only call the
pure message builder), keeping everything deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

from camera.stream import ContinuousAnalyzer, StreamState, VideoStreamCapture
from contracts.v1 import ModelProvenance, RiskBand
from contracts.v1.detection import DetectionResult, DetectionStatus
from tests.fixtures.builders import (
    make_detection_result,
    make_frame,
    make_operator_verdict,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def analyzer() -> ContinuousAnalyzer:
    # A capture object is required by __init__ but the message builder never
    # touches the camera — construct one without opening it.
    cap = VideoStreamCapture()

    async def _noop_broadcast(_lane, _msg):
        return None

    return ContinuousAnalyzer(
        cap,
        detector=object(),
        generator=object(),
        broadcast=_noop_broadcast,
        lane_id="lane-1",
        cadence_s=2.0,
    )


def _failed_detection() -> DetectionResult:
    return DetectionResult(
        scan_id=make_detection_result().scan_id,
        status=DetectionStatus.FAILED,
        emitted_at=_NOW,
        model=ModelProvenance(name="no-detector", version="0.0.0", runtime="camera-stream"),
        frames=[make_frame()],
        detections=[],
        error="Detektor ulanmagan.",
    )


class TestFailSafeMessage:
    def test_detector_unavailable_yields_unavailable_band(self, analyzer):
        msg = analyzer._build_message(
            device="0",
            ts=_NOW,
            detection=_failed_detection(),
            verdict=None,
            analysis_available=False,
            detector_ok=False,
            vlm_ok=False,
        )
        assert msg["type"] == "camera.analysis"
        assert msg["risk_band"] == "unavailable", (
            "A disabled detector must surface 'unavailable', NEVER a silent 'clear'."
        )
        assert "mavjud emas" in msg["summary_uz"].lower()
        assert msg["n_detections"] == 0

    def test_clear_only_when_detector_ran_and_found_nothing(self, analyzer):
        clear_det = make_detection_result(detections=[], has_findings=False)
        msg = analyzer._build_message(
            device="0",
            ts=_NOW,
            detection=clear_det,
            verdict=None,
            analysis_available=True,    # detector ran
            detector_ok=True,
            vlm_ok=False,
        )
        assert msg["risk_band"] == "clear"
        assert msg["n_detections"] == 0

    def test_vlm_verdict_drives_band_when_available(self, analyzer):
        det = make_detection_result()  # one finding
        verdict = make_operator_verdict(det, risk=RiskBand.HIGH)
        msg = analyzer._build_message(
            device="0",
            ts=_NOW,
            detection=det,
            verdict=verdict,
            analysis_available=True,
            detector_ok=True,
            vlm_ok=True,
        )
        assert msg["risk_band"] == "high"
        assert msg["n_detections"] == 1
        assert msg["summary_uz"] == verdict.summary_uz

    def test_detector_findings_no_vlm_still_reports_detections(self, analyzer):
        det = make_detection_result()  # one finding
        msg = analyzer._build_message(
            device="0",
            ts=_NOW,
            detection=det,
            verdict=None,
            analysis_available=True,
            detector_ok=True,
            vlm_ok=False,
        )
        # Risk band comes from the detector (calibrated scores), not a fake clear.
        assert msg["risk_band"] != "clear"
        assert msg["n_detections"] == 1
        assert len(msg["detections"]) == 1
        d0 = msg["detections"][0]
        assert {"category", "score", "box_x", "box_y", "box_w", "box_h"} <= set(d0)

    def test_canonical_message_shape(self, analyzer):
        msg = analyzer._build_message(
            device="cam0",
            ts=_NOW,
            detection=_failed_detection(),
            verdict=None,
            analysis_available=False,
            detector_ok=False,
            vlm_ok=False,
        )
        # Canonical WS contract the console parses (type == camera.analysis).
        for key in ("type", "device", "ts", "risk_band", "n_detections", "summary_uz", "detections"):
            assert key in msg, f"camera.analysis message missing {key!r}"
        assert msg["device"] == "cam0"
        assert msg["ts"] == _NOW.isoformat()


class TestStreamState:
    def test_default_state_not_running(self):
        st = StreamState()
        assert st.running is False
        assert st.frames_analyzed == 0
        assert st.last_risk_band is None
