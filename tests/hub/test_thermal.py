from backend.app.hub.thermal import ThermalController


def test_escalates_immediately_and_recovers_one_level_after_hold():
    values = {"temp": 70.0, "now": 0.0}
    controller = ThermalController(
        reader=lambda: values["temp"],
        clock=lambda: values["now"],
        recovery_hold_s=30.0,
    )

    assert controller.update()["state"] == "normal"
    values["temp"] = 88.0
    constrained = controller.update()
    assert constrained["state"] == "constrained"
    assert constrained["policy"]["front_fps"] == 4.0
    assert constrained["policy"]["dms_helmet_fps"] == 1.0
    values["temp"] = 89.0
    critical = controller.update()
    assert critical["state"] == "critical"
    assert critical["policy"]["front_fps"] == 1.0
    assert critical["policy"]["rear_depth_fps"] == 8.0
    assert critical["policy"]["rear_person_fps"] == 4.0
    assert critical["policy"]["dms_helmet_suspended"] is True

    values["temp"] = 84.0
    assert controller.update()["state"] == "critical"
    values["now"] = 29.0
    assert controller.update()["state"] == "critical"
    values["now"] = 30.0
    assert controller.update()["state"] == "constrained"
    values["now"] = 59.0
    assert controller.update()["state"] == "constrained"
    values["now"] = 60.0
    assert controller.update()["state"] == "normal"


def test_mid_band_cancels_recovery_timer():
    values = {"temp": 89.0, "now": 0.0}
    controller = ThermalController(
        reader=lambda: values["temp"],
        clock=lambda: values["now"],
        recovery_hold_s=30.0,
    )
    assert controller.update()["state"] == "critical"
    values.update(temp=84.0, now=1.0)
    controller.update()
    values.update(temp=86.0, now=20.0)
    assert controller.update()["state"] == "critical"
    values.update(temp=84.0, now=50.0)
    assert controller.update()["state"] == "critical"
    values["now"] = 80.0
    assert controller.update()["state"] == "constrained"


if __name__ == "__main__":
    for name, fn in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("GATE: PASS")
