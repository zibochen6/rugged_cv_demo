"""相机源错误路径：不依赖真实设备，也不触碰任何设备节点（A1/A5）。

覆盖两类失败（契约 §3.5）：
  open_failed     源根本打不开（文件不存在 / RTSP 不可达 / 设备拒绝 open）
  no_first_frame  能 open 但读不到首帧（本机 V4L2 允许重复 open，占用往往在
                  REQBUFS/STREAMON 阶段才失败，所以这条路径必须有确定性测试）
no_first_frame 用假采集对象 + 打桩的 open_source 复现，不碰真实设备节点。
"""
import numpy as np
import pytest

import app.camera_source as camera_source

from app.dms.camera import CameraUnavailable, SyntheticSource, open_dms_source

MISSING_IMAGE = "image:/tmp/dms_definitely_missing_image_20260915.jpg"
MISSING_VIDEO = "video:/tmp/dms_definitely_missing_video_20260915.mp4"


def test_synthetic_source_opens_and_shapes_are_stable():
    cap, label = open_dms_source("synthetic", open_timeout_s=1.0, fps=0)
    try:
        assert label == "synthetic"
        shapes = set()
        for _ in range(3):
            ok, frame = cap.read()
            assert ok is True
            assert frame is not None
            shapes.add(frame.shape)
        assert shapes == {(480, 640, 3)}
    finally:
        cap.release()


def test_synthetic_scenario_selection_and_release():
    cap, _ = open_dms_source("synthetic:lowlight", open_timeout_s=1.0, fps=0)
    assert cap.scenario == "lowlight"
    ok, frame = cap.read()
    assert ok and frame.mean() < 80            # lowlight 场景确实更暗
    cap.release()
    ok, frame = cap.read()
    assert ok is True                          # release() 后可重复调用，不崩


def test_synthetic_frames_are_deterministic_per_seed():
    first = SyntheticSource(fps=0, seed=11)
    second = SyntheticSource(fps=0, seed=11)
    _, frame_a = first.read()
    _, frame_b = second.read()
    assert np.array_equal(frame_a, frame_b)
    other = SyntheticSource(fps=0, seed=12)
    _, frame_c = other.read()
    assert not np.array_equal(frame_a, frame_c)


def test_missing_image_source_is_open_failed_without_traceback():
    with pytest.raises(CameraUnavailable) as excinfo:
        open_dms_source(MISSING_IMAGE, open_timeout_s=0.5)
    assert excinfo.value.reason == "open_failed"
    assert "not found" in str(excinfo.value) or "cannot" in str(excinfo.value)


def test_missing_video_source_is_open_failed():
    with pytest.raises(CameraUnavailable) as excinfo:
        open_dms_source(MISSING_VIDEO, open_timeout_s=0.5)
    assert excinfo.value.reason == "open_failed"


def test_undecodable_image_is_open_failed(tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image at all")
    with pytest.raises(CameraUnavailable) as excinfo:
        open_dms_source("image:%s" % broken, open_timeout_s=0.5)
    assert excinfo.value.reason == "open_failed"


class _FakeCapture:
    """最小采集对象桩：按预设帧数出帧，之后永远失败（模拟占不到流）。"""

    def __init__(self, frames: int = 0, opened: bool = True, negotiated=None):
        self.frames = int(frames)
        self.opened = bool(opened)
        self.released = False
        self.props = []
        self.negotiated = dict(negotiated or {})

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        self.props.append((int(prop), float(value)))
        # 默认"驱动照单全收"；测试可用 negotiated 覆盖成协商不足
        self.negotiated.setdefault(int(prop), float(value))
        return True

    def get(self, prop):
        return float(self.negotiated.get(int(prop), 0.0))

    def read(self):
        if self.frames > 0:
            self.frames -= 1
            return True, np.zeros((8, 8, 3), dtype=np.uint8)
        return False, None

    def release(self):
        self.released = True


def _mjpg() -> int:
    import cv2
    return int(cv2.VideoWriter_fourcc(*"MJPG"))


def _patch_open_source(monkeypatch, cap, label="usb:0"):
    monkeypatch.setattr(camera_source, "open_source",
                        lambda src, exposure="auto": (cap, label, 0.0))


def test_open_ok_but_no_first_frame_reports_no_first_frame(monkeypatch):
    cap = _FakeCapture(frames=0)          # open 成功，但一帧都读不到
    _patch_open_source(monkeypatch, cap)
    with pytest.raises(CameraUnavailable) as excinfo:
        open_dms_source("usb:0", open_timeout_s=0.3)
    assert excinfo.value.reason == "no_first_frame"
    assert "no first frame within 0.3s" in str(excinfo.value)
    assert cap.released is True           # 失败路径也必须释放采集对象


def test_first_frame_success_returns_capture_and_requests_resolution(monkeypatch):
    cap = _FakeCapture(frames=2)
    _patch_open_source(monkeypatch, cap)
    handle, label = open_dms_source("usb:0", open_timeout_s=1.0, width=1280,
                                    height=720)
    assert handle is cap and label == "usb:0"
    requested = [value for _prop, value in cap.props]
    assert 1280.0 in requested and 720.0 in requested


def test_usb_source_requests_resolution_fourcc_and_fps(monkeypatch):
    """契约 §12 C1d: usb: 源必须显式请求 W/H + FOURCC(MJPG) + FPS。"""
    import cv2
    cap = _FakeCapture(frames=2)
    _patch_open_source(monkeypatch, cap)
    open_dms_source("usb:0", open_timeout_s=1.0, width=1280, height=720,
                    fps=30.0)
    requested = dict((prop, value) for prop, value in cap.props)
    assert requested[cv2.CAP_PROP_FRAME_WIDTH] == 1280.0
    assert requested[cv2.CAP_PROP_FRAME_HEIGHT] == 720.0
    assert requested[cv2.CAP_PROP_FOURCC] == float(_mjpg())
    assert requested[cv2.CAP_PROP_FPS] == 30.0


def test_usb_negotiation_request_order_is_fourcc_then_size_then_fps(monkeypatch):
    """锁定 verifier 实测有效的顺序：FOURCC -> W/H -> FPS（驱动可能因切格式重置尺寸）。"""
    import cv2
    cap = _FakeCapture(frames=2)
    _patch_open_source(monkeypatch, cap)
    open_dms_source("usb:0", open_timeout_s=1.0, width=1280, height=720,
                    fps=30.0)
    order = [prop for prop, _value in cap.props]
    assert order.index(cv2.CAP_PROP_FOURCC) < order.index(cv2.CAP_PROP_FRAME_WIDTH)
    assert order.index(cv2.CAP_PROP_FRAME_WIDTH) < order.index(cv2.CAP_PROP_FPS)
    assert order.index(cv2.CAP_PROP_FRAME_HEIGHT) < order.index(cv2.CAP_PROP_FPS)


def test_usb_negotiation_line_is_printed(capsys, monkeypatch):
    cap = _FakeCapture(frames=2)
    _patch_open_source(monkeypatch, cap)
    open_dms_source("usb:0", open_timeout_s=1.0, width=1280, height=720,
                    fps=30.0)
    out = capsys.readouterr().out
    assert ("usb:0 negotiated 1280x720@30 MJPG "
            "(requested 1280x720; source: config)") in out
    assert "WARNING" not in out


def test_under_negotiated_mode_warns_but_still_opens(capsys, monkeypatch):
    """协商不足只打 WARNING、不退出；绝不因此放宽像素门控。"""
    import cv2
    cap = _FakeCapture(frames=2, negotiated={
        cv2.CAP_PROP_FRAME_WIDTH: 640.0,
        cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
        cv2.CAP_PROP_FPS: 30.0,
        cv2.CAP_PROP_FOURCC: float(_mjpg()),
    })
    _patch_open_source(monkeypatch, cap)
    handle, _label = open_dms_source("usb:0", open_timeout_s=1.0, width=1280,
                                     height=720, fps=30.0)
    assert handle is cap                      # 仍然正常返回，不抛异常
    out = capsys.readouterr().out
    assert ("usb:0 negotiated 640x480@30 MJPG "
            "(requested 1280x720; source: config)") in out
    assert "WARNING: camera negotiated 640x480 (< requested 1280x720)" in out


def test_non_device_sources_skip_resolution_setting(monkeypatch):
    cap = _FakeCapture(frames=1)
    _patch_open_source(monkeypatch, cap)
    handle, _label = open_dms_source("video:/tmp/x.mp4", open_timeout_s=0.5,
                                     width=1280, height=720)
    assert handle is cap and cap.props == []


def test_empty_source_is_rejected():
    with pytest.raises(CameraUnavailable):
        open_dms_source("", open_timeout_s=0.2)