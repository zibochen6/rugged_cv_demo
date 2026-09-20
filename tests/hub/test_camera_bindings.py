"""Camera binding persistence: file format, merge rules and overlay order.

A hand-edited or truncated file must never stop the hub from booting, and the
environment file must stay the factory default until an operator actually picks
something different.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from configs._runtime_store import load_cameras, save_cameras  # noqa: E402
from backend.app.hub.config import HubConfig, _apply_bindings  # noqa: E402


class TestCameraBindingStore(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "configs", "_camera_bindings.yaml")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(load_cameras(self.path), {})

    def test_round_trip(self):
        save_cameras(self.path, {"front": "rtsp://a:554/"})
        self.assertEqual(load_cameras(self.path), {"front": "rtsp://a:554/"})

    def test_unknown_roles_and_blank_values_are_dropped(self):
        save_cameras(self.path, {
            "front": "rtsp://a:554/",
            "side": "rtsp://invented:554/",
            "rear": "",
        })
        # `side` is not a role the hub supervises, and a blank value means
        # "no override" — neither may reach the caller or the file.
        self.assertEqual(load_cameras(self.path), {"front": "rtsp://a:554/"})
        self.assertNotIn("side", open(self.path, encoding="utf-8").read())

    def test_merge_keeps_previous_roles_and_foreign_sections(self):
        save_cameras(self.path, {"front": "rtsp://a:554/"})
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write("\nunrelated:\n  keep: true\n")
        save_cameras(self.path, {"dms": "usb:1"})

        self.assertEqual(load_cameras(self.path),
                         {"front": "rtsp://a:554/", "dms": "usb:1"})
        body = open(self.path, encoding="utf-8").read()
        self.assertIn("unrelated", body)
        self.assertIn("keep: true", body)

    def test_malformed_file_never_raises_and_can_be_repaired(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("cameras: [unclosed\n")
        self.assertEqual(load_cameras(self.path), {})
        save_cameras(self.path, {"front": "usb:3"})
        self.assertEqual(load_cameras(self.path), {"front": "usb:3"})

    def test_non_mapping_cameras_section_is_ignored(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("cameras: not-a-mapping\n")
        self.assertEqual(load_cameras(self.path), {})

    def test_write_is_atomic(self):
        save_cameras(self.path, {"front": "rtsp://a:554/"})
        leftovers = [name for name in os.listdir(os.path.dirname(self.path))
                     if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_file_is_not_world_readable(self):
        # The binding file carries the RTSP credential, so it must match the
        # posture of /etc/seg-demo/visual-hub.env (0600).
        save_cameras(self.path, {"front": "rtsp://admin:pw@a:554/"})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        # Rewriting an existing 0644 file must tighten it, not keep it.
        os.chmod(self.path, 0o644)
        save_cameras(self.path, {"dms": "usb:1"})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_creates_missing_parent_directory(self):
        nested = os.path.join(self.root, "a", "b", "_camera_bindings.yaml")
        save_cameras(nested, {"front": "usb:0"})
        self.assertEqual(load_cameras(nested), {"front": "usb:0"})


class TestBindingOverlay(unittest.TestCase):
    """`_apply_bindings` is the only place environment defaults meet operator
    choices, so its precedence rules are pinned here."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "configs", "_camera_bindings.yaml")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _config(self):
        return _apply_bindings(HubConfig(
            root=self.root,
            front_camera="ENV-FRONT",
            rear_camera="ENV-REAR",
            cabin_camera="usb:0",
        ))

    def test_environment_wins_when_no_file_exists(self):
        cfg = self._config()
        self.assertEqual(
            [cfg.front_camera, cfg.rear_camera, cfg.cabin_camera],
            ["ENV-FRONT", "ENV-REAR", "usb:0"])
        self.assertFalse(any(cfg.is_overridden(role)
                             for role in ("front", "rear", "dms")))

    def test_binding_file_overrides_only_the_roles_it_names(self):
        save_cameras(self.path, {"front": "BOUND-FRONT"})
        cfg = self._config()
        self.assertEqual(cfg.front_camera, "BOUND-FRONT")
        self.assertEqual(cfg.rear_camera, "ENV-REAR")
        self.assertTrue(cfg.is_overridden("front"))
        self.assertFalse(cfg.is_overridden("rear"))

    def test_selecting_the_factory_camera_again_clears_the_override(self):
        # Latched warnings would be worse than useless: the UI keys its
        # "recalibrate" hint off this flag.
        save_cameras(self.path, {"rear": "ENV-REAR"})
        cfg = self._config()
        self.assertEqual(cfg.rear_camera, "ENV-REAR")
        self.assertFalse(cfg.is_overridden("rear"))

    def test_runtime_rebind_keeps_the_flag_honest(self):
        cfg = self._config()
        cfg.set_role_camera("rear", "rtsp://new:554/")
        self.assertTrue(cfg.is_overridden("rear"))
        cfg.set_role_camera("rear", "ENV-REAR")
        self.assertFalse(cfg.is_overridden("rear"))

    def test_deleting_the_file_restores_the_environment_values(self):
        save_cameras(self.path, {"front": "BOUND-FRONT", "dms": "usb:7"})
        self.assertEqual(self._config().front_camera, "BOUND-FRONT")
        os.remove(self.path)
        cfg = self._config()
        self.assertEqual(cfg.front_camera, "ENV-FRONT")
        self.assertEqual(cfg.cabin_camera, "usb:0")

    def test_bindings_path_defaults_inside_the_project_root(self):
        cfg = HubConfig(root="/tmp/example-root")
        self.assertEqual(cfg.bindings_path(),
                         os.path.join("/tmp/example-root", "configs",
                                      "_camera_bindings.yaml"))
        cfg.bindings_file = "/var/lib/seg-demo/camera_bindings.yaml"
        self.assertEqual(cfg.bindings_path(),
                         "/var/lib/seg-demo/camera_bindings.yaml")


if __name__ == "__main__":
    unittest.main()