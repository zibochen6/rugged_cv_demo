"""Visual Hub occupancy, events, and lifecycle tests."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from configs._runtime_store import load_cameras
from backend.app.hub import inventory
from backend.app.hub.events import EventBus, classify_fatigue, classify_helmet, classify_rear_level
from backend.app.hub.occupancy import OccupancyError, OccupancyManager
from backend.app.hub.runtime import CameraBindError, HubRuntime, set_hub_runtime
from backend.app.hub.config import HubConfig, _apply_bindings
from backend.app.api import hub as hub_api
from backend.app.segment import api as segment_api
from backend.app.recording.runtime import RecordingRuntime, set_recording_runtime
from fastapi import HTTPException

FRONT_URL = "rtsp://admin:sup3rs3cret@192.168.137.20:554/"
REAR_URL = "rtsp://admin:sup3rs3cret@192.168.1.10:554/"


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


class TestCameraSelection(unittest.TestCase):
    """Per-role camera rebinding: persistence, guards, and the no-URL rule.

    `test_status_shape` above already pins "no rtsp:// in a status payload" for
    the shipped camera roles; these tests pin it for a camera the operator chose,
    which is the case that could regress by adding a `source` field to status.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        inventory.clear_probe_cache()
        self.cfg = HubConfig(root=self.root, front_camera=FRONT_URL,
                             rear_camera=REAR_URL, cabin_camera="usb:0")
        self.runtime = HubRuntime(self.cfg)
        self.recording = RecordingRuntime(root=tempfile.mkdtemp())
        set_hub_runtime(self.runtime)
        set_recording_runtime(self.recording)

    def tearDown(self):
        self.runtime.events.close()
        set_hub_runtime(None)
        set_recording_runtime(None)
        shutil.rmtree(self.root, ignore_errors=True)
        inventory.clear_probe_cache()

    # --- persistence -----------------------------------------------------

    def test_binding_is_persisted_and_reapplied_on_reload(self):
        self.runtime.set_module_camera("dms", "usb:1", restart=False)
        path = self.cfg.bindings_path()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(load_cameras(path), {"dms": "usb:1"})

        fresh = _apply_bindings(HubConfig(root=self.root, cabin_camera="usb:0"))
        self.assertEqual(fresh.cabin_camera, "usb:1")
        self.assertTrue(fresh.is_overridden("dms"))

    def test_status_reports_the_bound_camera_without_its_url(self):
        self.runtime.set_module_camera("front", "rtsp://admin:pw@10.9.9.9:554/",
                                       restart=False)
        module = hub_api.module_status("front")
        self.assertEqual(module["camera_label"], "RTSP · 10.9.9.9")
        self.assertEqual(module["camera_id"],
                         inventory.camera_id("rtsp://admin:pw@10.9.9.9:554/"))
        self.assertTrue(module["camera_configured"])
        serialized = str(hub_api.hub_status())
        self.assertNotIn("rtsp://", serialized)
        self.assertNotIn("pw@10.9.9.9", serialized)

    def test_binding_emits_a_session_event(self):
        self.runtime.set_module_camera("dms", "usb:2", restart=False)
        events = self.runtime.events.recent()
        self.assertTrue(any("切换为" in event["message"] for event in events))

    # --- guards ----------------------------------------------------------

    def test_recording_mode_refuses_a_rebind(self):
        self.recording.enter(self.runtime)
        with self.assertRaises(HTTPException) as ctx:
            hub_api.set_module_camera("dms", hub_api.CameraBindBody(source="usb:1"))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "RECORDING_ACTIVE")

    def test_camera_held_by_another_module_is_refused(self):
        self.runtime.occupancy.acquire("rear", REAR_URL, "rear")
        with self.assertRaises(HTTPException) as ctx:
            hub_api.set_module_camera("front", hub_api.CameraBindBody(source=REAR_URL))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "CAMERA_BUSY")

    def test_live_module_refuses_a_non_restarting_rebind(self):
        self.runtime._states["dms"]["state"] = "running"
        with self.assertRaises(CameraBindError) as ctx:
            self.runtime.set_module_camera("dms", "usb:1", restart=False)
        self.assertEqual(ctx.exception.code, "MODULE_RUNNING")
        # Nothing may be persisted when the request was refused.
        self.assertFalse(os.path.exists(self.cfg.bindings_path()))

    def test_unknown_camera_id_is_not_found(self):
        with self.assertRaises(HTTPException) as ctx:
            hub_api.set_module_camera("dms", hub_api.CameraBindBody(camera_id="deadbeef0000"))
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail["code"], "UNKNOWN_CAMERA")

    def test_missing_target_is_a_bad_request(self):
        with self.assertRaises(HTTPException) as ctx:
            hub_api.set_module_camera("dms", hub_api.CameraBindBody())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_incompatible_source_is_unprocessable(self):
        with self.assertRaises(HTTPException) as ctx:
            hub_api.set_module_camera("front", hub_api.CameraBindBody(source="video:/clip.mp4"))
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["code"], "CAMERA_INCOMPATIBLE")

    def test_persist_failure_rolls_back_and_reports_502(self):
        with mock.patch("backend.app.hub.runtime.save_cameras",
                        side_effect=OSError("read-only filesystem")):
            with self.assertRaises(HTTPException) as ctx:
                hub_api.set_module_camera("dms", hub_api.CameraBindBody(source="usb:9"))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["code"], "CAMERA_BIND_FAILED")
        # Memory must not run ahead of disk.
        self.assertEqual(self.cfg.cabin_camera, "usb:0")

    # --- the freeze is gone ----------------------------------------------

    def test_every_role_may_take_a_usb_camera(self):
        # No role has a source-type whitelist of its own any more.
        for role, source in (("front", "usb:0"), ("rear", "usb:1"), ("dms", "usb:2")):
            with self.subTest(role=role):
                self.runtime.set_module_camera(role, source, restart=False)
                self.assertEqual(self.cfg.role_camera(role), source)

    def test_cabin_is_no_longer_frozen_to_usb0(self):
        self.runtime.set_module_camera("dms", FRONT_URL, restart=False)
        self.assertEqual(self.cfg.cabin_camera, FRONT_URL)

    def test_dms_child_env_no_longer_pins_the_camera(self):
        self.runtime.set_module_camera("dms", "usb:4", restart=False)
        spec = self.cfg.module_specs()["dms"]
        self.assertEqual(spec.camera, "usb:4")

    # --- inventory endpoint ----------------------------------------------

    def test_camera_endpoint_redacts_and_reports_occupancy(self):
        self.runtime.occupancy.acquire("rear", REAR_URL, "rear")
        with mock.patch.object(inventory, "_tcp_reachable", return_value=True):
            body = hub_api.camera_inventory()
        self.assertTrue(body["ok"])
        serialized = str(body)
        self.assertNotIn("sup3rs3cret", serialized)
        self.assertNotIn("admin:sup3rs3cret", serialized)
        rear_items = [item for item in body["cameras"]
                      if item["in_use_by"] == "rear"]
        self.assertEqual(len(rear_items), 1)
        self.assertEqual(rear_items[0]["source"],
                         "rtsp://admin:***@192.168.1.10:554/")
        self.assertTrue(rear_items[0]["reachable"])

    def test_probe_endpoint_classifies_a_manual_source(self):
        clip = os.path.join(self.root, "clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"\0")
        body = hub_api.probe_camera(hub_api.CameraProbeBody(source=f"video:{clip}"))
        camera = body["camera"]
        self.assertEqual(camera["kind"], "file")
        self.assertEqual(camera["allowed_modules"], ["rear", "dms"])
        # Non-RTSP sources report path existence, never an RTSP port probe.
        self.assertIs(camera["reachable"], True)
        missing = hub_api.probe_camera(
            hub_api.CameraProbeBody(source="video:/nope/missing.mp4"))
        self.assertIs(missing["camera"]["reachable"], False)

    def test_probe_endpoint_rejects_an_empty_source(self):
        with self.assertRaises(HTTPException) as ctx:
            hub_api.probe_camera(hub_api.CameraProbeBody(source="   "))
        self.assertEqual(ctx.exception.status_code, 400)


    def test_camera_fields_carry_no_display_prose(self):
        """The UI must be able to localize every camera string it renders.

        `camera_label` is a language-neutral token string and `camera_id` is a
        hash; a role name arriving here is what made an English interface show
        Chinese.
        """
        self.runtime.set_module_camera("front", "rtsp://admin:pw@10.9.9.9:554/",
                                       restart=False)
        fields = []
        for module in hub_api.hub_status()["modules"].values():
            fields.append(str(module.get("camera_label") or ""))
            fields.append(str(module.get("camera_id") or ""))
        with mock.patch.object(inventory, "_tcp_reachable", return_value=True):
            for binding in hub_api.camera_inventory()["modules"].values():
                fields.append(str(binding.get("camera_label") or ""))
        leaked = sorted({ch for text in fields for ch in text
                         if "\u3400" <= ch <= "\u9fff"})
        self.assertEqual(leaked, [], "status leaked CJK into camera fields: %s" % leaked)

    def test_unconfigured_role_leaves_the_label_to_the_ui(self):
        cfg = HubConfig(root=self.root)
        runtime = HubRuntime(cfg)
        try:
            module = runtime.module_status("front")
            self.assertFalse(module["camera_configured"])
            self.assertEqual(module["camera_label"], "")
            self.assertIsNone(module["camera_id"])
        finally:
            runtime.events.close()


    def test_device_path_spelling_is_stored_canonically(self):
        """Typing `/dev/video2` must bind `usb:2`, and the file must agree.

        This is the self-healing path: the old spelling reached
        `open_source()`, which treated it as a file, so the module died with
        "no first frame" / exit 3 while the camera was fine.
        """
        self.runtime.set_module_camera("dms", "/dev/video2", restart=False)
        self.assertEqual(self.cfg.cabin_camera, "usb:2")
        self.assertEqual(load_cameras(self.cfg.bindings_path()), {"dms": "usb:2"})
        self.assertEqual(hub_api.module_status("dms")["camera_id"],
                         inventory.camera_id("usb:2"))

    def test_binding_a_device_path_does_not_create_a_second_camera(self):
        with mock.patch.object(inventory, "_tcp_reachable", return_value=True):
            first = hub_api.camera_inventory()
        self.runtime.set_module_camera("rear", "/dev/video0", restart=False)
        with mock.patch.object(inventory, "_tcp_reachable", return_value=True):
            second = hub_api.camera_inventory()
        ids = [item["id"] for item in second["cameras"]]
        self.assertEqual(len(ids), len(set(ids)), "a camera is listed twice")
        self.assertEqual(second["modules"]["rear"]["camera_id"],
                         inventory.camera_id("usb:0"))
        del first


    def test_front_is_not_reported_running_without_a_camera(self):
        """A dead capture must not look like a healthy module.

        `_poll_front` used to promote the front module to `running` and clear
        `last_error` whenever the segment service answered, so a camera that
        never opened showed a green indicator at 0.0 FPS with no explanation.
        """
        self.runtime._states["front"]["state"] = "running"
        self.runtime._states["front"]["last_error"] = None
        with mock.patch("backend.app.camera.manager.get_camera_manager") as get_camera, \
                mock.patch("backend.app.segment.service.get_segment_service") as get_segment:
            get_camera.return_value.is_running = False
            get_camera.return_value.metrics.return_value = {"capture_fps": 0.0}
            get_segment.return_value.status.return_value = {"state": "idle"}
            self.runtime._poll_front(self.cfg.module_specs()["front"])
        status = self.runtime.module_status("front")
        self.assertNotEqual(status["state"], "running")
        self.assertEqual(status["state"], "degraded")
        self.assertFalse(status["health_ok"])
        self.assertTrue(status["last_error"])
        self.assertFalse(status["metrics"]["camera_running"])

    def test_front_failure_keeps_its_original_error(self):
        self.runtime._states["front"]["state"] = "error"
        self.runtime._states["front"]["last_error"] = "Cannot open camera: no first frame"
        with mock.patch("backend.app.camera.manager.get_camera_manager") as get_camera, \
                mock.patch("backend.app.segment.service.get_segment_service") as get_segment:
            get_camera.return_value.is_running = False
            get_camera.return_value.metrics.return_value = {}
            get_segment.return_value.status.return_value = {}
            self.runtime._poll_front(self.cfg.module_specs()["front"])
        status = self.runtime.module_status("front")
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["last_error"], "Cannot open camera: no first frame")

    def test_front_is_running_once_the_camera_is_up(self):
        self.runtime._states["front"]["state"] = "starting"
        with mock.patch("backend.app.camera.manager.get_camera_manager") as get_camera, \
                mock.patch("backend.app.segment.service.get_segment_service") as get_segment:
            get_camera.return_value.is_running = True
            get_camera.return_value.metrics.return_value = {"capture_fps": 12.0}
            get_segment.return_value.status.return_value = {"state": "ready",
                                                            "camera_running": True}
            self.runtime._poll_front(self.cfg.module_specs()["front"])
        status = self.runtime.module_status("front")
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["health_ok"])
        self.assertIsNone(status["last_error"])
        self.assertTrue(status["metrics"]["camera_running"])


if __name__ == "__main__":
    unittest.main()
