from backend.app.hub.events import classify_fatigue, classify_helmet


def test_unknown_dms_results_are_neutral():
    assert classify_fatigue("UNKNOWN") == "info"
    assert classify_helmet("unknown") == "info"


def test_drowsy_states_keep_safety_severity():
    assert classify_fatigue("DROWSY_WARN") == "warning"
    assert classify_fatigue("DROWSY_ALARM") == "danger"
