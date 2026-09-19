"""Visual Hub runtime configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


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
    cabin_camera: str = "usb:0"
    front_port: int = 8000
    rear_port: int = 8080
    dms_port: int = 8010
    event_log: str = "logs/hub_events.jsonl"

    def resolved_python(self) -> str:
        if self.python:
            return self.python
        candidate = os.path.join(self.root, ".venv", "bin", "python")
        return candidate if os.path.exists(candidate) else "python3"

    def module_specs(self) -> Dict[str, ModuleSpec]:
        return {
            "front": ModuleSpec(
                id="front",
                label="前视分割",
                port=self.front_port,
                camera_slot="front",
                camera=self.front_camera,
                camera_label="PoE 前摄",
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
                camera_label="PoE 后摄",
            ),
            "dms": ModuleSpec(
                id="dms",
                label="座舱监测",
                port=self.dms_port,
                camera_slot="cabin",
                camera=self.cabin_camera,
                camera_label="USB 座舱",
            ),
        }


def load_hub_config(root: Optional[str] = None) -> HubConfig:
    cfg = HubConfig(root=root or _project_root())
    env = {}
    external_env = os.environ.get("HUB_ENV_FILE", "/etc/seg-demo/visual-hub.env")
    env.update(_load_env_file(external_env))
    env.update(os.environ)
    cfg.front_camera = env.get("FRONT_CAMERA_URL") or cfg.front_camera
    cfg.rear_camera = env.get("REAR_CAMERA_URL") or env.get("RTSP_URL") or cfg.rear_camera
    # Cabin is a fixed physical role. Never inherit a discovered or
    # synthetic source from another demo.
    cfg.cabin_camera = "usb:0"
    cfg.port = int(env.get("HUB_PORT") or cfg.port)
    cfg.front_port = int(env.get("FRONT_PORT") or env.get("PORT") or cfg.front_port)
    cfg.rear_port = int(env.get("WEB_PORT") or cfg.rear_port)
    cfg.dms_port = int(env.get("DMS_PORT") or cfg.dms_port)
    if env.get("HUB_PYTHON"):
        cfg.python = env["HUB_PYTHON"]
    return cfg
