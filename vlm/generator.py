"""VerdictGenerator — the ``app.deps.VerdictGenerator`` seam implementation.

This is the only concrete class that satisfies ``VerdictGenerator.generate``.
It wires together:

  prompts.py  — Uzbek templates, slot prompts, risk band
  guard.py    — Uzbek language guard (Cyrillic/drift/forbidden)
  backend.py  — transport to vLLM / Ollama / llama.cpp

Pipeline for a scan WITH detections:
  for each detection:
    1. Load the crop bytes (from SecureImageStore if available).
    2. Build the slot-fill user-turn prompt.
    3. Call the backend at low temperature (default 0.10).
    4. Parse TAVSIF / SABAB slots from the model output.
    5. Run the language guard:
         - pass → assemble rationale_uz from template + slots
         - Cyrillic/drift (retryable) → one retry, then fall back to template
         - Forbidden clearance (non-retryable) → fall back immediately, log CRITICAL
    6. Build DetectionVerdict.

  Assemble OperatorVerdict from per-detection verdicts + templated summary.
  Risk band is always computed deterministically — never from model output.

Pipeline for a CLEAR scan (no detections):
  Return a fully templated OperatorVerdict. Zero model calls.

No scan bytes flow out of this module. The SecureImageStore is optional;
without it the generator falls back to text-only prompts gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from contracts.v1 import (
    ModelProvenance,
    RiskBand,
    ThreatCategory,
)
from contracts.v1.detection import Detection, DetectionResult, DetectionStatus
from contracts.v1.verdict import (
    DetectionVerdict,
    Locale,
    OperatorVerdict,
    VerdictRequest,
)
from vlm.backend import VLMBackend, make_image_message
from vlm.guard import LanguageGuard, ViolationKind
from vlm.prompts import (
    SYSTEM_PROMPT,
    assemble_rationale,
    build_slot_prompt,
    build_summary,
    clear_summary,
    compute_risk_band,
    deterministic_slots,
    extract_slots,
)

log = logging.getLogger("xray.vlm.generator")

# Temperature constants — determinism where it matters.
_TEMP_SLOTS: float = 0.10   # slot fill: near-deterministic
_TEMP_RETRY: float = 0.15   # slightly warmer on retry to escape local minimum
_MAX_RETRIES: int = 1       # one retry for retryable violations; then fallback

# DetectionVerdict.confidence is a required UnitInterval whose contract meaning is
# "VLM's confidence in its OWN description, NOT a detection score". The VLM does
# not emit a calibrated number, so we report a single NEUTRAL sentinel (not a
# measured value) and disclaim it in the rationale. Never use this for ranking or
# the risk band — those use the detector's calibrated score.
_VLM_TEXT_CONFIDENCE: float = 0.50

# Appended to every model-authored rationale so the console never mistakes the
# VLM's prose (or the 0.50 sentinel above) for a verified/calibrated signal.
_TEXT_DISCLAIMER_MODEL = (
    "Eslatma: tavsif matni model tomonidan yozilgan, tasdiqlanmagan — "
    "qaror detektorning kalibrlangan ballariga asoslanadi."
)
_TEXT_DISCLAIMER_FALLBACK = (
    "Eslatma: model matni ishlatilmadi; tavsif detektor faktlaridan tuzildi."
)


def _append_text_disclaimer(rationale: str, *, is_fallback: bool) -> str:
    """Append the uncalibrated-text disclaimer, staying within the contract's
    2000-char ``rationale_uz`` bound (truncate the body, never drop the note)."""
    note = _TEXT_DISCLAIMER_FALLBACK if is_fallback else _TEXT_DISCLAIMER_MODEL
    tail = "\n" + note
    budget = 2000 - len(tail)
    if len(rationale) > budget:
        rationale = rationale[: max(0, budget - 1)].rstrip() + "…"
    return rationale + tail


class QwenVLGenerator:
    """``VerdictGenerator`` implementation backed by Qwen3-VL.

    Constructed once at startup via ``vlm.composition.build_vlm_generator``;
    shared across requests. The ``SecureImageStore`` is optional — the
    generator degrades to text-only when not injected.
    """

    def __init__(
        self,
        backend: VLMBackend,
        guard: LanguageGuard,
        provenance: ModelProvenance,
        *,
        store=None,               # datalayer.storage.SecureImageStore | None
        max_tokens: int = 300,
        temperature: float = _TEMP_SLOTS,
        describe: bool = True,    # False => skip the model, use deterministic Uzbek
    ) -> None:
        self._backend = backend
        self._guard = guard
        self._provenance = provenance
        self._store = store
        self._max_tokens = max_tokens
        self._temperature = temperature
        # When False (e.g. a CPU box where the model can't produce clean Uzbek in
        # time) the per-detection slots come straight from deterministic_slots —
        # no backend call, so captures stay fast and the text stays correct.
        self._describe = describe

    # ------------------------------------------------------------------
    # Public: VerdictGenerator seam
    # ------------------------------------------------------------------
    async def generate(self, request: VerdictRequest) -> OperatorVerdict:
        result = request.detection

        # Fast path: no findings — fully templated, zero model calls.
        if not result.has_findings:
            return self._clear_verdict(request)

        # Per-detection slot filling (async, but run sequentially to avoid
        # saturating a single-GPU server with parallel requests).
        per_detection: list[DetectionVerdict] = []
        rationales: list[str] = []
        for detection in result.detections:
            dv, rationale = await self._process_detection(detection, result)
            per_detection.append(dv)
            rationales.append(rationale)

        risk = compute_risk_band(result)
        summary = build_summary(risk, rationales)

        return OperatorVerdict(
            verdict_id=uuid.uuid4(),
            scan_id=request.scan_id,
            locale=request.locale,
            overall_risk=risk,
            summary_uz=summary,
            per_detection=per_detection,
            model=self._provenance,
            generated_at=datetime.now(timezone.utc),
            decision_support_only=True,
        )

    # ------------------------------------------------------------------
    # CLEAR verdict (no model call)
    # ------------------------------------------------------------------
    def _clear_verdict(self, request: VerdictRequest) -> OperatorVerdict:
        n_frames = len(request.detection.frames)
        return OperatorVerdict(
            verdict_id=uuid.uuid4(),
            scan_id=request.scan_id,
            locale=request.locale,
            overall_risk=RiskBand.CLEAR,
            summary_uz=clear_summary(n_frames),
            per_detection=[],
            model=self._provenance,
            generated_at=datetime.now(timezone.utc),
            decision_support_only=True,
        )

    # ------------------------------------------------------------------
    # Per-detection processing
    # ------------------------------------------------------------------
    async def _process_detection(
        self,
        detection: Detection,
        result: DetectionResult,
    ) -> tuple[DetectionVerdict, str]:
        """Fill slots for one detection. Returns (DetectionVerdict, rationale_uz)."""
        frame = next(
            (f for f in result.frames if f.frame_id == detection.frame_id),
            result.frames[0],
        )

        # Prefer a tight crop of the detection; if none is wired, fall back to
        # the FULL FRAME so the model still SEES pixels (with the box coords in
        # the prompt) instead of running blind on text alone. Text-only is the
        # last resort.
        # Disk reads (store.get / open()) are blocking — run them off the event
        # loop so concurrent verdict requests don't stall on one slow read.
        crop_bytes = await asyncio.to_thread(self._load_crop, detection)
        if crop_bytes is not None:
            image_bytes, full_frame = crop_bytes, False
        else:
            image_bytes = await asyncio.to_thread(self._load_frame_image, frame)
            full_frame = image_bytes is not None

        if self._describe:
            slots, used_fallback = await self._fill_slots(
                detection, frame.width_px, frame.height_px, image_bytes, full_frame
            )
            # When the VLM is unavailable or its output failed the guard, fall
            # back to a deterministic, fact-derived Uzbek description instead of
            # the bare generic template.
            if used_fallback or slots.fallback:
                slots = deterministic_slots(detection, frame.width_px, frame.height_px)
        else:
            # Description disabled (CPU box): skip the doomed/slow model call and
            # build clean Uzbek straight from the detector facts.
            slots = deterministic_slots(detection, frame.width_px, frame.height_px)

        rationale = assemble_rationale(detection, slots)
        # The VLM produces TEXT, not a calibrated probability. There is no
        # held-out set that maps "the model wrote a clean Uzbek description" to a
        # likelihood the detection is real — so the old fixed 0.80 was a
        # fabricated confidence the console could display as if it were measured.
        # The contract requires this field (UnitInterval, NOT optional) and its
        # documented meaning is "VLM's confidence in its own description, NOT a
        # detection score" — so we set a deliberately NEUTRAL, uncalibrated
        # sentinel and disclaim it in the rationale. Decision-relevant confidence
        # is the DETECTOR's calibrated score (on the Detection, driving the risk
        # band); nothing here feeds the risk decision.
        vlm_confidence = _VLM_TEXT_CONFIDENCE
        rationale = _append_text_disclaimer(rationale, is_fallback=slots.fallback)

        dv = DetectionVerdict(
            detection_id=detection.detection_id,
            category=detection.category,
            rationale_uz=rationale,
            confidence=vlm_confidence,
        )
        return dv, rationale

    # ------------------------------------------------------------------
    # Slot filling with guard + retry
    # ------------------------------------------------------------------
    async def _fill_slots(
        self,
        detection: Detection,
        frame_w: int,
        frame_h: int,
        image_bytes: bytes | None,
        full_frame: bool = False,
    ):
        """Call the backend, run the guard, retry once on retryable violations.

        ``image_bytes`` is a tight crop, the full frame, or None (text-only);
        ``full_frame`` tells the prompt builder which so it can point the model
        at the box. Returns (FilledSlots, used_fallback: bool).
        """
        user_prompt = build_slot_prompt(
            detection, frame_w, frame_h,
            has_image=image_bytes is not None, full_frame=full_frame,
        )
        system_msg = {"role": "system", "content": SYSTEM_PROMPT}
        # base64-encoding a full frame (can be several MB) is CPU-bound and
        # blocking; run it off the event loop so it doesn't stall other requests.
        user_msg = await asyncio.to_thread(
            make_image_message, "user", user_prompt, image_bytes
        )
        messages = [system_msg, user_msg]

        for attempt in range(_MAX_RETRIES + 1):
            temp = self._temperature if attempt == 0 else _TEMP_RETRY
            try:
                raw = await self._backend.generate(
                    messages, temperature=temp, max_tokens=self._max_tokens
                )
            except Exception as exc:
                log.error(
                    "VLM backend error detection=%s attempt=%d: %s",
                    detection.detection_id,
                    attempt,
                    exc,
                    exc_info=True,
                )
                return self._fallback_slots(), True

            slots = extract_slots(raw)

            # Guard both filled slots.
            tavsif_result = self._guard.check(slots.tavsif)
            sabab_result = self._guard.check(slots.sabab)

            all_passed = tavsif_result.passed and sabab_result.passed
            if all_passed:
                log.debug(
                    "slots OK detection=%s attempt=%d",
                    detection.detection_id, attempt,
                )
                return slots, False

            # Log violations.
            for res, slot_name in [(tavsif_result, "TAVSIF"), (sabab_result, "SABAB")]:
                for v in res.violations:
                    log.warning(
                        "guard %s detection=%s slot=%s: %s",
                        v.kind.value,
                        detection.detection_id,
                        slot_name,
                        v.detail,
                    )
                    if v.kind == ViolationKind.FORBIDDEN_CLEARANCE:
                        log.critical(
                            "FORBIDDEN CLEARANCE PHRASE in VLM output — "
                            "detection=%s. Falling back to template. "
                            "Review model and prompt immediately.",
                            detection.detection_id,
                        )

            # Non-retryable → skip retry loop.
            any_non_retryable = any(
                not v.retryable
                for res in (tavsif_result, sabab_result)
                for v in res.violations
            )
            if any_non_retryable or attempt >= _MAX_RETRIES:
                break

        return self._fallback_slots(), True

    @staticmethod
    def _fallback_slots():
        """Safe template-only slots — no model output involved."""
        from vlm.prompts import FilledSlots, _FALLBACK_TAVSIF, _FALLBACK_SABAB
        return FilledSlots(
            tavsif=_FALLBACK_TAVSIF,
            sabab=_FALLBACK_SABAB,
            fallback=True,
        )

    # ------------------------------------------------------------------
    # Image loading (SecureImageStore, optional)
    # ------------------------------------------------------------------
    def _load_crop(self, detection: Detection) -> bytes | None:
        """Load crop bytes from the store if available. Never raises."""
        if self._store is None or detection.crop is None:
            return None
        try:
            return self._store.get(detection.crop)
        except Exception as exc:
            log.warning(
                "Could not load crop for detection=%s: %s",
                detection.detection_id, exc,
            )
            return None

    def _load_frame_image(self, frame) -> bytes | None:
        """Load full-frame image bytes from the frame's ``file://`` StorageRef.

        Used when no tight crop is wired so the model still sees the scan. The
        box coordinates in the prompt point it at the detection. Best-effort:
        any failure returns None and the caller degrades to a text-only prompt.
        Only local ``file://`` refs are read — no egress, by design.
        """
        ref = getattr(frame, "image", None)
        uri = getattr(ref, "uri", None)
        if not uri:
            return None
        try:
            from urllib.parse import urlparse
            parsed = urlparse(uri)
            if (parsed.scheme or "file") != "file":
                return None
            with open(parsed.path, "rb") as fh:
                return fh.read()
        except Exception as exc:  # noqa: BLE001 — never let image loading break a verdict
            log.warning("Could not load frame image %s: %s", uri, exc)
            return None


__all__ = ["QwenVLGenerator"]
