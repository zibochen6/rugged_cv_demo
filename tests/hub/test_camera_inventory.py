"""Camera inventory: redaction, id addressing, capability matrix, probing.

The two invariants worth defending are (1) no RTSP credential may ever leave
this module in a response, and (2) building the inventory must never open a
device — that would steal the handle from the module that owns it.
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.app.hub import inventory  # noqa: E402
from backend.app.hub.config import HubConfig, canonical_source  # noqa: E402

SECRET = "sup3rs3cret"
URL = f"rtsp://admin:{SECRET}@192.168.137.20:554/"


class TestSourceTaxonomy(unittest.TestCase):
    def test_kind_of(self):
        cases = {
            URL: inventory.KIND_RTSP,
            "rtsp://192.168.1.10:554/": inventory.KIND_RTSP,
            "rtsp:192.168.1.10:554": inventory.KIND_RTSP,
            "rtsps://cam:554/": inventory.KIND_RTSP,
            "usb:0": inventory.KIND_USB,
            "/dev/video3": inventory.KIND_USB,
            "video:/data/clip.mp4": inventory.KIND_FILE,
            "image:/data/frame.jpg": inventory.KIND_FILE,
            "synthetic": inventory.KIND_TEST,
            "/data/clip.mp4": inventory.KIND_FILE,
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(inventory.kind_of(source), expected)

    def test_live_sources_are_available_to_every_role(self):
        for source in (URL, "usb:0", "/dev/video3"):
            with self.subTest(source=source):
                self.assertEqual(inventory.allowed_modules(source),
                                 ["front", "rear", "dms"])

    def test_front_cannot_take_file_or_test_sources(self):
        # CameraManager.start() only understands rtsp:// and /dev/videoN.
        for source in ("video:/data/clip.mp4", "image:/a.jpg", "synthetic"):
            with self.subTest(source=source):
                self.assertNotIn("front", inventory.allowed_modules(source))

    def test_cabin_owns_the_synthetic_and_image_sources(self):
        self.assertEqual(inventory.allowed_modules("synthetic"), ["dms"])
        self.assertEqual(inventory.allowed_modules("image:/a.jpg"), ["dms"])

    def test_empty_source_is_allowed_nowhere(self):
        self.assertEqual(inventory.allowed_modules(""), [])


class TestRedaction(unittest.TestCase):
    def test_password_is_masked_user_is_kept(self):
        self.assertEqual(inventory.redact_source(URL),
                         "rtsp://admin:***@192.168.137.20:554/")

    def test_credential_free_url_is_unchanged(self):
        self.assertEqual(inventory.redact_source("rtsp://192.168.1.10:554/"),
                         "rtsp://192.168.1.10:554/")
        self.assertEqual(inventory.redact_source("rtsps://cam:322/stream"),
                         "rtsps://cam:322/stream")

    def test_non_rtsp_sources_are_untouched(self):
        for source in ("usb:0", "video:/data/clip.mp4", "synthetic", ""):
            with self.subTest(source=source):
                self.assertEqual(inventory.redact_source(source), source)

    def test_password_survives_no_public_projection(self):
        item = inventory.describe(URL, probe=False)
        self.assertNotIn(SECRET, str(item))
        self.assertNotIn(SECRET, inventory.label_for(URL))
        self.assertNotIn(SECRET, inventory.validate("front", URL) or "")

    def test_label_carries_the_host_not_the_scheme(self):
        self.assertEqual(inventory.label_for(URL), "RTSP · 192.168.137.20")

    def test_label_keeps_a_non_default_port(self):
        self.assertEqual(inventory.label_for("rtsp://h:8554/x"), "RTSP · h:8554")

    def test_usb_label_normalises_both_writings(self):
        self.assertEqual(inventory.label_for("usb:0"), "USB · /dev/video0")
        self.assertEqual(inventory.label_for("/dev/video3"), "USB · /dev/video3")
        self.assertEqual(inventory.label_for("usb:2", "C920"),
                         "USB · /dev/video2 (C920)")


class TestCameraId(unittest.TestCase):
    def test_id_is_stable_and_opaque(self):
        first = inventory.camera_id(URL)
        self.assertEqual(first, inventory.camera_id(URL))
        self.assertEqual(len(first), 12)
        self.assertNotIn(SECRET, first)

    def test_distinct_sources_get_distinct_ids(self):
        self.assertNotEqual(inventory.camera_id("usb:0"),
                            inventory.camera_id("usb:1"))

    def test_whitespace_does_not_change_the_id(self):
        self.assertEqual(inventory.camera_id(f"  {URL} "),
                         inventory.camera_id(URL))


class TestUsbDiscovery(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        inventory.clear_probe_cache()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _fake_tree(self, cameras):
        """Build a sysfs-like tree: cameras = {usb_port: [video indices]}.

        Each node gets a real `device` symlink to a shared interface directory,
        mirroring how the kernel exposes one UVC camera as a streaming node plus
        a metadata node.
        """
        v4l = os.path.join(self.root, "video4linux")
        usb = os.path.join(self.root, "usb")
        os.makedirs(v4l, exist_ok=True)
        for port, indices in cameras.items():
            interface = os.path.join(usb, port, "%s:1.0" % port)
            os.makedirs(interface, exist_ok=True)
            with open(os.path.join(usb, port, "product"), "w",
                      encoding="utf-8") as handle:
                handle.write("1080P USB Camera\n")
            for position, index in enumerate(indices):
                node = os.path.join(v4l, "video%d" % index)
                os.makedirs(node, exist_ok=True)
                with open(os.path.join(node, "name"), "w",
                          encoding="utf-8") as handle:
                    handle.write("1080P USB Camera: 1080P USB Cam\n")
                with open(os.path.join(node, "index"), "w",
                          encoding="utf-8") as handle:
                    handle.write("%d\n" % position)
                os.symlink(interface, os.path.join(node, "device"))
        return v4l, usb

    def _patch_sysfs(self, v4l, usb):
        return (
            mock.patch.object(inventory, "_V4L2_SYSFS",
                              type(inventory._V4L2_SYSFS)(v4l)),
            mock.patch.object(inventory, "_USB_SYSFS",
                              type(inventory._USB_SYSFS)(usb)),
        )

    def test_reads_sysfs_without_opening_a_device(self):
        v4l, usb = self._fake_tree({"1-2.2": [0, 1]})
        with self._patch_sysfs(v4l, usb)[0], self._patch_sysfs(v4l, usb)[1], \
                mock.patch.object(os.path, "exists", side_effect=lambda p: True):
            found = inventory.list_usb_devices()
        self.assertEqual([item["source"] for item in found], ["usb:0"])
        # The V4L2 name repeats itself; the USB product string is preferred.
        self.assertEqual(found[0]["name"], "1080P USB Camera")
        self.assertEqual(found[0]["usb_port"], "1-2.2")
        self.assertEqual(found[0]["collapsed"], 1)

    def test_hand_typed_usb_source_carries_its_port(self):
        v4l, usb = self._fake_tree({"1-2.1": [2, 3]})
        patches = self._patch_sysfs(v4l, usb)
        with patches[0], patches[1], \
                mock.patch.object(os.path, "exists", side_effect=lambda p: True):
            item = inventory.describe("usb:2", probe=False)
        self.assertEqual(item["usb_port"], "1-2.1")
        self.assertEqual(item["label"], "USB · /dev/video2 (1080P USB Camera @1-2.1)")
        self.assertIsNone(inventory.describe("rtsp://h:554/", probe=False)["usb_port"])

    def test_metadata_nodes_are_collapsed_away(self):
        # Measured on the real hardware: index 1 of a UVC interface cannot be
        # opened at all, so listing it was a trap that ended in exit code 3.
        v4l, usb = self._fake_tree({"1-2.2": [0, 1], "1-2.1": [2, 3]})
        patches = self._patch_sysfs(v4l, usb)
        with patches[0], patches[1], \
                mock.patch.object(os.path, "exists", side_effect=lambda p: True):
            found = inventory.list_usb_devices()
        self.assertEqual([item["source"] for item in found], ["usb:0", "usb:2"])
        self.assertEqual([item["usb_port"] for item in found], ["1-2.2", "1-2.1"])
        self.assertTrue(all(item["collapsed"] == 1 for item in found))

    def test_two_identical_cameras_are_distinguishable_by_port(self):
        # Same product, same vendor, even the same serial on this hardware — the
        # USB port path is the only stable discriminator.
        v4l, usb = self._fake_tree({"1-2.2": [0, 1], "1-2.1": [2, 3]})
        patches = self._patch_sysfs(v4l, usb)
        with patches[0], patches[1], \
                mock.patch.object(os.path, "exists", side_effect=lambda p: True):
            found = inventory.list_usb_devices()
        labels = [inventory.label_for(item["source"], item["name"], item["usb_port"])
                  for item in found]
        self.assertEqual(labels, ["USB · /dev/video0 (1080P USB Camera @1-2.2)",
                                  "USB · /dev/video2 (1080P USB Camera @1-2.1)"])
        self.assertNotEqual(labels[0], labels[1])

    def test_module_never_opens_a_device(self):
        # Rule 1 of the module. Opening a camera that a module already holds
        # fails at best and steals the handle at worst, which is exactly why
        # `backend/app/camera/discovery.enumerate_devices()` is not used here.
        # Parsed rather than grepped: the docstring is allowed to name it.
        tree = ast.parse(open(inventory.__file__, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("cv2", imported)

        referenced = {node.id for node in ast.walk(tree)
                      if isinstance(node, ast.Name)}
        referenced |= {node.attr for node in ast.walk(tree)
                       if isinstance(node, ast.Attribute)}
        self.assertNotIn("enumerate_devices", referenced)
        self.assertNotIn("VideoCapture", referenced)

    def test_ignores_non_video_entries_and_stale_sysfs_nodes(self):
        v4l, usb = self._fake_tree({"1-2.2": [0, 1]})
        # A control device is not a camera, and a sysfs node whose /dev entry is
        # gone must not be offered either.
        os.makedirs(os.path.join(v4l, "controlC0"), exist_ok=True)
        patches = self._patch_sysfs(v4l, usb)
        with patches[0], patches[1], \
                mock.patch.object(os.path, "exists", return_value=False):
            self.assertEqual(inventory.list_usb_devices(), [])
        patches = self._patch_sysfs(v4l, usb)
        with patches[0], patches[1], \
                mock.patch.object(os.path, "exists", side_effect=lambda p: True):
            self.assertEqual([item["source"] for item in inventory.list_usb_devices()],
                             ["usb:0"])

    def test_missing_sysfs_directory_is_not_an_error(self):
        with mock.patch.object(inventory, "_V4L2_SYSFS",
                               type(inventory._V4L2_SYSFS)(
                                   os.path.join(self.root, "nope"))):
            self.assertEqual(inventory.list_usb_devices(), [])


class TestProbe(unittest.TestCase):
    def setUp(self):
        inventory.clear_probe_cache()

    def test_reachability_is_cached_and_bounded(self):
        with mock.patch.object(inventory, "_tcp_reachable",
                               return_value=True) as connect:
            self.assertTrue(inventory.probe_tcp(URL))
            self.assertTrue(inventory.probe_tcp(URL))
        # A polling UI must not become a scanner.
        self.assertEqual(connect.call_count, 1)

    def test_cache_expiry_reprobes(self):
        with mock.patch.object(inventory, "_tcp_reachable",
                               return_value=False) as connect:
            inventory.probe_tcp(URL, ttl=0.0)
            inventory.probe_tcp(URL, ttl=0.0)
        self.assertEqual(connect.call_count, 2)

    def test_hostless_source_returns_none(self):
        self.assertIsNone(inventory.probe_tcp("usb:0"))

    def test_unreachable_host_is_false_not_an_exception(self):
        with mock.patch.object(inventory, "_tcp_reachable", return_value=False):
            self.assertFalse(inventory.probe_tcp("rtsp://192.0.2.1:554/"))


class TestBuildInventory(unittest.TestCase):
    def setUp(self):
        inventory.clear_probe_cache()
        self.root = tempfile.mkdtemp()
        self.cfg = HubConfig(
            root=self.root,
            front_camera=URL,
            rear_camera="rtsp://admin:pw@192.168.1.10:554/",
            cabin_camera="usb:0",
            extra_cameras=[URL, "rtsp://spare:554/"],
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_candidates_deduplicate_and_keep_provenance(self):
        seen = [item["source"] for item in inventory.candidates(self.cfg)]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertIn("usb:0", seen)
        origins = {item["origin"] for item in inventory.candidates(self.cfg)}
        self.assertIn("config", origins)
        self.assertIn("extra", origins)

    def test_inventory_never_leaks_a_credential(self):
        with mock.patch.object(inventory, "_tcp_reachable", return_value=True):
            items = inventory.build_inventory(self.cfg)
        body = str(items)
        self.assertNotIn(SECRET, body)
        self.assertNotIn("admin:pw@", body.replace("admin:***@", ""))
        self.assertTrue(any(item["reachable"] for item in items))

    def test_probe_can_be_skipped_entirely(self):
        with mock.patch.object(inventory, "_tcp_reachable") as connect:
            items = inventory.build_inventory(self.cfg, probe=False)
        connect.assert_not_called()
        self.assertTrue(all(item["reachable"] is None for item in items))

    def test_usb_sources_are_checked_for_presence_not_probed(self):
        cfg = HubConfig(root=self.root, front_camera="usb:0", cabin_camera="synthetic")
        with mock.patch.object(inventory, "_tcp_reachable") as connect:
            items = inventory.build_inventory(cfg)
        connect.assert_not_called()
        by_source = {item["source"]: item for item in items}
        # A bound-but-unplugged USB camera has to be visible as such.
        self.assertIs(by_source["usb:0"]["reachable"],
                      os.path.exists("/dev/video0"))
        self.assertIs(by_source["synthetic"]["reachable"], True)

    def test_file_sources_report_their_path_existence(self):
        clip = os.path.join(self.root, "clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"\0")
        real = inventory.describe(f"video:{clip}", probe=True)
        missing = inventory.describe("video:/nope/missing.mp4", probe=True)
        self.assertIs(real["reachable"], True)
        self.assertIs(missing["reachable"], False)

    def test_in_use_by_comes_from_the_occupancy_snapshot(self):
        held = inventory.held_devices([
            {"slot": "front", "device": URL, "holder": "front"},
            {"slot": "rear", "device": "", "holder": "rear"},
        ])
        self.assertEqual(held, {URL: "front"})
        with mock.patch.object(inventory, "_tcp_reachable", return_value=True):
            items = inventory.build_inventory(self.cfg, held)
        bound = {item["source"].split("@")[-1]: item["in_use_by"] for item in items}
        self.assertEqual(bound["192.168.137.20:554/"], "front")

    def test_resolve_maps_id_back_to_the_raw_source(self):
        source, label = inventory.resolve(self.cfg, inventory.camera_id(URL))
        self.assertEqual(source, URL)
        self.assertEqual(label, "RTSP · 192.168.137.20")

    def test_resolve_rejects_unknown_and_empty_ids(self):
        self.assertIsNone(inventory.resolve(self.cfg, "deadbeef0000"))
        self.assertIsNone(inventory.resolve(self.cfg, ""))

    def test_current_bindings_report_labels_not_urls(self):
        bindings = inventory.current_bindings(self.cfg)
        self.assertEqual(bindings["front"]["camera_id"], inventory.camera_id(URL))
        self.assertEqual(bindings["front"]["camera_label"], "RTSP · 192.168.137.20")
        self.assertNotIn(SECRET, str(bindings))

    def test_unconfigured_role_is_reported_honestly(self):
        cfg = HubConfig(root=self.root)
        bindings = inventory.current_bindings(cfg)["front"]
        self.assertIsNone(bindings["camera_id"])
        self.assertFalse(bindings["configured"])
        # Empty rather than a role name: the UI substitutes its own translated
        # one, so no language can leak in through this field.
        self.assertEqual(bindings["camera_label"], "")


class TestSourceCanonicalization(unittest.TestCase):
    """`usb:2` and `/dev/video2` are one camera, not two.

    The `/dev/videoN` spelling used to be accepted but stored verbatim, and
    `app/camera_source.open_source()` then treated it as a *file* path — OpenCV
    picked GStreamer, no frame ever arrived, and the cabin module exited 3 with
    "no first frame within 8.0s". It also meant the picker showed the same
    camera twice, under two different ids.
    """

    def test_device_path_is_normalized_to_the_usb_form(self):
        self.assertEqual(canonical_source("/dev/video2"), "usb:2")
        self.assertEqual(canonical_source("  /dev/video12  "), "usb:12")
        self.assertEqual(canonical_source("usb:2"), "usb:2")
        self.assertEqual(canonical_source("rtsp://cam:554/"), "rtsp://cam:554/")
        self.assertEqual(canonical_source("video:/clip.mp4"), "video:/clip.mp4")
        self.assertEqual(canonical_source(""), "")

    def test_both_spellings_share_one_id(self):
        self.assertEqual(inventory.camera_id("/dev/video2"),
                         inventory.camera_id("usb:2"))
        self.assertNotEqual(inventory.camera_id("usb:2"),
                            inventory.camera_id("usb:3"))

    def test_both_spellings_render_one_label(self):
        self.assertEqual(inventory.label_for("/dev/video2"),
                         inventory.label_for("usb:2"))
        self.assertEqual(inventory.label_for("/dev/video2"), "USB · /dev/video2")

    def test_probe_echoes_the_canonical_source(self):
        item = inventory.describe("/dev/video2", origin="manual", probe=False)
        self.assertEqual(item["source"], "usb:2")
        self.assertEqual(item["id"], inventory.camera_id("usb:2"))

    def test_validate_accepts_the_device_path_spelling(self):
        self.assertIsNone(inventory.validate("dms", "/dev/video2"))
        self.assertIsNone(inventory.validate("front", "/dev/video2"))

    def test_stale_device_path_binding_resolves_and_does_not_duplicate(self):
        # A binding file written before canonicalization must not surface as a
        # second entry next to the real node.
        root = tempfile.mkdtemp()
        try:
            cfg = HubConfig(root=root, front_camera="/dev/video2",
                            rear_camera="rtsp://r:554/", cabin_camera="usb:0",
                            extra_cameras=["/dev/video2"])
            sources = [item["source"] for item in inventory.candidates(cfg)]
            self.assertEqual(sources.count("usb:2"), 1)
            self.assertNotIn("/dev/video2", sources)
            self.assertEqual(cfg.front_camera, "usb:2")
            self.assertEqual(inventory.resolve(cfg, inventory.camera_id("usb:2"))[0],
                             "usb:2")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestV4l2Index(unittest.TestCase):
    """`app.camera_source.v4l2_index` — the fix for the exit-3 failure.

    `/dev/videoN` used to fall through to the *file* branch of `open_source()`,
    so OpenCV auto-selected GStreamer, no frame ever arrived, and the module
    exited 3 while the camera was perfectly usable.
    """

    def test_both_spellings_map_to_the_index(self):
        from app.camera_source import v4l2_index

        self.assertEqual(v4l2_index("usb:2"), 2)
        self.assertEqual(v4l2_index("/dev/video2"), 2)
        self.assertEqual(v4l2_index("usb:0"), 0)
        self.assertEqual(v4l2_index("  /dev/video12  "), 12)

    def test_non_usb_sources_are_left_alone(self):
        from app.camera_source import v4l2_index

        for source in ("rtsp://cam:554/", "video:/clip.mp4", "synthetic",
                       "image:/a.jpg", "", "usb:", "/dev/video"):
            with self.subTest(source=source):
                self.assertIsNone(v4l2_index(source))


class TestValidate(unittest.TestCase):
    def test_unconfigured_role_says_so(self):
        # `errorText()` in the UI keys its English translation off "未配置".
        self.assertIn("未配置", inventory.validate("front", "") or "")

    def test_supported_source_passes(self):
        self.assertIsNone(inventory.validate("front", URL))
        self.assertIsNone(inventory.validate("dms", "usb:0"))
        self.assertIsNone(inventory.validate("rear", "video:/clip.mp4"))
        self.assertIsNone(inventory.validate("dms", "synthetic"))

    def test_incompatible_source_is_explained_and_redacted(self):
        problem = inventory.validate("front", "video:/clip.mp4")
        self.assertIn("不支持该源类型", problem or "")
        self.assertIn("rear/dms", problem or "")

    def test_every_role_may_use_a_usb_camera_now(self):
        # The cabin role used to be frozen to usb:0; that freeze is gone.
        for role in ("front", "rear", "dms"):
            with self.subTest(role=role):
                self.assertIsNone(inventory.validate(role, "usb:1"))


def cjk_only(text):
    return "".join(ch for ch in text if "\u3400" <= ch <= "\u9fff")


class TestLanguageNeutralPayload(unittest.TestCase):
    """The API ships structured data, never display prose (rule 3).

    A Chinese `detail` string carrying "· 当前绑定" was exactly what made an
    English interface show Chinese option text, so this is pinned to the whole
    payload rather than to the field that happened to be wrong.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        inventory.clear_probe_cache()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _config(self):
        return HubConfig(
            root=self.root,
            front_camera=URL,
            rear_camera="rtsp://admin:pw@192.168.1.10:554/",
            cabin_camera="usb:0",
            extra_cameras=["video:/data/clip.mp4", "rtsp://spare:554/"],
        )

    def test_whole_camera_payload_contains_no_cjk(self):
        cfg = self._config()
        with mock.patch.object(inventory, "_tcp_reachable", return_value=True):
            body = {
                "cameras": inventory.build_inventory(cfg, {"usb:0": "dms"}),
                "modules": inventory.current_bindings(cfg),
            }
        leaked = sorted({ch for ch in json.dumps(body, ensure_ascii=False)
                         if "\u3400" <= ch <= "\u9fff"})
        self.assertEqual(leaked, [], "API payload leaked CJK: %s" % leaked)

    def test_no_field_is_called_detail(self):
        # `detail` was the prose field; it must not come back.
        cfg = self._config()
        for item in inventory.build_inventory(cfg, probe=False):
            self.assertNotIn("detail", item)
        for binding in inventory.current_bindings(cfg).values():
            self.assertNotIn("role_label", binding)

    def test_labels_of_every_kind_are_language_neutral(self):
        cases = (
            (URL, "RTSP · 192.168.137.20"),
            ("rtsp://h:8554/x", "RTSP · h:8554"),
            ("usb:0", "USB · /dev/video0"),
            ("/dev/video3", "USB · /dev/video3"),
            ("video:/data/clip.mp4", "video · clip.mp4"),
            ("image:/data/frame.jpg", "image · frame.jpg"),
            ("synthetic", "synthetic"),
            ("/data/clip.mp4", "clip.mp4"),
            ("", ""),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(inventory.label_for(source), expected)
                self.assertEqual(cjk_only(inventory.label_for(source)), "")


if __name__ == "__main__":
    unittest.main()