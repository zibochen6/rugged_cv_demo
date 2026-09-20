# models/ — 推理产物

这里只放**转换/构建产物**，不是权重来源。上游权重在 `checkpoints/`（EfficientTAM、Depth-Anything-V2、
yolov8n），或由脚本从 HuggingFace / ultralytics 拉取。

| 目录 | 内容 | 生成方式 |
| --- | --- | --- |
| `manifests/` | 引擎来源与校验清单（**已入库**，体积很小） | 随代码维护 |
| `onnx/` | 转换中间件：深度、人体、PPE | 一次性转换脚本（已于 2026-09-20 移除） |
| `tensorrt/` | 实际推理使用的 FP16 引擎 | 一次性转换脚本（已于 2026-09-20 移除） |
| `mediapipe/` | `face_landmarker.task`（座舱面部关键点） | 一次性安装脚本（已于 2026-09-20 移除） |

`.gitignore` 忽略 `onnx/`、`tensorrt/`、`mediapipe/`：单个引擎 8–100 MB，且能按 `manifests/` 重新生成（生成脚本
已于 2026-09-20 移除，需要时用 `git show HEAD:scripts/<name>` 取回）；只把 `manifests/` 入库。

**必须在目标设备上生成**：TensorRT 引擎与 GPU 架构、TensorRT 版本绑定，不可跨机或跨 JetPack 复用。
后视底层的 PyTorch eager 后端在引擎缺失时会自动回退（fail-visible，见 `docs/warning.md`）。