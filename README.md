# Rugged J401 三路视觉总控（Visual Hub）

面向叉车/场内作业车的**三路视觉总控 + 纯录制中心**：前视点击分割、后视单目测距碰撞预警、座舱疲劳与头盔监测，
三路相机角色固定、按需启动、可一键释放。设备侧另有一个同页的桌面全屏入口。

| 项目 | 值 |
| --- | --- |
| 设备 | reComputer Rugged J401（Seeed） |
| 计算 | NVIDIA Jetson Orin NX 16GB |
| 系统 | JetPack 5.1.3 / L4T R35.5.0 / Ubuntu 20.04 |
| 运行时 | Python 3.8.10（项目内 `.venv`）、CUDA 11.4、TensorRT 8.5 |
| PyTorch | `2.1.0a0+41361538.nv23.6`（NVIDIA jp5 redist wheel） |
| 相机 | 2× PoE RTSP（前/后）+ 1× USB 1080P（座舱，固定 `usb:0`） |

**唯一用户入口：**

```text
http://<Jetson 局域网 IP>:8000/
```

> **安全边界（请先读）**：本系统是**驾驶/操作辅助原型**，不是功能安全系统。它不替代驾驶员观察，
> 不承诺"避免碰撞"，不构成任何安全认证，也不得用于合规判定。感知不可靠时必须显示
> `SYSTEM ERROR`，**绝不默认 SAFE**。详见[安全](#安全)。

## 目录

[系统组成](#系统组成) · [功能一览](#功能一览) · [目录结构](#目录结构) · [安装与部署](#安装与部署) ·
[运行与运维](#运行与运维) · [配置](#配置) · [HTTP API](#http-api) · [日志与保留](#日志与保留) ·
[测试](#测试) · [故障排查](#故障排查) · [安全](#安全) · [备份与恢复](#备份与恢复) · [文档](#文档)

## 快速开始

```bash
cd /home/seeed/workspace/seg_demo

sudo ./deploy/install.sh          # 一次性：systemd 单元 / logrotate / 免密 sudo / 桌面图标
sudo systemctl start visual-hub   # 启动总控（开机已 enabled）
# 浏览器打开 http://<Jetson IP>:8000/ → 点"一键全开"

./scripts/run_visual_hub.sh stop  # 停录制 + 停三路推理 + 释放相机/显存/端口
./scripts/run_visual_hub.sh status
```

## 系统组成

| 模块 | 相机 | 进程模型 | 端口 | 能力 |
| --- | --- | --- | --- | --- |
| **前视分割** `front` | `FRONT_CAMERA_URL`（PoE RTSP） | 主进程内 | 公开 **8000** | EfficientTAM 点击分割 + 记忆跟踪 + 目标重现自动重锁 |
| **后视预警** `rear` | `REAR_CAMERA_URL`（PoE RTSP） | 子进程 `app/warn_app.py` | 回环 **8080** | 单目度量深度 + 障碍/人体双通道 + SAFE/WARNING/DANGER/SYSTEM_ERROR |
| **座舱监测** `dms` | USB `usb:0`（固定） | 子进程 `app/dms_app.py` | 回环 **8010** | MediaPipe 面部疲劳 + 头盔佩戴三值判定，两路可独立开关 |

总控本身负责：**相机独占租约**（同一物理设备只允许一个模块持有）、子进程托管与重启、三色灯串口仲裁
（后视 DANGER 优先于座舱疲劳）、热管理降频（88 °C 降档 / 89 °C 暂停头盔并降前视 / 85 °C 以下保持 30 s 再恢复）、
事件总线（内存 500 条环形 + `logs/hub_events.jsonl`）、录制运行时与 MJPEG 代理。

启动时三路**均为空闲**：不占相机、不加载模型、不占显存。相机角色不按发现顺序互换。

## 功能一览

- **一键全开 / 停止全部**：按后视 → 前视 → 座舱启动；停止按座舱 → 前视 → 后视，逐路释放相机、模型、CUDA 与子进程。
  单路失败不会连带关闭已运行的后视。
- **前视交互**：左键选中/细化、右键排除、"清除目标"重选；目标长期丢失后按外观模板验证重锁，换目标会被拒。
- **后视预警**：危险距离必须小于警告距离（UI 强约束）；蜂鸣器开关与服务端值同步；配置持久化后立即生效。
- **座舱监测**：疲劳、头盔两个开关；关闭即真正停止对应推理（不空转、不写误导日志）。
- **显示级全屏**：任一路画面右上角全屏，`Esc` 或返回图标回到总控；浏览器拒绝全屏时自动降级为页内大画面，切换不重启推理。
- **纯录制中心**：顶栏切换，进入前先停全部推理，再按路或一键录制。
- **中英文切换**：顶栏切换，标签页标题同步。
- **Jetson 桌面 GUI**：双击桌面"Visual Hub 总控"打开同一套页面的 Firefox kiosk，带单实例锁与按需拉起服务。

## 目录结构

```text
seg_demo/
├── app/                相机子运行时
│   ├── camera_source.py    统一视频源（usb:/rtsp:/video:/image:/synthetic）
│   ├── web_stream.py       后视/座舱共用的零依赖 MJPEG 服务
│   ├── warn_app.py         后视预警入口（子进程）
│   ├── dms_app.py          座舱监测入口（子进程）
│   ├── warning/            后视：深度后端/滤波/ROI/障碍/时序/风险机/渲染/告警/留存
│   ├── dms/                座舱：相机/疲劳/头盔/PPE/状态/渲染/web
│   ├── geometry/           相机模型、地面平面、像素→XYZ 投影
│   └── depth/              Depth-Anything-V2 度量深度估计
├── backend/            Visual Hub 服务端
│   └── app/
│       ├── main.py          FastAPI 装配（system/camera/hub/recording/segment）
│       ├── config.py        预览流与 HTTP 默认值
│       ├── api/             system · camera(仅 MJPEG 源) · hub · recording
│       ├── hub/             占用租约/子进程托管/事件总线/热管理/三色灯/MJPEG 代理
│       ├── recording/       录制运行时（GStreamer H.264 → MP4）
│       ├── segment/         点击分割服务 + 内置 EfficientTAM 包装
│       ├── camera/          CameraManager（独占、状态机）
│       └── streaming/       MJPEG 编码器
├── frontend/           React 页面：Hub（三路总控）+ Recording（纯录制中心）
├── configs/            dms.yaml · warning.yaml · _runtime_store.py · _runtime_overrides.yaml(运行时)
├── deploy/             系统侧安装物（见"安装与部署"）
├── scripts/            17 个脚本，见下表
├── tests/              单一测试根：hub/ segment/ dms/ warning/ geometry/ + 顶层用例
├── docs/               现行文档；历史阶段资料在 docs/archive/
├── models/             转换产物：onnx/ tensorrt/ mediapipe/ manifests/
├── checkpoints/        上游权重：EfficientTAM、Depth-Anything-V2、yolov8n
├── third_party/        EfficientTAM 上游检出（git-ignored，但 venv egg-link 指向它，勿删）
└── logs/               运行时输出（见"日志与保留"）
```

**脚本分三类（共 17 个）**

| 分类 | 脚本 |
| --- | --- |
| 产品入口（3） | `run_visual_hub.sh`（start/stop/status，systemd `ExecStart` 同一脚本）、`run_visual_hub_gui.sh`（桌面入口）、`gui_display.sh`（物理屏/RDP/Xpra 选屏，被 GUI 入口 source） |
| 现场运维（7） | `install_dms_models.sh`、`setup_person_detector.sh`、`build_engine.sh`、`export_depth_onnx.py`、`download_models.sh`、`download_efficienttam.sh`、`monitor_jetson.sh` |
| 环境与验证（7） | `setup.sh`、`env.sh`、`verify_env.sh`、`segment_offline_demo.py`、`run_dms_demo.sh`、`calibrate_camera_intrinsics.py`、`check_readme_paths.py`（本 README 的引用自检） |

## 安装与部署

### 1. 运行环境

```bash
./scripts/setup.sh          # 幂等：创建/校验 .venv、装依赖、EfficientTAM 可编辑安装、下载权重、CUDA gate
./scripts/verify_env.sh     # 只做 CUDA/torch gate 复检
source scripts/env.sh       # 激活 venv + CUDA 环境
```

`setup.sh` 不会用 PyPI 的 torch 覆盖 Jetson 运行时，也不会替换系统 OpenCV（本机用系统
`python3-opencv 4.5.4`，带 GStreamer）。JetPack 6 → 5 的完整适配记录在 `docs/archive/MIGRATION.md`。

### 2. 模型与权重

```bash
./scripts/install_dms_models.sh      # 座舱：MediaPipe Face Landmarker + PPE 引擎（按 manifest 校验）
./scripts/setup_person_detector.sh   # 人体检测：YOLOv8n → models/onnx + models/tensorrt
./scripts/build_engine.sh 518        # 后视深度：Depth-Anything-V2 → TensorRT FP16 引擎
```

| 位置 | 内容 |
| --- | --- |
| `checkpoints/` | 上游权重：`efficienttam_ti_512x512.pt`、`depth_anything_v2_metric_indoor_small/`、`yolov8n.pt` |
| `models/onnx/` | 转换中间件：深度、人体、PPE |
| `models/tensorrt/` | 实际推理用 FP16 引擎（**后视/座舱都优先走引擎**） |
| `models/mediapipe/` | `face_landmarker.task` |
| `models/manifests/` | 引擎来源与校验清单 |

### 3. 系统侧安装（`deploy/install.sh`）

仓库可克隆到任意位置；`install.sh` 会把参考部署路径 `/home/seeed/workspace/seg_demo` 与用户 `seeed`
替换成当前 checkout 的实际路径与 `$HUB_USER`：

```bash
git clone https://github.com/zibochen6/rugged_cv_demo.git /home/seeed/workspace/seg_demo
```


> **路径无关**：`visual-hub.service`、`visual-hub.logrotate`、`visual-hub.desktop` 里写的是参考部署
> 路径与用户；`install.sh` 安装时统一改写，因此克隆到别的目录（例如 `/opt/rugged_cv_demo`）也能直接安装。

幂等、可预览、不启停服务、不装软件包：

```bash
sudo ./deploy/install.sh --dry-run     # 预览
sudo ./deploy/install.sh               # 安装/刷新
```

| 安装到 | 来源 | 说明 |
| --- | --- | --- |
| `/etc/systemd/system/visual-hub.service` | `deploy/visual-hub.service` | `User=seeed`、`KillMode=control-group`、`TimeoutStopSec=45`；仓库路径按当前 checkout 改写 |
| `/etc/systemd/system/poe-pse.service` | `deploy/poe-pse.service` | PoE PSE 电源保持（含排序环说明，**勿加** `After=multi-user.target`） |
| `/etc/systemd/system/poe-cam-net.service` | `deploy/poe-cam-net.service` | 相机网口与子网，`TimeoutStartSec=300` 的 oneshot |
| `/usr/local/bin/poe-cam-up.sh` | `deploy/poe-cam-up.sh` | 上面 oneshot 的实际脚本 |
| `/etc/logrotate.d/visual-hub` | `deploy/visual-hub.logrotate` | 按天/20 MB 轮转压缩；日志目录按当前 checkout 改写 |
| `/etc/sudoers.d/seeed-nopasswd` | `deploy/seeed-nopasswd.sudoers` | 安装前 `visudo -c -f` 校验，整体校验失败自动移除 |
| `/etc/seg-demo/visual-hub.env` | `deploy/visual-hub.env.example` | **仅当缺失时**用模板生成，之后不再覆盖 |
| `~/Desktop/visual-hub.desktop` | `deploy/visual-hub.desktop` | 按真实仓库路径重写 `Exec`，并标记 trusted |

### 4. 受保护的环境文件

真实 RTSP 凭据只放 `/etc/seg-demo/visual-hub.env`（`root:root 0600`）：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `FRONT_CAMERA_URL` | ✅ | 前摄 RTSP URL |
| `REAR_CAMERA_URL` | ✅ | 后摄 RTSP URL |
| `DMS_CAMERA` | — | 固定 `usb:0`；写成别的值总控会拒绝启动 |
| `HUB_PORT` | — | 默认 `8000` |
| `SEG_DEMO_RUNTIME_OVERRIDES` | — | 后视配置持久化路径，默认 `configs/_runtime_overrides.yaml` |
| `VISUAL_HUB_RECORDING_ROOT` | — | 录像根目录，默认 `~/Videos/visual-hub` |
| `FRONT_PORT` / `WEB_PORT` / `DMS_PORT` / `HUB_PYTHON` | — | 端口与解释器覆盖（一般不动） |

网页、公开状态 API、日志与子进程命令行只显示"PoE 前摄 / PoE 后摄 / USB 座舱"，从不回显完整 URL。

## 运行与运维

### 启动 / 停止 / 状态

```bash
sudo systemctl start|stop|restart visual-hub
sudo systemctl status visual-hub
journalctl -u visual-hub -n 200 --no-pager

./scripts/run_visual_hub.sh           # 前台启动（systemd ExecStart 用同一脚本）
./scripts/run_visual_hub.sh stop      # 停录制 → 停三路 → 结束总控 → 校验端口/子进程释放
./scripts/run_visual_hub.sh stop --poe# 再切掉 PoE 供电与子网配置
./scripts/run_visual_hub.sh status    # 只读状态
```

`stop` 幂等、免密，重复执行返回 0 并提示已是停止状态；主路径走
`POST /api/recording/actions/stop-all` → `POST /api/hub/actions/stop-all` → `systemctl stop`，
失败才降级为对总控进程 `SIGTERM`。`--poe` 会断相机电源，下次启动需等 `poe-cam-net` 重新探测（最长 300 s）。

### 开机自启

三个 unit 都是 `enabled`。**注意**：`poe-pse.service` 曾写成
`After=multi-user.target`，与自身 `WantedBy=multi-user.target` 构成排序环
（`multi-user.target → visual-hub → poe-pse → multi-user.target`），systemd 的处理方式是
**删除 `visual-hub` 与 `poe-cam-net` 的开机启动任务**——即两者此前从不自启。该行已移除。

已实测（2026-09-19 15:01 重启）：本次启动日志 `ordering cycle` **0 行**，
`visual-hub`/`poe-pse`/`poe-cam-net` 全部自动启动、`/api/health` 正常、三路保持 idle。
`deploy/install.sh` 会在安装时检查该行有没有被重新加回来。

### Jetson 桌面 GUI

连接显示器后双击桌面"Visual Hub 总控"→ 免密拉起服务 → Firefox kiosk 打开同一套页面。
单实例锁保证重复点击只提示；服务未运行时用 `sudo -n systemctl start --no-block` 启动，
随后轮询 `/api/health`（≤300 s），因此慢速 PoE 探测不会冻住桌面入口。手工启动：

```bash
./scripts/run_visual_hub_gui.sh
```

### 日常操作

- 关闭浏览器不会停止安全功能；需要释放资源必须显式"停止全部"或跑 `stop`。
- 后视配置改动即时生效并写入 `configs/_runtime_overrides.yaml`（`configs/warning.yaml` 永不被覆写）。
- 单路失败先看状态卡片里的 `last_error` 与 `logs/hub_<module>.log`。

## 配置

### 后视 `configs/warning.yaml`

顶层段：`pipeline` `system` `camera`（含 `intrinsics` / `calibrated`）`model` `depth` `camera_mount`
`danger_roi` `collision_corridor` `ground_filter` `obstacle` `distance` `temporal` `ttc` `velocity`
`warning`（危险/警告距离与迟滞）`person`（人体通道与距离阈值）`watchdog` `logger` `events` `io`（报警方式）。

- 状态机 `SAFE → WARNING → DANGER`，进出阈值带迟滞；障碍需连续多帧确认；测距取障碍区域 10% 百分位。
- 相机失联、连续无效深度、推理后端故障 → `SYSTEM ERROR`（fail-visible）。
- `camera.calibrated: false` 时使用 60° 水平 FOV 估算，距离只具备序关系意义；
  内参标定用 `./scripts/calibrate_camera_intrinsics.py --source usb:0` 生成后手工填入。
- 详见 [docs/warning.md](docs/warning.md)。

### 座舱 `configs/dms.yaml`

顶层段：`system` `camera` `runtime` `fatigue` `helmet` `web` `ui` `logger` `notices`。
疲劳与头盔可独立开关，关闭后不加载对应模型、不写误导日志。详见
[docs/dms_helmet_demo.md](docs/dms_helmet_demo.md)（含诚实口径）。

### 运行时覆盖

`configs/_runtime_overrides.yaml` 由网页写入（`_runtime_store.py` 原子替换），只保存用户可见开关，
例如：

```yaml
runtime:
  danger_m: 1.3
  warning_m: 1.7
  buzzer: true
```

## 纯录制中心

顶栏切换到 **Recording Center**：进入时先停止全部推理，随后按路或一键录制；只保存原始画面，
不加载模型、不写叠加层、无音频。

- 出流：GStreamer `nvv4l2h264enc` → H.264 MP4，**1920×1080 @ 15 FPS**，单段最长 **30 分钟**。
- 存储：`~/Videos/visual-hub/<camera>/<日期>/`（可用 `VISUAL_HUB_RECORDING_ROOT` 改到大盘）。
- 录制中可实时预览与全屏；关闭网页不会停止录制；写盘用 `.part.mp4` 临时名，正常停止后改名，异常中断的残留会被收尾。
- 录制进行时不能回到推理模式；先"停止全部录制"，再点 "Return to Live Inference"。
- 服务退出会关闭全部录像文件并释放两路 PoE 拉流与 `/dev/video0`。

## HTTP API

`{module_id}` ∈ `front|rear|dms`；`{camera}` 同集合。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（前端与 GUI 用它判定服务可用） |
| GET | `/api/system/info` `/api/system/openapi` | 环境与 OpenCV 能力 |
| GET | `/api/hub/status` | 总控全量快照：三路状态/指标、占用、事件、热策略、录制、三色灯 |
| GET | `/api/hub/events` | 事件总线 |
| GET | `/api/hub/modules/{module_id}` | 单路状态 |
| POST | `/api/hub/modules/{module_id}/start` `stop` `restart` | 逐路启停 |
| POST | `/api/hub/actions/start-all` `stop-all` | 一键全开 / 停止全部 |
| POST | `/api/hub/modules/{rear,dms}/config` | 后视距离/蜂鸣器、座舱开关 |
| GET | `/api/hub/stream/{module_id}` | 三路 MJPEG（浏览器唯一取流入口） |
| GET | `/api/camera/stream.mjpg` | 前视原始 MJPEG（上一条 front 的源） |
| GET | `/api/segment/status` | 前视分割状态 |
| POST | `/api/segment/start` `stop` `click` `clear` | 分割启停、正/负点选、清除目标 |
| GET | `/api/recording/status` | 录制模式与三路状态、存储余量 |
| POST | `/api/recording/mode/enter` `exit` | 进入/退出录制模式 |
| POST | `/api/recording/cameras/{camera}/start` `stop` | 单路录制 |
| POST | `/api/recording/actions/start-all` `stop-all` | 一键录制 / 停止录制 |
| GET | `/api/recording/stream/{camera}` | 录制预览 |
| GET | `/api/recording/files` | 录像列表 |
| GET | `/api/recording/files/{id}/play` `download` | 播放 / 下载 |

后视配置示例：`{"danger_m": 1.5, "warning_m": 3.0, "buzzer": false}`

## 日志与保留

`logs/` 只放运行时输出，三层保留策略：

| 文件 | 写入方 | 保留 |
| --- | --- | --- |
| `danger_*.jpg` | 后视 DANGER 截图 | 代码内裁剪：最新 200 张且 ≤14 天 |
| `session_*.csv` | 后视逐帧遥测（每次运行一个文件） | 代码内裁剪：最新 40 个且 ≤14 天 |
| `hub_*.log`、`hub_events.jsonl`、`dms_events.jsonl` | 总控/子进程 | logrotate：按天 / 20 MB，压缩保留 7 份 |
| `tegrastats.log` | `scripts/monitor_jetson.sh` | 手动采样，自行管理 |

`logrotate` 缺失时后两类会无界增长，安装后确认：

```bash
command -v logrotate || sudo apt-get install -y logrotate
sudo ./deploy/install.sh
systemctl list-timers | grep logrotate
```

## 测试

```bash
.venv/bin/python -m pytest -q          # 单一测试根 tests/（pytest.ini 已配置）
cd frontend && npm run build
```

当前：**254 passed, 2 skipped**。需要真实硬件的用例统一由 `SEG_DEMO_HARDWARE_TESTS=1` 门控
（默认 skipped，可见不隐藏）：

```bash
SEG_DEMO_HARDWARE_TESTS=1 .venv/bin/python -m pytest -q tests/test_camera_manager.py \
    tests/segment/test_model_load.py
```

改完 README 或移动文件后，顺手校验文档引用没有指向不存在的路径：

```bash
.venv/bin/python scripts/check_readme_paths.py     # 0 = 所有引用都能解析
```

| 目录 | 覆盖 |
| --- | --- |
| `tests/hub/` | 运行时托管、录制运行时、热管理、三色灯、座舱事件 |
| `tests/segment/` | 分割服务/状态机/多边形/模板/模型加载（硬件门控） |
| `tests/dms/` | 疲劳信号与状态机、头盔启发式、PPE、渲染/HUD、开关、web 契约 |
| `tests/warning/` | 风险核心、合成场景 gate、人体通道、热降频、留存裁剪 |
| `tests/geometry/` | 相机模型与像素→XYZ 投影 |
| 顶层 | `CameraManager` 生命周期、警告核心、合成场景 |

## 故障排查

| 现象 | 检查 | 处置 |
| --- | --- | --- |
| 双击桌面图标弹密码框 | `/etc/sudoers.d/seeed-nopasswd` 是否存在 | `sudo ./deploy/install.sh` |
| 开机后 8000 不通、服务 inactive | `journalctl -b \| grep "ordering cycle"`、`systemctl is-enabled visual-hub` | `sudo ./deploy/install.sh`（脚本会检查并报告排序环） |
| 某一路报"相机被占用" | `curl -s :8000/api/hub/status` 的 `occupancy` | 先停占用方；USB 用 `fuser -v /dev/video0` |
| 前视有画面、后视/座舱无画面 | `ss -ltnp \| grep -E ':8080\|:8010'` | 8080/8010 只回环，必须经 `/api/hub/stream/*` |
| 前视启动后约 8 s 才出画面 | `journalctl -u visual-hub` 里的 model load 行 | 首次加载 EfficientTAM 权重，属预期 |
| 两路 PoE 同时掉线/反复重启 | 共享 PoE 功率预算 | 至少一路改外置 PoE 交换机/注入器（无限 RTSP 重试救不了 brownout） |
| `*_events.jsonl` 一直变大 | `systemctl list-timers \| grep logrotate` | 装 logrotate 后重跑 `deploy/install.sh` |
| 状态下 `signal_light.last_error` 报串口 `FileNotFoundError` | 是否接了串口三色灯 | 未接属预期；可改 `configs/warning.yaml: io.alarm_mode` |
| 座舱夜间/强逆光不可用 | 是否近红外相机 | 无近红外相机时RGB面部关键点失效，属已知限制 |
| 停止后仍占用相机 | `./scripts/run_visual_hub.sh stop` 输出、`pgrep -af 'warn_app\|dms_app'` | 等 45 s（`TimeoutStopSec`）；仍有残留看 journal |

```bash
curl -s http://127.0.0.1:8000/api/hub/status          # 总控与逐路状态
ss -ltnp | grep -E ':8000|:8010|:8080'                # 只应公开 8000
fuser -v /dev/video0                                  # USB 停止后不应有占用者
ps -ef | grep -E 'warn_app|dms_app|backend.app.main'
./scripts/monitor_jetson.sh                           # 采样到 logs/tegrastats.log
```

## 安全

**凭据**：真实 RTSP 凭据只在 `/etc/seg-demo/visual-hub.env`（`root:root 0600`），模板见
`deploy/visual-hub.env.example`。不要提交真实凭据；脚本里也不要写 `SUDO_PASS=...` 或
`echo <密码> | sudo -S`。

**免密 sudo**：本机安装了 `deploy/seeed-nopasswd.sudoers` → `/etc/sudoers.d/seeed-nopasswd`，
`seeed` 拥有**完全免密 sudo**（为桌面图标免输密码而按操作方要求启用）。

```bash
sudo rm -f /etc/sudoers.d/seeed-nopasswd      # 卸载（GUI 退回 pkexec 密码框）
```

含义：任何以 `seeed` 身份运行的进程都能无密码取得 root。请把设备限制在可信局域网并控制物理接入；
若 sudoers 写坏，用串口/单用户控制台或 `pkexec visudo` 恢复。

**感知边界（诚实口径）**：

- **后视预警**：单目 RGB 学习式度量深度，在低照度、强反光、透明物体、黑色物体、镜头污染、强振动下会失效。
  仅为驾驶辅助提示；不可靠状态必须 `SYSTEM ERROR`，绝不默认 SAFE。
- **座舱监测**：疲劳 = MediaPipe Face Landmarker + 固定时间规则，**未做** PERCLOS 个体标定；
  头盔 = 人体框→头部 ROI 的颜色/肤色/暗区启发式，按人输出"佩戴/未佩戴/未知"三值，不输出百分比结论。
  两者均为**演示级**，不构成安全认证，不得用于合规判定。
- **前视分割**：交互式跟踪工具（点击选中、负点排除、重现重锁），不是自动目标识别，不产生安全结论。

## 备份与恢复

每次大规模变更前，恢复包保存在项目外：

```text
/home/seeed/workspace/.seg_demo_backups/<时间戳>/
```

包含 Git 状态、tracked 工作树补丁、未跟踪源码压缩包、分阶段删除清单、前后对比报告与 `SHA256SUMS`。
恢复前先停 `visual-hub`，按清单还原；**不要**使用会覆盖当前工作树的强制 reset。

## 文档

现行：

| 文档 | 内容 |
| --- | --- |
| [docs/warning.md](docs/warning.md) | 后视测距预警：状态机、性能、标定、测试 |
| [docs/dms_helmet_demo.md](docs/dms_helmet_demo.md) | 座舱疲劳/头盔：契约、开关语义、诚实口径、验收 |
| [docs/click-segment.md](docs/click-segment.md) | 前视点击分割：接口与用法 |
| [docs/SEGMENT-LESSONS-LEARNED.md](docs/SEGMENT-LESSONS-LEARNED.md) | 目标重现/重锁的排查与检查单 |
| [docs/requirements/visual-hub-gui-fullscreen/](docs/requirements/visual-hub-gui-fullscreen/requirements-contract.md) | 桌面 GUI 与显示级全屏需求契约（frozen） |
| [docs/forklift_scenario_research/](docs/forklift_scenario_research/README.md) | 场景调研（历史快照，路径多为旧结构） |

历史阶段资料（标定工坊、6D 位姿/Marker、3D 点击跟踪、数据集工具、落货放置、双机迁移）在
`docs/archive/`，其中路径与命令大多已失效，仅作追溯。

## 已知限制

- 前视分割首次加载约 7–8 s；单目标跟踪，多目标需清空重选。
- 座舱疲劳无个体标定、无近红外能力；头盔为三态启发式。
- 后视/前视相机共用 Rugged J401 的 PoE 功率预算，同时掉线时优先怀疑供电而非软件。
- `poe-cam-net.service` 的探测最长 300 s，探测期间总控照常可用（已解耦，不再排队等待）。
- 仓库尚未提交：当前工作树有大量新增/删除，请自行决定提交策略。