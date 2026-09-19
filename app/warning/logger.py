"""CSV telemetry logger (§40).

One row per processed frame:
  ts, frame_id, risk, distance_raw, distance_filtered, velocity, ttc,
  n_candidates, depth_valid, fps, backend, engine_ms
File: logs/session_YYYYmmdd_HHMMSS.csv

One file per run, so runs alone accumulate files forever (6 files / 768 KB in the
first two days after the 2026-09-18 cleanup). The newest `keep_files` sessions
(and anything younger than `keep_days`) are kept; older ones are pruned at start.
"""
from __future__ import annotations

import csv
import os
import time
from typing import Optional

from .retention import DEFAULT_KEEP_DAYS, prune_old_files

DEFAULT_KEEP_SESSIONS = 40


class TelemetryLogger:
    FIELDS = ["ts", "frame_id", "risk", "distance_raw", "distance_filtered",
              "velocity", "ttc", "n_candidates", "depth_valid", "fps",
              "backend", "engine_ms"]

    def __init__(self, base_dir: str = "logs", enabled: bool = True,
                 keep_files: int = DEFAULT_KEEP_SESSIONS,
                 keep_days: float = DEFAULT_KEEP_DAYS) -> None:
        self.enabled = enabled
        self.fh = None
        self.writer = None
        self.path: Optional[str] = None
        self.keep_files = int(keep_files)
        self.keep_days = float(keep_days)
        if not self.enabled:
            return
        os.makedirs(base_dir, exist_ok=True)
        self.path = os.path.join(
            base_dir, "session_" + time.strftime("%Y%m%d_%H%M%S") + ".csv")
        self.fh = open(self.path, "w", newline="")
        self.writer = csv.DictWriter(self.fh, fieldnames=self.FIELDS)
        self.writer.writeheader()
        self.prune()

    def prune(self, now=None) -> int:
        """Keep the newest `keep_files` session CSVs (and anything < keep_days)."""
        if not self.enabled:
            return 0
        return prune_old_files(os.path.dirname(self.path) or ".", "session_", ".csv",
                               self.keep_files, self.keep_days, now)

    def log(self, frame_id: int, risk: str, d_raw, d_filt, velocity,
            ttc, n_candidates: int, depth_valid: bool, fps: float,
            backend: str = "", engine_ms: float = 0.0) -> None:
        if not self.enabled or self.writer is None:
            return
        fmt = lambda v: ("%.3f" % v) if isinstance(v, float) and v == v \
            else ""  # noqa: E731
        self.writer.writerow({
            "ts": "%.3f" % time.time(),
            "frame_id": frame_id,
            "risk": risk,
            "distance_raw": fmt(d_raw),
            "distance_filtered": fmt(d_filt),
            "velocity": fmt(velocity),
            "ttc": fmt(ttc),
            "n_candidates": n_candidates,
            "depth_valid": 1 if depth_valid else 0,
            "fps": "%.1f" % fps,
            "backend": backend,
            "engine_ms": "%.2f" % engine_ms,
        })
        if frame_id % 30 == 0:
            self.fh.flush()

    def close(self) -> None:
        if self.fh:
            self.fh.flush()
            self.fh.close()
            self.fh = None