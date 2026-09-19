"""Append-only event log for the DMS demo (logs/dms_events.jsonl).

一行一个 JSON 对象: {"ts": float, "kind": str, "payload": dict}。
写失败**不得**中断主循环（对齐 app/warn_app.py 的 "dataset must never kill
loop" 策略），只打印一行诊断。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

KINDS = (
    "session",
    "switch",
    "fatigue_state",
    "fatigue_face_lost",
    "helmet_verdict",
    "error",
)


class DmsEventLogger:
    def __init__(self, path: str = "logs/dms_events.jsonl",
                 enabled: bool = True) -> None:
        self.path = path
        self.enabled = bool(enabled)
        self._fh = None
        if self.enabled:
            self._open()

    def _open(self) -> None:
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - logging must never kill loop
            self._fh = None
            print(f"[dms] event log unavailable ({self.path}): {exc}")

    def log(self, kind: str, payload: Optional[dict] = None) -> None:
        if not self.enabled:
            return
        if kind not in KINDS:
            kind = "error"
        record = {"ts": round(time.time(), 3), "kind": kind,
                  "payload": payload or {}}
        try:
            if self._fh is None:
                self._open()
            if self._fh is None:
                return
            self._fh.write(json.dumps(record, ensure_ascii=False,
                                      default=str) + "\n")
            self._fh.flush()
        except Exception as exc:  # noqa: BLE001
            print(f"[dms] event log write failed: {exc}")

    def close(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:  # noqa: BLE001
            pass
        self._fh = None