#!/bin/bash
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"
PY=.venv/bin/python
PIP=.venv/bin/pip
mkdir -p models/onnx models/tensorrt checkpoints

# Keep NVIDIA's Jetson PyTorch wheel. --no-deps prevents pip from replacing it
# with an incompatible generic CUDA wheel.
"$PIP" install --no-deps "ultralytics==8.3.40" "ultralytics-thop==2.0.14"
# Versions are pinned to Python 3.8 / JetPack 5.1.3. Do not install pip's
# opencv-python package: the system OpenCV build provides GStreamer support.
"$PIP" install "onnx==1.14.1" "matplotlib==3.7.5" "pandas==2.0.3" \
  "seaborn==0.13.2" "psutil==6.1.1" "py-cpuinfo==9.0.0" "scipy==1.10.1"

weights=checkpoints/yolov8n.pt
if [ ! -s "$weights" ]; then
  asset_url=https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt
  curl -fL --retry 3 -o "$weights" "$asset_url"
fi
onnx_model=models/onnx/yolov8n_person.onnx
if [ ! -s "$onnx_model" ]; then
"$PY" - <<'PY'
from pathlib import Path
from ultralytics import YOLO
model = YOLO("checkpoints/yolov8n.pt")
out = Path(model.export(format="onnx", imgsz=640, opset=12,
                        simplify=False, dynamic=False))
target = Path("models/onnx/yolov8n_person.onnx")
target.write_bytes(out.read_bytes())
print(target)
PY
fi

trtexec_bin=/usr/src/tensorrt/bin/trtexec
[ -x "$trtexec_bin" ] || trtexec_bin="$(command -v trtexec)"
engine=models/tensorrt/yolov8n_person_fp16.engine
if [ ! -s "$engine" ]; then
  "$trtexec_bin" --onnx="$onnx_model" --saveEngine="$engine" \
    --fp16 --workspace=2048
fi
"$PY" - <<'PY'
import numpy as np
from ultralytics import YOLO
if "bool" not in np.__dict__:
    setattr(np, "bool", np.bool_)
model = YOLO("models/tensorrt/yolov8n_person_fp16.engine", task="detect")
model.predict(np.zeros((640, 640, 3), dtype=np.uint8), verbose=False)
print("Person detector ready")
PY
