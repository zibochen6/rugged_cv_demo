"""Depth engine backends.

`DepthEngine.infer(frame_bgr) -> np.ndarray`  returns metric depth in METERS
at camera resolution (H x W float32). Invalid pixels are NaN.

Backends:
  PyTorchDepthEngine  - eager fp16/fp32 (baseline, works everywhere)
  TensorRTDepthEngine - prebuilt FP16 engine, CUDA buffers reused, warmup;
                        silently falls back to the PyTorch backend if the
                        engine file is missing/errors (fail-visible >= log).
"""
from __future__ import annotations

import os
import time
from typing import Optional

import cv2
import numpy as np

from app.depth.preprocessing import preprocess_bgr, _target_size


class DepthEngine:
    """Abstract metric-depth backend."""

    name = "abstract"
    model_input_hw: tuple[int, int] = (280, 518)  # (H, W) fixed static shape

    def infer(self, frame_bgr: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    @property
    def last_ms(self) -> float:
        return getattr(self, "_last_ms", 0.0)


class PyTorchDepthEngine(DepthEngine):
    """Eager PyTorch backend (the P0 baseline). Holds its own model; must be
    constructed after torch import, once, and reused."""

    name = "pytorch"

    def __init__(self, checkpoint: str, longest_edge: int = 518,
                 dtype: str = "fp16", device: str = "cuda",
                 out_hw: tuple | None = None) -> None:
        import torch
        from app.depth.estimator import MetricDepthEstimator
        self.torch = torch
        self.out_hw = tuple(out_hw) if out_hw else None   # (h, w) optional
        self._dtype = {"fp32": torch.float32, "fp16": torch.float16,
                       "bf16": torch.bfloat16}[dtype]
        self._back = MetricDepthEstimator(
            checkpoint_dir=checkpoint, device=device,
            dtype=self._dtype, longest_edge=longest_edge)

    def infer(self, frame_bgr: np.ndarray) -> np.ndarray:
        t0 = time.perf_counter()
        depth = self._back.predict(frame_bgr)  # H x W float32 meters (CPU)
        if self.out_hw is not None and depth.shape[:2] != tuple(self.out_hw):
            oh, ow = self.out_hw
            depth = cv2.resize(depth, (ow, oh), interpolation=cv2.INTER_NEAREST)
        self._last_ms = (time.perf_counter() - t0) * 1000.0
        return depth.astype(np.float32, copy=False)


class TensorRTDepthEngine(DepthEngine):
    """TRT FP16 engine backend.

    Engine built for static input 3 x model_input_hw (e.g. 3x280x518).
    The DAV2 raw head output is the input-sized grid; we blit it to camera
    resolution with an OpenCV resize (bilinear).

    `fallback_factory` builds the PyTorch backend LAZILY (only when this
    engine cannot run). Creating the torch CUDA context eagerly would clash
    with the TRT execution context (Myelin "Final synchronize failed" /
    illegal memory access on Orin), so the torch model must not be loaded
    while a TRT engine is active.
    """

    name = "tensorrt"

    def __init__(self, engine_path: str, checkpoint: str,
                 longest_edge: int = 518, dtype: str = "fp16",
                 fallback_factory=None, out_hw: tuple | None = None) -> None:
        self.engine_path = engine_path
        self._fb_factory = fallback_factory
        self._fb = None
        self._ctx = None
        self._load_error = ""
        self.out_hw = tuple(out_hw) if out_hw else None   # (h, w) optional
        self.model_input_hw = self._probe_hw(longest_edge)
        try:
            self._load_engine()
        except Exception as exc:  # noqa: BLE001 - any load error => fallback
            self._load_error = f"{type(exc).__name__}: {exc}"
            print(f"[warn:trt] engine load failed ({self._load_error}); "
                  f"falling back to pytorch")

    # -- internal -------------------------------------------------------------
    @staticmethod
    def _probe_hw(longest_edge: int, cam_h: int = 1296, cam_w: int = 2304):
        h, w = _target_size(cam_h, cam_w, longest_edge)
        return (h, w)

    def _load_engine(self) -> None:
        if not (self.engine_path and os.path.exists(self.engine_path)):
            raise FileNotFoundError(self.engine_path)
        import tensorrt as trt
        import torch as _torch
        # initialize torch's CUDA primary context FIRST so the TRT runtime
        # attaches to the same context (mixing contexts crashes on Orin)
        if _torch.cuda.is_available():
            _torch.cuda.init()
        self._torch = _torch
        logger = trt.Logger(trt.Logger.WARNING)
        with open(self.engine_path, "rb") as fh, \
                trt.Runtime(logger) as runtime:
            engine = runtime.deserialize_cuda_engine(fh.read())
        if engine is None:
            raise RuntimeError("deserialize_cuda_engine returned None")
        self._ctx = engine.create_execution_context()
        self._engine = engine
        # enumerate bindings in ENGINE order (order may differ from our intuitions)
        self._out_idx, self._out_dtype, self._out_size = None, None, None
        self._in_idx = None
        for i in range(engine.num_bindings):
            if engine.binding_is_input(i):
                self._in_idx = i
            else:
                self._out_idx = i
                self._out_size = tuple(engine.get_binding_shape(i))
                self._out_dtype = engine.get_binding_dtype(i)
        if self._in_idx is None or self._out_idx is None:
            raise RuntimeError("engine must have exactly 1 input + 1 output")
        ih, iw = self.model_input_hw
        self._x_dev = _torch.zeros((1, 3, ih, iw), dtype=_torch.float32,
                                   device="cuda")
        self._y_dev = _torch.zeros(tuple(self._out_size), dtype=_torch.float32,
                                   device="cuda")

    def available(self) -> bool:
        return self._ctx is not None

    # -- inference ------------------------------------------------------------
    @staticmethod
    def _preprocess_np(frame_bgr: np.ndarray, ih: int, iw: int) -> np.ndarray:
        """torch-free preprocessing into (1,3,ih,iw) float32 CHW (imagenet norm)."""
        import cv2 as _cv2
        rgb = _cv2.cvtColor(frame_bgr, _cv2.COLOR_BGR2RGB)
        rgb = _cv2.resize(rgb, (iw, ih), interpolation=_cv2.INTER_LINEAR)
        x = rgb.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        x = (x - mean) / std
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None, ...])

    def _get_fallback(self):
        if self._fb is None and self._fb_factory is not None:
            self._fb = self._fb_factory()
        return self._fb

    def infer(self, frame_bgr: np.ndarray) -> np.ndarray:
        if not self.available():
            fb = self._get_fallback()
            if fb is not None:
                self._last_ms = fb.last_ms
                return fb.infer(frame_bgr)
            raise RuntimeError(f"TRT engine unavailable: {self._load_error}")

        h, w = frame_bgr.shape[:2]
        ih, iw = self.model_input_hw
        t0 = time.perf_counter()
        # preprocess on CPU, upload to the persistent GPU input buffer
        x_host = self._preprocess_np(frame_bgr, ih, iw)
        self._x_dev.copy_(self._torch.from_numpy(x_host), non_blocking=True)
        bindings = [0] * self._engine.num_bindings
        bindings[self._in_idx] = int(self._x_dev.data_ptr())
        bindings[self._out_idx] = int(self._y_dev.data_ptr())
        self._ctx.execute_v2(bindings)
        self._torch.cuda.synchronize()
        d_host = self._y_dev[0].cpu().numpy()
        if self.out_hw is not None:
            oh, ow = self.out_hw
            depth_cam = cv2.resize(d_host, (ow, oh),
                                   interpolation=cv2.INTER_NEAREST)
        else:
            depth_cam = cv2.resize(d_host, (w, h),
                                   interpolation=cv2.INTER_LINEAR)
        self._last_ms = (time.perf_counter() - t0) * 1000.0
        return depth_cam.astype(np.float32, copy=False)


def make_depth_engine(cfg, out_hw: tuple | None = None) -> DepthEngine:
    """Build the configured backend.

    tensorrt backend: the PyTorch engine is created LAZILY (fallback factory)
    so torch does not initialize its CUDA context while the TRT engine is
    active (mixing the two contexts crashes on Orin/TRT8.5).
    out_hw: optional (h,w) output resolution (skips full-res resize)."""
    ckpt = cfg.get("model.checkpoint",
                   "checkpoints/depth_anything_v2_metric_indoor_small")
    edge = int(cfg.get("model.input_longest_edge", 518))
    dtype = str(cfg.get("model.dtype", "fp16"))
    backend = str(cfg.get("system.backend", "pytorch")).lower()
    if backend in ("tensorrt", "trt"):
        engine_path = cfg.get("model.engine", "")
        trt_engine = TensorRTDepthEngine(
            engine_path, ckpt, longest_edge=edge, dtype=dtype,
            out_hw=out_hw,
            fallback_factory=lambda: PyTorchDepthEngine(
                ckpt, longest_edge=edge, dtype=dtype, out_hw=out_hw))
        if trt_engine.available():
            return trt_engine
        print("[warn:trt] unavailable — using pytorch backend")
        return trt_engine._get_fallback()
    return PyTorchDepthEngine(ckpt, longest_edge=edge, dtype=dtype,
                              out_hw=out_hw)