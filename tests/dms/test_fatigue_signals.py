"""疲劳信号层：ROI 裁剪边界、暗区占比、min_face_h_px 门控（不依赖相机）。"""
import numpy as np

from app.dms.config import DEFAULTS, DmsConfig, _deep
from app.dms.face import FaceTrack, crop_norm, dark_ratio
from app.dms.fatigue import STATE_NORMAL, STATE_UNKNOWN, FatigueEngine


def make_cfg(**overrides) -> DmsConfig:
    return DmsConfig(_deep(DEFAULTS, overrides))


class StubFaceDetector:
    """最小人脸检测桩：只返回预设的人脸框（不碰 Haar / 不碰相机）。"""

    def __init__(self, box):
        self.box = box
        self.resets = 0

    def update(self, gray):
        if self.box is None:
            return []
        return [FaceTrack(self.box, 1)]

    def main(self, tracks):
        return tracks[0] if tracks else None

    def reset(self):
        self.resets += 1


def test_crop_norm_roi_geometry():
    gray = np.zeros((200, 200), dtype=np.uint8)
    roi, box = crop_norm(gray, (40, 40, 100, 100), (0.15, 0.85), (0.20, 0.52))
    assert box == (55, 60, 70, 32)
    assert roi.shape == (32, 70)


def test_crop_norm_clamps_out_of_frame_roi():
    gray = np.zeros((50, 50), dtype=np.uint8)
    roi, box = crop_norm(gray, (-40, -40, 100, 100), (0.15, 0.85),
                         (0.20, 0.52))
    assert roi is not None and roi.size > 0
    assert box[0] >= 0 and box[1] >= 0
    assert box[0] + box[2] <= 50 and box[1] + box[3] <= 50


def test_crop_norm_degenerate_box_returns_none():
    gray = np.zeros((20, 20), dtype=np.uint8)
    assert crop_norm(gray, (5, 5, 0, 10), (0.0, 1.0), (0.0, 1.0)) is None
    assert crop_norm(None, (5, 5, 10, 10), (0.0, 1.0), (0.0, 1.0)) is None


def test_dark_ratio_extremes():
    black = np.zeros((16, 16), dtype=np.uint8)
    white = np.full((16, 16), 255, dtype=np.uint8)
    assert dark_ratio(black, 60) == 1.0
    assert dark_ratio(white, 60) == 0.0
    assert dark_ratio(np.full((8, 8), 30, dtype=np.uint8), 60) == 1.0
    assert dark_ratio(np.full((8, 8), 200, dtype=np.uint8), 60) == 0.0
    assert dark_ratio(None, 60) is None
    half = np.zeros((10, 10), dtype=np.uint8)
    half[:5] = 255
    assert abs(dark_ratio(half, 60) - 0.5) < 1e-9


def test_min_face_h_gate_keeps_state_unknown_and_scores_nothing():
    cfg = make_cfg()
    engine = FatigueEngine(cfg, StubFaceDetector((10, 10, 100, 30)))  # h=30 < 48
    gray = np.zeros((240, 320), dtype=np.uint8)
    result = engine.update(np.zeros((240, 320, 3), dtype=np.uint8), gray)
    assert result.state == STATE_UNKNOWN
    assert result.signals is not None and result.signals.face_present is True
    assert result.signals.eye_dark_ratio is None
    assert result.signals.mouth_open_ratio is None
    assert len(engine.window) == 0        # 不合格人脸不进入疲劳计分
    assert engine.ema == 0.0


class _StubCascade:
    """模拟 cv2.CascadeClassifier：有检出时返回 ndarray（真机行为）。"""

    def __init__(self, result):
        self.result = result

    def empty(self):
        return False

    def detectMultiScale(self, *_args, **_kwargs):
        return self.result


def _stub_detector(monkeypatch, result, crosscheck=False):
    import app.dms.face as face_mod

    monkeypatch.setattr(face_mod, "load_cascade",
                        lambda _dir, _name: _StubCascade(result))
    return face_mod.FaceDetector(haar_dir="/tmp/stub", crosscheck=crosscheck)


def test_detect_handles_numpy_cascade_result(monkeypatch):
    """回归：真机有人脸时 detectMultiScale 返回 ndarray，不允许布尔求值。"""
    detector = _stub_detector(monkeypatch, np.array([[10, 20, 30, 40]],
                                                    dtype=np.int32))
    assert detector.detect(np.zeros((100, 100), dtype=np.uint8)) == \
        [(10, 20, 30, 40)]


def test_detect_handles_empty_and_none_cascade_results(monkeypatch):
    for empty in (np.zeros((0, 4), dtype=np.int32), (), None):
        detector = _stub_detector(monkeypatch, empty)
        assert detector.detect(np.zeros((100, 100), dtype=np.uint8)) == []


def test_cascade_hits_handles_numpy_results(monkeypatch):
    detector = _stub_detector(monkeypatch, np.array([[5, 5, 12, 12]],
                                                    dtype=np.int32),
                              crosscheck=True)
    assert detector.cascade_hits(np.zeros((100, 100), dtype=np.uint8),
                                 (0, 0, 80, 80)) == (1, 1)
    detector2 = _stub_detector(monkeypatch, np.zeros((0, 4), dtype=np.int32),
                               crosscheck=True)
    assert detector2.cascade_hits(np.zeros((100, 100), dtype=np.uint8),
                                  (0, 0, 80, 80)) == (0, 0)


def test_qualified_face_with_bright_image_is_normal():
    cfg = make_cfg()
    engine = FatigueEngine(cfg, StubFaceDetector((60, 20, 120, 200)))
    gray = np.full((240, 320), 255, dtype=np.uint8)
    result = engine.update(np.dstack([gray] * 3), gray)
    assert result.state == STATE_NORMAL
    assert result.signals.eye_dark_ratio == 0.0
    assert result.signals.mouth_open_ratio == 0.0
    assert len(engine.window) == 1