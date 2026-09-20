# 01 · 现状盘点：仓库与设备能力 / 硬约束基线（带证据）

> 交付物：`staging/forklift_scenario_research/01_capability_baseline.md`
> 作者：repo-scout（t1，只读勘察）
> 勘察时间：2026-09-14 17:25–17:45（设备本地时间）
> 远程仓库：`/home/seeed/workspace/seg_demo` @ 分支 `codex/dual-camera-forklift-upgrade`，HEAD `f9d9af9`
> 设备：`seeed@100.109.1.72`（Tailscale），hostname `seeed`

## 0. 勘察方式、边界与证据标注规则

**只读承诺（已遵守）**：全程未修改/删除远程任何文件、未启动或停止任何服务与摄像头、未 pip 安装、未 `git add`。
仅执行了 `ls / cat / grep / ps / lsof / ss / ip / ping / git log / git status / df / free / nvpmodel -q` 等只读命令，以及 `scp` **从远程拉取**文件到本地快照目录。

**证据标注规则**（本文所有结论均按此标注，无证据者一律写"未验证"）：

| 标注 | 含义 |
|---|---|
| `远程 <路径>:<行号>` | 直接引用远程文件行（本地快照与远程同内容同行号） |
| `命令输出` | 在设备上执行只读命令得到的原文片段 |
| `本地快照 <相对路径>` | `staging/forklift_scenario_research/_remote_evidence/` 下的远程文件副本，供复核 |
| **未验证** | 本次勘察无法证实的事项，不做任何推测 |

**原始证据快照**：`_remote_evidence/docs_remote/`（docs 全量）、`_remote_evidence/code/`（关键代码与配置）、`_remote_evidence/code/git_head_configs/`（工作树已删除配置的 HEAD 版本）。该目录仅供本地复核，**不属于 t6 的 5 个交付文件**。

---

## 0.1 ★ 设备事实核对：仓库 README 描述的不是这台机器（最高优先级约束）

`README.md` 声称的硬件与实测**严重不一致**：README 描述的是**迁移前的源机**，本机是另一台。所有基于 README 的性能数字在本机上**不可直接采信**。

| 项目 | `README.md` 声明 | 本机实测 | 证据 |
|---|---|---|---|
| 板卡 | Seeed reComputer mini / J501 + **AGX Orin 64GB** | **Orin NX 16GB**（Seeed Rugged Orin NX J401） | `cat /proc/device-tree/model` → `NVIDIA Orin NX Developer Kit`；`/etc/nv_tegra_release` → `Seeed Image Name mfi_recomputer-rugged-orin-nx-16g-j401-5.1-35.5.0-2026-04-01.tar.gz`；README 声明见 `远程 README.md:34-49` |
| JetPack / L4T | 6.2.1 / R36.4.4 | **5.1.3 / R35.5.0** | `/etc/nv_tegra_release` → `# R35 (release), REVISION: 5.0`；`dpkg -l \| grep nvidia-jetpack` → `5.1.3-b29` |
| CUDA | 12.6 | **11.4** | `ls /usr/local/` → `cuda-11.4`；`dpkg -l` → `libnvinfer-bin 8.5.2-1+cuda11.4` |
| TensorRT | 10.3 | **8.5.2.2** | `.venv/bin/python -c "import tensorrt;print(trt.__version__)"` → `8.5.2.2` |
| Python | 3.10.12 | **3.8.10** | `.venv/bin/python -V` → `Python 3.8.10` |
| torch | 2.11.0 (jp6/cu126) | **2.1.0a0+41361538.nv23.06** | `.venv/bin/python -c "import torch;print(torch.__version__)"` → `2.1.0a0+41361538.nv23.06`，`cuda True Orin (8,7)` |
| 内存 | 61Gi 统一内存 | **15Gi（15,874,780 kB）** | `free -h` → `Mem: 15Gi`；`grep MemTotal /proc/meminfo` → `15874780 kB` |
| EfficientTAM 在线 FPS | **25.85 FPS**（FP32 / MAXN） | 本机未复现；迁移记录给 **~6–7 FPS fp32** | `远程 README.md:171-179`（源机数据）vs `远程 MIGRATION.md:83` → "源机 AGX Orin 64GB ~25fps；本机 Orin NX 16GB fp32 ~6-7fps" |

**同一台机器的环境已在仓库内被诚实记录**，可作为权威对照：`远程 MIGRATION.md:8-17`（两机环境差异表，内容与上表实测完全一致）。

**由此产生的硬约束（贯穿本报告）**：

- `docs/3d_click_track/phase10*`–`phase12` 的全部性能/稳定性数字都测于 **AGX Orin 64GB / JetPack 6.2.1**：`远程 docs/3d_click_track/phase10_0_audit.md:13` → `| HW/SW | Jetson AGX Orin 64GB · JetPack 6.2.1 · CUDA 12.6 · SM87 |`；`远程 docs/3d_click_track/phase12_report.md:3` → `Date: run on Jetson AGX Orin (JetPack 6.2.1)`。设备上**没有任何 `logs/3d_pipeline/` 或 3D 结果产物**（`ls logs/3d_pipeline` → `No such file or directory`），说明 3D 链路**未在本机跑过**。
- 本机跑过的、可用的性能事实只有三处：点击分割（本节 §1.1）、warn_app 深度 TRT（§1.12）、VLM（§1.14）。

---

## 1. 能力清单

成熟度用四档：**已验收**（有设备上产物/日志/报告，且我能定位到证据）/ **代码就绪未上机**（代码+单测存在，缺真机验收）/ **仅离线或合成验证** / **未验证**。

### 1.1 点击分割 + 流式单目标跟踪（EfficientTAM-Ti @512，bf16）— **已验收（本机）**

| 事实 | 证据 |
|---|---|
| 模型：EfficientTAM-Ti @512×512，官方权重 71,620,052 B | `远程 backend/app/segment/config.py:8-11`；`ls -la backend/app/segment/checkpoints/` → `efficienttam_ti_512x512.pt` |
| 推理配置：`torch.inference_mode()` + **bfloat16 autocast**；`compile_image_encoder=false` | `远程 backend/app/segment/model.py:167,186,196,215`；`远程 backend/app/segment/config.py:13` |
| 帧处理：先降采样到 max side **640**，掩膜多边形输出到 **1280×720** overlay | `远程 backend/app/segment/config.py:18-20` |
| 本机性能：单目标跟踪 **75.7 ms/帧 ≈ 13.2 FPS**，显存 ~0.21 GB，加载 ~5 s（含预热） | `远程 docs/click-segment.md:125-127` |
| 本机离线真模型产物（现有文件，非我运行）：`states.jsonl` 459 帧，稳态 `model_fps ≈ 12.28–12.30`、`infer_ms ≈ 81.3–81.6`（首帧为预热：7.54 fps / 132.6 ms） | `远程 frontend/dist/debug/segment_offline/states.jsonl`（105,922 B，mtime `2026-09-14 15:22`）首行与末行；`.../segment_offline_swap/states.jsonl` 同 |
| 真机实际被点击使用过：HTTP 日志有 `POST /api/segment/click` 200（来自 LAN 浏览器 192.168.6.219） | `logs/studio_server.log` → `POST /api/segment/click ... 200 OK`；`EfficientTAM loaded ... in 1.9s` |
| 前端已构建进产物（Segment 页可达） | `grep -o "api/segment[a-z/]*" frontend/dist/assets/index-B4_PoQq0.js` → `api/segment/{clear,click,start,status,stop}` |

**成熟度**：**已验收**。这是当前唯一"点击即用、已在本机跑通并被真实点击过"的可提示分割能力。

### 1.2 丢失/重现状态机（淡影 ghost + 自动恢复）— **仅离线验收**

- 状态机：`IDLE→TRACKING`；面积比 < `LOST_AREA_RATIO`(0.001) 连续 ≥ `LOST_FRAMES`(6) → `LOST`（保留最后掩膜淡影）；面积比 > `RESUME_AREA_RATIO`(0.002) 连续 ≥ `RESUME_FRAMES`(2) → 自动回到 `TRACKING`。`远程 docs/click-segment.md:67-77`；阈值 `远程 backend/app/segment/config.py:24-27`。
- 离线双场景验收 PASS，**关键证据是设备上现存的逐帧产物**（我按帧号复核，与文档完全一致）：
  - 场景 A（目标重现）：`tracking(1-169) → lost(170-240) → tracking(241-409) → lost(410-459)` — 实测计数 `169 tracking / 71 lost / 169 tracking / 50 lost`。
  - 场景 B（换物体再换回）：`tracking(1-169) → lost(170-330) → tracking(331-409) → lost(410-459)` — 实测计数 `169 / 161 / 79 / 50`；换物体窗口（170–330）**全程 LOST 不误锁**。
  - 证据：`远程 frontend/dist/debug/segment_offline/states.jsonl`、`.../segment_offline_swap/states.jsonl`（各 459 行）；写法约定见 `远程 docs/SEGMENT-LESSONS-LEARNED.md:134-143`。
- 单测：`backend/tests/segment` 63 项；全量 `backend/tests` 297 项。`远程 docs/click-segment.md:115-116`、`远程 docs/SEGMENT-LESSONS-LEARNED.md:130-132`。**本次未运行测试**（只读边界），故"测试全绿"是**引用文档结论**，非本次复现。

**成熟度**：**仅离线验证**。**真机上的"自动再锁定"未单独复验**（真机只留下 1 次 click 记录，无 re-lock 日志）；见 §3。

### 1.3 负样本点精修 + 掩膜质量门 + 身份模板 — 代码就绪

- 右键负样本点、`MAX_POINTS=16`；掩膜质量门 `MASK_QUALITY_POOR_AREA_RATIO`(0.30) / `..._BBOX_RATIO`(0.60) → `target_quality="poor"` 且 `resume_armed=false`。`远程 docs/click-segment.md:46-65`、`远程 backend/app/segment/config.py:29-40`。
- 身份模板：腐蚀 3px、48px 固定尺寸皮尔逊相关、锚定 + 谨慎刷新（≥0.8）。`远程 docs/SEGMENT-LESSONS-LEARNED.md:86-87,104-118`。
- 真机"手持小物体过分割"这一**能力边界**已用现场现象记录：单点掩膜可覆盖近整帧（`mask_area ≈ 0.996`）。`远程 docs/SEGMENT-LESSONS-LEARNED.md:28-41`。

**成熟度**：**代码就绪**（真机精修路径未复验）。

### 1.4 单目度量深度（Depth Anything V2 Metric Indoor Small）— **已验收（本机，走 warn_app 链路）**

- 模型：`checkpoints/depth_anything_v2_metric_indoor_small`；ONNX opset16 → **TensorRT 8.5 FP16** 引擎 `models/tensorrt/depth_metric_small_518_fp16.engine`（51,179,001 B，存在）。`远程 configs/warning.yaml:36-41`；`ls -la models/tensorrt/`。
- 本机实测延迟：TRT fp16（518）**26.5 ms**（trtexec 原生）/ 端到端 ~46 ms；PyTorch fp16 回退 **79 ms**。`远程 docs/warning.md:46-51`、`远程 MIGRATION.md:145-151`。
- **度量深度是学习先验，不是 LiDAR 测量**；无标定时界面标注 `[APPROXIMATE: estimated K]`。`远程 README.md:287-293`。

**成熟度**：**已验收**（warn_app 链路）；作为「3D 点击跟踪」链路的深度来源则**未在本机验证**（见 1.5）。

### 1.5 mask+深度融合（robust sampling）→ 相机系 XYZ — **仅离线验证（源机）**

- 融合：腐蚀 → 有效像素过滤 → 20–80 百分位裁剪 → 中位数；**不使用单像素/bbox 中心深度**。`远程 README.md:260-272`。
- 相机系约定：`+X 右 / +Y 下 / +Z 前`（OpenCV）。`远程 docs/coordinate-systems.md:6-24`。
- 验收：`dog.mp4` XYZ 稳定（30 帧 std 0.04/0.03/0.22 m）、35 m 尖峰被拒、轨迹 deque(maxlen=300)。`远程 docs/3d_click_track/FINAL_ACCEPTANCE.md:12-14`。

**成熟度**：**仅离线验证，且测于 AGX 源机**（`phase10_0_audit.md:13`）→ 在 NX 上属**未验证**。

### 1.6 3D 状态分层 + 物理门控 Kalman（2D/Depth/3D 三层）— 代码就绪（源机验收）

- 三层状态：`2D: TRACKING|LOST` / `Depth: VALID|INVALID` / `3D: UPDATED|PREDICTED|STALE`；显示距离只来自真实测量；`Z<=0` 永不外露；丢失窗口 `max_pred_frames`(3) 后冻结。`远程 README.md:379-404`。
- 物理门：`min_depth_m`(0.3) / `max_depth_m`(10.0)，越界测量视同缺失。`远程 configs/3d_pipeline.yaml:40-50`。
- 源机真机 500 帧 gate 全 PASS（G2 负 Z 显示=0、G6 16.4 FPS P50）。`远程 docs/3d_click_track/phase12_report.md:82-92`。

**成熟度**：**源机已验收，本机未验证**（本机无 3D 运行产物）。

### 1.7 类无关 3D OBB（PCA）单目 3D 框 — 仅合成/离线验证

- 掩膜 → 局部点云 → MAD/百分位离群过滤 → PCA 定向长方体（中心/WHL/yaw）→ 时序稳定（中心 EMA、尺寸限速、yaw 倍角 EMA）。`远程 docs/3d_click_track/phase11_report.md:10-37`。
- 明确定位：**Estimated Monocular 3D Box，非 LiDAR 真值**。`远程 docs/3d_click_track/phase11_report.md:100-105`。

**成熟度**：**仅合成/离线验证（源机）**。

### 1.8 俯视 BEV（轨迹 + 定向占位框）— 仅离线验证

- 纯 cv2 俯视，无神经 BEV；`x_range [-10,10]`、`z_range [0,20]`。`远程 configs/3d_pipeline.yaml:52-56`；`远程 docs/3d_click_track/FINAL_ACCEPTANCE.md:15`。

**成熟度**：**仅离线验证（源机）**。

### 1.9 落货辅助：地面系持久目标区 + ΔX/ΔZ/ΔYaw + PLACEMENT_OK — 仅合成验证

- 关键性质：目标区是**地面系几何**（非像素），货叉/托盘/货物可 100% 遮挡它而计算不中断。`远程 docs/forklift_placement/README.md:64-68`。
- 验收：合成 200 组随机场景 **0 误 PASS / 0 误 FAIL**；核心放置逻辑 **0.94 ms < 2 ms** 预算。`远程 docs/forklift_placement/phase13.2-13.15_reports.md:98-106`、`远程 benchmarks/placement_accuracy.md:6-26`。
- 精度诚实边界：**±10 cm 演示级**，全套标定后才谈 5 cm。`远程 docs/forklift_placement/README.md:85-86`、`远程 benchmarks/placement_accuracy.md:54-57`。
- 真机卷尺矩阵 A–J **仍是空模板**。`远程 benchmarks/placement_accuracy.md:35-56`。

**成熟度**：**仅合成验证**；真机 **未验收**。

### 1.10 前视 AprilTag 定位（地面 tag 0/1/2/3 + 托盘 tag 10）— 代码就绪未上机

- 前摄每帧由地面 tag 做 PnP 解算自身位姿，**不需要手眼/外参标定**，相机可自由移动。`远程 docs/forklift_dual_demo_field_calibration.md:7-13`。
- 地面系：`+X 右 / +Y 上 / +Z 前`，Y=0 为地面；tag 角点序 `[BL,BR,TR,TL]`，页顶朝 +Z。`远程 docs/forklift_dual_demo_field_calibration.md:11-13,46-64`。
- 验收门槛（明确写"未达成前不得宣称"）：P95 平移 ≤ 0.05 m、P95 yaw ≤ 5°、≥10 FPS；负例（遮 tag / 错 ID / 冻结流 / 无人工确认）**必须永不 VERIFIED**。`远程 docs/forklift_dual_demo.md:40-52`、`远程 docs/forklift_dual_demo_field_calibration.md:130-146`。
- 前视完成流程**要求内参已标定**（`calibrated: true` 且运行时保持 2304×1296）。`远程 docs/forklift_dual_demo_field_calibration.md:17-18`。
- **验收表和 30 poses 表均为空模板** → 未验收。

**成熟度**：**代码就绪未上机**。

### 1.11 双相机启动器（前俯视 + 后盲区）— 代码就绪，**当前配置在本机跑不起来**

- 前摄 `app/marker_placement_app.py`（需 `configs/marker_placement.yaml`），后摄 `app/warn_app.py`；双进程 + 合成窗口；一键 `./start_dual_demo.sh`。`远程 scripts/run_two_demos.sh:176-189`、`远程 start_dual_demo.sh:1-4`。
- 前置校验（缺一即 exit 2）：`configs/camera_calibration.yaml` 存在且 `calibrated: true`、`models/tensorrt/yolov8n_person_fp16.engine` 存在、前/后 URL 不同。`远程 scripts/run_two_demos.sh:86-98`。
- **实测阻断点**：`configs/two_cameras.env` 写 `FRONT_IFACE="eth0"`（`远程 configs/two_cameras.env:8-9`），但本机 `eth0 carrier=0 DOWN`；前摄实际路由在 **eth2**（`ip route get 192.168.137.20` → `dev eth2`），后摄在 **eth1**。→ 脚本第 148-161 行的 `ping -I eth0` 预检必然失败并 `exit 1`。
- 两台相机**在线可达**（正面事实）：`ping -c1 192.168.137.20` 与 `192.168.1.10` 均 0% 丢包；`/dev/tcp/<ip>/554` 双通（RTSP 端口开放）。

**成熟度**：**代码就绪未上机**，且**当前 git 工作树缺少 3 个必需配置**（见 §3）。

### 1.12 后视碰撞预警 warn_app（深度 TRT + 地面/ROI/障碍 + 时域 + 风险 + 蜂鸣）— **已验收（本机合成/录像）**

- 链路：`PoE 相机 → 深度(TRT) → 地面/ROI/障碍物 → 时域滤波 → 风险引擎 → 工业 UI`。`远程 README.md:468-469`。
- 状态机：`SAFE → WARNING → DANGER`，带迟滞（WARNING 进 3.0 m 退 3.3 m；DANGER 进 1.5 m 退 1.8 m）；障碍需 3/5 帧连续；测距用障碍区 **10% 百分位**；TTC = d/v（接近速度 > 0.10 m/s）。`远程 docs/warning.md:38-41`。
- **故障可见原则**：相机失联 >5 s / 连续 3 帧深度无效 / 后端故障 → `SYSTEM ERROR`，**绝不显示 SAFE**（fail visible）。`远程 docs/warning.md:43-44`。
- 本机实测：12/12 单测、合成场景全链 gate PASS、端到端 25 帧跑通（DANGER + 蜂鸣）。`远程 MIGRATION.md:147-151`。
- 安全边界（原文）：单目深度在低照度/强反光/透明/黑色物体/镜头污染/强振动下会失效，本系统为**原型/辅助提示**，不能替代驾驶员观察。`远程 docs/warning.md:71-75`。

**成熟度**：**已验收**（合成/录像）；**真机场测（卷尺精度 / Test 1–7）BLOCKED**。`远程 MIGRATION.md:152-153`。

### 1.13 行人检测（YOLOv8n → TensorRT FP16）— **已上机运行**

- 引擎 `models/tensorrt/yolov8n_person_fp16.engine`（8,567,164 B）存在；配置 `confidence 0.45 / iou 0.50 / input 640`；人员阈值 warning 4.0 m / danger 2.0 m。`远程 configs/warning.yaml:103-113`。
- 设备 `events/` 现场事件中包含 person 分类事件（如 `warning_20260911_114832_024 ... person`）。`远程 docs/VLM_V04_SEMANTIC_CORRECTNESS.md:30-35`。
- V0.4.1 起，画面上**只保留 person 检测结果**，VLM 对象标签不再上屏。`远程 configs/vlm.yaml:64-67`。

**成熟度**：**已上机运行**（存在现场事件与配置证据）。

### 1.14 VLM 语义（SmolVLM2-2.2B + llama.cpp）— **已上机运行，但输出被现场判定为不可用**

- 模型 `SmolVLM2-2.2B-Instruct-Q4_K_M`，llama-server 监听 127.0.0.1:8081，`gpu_layers 99`。`远程 configs/vlm.yaml:4-21`。
- 本机实测延迟：全新图 **~1.30–1.37 s**（SigLIP encode 硬件上界，与输入尺寸 256/320/384 无关，`prompt_n=187` 恒定）；相同图缓存命中 386–498 ms；全 app 载荷 **median ~2.6 s**。`远程 docs/VLM_V03_SEMANTIC_CACHE.md:51-94`。
- 输出格式结论：`json` 保持（B 裸 label / C class_id 解码快 ~270–300 ms 但**准确率崩坏**）。`远程 docs/VLM_V03_SEMANTIC_CACHE.md:72-84`。
- **现场结论（重要）**：VLM 对象输出基本无效（误分类 + crop/关联 bug），V0.4.1 起 `show_vlm_object: false`，对象标签永不绘制，VLM 仅后台记录。`远程 docs/VLM_V04_SEMANTIC_CORRECTNESS.md:180-217`。
- 安全铁律：距离/TTC/风险**全部**来自 Depth + Risk Engine，VLM 只提供 `object` + ≤12 词描述，**绝不改写距离与风险**。`远程 docs/WEB_MONITOR.md:69`。

**成熟度**：**已上机运行**；语义可用性=**不可用（现场判定）**。

### 1.15 相机标定（Web Calibration Studio，棋盘格）— **已验收**

- 9×7 印刷方格 = 8×6 内角点；质量门 RMS：<0.5 GOOD / 0.5–0.8 ACCEPTABLE / 0.8–1.0 WARNING / >1.0 POOR。`远程 docs/ARCHITECTURE.md:144-161`。
- 现存标定结果：`configs/camera_calibration.yaml` → `calibrated: true`，`fx 749.53 / fy 747.46 / cx 623.76 / cy 336.04`，`rms_reprojection_error_px 0.3215`，30 views，`board_square_mm 25.0`，`Device: /dev/video25 (1280x720)`。
- 一致性硬要求：运行分辨率必须等于标定分辨率，否则 PnP 结果错误。`远程 docs/coordinate-systems.md:287-309`。

**成熟度**：**已验收**（有结果文件与 RMS）。**但标定档位是 1280×720 的 `/dev/video25`**，与本机现存 `/dev/video0,video1` 及前摄 2304×1296 不一致 → 复用前必须重标（见 §3）。

### 1.16 6D 位姿估计（棋盘格 / AprilTag + ObjectProfile）— 代码就绪

- 位姿源可热切换 `chessboard | marker`。`远程 configs/pose.yaml:10-11`。
- 位姿约定：相机系 OpenCV；内部用旋转矩阵+四元数，显示用 ZYX 内旋欧拉角；平移单位恒为米。`远程 docs/coordinate-systems.md:89-119,140-148`。
- AprilTag tag36h11 角点序经验钉死为 `[BL,BR,TR,TL]`，管线中**不做任何角点重排**。`远程 docs/coordinate-systems.md:401-431`。
- 冻结的实物对象系：Seeed SD 卡工具盒 115×78×28 mm + 8 角点。`远程 docs/coordinate-systems.md:361-399`。
- 门控：`PROFILE_INCOMPLETE` 时生产位姿被阻断。`远程 configs/pose.yaml:25-32`。

**成熟度**：**代码就绪**（有单测目录 `backend/tests/pose/`）。

### 1.17 Web 数据集工具（采集/标注/校验/导出）— 代码就绪

- API：`/api/dataset/*`（new/profiles/list/{id}/capture/images/annotation/split/validate/coverage/export），见 §4.1。
- 覆盖度指引是可建议性的（距离/位置/俯仰/偏航/光照/遮挡等矩阵，`recommended_total: 300`）。`远程 configs/dataset.yaml:13-52`。

**成熟度**：**代码就绪**（本次未运行）。

### 1.18 远程可视化（MJPEG + JSON 状态）— **已上机运行**

- warn_app `--web`：`GET /`（HTML 监控页）、`/health`、`/state`（全量 JSON）、`/stream`（MJPEG）、`/frame`，`POST /api/config`；默认端口 8080，dual 里用 8090。`远程 app/web_stream.py:55-101`、`远程 scripts/run_web.sh:23,57`、`远程 scripts/run_two_demos.sh:181`。
- 数据源约定：距离/TTC/风险来自 Depth + Risk Engine。`远程 docs/WEB_MONITOR.md:59-69`。
- 本机 Studio 亦提供 MJPEG：`GET /api/camera/stream.mjpg`（960×540 @Q75，目标 <100 ms 端到端）。`远程 docs/ARCHITECTURE.md:98-140`。

**成熟度**：**已上机运行**（当前 Studio 正在 8000 端口提供服务）。

---

## 2. 硬约束（不可越界前提）

### 2.1 感知硬件

| # | 约束 | 证据 |
|---|---|---|
| C1 | **只有单目 RGB，无 LiDAR、无深度相机**。所有"距离/3D"都来自单目学习模型。 | 仓库无任何 depth-camera/点云驱动代码；深度来源唯一：`Depth Anything V2 Metric`（`远程 configs/warning.yaml:36-41`、`远程 configs/3d_pipeline.yaml:14-20`） |
| C2 | 单目度量深度是**学习先验**，室内模型在室外场景尺度有偏；无标定即 `APPROXIMATE`。 | `远程 README.md:287-293`、`远程 configs/3d_pipeline.yaml:1-4` |
| C3 | **深度精度无卷尺 GT**，当前不存在可引用的绝对精度数字。 | `远程 benchmarks/depth_accuracy.md:3-4,26-28` → `Status: PENDING live camera ... no GT numbers are fabricated` |
| C4 | 分辨率现状：USB 相机 `/dev/video0,video1`（Studio 用 1280×720 MJPG）；PoE 前/后摄 RTSP 为 2304×1296 @ ~19–20 fps（H.265）。 | `远程 configs/calibration_studio.yaml:3-10`；`远程 MIGRATION.md:106`；`远程 configs/warning.yaml:14-17` |
| C5 | 标定必须与运行分辨率严格一致，否则 PnP/内参全部作废。 | `远程 docs/coordinate-systems.md:287-309` |

### 2.2 摄像头与并发（**最容易踩的约束**）

| # | 约束 | 证据 |
|---|---|---|
| C6 | **全局只有一个 `cv2.VideoCapture`（单例）**：跑 camera 测试或离线 demo 前必须先停 Studio，否则 `CameraBusyError` / GPU 争用。 | `远程 docs/SEGMENT-LESSONS-LEARNED.md:165-166` |
| C7 | **实测正在发生的占用**：Studio 进程 PID 438670 持有 `/dev/video0`（`lsof` 输出 `python 438670 ... 16u CHR 81,0 /dev/video0`），监听 `0.0.0.0:8000`，CPU ~104%，RSS 3.4 GB，已运行 2h07m。 → 现在任何需要 USB 相机的 demo（3D/placement/warning dual）都与它互斥。 | `ps -eo ... \| grep backend.app.main`；`ss -ltnp \| grep 8000`；`lsof /dev/video0` |
| C8 | 点击分割服务**不自己开相机**，只消费 CameraManager 的 latest frame；相机未运行时 `/api/segment/start` 返回 409 `SEGMENT_CAMERA_NOT_RUNNING`。 → Segment 页当前依赖 Studio 的相机存活。 | `远程 docs/click-segment.md:92-104`；`远程 backend/app/segment/api.py:36-46` |
| C9 | 前/后摄是**两条直连 PoE 网口**，且 PoE 由**同一条** `PSE_PWR_EN` 使能（gpiochip2 line15）。 | `远程 configs/two_cameras.env:13-18`、`远程 MIGRATION.md:100-101` |
| C10 | 本机网口现实：仅 `eth1`(192.168.1.1/24)、`eth2`(192.168.1.199/24 + 192.168.137.100/24) 有 carrier；`eth0/eth3/eth4` **DOWN**。前摄路由走 eth2，后摄走 eth1。 → 现有 `two_cameras.env` 的 `FRONT_IFACE=eth0` 是**错的**。 | `ip -br addr`；`for i in eth0..4; cat /sys/class/net/$i/carrier`；`ip route get 192.168.137.20 → dev eth2` |

### 2.3 算力与模型

| # | 约束 | 证据 |
|---|---|---|
| C11 | Orin NX 16GB（15.87 GB 可用），8 核，**当前 MAXN 模式**（`pmode:0000`），CPU 最大 1984 MHz；存储 `/` 116 G 用 34%。 | `free -h`；`nproc`；`nvpmodel -q` → `NV Power Mode: MAXN`；`cat /var/lib/nvpmodel/status` → `pmode:0000`；`df -h` |
| C12 | 点击分割本机实测 **~12–13 FPS / 75–82 ms 每帧**（单目标）。这是**上限**，不是下限。 | `远程 docs/click-segment.md:125-127`；`远程 frontend/dist/debug/segment_offline/states.jsonl`（`model_fps 12.28–12.3`、`infer_ms 81.3–81.6`） |
| C13 | EfficientTAM 用 **bf16 autocast**；`compile_image_encoder=false`（JetPack torch 2.1 在 aarch64 无 triton）。 | `远程 backend/app/segment/model.py:167,186,196,215`；`远程 backend/app/segment/config.py:12-13`；`远程 README.md:203-206` |
| C14 | 模型输入固定 **512×512**；分割前帧降采样到 **max side 640**；overlay **1280×720**。 | `远程 backend/app/segment/config.py:8-20`；`远程 backend/app/segment/model.py:104-109` |
| C15 | **EfficientTAM 未用 TensorRT**（明确的设计决策，非遗漏）。 | `远程 README.md:329` → `TensorRT not used yet (by design, §14); revisit only if live FPS < 10`；`远程 docs/3d_click_track/FINAL_ACCEPTANCE.md:36` |
| C16 | 3D 链路（深度）在源机 AGX 上把能量吃满才 12.3 FPS；本机 **未验证**，且**不得引用源机数字当作本机能力**。 | `远程 docs/3d_click_track/phase10_0_audit.md:13`；`远程 docs/3d_click_track/FINAL_ACCEPTANCE.md:18` |
| C17 | 全栈版本锁死：Python 3.8.10 / torch 2.1.0a0+nv23.06 / TensorRT 8.5.2.2 / 系统 OpenCV 4.5.4（GStreamer YES）/ JetPack 5.1.3。 | 实测命令；`远程 MIGRATION.md:8-17,32-36` |
| C18 | 本机**无公网出口**，联网须经本地代理 `http://10.42.0.1:8888`。 | `远程 MIGRATION.md:38-45` |
| C19 | 内存不可假设宽裕：可用的 9.4 GB 需同时容纳 Studio(RSS 3.4 GB) + 模型 + 前端；且源机曾观察到 stress 模式下 native heap 延迟增长 +30 MB/min（本机未验证）。 | `free -h`；`远程 README.md:314-322` |

### 2.4 语义、合规与安全表述（红线）

| # | 约束 | 证据 |
|---|---|---|
| C20 | 落货辅助 = **Monocular AI Placement Assistance，非安全认证测量系统**；未标定则必须显示 APPROXIMATE。 | `远程 docs/forklift_placement/README.md:7-8` |
| C21 | 双相机演示 = **产品演示，非功能安全或标定过的制动系统**；不负责货叉与托盘孔对齐。 | `远程 docs/forklift_dual_demo.md:3-6` |
| C22 | 后视预警 = **驾驶辅助原型**；感知不可靠必须显示 SYSTEM ERROR，绝不默认 SAFE。 | `远程 docs/warning.md:43-44,71-75` |
| C23 | VLM 语义**不得**改写距离与风险（风险链零改动由 `git diff` 证明）。 | `远程 docs/WEB_MONITOR.md:69`；`远程 docs/VLM_V04_SEMANTIC_CORRECTNESS.md:123` |
| C24 | 目标区必须是**地面系几何**，不得退化为像素检测（前视/落货两处都如此）。 | `远程 docs/forklift_placement/README.md:64-68`；`远程 docs/VLM_V04_SEMANTIC_CORRECTNESS.md` 无关 |
| C25 | 仓库**无任何功能安全认证**，全仓找不到任何功能安全标准编号或等级声明。 | 命令输出：`grep -rIn -E "ISO 3691\|ISO 20898\|ISO 13849\|IEC 61508\|ANSI/ITSDF\|B56\.5\|GB/T\|功能安全\|安全等级\|SIL ?[123]" docs/ configs/ backend/app/ app/ README.md AGENTS.md MIGRATION.md` → **0 命中**；反向命中仅有明确否认声明：`远程 docs/forklift_placement/README.md:7` → `NOT a safety-certified measurement system`、`远程 README.md:439` → `not a safety system` |

### 2.5 工作树状态（影响"现在能不能跑"）

- 分支 `codex/dual-camera-forklift-upgrade`，HEAD `f9d9af9`。
- `git status --porcelain` 共 **59 项**，其中 **3 个必需配置被删除（未暂存）**：`D configs/marker_placement.yaml`、`D configs/placement.yaml`、`D configs/target_zone.yaml`。
  → **前视 dual demo（1.10/1.11）与落货 demo（1.9）当前在磁盘上缺配置，直接跑会失败**（HEAD 版本仍可 `git show HEAD:<path>` 取回，见 `_remote_evidence/code/git_head_configs/`）。
- 未跟踪新增中包含 `app/vlm/`、`backend/app/`（api/calibration/camera/segment/…）、`docs/ARCHITECTURE.md` 等 —— 点击分割与 VLM 子系统**尚未提交进 git**。
- `git log --oneline -15` 顶部 5 条全部是点击分割相关（`f9d9af9 docs(segment): lessons-learned ...` 至 `d9e1026 feat(segment): minimal click-to-segment page (EfficientTAM-Ti)`），说明**点击分割是最近一轮的开发重心**。

**提交卫生约定（必须遵守）**：仓库有大量历史遗留未暂存改动，**只 `git add` 相关路径，绝不 `git add -A`**。`远程 docs/SEGMENT-LESSONS-LEARNED.md:170`。

---

## 3. 已知缺口（诚实清单）

按对后续场景筛选的影响排序。

| # | 缺口 | 级别 | 证据 |
|---|---|---|---|
| G1 | **点击分割只有单目标**。多目标、YOLO 自动检测、保存、视频文件输入在"最小化范围"内被删除。 | 高 | `远程 docs/click-segment.md:136-138` |
| G2 | **多目标身份保持未解决**：外观相似的新物体若相关度 ≥0.6 仍可能被当原目标恢复；旋转/尺度剧变可能不恢复。 | 高 | `远程 docs/click-segment.md:142-144` |
| G3 | **真机自动再锁定未单独复验**：本机只留下 1 次 `/api/segment/click`，没有真机 re-lock 日志；离线双场景 PASS 是合成视频。 | 高 | `logs/studio_server.log`（3 个 POST：start/click/stop 各 1）；`远程 docs/click-segment.md:117-124` |
| G4 | **3D/placement 全链路在本机未复现**：所有 FPS/内存/精度数字来自 AGX 源机；本机无 `logs/3d_pipeline/`、无 3D 结果产物。 | 高 | `远程 docs/3d_click_track/phase10_0_audit.md:13`、`phase12_report.md:3`；`ls logs/3d_pipeline` → not found |
| G5 | **度量深度无卷尺 GT**，绝对精度未知（仅"序关系正确、尺度有偏"）。 | 高 | `远程 benchmarks/depth_accuracy.md:3-13` |
| G6 | **落货真机卷尺矩阵 A–J 未做**（模板全空），合成链路口径为 0 误差，所有真实误差来自感知。 | 高 | `远程 benchmarks/placement_accuracy.md:35-58` |
| G7 | **前视 AprilTag 定位验收表/30 poses 表全空**；P95 ≤5 cm / ≤5° 只是目标值。 | 高 | `远程 docs/forklift_dual_demo_field_calibration.md:130-152`；`远程 docs/forklift_dual_demo.md:40-52` |
| G8 | **不需要 LiDAR/外参的代价是必须放标记**：地面 tag 0–3 + 托盘 tag 10；tag 页朝向错/被遮/错 ID → LOST。 | 中 | `远程 docs/forklift_dual_demo_field_calibration.md:119-139` |
| G9 | **双相机 launcher 网口配置与实际不符**（eth0 应为 eth2），当前一键启动必然失败。 | 高 | `远程 configs/two_cameras.env:8-9` vs `ip route get 192.168.137.20 → dev eth2` |
| G10 | **3 个必需配置被删除**（marker_placement/placement/target_zone），前视与落货 demo 现状不可运行。 | 高 | `git status --porcelain` → `D configs/...` |
| G11 | **多路相机时间同步 / 跨相机目标交接不存在**：全仓 `grep -rIn -iE "cross_camera\|time_sync\|timesync\|handoff"` → **0 命中**；前/后视是两个互不通信的进程（各自独立视频流）。已观测到的对齐问题甚至发生在**单应用内部**：canvas 旧帧 vs `/state` 实时帧错位，且**无 frame_id 无法对账**。 | 中 | 命令输出（0 命中）；`远程 docs/VLM_V04_SEMANTIC_CORRECTNESS.md:26`（MJPEG 旧帧无 frame_id）、`:42`（canvas↔/state 时间错位 + 双测量源）、`:75-78`（V0.4 才补上 `F#<fidx>` 水印与 `/state` 帧号） |
| G12 | **相机互斥**：演示与标定不能同时跑（唯一 VideoCapture）；当前 Studio 已占用 `/dev/video0`。 | 中 | `远程 docs/SEGMENT-LESSONS-LEARNED.md:165-166`；`lsof /dev/video0` |
| G13 | **TensorRT 化 EfficientTAM 未做**（仅在 live FPS < 10 时才考虑；本机 12–13 FPS 刚好在阈值之上）。 | 中 | `远程 README.md:329` |
| G14 | **VLM 语义在当前现场不可用**（误分类 + crop/关联缺陷）；已从 UI 撤下。 | 中 | `远程 docs/VLM_V04_SEMANTIC_CORRECTNESS.md:180-217` |
| G15 | **stress 模式内存增长未根治**（源机 +30 MB/min，需重启 demo 规避）；live 模式 30 min 稳定但只在源机测过。 | 中 | `远程 README.md:314-322` |
| G16 | **标定档位与运行设备/分辨率不匹配**：`camera_calibration.yaml` 是 `/dev/video25 @1280×720`，本机只有 video0/video1，且前摄运行在 2304×1296。 | 中 | `远程 configs/camera_calibration.yaml:3`；`ls /dev/video*`；`远程 docs/forklift_dual_demo_field_calibration.md:18` |
| G17 | **移动叉车 vs 固定世界地图未解决**：MVP 假设一次落货机动内相机↔目标区关系不变；移动载具需要 marker/SLAM/里程计，**明说未解决、不隐藏**。 | 高 | `远程 docs/forklift_placement/README.md:78-82` |
| G18 | **货叉本身不做分割**：货叉仅按配置几何绘制。 | 中 | `远程 docs/forklift_placement/README.md:87` |
| G19 | **夜/低照度、反光、透明/黑色物体、镜头污染、强振动**下单目深度会失效（自陈）。 | 高 | `远程 docs/warning.md:71-75` |
| G20 | **无功能安全认证**，且不得宣称可替代制动/联锁。 | 高 | §2.4 C20–C25 |

**"未验证"清单（明确不做的假设）**：

1. 3D 点击跟踪（深度融合/XYZ/Kalman/OBB/BEV）在本机的 FPS、显存、稳定性 —— **未验证**。
2. 落货辅助在本机的精度 —— **未验证**（真机未做）。
3. 前视 AprilTag 定位精度（P95 ≤5 cm / ≤5°）—— **未验证**。
4. 双相机联动（≥10 FPS 每路、P95 告警 <500 ms、2 小时无内存增长）—— **未验证**。
5. 点击分割在**真实车间**（粉尘/振动/强光/低纹理）下的表现 —— **未验证**（现有真机证据只有 1 次点击 + 合成视频）。
6. 度量深度的绝对误差量级 —— **未验证**（无 GT，禁止编造）。
7. USB 相机当前是否可用：`/dev/video0,video1` 存在且被 Studio 打开，但**我未打开相机取证**（只读边界）；因此"USB 相机出图正常"是**未验证**。

---

## 4. 可复用接口与配置面

### 4.1 HTTP API（Studio：`backend.app.main`，默认 `0.0.0.0:8000`）

来源：代码路由表（`远程 backend/app/main.py:190-199` 注册 6 个 router；前缀见下）——**本次未对其发起任何请求**。

| 前缀 | 端点 |
|---|---|
| `/api` | `GET /api/health`、`GET /api/system/info`、`GET /api/system/openapi`（`远程 backend/app/api/system.py:10,22,35`） |
| `/api/camera` | `GET /devices`、`GET /status`、`POST /start`、`POST /stop`、`POST /restart`、`GET /stream.mjpg`（`远程 backend/app/api/camera.py:21,42,64,99,111,177`） |
| `/api/calibration` | `GET /board`、`POST /session/new`、`GET /session`、`POST /session/square_size`、`POST /capture`、`GET /frames`、`DELETE /frames/{id}`、`POST /frames/{id}/exclude`、`POST /frames/{id}/include`、`POST /run`、`GET /result`、`GET /export/yaml`、`GET /export/json`、`GET /verify/undistort`（`远程 backend/app/api/calibration.py:26,32,42,55,76,164,193,204,215,226,266,287,311,332`） |
| `/api/dataset` | `POST /new`、`GET /profiles`、`GET /list`、`GET /{id}`、`DELETE /{id}`、`POST /{id}/capture`、`GET /{id}/images`、`DELETE /{id}/image/{image_id}`、`GET /{id}/image/{image_id}`、`GET /{id}/annotation/{image_id}`、`PUT /{id}/annotation/{image_id}`、`POST /{id}/split`、`POST /{id}/validate`、`GET /{id}/coverage`、`POST /{id}/export`（`远程 backend/app/api/dataset.py`） |
| `/api/pose` | `GET /status`、`POST /start`、`POST /stop`、`POST /reset`、`GET /latest`、`GET /raw`、`GET /object_profile`、`GET /source`、`POST /source`、`GET|POST /config/marker`、`GET /config`、`POST /recorder/start`、`POST /recorder/stop`、`GET /recorder/status`、`GET /stability`、`GET /events`（`远程 backend/app/pose/api.py`，前缀见 `:36`） |
| `/api/segment` | `GET /status`、`POST /start`、`POST /stop`、`POST /click`、`POST /clear`（`远程 backend/app/segment/api.py:36,41,48,53,64`） |
| 静态 | `GET /`（挂载 `frontend/dist`，存在时重定向 `/index.html`）（`远程 backend/app/main.py:201-211`） |

**Segment API 数据契约**（这是"点击分割"对外最关键的接口面）：

- `POST /api/segment/click` body `{x, y ∈[0,1], label ∈{0,1}}`；无目标→`select`，有目标→`add_point`。`远程 backend/app/segment/api.py:14-17,53-61`。
- `GET /api/segment/status` 返回：状态机状态、多边形、ghost、fps、`mask_area`、`model_fps`、`infer_ms`、`target_quality`、`target_hint`、`relock_note`、`resume_armed`。`远程 docs/click-segment.md:96`；字段实现见 `远程 backend/app/segment/service.py:733-791`。
- 错误码：`SEGMENT_NOT_STARTED / SEGMENT_LOADING / SEGMENT_ERROR / SEGMENT_CAMERA_NOT_RUNNING / SEGMENT_NO_TARGET / SEGMENT_TOO_MANY_POINTS(16) / SEGMENT_INVALID_COORDINATES(422) / SEGMENT_NO_FRAME`（`远程 docs/click-segment.md:102-104`；HTTP 映射 `远程 backend/app/segment/api.py:20-33`，422 仅给 INVALID_COORDINATES，其余 409）。

**warn_app Web API**（默认 8080；dual 用 8090）：`GET /`、`GET /health`、`GET /state`、`GET /stream`、`GET /frame`、`POST /api/config`（`远程 app/web_stream.py:55-101`；端口 `远程 app/warn_app.py:193` 与 `远程 scripts/run_web.sh:23`）。

**其他独立工具端口**：定位标定 Web `8091`（`远程 docs/forklift_dual_demo_field_calibration.md:192`）；相机标定 Web `8090`（`远程 docs/forklift_dual_demo.md:60`，注意与 dual 的 rear web 端口可能冲突）；llama-server `127.0.0.1:8081`（`远程 configs/vlm.yaml:5-6`）。

### 4.2 配置文件与关键字段

| 文件 | 作用 | 关键字段（行号） |
|---|---|---|
| `configs/calibration_studio.yaml` | Studio 相机/流/标定/质量/服务 | `camera.{device,width:1280,height:720,fps:30}`(:3-8)、`stream.preview 960×540 Q75`(:12-19)、`calibration.inner_corners 8×6`(:29-42)、`quality.good_rms_px 0.5`(:44-51)、`server.port 8000`(:58-60) |
| `configs/camera_calibration.yaml` | 内参+畸变+标定质量（**当前 1280×720 / /dev/video25**） | `calibrated: true`(:7)、`fx 749.53/fy 747.46/cx 623.76/cy 336.04`(:9-12)、`rms 0.3215`(:21) |
| `configs/3d_pipeline.yaml` | 3D 点击跟踪 | `efficienttam.image_size 512 / dtype fp32`(:8-9)、`depth.input_longest_edge 518 / frequency 2`(:17-19)、`camera.calibrated false`(:25)、`tracking.min/max_depth_m 0.3/10.0`、`max_pred_frames 3`、`vel_decay 0.5`(:43-46)、`bev.x_range[-10,10]/z_range[0,20]`(:52-56) |
| `configs/warning.yaml`（NX 适配版） | 后视预警全参数 | `pipeline.scale 0.5`(:6)、`camera 2304×1296@20fps`(:13-17)、`camera.calibrated false`(:19)、`depth 0.3–10 m`(:43-45)、`danger_roi`(:59-60)、`warning 3.0/1.5 m`(:94-96)、`person`(:103-113)、`watchdog`(:115-117)、`io.alarm_mode serial`(:128-133) |
| `configs/vlm.yaml` | VLM 语义（llama.cpp） | `model.output_format json`(:21)、`image.max_side 384`(:31)、`semantic.ttl_ms 5000 / pretrigger 5.0m`(:34-43)、`tracker.match`(:50-60)、`ui.show_vlm_object false`(:64-67) |
| `configs/pose.yaml` | 6D 位姿 | `pose_source chessboard|marker`(:10)、`object_profile`(:31)、`marker`(:46)、`validation`(:153)、`quality`(:221)、`recorder`(:252) |
| `configs/two_cameras.env` | 双相机角色绑定（**前摄网口配置有误**） | `FRONT/REAR_CAMERA_URL`(:3-4)、`FRONT_IFACE eth0`(:8) ← 应为 eth2、`POE_GPIO_CHIP 2 / LINE 15`(:14-15) |
| `configs/ip_camera.env` | 单相机一键启动 | `CAM_URL rtsp://admin:***@192.168.137.20:554/`(:6)、`CAM_IFACE eth1`(:10)、`CAM_LOCAL_IP 192.168.137.100/24`(:11) |
| `configs/dataset.yaml` | 数据集覆盖度建议 | `recommended_total 300`(:14) |
| `configs/placement.yaml`（**已删除，HEAD 可取回**） | 落货配置 | `camera_extrinsics.{calibrated:false,height 1.60,pitch 32.0}`(:7-12)、`forklift.fork_tip_z 1.20 / spacing 0.68`(:14-19)、`placement.tolerance_x 0.05 / min_inside_ratio 0.95 / stable_time_s 0.8 / min_stable_frames 10`(:25-35)、`footprint.length_scale`(:43-53) |
| `configs/target_zone.yaml` / `configs/marker_placement.yaml`（**已删除，HEAD 可取回**） | 地面目标区 / 前视 tag 几何 | 见 `_remote_evidence/code/git_head_configs/` |

### 4.3 坐标系约定（唯一真值源：`docs/coordinate-systems.md`）

| 概念 | 约定 | 证据 |
|---|---|---|
| 相机系 | **+X 右 / +Y 下 / +Z 前**（OpenCV，右手系），原点=光心 | `远程 docs/coordinate-systems.md:6-28` |
| 地面/叉车系（落货、前视） | **+X 右 / +Y 上 / +Z 前**，原点=相机在地面的投影，地面 Y=0；放置数学只在 (X,Z) 平面 | `远程 docs/forklift_placement/README.md:10-16`；`远程 docs/forklift_dual_demo_field_calibration.md:11-13` |
| yaw 定义 | 从 +Z（前）转向 +X（右），归一到 (-90°, 90°]（矩形 180° 对称） | `远程 docs/forklift_placement/README.md:14-15` |
| 棋盘格系 | 原点=第一个内角点，+X 列 / +Y 行 / +Z 法向朝观察者 | `远程 docs/coordinate-systems.md:37-71` |
| 内部旋转表示 | 旋转矩阵（主）+ 四元数（滤波）；显示用 ZYX 内旋欧拉角（yaw-pitch-roll，度） | `远程 docs/coordinate-systems.md:87-119` |
| 平移单位 | **恒为米** | `远程 docs/coordinate-systems.md:140-148` |
| AprilTag 角点序 | `[BL, BR, TR, TL]`（pupil_apriltags 顺序），**全链路不做重排**；tag 页顶朝板 +Z | `远程 docs/coordinate-systems.md:419-431`；`远程 docs/forklift_dual_demo_field_calibration.md:52-54` |
| 变换语义 | `T_A_B` 把点从 B 系映到 A 系；对外发布的永远是 `T_camera_object`，不得与 `T_camera_marker` 混用 | `远程 docs/coordinate-systems.md:433-446` |

### 4.4 复用切入点（给后续场景筛选的落点提示）

- **点击分割的复用面**：`POST /api/segment/click` → 掩膜多边形（1280×720，归一化坐标）是**唯一已上机**的语义输出接口；下游任何场景都可以从它取"目标掩膜"。
- **加 3D 的复用面**：`configs/3d_pipeline.yaml` 的 `camera.intrinsics + calibrated` 开关是"APPROXIMATE → 真实度量"的唯一闸门（`远程 README.md:295-299`）。
- **地面系的复用面**：`TargetZoneProvider` 被明确标注为 marker-based zone 的扩展点（`远程 docs/forklift_placement/README.md:80-82`）。
- **不要复用的**：VLM 对象标签（现场不可用，`远程 docs/VLM_V04_SEMANTIC_CORRECTNESS.md:180-217`）。

---

## 5. 本次勘察的命令留痕（可复现）

```text
# 连通性 / 硬件
sshpass -p 'seeed' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 seeed@100.109.1.72 '<cmd>'
  cat /proc/device-tree/model            -> NVIDIA Orin NX Developer Kit
  cat /etc/nv_tegra_release              -> R35.5.0 / mfi_recomputer-rugged-orin-nx-16g-j401-...
  free -h                                -> Mem 15Gi
  nvpmodel -q                            -> NV Power Mode: MAXN
  dpkg -l | grep nvidia-jetpack          -> 5.1.3-b29
  .venv/bin/python -V                    -> Python 3.8.10
  .venv/bin/python -c "import torch"     -> 2.1.0a0+41361538.nv23.06 cuda True Orin (8,7)
  .venv/bin/python -c "import tensorrt"  -> 8.5.2.2

# 仓库状态
  git log --oneline -15 / git status --porcelain / git branch -vv
  git show HEAD:configs/{placement,target_zone,marker_placement}.yaml

# 运行态（只查不动）
  ps -eo pid,ppid,etime,rss,pcpu,args | grep backend.app.main
  ss -ltnp                       -> 0.0.0.0:8000 python pid=438670
  lsof /dev/video0               -> python 438670 16u CHR 81,0
  ls -la /dev/video*

# 网络与相机
  ip -br addr / ip route get 192.168.137.20 / ip route get 192.168.1.10
  ping -c1 -W2 192.168.137.20 / 192.168.1.10      -> 均 0% 丢包
  /dev/tcp/192.168.137.20/554 与 /dev/tcp/192.168.1.10/554 -> 可连接

# 证据产物复核
  ls -la frontend/dist/debug/segment_offline{,/_swap}
  grep -o '"state": "[a-z]*"' frontend/dist/debug/segment_offline/states.jsonl | uniq -c
  grep -o "POST /api/segment/[a-z]*" logs/studio_server.log | sort | uniq -c
```

---

## 6. 给下游（t2/t3/t4）的硬性提醒

1. **不要引用 `README.md` 的性能表（25.85 FPS 等）当作本机能力** —— 那是 AGX 源机（§0.1）。
2. **不要引用 `docs/3d_click_track/*` 与 `docs/forklift_placement/*` 的 FPS/精度数字**当作本机已验证 —— 本机无对应产物（G4/G6）。
3. 任何需要**多路相机同时供电 + 两条独立网口**的场景，必须显式写出 C9/C10 冲突（当前 `two_cameras.env` 网口配置本身是错的）。
4. 任何需要**毫秒级硬实时**或**功能安全等级**的场景，必须显式写"做不到"（C20–C25、G20）。
5. 任何涉及"当前能不能马上演示"的场景，必须考虑 **Studio 正占用 `/dev/video0`**（C7/C12）与 **3 个配置被删除**（G10）。
6. 单目深度相关的一切精度陈述，在卷尺标定完成前一律标注 **APPROXIMATE / 未验证**（C2/C3/G5）。