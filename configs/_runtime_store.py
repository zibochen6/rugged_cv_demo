"""Persistence of web-controlled runtime knobs.

Stores runtime overrides in a dedicated YAML file so the canonical config
(`configs/warning.yaml`) is never rewritten (preserves its comments and
key order — PyYAML cannot preserve them). The runtime file lives next to
the canonical configs and is loaded once at startup and rewritten whenever
a web toggle changes.

Schema:

  runtime:
    recording: true|false      # user-controlled recording of events
    save_dir: events            # where events/danger_*.jpg land (if enabled)
    danger_m: 1.5               # last slider value (synced with /api/config)
    buzzer: true|false          # last buzzer toggle

Only the keys present in the file are returned by `load()`. `save()` is
a merge-on-write that preserves any keys not mentioned.

Camera bindings live in their own file (`configs/_camera_bindings.yaml`) with
their own top-level section, so the Visual Hub never read-modify-writes the
same file as a child module:

  cameras:
    front: rtsp://user:pass@10.0.0.5:554/
    rear: usb:0
    dms: usb:1

`load_cameras()` / `save_cameras()` follow the same discipline as `load()` /
`save()`: unknown keys are ignored and the write is atomic.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping

_lock = threading.Lock()
_RUNTIME_KEYS = ("recording", "save_dir", "danger_m", "warning_m", "buzzer")
# Physical roles, not module ids invented by a caller. A binding file written by
# hand must never be able to add a role the hub does not supervise.
_BINDING_KEYS = ("front", "rear", "dms")


def load(path: str | Path) -> dict[str, Any]:
    """Return persisted runtime knobs as a dict (empty if absent)."""
    p = Path(path)
    if not p.exists():
        return {}
    import yaml  # PyYAML (project-wide dep)
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return dict(data.get("runtime") or {})


def save(path: str | Path, values: Mapping[str, Any]) -> None:
    """Merge `values` into yaml top-level `runtime:` section (atomic write).

    Writes to `path` via temp-file + os.replace so a crash mid-write cannot
    leave a partial file. Thread-safe via module-level lock.
    """
    import os
    import yaml
    p = Path(path)
    data: dict = {}
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    rt = dict(data.get("runtime") or {})
    for k, v in values.items():
        if k in _RUNTIME_KEYS:
            rt[k] = v
    data["runtime"] = rt
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _lock:
        with tmp.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, p)


def load_cameras(path: str | Path) -> dict[str, Any]:
    """Return persisted per-role camera bindings (empty if absent/unreadable).

    A hand-edited or truncated file must never stop the hub from booting: it
    falls back to the environment-supplied defaults instead. Only the known
    roles are returned, and empty values are dropped so that "key present but
    blank" keeps meaning "no operator override".
    """
    p = Path(path)
    if not p.exists():
        return {}
    import yaml
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - malformed yaml must not break startup
        return {}
    if not isinstance(data, dict):
        return {}
    cameras = data.get("cameras")
    if not isinstance(cameras, Mapping):
        return {}
    return {k: str(cameras[k]) for k in _BINDING_KEYS
            if cameras.get(k) not in (None, "")}


def save_cameras(path: str | Path, values: Mapping[str, Any]) -> None:
    """Merge `values` into the yaml top-level `cameras:` section (atomic write).

    Writes via temp-file + os.replace so a crash mid-write cannot leave a
    partial file, and preserves any sections/keys not mentioned.

    Unlike the runtime knobs, a binding file holds RTSP credentials, so it is
    created 0600 — the same posture as `/etc/seg-demo/visual-hub.env`.
    """
    import os
    import yaml
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001 - overwrite the malformed file
            data = {}
    cameras = data.get("cameras")
    cameras = dict(cameras) if isinstance(cameras, Mapping) else {}
    for k, v in values.items():
        # Blank values are skipped rather than written: `load_cameras` treats an
        # empty value as "no override" anyway, and a file containing only real
        # bindings is easier for an operator to read and edit by hand.
        if k in _BINDING_KEYS and v not in (None, ""):
            cameras[k] = v
    data["cameras"] = cameras
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _lock:
        # Create with 0600 up front so there is no window in which the
        # credential is world-readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
