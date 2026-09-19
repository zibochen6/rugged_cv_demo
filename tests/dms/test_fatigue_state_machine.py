"""疲劳状态机：迁移与迟滞、窗滚动、reset、关闭开关、无脸不计分。

全部用合成灰度图 + 人脸桩，不依赖任何相机（A1）。
"""
import numpy as np

from app.dms.config import DEFAULTS, DmsConfig, _deep
from app.dms.face import FaceTrack
from app.dms.fatigue import (STATE_ALARM, STATE_DISABLED, STATE_NORMAL,
                             STATE_UNKNOWN, STATE_WARN, FatigueEngine)

W, H = 320, 240
FACE = (60, 20, 120, 200)          # x, y, w, h  -> h=200 通过 min_face_h_px
EYE_ROWS = (60, 124)               # 0.20~0.52 of face height（见 configs/dms.yaml）
MOUTH_ROWS = (144, 204)            # 0.62~0.92


def make_cfg(**overrides) -> DmsConfig:
    return DmsConfig(_deep(DEFAULTS, overrides))


class StubFaceDetector:
    def __init__(self, box):
        self.box = box
        self.resets = 0

    def update(self, gray):
        return [] if self.box is None else [FaceTrack(self.box, 1)]

    def main(self, tracks):
        return tracks[0] if tracks else None

    def reset(self):
        self.resets += 1


class RecordingEvents:
    def __init__(self):
        self.events = []

    def __call__(self, kind, payload):
        self.events.append((kind, payload))


def gray_frame(dark_rows=()):
    image = np.full((H, W), 255, dtype=np.uint8)
    for y0, y1 in dark_rows:
        image[y0:y1, :] = 0
    return image


AWAKE = gray_frame()
EYES_CLOSED = gray_frame([EYE_ROWS])
DROWSY = gray_frame([EYE_ROWS, MOUTH_ROWS])


def fast_engine(box=FACE, **state_machine):
    cfg = make_cfg(fatigue={"state_machine": dict({
        "warn_confirm_frames": 3, "alarm_confirm_frames": 3,
        "exit_confirm_frames": 2, "warn_hold_s": 999.0}, **state_machine)})
    events = RecordingEvents()
    return FatigueEngine(cfg, StubFaceDetector(box), on_event=events), events


def test_awake_then_drowsy_then_recovery_sequence():
    engine, events = fast_engine()
    states = []

    def step(gray):
        result = engine.update(np.dstack([gray] * 3), gray)
        states.append(result.state)
        return result

    step(AWAKE)                      # 0 -> NORMAL
    step(AWAKE)
    assert states[0] == STATE_NORMAL

    for _ in range(8):               # 闭眼 + 张口 -> WARN -> ALARM
        step(DROWSY)

    assert STATE_WARN in states, states
    assert STATE_ALARM in states, states
    assert states.index(STATE_WARN) < states.index(STATE_ALARM)

    for _ in range(10):              # 清醒 -> 回落 NORMAL
        step(AWAKE)
    assert states[-1] == STATE_NORMAL
    assert states.index(STATE_ALARM) < len(states) - 1

    kinds = [kind for kind, _ in events.events]
    assert "fatigue_state" in kinds
    transitions = [payload["to"] for kind, payload in events.events
                   if kind == "fatigue_state"]
    assert STATE_WARN in transitions and STATE_ALARM in transitions
    assert STATE_NORMAL in transitions


def test_single_signal_still_climbs_through_warn_to_alarm():
    engine, _ = fast_engine()
    engine.update(np.dstack([AWAKE] * 3), AWAKE)
    states = []
    for _ in range(12):
        states.append(engine.update(np.dstack([EYES_CLOSED] * 3),
                                    EYES_CLOSED).state)
    # 只有眼带信号时 score_ema 上限 1.0（> warn_enter 0.6, > alarm_enter 0.8）
    assert STATE_WARN in states
    assert states[-1] == STATE_ALARM


def test_window_rolls_and_reset_starts_from_unknown():
    engine, _ = fast_engine()          # window_frames 由 DEFAULTS 提供
    engine.window_frames = 5
    engine.reset()
    for _ in range(9):
        engine.update(np.dstack([AWAKE] * 3), AWAKE)
    assert len(engine.window) <= 5

    engine.reset()
    assert engine.state == STATE_UNKNOWN
    assert len(engine.window) == 0 and engine.ema == 0.0


def test_no_face_is_unknown_and_not_scored():
    engine, events = fast_engine(box=None)
    result = engine.update(np.dstack([AWAKE] * 3), AWAKE)
    assert result.state == STATE_UNKNOWN
    assert result.signals.face_present is False
    assert engine.ema == 0.0
    assert len(engine.window) == 0

    # 主脸持续丢失 >= face_lost_warn_s 时写一条诚实事件
    engine.face_lost_warn_s = 0.0
    engine.update(np.dstack([AWAKE] * 3), AWAKE)
    engine.update(np.dstack([AWAKE] * 3), AWAKE)
    assert any(kind == "fatigue_face_lost" for kind, _ in events.events)
    assert all(payload["to"] == STATE_UNKNOWN
               for kind, payload in events.events
               if kind == "fatigue_state")


def test_disabled_switch_reports_disabled():
    engine, _ = fast_engine()
    engine.update(np.dstack([AWAKE] * 3), AWAKE)
    engine.set_enabled(False)
    assert engine.state == STATE_DISABLED
    result = engine.update(np.dstack([DROWSY] * 3), DROWSY)
    assert result.state == STATE_DISABLED
    assert result.score == 0.0
    assert result.signals is None
    engine.set_enabled(True)
    assert engine.state == STATE_UNKNOWN
    assert len(engine.window) == 0