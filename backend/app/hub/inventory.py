"""Camera inventory for the Visual Hub picker.

Four rules shape this module, and breaking any of them causes a real bug:

1. **Never open a device.** `backend/app/camera/discovery.enumerate_devices()`
   opens every `/dev/videoN` to read its caps. For a camera a module already
   holds that fails at best and steals the handle at worst, so the inventory is
   built from sysfs only, plus at most a TCP connect for RTSP reachability.
2. **Never hand out a credential.** Sources are addressed by an opaque id and
   every URL leaving this module goes through `redact_source()`. The hub's
   status payload must stay free of `rtsp://` (asserted by
   `tests/hub/test_runtime.py`), which is why only ids and display labels cross
   the API boundary.
3. **Never ship display prose.** These fields are rendered verbatim by the UI,
   so they must contain no wording in *any* language — a Chinese `detail` string
   here is what once made an English interface show Chinese. `label_for()` is
   therefore built from source-scheme tokens (`RTSP`, `USB`, `video`, …) that
   read the same in both languages, and every human-readable word the operator
   sees is composed by the frontend from its own translations. There is a test
   that the whole `/api/hub/cameras` payload is free of CJK characters.
4. **Never block the hub.** The RTSP reachability probe is a bounded TCP
   connect whose result is cached, so a polling UI cannot turn into a scanner.

Reachability is a *port* check, not an RTSP session: opening a real GStreamer
pipeline here would allocate the NVIDIA decoder and contend with the module that
legitimately owns the camera.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from .config import ROLE_LABELS, HubConfig, canonical_source

MODULE_ORDER: Tuple[str, ...] = ("front", "rear", "dms")

KIND_RTSP = "rtsp"
KIND_USB = "usb"
KIND_FILE = "file"
KIND_TEST = "test"

# How long a reachability result is trusted. The UI polls status every 1.2 s and
# may refresh the picker at the same rate; without this each refresh would open
# two TCP connections to cameras that are already known to be up.
PROBE_TTL_S = 10.0
PROBE_TIMEOUT_S = 0.5

_V4L2_SYSFS = Path("/sys/class/video4linux")
_USB_SYSFS = Path("/sys/bus/usb/devices")
_VIDEO_RE = re.compile(r"^video(\d+)$")
_RTSP_SCHEMES = ("rtsp://", "rtsps://", "rtsp:")

_probe_lock = threading.Lock()
_probe_cache: Dict[str, Tuple[float, bool]] = {}


# ---------------------------------------------------------------------------
# source taxonomy
# ---------------------------------------------------------------------------

def kind_of(source: str) -> str:
    """Classify a source string the way `app/camera_source.open_source` would."""
    value = (source or "").strip()
    low = value.lower()
    if low.startswith(_RTSP_SCHEMES):
        return KIND_RTSP
    if low.startswith("usb:") or low.startswith("/dev/video"):
        return KIND_USB
    if low.startswith("synthetic"):
        return KIND_TEST
    return KIND_FILE


def allowed_modules(source: str) -> List[str]:
    """Which roles can actually open this source.

    `front` runs in-process through `CameraManager.start()`, which understands
    only `rtsp://` and `/dev/videoN` (`usb:N` is rewritten to the device path).
    It therefore cannot take `video:` / `image:` / `synthetic`. That is a
    limitation of the manager, not a policy about the role.
    """
    value = (source or "").strip()
    low = value.lower()
    if not value:
        return []
    kind = kind_of(value)
    if kind in (KIND_RTSP, KIND_USB):
        return list(MODULE_ORDER)
    if kind == KIND_TEST:
        return ["dms"]
    # File-backed sources: rear and dms both reach them through
    # `app.camera_source.open_source`; front never does.
    if low.startswith("image:"):
        return ["dms"]
    if low.startswith(("video:", "http://", "https://")):
        return ["rear", "dms"]
    # A bare path is opened as a video file by `open_source`.
    return ["rear", "dms"]


def redact_source(source: str) -> str:
    """Strip any password from an RTSP URL; leave other kinds untouched."""
    value = (source or "").strip()
    low = value.lower()
    if not low.startswith(("rtsp://", "rtsps://")):
        return value
    if "://" not in value:
        return value
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        # An unparseable authority is not safe to echo back verbatim.
        return "rtsp://***"
    if not host:
        return value
    authority = f"{host}:{port}" if port else host
    if parts.username:
        authority = f"{parts.username}:***@{authority}"
    return urlunsplit((parts.scheme, authority, parts.path, parts.query,
                       parts.fragment))


def camera_id(source: str) -> str:
    """Stable, non-reversible identifier used by the API instead of the URL.

    Computed on the *canonical* source, so `usb:2` and `/dev/video2` — the same
    camera written two ways — are one id rather than two picker entries.
    """
    value = canonical_source(source)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _host_port(source: str) -> Tuple[str, int]:
    value = (source or "").strip()
    # A non-RTSP source has no host: `usb:0` must not be mistaken for the
    # authority "0" of an empty URL.
    if not value.lower().startswith(_RTSP_SCHEMES):
        return "", 0
    if "://" not in value:
        # `app/camera_source` accepts the short `rtsp:host:port` form.
        value = "//" + value.split(":", 1)[1]
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "", 0
    return host, int(port or 554)


def label_for(source: str, name: str = "", usb_port: str = "") -> str:
    """Technical label for a source. Deliberately language-neutral.

    This string reaches the browser verbatim, so it carries no prose: `RTSP`,
    `USB`, `video`, `image` and `synthetic` are source-scheme tokens, and the
    host / device path / file name are data. See rule 3 in the module docstring.

    `usb_port` is the USB port path (e.g. `1-2.1`). It is the only thing that
    tells two identical cameras apart, which matters because cheap UVC models
    report the same product *and* the same serial.
    """
    value = canonical_source(source)
    if not value:
        return ""
    kind = kind_of(value)
    if kind == KIND_RTSP:
        host, port = _host_port(value)
        if not host:
            return "RTSP"
        return f"RTSP · {host}:{port}" if port != 554 else f"RTSP · {host}"
    if kind == KIND_USB:
        index = value.split(":", 1)[1].strip() if value.startswith("usb:") else value
        base = f"USB · /dev/video{index}"
        detail = " @".join(part for part in (name, usb_port) if part)
        return f"{base} ({detail})" if detail else base
    if kind == KIND_TEST:
        return "synthetic"
    for prefix in ("video:", "image:"):
        if value.startswith(prefix):
            return f"{prefix[:-1]} · {os.path.basename(value.split(':', 1)[1])}"
    return os.path.basename(value)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def list_usb_devices() -> List[Dict[str, Any]]:
    """Enumerate real V4L2 capture nodes from sysfs, without opening anything.

    One UVC camera registers **several** nodes on a single USB interface: the
    streaming function first (sysfs `index` 0) and then a metadata function
    (`index` 1). Measured on this hardware, the metadata node cannot be opened at
    all (`VideoCapture(...).isOpened()` is False), so offering it was a trap that
    ended in a module error. Only the lowest-index node per USB interface is
    therefore reported; `collapsed` records how many were folded away so the API
    can be honest about it.

    `usb_port` (the USB topology path, e.g. `1-2.1`) is the only stable way to
    tell two identical cameras apart: this pair of "1080P USB Camera" units
    reports the same product, vendor, and even the same serial string.
    """
    if not _V4L2_SYSFS.is_dir():
        return []
    try:
        entries = sorted(_V4L2_SYSFS.iterdir(), key=lambda item: item.name)
    except OSError:
        return []

    nodes: List[Dict[str, Any]] = []
    for entry in entries:
        match = _VIDEO_RE.match(entry.name)
        if not match:
            continue
        index = int(match.group(1))
        if not os.path.exists(f"/dev/video{index}"):
            continue
        interface = os.path.realpath(entry / "device")
        port = os.path.basename(os.path.dirname(interface)) if interface else ""
        function = _read_text(entry / "index") or "0"
        usb_product = _read_text(_USB_SYSFS / port / "product") \
            if port else ""
        nodes.append({
            "source": f"usb:{index}",
            "device": f"/dev/video{index}",
            "video_index": index,
            "function_index": int(function) if function.isdigit() else 0,
            "interface": interface,
            # Prefer the clean USB product string; the V4L2 `name` repeats itself
            # ("1080P USB Camera: 1080P USB Cam").
            "name": usb_product or _read_text(entry / "name"),
            "usb_port": port,
        })

    # Keep only the streaming function of each physical interface.
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes:
        groups.setdefault(node["interface"] or node["source"], []).append(node)
    kept: List[Dict[str, Any]] = []
    for group in groups.values():
        group.sort(key=lambda node: node["function_index"])
        primary = dict(group[0])
        primary["collapsed"] = len(group) - 1
        kept.append(primary)
    kept.sort(key=lambda node: node["video_index"])
    return kept


def usb_entry(source: str) -> Dict[str, Any]:
    """Sysfs facts about one `usb:<idx>` source (never opens the device)."""
    value = canonical_source(source)
    if not value.startswith("usb:"):
        return {}
    for entry in list_usb_devices():
        if entry["source"] == value:
            return entry
    return {}


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_tcp(source: str, timeout: float = PROBE_TIMEOUT_S,
              ttl: float = PROBE_TTL_S) -> Optional[bool]:
    """Is an RTSP port accepting connections? `None` when there is no host."""
    host, port = _host_port(source)
    if not host:
        return None
    key = f"{host}:{port}"
    now = time.time()
    with _probe_lock:
        cached = _probe_cache.get(key)
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
    reachable = _tcp_reachable(host, port, timeout)
    with _probe_lock:
        _probe_cache[key] = (now, reachable)
    return reachable


def clear_probe_cache() -> None:
    with _probe_lock:
        _probe_cache.clear()


def _presence(source: str) -> Optional[bool]:
    """Does a non-RTSP source exist? Never opens a device.

    A bound-but-unplugged USB camera must be visible as such in the picker
    rather than silently listed, so `usb:N` checks its `/dev/videoN` node and a
    file/`video:` source checks its path.
    """
    value = (source or "").strip()
    kind = kind_of(value)
    if kind == KIND_TEST:
        return True
    if kind == KIND_USB:
        if value.startswith("usb:"):
            return os.path.exists(f"/dev/video{value.split(':', 1)[1].strip()}")
        return os.path.exists(value)
    if kind == KIND_FILE:
        path = value.split(":", 1)[1] if value.startswith(("video:", "image:")) else value
        return os.path.exists(path)
    return None


# ---------------------------------------------------------------------------
# candidates / public inventory
# ---------------------------------------------------------------------------

def candidates(cfg: HubConfig) -> List[Dict[str, Any]]:
    """Raw candidate sources with provenance. Stays inside the hub process."""
    out: List[Dict[str, Any]] = []
    seen = set()

    def add(source: str, origin: str, label: str = "") -> None:
        value = canonical_source(source)
        if not value or value in seen:
            return
        seen.add(value)
        out.append({
            "source": value,
            "origin": origin,
            "label": label or label_for(value),
        })

    for entry in list_usb_devices():
        add(entry["source"], "usb",
            label_for(entry["source"], entry["name"], entry["usb_port"]))
    for module_id in MODULE_ORDER:
        add(cfg.role_camera(module_id), "config")
    for url in cfg.extra_cameras:
        add(url, "extra")
    return out


def describe(source: str, *, origin: str = "", label: str = "",
             probe: bool = True) -> Dict[str, Any]:
    """Public (redacted) view of one candidate.

    Structured data only — no prose in any language (rule 3). The UI composes
    every human-readable word from `kind`, `origin`, `reachable` and
    `in_use_by` plus its own translations.
    """
    value = canonical_source(source)
    kind = kind_of(value)
    reachable: Optional[bool] = None
    if probe:
        if kind == KIND_RTSP:
            reachable = probe_tcp(value)
        else:
            reachable = _presence(value)
    entry = usb_entry(value) if kind == KIND_USB else {}
    return {
        "id": camera_id(value),
        "kind": kind,
        "source": redact_source(value),
        "label": label or label_for(value, entry.get("name", ""),
                                    entry.get("usb_port", "")),
        "origin": origin,
        "reachable": reachable,
        "allowed_modules": allowed_modules(value),
        "usb_port": entry.get("usb_port") or None,
    }


def build_inventory(cfg: HubConfig, held_devices: Optional[Dict[str, str]] = None,
                    *, probe: bool = True) -> List[Dict[str, Any]]:
    """Detected cameras, with the module currently holding each one.

    `held_devices` maps a raw source to the module id leased on it, taken from
    `OccupancyManager.snapshot()`.
    """
    held = held_devices or {}
    items: List[Dict[str, Any]] = []
    for raw in candidates(cfg):
        item = describe(raw["source"], origin=raw["origin"],
                        label=raw["label"], probe=probe)
        item["in_use_by"] = held.get(raw["source"])
        items.append(item)
    return items


def current_bindings(cfg: HubConfig) -> Dict[str, Dict[str, Any]]:
    """What each role is bound to right now (ids and labels, never URLs)."""
    out: Dict[str, Dict[str, Any]] = {}
    for module_id in MODULE_ORDER:
        source = cfg.role_camera(module_id)
        out[module_id] = {
            "module": module_id,
            # Empty when unconfigured, so the UI falls back to its own
            # translated role name instead of receiving one from here.
            "camera_label": label_for(source) if source else "",
            "camera_id": camera_id(source) if source else None,
            "configured": bool(source),
            "overridden": cfg.is_overridden(module_id),
            "allowed_modules": allowed_modules(source) if source
            else list(MODULE_ORDER),
        }
    return out


def held_devices(leases: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """Invert an occupancy snapshot into `{device: holder}`."""
    out: Dict[str, str] = {}
    for lease in leases:
        device = str(lease.get("device") or "")
        holder = str(lease.get("holder") or "")
        if device and holder:
            out[device] = holder
    return out


def resolve(cfg: HubConfig, id_value: str) -> Optional[Tuple[str, str]]:
    """Map an opaque id back to `(raw_source, label)`."""
    wanted = (id_value or "").strip()
    if not wanted:
        return None
    for item in candidates(cfg):
        if camera_id(item["source"]) == wanted:
            return item["source"], item["label"]
    return None


def validate(module_id: str, source: str) -> Optional[str]:
    """Return a human-readable error when `module_id` cannot use `source`."""
    value = canonical_source(source)
    role = ROLE_LABELS.get(module_id, module_id)
    if not value:
        return f"{role} 未配置摄像头"
    allowed = allowed_modules(value)
    shown = redact_source(value)
    if not allowed:
        return f"不支持的摄像头源: {shown}"
    if module_id not in allowed:
        return (f"{role} 不支持该源类型: {shown}"
                f"（可用模块: {'/'.join(allowed)}）")
    return None