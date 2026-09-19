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
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping

_lock = threading.Lock()
_RUNTIME_KEYS = ("recording", "save_dir", "danger_m", "warning_m", "buzzer")


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
