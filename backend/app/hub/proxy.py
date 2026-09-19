"""HTTP helpers for Visual Hub module health/state/stream proxy."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def fetch_json(url: str, timeout: float = 1.2) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8") or "{}"), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - supervisor must never crash on a dead module
        return None, str(exc) or type(exc).__name__


def post_json(url: str, payload: Dict[str, Any], timeout: float = 1.5) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    body = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if not raw:
            return {}, None
        return json.loads(raw.decode("utf-8") or "{}"), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc) or type(exc).__name__
