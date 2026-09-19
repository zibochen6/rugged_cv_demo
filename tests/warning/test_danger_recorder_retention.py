"""DangerRecorder retention: snapshots must not grow without bound.

Regression: a busy rear-warning session wrote 5291 danger_*.jpg (1.2 GB) before
the 2026-09-18 cleanup because nothing ever deleted them.
"""
import os
import time

from app.warning.recorder import DangerRecorder


def _touch(directory, name, mtime):
    path = os.path.join(str(directory), name)
    with open(path, "wb") as fh:
        fh.write(b"jpeg")
    os.utime(path, (mtime, mtime))
    return path


def _names(directory):
    return sorted(p.name for p in directory.iterdir())


def test_prune_keeps_only_the_newest_files(tmp_path):
    now = time.time()
    for i in range(10):
        _touch(tmp_path, f"danger_2026010{i}_120000_000.jpg", now - (10 - i) * 60)

    DangerRecorder(base_dir=str(tmp_path), enabled=True, keep_files=4, keep_days=14.0)

    remaining = _names(tmp_path)
    assert len(remaining) == 4
    assert remaining == [
        "danger_20260106_120000_000.jpg",
        "danger_20260107_120000_000.jpg",
        "danger_20260108_120000_000.jpg",
        "danger_20260109_120000_000.jpg",
    ]


def test_prune_drops_expired_files_even_below_keep_count(tmp_path):
    now = time.time()
    _touch(tmp_path, "danger_expired_000.jpg", now - 30 * 86400)
    _touch(tmp_path, "danger_fresh_000.jpg", now - 60)

    DangerRecorder(base_dir=str(tmp_path), enabled=True, keep_files=100, keep_days=14.0)

    assert _names(tmp_path) == ["danger_fresh_000.jpg"]


def test_prune_ignores_unrelated_files(tmp_path):
    now = time.time()
    _touch(tmp_path, "hub_rear.log", now - 90 * 86400)
    _touch(tmp_path, "danger_keep_000.jpg", now - 60)

    DangerRecorder(base_dir=str(tmp_path), enabled=True, keep_files=1, keep_days=14.0)

    assert _names(tmp_path) == ["danger_keep_000.jpg", "hub_rear.log"]


def test_prune_is_rate_limited_unless_forced(tmp_path):
    now = time.time()
    rec = DangerRecorder(base_dir=str(tmp_path), enabled=True, keep_files=1,
                         keep_days=14.0, prune_interval_s=3600.0)
    _touch(tmp_path, "danger_a_000.jpg", now - 120)
    _touch(tmp_path, "danger_b_000.jpg", now - 60)

    assert rec.prune() == 0
    assert rec.prune(force=True) == 1
    assert _names(tmp_path) == ["danger_b_000.jpg"]


def test_disabled_recorder_never_prunes(tmp_path):
    now = time.time()
    _touch(tmp_path, "danger_a_000.jpg", now - 60)
    _touch(tmp_path, "danger_b_000.jpg", now - 30)

    rec = DangerRecorder(base_dir=str(tmp_path), enabled=False, keep_files=1)
    assert rec.prune(force=True) == 0
    assert len(_names(tmp_path)) == 2