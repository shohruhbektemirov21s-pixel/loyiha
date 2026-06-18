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
import os
import time
from collections import Counter
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
    "TAVSIF: <1-2 jumla: rasmda aniq nima ko'rinmoqda — vagon tuzilishi, yuk shakli, zichlik, anomaliya bo'lsa>\n"
    "VAGON_TURI: <rasmda ko'rgan vagon turi>\n"
    "ASOSIY_YUK: <rasmda ko'rgan yuk>\n"
    "QORADORI: YO'Q\nQUROL: YO'Q\nTAMAKI: YO'Q\nBOSHQA: YO'Q\nXAVF: PAST"
)
USER = (
    SHOT + "\n\nEndi BERILGAN rasmni DIQQAT bilan ko'rib chiq. Avval TAVSIF da ko'rganingni "
    "aniq bayon qil, keyin shu kuzatuvga TAYANIB qolgan maydonlarni to'ldir. Dalil bo'lmasa "
    "YO'Q yoz, qiymatlarni namunadan ko'chirma:\n"
    "TAVSIF:\nVAGON_TURI:\nASOSIY_YUK:\nQORADORI:\nQUROL:\nTAMAKI:\nBOSHQA:\nXAVF:"
)

# Bayroq kalitlari (prompt maydonlari) -> kanonik javob kaliti.
_FLAG_FIELDS: dict[str, str] = {
    "QORADORI": "narcotics",
    "QUROL": "weapon",
    "TAMAKI": "tobacco",
    "BOSHQA": "other",
}
_STRUCT_FIELDS = {"TAVSIF", "VAGON_TURI", "ASOSIY_YUK", "QORADORI", "QUROL", "TAMAKI", "BOSHQA", "XAVF"}

# Bayroq jiddiylik darajasi (voting + eskalatsiya uchun).
_FLAG_RANK = {"YO'Q": 0, "SHUBHALI": 1, "BOR": 2}
_RANK_FLAG = {0: "YO'Q", 1: "SHUBHALI", 2: "BOR"}

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


def _vote_text(values: list[str]) -> str:
    """Erkin matn maydoni uchun eng ko'p uchragan (mode) qiymat.

    Bo'sh qiymatlar tashlanadi. Teng kelsa — eng to'lig'i (uzunrog'i). Taqqoslash
    katta-kichik harf va ortiqcha bo'shliqqa sezgir emas.
    """
    cleaned = [v.strip() for v in values if v and v.strip()]
    if not cleaned:
        return ""
    counts = Counter(v.lower() for v in cleaned)
    best_key, best_n = counts.most_common(1)[0]
    # Bir nechta qiymat bir xil chastotada bo'lsa — eng uzunini tanlaymiz.
    tied = [v for v in cleaned if counts[v.lower()] == best_n]
    return max(tied, key=len)


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
    temperature: float = 0.2
    max_tokens: int = 512
    # Aniqlik uchun ko'p o'tish (self-consistency). Har bir rasm `passes` marta
    # mustaqil tahlil qilinadi, natijalar OVOZ berish bilan birlashtiriladi —
    # bitta o'tishdagi format/yanglish xatosi bartaraf bo'ladi. Vaqt `passes`
    # marta ko'proq, lekin aniqroq. XRAY_SCREEN_PASSES bilan sozlanadi.
    passes: int = field(
        default_factory=lambda: max(1, int(os.environ.get("XRAY_SCREEN_PASSES", "3")))
    )

    async def screen_one(self, image_bytes: bytes, filename: str) -> ScreenResult:
        """Bitta rasmni skrining qiladi (ko'p o'tishli, ovoz berish bilan).

        Fail-safe: barcha o'tishlar xato bersa -> ok=False, risk=medium.
        """
        t0 = time.monotonic()
        raws: list[str] = []
        last_exc: Exception | None = None
        for i in range(self.passes):
            try:
                raws.append(await self._infer(image_bytes))
            except Exception as exc:  # noqa: BLE001 — Ollama timeout/transport
                last_exc = exc
                log.warning("screen_one o'tish %d/%d xatosi (%s): %s",
                            i + 1, self.passes, filename, exc)

        if not raws:
            # Hamma o'tish muvaffaqiyatsiz — fail-safe, jim soxta "toza" emas.
            return ScreenResult(
                filename=filename, ok=False,
                wagon_type=_FALLBACK_WAGON, main_cargo=_FALLBACK_CARGO,
                flags={name: "SHUBHALI" for name in _FLAG_FIELDS.values()},
                risk_band="medium", summary_uz=_FALLBACK_SUMMARY,
                seconds=time.monotonic() - t0,
                error=f"VLM inference xatosi (barcha {self.passes} o'tish): {last_exc}",
            )

        return self._aggregate(raws, filename, time.monotonic() - t0)

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

    def _aggregate(self, raws: list[str], filename: str, seconds: float) -> ScreenResult:
        """Ko'p o'tish javoblarini OVOZ berish bilan birlashtiradi (self-consistency).

        - vagon_turi / asosiy_yuk: eng ko'p uchragan (mode) qiymat.
        - har bir bayroq: KONSERVATIV ko'pchilik — flag YO'Q bo'lishi uchun
          o'tishlarning ko'pchiligi YO'Q deyishi shart; aks holda eskalatsiya.
        - tavsif: guard'dan o'tgan eng to'liq (uzun) TAVSIF.
        """
        parsed = [parse(r) for r in raws]
        n = len(parsed)
        need = n // 2 + 1  # qat'iy ko'pchilik

        # --- Bayroqlar: konservativ ko'pchilik (severity bo'yicha) ---
        flags: dict[str, str] = {}
        any_missing = False
        for prompt_key, canon in _FLAG_FIELDS.items():
            ranks: list[int] = []
            for d in parsed:
                if prompt_key in d:
                    ranks.append(_FLAG_RANK[_normalize_flag(d[prompt_key])])
                else:
                    any_missing = True
                    ranks.append(_FLAG_RANK["SHUBHALI"])  # yo'q maydon -> eskalatsiya
            yoq = sum(1 for r in ranks if r == 0)
            bor = sum(1 for r in ranks if r == 2)
            if bor >= need:
                flags[canon] = "BOR"
            elif yoq >= need:
                flags[canon] = "YO'Q"          # faqat ko'pchilik YO'Q desa toza
            else:
                flags[canon] = "SHUBHALI"      # tortishuv/noaniq -> eskalatsiya

        # --- Vagon turi / asosiy yuk: eng ko'p uchragan qiymat ---
        wagon = _vote_text([d.get("VAGON_TURI", "") for d in parsed]) or _FALLBACK_WAGON
        cargo = _vote_text([d.get("ASOSIY_YUK", "") for d in parsed]) or _FALLBACK_CARGO

        # --- Tavsif (summary): guard'dan o'tgan eng to'liq TAVSIF ---
        descriptions = [d.get("TAVSIF", "").strip() for d in parsed]
        descriptions = [t for t in descriptions if t]
        # Eng uzunidan boshlab guard'dan o'tganini tanlaymiz.
        summary = ""
        for t in sorted(descriptions, key=len, reverse=True):
            if self._text_clean(t):
                summary = t
                break
        if not summary:
            # TAVSIF yo'q — eng uzun guard-toza xom javobga qaytamiz.
            for r in sorted(raws, key=len, reverse=True):
                if self._text_clean(r.strip()):
                    summary = r.strip()
                    break
        summary = summary or _FALLBACK_SUMMARY

        # --- Guard: yakuniy matnlar drift/clearance/homoglif tutsa eskalatsiya ---
        guard_tripped = False
        guard_kind = ""
        for text in (summary, cargo, wagon):
            if not text.strip():
                continue
            res = self.guard.check(text)
            if not res.passed:
                v = res.first_violation()
                if v and v.kind.value == "too_long":
                    continue  # uzun tavsif xavf emas
                guard_tripped, guard_kind = True, (v.kind.value if v else "nomalum")
                break

        ok = True
        error: str | None = None
        if guard_tripped:
            ok = False
            error = f"guard rad etdi: {guard_kind}"
            summary, wagon, cargo = _FALLBACK_SUMMARY, _FALLBACK_WAGON, _FALLBACK_CARGO
            flags = {k: ("SHUBHALI" if v == "YO'Q" else v) for k, v in flags.items()}
        elif any_missing:
            ok = False
            error = "ba'zi o'tishlarda format to'liqsiz edi (ovoz berishda eskalatsiya qilindi)"

        return ScreenResult(
            filename=filename, ok=ok, wagon_type=wagon, main_cargo=cargo,
            flags=flags, risk_band=risk_from_flags(flags),
            summary_uz=summary, seconds=seconds, error=error,
        )

    def _text_clean(self, text: str) -> bool:
        """Matn guard'dan (drift/clearance/homoglif) o'tadimi? TOO_LONG bardosh."""
        if not text.strip():
            return False
        res = self.guard.check(text)
        if res.passed:
            return True
        v = res.first_violation()
        return bool(v and v.kind.value == "too_long")


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
