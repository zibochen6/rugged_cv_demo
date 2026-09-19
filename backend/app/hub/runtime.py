"""Visual Hub runtime: lifecycle, occupancy, status, event fan-in."""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

from .config import HubConfig, ModuleSpec, load_hub_config
from .events import EventBus, classify_fatigue, classify_helmet, classify_rear_level
from .models import ModuleState, OccupancyLease, Severity
from .occupancy import OccupancyError, OccupancyManager
from .proxy import fetch_json, post_json
from .signal_light import SignalLightController
from .supervisor import SupervisedProcess
from .thermal import ThermalController


LABELS = {
    "front": "前视分割",
    "rear": "后视预警",
    "dms": "座舱监测",
}


class HubRuntime:
    """Owns child-module lifecycle. Front inference stays in-process."""

    def __init__(self, config: Optional[HubConfig] = None,
                 thermal: Optional[ThermalController] = None) -> None:
        self.config = config or load_hub_config()
        self.events = EventBus(os.path.join(self.config.root, self.config.event_log))
        self.occupancy = OccupancyManager()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._poller: Optional[threading.Thread] = None
        self._children: Dict[str, SupervisedProcess] = {}
        self._states: Dict[str, Dict[str, Any]] = {
            mid: self._blank_status(mid) for mid in ("front", "rear", "dms")
        }
        self._last_rear_level: Optional[str] = None
        self._last_fatigue: Optional[str] = None
        self._last_helmet: Optional[str] = None
        self.signal_light = SignalLightController(
            port=os.environ.get(
                "HUB_SIGNAL_LIGHT_PORT",
                "/dev/serial/by-id/usb-1a86_5523-if00-port0",
            ),
            baud=int(os.environ.get("HUB_SIGNAL_LIGHT_BAUD", "9600")),
        )
        self._front_started_at: Optional[float] = None
        self.thermal = thermal or ThermalController()
        self._thermal_status = self.thermal.snapshot()
        self._thermal_applied: Dict[str, Optional[str]] = {
            "front": None,
            "rear": None,
            "dms": None,
        }

    def start(self) -> None:
        if self._poller and self._poller.is_alive():
            return
        self._stop.clear()
        # A service restart must never inherit a latched device-side pattern.
        self.signal_light.stop()
        self.events.emit("hub", "session", "Visual Hub started", Severity.INFO.value)
        self._poller = threading.Thread(target=self._poll_loop, name="hub-poll", daemon=True)
        self._poller.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            # Recording uses independent capture handles, which must be
            # released before shutting down the shared hub components.
            from ..recording.runtime import get_recording_runtime
            get_recording_runtime().shutdown()
        except Exception as exc:  # noqa: BLE001
            print(f"[hub] recording shutdown failed: {exc}")
        self.stop_all()
        self.signal_light.stop()
        self.events.close()
        if self._poller is not None:
            self._poller.join(timeout=2.0)
            self._poller = None

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            modules = {}
            for mid, status in self._states.items():
                item = dict(status)
                item.pop("native_url", None)
                started = item.get("started_at")
                item["uptime_s"] = round(now - started, 1) if started else 0.0
                modules[mid] = item
            occupancy = [
                {
                    **lease,
                    "device": self.config.module_specs().get(
                        str(lease.get("holder")),
                        self.config.module_specs()["front"],
                    ).camera_label,
                }
                for lease in self.occupancy.snapshot()
            ]
        overall = self._overall(modules)
        try:
            from ..recording.runtime import get_recording_runtime
            recording = get_recording_runtime().snapshot()
        except Exception as exc:  # noqa: BLE001
            recording = {"mode": "unknown", "error": str(exc)}
        return {
            "ok": overall != ModuleState.ERROR.value,
            "service": "visual-hub",
            "overall": overall,
            "ts": now,
            "modules": modules,
            "occupancy": occupancy,
            "events": self.events.recent(limit=40),
            "thermal": dict(self._thermal_status),
            "operation_mode": recording.get("mode", "inference"),
            "recording": recording,
            "signal_light": self.signal_light.snapshot(),
        }

    def module_status(self, module_id: str) -> Dict[str, Any]:
        snap = self.snapshot()
        if module_id not in snap["modules"]:
            raise KeyError(module_id)
        return snap["modules"][module_id]

    def start_module(self, module_id: str) -> Dict[str, Any]:
        self._ensure_inference_mode()
        if module_id == "front":
            return self._start_front()
        spec = self.config.module_specs()[module_id]
        if module_id == "rear" and not spec.camera.startswith("rtsp://"):
            raise RuntimeError("REAR_CAMERA_URL 未配置或不是 RTSP 地址")
        if module_id == "dms" and spec.camera != "usb:0":
            raise RuntimeError("座舱摄像头必须固定为 usb:0")
        ok, holder = self.occupancy.check_available(spec.camera_slot, spec.camera, module_id)
        if not ok:
            raise OccupancyError(
                f"{LABELS.get(module_id, module_id)} 无法启动：相机 {spec.camera} 正被 {holder} 占用",
                "CAMERA_BUSY",
            )
        with self._lock:
            child = self._children.get(module_id)
            if child and child.alive:
                return self.module_status(module_id)
            self._states[module_id]["state"] = ModuleState.STARTING.value
            self._states[module_id]["last_error"] = None
        lease = self.occupancy.acquire(spec.camera_slot, spec.camera, module_id)
        argv = self._argv_for(spec)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if module_id == "rear":
            env["REAR_CAMERA_URL"] = spec.camera
        elif module_id == "dms":
            env["DMS_CAMERA"] = "usb:0"
            env["DMS_FIXED_USB_ONLY"] = "1"
            env["DMS_THERMAL_STATE"] = str(
                self._thermal_status.get("state", "normal"))
        gld = "/lib/aarch64-linux-gnu/libGLdispatch.so.0"
        if os.path.exists(gld):
            env["LD_PRELOAD"] = gld + (":" + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else "")
        log_path = os.path.join(self.config.root, "logs", f"hub_{module_id}.log")
        child = SupervisedProcess(
            module_id=module_id,
            argv=argv,
            cwd=self.config.root,
            env=env,
            log_path=log_path,
            on_exit=self._on_child_exit,
        )
        try:
            child.start()
        except Exception as exc:
            self.occupancy.release(module_id)
            with self._lock:
                self._states[module_id]["state"] = ModuleState.ERROR.value
                self._states[module_id]["last_error"] = str(exc)
            self.events.emit(module_id, "error", f"{spec.label} 启动失败: {exc}", Severity.ERROR.value)
            raise
        with self._lock:
            self._children[module_id] = child
            self._states[module_id].update({
                "pid": child.pid,
                "started_at": child.started_at,
                "camera": spec.camera_label,
                "camera_slot": spec.camera_slot,
                "port": spec.port,
                "state": ModuleState.STARTING.value,
            })
            if module_id in self._thermal_applied:
                self._thermal_applied[module_id] = None
        self.events.emit(
            module_id,
            "session",
            f"{spec.label} 已启动 (pid={child.pid})",
            Severity.INFO.value,
            {"pid": child.pid, "camera": spec.camera_label},
        )
        return self.module_status(module_id)

    def stop_module(self, module_id: str) -> Dict[str, Any]:
        if module_id == "front":
            return self._stop_front()
        with self._lock:
            child = self._children.get(module_id)
            self._states[module_id]["state"] = ModuleState.STOPPING.value
        if child is not None:
            child.stop()
        if module_id == "rear":
            self.signal_light.clear_rear()
        elif module_id == "dms":
            self.signal_light.clear_fatigue()
        self.occupancy.release(module_id)
        with self._lock:
            self._children.pop(module_id, None)
            self._states[module_id].update({
                "state": ModuleState.STOPPED.value,
                "pid": None,
                "health_ok": False,
                "started_at": None,
                "metrics": {},
            })
            if module_id in self._thermal_applied:
                self._thermal_applied[module_id] = None
        self.events.emit(module_id, "session", f"{LABELS.get(module_id, module_id)} 已停止", Severity.INFO.value)
        return self.module_status(module_id)

    def restart_module(self, module_id: str) -> Dict[str, Any]:
        self._ensure_inference_mode()
        self.stop_module(module_id)
        return self.start_module(module_id)

    def start_all(self) -> Dict[str, Any]:
        self._ensure_inference_mode()
        modules: Dict[str, Any] = {}
        errors: Dict[str, Dict[str, str]] = {}
        for module_id in ("rear", "front", "dms"):
            try:
                modules[module_id] = self.start_module(module_id)
            except Exception as exc:  # partial success is intentional
                errors[module_id] = {
                    "code": getattr(exc, "code", "MODULE_START_FAILED"),
                    "message": str(exc),
                }
                modules[module_id] = self.module_status(module_id)
        return {"ok": not errors, "modules": modules, "errors": errors}

    def stop_all(self) -> Dict[str, Any]:
        modules: Dict[str, Any] = {}
        errors: Dict[str, Dict[str, str]] = {}
        for module_id in ("dms", "front", "rear"):
            try:
                modules[module_id] = self.stop_module(module_id)
            except Exception as exc:
                errors[module_id] = {
                    "code": "MODULE_STOP_FAILED",
                    "message": str(exc),
                }
                modules[module_id] = self.module_status(module_id)
        return {"ok": not errors, "modules": modules, "errors": errors}

    def apply_module_config(self, module_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        spec = self.config.module_specs()[module_id]
        if module_id == "rear":
            url = f"http://127.0.0.1:{spec.port}/api/config"
        elif module_id == "dms":
            url = f"http://127.0.0.1:{spec.port}/api/dms_config"
        else:
            raise KeyError(module_id)
        # DMS shares CPU with its models and can legitimately take longer
        # than a poll request to schedule its threaded HTTP handler. A short
        # timeout made successful switch updates look like failures in UI.
        data, err = post_json(url, payload, timeout=5.0)
        if err:
            raise RuntimeError(err)
        return data or {}

    def _start_front(self) -> Dict[str, Any]:
        spec = self.config.module_specs()["front"]
        if not spec.camera.startswith("rtsp://"):
            raise RuntimeError("FRONT_CAMERA_URL 未配置或不是 RTSP 地址")
        ok, holder = self.occupancy.check_available(spec.camera_slot, spec.camera, "front")
        if not ok:
            raise OccupancyError(f"前视相机正被 {holder} 占用", "CAMERA_BUSY")
        lease_acquired = False
        try:
            from ..camera.manager import get_camera_manager
            camera = get_camera_manager()
            if not camera.is_running:
                camera.start(device=spec.camera, width=1280, height=720, fps=30)
            self.occupancy.acquire(spec.camera_slot, spec.camera, "front")
            lease_acquired = True
        except Exception as exc:
            with self._lock:
                self._states["front"]["state"] = ModuleState.DEGRADED.value
                self._states["front"]["last_error"] = str(exc)
            self.events.emit("front", "error", f"前视相机启动失败: {exc}", Severity.ERROR.value)
            raise RuntimeError(str(exc)) from exc
        try:
            from ..segment.service import get_segment_service
            result = get_segment_service().start()
            if not result.get("ok"):
                raise RuntimeError(result.get("message") or "分割服务启动失败")
        except Exception as exc:
            if lease_acquired:
                self.occupancy.release("front")
            camera.stop()
            with self._lock:
                self._states["front"]["state"] = ModuleState.ERROR.value
                self._states["front"]["last_error"] = str(exc)
            self.events.emit("front", "error", f"分割服务启动失败: {exc}", Severity.ERROR.value)
            raise RuntimeError(str(exc)) from exc
        now = time.time()
        with self._lock:
            self._states["front"].update({
                "state": ModuleState.RUNNING.value,
                "health_ok": True,
                "started_at": now,
                "camera": spec.camera_label,
                "camera_slot": spec.camera_slot,
                "port": spec.port,
                "last_error": None,
            })
            self._thermal_applied["front"] = None
        self.events.emit("front", "session", "前视分割已就绪", Severity.INFO.value)
        return self.module_status("front")

    def _stop_front(self) -> Dict[str, Any]:
        try:
            from ..segment.service import get_segment_service
            get_segment_service().stop()
        except Exception:
            pass
        try:
            from ..camera.manager import get_camera_manager
            get_camera_manager().stop()
        except Exception:
            pass
        self.occupancy.release("front")
        with self._lock:
            self._states["front"].update({
                "state": ModuleState.STOPPED.value,
                "health_ok": False,
                "started_at": None,
                "last_error": None,
                "metrics": {},
            })
            self._thermal_applied["front"] = None
        self.events.emit("front", "session", "前视分割已停止并释放资源", Severity.INFO.value)
        return self.module_status("front")

    def _argv_for(self, spec: ModuleSpec) -> list:
        py = self.config.resolved_python()
        if spec.id == "rear":
            return [
                py, "-u", "app/warn_app.py",
                "--web", "--headless",
                "--host", "127.0.0.1",
                "--port", str(spec.port),
                "--alarm-mode", "none",
            ]
        if spec.id == "dms":
            return [
                py, "-u", "app/dms_app.py",
                "--mode", "web", "--headless",
                "--host", "127.0.0.1",
                "--port", str(spec.port),
                "--camera", spec.camera,
            ]
        raise KeyError(spec.id)

    def _on_child_exit(self, module_id: str, code: int) -> None:
        self.occupancy.release(module_id)
        if module_id == "rear":
            self.signal_light.clear_rear()
        elif module_id == "dms":
            self.signal_light.clear_fatigue()
        with self._lock:
            status = self._states.get(module_id, {})
            if status.get("state") == ModuleState.STOPPING.value:
                status["state"] = ModuleState.STOPPED.value
                status["health_ok"] = False
                status["pid"] = None
                return
            status["state"] = ModuleState.ERROR.value
            status["health_ok"] = False
            status["pid"] = None
            status["last_error"] = f"process exited with code {code}"
        self.events.emit(
            module_id,
            "error",
            f"{LABELS.get(module_id, module_id)} 异常退出 (code={code})",
            Severity.ERROR.value,
            {"code": code},
        )

    def _poll_loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[hub] poll failed: {exc}")

    def _poll_once(self) -> None:
        self._thermal_status = self.thermal.update()
        specs = self.config.module_specs()
        with self._lock:
            front_state = self._states["front"]["state"]
            rear_state = self._states["rear"]["state"]
            dms_state = self._states["dms"]["state"]
        if front_state not in (ModuleState.STOPPED.value, ModuleState.STOPPING.value):
            self._poll_front(specs["front"])
        if rear_state not in (ModuleState.STOPPED.value, ModuleState.STOPPING.value):
            self._poll_child("rear", specs["rear"])
        if dms_state not in (ModuleState.STOPPED.value, ModuleState.STOPPING.value):
            self._poll_child("dms", specs["dms"])
        self._apply_thermal_policy(front_state, rear_state, dms_state, specs)

    def _apply_thermal_policy(
        self,
        front_state: str,
        rear_state: str,
        dms_state: str,
        specs: Dict[str, ModuleSpec],
    ) -> None:
        thermal_state = str(self._thermal_status.get("state", "normal"))
        policy = self._thermal_status.get("policy") or {}
        reason = self._thermal_status.get("reason")
        active_states = {
            ModuleState.STARTING.value,
            ModuleState.RUNNING.value,
            ModuleState.DEGRADED.value,
        }
        if (front_state in active_states
                and self._thermal_applied.get("front") != thermal_state):
            try:
                from ..segment.service import get_segment_service
                get_segment_service().set_target_fps(
                    float(policy.get("front_fps", 8.0)))
                self._thermal_applied["front"] = thermal_state
            except Exception as exc:  # noqa: BLE001
                print(f"[hub] front thermal policy failed: {exc}")
        if (rear_state in active_states
                and self._thermal_applied.get("rear") != thermal_state):
            payload = {
                "thermal_state": thermal_state,
                "depth_target_fps": float(
                    policy.get("rear_depth_fps", 10.0)),
                "person_target_fps": float(
                    policy.get("rear_person_fps", 5.0)),
                "degradation_reason": reason,
            }
            _, err = post_json(
                f"http://127.0.0.1:{specs['rear'].port}/api/config",
                payload,
            )
            if err is None:
                self._thermal_applied["rear"] = thermal_state
        if (dms_state in active_states
                and self._thermal_applied.get("dms") != thermal_state):
            payload = {
                "thermal_state": thermal_state,
                "helmet_target_fps": float(
                    policy.get("dms_helmet_fps", 5.0)),
                "helmet_suspended": bool(
                    policy.get("dms_helmet_suspended", False)),
                "degradation_reason": reason,
            }
            _, err = post_json(
                f"http://127.0.0.1:{specs['dms'].port}/api/dms_config",
                payload,
            )
            if err is None:
                self._thermal_applied["dms"] = thermal_state

    def _poll_front(self, spec: ModuleSpec) -> None:
        try:
            from ..camera.manager import get_camera_manager
            from ..segment.service import get_segment_service
            data = get_segment_service().status()
            capture = get_camera_manager().metrics()
            err = None
        except Exception as exc:
            data, capture, err = {}, {}, str(exc)
        with self._lock:
            status = self._states["front"]
            if err:
                if status["state"] not in (ModuleState.STOPPED.value, ModuleState.STOPPING.value):
                    status["health_ok"] = False
                    status["state"] = ModuleState.DEGRADED.value
                    status["last_error"] = err
                return
            status["health_ok"] = True
            if status["state"] == ModuleState.STOPPED.value:
                return
            status["state"] = ModuleState.RUNNING.value
            status["last_error"] = None
            status["metrics"] = {
                "state": data.get("state"),
                "has_target": data.get("has_target"),
                "model_fps": data.get("model_fps"),
                "infer_ms": data.get("infer_ms"),
                "camera_running": data.get("camera_running"),
                **capture,
                "inference_fps": data.get("model_fps"),
                "target_fps": data.get("target_fps"),
            }
            status["stream_url"] = f"/api/hub/stream/front"
            status["native_url"] = None
            status["camera"] = spec.camera_label
            status["camera_slot"] = spec.camera_slot
            status["port"] = spec.port

    def _poll_child(self, module_id: str, spec: ModuleSpec) -> None:
        with self._lock:
            child = self._children.get(module_id)
            status = self._states[module_id]
            if not child or not child.alive:
                if status["state"] in (ModuleState.STARTING.value, ModuleState.RUNNING.value, ModuleState.DEGRADED.value):
                    status["state"] = ModuleState.ERROR.value
                    status["health_ok"] = False
                return
        health, health_err = fetch_json(f"http://127.0.0.1:{spec.port}{spec.health_path}")
        state, state_err = fetch_json(f"http://127.0.0.1:{spec.port}{spec.state_path}")
        with self._lock:
            status = self._states[module_id]
            status["pid"] = child.pid if child else None
            status["port"] = spec.port
            status["camera"] = spec.camera_label
            status["camera_slot"] = spec.camera_slot
            status["stream_url"] = f"/api/hub/stream/{module_id}"
            status["native_url"] = f"http://127.0.0.1:{spec.port}{spec.stream_path}"
            if health_err and state_err:
                status["health_ok"] = False
                if status["state"] == ModuleState.STARTING.value and child and child.started_at and time.time() - child.started_at < 20:
                    return
                status["state"] = ModuleState.DEGRADED.value
                status["last_error"] = health_err or state_err
                return
            status["health_ok"] = True
            status["state"] = ModuleState.RUNNING.value
            status["last_error"] = None
            status["metrics"] = state or health or {}
            metrics = status["metrics"]
            if module_id == "rear":
                metrics["capture_fps"] = metrics.get("capture_fps") or metrics.get("fps")
                metrics["inference_fps"] = (
                    metrics.get("depth_inference_fps") or metrics.get("fps"))
                snap_ts = metrics.get("snap_ts")
                age_ms = metrics.get("stream_age_ms")
                metrics["frame_age_s"] = (
                    round(time.time() - float(snap_ts), 3) if snap_ts
                    else round(float(age_ms) / 1000.0, 3) if age_ms is not None else None
                )
            elif module_id == "dms":
                dms = metrics.get("dms") or {}
                metrics["capture_fps"] = metrics.get("fps") or dms.get("fps")
                helmet = dms.get("helmet") or {}
                fatigue = dms.get("fatigue") or {}
                metrics["inference_fps"] = helmet.get("inference_fps")
                metrics["helmet_inference_fps"] = helmet.get("inference_fps")
                metrics["fatigue_inference_fps"] = fatigue.get("inference_fps")
                frame_ts = metrics.get("ts")
                metrics["frame_age_s"] = round(time.time() - float(frame_ts), 3) if frame_ts else None
        if module_id == "rear":
            self._ingest_rear(state or {})
        elif module_id == "dms":
            self._ingest_dms(state or {})

    def _ingest_rear(self, state: Dict[str, Any]) -> None:
        level = str(state.get("level") or "")
        self.signal_light.update_rear(level or "SAFE", bool(state.get("buzzer", False)))
        if not level or level == self._last_rear_level:
            return
        prev = self._last_rear_level
        self._last_rear_level = level
        severity = classify_rear_level(level) or Severity.INFO.value
        if severity in (Severity.WARNING.value, Severity.DANGER.value, Severity.ERROR.value) or (
            prev in ("WARNING", "DANGER", "SYSTEM_ERROR", "SYSTEM ERROR")
        ):
            dist = state.get("distance")
            ttc = state.get("ttc")
            msg = f"后视 {level}"
            if dist is not None:
                msg += f" 距离={dist}m"
            if ttc is not None:
                msg += f" TTC={ttc}s"
            self.events.emit("rear", "risk", msg, severity, {
                "level": level,
                "distance": dist,
                "ttc": ttc,
                "people": state.get("people"),
            })

    def _ingest_dms(self, state: Dict[str, Any]) -> None:
        dms = state.get("dms") or {}
        fatigue = (dms.get("fatigue") or {})
        helmet = (dms.get("helmet") or {})
        fat_state = str(fatigue.get("state") or "")
        self.signal_light.update_fatigue(
            fat_state or "NORMAL", bool(fatigue.get("alarm_buzzer", False)))
        if fat_state and fat_state != self._last_fatigue:
            prev = self._last_fatigue
            self._last_fatigue = fat_state
            severity = classify_fatigue(fat_state) or Severity.INFO.value
            if fat_state in ("DROWSY_WARN", "WARN", "WARNING", "DROWSY_ALARM", "ALARM") or (
                prev in ("DROWSY_WARN", "WARN", "WARNING", "DROWSY_ALARM", "ALARM")
                and fat_state == "NORMAL"
            ):
                self.events.emit("dms", "fatigue_state", f"Cabin fatigue: {fat_state}", severity, {
                    "state": fat_state,
                    "score": fatigue.get("score"),
                    "face_present": fatigue.get("face_present"),
                })
        persons = helmet.get("persons") or []
        helmet_key = ",".join(sorted({str(p.get("verdict")) for p in persons})) or "none"
        if helmet_key != self._last_helmet:
            prev = self._last_helmet
            self._last_helmet = helmet_key
            worst = "worn"
            for p in persons:
                verdict = str(p.get("verdict") or "unknown")
                if verdict == "not_worn":
                    worst = "not_worn"
                    break
                if verdict == "unknown" and worst != "not_worn":
                    worst = "unknown"
            severity = classify_helmet(worst) or Severity.INFO.value
            if worst == "not_worn" or (prev and "not_worn" in (prev or "") and worst == "worn"):
                self.events.emit("dms", "helmet_verdict", f"Helmet: {worst} ({len(persons)} people)", severity, {
                    "verdict": worst,
                    "persons": persons,
                })

    def _blank_status(self, module_id: str) -> Dict[str, Any]:
        spec = self.config.module_specs()[module_id]
        return {
            "id": module_id,
            "label": LABELS[module_id],
            "state": ModuleState.STOPPED.value,
            "pid": None,
            "port": spec.port,
            "camera": spec.camera_label,
            "camera_slot": spec.camera_slot,
            "health_ok": False,
            "last_error": None,
            "started_at": None,
            "uptime_s": 0.0,
            "metrics": {},
            "stream_url": f"/api/hub/stream/{module_id}",
            "native_url": None,
        }

    @staticmethod
    def _ensure_inference_mode() -> None:
        from ..recording.runtime import RecordingConflict, get_recording_runtime
        if get_recording_runtime().mode == "recording":
            raise RecordingConflict("exit recording mode before starting inference")

    @staticmethod
    def _overall(modules: Dict[str, Dict[str, Any]]) -> str:
        states = [m.get("state") for m in modules.values()]
        if any(s == ModuleState.ERROR.value for s in states):
            return ModuleState.ERROR.value
        if any(s == ModuleState.DEGRADED.value for s in states):
            return ModuleState.DEGRADED.value
        if any(s == ModuleState.RUNNING.value for s in states):
            return ModuleState.RUNNING.value
        if any(s == ModuleState.STARTING.value for s in states):
            return ModuleState.STARTING.value
        return ModuleState.STOPPED.value


_RUNTIME: Optional[HubRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_hub_runtime() -> HubRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = HubRuntime()
        return _RUNTIME


def set_hub_runtime(runtime: Optional[HubRuntime]) -> None:
    global _RUNTIME
    with _RUNTIME_LOCK:
        _RUNTIME = runtime
