"""CLI utility: run the USB camera driver and print captured frame info.

Usage:
    # List available cameras
    python -m camera.cli --list

    # Capture 5 motion-triggered frames and save as JPEG files
    XRAY_CAM_DEVICE=0 python -m camera.cli --capture 5 --out /tmp/frames

    # One-shot manual capture (no motion trigger)
    XRAY_CAM_DEVICE=0 python -m camera.cli --snapshot --out /tmp/snap.jpg
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time


def cmd_list(_args) -> None:
    from camera.driver import list_cameras
    found = list_cameras()
    if not found:
        print("No cameras found (indices 0-7).")
    else:
        print(f"Found cameras at device indices: {found}")
        for i in found:
            print(f"  /dev/video{i}  (index {i})")


def cmd_capture(args) -> None:
    from camera.composition import build_camera_driver, capture_out_dir
    out_dir = pathlib.Path(args.out) if args.out else capture_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    driver = build_camera_driver()
    driver.open()
    print(f"Camera opened. Waiting for motion-triggered frames (device={driver._cfg.device})…")
    print("Move something in front of the camera to trigger capture.")

    try:
        for i in range(args.capture):
            print(f"\n[{i+1}/{args.capture}] Waiting for motion trigger…")
            frame = driver.next_frame(timeout_s=60.0)
            fname = out_dir / f"frame_{int(frame.captured_at * 1000)}.jpg"
            fname.write_bytes(frame.jpeg_bytes)
            print(
                f"  Saved: {fname}  ({frame.width}x{frame.height}  "
                f"{len(frame.jpeg_bytes)//1024}KB  motion={frame.motion_score:.1f})"
            )
    finally:
        driver.close()
    print(f"\nDone. Saved {args.capture} frames to {out_dir}")


def cmd_snapshot(args) -> None:
    from camera.composition import build_camera_driver, capture_out_dir
    out_path = (
        pathlib.Path(args.out)
        if args.out
        else capture_out_dir() / f"snapshot_{int(time.time())}.jpg"
    )

    driver = build_camera_driver()
    driver.open()
    try:
        frame = driver.capture_now()
        out_path.write_bytes(frame.jpeg_bytes)
        print(
            f"Snapshot saved: {out_path}  "
            f"({frame.width}x{frame.height}  {len(frame.jpeg_bytes)//1024}KB)"
        )
    finally:
        driver.close()


def cmd_stream(args) -> None:
    """Uzluksiz video oqimini ishga tushiradi va eng so'nggi kadr holatini chop etadi.

    Bu API'siz, lokal sinov uchun: kamera fonda uzluksiz o'qiydi. (Doimiy Qwen
    tahlili API jarayonida ishlaydi — bu yerda faqat capture ko'rsatiladi.)
    """
    from camera.composition import build_camera_config
    from camera.stream import VideoStreamCapture

    record = (args.record or "").strip().lower() in ("1", "true", "yes", "on")
    cap = VideoStreamCapture(build_camera_config(), record=record)
    cap.open()
    print(f"Video oqimi ochildi (device={cap.device}, record={cap.recording}). Ctrl+C to'xtatadi.")
    try:
        cap.wait_first_frame(5.0)
        for _ in range(args.stream):
            jpeg = cap.latest_jpeg()
            kb = len(jpeg) // 1024 if jpeg else 0
            print(f"  kadrlar={cap.frames_read}  so'nggi JPEG={kb}KB")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nTo'xtatilmoqda…")
    finally:
        cap.close()
        if cap.record_path:
            print(f"Sessiya videosi: {cap.record_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="USB camera driver CLI")
    parser.add_argument("--list",     action="store_true",   help="List available cameras")
    parser.add_argument("--capture",  type=int, default=0,   help="Capture N motion-triggered frames")
    parser.add_argument("--snapshot", action="store_true",   help="One-shot immediate capture")
    parser.add_argument("--stream",   type=int, default=0,   help="Run continuous video stream for N seconds")
    parser.add_argument("--record",   type=str, default=None, help="Record session video (1/true) with --stream")
    parser.add_argument("--out",      type=str, default=None, help="Output path/directory")
    args = parser.parse_args()

    if args.list:
        cmd_list(args)
    elif args.snapshot:
        cmd_snapshot(args)
    elif args.capture > 0:
        cmd_capture(args)
    elif args.stream > 0:
        cmd_stream(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
