"""Visual Hub data models."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class ModuleId(str, Enum):
    FRONT = "front"
    REAR = "rear"
    DMS = "dms"


class ModuleState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    ERROR = "error"
    STOPPING = "stopping"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"
    ERROR = "error"


class CameraSlot(str, Enum):
    FRONT = "front"
    REAR = "rear"
    CABIN = "cabin"


MODULE_SLOTS = {
    ModuleId.FRONT: CameraSlot.FRONT,
    ModuleId.REAR: CameraSlot.REAR,
    ModuleId.DMS: CameraSlot.CABIN,
}


@dataclass
class HubEvent:
    ts: float
    source: str
    severity: str
    kind: str
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)
    id: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OccupancyLease:
    slot: str
    device: str
    holder: str
    since: float
    exclusive: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModuleStatus:
    id: str
    label: str
    state: str
    pid: Optional[int] = None
    port: Optional[int] = None
    camera: Optional[str] = None
    camera_slot: Optional[str] = None
    health_ok: bool = False
    last_error: Optional[str] = None
    started_at: Optional[float] = None
    uptime_s: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    stream_url: Optional[str] = None
    native_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
