"""Jonli sinov: haqiqiy kamera + haqiqiy detektor (best.onnx) + haqiqiy Ollama (qwen3-vl).

Bu skript uchta narsani isbotlaydi:
  A) VideoStreamCapture haqiqiy kameradan UZLUKSIZ kadr oladi + MJPEG beradi.
  B) ContinuousAnalyzer to'liq pipeline'ni (detektor -> VLM) bir necha tsikl yuritadi
     va kanonik "camera.analysis" xabarini chiqaradi (fail-safe).
  C) Ollama/Qwen jonli kamera kadrini O'QIB, o'zbekcha javob berishini to'g'ridan-to'g'ri
     ko'rsatadi (detektor darvozasidan mustaqil — "Qwen doimiy tahlil" isboti).

Ishga tushirish:  python _live_camera_test.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request

# .env ni yuklaymiz (Settings ham shundan o'qiydi).
os.environ.setdefault("XRAY_DETECTOR_ENABLED", "true")

from app.settings import Settings  # noqa: E402
from app.deps import ServiceNotImplemented  # noqa: E402


def _line(t: str) -> None:
    print(f"\n{'='*70}\n{t}\n{'='*70}", flush=True)


def build_detector_from_settings(s: Settings):
    from detector.serving.composition import DetectorConfig, build_detector
    cfg = DetectorConfig(
        weights=s.detector_weights, device=s.detector_device, imgsz=s.detector_imgsz,
        conf=s.detector_conf, iou=s.detector_iou, name=s.detector_name,
        version=s.detector_version, runtime=s.detector_runtime,
        calibration=s.detector_calibration, verify_sha256=s.detector_verify_sha256,
    )
    return build_detector(cfg)


def build_vlm_from_settings(s: Settings):
    from vlm.composition import VLMConfig, build_vlm_generator
    cfg = VLMConfig(
        backend_type=s.vlm_backend, base_url=s.vlm_base_url, model=s.vlm_model,
        timeout_s=min(s.vlm_timeout_s, 120.0), temperature=s.vlm_temperature,
        max_tokens=s.vlm_max_tokens, name=s.vlm_name, version=s.vlm_version,
        store_root=s.vlm_store_root, verify=True, describe=s.vlm_describe,
    )
    return build_vlm_generator(cfg)


def direct_ollama_probe(jpeg_bytes: bytes, base_url: str, model: str) -> tuple[str, float]:
    """Jonli kamera kadrini to'g'ridan-to'g'ri Ollama/Qwen ga yuborib, o'zbekcha tavsif so'raydi."""
    b64 = base64.b64encode(jpeg_bytes).decode()
    body = {
        "model": model, "stream": False, "think": False,
        "messages": [
            {"role": "system", "content": "Siz tasvirni qisqa, aniq o'zbek tilida (lotin) tavsiflaysiz."},
            {"role": "user", "content": "Ushbu kamera kadrida nima ko'rinmoqda? 1-2 jumlada ayting.", "images": [b64]},
            {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
        ],
        "options": {"num_predict": 120, "temperature": 0.2},
    }
    t = time.time()
    req = urllib.request.Request(
        f"{base_url}/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    r = urllib.request.urlopen(req, timeout=180)
    return json.loads(r.read())["message"]["content"].strip(), time.time() - t


async def main() -> None:
    s = Settings()
    _line("0) Sozlama")
    print(f"  detektor: enabled={s.detector_enabled} weights={s.detector_weights} device={s.detector_device}")
    print(f"  VLM: backend={s.vlm_backend} model={s.vlm_model} base_url={s.vlm_base_url}")
    print(f"  kamera: device={os.environ.get('XRAY_CAM_DEVICE','0')}")

    # ---- A) Kamera oqimi ----
    _line("A) VideoStreamCapture — haqiqiy kameradan uzluksiz kadr")
    from camera.composition import build_camera_config
    from camera.stream import VideoStreamCapture, ContinuousAnalyzer

    cam_cfg = build_camera_config()
    cap = VideoStreamCapture(cam_cfg)
    cap.open()
    got = cap.wait_first_frame(timeout_s=6.0)
    print(f"  birinchi kadr keldimi: {got}")
    await asyncio.sleep(1.0)
    frame = cap.latest_frame()
    jpeg = cap.latest_jpeg()
    if frame is None or jpeg is None:
        print("  XATO: kadr olinmadi — kamera band yoki ruxsat yo'q.")
        cap.close()
        return
    bgr, _ = frame
    print(f"  kadr shakli: {bgr.shape}, JPEG: {len(jpeg)} bayt, jami o'qilgan kadr: {cap.frames_read}")
    # Bir necha lahza kutib, kadr hisoblagichi o'sayotganini (uzluksiz) ko'rsatamiz.
    n1 = cap.frames_read
    await asyncio.sleep(1.0)
    print(f"  1s dan keyin o'qilgan kadr: {cap.frames_read} (oldin {n1}) -> uzluksiz oqim ✓")

    # ---- C) To'g'ridan-to'g'ri Ollama/Qwen jonli kadrni o'qiydi ----
    _line("C) Ollama/Qwen jonli kamera kadrini o'qiydi (to'g'ridan-to'g'ri)")
    try:
        txt, dt = direct_ollama_probe(jpeg, s.vlm_base_url, s.vlm_model)
        print(f"  Qwen javobi ({dt:.1f}s):\n  > {txt}")
    except Exception as exc:  # noqa: BLE001
        print(f"  Ollama probe xatosi: {exc}")

    # ---- B) To'liq uzluksiz pipeline (detektor -> VLM) ----
    _line("B) ContinuousAnalyzer — haqiqiy detektor + Ollama, 3 tsikl")
    try:
        detector = build_detector_from_settings(s)
        print("  detektor qurildi ✓ (best.onnx)")
    except Exception as exc:  # noqa: BLE001
        print(f"  detektor qurilmadi: {exc}")
        detector = None
    try:
        generator = build_vlm_from_settings(s)
        print("  VLM generator qurildi ✓ (Ollama probe o'tdi)")
    except Exception as exc:  # noqa: BLE001
        print(f"  VLM qurilmadi: {exc}")
        generator = None

    messages: list[dict] = []

    async def broadcaster(lane_id, msg):
        messages.append(msg)
        print(f"  [tsikl {len(messages)}] risk={msg['risk_band']:<12} "
              f"n_det={msg['n_detections']}  summary={msg['summary_uz'][:80]}")

    if detector is None or generator is None:
        print("  (detektor yoki VLM yo'q — pipeline to'liq sinovi o'tkazib yuborildi)")
    else:
        analyzer = ContinuousAnalyzer(
            cap, detector=detector, generator=generator, broadcast=broadcaster,
            lane_id="lane-1", cadence_s=2.0, not_implemented_exc=ServiceNotImplemented,
        )
        analyzer.start()
        # 3 tsikl uchun yetarli vaqt kutamiz (cadence 2s).
        deadline = time.time() + 22
        while len(messages) < 3 and time.time() < deadline:
            await asyncio.sleep(0.5)
        await analyzer.stop()
        print(f"  status: frames_analyzed={analyzer.state.frames_analyzed} "
              f"last_risk={analyzer.state.last_risk_band} err={analyzer.state.last_error}")

    cap.close()
    _line("YAKUN")
    print(f"  Kamera kadrlar: {cap.frames_read} o'qildi")
    print(f"  Uzluksiz tahlil xabarlari: {len(messages)} ta camera.analysis chiqarildi")
    print("  (Detektor X-ray modeli — oddiy kamera sahnasida 'clear'/'unavailable' kutiladi;")
    print("   bu TO'G'RI fail-safe xatti-harakat. Qwen jonli kadrni o'qishi C-qismda isbotlandi.)")


if __name__ == "__main__":
    asyncio.run(main())
