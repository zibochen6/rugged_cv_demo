"""Lightweight camera source helpers shared by all live demos."""
from __future__ import annotations

import os

import cv2


def rtsp_gst_pipeline(url: str, codec: str = "h264", latency: int = 120) -> str:
    depay = {
        "h264": "rtph264depay ! h264parse",
        "h265": "rtph265depay ! h265parse",
    }[codec]
    return (
        f"rtspsrc location={url} latency={latency} protocols=tcp ! "
        f"{depay} ! nvv4l2decoder ! nvvidconv ! "
        "video/x-raw,format=BGRx ! videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=1 sync=false max-buffers=1"
    )


def open_rtsp(url: str):
    for codec in ("h264", "h265"):
        cap = cv2.VideoCapture(
            rtsp_gst_pipeline(url, codec), cv2.CAP_GSTREAMER)
        if cap.isOpened():
            return cap, f"rtsp[{codec}]:{url}"
        cap.release()
    return None, f"rtsp open failed (h264/h265): {url}"


def v4l2_index(src: str):
    """Return the V4L2 index for a live USB source, else None.

    `usb:2` and `/dev/video2` name the same device. Only the `usb:` spelling
    used to select the V4L2 backend; the `/dev/videoN` spelling fell through to
    the file branch below, where `cv2.VideoCapture(path)` let OpenCV pick its own
    backend — on this JetPack build that is GStreamer, whose default pipeline for
    a bare device path fails ("Could not read from resource", pipeline never
    created). The module then died with "no first frame within 8.0s" and exit
    code 3 even though the camera was perfectly usable.
    """
    value = (src or "").strip()
    if value.startswith("usb:"):
        value = value.split(":", 1)[1]
    elif value.startswith("/dev/video"):
        value = value[len("/dev/video"):]
    else:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def open_source(src: str, exposure="auto"):
    if src.startswith("rtsp:") and not src.startswith("rtsp://"):
        src = "rtsp://" + src.split(":", 1)[1]
    if src.startswith("rtsp://"):
        url = os.environ.get("SEG_DEMO_RTSP_URL", src)
        cap, label = open_rtsp(url)
        assert cap is not None, f"cannot open RTSP source {url} ({label})"
        return cap, label, 0.0
    idx = v4l2_index(src)
    if idx is not None:
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FPS, 30)
        if exposure != "auto":
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
            if isinstance(exposure, (int, float)):
                cap.set(cv2.CAP_PROP_EXPOSURE, float(exposure))
        # Always report the canonical `usb:<idx>` form, whatever was passed in.
        return cap, f"usb:{idx}", 0.0
    path = src.split(":", 1)[1] if src.startswith("video:") else src
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    return cap, f"video:{os.path.basename(path)}", 1.0 / fps if fps > 0 else 0.0
