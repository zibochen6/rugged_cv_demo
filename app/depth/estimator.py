"""Metric depth estimator (Depth Anything V2, PyTorch eager CUDA).

Single responsibility: RGB frame -> metric depth in METERS (H x W float32),
at CAMERA resolution. No TensorRT yet (Phase 14: validate PyTorch first).

Model: depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf
  - fine-tuned on Hypersim for INDOOR metric depth (meters)
  - DINOv2-ViT-S backbone + DPT head, ~25M params, input longest edge 518
  - NOTE: single-image learned-prior depth, NOT a LiDAR measurement (§43).
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F

from app.depth.preprocessing import preprocess_bgr

DEFAULT_CHECKPOINT = "checkpoints/depth_anything_v2_metric_indoor_small"


class MetricDepthEstimator:
    """Depth Anything V2 metric depth in PyTorch eager mode.

    Usage:
        est = MetricDepthEstimator()            # cuda, fp32
        depth_m = est.predict(frame_bgr)        # H x W float32, meters
    """

    def __init__(
        self,
        checkpoint_dir: str = DEFAULT_CHECKPOINT,
        device: str = "cuda",
        dtype: torch.dtype = torch.float32,
        longest_edge: int = 518,
        is_metric: bool = True,
    ) -> None:
        from transformers import DepthAnythingForDepthEstimation

        self.device = torch.device(device)
        self.dtype = dtype
        self.longest_edge = longest_edge
        self.is_metric = is_metric  # False would mean "relative depth only" (§81)

        self.model = DepthAnythingForDepthEstimation.from_pretrained(checkpoint_dir)
        self.model.eval()
        self.model.to(self.device)
        if dtype != torch.float32:
            self.model.to(dtype)

    @torch.inference_mode()
    def predict_tensor(self, frame_bgr: np.ndarray) -> torch.Tensor:
        """Depth at CAMERA resolution as a CUDA float32 tensor (meters).

        Kept on GPU so Phase 10B mask-depth sampling can index it directly
        without a CPU round-trip (§39).
        """
        pre = preprocess_bgr(frame_bgr, self.longest_edge, device=self.device)
        x = pre.pixel_values.to(self.dtype)
        out = self.model(pixel_values=x)
        depth = out.predicted_depth  # (1, h_model, w_model) in meters

        # predicted_depth spans the padded model input; our resize has no
        # padding (we snapped to multiples of 14 ourselves), so just map it
        # back to camera resolution with a single bilinear resize.
        depth = depth.unsqueeze(0).to(torch.float32)  # (1,1,h,w) if needed
        if depth.dim() == 3:
            depth = depth.unsqueeze(0)
        depth = F.interpolate(
            depth, size=(pre.orig_h, pre.orig_w), mode="bilinear", align_corners=False
        )
        return depth.squeeze(0).squeeze(0)  # (H, W) float32 CUDA, meters

    def predict(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Depth at CAMERA resolution as H x W float32 numpy (meters)."""
        return self.predict_tensor(frame_bgr).cpu().numpy()


def depth_stats(depth_m: np.ndarray) -> dict:
    """Basic sanity stats over valid (finite, > 0) pixels."""
    valid = depth_m[np.isfinite(depth_m) & (depth_m > 0)]
    if valid.size == 0:
        return {"valid": 0, "min": float("nan"), "max": float("nan"), "median": float("nan")}
    return {
        "valid": int(valid.size),
        "min": float(valid.min()),
        "max": float(valid.max()),
        "median": float(np.median(valid)),
    }
