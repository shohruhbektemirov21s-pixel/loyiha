"""Customs cargo X-ray screening with Qwen3-VL (Uzbek output).

  ⚠️  DEMO / PROTOTYPE — NOT PRODUCTION.  ⚠️
  This standalone script is a quick local harness for eyeballing what a 4B VLM
  says about a folder of X-ray JPEGs. It does NOT go through the production
  pipeline (contracts.v1 / detector / vlm.generator) and its flags MUST NOT be
  used to clear or hold real cargo. The production path computes the risk band
  deterministically from a trained detector and uses the VLM only for Uzbek
  TEXT, never for the BOR/SHUBHALI/YO'Q decision. See vlm/generator.py.

Runs the local Ollama qwen3-vl model over railway-wagon X-ray scans and produces
a STRUCTURED Uzbek screening report per image:
  wagon type, main cargo, and per-class contraband flags (narcotics / weapon /
  tobacco / other) as BOR / SHUBHALI / YO'Q, plus an overall risk band.

Design rule even for the demo: the VLM is a DESCRIBER, not a decider.
  * The model's per-class strings are treated as advisory TEXT only.
  * The operator-facing flag is computed CONSERVATIVELY by us, not copied from
    the model: any non-YO'Q signal (including a malformed/empty answer) flags the
    image for human review. The model can only ESCALATE, never clear.
  * Every free-text field is passed through vlm.guard.LanguageGuard so a Cyrillic
    drift, a homoglyph, or a forbidden "let it pass" phrase is caught and the
    image is flagged for review rather than silently trusted.

Engineering notes (honest limits of a 4B VLM on single-energy X-ray):
  * The model reliably reads COARSE content (wagon type, main cargo).
  * It CANNOT confirm material type (narcotics/explosive) from shape alone on a
    single-energy scan, so the prompt forces a conservative default of YO'Q and
    only raises a flag on a concretely-described visible object. A 1-shot FORMAT
    example anchors the structure without leaking its values.
  * This is decision-SUPPORT, not a detector. Reliable contraband detection needs
    a supervised model trained on LABELED contraband X-ray data (SIXray/PIDray).

Usage:
  python xray_cargo_screen.py "/home/kali/Рабочий стол/kas"
"""
from __future__ import annotations
import base64, glob, json, os, sys, time, urllib.request

from vlm.guard import get_guard

OLLAMA = os.environ.get("XRAY_VLM_BASE_URL", "http://127.0.0.1:11434")
MODEL  = os.environ.get("XRAY_VLM_MODEL", "qwen3-vl:4b")
PREFILL = "<think>\n\n</think>\n\n"   # skip qwen3-vl chain-of-thought (see vlm/backend.py)

# NOTE: prompt taxonomy + tank anti-bias kept in sync with vlm/screen.py so the
# prototype and the production screener label wagons the same way (no tank
# over-prediction). The contraband logic is unchanged.
SYS = (
 "Siz bojxona temir-yo'l rentgen (X-ray) skanlarini tahlil qiluvchi mutaxassisiz. "
 "Tasvirlar yon ko'rinishdagi vagonlar. Vazifa: vagon turini ANIQ ajratish, asosiy "
 "yukni aniqlash, hamda kontrabanda belgilarini baholash.\n"
 "\n"
 "VAGON TURLARI TAKSONOMIYASI (faqat shu ro'yxatdan birini tanla, eng mosini):\n"
 "- Ochiq bortli vagon (gondola): yuqorisi OCHIQ, atrofida bortlar; yuk tepadan ko'rinadi.\n"
 "- Yopiq (kryti) vagon: to'liq BERK to'rtburchak QUTI shaklida, yon devor va tomi bor, "
 "o'rtasida suriladigan eshik.\n"
 "- Sisterna / tank vagon: FAQAT aniq SILINDR yoki OVAL tank IDISHI ko'ringanda — "
 "yumaloq uchli, gorizontal yotgan idish.\n"
 "- Platforma + konteyner(lar): tekis platforma ustida TO'RTBURCHAK konteyner(lar).\n"
 "- Hopper vagon: pastga TORAYIB, ostidan to'kiluvchi voronka (sochiluvchi yuk).\n"
 "- Avtomobil tashuvchi: ichida AVTOMOBIL(lar) shakli aniq ko'rinadi.\n"
 "Agar hech biriga aniq mos kelmasa: \"aniqlanmadi\" deb yoz, taxmin qilma.\n"
 "\n"
 "TANK ANTI-BIAS (JUDA MUHIM): Sisterna/tank deb FAQAT aniq SILINDRIK yoki OVAL tank "
 "idishi ko'ringandagina yoz. To'rtburchak konteyner, berk quti vagon, yashik, ochiq "
 "yuk, qop, g'altak, avtomobil, yoki shakli noaniq bo'lsa — bu TANK EMAS, boshqa turni "
 "tanla. Shaklga shoshilib 'tank' DEMA.\n"
 "\n"
 "KONTRABANDA QAT'IY QOIDALARI:\n"
 "- Oddiy yuk (avtomobil, qop-jako, g'altak, metall buyum, suyuqlik idishi) "
 "KONTRABANDA EMAS — bunday holda barcha kontrabanda bandlari YO'Q.\n"
 "- Faqat ko'zga aniq tashlanadigan, tasvirlab bera oladigan dalil bo'lsagina BOR yoki SHUBHALI de.\n"
 "- Yagona energiyali X-ray faqat SHAKLNI ko'rsatadi, material turini emas. Narkotikni "
 "shakl bo'yicha tasdiqlab bo'lmaydi — faqat yashirish joyida (tank tubi, qo'sh devor) "
 "g'ayrioddiy zич massa bo'lsa SHUBHALI.\n"
 "- Taxmin qilma. Dalil bo'lmasa aniq YO'Q yoz."
)
SHOT = (
 "Quyida FAQAT format namunasi (qiymatlarni ko'chirma, har bir maydonni o'zing rasmga qarab to'ldir):\n"
 "VAGON_TURI: <taksonomiyadan eng mos tur — tank deb faqat aniq silindr/oval idish ko'rinsa>\n"
 "ASOSIY_YUK: <rasmda ko'rgan yuk>\n"
 "QORADORI: YO'Q\nQUROL: YO'Q\nTAMAKI: YO'Q\nBOSHQA: YO'Q\nXAVF: PAST"
)
USER = (
 SHOT + "\n\nEndi BERILGAN rasmni xuddi shu formatда tahlil qil. Avval vagon SHAKLINI "
 "aniqlab, VAGON_TURI ni taksonomiyadan tanla; aniq silindr/oval idish ko'rinmasa TANK "
 "DEMA. Dalil bo'lmasa YO'Q yoz, qiymatlarni namunadan ko'chirma:\n"
 "VAGON_TURI:\nASOSIY_YUK:\nQORADORI:\nQUROL:\nTAMAKI:\nBOSHQA:\nXAVF:"
)

def screen(path: str) -> tuple[str, float]:
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    body = {"model": MODEL, "stream": False, "think": False,
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": USER, "images": [b64]},
                         {"role": "assistant", "content": PREFILL}],
            "options": {"num_predict": 240, "temperature": 0.1}}
    t = time.time()
    r = urllib.request.urlopen(urllib.request.Request(
        f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}), timeout=400)
    return json.loads(r.read())["message"]["content"].strip(), time.time() - t

_CONTRABAND_KEYS = ("QORADORI", "QUROL", "TAMAKI", "BOSHQA")
_GUARD = get_guard()


def parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip().upper()
            if k in {"VAGON_TURI","ASOSIY_YUK","QORADORI","QUROL","TAMAKI","BOSHQA","XAVF"}:
                out[k] = v.strip()
    return out


def decide_flag(raw: str, d: dict) -> tuple[bool, str]:
    """Conservative flag — the model can only ESCALATE, never clear.

    Returns (flagged, reason). The image is flagged for human review when ANY of:
      * the structured answer is missing required fields (model failed to follow
        the format → we cannot trust a "clean" read, so we escalate);
      * any contraband field is not an explicit YO'Q;
      * the guard rejects the free-text (Cyrillic/homoglyph/forbidden clearance).
    We never copy a "clean" verdict from the model; absence of a YO'Q is treated
    as a hit, not as a pass.
    """
    # 1. Format completeness — a malformed answer is not a clean answer.
    missing = [k for k in _CONTRABAND_KEYS if k not in d]
    if missing:
        return True, f"to'liqsiz javob (yo'q maydonlar: {','.join(missing)})"

    # 2. Any non-YO'Q contraband signal flags. "Absence of YO'Q" == escalate.
    hits = [k for k in _CONTRABAND_KEYS
            if not d.get(k, "").strip().upper().startswith("YO")]
    if hits:
        return True, "kontrabanda belgisi: " + ",".join(hits)

    # 3. Language/safety guard over the model's own free text. A drift or a
    #    forbidden "let it pass" phrase means the output is untrustworthy →
    #    escalate to a human rather than trust the YO'Q values above.
    for field in (raw, d.get("ASOSIY_YUK", ""), d.get("VAGON_TURI", "")):
        if not field.strip():
            continue
        res = _GUARD.check(field)
        if not res.passed:
            v = res.first_violation()
            kind = v.kind.value if v else "nomalum"
            return True, f"guard rad etdi: {kind}"

    return False, "model belgisi yo'q (lekin bu DEMO — tasdiqlash uchun emas)"

def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "/home/kali/Рабочий стол/kas"
    images = sorted(glob.glob(os.path.join(folder, "*.jpg")) + glob.glob(os.path.join(folder, "*.png")))
    print(f"Skrining: {len(images)} ta rasm  model={MODEL}\n")
    rows = []
    flagged = 0
    for i, p in enumerate(images, 1):
        raw, dt = screen(p)
        d = parse(raw)
        hit, reason = decide_flag(raw, d)
        flagged += hit
        rows.append({"image": os.path.basename(p), "seconds": round(dt, 1),
                     "flagged": hit, "flag_reason": reason, **d, "raw": raw})
        mark = "⚠️ " if hit else "✓ "
        print(f"{mark}[{i}/{len(images)}] {os.path.basename(p)}  ({dt:.0f}s)  {reason}")
        print(f"    vagon={d.get('VAGON_TURI','?')} | yuk={d.get('ASOSIY_YUK','?')} | "
              f"qoradori={d.get('QORADORI','?')} qurol={d.get('QUROL','?')} "
              f"tamaki={d.get('TAMAKI','?')} boshqa={d.get('BOSHQA','?')} | xavf={d.get('XAVF','?')}")
    report = os.path.join(folder, "screening_report.json")
    json.dump(rows, open(report, "w"), ensure_ascii=False, indent=2)
    print(f"\nXulosa: {len(images)} ta rasm, {flagged} tasида shubhali belgi. Hisobot: {report}")

if __name__ == "__main__":
    main()
