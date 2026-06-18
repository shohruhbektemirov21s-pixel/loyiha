"""Uzbek prompt templates and slot-filling for the VLM verdict layer.

Design rules — enforced by structure, not convention:

* **Template the structure; let the model fill only variable slots.**
  Fixed Uzbek text (labels, disclaimers, headings) is hardcoded here in
  Latin-script Uzbek. The model fills {tavsif} (description) and {sabab}
  (reason for flag) and nothing else.  A corrupt model cannot corrupt the
  frame around the slots.

* **CLEAR scans require zero model calls.**  If the detector found nothing
  and the scan is CLEAR, the summary is fully assembled from templates here.
  The LLM is never invoked, so latency is zero and hallucination risk is zero.

* **Risk band is computed deterministically.**  Overall risk is derived from
  detector scores and category severity — not from model prose.

* **Category names are a closed vocabulary.**  ``CATEGORY_UZ`` maps every
  ``ThreatCategory`` value to a fixed, unambiguous Uzbek term. The model
  never invents category names.

* **Slot prompts are deliberately narrow.**  The user-turn asks the model to
  fill exactly two fields of ≤ 80 words total, in Uzbek Latin only.  The
  narrower the generation space, the easier the guard (``guard.py``) can
  enforce output quality.
"""

from __future__ import annotations

from typing import NamedTuple

from contracts.v1 import RiskBand, ThreatCategory
from contracts.v1.detection import Detection, DetectionResult

# ---------------------------------------------------------------------------
# Closed-vocabulary Uzbek Latin category names
# ---------------------------------------------------------------------------
CATEGORY_UZ: dict[ThreatCategory, str] = {
    ThreatCategory.NARCOTICS:         "giyohvand modda",
    ThreatCategory.FIREARM:           "oʻqotar qurol",
    ThreatCategory.BLADED_WEAPON:     "kesuvchi qurol",
    ThreatCategory.EXPLOSIVE:         "portlovchi modda",
    ThreatCategory.CURRENCY:          "naqd pul",
    ThreatCategory.ORGANIC_ANOMALY:   "organik anomaliya",
    ThreatCategory.METALLIC_ANOMALY:  "metall anomaliya",
    ThreatCategory.CONTRABAND_OTHER:  "kontrabanda",
    ThreatCategory.UNKNOWN:           "noaniq predmet",
}

# Risk-band labels — shown in summary header.
RISK_LABEL_UZ: dict[RiskBand, str] = {
    RiskBand.CLEAR:  "Shubha yoʻq",
    RiskBand.LOW:    "Past xavf",
    RiskBand.MEDIUM: "Oʻrta xavf",
    RiskBand.HIGH:   "Yuqori xavf",
}

# Operator-decision disclaimer — hardcoded, always appended, never generated.
_DISCLAIMER = (
    "Eslatma: Yakuniy qaror faqat operator tomonidan qabul qilinadi."
)

# ---------------------------------------------------------------------------
# System prompt (static, injected once per session)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
Siz bojxona rentgen tekshiruvi qaror-qoʻllab-quvvatlash yordamchisisiz.

Qoidalar (bajarilishi MAJBURIY):
1. Siz DETEKTOR EMASSIZ. Asosiy detektor allaqachon shubhali predmetlarni
   aniqlagan. Sizning vazifangiz — detektor natijalarini operator uchun
   tushunarli tilda tushuntirish.
2. Faqat OZBEK TILIDA yozing (lotin yozuvi). Kirill harflari, rus tili yoki
   ingliz tili — mutlaqo man etilgan.
3. Hech qachon "yoʻl qoʻying", "xavfsiz", "ruxsat bering" yoki shunga
   oʻxshash iboralar ishlatmang. Operator qaror qiladi.
4. Qisqa, aniq va professional yozing.
5. Faqat berilgan maydonlarni toʻldiring — ortiqcha matn qoʻshmang.\
"""

# ---------------------------------------------------------------------------
# Slot prompt (per-detection, user turn)
# ---------------------------------------------------------------------------
def build_slot_prompt(
    detection: Detection,
    frame_w: int,
    frame_h: int,
    has_image: bool,
    full_frame: bool = False,
) -> str:
    """Build the user-turn text asking the model to fill two Uzbek slots.

    The model must fill:
      TAVSIF:  ≤ 60 so'z — what is visually observed in the crop/region
      SABAB:   ≤ 40 so'z — why this specific region warrants attention

    ``full_frame`` distinguishes a tight crop of the detection from the whole
    frame: when we hand the model the full frame we must point it at the box
    coordinates so it describes the right region, not the whole scene.

    Everything else (labels, structure, disclaimer) is assembled by the
    caller from hardcoded templates — never generated.
    """
    cat_uz = CATEGORY_UZ[detection.category]
    box = detection.box
    conf_pct = int(detection.score * 100)

    if not has_image:
        image_line = f"Rasm mavjud emas. Kadr oʻlchami: {frame_w}×{frame_h} px."
    elif full_frame:
        image_line = (
            f"Yuqoridagi rasm — BUTUN kadr ({frame_w}×{frame_h} px). "
            f"Aniqlangan hudud: ({box.x},{box.y}) nuqtadan boshlab "
            f"{box.width}×{box.height} px. Faqat shu hududdagi narsani tasvirlang."
        )
    else:
        image_line = "Yuqoridagi rasm — aniqlanagan hududning kesib olingan tasvirI."
    return (
        f"Detektor quyidagini aniqladi:\n"
        f"  Toifa: {cat_uz}\n"
        f"  Ishonch: {conf_pct}%\n"
        f"  Joylashuv: ({box.x},{box.y}), oʻlcham {box.width}×{box.height} px\n"
        f"\n"
        f"{image_line}\n"
        f"\n"
        f"Quyidagi maydonlarni FAQAT OZBEK TILIDA (lotin yozuvi) toʻldiring:\n"
        f"\n"
        f"TAVSIF: [rasmda koʻrinayotgan narsani 1–3 gap bilan tasvirlab bering]\n"
        f"SABAB: [nima uchun bu hudud diqqatni tortishini 1–2 gapda tushuntiring]\n"
        f"\n"
        f"Faqat TAVSIF va SABAB qatorlarini toʻldiring. Boshqa hech narsa yozmang."
    )


# ---------------------------------------------------------------------------
# Slot extraction — parse model output
# ---------------------------------------------------------------------------
class FilledSlots(NamedTuple):
    tavsif: str      # description slot
    sabab: str       # reason slot
    fallback: bool   # True if slots were not cleanly filled (fallback applied)


_FALLBACK_TAVSIF = "Detektor tomonidan aniqlangan hudud."
_FALLBACK_SABAB = "Avtomatik ravishda shubhali deb belgilangan."


def extract_slots(raw: str) -> FilledSlots:
    """Parse TAVSIF/SABAB lines from model output. Graceful fallback on failure."""
    tavsif: str | None = None
    sabab: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TAVSIF:"):
            tavsif = stripped[len("TAVSIF:"):].strip()
        elif stripped.upper().startswith("SABAB:"):
            sabab = stripped[len("SABAB:"):].strip()

    fallback = not tavsif or not sabab
    return FilledSlots(
        tavsif=tavsif or _FALLBACK_TAVSIF,
        sabab=sabab or _FALLBACK_SABAB,
        fallback=fallback,
    )


# ---------------------------------------------------------------------------
# Assembly: slots + template → rationale_uz
# ---------------------------------------------------------------------------
def assemble_rationale(
    detection: Detection,
    slots: FilledSlots,
) -> str:
    """Assemble the per-detection ``rationale_uz`` from template + filled slots.

    The fixed Uzbek text is hardcoded here; only the slot values come from the
    model. The model cannot corrupt the structural framing.
    """
    cat_uz = CATEGORY_UZ[detection.category]
    conf_pct = int(detection.score * 100)
    return (
        f"Aniqlangan toifa: {cat_uz} ({conf_pct}% ishonch)\n"
        f"Tavsif: {slots.tavsif}\n"
        f"Sabab: {slots.sabab}"
    )


# ---------------------------------------------------------------------------
# CLEAR summary — fully templated, zero model calls
# ---------------------------------------------------------------------------
def clear_summary(n_frames: int) -> str:
    """Return a complete summary_uz for a scan with no findings.

    This is the highest-volume path (most scans are benign). Zero LLM cost.
    The text is deliberately conservative: we never say "safe", only "not found".
    """
    return (
        f"{n_frames} ta kadrdan iborat skan koʻrib chiqildi.\n"
        f"Shubhali predmet aniqlanmadi.\n"
        f"\n"
        f"{_DISCLAIMER}"
    )


# ---------------------------------------------------------------------------
# Detection summary — templated header + per-detection blocks + disclaimer
# ---------------------------------------------------------------------------
def build_summary(
    risk: RiskBand,
    per_detection_rationales: list[str],
) -> str:
    """Assemble the overall ``summary_uz`` from deterministic parts.

    Nothing here is generated; all prose comes from the per-detection
    rationales assembled by ``assemble_rationale``.
    """
    risk_label = RISK_LABEL_UZ[risk]
    n = len(per_detection_rationales)
    lines = [
        f"Xavf darajasi: {risk_label}",
        f"Aniqlangan shubhali predmetlar soni: {n}",
        "",
    ]
    for i, rationale in enumerate(per_detection_rationales, start=1):
        lines.append(f"[ {i}-predmet ]")
        lines.append(rationale)
        lines.append("")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Risk band — computed deterministically, never generated
# ---------------------------------------------------------------------------
_HIGH_RISK_CATEGORIES = {
    ThreatCategory.NARCOTICS,
    ThreatCategory.FIREARM,
    ThreatCategory.EXPLOSIVE,
    ThreatCategory.BLADED_WEAPON,
}
_HIGH_THRESHOLD = 0.70
_MEDIUM_THRESHOLD = 0.40


def compute_risk_band(result: DetectionResult) -> RiskBand:
    """Derive coarse risk from detector scores and category severity.

    Intentionally conservative — ambiguous detections round up.
    This is a pure function; the VLM never overrides it.
    """
    if not result.has_findings:
        return RiskBand.CLEAR

    for d in result.detections:
        if d.category in _HIGH_RISK_CATEGORIES and d.score >= _HIGH_THRESHOLD:
            return RiskBand.HIGH

    max_score = max(d.score for d in result.detections)
    if max_score >= _MEDIUM_THRESHOLD:
        return RiskBand.MEDIUM
    return RiskBand.LOW


# ---------------------------------------------------------------------------
# Deterministic, fact-derived Uzbek slots (no model — used when the VLM is
# unavailable or its output fails the guard, e.g. on a CPU box where a small
# model can't produce clean Uzbek). Built purely from verified detector facts
# (category, location, size, confidence) so the text is always correct Uzbek
# and never invents anything the detector did not report.
# ---------------------------------------------------------------------------
def _position_uz(box, frame_w: int, frame_h: int) -> str:
    cx = (box.x + box.width / 2) / frame_w if frame_w else 0.5
    cy = (box.y + box.height / 2) / frame_h if frame_h else 0.5
    horiz = "chap" if cx < 0.34 else ("oʻng" if cx > 0.66 else "markaziy")
    vert = "yuqori" if cy < 0.34 else ("quyi" if cy > 0.66 else "oʻrta")
    if horiz == "markaziy" and vert == "oʻrta":
        return "markaziy qismida"
    if horiz == "markaziy":
        return f"{vert} qismida"
    if vert == "oʻrta":
        return f"{horiz} tomonida"
    return f"{vert} {horiz} burchagida"


def _size_uz(box, frame_w: int, frame_h: int) -> str:
    frac = (box.width * box.height) / ((frame_w * frame_h) or 1)
    return "kichik" if frac < 0.05 else ("katta" if frac > 0.20 else "oʻrta")


def _confidence_uz(score: float) -> str:
    return "yuqori" if score >= 0.70 else ("oʻrta" if score >= 0.40 else "past")


def deterministic_slots(detection: Detection, frame_w: int, frame_h: int) -> FilledSlots:
    """Assemble Uzbek TAVSIF/SABAB from detector facts alone — no model call.

    This is the trusted fallback: clean Latin-script Uzbek derived from the
    detection's category, position, size and confidence. It never hallucinates
    and always passes the language guard, so a customs operator gets a useful,
    correct description even when the VLM can't run (CPU). ``fallback=True`` keeps
    the per-detection confidence honest (detector-derived, not VLM-verified).
    """
    cat_uz = CATEGORY_UZ[detection.category]
    pos = _position_uz(detection.box, frame_w, frame_h)
    size = _size_uz(detection.box, frame_w, frame_h)
    conf_word = _confidence_uz(detection.score)
    conf_pct = int(detection.score * 100)

    tavsif = (
        f"Kadrning {pos} {size} oʻlchamli, {cat_uz}ga oʻxshash obyekt aniqlandi "
        f"(detektor ishonchi {conf_word}, {conf_pct}%)."
    )
    if detection.category in _HIGH_RISK_CATEGORIES:
        sabab = (
            f"Aniqlangan {cat_uz} yuqori xavfli toifaga kiradi va majburiy "
            f"operator tekshiruvini talab qiladi."
        )
    else:
        sabab = (
            f"Aniqlangan {cat_uz} avtomatik tarzda shubhali deb belgilandi; "
            f"operator vizual tekshiruvi tavsiya etiladi."
        )
    return FilledSlots(tavsif=tavsif, sabab=sabab, fallback=True)


__all__ = [
    "CATEGORY_UZ",
    "RISK_LABEL_UZ",
    "SYSTEM_PROMPT",
    "FilledSlots",
    "build_slot_prompt",
    "extract_slots",
    "assemble_rationale",
    "clear_summary",
    "build_summary",
    "compute_risk_band",
]
