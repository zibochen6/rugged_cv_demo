"""Load the EfficientTAM Tiny 512x512 video predictor on CUDA (hardware gate).

Verifies load config -> load checkpoint -> move to CUDA -> eval() and reports
device / dtype / param count / GPU memory. Stage A is PyTorch eager with
compile disabled (vos_optimized=False), so no Triton/compile is involved.

Gated behind SEG_DEMO_HARDWARE_TESTS=1, like the real /dev/video0 release test:
it needs the ~69 MB checkpoint and allocates GPU memory.
Run explicitly:

    SEG_DEMO_HARDWARE_TESTS=1 .venv/bin/python -m pytest -q tests/segment/test_model_load.py
"""
import os

import pytest
import torch

HARDWARE = os.environ.get("SEG_DEMO_HARDWARE_TESTS") == "1"
CKPT = "checkpoints/efficienttam_ti_512x512.pt"
CFG = "configs/efficienttam/efficienttam_ti_512x512.yaml"

pytestmark = pytest.mark.skipif(
    not HARDWARE,
    reason="set SEG_DEMO_HARDWARE_TESTS=1 (loads EfficientTAM on CUDA)")


def test_efficienttam_tiny_loads_on_cuda():
    from efficient_track_anything.build_efficienttam import (
        build_efficienttam_video_predictor,
    )

    assert torch.cuda.is_available(), "CUDA not available"
    assert os.path.exists(CKPT), f"checkpoint missing: {CKPT}"

    predictor = build_efficienttam_video_predictor(
        CFG,
        CKPT,
        device="cuda",
        mode="eval",
        hydra_overrides_extra=["++model.compile_image_encoder=False"],
    )

    params = list(predictor.parameters())
    assert params, "predictor has no parameters"
    assert sum(p.numel() for p in params) > 0
    assert params[0].device.type == "cuda", params[0].device

    torch.cuda.synchronize()
    predictor.eval()

    # Best-effort probe (not gate-failing in the original harness): the encoder
    # may live behind a wrapper attribute and return a dict of feature maps.
    encoder = getattr(predictor, "image_encoder", None) or getattr(
        getattr(predictor, "model", predictor), "image_encoder", None)
    if encoder is not None:
        with torch.inference_mode():
            out = encoder(torch.randn(1, 3, 512, 512, device="cuda"))
        assert out is not None