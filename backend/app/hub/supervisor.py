"""Supervise Visual Hub child processes without owning inference."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from .models import ModuleId, ModuleState


class SupervisedProcess:
    def __init__(
        self,
        module_id: str,
        argv: List[str],
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        log_path: Optional[str] = None,
        on_exit: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        self.module_id = module_id
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.log_path = log_path
        self.on_exit = on_exit
        self.proc: Optional[subprocess.Popen] = None
        self.started_at: Optional[float] = None
        self.returncode: Optional[int] = None
        self.last_error: Optional[str] = None
        self._log_fh = None
        self._waiter: Optional[threading.Thread] = None

    @property
    def pid(self) -> Optional[int]:
        return None if self.proc is None else self.proc.pid

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if self.alive:
            return
        if self.log_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)
            self._log_fh = open(self.log_path, "ab")
            stdout = self._log_fh
            stderr = subprocess.STDOUT
        else:
            stdout = subprocess.DEVNULL
            stderr = subprocess.DEVNULL
        self.proc = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            env=self.env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        self.started_at = time.time()
        self.returncode = None
        self.last_error = None
        self._waiter = threading.Thread(target=self._wait, name=f"hub-wait-{self.module_id}", daemon=True)
        self._waiter.start()

    def _wait(self) -> None:
        if self.proc is None:
            return
        code = self.proc.wait()
        self.returncode = code
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None
        if self.on_exit is not None:
            try:
                self.on_exit(self.module_id, int(code))
            except Exception:
                pass

    def stop(self, timeout: float = 8.0) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    proc.terminate()
                except OSError:
                    pass
            deadline = time.time() + timeout
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                proc.wait(timeout=3)
        self.returncode = proc.poll()
        self.proc = None
