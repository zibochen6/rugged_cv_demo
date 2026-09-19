#!/usr/bin/env bash
# Download only manifest-pinned DMS artifacts and build the Jetson-local PPE engine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
PPE_MANIFEST="${ROOT}/models/manifests/ppe_hard_hat.experimental.json"
PPE_ONNX="${ROOT}/models/onnx/ppe_hard_hat.experimental.onnx"
PPE_ENGINE="${ROOT}/models/tensorrt/ppe_hard_hat_fp16.engine"
FACE_MODEL="${ROOT}/models/mediapipe/face_landmarker.task"
mkdir -p "$(dirname "${PPE_ONNX}")" "$(dirname "${PPE_ENGINE}")" "$(dirname "${FACE_MODEL}")"

if [[ ! -x "${PYTHON}" ]]; then
  echo "ERROR: virtual environment is missing: ${PYTHON}" >&2
  exit 2
fi

expected_blob="$(${PYTHON} -c 'import json,sys; print(json.load(open(sys.argv[1]))["github_blob_sha1"])' "${PPE_MANIFEST}")"
actual_blob="$(curl -fsSL --connect-timeout 15 --max-time 120 \
  'https://api.github.com/repos/Sanaurrehmanarain/object-detection-yolov8/contents/best.onnx' \
  | ${PYTHON} -c 'import json,sys; print(json.load(sys.stdin)["sha"])')"
if [[ "${actual_blob}" != "${expected_blob}" ]]; then
  echo "ERROR: PPE upstream blob changed; update and review the manifest before installing." >&2
  exit 3
fi

tmp_ppe="$(mktemp)"
trap 'rm -f "${tmp_ppe}"' EXIT
curl -fsSL --connect-timeout 15 --max-time 180 \
  'https://raw.githubusercontent.com/Sanaurrehmanarain/object-detection-yolov8/main/best.onnx' \
  -o "${tmp_ppe}"
mv "${tmp_ppe}" "${PPE_ONNX}"
trap - EXIT
echo "PPE ONNX installed: $(sha256sum "${PPE_ONNX}")"

face_md5="$(${PYTHON} -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact_md5"])' "${ROOT}/models/manifests/face_landmarker.json")"
tmp_face="$(mktemp)"
trap 'rm -f "${tmp_face}"' EXIT
curl -fsSL --connect-timeout 15 --max-time 120 \
  'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task' \
  -o "${tmp_face}"
[[ "$(md5sum "${tmp_face}" | awk '{print $1}')" == "${face_md5}" ]] || { echo "ERROR: Face Landmarker checksum mismatch" >&2; exit 4; }
mv "${tmp_face}" "${FACE_MODEL}"
trap - EXIT

"${PYTHON}" -m pip install --no-deps 'absl-py' 'attrs>=19.1.0' 'flatbuffers>=2.0' \
  'sounddevice>=0.4.4' 'mediapipe==0.10.5'
"${PYTHON}" -c 'import mediapipe; print("MediaPipe", mediapipe.__version__)'

trtexec_bin="${TRTEXEC:-/usr/src/tensorrt/bin/trtexec}"
[[ -x "${trtexec_bin}" ]] || trtexec_bin="$(command -v trtexec)"
"${trtexec_bin}" --onnx="${PPE_ONNX}" --saveEngine="${PPE_ENGINE}" --fp16 --workspace=1024
echo "DMS models ready. PPE is EXPERIMENTAL: validate with cabin-camera recordings before enabling operational alerts."
