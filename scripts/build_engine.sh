#!/bin/bash
# Build the Depth-Anything-V2 Metric Small TensorRT FP16 engine.
# Usage: ./scripts/build_engine.sh [longest_edge]
#   default 518 -> input 3x280x518 for a 16:9 2304x1296 camera
#   ./scripts/build_engine.sh 448   (benchmark ladder 518/448/392)
set -e
cd "$(dirname "$0")/.."

LONGEST=${1:-518}
CAM_W=2304
CAM_H=1296
MULT=14
SCALE=$(python3 -c "print($LONGEST/max($CAM_H,$CAM_W))")
IN_W=$(python3 -c "import math; print(max($MULT,(round($CAM_W*$SCALE)//$MULT)*$MULT))")
IN_H=$(python3 -c "import math; print(max($MULT,(round($CAM_H*$SCALE)//$MULT)*$MULT))")

ONNX="models/onnx/depth_metric_small_${LONGEST}.onnx"
[ -f "$ONNX" ] || { echo "missing $ONNX — run scripts/export_depth_onnx.py first"; exit 1; }
ENGINE="models/tensorrt/depth_metric_small_${LONGEST}_fp16.engine"
mkdir -p models/tensorrt

echo "[build] $ONNX ($IN_H x $IN_W) -> $ENGINE"
# NOTE: this ONNX is fully static — do NOT pass min/opt/maxShapes
# (TRT 8.5 rejects explicit shapes for static models: "Static model does
# not take explicit shapes...")
/usr/src/tensorrt/bin/trtexec \
  --onnx="$ONNX" \
  --saveEngine="$ENGINE" \
  --fp16 \
  --workspace=1024 \
  2>&1 | tail -12
echo "[build] done: $ENGINE"