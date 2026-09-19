"""Visual Hub occupancy, events, and lifecycle tests."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backend.app.hub.events import EventBus, classify_fatigue, classify_helmet, classify_rear_level
from backend.app.hub.occupancy import OccupancyError, OccupancyManager
from backend.app.hub.runtime import HubRuntime, set_hub_runtime
from backend.app.hub.config import HubConfig
from backend.app.api import hub as hub_api
from backend.app.segment import api as segment_api
from backend.app.recording.runtime import RecordingRuntime, set_recording_runtime
from fastapi import HTTPException


class TestOccupancy(unittest.TestCase):
    def test_exclusive_same_slot(self):
        occ = OccupancyManager()
        occ.acquire("rear", "rtsp://cam", "rear")
        with self.assertRaises(OccupancyError):
            occ.acquire("rear", "rtsp://other", "front")

    def test_exclusive_same_device(self):
        occ = OccupancyManager()
        occ.acquire("rear", "rtsp://shared", "rear")
        with self.assertRaises(OccupancyError):
            occ.acquire("front", "rtsp://shared", "front")

    def test_release_allows_reuse(self):
        occ = OccupancyManager()
        occ.acquire("cabin", "usb:0", "dms")
        occ.release("dms")
        lease = occ.acquire("cabin", "usb:0", "dms")
        self.assertEqual(lease.holder, "dms")


class TestEvents(unittest.TestCase):
    def test_ring_and_since_id(self):
        bus = EventBus(path=None, maxlen=3)
        bus.emit("rear", "risk", "WARNING", "warning")
        bus.emit("rear", "risk", "DANGER", "danger")
        bus.emit("dms", "fatigue_state", "ALARM", "danger")
        bus.emit("hub", "session", "started", "info")
        recent = bus.recent(limit=10)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[0]["id"], 2)
        later = bus.recent(since_id=3)
        self.assertEqual([e["id"] for e in later], [4])

    def test_classifiers(self):
        self.assertEqual(classify_rear_level("DANGER"), "danger")
        self.assertEqual(classify_fatigue("ALARM"), "danger")
        self.assertEqual(classify_helmet("not_worn"), "danger")


class TestHubApi(unittest.TestCase):
    def setUp(self):
        cfg = HubConfig(root=tempfile.mkdtemp(), front_camera="/dev/video0",
                        rear_camera="rtsp://rear", cabin_camera="usb:0")
        self.runtime = HubRuntime(cfg)
        self.recording = RecordingRuntime(root=tempfile.mkdtemp())
        set_hub_runtime(self.runtime)
        set_recording_runtime(self.recording)

    def tearDown(self):
        self.runtime.events.close()
        set_hub_runtime(None)
        set_recording_runtime(None)

    def test_unknown_module(self):
        with self.assertRaises(HTTPException) as ctx:
            hub_api.start_module("side")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_busy_conflict(self):
        self.runtime.occupancy.acquire("rear", "rtsp://rear", "other")
        with self.assertRaises(HTTPException) as ctx:
            hub_api.start_module("rear")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "CAMERA_BUSY")

    def test_recording_mode_blocks_inference_start(self):
        self.recording.enter(self.runtime)
        with self.assertRaises(HTTPException) as ctx:
            hub_api.start_module("rear")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "RECORDING_ACTIVE")

    def test_recording_mode_blocks_direct_segment_start(self):
        self.recording.enter(self.runtime)
        with self.assertRaises(HTTPException) as ctx:
            segment_api.segment_start()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "RECORDING_ACTIVE")

    def test_status_shape(self):
        body = hub_api.hub_status()
        self.assertEqual(body["service"], "visual-hub")
        self.assertIn("front", body["modules"])
        self.assertIn("rear", body["modules"])
        self.assertIn("dms", body["modules"])
        self.assertIn("thermal", body)
        self.assertEqual(body["thermal"]["state"], "normal")
        serialized = str(body)
        self.assertNotIn("rtsp://", serialized)
        self.assertEqual(body["modules"]["rear"]["camera"], "PoE 后摄")

    def test_start_all_order_and_partial_failure(self):
        calls = []

        def start(module_id):
            calls.append(module_id)
            if module_id == "front":
                raise RuntimeError("front failed")
            return {"id": module_id, "state": "running"}

        with mock.patch.object(self.runtime, "start_module", side_effect=start):
            result = self.runtime.start_all()
        self.assertEqual(calls, ["rear", "front", "dms"])
        self.assertFalse(result["ok"])
        self.assertIn("front", result["errors"])
        self.assertEqual(result["modules"]["rear"]["state"], "running")

    def test_stop_all_order(self):
        calls = []

        def stop(module_id):
            calls.append(module_id)
            return {"id": module_id, "state": "stopped"}

        with mock.patch.object(self.runtime, "stop_module", side_effect=stop):
            result = self.runtime.stop_all()
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["dms", "front", "rear"])

    def test_stopped_front_does_not_report_stale_camera_metrics(self):
        self.runtime._states["front"]["metrics"] = {
            "camera_running": True,
            "capture_fps": 12.0,
        }
        with mock.patch("backend.app.camera.manager.get_camera_manager") as get_camera, \
                mock.patch("backend.app.segment.service.get_segment_service") as get_segment:
            self.runtime._stop_front()
        get_segment.return_value.stop.assert_called_once_with()
        get_camera.return_value.stop.assert_called_once_with()
        self.assertEqual(self.runtime.module_status("front")["metrics"], {})

    def test_critical_policy_reduces_rear_without_persisting_user_config(self):
        self.runtime._thermal_status = {
            "state": "critical",
            "reason": "hot",
            "policy": {
                "rear_depth_fps": 8.0,
                "rear_person_fps": 4.0,
            },
        }
        specs = self.runtime.config.module_specs()
        with mock.patch(
            "backend.app.hub.runtime.post_json", return_value=({}, None)
        ) as post:
            self.runtime._apply_thermal_policy(
                "stopped", "running", "stopped", specs)
        post.assert_called_once_with(
            "http://127.0.0.1:8080/api/config",
            {
                "thermal_state": "critical",
                "depth_target_fps": 8.0,
                "person_target_fps": 4.0,
                "degradation_reason": "hot",
            },
        )

    def test_rear_thresholds_are_atomic(self):
        body = hub_api.ModuleConfigBody(danger_m=3.0, warning_m=2.0)
        with self.assertRaises(HTTPException) as ctx:
            hub_api.config_module("rear", body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_dms_config_uses_reliable_control_timeout(self):
        with mock.patch(
            "backend.app.hub.runtime.post_json", return_value=({}, None)
        ) as post:
            self.runtime.apply_module_config(
                "dms", {"fatigue_alarm_buzzer": True})
        post.assert_called_once_with(
            "http://127.0.0.1:8010/api/dms_config",
            {"fatigue_alarm_buzzer": True},
            timeout=5.0,
        )

    def test_rear_child_does_not_own_the_shared_serial_light(self):
        argv = self.runtime._argv_for(self.runtime.config.module_specs()["rear"])
        self.assertIn("--alarm-mode", argv)
        self.assertEqual(argv[argv.index("--alarm-mode") + 1], "none")

    def test_event_ingest_rear(self):
        self.runtime._ingest_rear({"level": "DANGER", "distance": 0.8, "ttc": 0.4})
        events = self.runtime.events.recent()
        self.assertTrue(any(e["kind"] == "risk" and e["severity"] == "danger" for e in events))

    def test_event_ingest_dms(self):
        self.runtime._ingest_dms({
            "dms": {
                "fatigue": {"state": "ALARM", "score": 0.9, "face_present": True},
                "helmet": {"persons": [{"verdict": "not_worn"}]},
            }
        })
        kinds = {e["kind"] for e in self.runtime.events.recent()}
        self.assertIn("fatigue_state", kinds)
        self.assertIn("helmet_verdict", kinds)


if __name__ == "__main__":
    unittest.main()
