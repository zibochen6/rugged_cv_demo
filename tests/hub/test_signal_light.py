"""Protocol and priority tests for the shared USB alarm light."""
from __future__ import annotations

import unittest

from backend.app.hub.signal_light import (
    ALL_OFF,
    BUZZER_INTERMITTENT,
    RED_FLASH,
    RED_FLASH_BUZZER,
    YELLOW_FLASH,
    SignalLightController,
)


class TestSignalLightController(unittest.TestCase):
    def setUp(self):
        self.frames = []
        self.light = SignalLightController(writer=self.frames.append)

    def test_fatigue_flashes_yellow_and_defaults_silent(self):
        self.light.update_fatigue("DROWSY_WARN")
        self.assertEqual(self.frames, [ALL_OFF, YELLOW_FLASH])
        self.assertEqual(self.light.snapshot()["mode"], "fatigue")
        self.assertFalse(self.light.snapshot()["buzzer"])

    def test_fatigue_buzzer_is_operator_controlled(self):
        self.light.update_fatigue("ALARM", buzzer=True)
        self.assertEqual(self.frames, [ALL_OFF, YELLOW_FLASH, BUZZER_INTERMITTENT])
        self.light.update_fatigue("NORMAL", buzzer=True)
        self.assertEqual(self.frames[-1], ALL_OFF)
        self.assertEqual(self.light.snapshot()["mode"], "off")

    def test_rear_danger_has_priority_then_fatigue_resumes(self):
        self.light.update_fatigue("DROWSY_ALARM", buzzer=True)
        self.light.update_rear("DANGER", buzzer=False)
        self.assertEqual(self.frames[-2:], [ALL_OFF, RED_FLASH])
        self.light.update_fatigue("DROWSY_ALARM", buzzer=True)
        self.assertEqual(self.frames[-1], RED_FLASH)
        self.light.clear_rear()
        self.assertEqual(self.frames[-3:], [ALL_OFF, YELLOW_FLASH, BUZZER_INTERMITTENT])

    def test_rear_buzzer_uses_combined_red_command(self):
        self.light.update_rear("DANGER", buzzer=True)
        self.assertEqual(self.frames, [ALL_OFF, RED_FLASH_BUZZER])

    def test_serial_errors_are_non_fatal(self):
        def fail(_frame):
            raise OSError("disconnected")

        light = SignalLightController(writer=fail)
        light.update_fatigue("ALARM")
        self.assertIn("OSError", light.snapshot()["last_error"])


if __name__ == "__main__":
    unittest.main()
