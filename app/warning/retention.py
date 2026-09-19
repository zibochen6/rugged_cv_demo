"""Retention for warning-side runtime output.

Debug output must not grow without bound:

* the 2026-09-18 cleanup deleted 5291 accumulated `logs/danger_*.jpg` (1.2 GB);
* every run of the rear warning writes one more `logs/session_*.csv`.

Both writers share this helper so there is a single retention rule to reason about.
It is best-effort by contract: a broken directory must never break inference.
"""
from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple

DEFAULT_KEEP_FILES = 200
DEFAULT_KEEP_DAYS = 14.0


def prune_old_files(base_dir: str, prefix: str, suffix: str, keep_files: int,
                    keep_days: float = DEFAULT_KEEP_DAYS,
                    now: Optional[float] = None) -> int:
    """Drop `<prefix>*<suffix>` files beyond the newest `keep_files` or older than
    `keep_days`; return how many files were removed.

    Only files matching the prefix/suffix pair are touched.
    """
    now = time.time() if now is None else now
    removed = 0
    try:
        entries: List[Tuple[float, str]] = []
        for name in os.listdir(base_dir):
            if not (name.startswith(prefix) and name.endswith(suffix)):
                continue
            path = os.path.join(base_dir, name)
            try:
                entries.append((os.path.getmtime(path), path))
            except OSError:
                continue
        entries.sort(reverse=True)
        cutoff = now - float(keep_days) * 86400.0
        for index, (mtime, path) in enumerate(entries):
            if index < int(keep_files) and mtime >= cutoff:
                continue
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed