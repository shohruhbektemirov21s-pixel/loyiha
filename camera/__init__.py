"""USB/webcam camera module — lightweight capture layer.

This module provides a standalone USB camera driver that can work alongside
or independently of the X-ray scanner acquisition pipeline.  Useful for:

  * Secondary visible-light cameras at the checkpoint (person/document capture)
  * Standalone testing without X-ray hardware
  * Trigger cameras that detect object presence on the belt

Entry points
------------
    from camera.driver import USBCameraDriver
    from camera.composition import build_camera_driver

Environment variables (also see camera/driver.py docstring):
    XRAY_CAM_DEVICE       device index or /dev/video path (default: 0)
    XRAY_CAM_WIDTH        capture width  in pixels (default: 1280)
    XRAY_CAM_HEIGHT       capture height in pixels (default: 720)
    XRAY_CAM_FPS          capture FPS (default: 30)
    XRAY_CAM_ROI          "x,y,w,h" crop region (optional)
    XRAY_CAM_ENCODE_QUAL  JPEG quality 1-95 (default: 90)
    XRAY_CAM_MOTION_THRESH pixel diff threshold for motion detect (default: 20)
    XRAY_CAM_STABLE_FRAMES frames with no motion before snapshot (default: 6)
"""

from camera.driver import USBCameraDriver, CameraConfig, CameraError
from camera.composition import build_camera_driver

__all__ = ["USBCameraDriver", "CameraConfig", "CameraError", "build_camera_driver"]
