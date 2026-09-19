"""Export Depth Anything V2 Metric Small to ONNX (static shape).

The model converts trivially (no FlashAttention in the HF DINOv2 impl, plain
matmul/softmax/interpolate). Static input 3xHxW is derived from the camera
aspect: longest edge (518) with both dims snapped to multiples of 14.

Run (on the NX, inside the venv):
    .venv/bin/python scripts/export_depth_onnx.py \
        --camera 2304 1296 --longest 518 \
        --checkpoint checkpoints/depth_anything_v2_metric_indoor_small \
        --out models/onnx/depth_metric_small.onnx
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import torch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", nargs=2, type=int, default=[2304, 1296],
                    metavar=("W", "H"))
    ap.add_argument("--longest", type=int, default=518)
    ap.add_argument("--checkpoint",
                    default="checkpoints/depth_anything_v2_metric_indoor_small")
    ap.add_argument("--out", default="models/onnx/depth_metric_small.onnx")
    ap.add_argument("--opset", type=int, default=16,
                    help="opset <= 16 decomposes LayerNorm (TRT 8.5 has no "
                         "LayerNormalization importer)")
    args = ap.parse_args()

    w_cam, h_cam = args.camera

    def target(h, w, longest, mult=14):
        scale = longest / float(max(h, w))
        nh = max(mult, (int(round(h * scale)) // mult) * mult)
        nw = max(mult, (int(round(w * scale)) // mult) * mult)
        return nh, nw

    in_h, in_w = target(h_cam, w_cam, args.longest)

    from transformers import DepthAnythingForDepthEstimation
    print(f"loading {args.checkpoint} ...")
    model = DepthAnythingForDepthEstimation.from_pretrained(args.checkpoint)
    model.eval()

    # thin wrapper: single output tensor - NO extra squeeze (the HF model
    # already returns (1,H,W); an extra Squeeze node breaks TRT 8.5's shape
    # analyzer: "Reshaping [1,280,518] to [1,518]")
    class Wrapped(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, pixel_values):
            out = self.m(pixel_values=pixel_values)
            return out.predicted_depth       # (1, H, W)

    wrapped = Wrapped(model)
    x = torch.zeros((1, 3, in_h, in_w), dtype=torch.float32)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    torch.onnx.export(
        wrapped, (x,), args.out,
        input_names=["pixel_values"],
        output_names=["predicted_depth"],
        opset_version=args.opset,
        do_constant_folding=True,
    )
    print(f"ONNX written: {args.out}  input 3x{in_h}x{in_w}")
    # report output shape
    with torch.inference_mode():
        y = wrapped(x)
    print(f"torch output shape: {tuple(y.shape)}")


if __name__ == "__main__":
    main()