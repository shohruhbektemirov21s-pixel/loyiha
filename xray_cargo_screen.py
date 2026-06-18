"""Customs cargo X-ray screening with Qwen3-VL (Uzbek output).

Runs the local Ollama qwen3-vl model over railway-wagon X-ray scans and produces
a STRUCTURED Uzbek screening report per image:
  wagon type, main cargo, and per-class contraband flags (narcotics / weapon /
  tobacco / other) as BOR / SHUBHALI / YO'Q, plus an overall risk band.

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

OLLAMA = os.environ.get("XRAY_VLM_BASE_URL", "http://127.0.0.1:11434")
MODEL  = os.environ.get("XRAY_VLM_MODEL", "qwen3-vl:4b")
PREFILL = "<think>\n\n</think>\n\n"   # skip qwen3-vl chain-of-thought (see vlm/backend.py)

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

def parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip().upper()
            if k in {"VAGON_TURI","ASOSIY_YUK","QORADORI","QUROL","TAMAKI","BOSHQA","XAVF"}:
                out[k] = v.strip()
    return out

def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "/home/kali/Рабочий стол/kas"
    images = sorted(glob.glob(os.path.join(folder, "*.jpg")) + glob.glob(os.path.join(folder, "*.png")))
    print(f"Skrining: {len(images)} ta rasm  model={MODEL}\n")
    rows = []
    flagged = 0
    for i, p in enumerate(images, 1):
        raw, dt = screen(p)
        d = parse(raw)
        hit = any(d.get(c, "YO'Q").upper().startswith(("BOR", "SHUBHALI"))
                  for c in ("QORADORI", "QUROL", "TAMAKI", "BOSHQA"))
        flagged += hit
        rows.append({"image": os.path.basename(p), "seconds": round(dt, 1), **d, "raw": raw})
        mark = "⚠️ " if hit else "✓ "
        print(f"{mark}[{i}/{len(images)}] {os.path.basename(p)}  ({dt:.0f}s)")
        print(f"    vagon={d.get('VAGON_TURI','?')} | yuk={d.get('ASOSIY_YUK','?')} | "
              f"qoradori={d.get('QORADORI','?')} qurol={d.get('QUROL','?')} "
              f"tamaki={d.get('TAMAKI','?')} boshqa={d.get('BOSHQA','?')} | xavf={d.get('XAVF','?')}")
    report = os.path.join(folder, "screening_report.json")
    json.dump(rows, open(report, "w"), ensure_ascii=False, indent=2)
    print(f"\nXulosa: {len(images)} ta rasm, {flagged} tasида shubhali belgi. Hisobot: {report}")

if __name__ == "__main__":
    main()
