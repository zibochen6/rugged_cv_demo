"""头盔启发式：三值判定、三道门控、多数票平滑（不依赖相机）。

合成头部 ROI 的构造基于**实测**的 OpenCV 颜色映射（见 docs 的验证记录）：
  纯黄 BGR(0,255,255)     -> HSV H=30,S=255,V=255；YCrCb Cb=1（不落入肤色带）
  低饱和肤色 BGR(170,180,200) -> HSV H=10,S=38（S<70 不算安全帽色）；YCrCb 在肤色带内
  暗灰 BGR(30,30,30)      -> 无彩色、V<45（暗区），判定 insufficient -> unknown
"""
import numpy as np

from app.dms.config import DEFAULTS, DmsConfig, _deep
from app.dms.helmet import (REASON_HEAD_SMALL, REASON_PERSON_SMALL,
                            REASON_ROI_CLIPPED, REASON_WARMING_UP, VERDICTS,
                            HelmetEngine, classify_head_roi)

YELLOW = np.full((24, 24, 3), (0, 255, 255), dtype=np.uint8)
SKIN = np.full((24, 24, 3), (170, 180, 200), dtype=np.uint8)
DARK_GRAY = np.full((24, 24, 3), (30, 30, 30), dtype=np.uint8)
BRIGHT_GRAY = np.full((24, 24, 3), (128, 128, 128), dtype=np.uint8)


def make_cfg(**overrides) -> DmsConfig:
    return DmsConfig(_deep(DEFAULTS, overrides))


class Det:
    """最小的人体检测结果桩（只暴露 bbox / track_id）。"""

    def __init__(self, bbox, track_id=1):
        self.bbox = bbox
        self.track_id = track_id


def test_yellow_head_roi_is_worn():
    verdict, reason, color, skin, dark, conf = classify_head_roi(YELLOW,
                                                                make_cfg())
    assert verdict == "worn"
    assert color > 0.9 and skin < 0.1
    assert conf in ("low", "mid")


def test_low_saturation_skin_roi_is_not_worn():
    # 实测: 浅肤色/低饱和肤色会命中 white 低饱和色带（V>=170, S<=40），
    # 因此 color_ratio 可能是 1.0；冻结规则用 skin_ratio > worn_skin_max 兜住，
    # 最终判 not_worn（reason=skin_evidence）。
    verdict, reason, color, skin, _, _ = classify_head_roi(SKIN, make_cfg())
    assert verdict == "not_worn"
    assert reason == "skin_evidence"
    assert skin > 0.9
    assert color >= 0.0


def test_dark_gray_roi_is_unknown():
    verdict, reason, color, skin, dark, _ = classify_head_roi(DARK_GRAY,
                                                              make_cfg())
    assert verdict == "unknown"
    assert color == 0.0 and skin == 0.0 and dark > 0.9


def test_bright_neutral_roi_follows_frozen_rule_not_worn():
    """冻结规则（无彩色 + 不暗 -> not_worn）的已知结果，文档已列失败模式。"""
    verdict, reason, _, _, dark, _ = classify_head_roi(BRIGHT_GRAY, make_cfg())
    assert verdict == "not_worn"
    assert dark == 0.0


def test_classifier_only_returns_three_values():
    for roi in (YELLOW, SKIN, DARK_GRAY, BRIGHT_GRAY, np.zeros((4, 4, 3),
                                                               np.uint8)):
        verdict, _, _, _, _, conf = classify_head_roi(roi, make_cfg())
        assert verdict in VERDICTS
        assert conf in ("low", "mid")


def test_empty_roi_is_unknown():
    assert classify_head_roi(None, make_cfg())[0] == "unknown"


def test_person_too_small_gate():
    engine = HelmetEngine(make_cfg())
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    verdicts = engine.update(frame, [Det((100, 100, 200, 60), 1)])  # h=60 < 80
    assert verdicts[0].verdict == "unknown"
    assert verdicts[0].reason == REASON_PERSON_SMALL


def test_head_roi_clipped_gate():
    engine = HelmetEngine(make_cfg())
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    verdicts = engine.update(frame, [Det((620, 50, 200, 300), 1)])
    assert verdicts[0].verdict == "unknown"
    assert verdicts[0].reason == REASON_ROI_CLIPPED


def test_head_too_small_gate():
    engine = HelmetEngine(make_cfg())
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    verdicts = engine.update(frame, [Det((100, 50, 10, 100), 1)])
    assert verdicts[0].verdict == "unknown"
    assert verdicts[0].reason == REASON_HEAD_SMALL


def _yellow_head_frame():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[50:140, 230:370] = (0, 255, 255)      # 与 Det((200,50,200,300)) 的头部 ROI 对齐
    return frame


def test_warming_up_then_majority_vote():
    engine = HelmetEngine(make_cfg())            # k_verdict_frames = 5
    frame = _yellow_head_frame()
    det = Det((200, 50, 200, 300), 7)
    verdicts = [engine.update(frame, [det])[0] for _ in range(6)]
    assert all(v.verdict == "unknown" for v in verdicts[:4])
    assert all(v.reason == REASON_WARMING_UP for v in verdicts[:4])
    assert [v.age_frames for v in verdicts[:5]] == [1, 2, 3, 4, 5]
    assert verdicts[4].verdict == "worn"
    assert verdicts[4].verdict in VERDICTS


def test_reset_clears_smoothing_state():
    engine = HelmetEngine(make_cfg())
    frame = _yellow_head_frame()
    det = Det((200, 50, 200, 300), 7)
    for _ in range(5):
        engine.update(frame, [det])
    assert engine.update(frame, [det])[0].verdict == "worn"
    engine.reset()
    after = engine.update(frame, [det])[0]
    assert after.verdict == "unknown"
    assert after.reason == REASON_WARMING_UP
    assert after.age_frames == 1


def test_no_detections_returns_empty_list():
    engine = HelmetEngine(make_cfg())
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert engine.update(frame, []) == []
    assert engine.update(frame, None) == []