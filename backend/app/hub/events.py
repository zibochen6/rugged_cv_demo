"""In-memory Visual Hub event bus with JSONL persistence."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Optional

from .models import HubEvent, Severity


SEVERITY_RANK = {
    Severity.INFO.value: 0,
    Severity.WARNING.value: 1,
    Severity.DANGER.value: 2,
    Severity.ERROR.value: 3,
}


class EventBus:
    """Thread-safe ring buffer + optional append-only JSONL log."""

    def __init__(self, path: Optional[str] = None, maxlen: int = 500) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        self._events: deque[HubEvent] = deque(maxlen=maxlen)
        self._fh = None
        if path:
            try:
                directory = os.path.dirname(os.path.abspath(path))
                if directory:
                    os.makedirs(directory, exist_ok=True)
                self._fh = open(path, "a", encoding="utf-8")
            except OSError as exc:
                print(f"[hub] event log open failed: {exc}")
                self._fh = None

    def emit(
        self,
        source: str,
        kind: str,
        message: str,
        severity: str = Severity.INFO.value,
        payload: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> HubEvent:
        event = HubEvent(
            ts=float(ts if ts is not None else time.time()),
            source=str(source),
            severity=str(severity),
            kind=str(kind),
            message=str(message),
            payload=dict(payload or {}),
        )
        with self._lock:
            self._seq += 1
            event.id = self._seq
            self._events.append(event)
            fh = self._fh
        if fh is not None:
            try:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                fh.flush()
            except OSError as exc:
                print(f"[hub] event log write failed: {exc}")
        return event

    def recent(self, limit: int = 80, since_id: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            items = [e for e in self._events if e.id > since_id]
        if limit > 0:
            items = items[-limit:]
        return [e.to_dict() for e in items]

    def close(self) -> None:
        with self._lock:
            fh = self._fh
            self._fh = None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass


def classify_rear_level(level: Optional[str]) -> Optional[str]:
    if not level:
        return None
    mapping = {
        "SAFE": Severity.INFO.value,
        "WARNING": Severity.WARNING.value,
        "DANGER": Severity.DANGER.value,
        "SYSTEM_ERROR": Severity.ERROR.value,
        "SYSTEM ERROR": Severity.ERROR.value,
    }
    return mapping.get(str(level).upper())


def classify_fatigue(state: Optional[str]) -> Optional[str]:
    if not state:
        return None
    mapping = {
        "NORMAL": Severity.INFO.value,
        "WARN": Severity.WARNING.value,
        "WARNING": Severity.WARNING.value,
        "DROWSY_WARN": Severity.WARNING.value,
        "ALARM": Severity.DANGER.value,
        "DROWSY_ALARM": Severity.DANGER.value,
        "DISABLED": Severity.INFO.value,
        "UNKNOWN": Severity.INFO.value,
        "UNAVAILABLE": Severity.ERROR.value,
    }
    return mapping.get(str(state).upper())


def classify_helmet(verdict: Optional[str]) -> Optional[str]:
    if not verdict:
        return None
    mapping = {
        "worn": Severity.INFO.value,
        "not_worn": Severity.DANGER.value,
        "unknown": Severity.INFO.value,
        "unavailable": Severity.ERROR.value,
    }
    return mapping.get(str(verdict).lower())
