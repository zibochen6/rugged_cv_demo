"""Landmark fatigue rules without requiring MediaPipe or a physical camera."""
from types import SimpleNamespace

import numpy as np

from app.dms.fatigue import (LandmarkFatigueEngine, STATE_ALARM, STATE_NORMAL,
                             STATE_UNKNOWN, STATE_WARN)
from app.dms.state import DmsRuntime


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeImage:
    def __init__(self, **_kwargs):
        pass


class FakeMp:
    ImageFormat = SimpleNamespace(SRGB=1)
    Image = FakeImage


class FakeLandmarker:
    def __init__(self):
        self.face_visible = True
        self.eye = 0.0
        self.jaw = 0.0

    def detect_for_video(self, _image, _timestamp):
        if not self.face_visible:
            return SimpleNamespace(face_landmarks=[], face_blendshapes=[],
                                   facial_transformation_matrixes=[])
        landmarks = [SimpleNamespace(x=x, y=y) for x, y in (
            (0.35, 0.25), (0.65, 0.25), (0.65, 0.75), (0.35, 0.75))]
        shapes = [
            SimpleNamespace(category_name="eyeBlinkLeft", score=self.eye),
            SimpleNamespace(category_name="eyeBlinkRight", score=self.eye),
            SimpleNamespace(category_name="jawOpen", score=self.jaw),
        ]
        return SimpleNamespace(face_landmarks=[landmarks], face_blendshapes=[shapes],
                               facial_transformation_matrixes=[])


def engine():
    clock = Clock()
    landmarker = FakeLandmarker()
    value = object.__new__(LandmarkFatigueEngine)
    value._clock = clock
    value._mp = FakeMp()
    value._landmarker = landmarker
    value.on_event = None
    value.eye_warn_s = 1.5
    value.eye_alarm_s = 3.0
    value.perclos_warn = 0.40
    value.perclos_alarm = 0.55
    value.perclos_min_window_s = 10.0
    value.yawn_warn_s = 1.5
    value.yawn_alarm_s = 3.0
    value.yawn_alarm_count = 3
    value.recovery_s = 1.0
    value.pose_yaw_deg = 30.0
    value.pose_pitch_deg = 25.0
    value.pose_warn_s = 3.0
    value.pose_alarm_s = 5.0
    value.closure_enter = 0.45
    value.yawn_enter = 0.30
    value.signal_ema_alpha = 0.35
    value.enabled = True
    value.reset()
    return value, landmarker, clock


def step(value):
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    return value.update(frame, np.zeros((120, 160), dtype=np.uint8))


def test_mouth_opens_immediately_then_escalates_by_duration():
    value, model, clock = engine()
    model.jaw = 0.35
    first = step(value)
    assert first.state == STATE_NORMAL
    assert first.signals.mouth_open is True
    assert first.signals.mouth_open_duration_s == 0.0

    clock.advance(1.6)
    warned = step(value)
    assert warned.state == STATE_WARN
    assert warned.signals.reason == "YAWN"

    clock.advance(1.6)
    alarmed = step(value)
    assert alarmed.state == STATE_ALARM
    assert alarmed.signals.mouth_open_duration_s >= 3.0


def test_eye_closure_is_visible_and_escalates_by_duration():
    value, model, clock = engine()
    model.eye = 0.50
    assert step(value).signals.eyes_closed is True
    clock.advance(1.6)
    assert step(value).state == STATE_WARN
    clock.advance(1.6)
    assert step(value).state == STATE_ALARM


def test_lost_face_clears_held_duration_before_returning():
    value, model, clock = engine()
    model.jaw = 0.35
    step(value)
    clock.advance(1.0)
    step(value)
    model.face_visible = False
    clock.advance(4.0)
    assert step(value).state == STATE_UNKNOWN

    model.face_visible = True
    returned = step(value)
    assert returned.state == STATE_NORMAL
    assert returned.signals.mouth_open_duration_s == 0.0


def test_sustained_recovery_clears_a_previous_alarm_window():
    value, model, clock = engine()
    # Simulate a mature PERCLOS alert: historical closed samples must not pin
    # the visible alert after the driver has been normal for recovery_s.
    value.perclos_min_window_s = 0.0
    model.eye = 0.50
    assert step(value).state == STATE_ALARM

    model.eye = 0.0
    clock.advance(0.1)
    step(value)
    clock.advance(1.1)
    recovered = step(value)
    assert recovered.state == STATE_NORMAL
    assert recovered.signals.reason == "RECOVERED"
    assert recovered.signals.yawn_count_60s == 0


def test_runtime_publishes_semantic_signal_fields_and_legacy_alias():
    value, model, _clock = engine()
    model.jaw = 0.35
    result = step(value)
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=False)
    runtime.set_fatigue_view(result)
    fatigue = runtime.snapshot()["fatigue"]
    assert fatigue["jaw_open"] == fatigue["mouth_open_ratio"]
    assert fatigue["mouth_open"] is True
    assert fatigue["mouth_open_duration_s"] == 0.0
