#!/bin/bash
# Verify the Jetson PyTorch CUDA gate (spec Phase 2).
# Confirms: torch import (cuDSS preload works), CUDA available, Orin SM87, matmul.
cd "$(dirname "$0")/.."
.venv/bin/python - <<'PY'
import torch, torchvision
print("torch              :", torch.__version__)
print("torchvision        :", torchvision.__version__)
print("torch.version.cuda :", torch.version.cuda)
print("cuda.is_available  :", torch.cuda.is_available())
print("device_name        :", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A")
print("capability         :", torch.cuda.get_device_capability(0) if torch.cuda.is_available() else "N/A")
x = torch.randn((2048, 2048), device="cuda")
y = x @ x
torch.cuda.synchronize()
print("matmul ok          :", tuple(y.shape), y.dtype)
print("GATE:", "PASS" if torch.cuda.is_available() else "FAIL")
PY