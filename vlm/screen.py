"""Yuk rentgen (X-ray) skrining yadrosi — Qwen3-VL orqali operatorga yordam.

Bu modul ``xray_cargo_screen.py`` prototipining ishlab-chiqarish (production) varianti:
operator kamera o'rniga RENTGEN rasm(lar)ni yuklaydi va Qwen VLM vagon/yuk ICHIDA
nima borligini o'zbekcha tasvirlab beradi hamda konservativ kontrabanda bayrog'ini
qaytaradi.

Dizayn falsafasi (prototipdan saqlangan — vlm/generator.py bilan bir xil):
  * VLM bu DETEKTOR EMAS, TASVIRLOVCHI (describer). U "ichida nima bor" ni aytadi
    va konservativ belgi beradi; bu QAROR emas, operatorga SKRINING YORDAMI.
  * Operatorga ko'rsatiladigan bayroq KONSERVATIV hisoblanadi: dalil bo'lmasa YO'Q.
    Model faqat ESKALATSIYA qila oladi (YO'Q dan SHUBHALI/BOR ga), hech qachon
    o'zicha "tozalay" olmaydi. To'liqsiz/buzilgan javob ham eskalatsiya.
  * Har bir erkin matn ``vlm.guard.LanguageGuard`` orqali o'tadi — kirill drift,
    homoglif yoki taqiqlangan "o'tkazib yuborish" iborasi bo'lsa matn xavfsiz
    fallback bilan almashtiriladi va holat eskalatsiya tomon suriladi.
  * ``risk_band`` DETERMINISTIK ravishda BAYROQLARDAN hisoblanadi (modelning XAVF
    qatoridan emas): biror BOR -> high, biror SHUBHALI -> medium, barchasi YO'Q ->
    clear. Fail-safe holat (xato) -> medium (ehtiyot uchun).

4B VLM ning halol cheklovi: u QO'POL (coarse) mazmunni ishonchli o'qiydi (vagon
turi, asosiy yuk). Yagona energiyali X-ray faqat SHAKLNI ko'rsatadi, material
turini emas — shu sababli prompt konservativ YO'Q defaultni majburlaydi.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

from vlm.backend import VLMBackend, make_image_message
from vlm.guard import LanguageGuard, get_guard

log = logging.getLogger("xray.vlm.screen")

# ---------------------------------------------------------------------------
# Promptlar — xray_cargo_screen.py dan qayta ishlatilgan (yaxshi ishlaydi).
# SYS / SHOT / USER aynan prototipdagidek: vagon turi, asosiy yuk va
# QORADORI/QUROL/TAMAKI/BOSHQA = BOR/SHUBHALI/YO'Q + XAVF.
# ---------------------------------------------------------------------------
SYS = (
    "Siz bojxona temir-yo'l rentgen (X-ray) skanlarini tahlil qiluvchi mutaxassisiz. "
    "Tasvirlar yon ko'rinishdagi vagonlar. Vazifa: vagon turi va asosiy yukni aniqlash, "
    "hamda kontrabanda belgilarini baholash.\n"
    "QAT'IY QOIDALAR:\n"
    "- Oddiy yuk (avtomobil, sanoat suyuqligi/tanki, qop-jako, g'altak, metall buyum) "
    "KONTRABANDA EMAS — bunday holda barcha kontrabanda bandlari YO'Q.\n"
    "- Faqat ko'zga aniq tashlanadigan, tasvirlab bera oladigan dalil bo'lsagina BOR yoki SHUBHALI de.\n"
    "- Yagona energiyali X-ray faqat SHAKLNI ko'rsatadi, material turini emas. Narkotikni "
    "shakl bo'yicha tasdiqlab bo'lmaydi — faqat yashirish joyida (tank tubi, qo'sh devor) "
    "g'ayrioddiy zич massa bo'lsa SHUBHALI.\n"
    "- Taxmin qilma. Dalil bo'lmasa aniq YO'Q yoz."
)
SHOT = (
    "Quyida FAQAT format namunasi (qiymatlarni ko'chirma, har bir maydonni o'zing rasmga qarab to'ldir):\n"
    "VAGON_TURI: <rasmda ko'rgan vagon turi>\n"
    "ASOSIY_YUK: <rasmda ko'rgan yuk>\n"
    "QORADORI: YO'Q\nQUROL: YO'Q\nTAMAKI: YO'Q\nBOSHQA: YO'Q\nXAVF: PAST"
)
USER = (
    SHOT + "\n\nEndi BERILGAN rasmni xuddi shu formatда tahlil qil. Dalil bo'lmasa YO'Q yoz, "
    "qiymatlarni namunadan ko'chirma:\n"
    "VAGON_TURI:\nASOSIY_YUK:\nQORADORI:\nQUROL:\nTAMAKI:\nBOSHQA:\nXAVF:"
)

# Bayroq kalitlari (prompt maydonlari) -> kanonik javob kaliti.
_FLAG_FIELDS: dict[str, str] = {
    "QORADORI": "narcotics",
    "QUROL": "weapon",
    "TAMAKI": "tobacco",
    "BOSHQA": "other",
}
_STRUCT_FIELDS = {"VAGON_TURI", "ASOSIY_YUK", "QORADORI", "QUROL", "TAMAKI", "BOSHQA", "XAVF"}

RiskBand = Literal["clear", "low", "medium", "high"]

# Guard rad etganda / model jim qolganda ishlatiladigan xavfsiz fallbacklar.
_FALLBACK_SUMMARY = (
    "Tizim ishonchli o'zbekcha tavsif bera olmadi. Bu rasmni operator qo'lda ko'rib chiqsin."
)
_FALLBACK_WAGON = "aniqlanmadi (qo'lda ko'rik kerak)"
_FALLBACK_CARGO = "aniqlanmadi (qo'lda ko'rik kerak)"


@dataclass(frozen=True)
class ScreenResult:
    """Bitta rasm uchun skrining natijasi (kanonik API maydonlari)."""

    filename: str
    ok: bool
    wagon_type: str
    main_cargo: str
    flags: dict[str, str]          # narcotics/weapon/tobacco/other -> BOR|SHUBHALI|YO'Q
    risk_band: RiskBand
    summary_uz: str
    seconds: float
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "filename": self.filename,
            "ok": self.ok,
            "wagon_type": self.wagon_type,
            "main_cargo": self.main_cargo,
            "flags": dict(self.flags),
            "risk_band": self.risk_band,
            "summary_uz": self.summary_uz,
            "seconds": round(self.seconds, 2),
            "error": self.error,
        }


def parse(text: str) -> dict[str, str]:
    """Qwen ning ``KALIT: qiymat`` javobini structured dict ga aylantiradi.

    xray_cargo_screen.parse mantig'i bilan bir xil — faqat tan olingan
    maydonlar saqlanadi, qolgan satrlar e'tiborsiz qoldiriladi.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip().upper()
        if k in _STRUCT_FIELDS:
            out[k] = v.strip()
    return out


def _normalize_flag(value: str) -> str:
    """Modelning erkin javobini kanonik BOR/SHUBHALI/YO'Q ga keltiradi.

    Konservativ: aniq YO'Q bo'lmasa -> eskalatsiya. Bo'sh/noma'lum qiymat
    ham SHUBHALI sifatida qaraladi (model faqat eskalatsiya qila oladi).
    """
    v = value.strip().upper()
    if not v:
        return "SHUBHALI"
    if v.startswith("YO"):          # YO'Q / YOQ / YO‘Q
        return "YO'Q"
    if v.startswith("BOR") or v.startswith("HA"):
        return "BOR"
    if v.startswith("SHUB") or v.startswith("EHTIM") or v.startswith("MUMKIN"):
        return "SHUBHALI"
    # Tan olinmagan, lekin YO'Q ham emas — eskalatsiya tomon.
    return "SHUBHALI"


def risk_from_flags(flags: dict[str, str]) -> RiskBand:
    """Bayroqlardan DETERMINISTIK risk band. Modelning XAVF qatoridan EMAS."""
    values = {v.upper() for v in flags.values()}
    if "BOR" in values:
        return "high"
    if "SHUBHALI" in values:
        return "medium"
    # Barchasi YO'Q — operatorga yordam, lekin bu DEMO/decision-support: clear.
    return "clear"


@dataclass
class CargoScreener:
    """Yuk rentgen rasmini Qwen3-VL orqali skrining qiluvchi yadro.

    Bitta backend (Ollama/vLLM/...) ni qayta ishlatadi. Ko'p rasm KETMA-KET
    qayta ishlanadi (bitta GPU/CPU ni to'ldirmaslik uchun). Bloklovchi inference
    ``asyncio.to_thread`` da emas — backend allaqachon async (httpx) — biz faqat
    ketma-ket ``await`` qilamiz.
    """

    backend: VLMBackend
    guard: LanguageGuard = field(default_factory=get_guard)
    temperature: float = 0.1
    max_tokens: int = 300

    async def screen_one(self, image_bytes: bytes, filename: str) -> ScreenResult:
        """Bitta rasmni skrining qiladi. Fail-safe: xato -> ok=False, risk=medium."""
        t0 = time.monotonic()
        try:
            raw = await self._infer(image_bytes)
        except Exception as exc:  # noqa: BLE001 — Ollama timeout/transport/parse
            # Fail-safe: jim soxta "toza" emas. Konservativ medium + xato sababi.
            dt = time.monotonic() - t0
            log.warning("screen_one inference xatosi (%s): %s", filename, exc)
            return ScreenResult(
                filename=filename,
                ok=False,
                wagon_type=_FALLBACK_WAGON,
                main_cargo=_FALLBACK_CARGO,
                flags={name: "SHUBHALI" for name in _FLAG_FIELDS.values()},
                risk_band="medium",
                summary_uz=_FALLBACK_SUMMARY,
                seconds=dt,
                error=f"VLM inference xatosi: {exc}",
            )

        result = self._build_result(raw, filename, time.monotonic() - t0)
        return result

    async def screen_many(
        self, items: list[tuple[bytes, str]]
    ) -> list[ScreenResult]:
        """Ko'p rasm — KETMA-KET. Bittasi xato bersa boshqalari davom etadi."""
        results: list[ScreenResult] = []
        for image_bytes, filename in items:
            results.append(await self.screen_one(image_bytes, filename))
        return results

    # ------------------------------------------------------------------
    # Ichki
    # ------------------------------------------------------------------
    async def _infer(self, image_bytes: bytes) -> str:
        """Qwen ga rasm + promptni yuboradi, xom matnni qaytaradi."""
        messages = [
            {"role": "system", "content": SYS},
            make_image_message("user", USER, image_bytes),
        ]
        return await self.backend.generate(
            messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    def _build_result(self, raw: str, filename: str, seconds: float) -> ScreenResult:
        """Xom javobni parse qilib, guard'dan o'tkazib, kanonik natija quradi."""
        d = parse(raw)

        # 1. Bayroqlar — har biri normallashtiriladi (default eskalatsiya).
        #    To'liqsiz javob (maydon yo'q) -> SHUBHALI (model faqat eskalatsiya).
        flags: dict[str, str] = {}
        missing: list[str] = []
        for prompt_key, canon in _FLAG_FIELDS.items():
            if prompt_key in d:
                flags[canon] = _normalize_flag(d[prompt_key])
            else:
                flags[canon] = "SHUBHALI"
                missing.append(prompt_key)

        wagon = d.get("VAGON_TURI", "").strip() or _FALLBACK_WAGON
        cargo = d.get("ASOSIY_YUK", "").strip() or _FALLBACK_CARGO

        # 2. Summary — Qwen ning to'liq o'zbekcha tavsifi (xom javob).
        summary = raw.strip() or _FALLBACK_SUMMARY

        # 3. Til/xavfsizlik guard'i. Erkin matnlarni tekshiramiz — drift yoki
        #    taqiqlangan "o'tkazib yuborish" iborasi bo'lsa, matnni xavfsiz
        #    fallback bilan almashtiramiz va bayroqni eskalatsiya tomon suramiz.
        guard_tripped = False
        guard_kind = ""
        for text in (summary, cargo, wagon):
            if not text.strip():
                continue
            res = self.guard.check(text)
            if not res.passed:
                v = res.first_violation()
                # TOO_LONG — bu xavf emas, faqat uzun tavsif (summary uzun bo'lishi
                # tabiiy). Faqat haqiqiy drift/clearance/homoglif eskalatsiya qiladi.
                if v and v.kind.value == "too_long":
                    continue
                guard_tripped = True
                guard_kind = v.kind.value if v else "nomalum"
                break

        ok = True
        error: str | None = None
        if guard_tripped:
            ok = False
            error = f"guard rad etdi: {guard_kind}"
            summary = _FALLBACK_SUMMARY
            wagon = _FALLBACK_WAGON
            cargo = _FALLBACK_CARGO
            # Eskalatsiya: barcha YO'Q bo'lganini ham SHUBHALI ga ko'taramiz.
            flags = {k: ("SHUBHALI" if val == "YO'Q" else val) for k, val in flags.items()}
        elif missing:
            # Format to'liqsiz — ishonchli "toza" emas; ok=False, lekin matn qoladi.
            ok = False
            error = f"to'liqsiz javob (yo'q maydonlar: {','.join(missing)})"

        # 4. risk_band — DETERMINISTIK, bayroqlardan.
        risk = risk_from_flags(flags)

        return ScreenResult(
            filename=filename,
            ok=ok,
            wagon_type=wagon,
            main_cargo=cargo,
            flags=flags,
            risk_band=risk,
            summary_uz=summary,
            seconds=seconds,
            error=error,
        )


__all__ = [
    "CargoScreener",
    "ScreenResult",
    "RiskBand",
    "parse",
    "risk_from_flags",
    "SYS",
    "SHOT",
    "USER",
]
