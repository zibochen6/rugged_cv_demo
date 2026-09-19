"""Camera sources for the DMS demo — one frame source, no second camera open.

复刻/复用既有语义（只读 import `app.camera_source.open_source` 继承
`rtsp://` / `usb:<idx>` / `video:<file>`），并**新增**两种源：

  image:<path>  单张图片包装成一个源（首帧之后重复推同一帧），零相机冒烟
  synthetic     程序化生成帧（确定性；可注入场景），供自动化验收使用

真机协商（契约 §12 C1d）: `usb:` 源显式请求 `camera.width x camera.height`
（默认 1280x720）+ FOURCC=MJPG + `camera.fps`，随后**回读**并打印
`[dms] camera: <src> negotiated <w>x<h>@<fps> <FOURCC> (requested <w>x<h>; source: cli|config|default)`；
协商不足只打 WARNING、**不退出**，像素门控也**不**因此放宽。

互斥（契约 §3.5，硬约束 H4）: Studio 的独占只在它自己的进程内成立，本 demo
**不做**任何跨进程协商，只做"探测式互斥"——打开设备并读首帧。失败时区分两类：
  open_failed    源根本打不开（文件不存在 / RTSP 不可达）
  no_first_frame 源能打开但读不到首帧（设备被别的进程占着 / 格式不支持）
两类都给出单行可操作结论（无 traceback），由 app/dms_app.py 映射到退出码 3。
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional, Tuple

import cv2
import numpy as np

SYNTH_SCENARIOS = ("scene", "lowlight", "eyes_closed", "head_yellow")
USABLE_SOURCES = ("usb:<idx>", "rtsp://...", "video:<file>", "image:<path>",
                  "synthetic")


class CameraUnavailable(Exception):
    """reason: "open_failed" | "no_first_frame"."""

    def __init__(self, message: str, reason: str = "open_failed") -> None:
        super().__init__(message)
        self.reason = reason


class SyntheticSource:
    """Deterministic synthetic frames (no camera, no device access).

    scenario:
      scene        渐变 + 噪点 + 移动暗条（默认；没有真人脸 -> 疲劳 UNKNOWN）
      lowlight     整体很暗（演示"夜间不可用"的诚实场景）
      eyes_closed  在"人脸"位置画出深色眼带（喂给 Haar 不会检出 -> 仍 UNKNOWN）
      head_yellow  在"头部"位置画出黄色区域（供颜色启发式的可视化演示）
    """

    label = "synthetic"

    def __init__(self, width: int = 640, height: int = 480, fps: float = 30.0,
                 scenario: str = "scene", seed: int = 1234) -> None:
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps) if fps is not None else 30.0
        self.scenario = scenario if scenario in SYNTH_SCENARIOS else "scene"
        self._rng = np.random.default_rng(int(seed))
        self._fidx = 0
        self._base = self._make_base()

    def _make_base(self) -> np.ndarray:
        gradient = np.tile(
            np.linspace(40, 200, self.width, dtype=np.uint8),
            (self.height, 1))
        frame = np.dstack([gradient] * 3).astype(np.uint8)
        if self.scenario == "lowlight":
            frame = (frame * 0.12).astype(np.uint8)
        return frame

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        frame = self._base.copy()
        noise = self._rng.integers(0, 24, frame.shape, dtype=np.uint8)
        frame = cv2.add(frame, noise)
        offset = (self._fidx * 7) % max(1, self.width)
        cv2.rectangle(frame, (offset, self.height // 2),
                      (min(self.width - 1, offset + 40),
                       min(self.height - 1, self.height // 2 + 120)),
                      (60, 60, 60), -1)
        if self.scenario == "eyes_closed":
            cv2.rectangle(frame, (self.width // 3, self.height // 3),
                          (2 * self.width // 3, self.height // 3 + 40),
                          (10, 10, 10), -1)
        elif self.scenario == "head_yellow":
            cv2.rectangle(frame, (self.width // 3, self.height // 4),
                          (2 * self.width // 3, self.height // 4 + 80),
                          (0, 200, 230), -1)
        self._fidx += 1
        if self.fps > 0 and self._fidx > 1:
            time.sleep(1.0 / self.fps)
        return True, frame

    def release(self) -> None:
        return None

    def isOpened(self) -> bool:  # 与 cv2.VideoCapture 保持最小的共同接口
        return True


class ImageSource:
    """单张图片重复推流（零相机冒烟 / 可复现的静态输入）。"""

    def __init__(self, path: str, fps: float = 30.0) -> None:
        if not os.path.isfile(path):
            raise CameraUnavailable(f"image file not found: {path}",
                                    "open_failed")
        image = cv2.imread(path)
        if image is None or image.size == 0:
            raise CameraUnavailable(f"cannot decode image: {path}",
                                    "open_failed")
        self.image = image
        self.label = f"image:{os.path.basename(path)}"
        self.fps = float(fps) if fps is not None else 30.0
        self._count = 0

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        self._count += 1
        if self.fps > 0 and self._count > 1:
            time.sleep(1.0 / self.fps)
        return True, self.image.copy()

    def release(self) -> None:
        return None

    def isOpened(self) -> bool:
        return True


def open_dms_source(src: str, *, open_timeout_s: float = 8.0,
                    debug: bool = False, fps: float = 30.0,
                    width: int = 0, height: int = 0,
                    provenance: str = "config"
                    ) -> Tuple[Any, str]:
    """Open a demo source and wait for the first frame.

    Raises CameraUnavailable(reason="open_failed"|"no_first_frame")；绝不
    抛 traceback 给用户（调用方打印冻结的单行结论并退出码 3）。
    """
    source = str(src or "").strip()
    if not source:
        raise CameraUnavailable("empty camera source", "open_failed")

    if source == "synthetic" or source.startswith("synthetic:"):
        scenario = source.split(":", 1)[1] if ":" in source else "scene"
        return SyntheticSource(fps=fps, scenario=scenario), "synthetic"

    if source.startswith("image:"):
        handle: Any = ImageSource(source.split(":", 1)[1], fps=fps)
        return handle, handle.label

    cap, label = _open_capture(source, debug=debug)
    _configure_capture(cap, source, width=width, height=height,
                       fps=fps, provenance=provenance, debug=debug)
    first = _wait_first_frame(cap, source, open_timeout_s)
    if first is None:
        _release(cap)
        raise CameraUnavailable(
            f"no first frame within {float(open_timeout_s):.1f}s",
            "no_first_frame")
    if debug and source.startswith("usb:"):
        print(f"[dms] camera: opened {source} — device allowed shared access "
              "(another consumer may be active)")
    return cap, label


DEFAULT_FOURCC = "MJPG"     # 契约 §12 C1d: 真机 usb: 源默认请求 1280x720 MJPG


def _safe_set(cap: Any, prop: int, value: float) -> None:
    try:
        cap.set(prop, float(value))
    except Exception:  # noqa: BLE001 - a stubborn driver must not abort open
        pass


def _safe_get(cap: Any, prop: int) -> float:
    try:
        return float(cap.get(prop))
    except Exception:  # noqa: BLE001
        return 0.0


def _fourcc_text(value: float) -> str:
    code = int(value)
    if code <= 0:
        return "?"
    text = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    text = text.replace("\x00", "").strip()
    return text or "?"


def _configure_capture(cap: Any, src: str, *, width: int, height: int,
                       fps: float, provenance: str = "config",
                       debug: bool = False) -> None:
    """真机设备的显式协商（**不改 app/camera_source.py**）。

    契约 §12 C1d: `usb:` 源必须显式请求分辨率 + FOURCC + FPS（默认 1280x720 MJPG），
    然后**回读协商结果**并打印一行；协商不足只打 WARNING，**不退出**。
    绝不因为"unknown 多"而放宽像素门控——那等于把 unknown 伪装成判断。
    """
    if src.startswith("usb:"):
        _safe_set(cap, cv2.CAP_PROP_BUFFERSIZE, 1)
        # 顺序很关键（verifier 独立实测 + 本地实测一致）：**先 FOURCC，再 W/H，最后 FPS**。
        # 某些 V4L2 驱动在切换像素格式时会把帧尺寸重置回默认，所以宽高必须放在 FOURCC 之后；
        # 在这之后仍然要 cap.get() 回读（set 是"请求"，不是"保证"）。
        _safe_set(cap, cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*DEFAULT_FOURCC))
        if width > 0 and height > 0:
            _safe_set(cap, cv2.CAP_PROP_FRAME_WIDTH, width)
            _safe_set(cap, cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fps and fps > 0:
            _safe_set(cap, cv2.CAP_PROP_FPS, fps)
        _report_negotiation(cap, src, width, height, fps, provenance)
    elif src.startswith("rtsp"):
        # RTSP 的源由 app/camera_source.py 的 GStreamer pipeline 决定，只补 W/H
        if width > 0 and height > 0:
            _safe_set(cap, cv2.CAP_PROP_FRAME_WIDTH, width)
            _safe_set(cap, cv2.CAP_PROP_FRAME_HEIGHT, height)


def _report_negotiation(cap: Any, src: str, req_w: int, req_h: int,
                        req_fps: float, provenance: str) -> None:
    got_w = int(_safe_get(cap, cv2.CAP_PROP_FRAME_WIDTH))
    got_h = int(_safe_get(cap, cv2.CAP_PROP_FRAME_HEIGHT))
    got_fps = _safe_get(cap, cv2.CAP_PROP_FPS)
    got_cc = _fourcc_text(_safe_get(cap, cv2.CAP_PROP_FOURCC))
    fps_text = ("%g" % got_fps) if got_fps > 0 else "?"
    # 契约 §4.9/§12 C1d 冻结的一行：源名 + 实际值 + 请求值 + 请求值来源
    print("[dms] camera: %s negotiated %dx%d@%s %s (requested %dx%d; source: %s)"
          % (src, got_w, got_h, fps_text, got_cc, req_w, req_h, provenance),
          flush=True)
    if req_w > 0 and req_h > 0 and (got_w < req_w or got_h < req_h):
        # 契约 §4.9:561 冻结的头串（语义不变：只告警、不退出；也不放宽任何门控）
        print("[dms] WARNING: camera negotiated %dx%d (< requested %dx%d) — helmet "
              "may report many 'unknown'; raise --camera resolution or move closer"
              % (got_w, got_h, req_w, req_h), flush=True)


def _open_capture(src: str, debug: bool = False) -> Tuple[Any, str]:
    from app.camera_source import open_source  # 只读复用既有语义

    try:
        cap, label, _delay = open_source(src)
    except CameraUnavailable:
        raise
    except AssertionError as exc:  # open_source 用 assert 报告 RTSP 失败
        raise CameraUnavailable(str(exc) or "cannot open source",
                                "open_failed")
    except Exception as exc:  # noqa: BLE001 - 明确的单行结论，不是 traceback
        raise CameraUnavailable(f"{type(exc).__name__}: {exc}", "open_failed")
    if cap is None:
        raise CameraUnavailable("cannot open source", "open_failed")
    if hasattr(cap, "isOpened") and not cap.isOpened():
        _release(cap)
        raise CameraUnavailable("cannot open source", "open_failed")
    return cap, label


def _wait_first_frame(cap: Any, src: str, timeout_s: float
                      ) -> Optional[np.ndarray]:
    deadline = time.time() + max(0.1, float(timeout_s))
    while time.time() < deadline:
        ok = False
        frame = None
        try:
            ok, frame = cap.read()
        except Exception:  # noqa: BLE001 - a broken read is "no first frame"
            ok = False
        if ok and frame is not None and getattr(frame, "size", 0) > 0:
            return frame
        time.sleep(0.05)
    return None


def _release(cap: Any) -> None:
    try:
        cap.release()
    except Exception:  # noqa: BLE001
        pass
