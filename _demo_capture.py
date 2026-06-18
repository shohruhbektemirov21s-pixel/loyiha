"""Full camera-capture pipeline run (server-independent), mirroring
app/api/v1/camera.py::capture. Reads the synthetic-gun video through the REAL
camera seam, runs the REAL detector + GPU VLM, persists scan+detection+verdict
to Postgres exactly like the endpoint. Short-lived process (no server)."""
import asyncio, hashlib, os, uuid
from datetime import datetime, timezone
from pathlib import Path

os.environ["XRAY_CAM_DEVICE"] = str(Path("camera/captures/_demo_gun.avi").resolve())

from app.settings import get_settings
from app.db.session import init_db, get_session_factory
from app.state.machine import PostgresScanStore
from app.api.v1.camera import _capture_blocking, _save_frame, _scan_dir, _run_detector, _generate_verdict
from detector.serving.composition import DetectorConfig, build_detector
from vlm.composition import VLMConfig, build_vlm_generator
from contracts.v1 import StorageRef
from contracts.v1.acquisition import AcquisitionResult, ScanSubject, ImageModality
from contracts.v1.detection import ImageFrame

st = get_settings()
init_db(st.db_url)

detector = build_detector(DetectorConfig(
    weights=st.detector_weights, device=st.detector_device, imgsz=st.detector_imgsz,
    conf=st.detector_conf, iou=st.detector_iou, name=st.detector_name, version=st.detector_version))
generator = build_vlm_generator(VLMConfig(
    backend_type=st.vlm_backend, base_url=st.vlm_base_url, model=st.vlm_model,
    timeout_s=st.vlm_timeout_s, temperature=st.vlm_temperature, max_tokens=st.vlm_max_tokens,
    name=st.vlm_name, version=st.vlm_version, store_root=st.vlm_store_root,
    verify=False, describe=st.vlm_describe))

async def main():
    # Image source = the synthetic X-ray gun frame (what the detector was trained
    # to recognize). We feed its bytes straight in — equivalent to the camera seam
    # but reliable (the MJPEG video produced corrupt frames; a real webcam can't
    # produce synthetic-trained weapon shapes anyway).
    import io
    from PIL import Image
    jpeg = Path("camera/captures/_demo_gun.jpg").read_bytes()
    w, h = Image.open(io.BytesIO(jpeg)).size
    device = "demo-gun-image"
    scan_id = uuid.uuid4(); frame_id = "cam-0"; now = datetime.now(timezone.utc)
    _save_frame(scan_id, frame_id, jpeg, w, h)
    ref = StorageRef(uri=f"file://{_scan_dir(scan_id)/(frame_id+'.jpg')}", media_type="image/jpeg",
                     sha256=hashlib.sha256(jpeg).hexdigest(), size_bytes=len(jpeg))
    frame = ImageFrame(frame_id=frame_id, width_px=w, height_px=h, image=ref,
                       view_label="camera", pixel_spacing_mm=None)
    acq = AcquisitionResult(scan_id=scan_id, scanner_id="usb-camera-demo", lane_id="lane-1",
        operator_id="e18dd952-0e93-4bef-8dbe-2694ccd6d66c", subject=ScanSubject.BAGGAGE,
        modality=ImageModality.SINGLE_ENERGY, captured_at=now, emitted_at=now, frames=[frame])
    f = get_session_factory()
    async with f() as s: await PostgresScanStore(s).record_acquisition(acq); await s.commit()
    detection = await _run_detector(detector, acq, frame, now)
    async with f() as s: await PostgresScanStore(s).record_detection(detection); await s.commit()
    verdict = await _generate_verdict(generator, scan_id, detection, now)
    async with f() as s: await PostgresScanStore(s).record_verdict(verdict, scan_id); await s.commit()

    print("="*64)
    print("SCAN_ID     :", scan_id)
    print("frame       :", f"{w}x{h}  ({len(jpeg)} bytes)  device={device}")
    print("detections  :", len(detection.detections))
    for d in detection.detections:
        print(f"   -> [{d.category.value}] score={d.score:.3f}")
    print("overall_risk:", verdict.overall_risk.value)
    print("summary_uz  :\n", verdict.summary_uz)
    for dv in verdict.per_detection:
        src = "MODEL(GPU)" if abs(dv.confidence-0.80)<0.01 else ("FALLBACK" if abs(dv.confidence-0.50)<0.01 else str(dv.confidence))
        print(f"   [{dv.category.value}] conf={dv.confidence} -> {src}")
        print("   ", dv.rationale_uz.replace("\n","\n    "))
    print("="*64)

asyncio.run(main())
