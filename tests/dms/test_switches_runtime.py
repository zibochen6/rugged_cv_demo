"""开关语义：set_flag 幂等、白名单、真停止（用假引擎计调用次数）、重开重置。

"真停止"用 app/dms_app.process_frame 的真实门控结构验证：关闭后两路引擎
（以及头盔路的人体检测器）**一次都不再被调用**。
"""
import numpy as np

from app.dms.config import DEFAULTS, DmsConfig, _deep, load_dms_config
from app.dms.fatigue import FatigueResult
from app.dms.state import DmsRuntime
from app.dms_app import process_frame

FRAME = np.zeros((240, 320, 3), dtype=np.uint8)
GRAY = np.zeros((240, 320), dtype=np.uint8)


class FakeFatigue:
    def __init__(self):
        self.calls = 0
        self.resets = 0

    def update(self, frame, gray):
        self.calls += 1
        return FatigueResult("NORMAL", 0.0, None)

    def reset(self):
        self.resets += 1


class FakeHelmet:
    def __init__(self):
        self.calls = 0
        self.resets = 0

    def update(self, frame, detections):
        self.calls += 1
        return []

    def reset(self):
        self.resets += 1


class FakePersonDetector:
    def __init__(self):
        self.calls = 0

    def infer(self, frame):
        self.calls += 1
        return []


def _run_frames(count, runtime, fatigue, helmet, detector):
    for index in range(count):
        process_frame(FRAME, GRAY, index + 1, runtime=runtime,
                      fatigue_engine=fatigue, helmet_engine=helmet,
                      person_detector=detector)


def test_set_flag_is_idempotent_and_rejects_unknown_names():
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True)
    runtime.set_flag("fatigue_enabled", False)
    runtime.set_flag("fatigue_enabled", False)
    assert runtime.fatigue_enabled is False
    runtime.set_flag("definitely_not_a_flag", True)      # 静默忽略
    runtime.set_flag("helmet_enabled", False)
    assert runtime.helmet_enabled is False
    snapshot = runtime.snapshot()
    assert snapshot["helmet"]["persons"] == []
    assert snapshot["helmet"]["verdict_counts"] == {
        "worn": 0, "not_worn": 0, "unknown": 0}


def test_closed_branch_really_stops_calling_engines():
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True)
    fatigue, helmet, detector = FakeFatigue(), FakeHelmet(), FakePersonDetector()

    _run_frames(3, runtime, fatigue, helmet, detector)
    assert fatigue.calls == 3 and helmet.calls == 3 and detector.calls == 3
    counts = runtime.snapshot()
    assert counts["fatigue"]["infer_count"] == 3
    assert counts["helmet"]["infer_count"] == 3
    assert counts["fatigue"]["last_infer_fidx"] == 3

    runtime.set_flag("fatigue_enabled", False)
    runtime.set_flag("helmet_enabled", False)
    frozen = runtime.snapshot()
    assert frozen["fatigue"]["state"] == "DISABLED"
    assert frozen["helmet"]["persons"] == []

    _run_frames(5, runtime, fatigue, helmet, detector)
    after = runtime.snapshot()
    assert fatigue.calls == 3                      # 真的没再调用
    assert helmet.calls == 3
    assert detector.calls == 3                     # 人体检测也停了
    assert after["fatigue"]["infer_count"] == frozen["fatigue"]["infer_count"]
    assert after["helmet"]["infer_count"] == frozen["helmet"]["infer_count"]
    assert after["fatigue"]["last_infer_fidx"] == \
        frozen["fatigue"]["last_infer_fidx"]
    assert after["fatigue"]["state"] == "DISABLED"
    assert after["helmet"]["persons"] == []

    runtime.set_flag("fatigue_enabled", True)
    runtime.set_flag("helmet_enabled", True)
    _run_frames(2, runtime, fatigue, helmet, detector)
    resumed = runtime.snapshot()
    assert fatigue.calls == 5 and helmet.calls == 5 and detector.calls == 5
    assert resumed["fatigue"]["infer_count"] > after["fatigue"]["infer_count"]


def test_apply_config_whitelist_keeps_invalid_values():
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True)
    snapshot = runtime.apply_config({"fatigue_enabled": False,
                                     "helmet_enabled": "off",     # 非 bool -> 忽略
                                     "unknown_key": True})       # 未知键 -> 忽略
    assert snapshot["fatigue"]["enabled"] is False
    assert snapshot["helmet"]["enabled"] is True
    assert snapshot["fatigue"]["state"] == "DISABLED"
    assert set(snapshot) == {"fatigue", "helmet", "thermal"}
    assert runtime.apply_config({})["fatigue"]["enabled"] is False


def test_fatigue_alarm_buzzer_defaults_silent_and_accepts_boolean_config():
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True)
    assert runtime.snapshot()["fatigue"]["alarm_buzzer"] is False
    snapshot = runtime.apply_config({"fatigue_alarm_buzzer": True})
    assert snapshot["fatigue"]["alarm_buzzer"] is True
    snapshot = runtime.apply_config({"fatigue_alarm_buzzer": "on"})
    assert snapshot["fatigue"]["alarm_buzzer"] is True


def test_snapshot_has_frozen_evidence_fields():
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True)
    runtime.note_fatigue_infer(4.2, 11)
    runtime.note_helmet_infer(18.6, 11)
    snapshot = runtime.snapshot()
    for scope in ("fatigue", "helmet"):
        for key in ("infer_count", "last_infer_fidx", "last_infer_ms",
                    "inference_fps", "result_age_s"):
            assert key in snapshot[scope]
    assert snapshot["fatigue"]["last_infer_fidx"] == 11
    assert snapshot["helmet"]["last_infer_ms"] == 18.6
    assert set(snapshot["helmet"]["verdict_counts"]) == {
        "worn", "not_worn", "unknown"}
    for forbidden in ("accuracy", "precision", "probability", "confidence",
                      "recall"):
        assert forbidden not in repr(snapshot)


def test_thermal_policy_suspends_helmet_without_disabling_user_switch():
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True)
    fatigue, helmet, detector = FakeFatigue(), FakeHelmet(), FakePersonDetector()
    process_frame(FRAME, GRAY, 1, runtime=runtime,
                  fatigue_engine=fatigue, helmet_engine=helmet,
                  person_detector=detector)
    cached = []
    before = runtime.snapshot()["helmet"]["infer_count"]

    runtime.apply_config({"thermal_state": "critical",
                          "helmet_target_fps": 0.0,
                          "helmet_suspended": True,
                          "degradation_reason": "hot"})
    result = process_frame(FRAME, GRAY, 2, runtime=runtime,
                           fatigue_engine=fatigue, helmet_engine=helmet,
                           person_detector=detector, run_helmet=False,
                           cached_helmet=cached)
    snapshot = runtime.snapshot()
    assert runtime.helmet_enabled is True
    assert runtime.helmet_inference_allowed is False
    assert snapshot["helmet"]["infer_count"] == before
    assert snapshot["helmet"]["thermal_suspended"] is True
    assert snapshot["helmet"]["inference_fps"] == 0.0
    assert snapshot["thermal"]["state"] == "critical"
    assert result.helmet == cached


def test_set_flag_never_rewrites_the_yaml_file(tmp_path):
    path = tmp_path / "dms.yaml"
    path.write_text("fatigue:\n  enabled: false\nhelmet:\n  enabled: true\n")
    before = path.read_text()
    cfg = load_dms_config(str(path))
    assert cfg.get("fatigue.enabled") is False
    runtime = DmsRuntime(fatigue_enabled=bool(cfg.get("fatigue.enabled")),
                         helmet_enabled=bool(cfg.get("helmet.enabled")))
    runtime.set_flag("fatigue_enabled", True)
    assert runtime.fatigue_enabled is True
    assert path.read_text() == before            # yaml 是初始值真源，不被热改

    merged = DmsConfig(_deep(DEFAULTS, {}))
    assert merged is not None
