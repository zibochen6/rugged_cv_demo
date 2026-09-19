from app.web_stream import MJPEGServer


def test_runtime_config_is_shared_and_thermal_values_are_not_persisted():
    runtime = {
        "danger_m": 1.5,
        "warning_m": 3.0,
        "buzzer": False,
        "recording": False,
    }
    persisted = []
    server = MJPEGServer(
        "127.0.0.1", 0, runtime=runtime, persist=persisted.append)
    try:
        result = server.apply_config({
            "danger_m": 1.2,
            "warning_m": 2.8,
            "depth_target_fps": 8.0,
            "person_target_fps": 4.0,
            "thermal_state": "critical",
            "degradation_reason": "hot",
        })
    finally:
        server.server_close()

    assert result["ok"] is True
    assert runtime["danger_m"] == 1.2
    assert runtime["warning_m"] == 2.8
    assert runtime["depth_target_fps"] == 8.0
    assert runtime["person_target_fps"] == 4.0
    assert runtime["thermal_state"] == "critical"
    assert persisted == [{"danger_m": 1.2, "warning_m": 2.8}]
