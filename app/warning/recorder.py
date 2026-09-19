"""Event recording (§41): DANGER screenshots.

On DANGER entry and while DANGER persists (rate limited by min_interval_s),
save the rendered canvas as logs/danger_YYYYmmdd_HHMMSS_fff.jpg.

Snapshots are debug evidence, not data: without retention a busy day writes tens
of thousands of JPEGs (1.2 GB accumulated before 2026-09-18). `prune()` keeps at
most `keep_files` newest snapshots and drops anything older than `keep_days`;
it is best-effort and never raises into the inference loop.
"""
from __future__ import annotations

import os
import time

import cv2

from .retention import DEFAULT_KEEP_DAYS, DEFAULT_KEEP_FILES, prune_old_files


class DangerRecorder:
    def __init__(self, base_dir: str = "logs", enabled: bool = True,
                 min_interval_s: float = 2.0, keep_files: int = DEFAULT_KEEP_FILES,
                 keep_days: float = DEFAULT_KEEP_DAYS,
                 prune_interval_s: float = 3600.0) -> None:
        self.enabled = enabled
        self.base_dir = base_dir
        self.min_interval = float(min_interval_s)
        self.keep_files = int(keep_files)
        self.keep_days = float(keep_days)
        self.prune_interval = float(prune_interval_s)
        self._last = 0.0
        self._last_prune = 0.0
        if self.enabled:
            os.makedirs(base_dir, exist_ok=True)
            self.prune(force=True)

    def on_frame(self, canvas, level: str) -> None:
        if not self.enabled or level != "DANGER":
            return
        now = time.time()
        if now - self._last < self.min_interval:
            return
        self._last = now
        name = time.strftime("danger_%Y%m%d_%H%M%S") + \
            f"_{int(now * 1000) % 1000:03d}.jpg"
        path = os.path.join(self.base_dir, name)
        cv2.imwrite(path, canvas)
        self.prune()

    def prune(self, force: bool = False, now=None) -> int:
        """Drop old danger_*.jpg snapshots; return how many were removed.

        Rate limited by `prune_interval` unless `force=True`.
        """
        if not self.enabled:
            return 0
        now = time.time() if now is None else now
        if not force and now - self._last_prune < self.prune_interval:
            return 0
        self._last_prune = now
        return prune_old_files(self.base_dir, "danger_", ".jpg",
                               self.keep_files, self.keep_days, now)