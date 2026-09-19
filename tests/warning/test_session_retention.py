"""Retention for warning runtime output: session telemetry CSVs.

Regression: each run of the rear warning writes one session_*.csv and nothing
ever removed them; the same class of bug left 5291 danger_*.jpg (1.2 GB) behind
before the 2026-09-18 cleanup.
"""
import os
import time

from app.warning.logger import TelemetryLogger


def _touch(directory, name, mtime, size=32):
    path = os.path.join(str(directory), name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def _names(directory):
    return sorted(p.name for p in directory.iterdir())


def test_old_sessions_beyond_keep_files_are_pruned(tmp_path):
    now = time.time()
    for i in range(10):
        _touch(tmp_path, f"session_2026090{i}_0000{i:02d}.csv", now - (10 - i) * 600)

    logger = TelemetryLogger(base_dir=str(tmp_path), enabled=True, keep_files=3)
    logger.close()

    remaining = _names(tmp_path)
    assert len(remaining) == 3, remaining
    # the freshly created session is kept, plus the two newest older ones
    created = os.path.basename(logger.path)
    assert created in remaining


def test_expired_sessions_are_pruned_even_below_keep_count(tmp_path):
    now = time.time()
    _touch(tmp_path, "session_20260101_000000.csv", now - 30 * 86400)
    _touch(tmp_path, "session_20260918_000000.csv", now - 3600)

    logger = TelemetryLogger(base_dir=str(tmp_path), enabled=True, keep_files=100,
                             keep_days=14.0)
    logger.close()

    remaining = _names(tmp_path)
    assert "session_20260101_000000.csv" not in remaining
    assert "session_20260918_000000.csv" in remaining


def test_unrelated_files_are_never_touched(tmp_path):
    now = time.time()
    _touch(tmp_path, "danger_20260101_000000_000.jpg", now - 60 * 86400)
    _touch(tmp_path, "hub_rear.log", now - 60 * 86400)

    logger = TelemetryLogger(base_dir=str(tmp_path), enabled=True, keep_files=1)
    logger.close()

    remaining = _names(tmp_path)
    assert "danger_20260101_000000_000.jpg" in remaining
    assert "hub_rear.log" in remaining


def test_disabled_logger_writes_and_prunes_nothing(tmp_path):
    now = time.time()
    _touch(tmp_path, "session_20260101_000000.csv", now - 60 * 86400)

    logger = TelemetryLogger(base_dir=str(tmp_path), enabled=False, keep_files=1)
    logger.prune()

    assert _names(tmp_path) == ["session_20260101_000000.csv"]