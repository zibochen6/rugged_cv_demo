"""Visual Hub runtime configuration.

Camera roles are *defaults* here, not constants. The protected environment file
(`/etc/seg-demo/visual-hub.env`) supplies the factory values, and an operator
chosen binding persisted in `configs/_camera_bindings.yaml` (written by the web
UI) overrides them at load time. Every role may therefore point at any detected
camera without editing a root-owned file.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from configs._runtime_store import load_cameras


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


DEFAULT_BINDINGS_FILE = os.path.join("configs", "_camera_bindings.yaml")
# Built-in fallback, used only when neither the environment nor a binding file
# says anything. front/rear deliberately have no guess — they need a real
# address, and a wrong one is worse than an obvious "not configured".
DEFAULT_CABIN_CAMERA = "usb:0"
# Physical role names. These are the *floor* labels shown when a role has no
# usable camera yet; once a camera is bound the UI shows that camera's own label.
ROLE_LABELS = {"front": "PoE 前摄", "rear": "PoE 后摄", "dms": "USB 座舱"}
ROLE_SLOTS = {"front": "front", "rear": "rear", "dms": "cabin"}

_DEV_VIDEO_RE = re.compile(r"^/dev/video(\d+)$")


def canonical_source(source: str) -> str:
    """Normalize a camera source to the spelling the openers expect.

    `usb:2` and `/dev/video2` are the same device, but only `usb:` makes
    `app/camera_source.open_source()` choose the V4L2 backend — the `/dev/videoN`
    spelling used to fall through to the file branch, where OpenCV auto-selected
    GStreamer and the module failed with "no first frame" and exit code 3.

    Normalizing on the way in (environment, binding file, web UI) means a source
    typed as a device path is stored, addressed and compared as `usb:<idx>`, so
    the two spellings can no longer appear as two different cameras.
    """
    value = (source or "").strip()
    match = _DEV_VIDEO_RE.match(value)
    return f"usb:{match.group(1)}" if match else value


def _load_env_file(path: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except PermissionError:
        # A root-owned 0600 systemd EnvironmentFile is intentionally not
        # readable by the service user; systemd already injected its values.
        return {}
    return values


@dataclass
class ModuleSpec:
    id: str
    label: str
    port: int
    camera_slot: str
    camera: str
    camera_label: str
    health_path: str = "/health"
    state_path: str = "/state"
    stream_path: str = "/stream"


@dataclass
class HubConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    root: str = field(default_factory=_project_root)
    python: str = ""
    front_camera: str = ""
    rear_camera: str = ""
    cabin_camera: str = ""
    front_port: int = 8000
    rear_port: int = 8080
    dms_port: int = 8010
    event_log: str = "logs/hub_events.jsonl"
    # Operator-selected camera bindings (see load_hub_config / _apply_bindings).
    bindings_file: str = ""
    # Extra candidate RTSP URLs offered by the camera picker, so a spare PoE
    # camera can be chosen without first promoting it to FRONT/REAR_CAMERA_URL.
    extra_cameras: List[str] = field(default_factory=list)
    #: Roles whose camera came from the binding file instead of the environment.
    #: The UI uses this to warn that a calibration made for the factory camera
    #: no longer applies.
    bound_roles: Dict[str, bool] = field(default_factory=dict)
    #: The environment-supplied factory cameras, kept so a later runtime rebind
    #: can still tell "operator choice" from "shipped default".
    env_cameras: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Every construction path (environment, binding file, a caller, a test)
        # gets canonical sources, so `usb:2` and `/dev/video2` can never coexist
        # as two different cameras.
        self.front_camera = canonical_source(self.front_camera)
        self.rear_camera = canonical_source(self.rear_camera)
        self.cabin_camera = canonical_source(self.cabin_camera)
        self.extra_cameras = [canonical_source(url) for url in self.extra_cameras]

    def resolved_python(self) -> str:
        if self.python:
            return self.python
        candidate = os.path.join(self.root, ".venv", "bin", "python")
        return candidate if os.path.exists(candidate) else "python3"

    def bindings_path(self) -> str:
        """Absolute path of the operator binding file (never empty)."""
        if self.bindings_file:
            return self.bindings_file
        return os.path.join(self.root, DEFAULT_BINDINGS_FILE)

    def role_camera(self, module_id: str) -> str:
        return {
            "front": self.front_camera,
            "rear": self.rear_camera,
            "dms": self.cabin_camera,
        }[module_id]

    def set_role_camera(self, module_id: str, source: str) -> None:
        """Rebind one role in memory; persisting it is the caller's job.

        Also refreshes `bound_roles`, so the UI stops warning about a stale
        calibration as soon as the factory camera is selected again.
        """
        source = canonical_source(source)
        if module_id == "front":
            self.front_camera = source
        elif module_id == "rear":
            self.rear_camera = source
        elif module_id == "dms":
            self.cabin_camera = source
        else:
            raise KeyError(module_id)
        self.bound_roles[module_id] = source != self.env_cameras.get(module_id, "")

    def is_overridden(self, module_id: str) -> bool:
        return bool(self.bound_roles.get(module_id))

    def module_specs(self) -> Dict[str, ModuleSpec]:
        return {
            "front": ModuleSpec(
                id="front",
                label="前视分割",
                port=self.front_port,
                camera_slot="front",
                camera=self.front_camera,
                camera_label=ROLE_LABELS["front"],
                health_path="/api/health",
                state_path="/api/segment/status",
                stream_path="/api/camera/stream.mjpg",
            ),
            "rear": ModuleSpec(
                id="rear",
                label="后视预警",
                port=self.rear_port,
                camera_slot="rear",
                camera=self.rear_camera,
                camera_label=ROLE_LABELS["rear"],
            ),
            "dms": ModuleSpec(
                id="dms",
                label="座舱监测",
                port=self.dms_port,
                camera_slot="cabin",
                camera=self.cabin_camera,
                camera_label=ROLE_LABELS["dms"],
            ),
        }


def _apply_bindings(cfg: HubConfig) -> HubConfig:
    """Overlay operator-selected cameras from the binding file, if any.

    Environment values stay the factory defaults: a role is only replaced when
    the binding file actually carries a non-empty value for it, so deleting
    that file restores the previous behaviour exactly.
    """
    values = load_cameras(cfg.bindings_path())
    # Snapshot the factory defaults *before* any override, so bound_roles can
    # still distinguish them afterwards (including after a runtime rebind).
    cfg.env_cameras = {module_id: cfg.role_camera(module_id)
                       for module_id in ("front", "rear", "dms")}
    for module_id in cfg.env_cameras:
        source = values.get(module_id)
        if source:
            cfg.set_role_camera(module_id, source)
    return cfg


def load_hub_config(root: Optional[str] = None) -> HubConfig:
    cfg = HubConfig(root=root or _project_root())
    env = {}
    external_env = os.environ.get("HUB_ENV_FILE", "/etc/seg-demo/visual-hub.env")
    env.update(_load_env_file(external_env))
    env.update(os.environ)
    cfg.front_camera = canonical_source(env.get("FRONT_CAMERA_URL") or cfg.front_camera)
    cfg.rear_camera = canonical_source(
        env.get("REAR_CAMERA_URL") or env.get("RTSP_URL") or cfg.rear_camera)
    # The cabin camera defaults to the USB device but is no longer frozen: any
    # role can be rebound from the web UI (see _apply_bindings below).
    cfg.cabin_camera = canonical_source(
        env.get("DMS_CAMERA") or DEFAULT_CABIN_CAMERA)
    cfg.bindings_file = env.get("HUB_CAMERA_BINDINGS") or os.path.join(
        cfg.root, DEFAULT_BINDINGS_FILE)
    cfg.extra_cameras = [
        canonical_source(url) for url in
        (env.get("HUB_EXTRA_CAMERAS") or "").split(",") if url.strip()
    ]
    cfg.port = int(env.get("HUB_PORT") or cfg.port)
    cfg.front_port = int(env.get("FRONT_PORT") or env.get("PORT") or cfg.front_port)
    cfg.rear_port = int(env.get("WEB_PORT") or cfg.rear_port)
    cfg.dms_port = int(env.get("DMS_PORT") or cfg.dms_port)
    if env.get("HUB_PYTHON"):
        cfg.python = env["HUB_PYTHON"]
    return _apply_bindings(cfg)
