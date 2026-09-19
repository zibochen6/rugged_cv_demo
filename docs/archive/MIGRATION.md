# 迁移记录 — seg_demo @ Orin NX (10.42.0.200)

从源机 `seeed@192.168.100.164`（AGX Orin 64GB, JetPack 6.2.1）迁移到本机
`seeed@10.42.0.200`（Orin NX 16GB, JetPack 5.1.3）+ IP 摄像头（RTSP）适配。

## 1. 两机环境差异

| | 源机 192.168.100.164 | 本机 10.42.0.200 |
|---|---|---|
| 模块 | AGX Orin 64GB | Orin NX 16GB (MAXN) |
| JetPack | 6.2.1 (L4T R36.4.4) | 5.1.3 (L4T R35.5.0) |
| CUDA | 12.6 | 11.4 |
| TensorRT | 10.3 | 8.5.2 |
| Python | 3.10.12 | 3.8.10 |
| torch | 2.11.0 (jetson-ai-lab jp6/cu126) | 2.1.0a0+41361538.nv23.06 (NVIDIA redist jp5/v512) |
| torchvision | 0.26.0 | 0.16.2（源码编译） |
| OpenCV | venv opencv-python | 系统 python3-opencv 4.5.4（带 GStreamer！）复制进 venv |

关键适配点（都是 JetPack 6→5 的版本跨度造成）：
- torch：`pypi.jetson-ai-lab.io` 已无 jp5 索引；改用 NVIDIA 官方 redist wheel
  （与 Seeed 教程 reComputer-Jetson-for-Beginners 3.5 同源）。
- torchvision：无 cp38 预编译 wheel（教程的 nvidia.box 链接已 404，教程本身也
  指导此情形用源码编译）；已从源码编译，C ops（nms/box_area）GPU 验证通过。
- OpenCV：pip 的 opencv-python 不带 GStreamer，无法硬件解码 RTSP；改用系统的
  python3-opencv 4.5.4（GStreamer YES），拷贝 `/usr/lib/python3.8/dist-packages/cv2`
  进 venv site-packages。
- Python 3.8：EfficientTAM setup.py 声明 `>=3.10`，实际代码 3.8 兼容，安装时加
  `--ignore-requires-python` 即可。
- cuDSS 修复（源机的步骤 3）不需要：torch 2.1 不链接 cuDSS。
- 新增系统包：`python3.8-venv`、`libopenblas-base`（torch 依赖）。

## 2. 环境（/home/seeed/workspace/seg_demo/.venv）

torch 2.1.0a0+nv23.06 / torchvision 0.16.2 / numpy 1.24.4 / hydra-core 1.3.6 /
iopath / opencv(系统 4.5.4+GStreamer) / tqdm / pillow / huggingface-hub / socksio；
EfficientTAM 以 `-e third_party/EfficientTAM`（BUILD_CUDA=0, --no-deps）安装。

## 3. 网络与上网方式

本机无公网出口，上网经 10.42.0.1（本地 PC 共享网口）上的用户态 HTTP 代理：
```
export http_proxy=http://10.42.0.1:8888 https_proxy=http://10.42.0.1:8888
```
（代理为本地 PC 上的 `python3 fwd_proxy.py 8888`，pip/curl 均可用；apt 用
`-o Acquire::http::Proxy=...`。若换网络环境请删除这些代理设置。）

## 4. IP 摄像头（RTSP，硬件解码）

`app/camera_demo.py` 已支持 `rtsp:` 源 + 断线自动重连：

```bash
./scripts/run_camera.sh --source rtsp://user:pass@<cam_ip>:554/stream1 --show
# 或用环境变量：
SEG_DEMO_RTSP_URL=rtsp://<cam_ip>:554/stream1 ./scripts/run_camera.sh --source rtsp --show
```

- 管线：`rtspsrc(latency=200, tcp) ! rtph264depay ! h264parse ! nvv4l2decoder
  ! nvvidconv ! BGRx ! videoconvert ! BGR ! appsink(drop=1)` → Jetson 硬件解码，
  先试 H.264，失败自动换 H.265。
- 代码修复：`camera_demo.py` 在 import torch 前预加载 `libGLdispatch.so.0`，
  否则 nvvidconv/nvvideo4linux2 插件因 static TLS 耗尽加载失败（报
  "cannot allocate memory in static TLS block"），拉流永久阻塞。
- 流断开时 capture 线程自动重连（2s 间隔），已实测杀流→重连→恢复。
- 找摄像头：`./scripts/discover_ip_camera.sh`（扫所有网口的 /24，报 554/8899
  端口设备；加参数 `probe` 会用 gst-launch 试常见 RTSP 路径）。

⚠️ 截至 2026-09-04，10.42.0.0/24 与 192.168.100.0/24 均未发现 RTSP/ONVIF 设备
（ONVIF WS-Discovery 无响应，其余网口无链路）。摄像头接上后运行探测脚本，
把 URL 填进 run 命令即可。**验证时用 MediaMTX+ffmpeg 在 10.42.0.1:8554 模拟了
RTSP 摄像头（H.264 720p），全链路 GATE PASS（7.4fps 在线跟踪）**。

## 5. 验证结果（本机）

| 测试 | 结果 |
|---|---|
| torch CUDA matmul | ✅ |
| torchvision C ops (nms/box_area GPU) | ✅ |
| 模型加载（efficienttam_ti_512x512.pt） | ✅ 2.1s, 0.18GB |
| video gate (cups.mp4, 80帧, click 384,251) | ✅ GATE PASS, 6.3fps |
| RTSP→硬解→跟踪 (模拟摄像头, 100帧) | ✅ GATE PASS, 7.4fps, mask 25/25 |
| 断流重连 | ✅ 杀流后 #1/#2 重连成功恢复 |

参考性能：源机 AGX Orin 64GB ~25fps；本机 Orin NX 16GB fp32 ~6-7fps。
可试 `--dtype fp16` 提速（推理链上 torch 2.1/Orin NX 均支持）。

## 6. 常用命令

```bash
cd /home/seeed/workspace/seg_demo
./scripts/discover_ip_camera.sh probe                # 找 IP 摄像头
./scripts/run_camera.sh --source rtsp://IP:554/PATH --show   # RTSP 摄像头跟踪
./scripts/run_camera.sh --source usb:0 --show       # USB 摄像头（原有功能）
.venv/bin/python -u app/camera_demo.py --source video:third_party/EfficientTAM/examples/videos/cups.mp4 --click 384,251 --max-frames 80   # headless gate
```

注意：demo 用 `os._exit(0)` 结束，务必带 `-u`（run_camera.sh 已内置）才能看到输出。

## 7. 实机 IP 摄像头验证（2026-09-04，串口调试）

网线从 eth4 改接到了 **eth1**，直连 IP 摄像头（PoE 由本机 PSE_PWR_EN 供电，
gpiochip2 line15，已用 `setsid gpioset -m signal 2 15=1` 常驻拉高）。

- 摄像头实际 IP：**192.168.137.20**（机身标注 192.168.1.10 与实际不符；
  通过 eth1 嗅探 ARP 发现，MAC 00:12:34:be:7c:a6）
- 认证：admin/admin；RTSP：`rtsp://admin:admin@192.168.137.20:554/`
- 编码 **H.265**，2304x1296 @ ~19fps（camera_demo 会自动回退 h265 管线）
- 网络配置（eth1 直连，经串口）：
  ```
  sudo ip addr flush dev eth1
  sudo ip addr add 192.168.137.100/24 dev eth1
  sudo ip link set eth1 up
  ```
- 已验证：gst-launch 硬解 10s 出 192 帧；camera_demo 全链路 mask_present
  18/18、6fps、GPU 0.27GB；画面经串口 base64 传回确认非黑屏。
  （GATE=FAIL 仅因镜头对着静止场景 move=0.6px，低于 20px 阈值——该门槛是
  视频回归测试用的，静态场景属预期。）
- 运行：`./scripts/run_camera.sh --source rtsp://admin:admin@192.168.137.20:554/ --show`

调试通道备忘：本机 USB 串口 /dev/ttyACM0 115200 需先拉 DTR/RTS 才有输出；
串口会话用 `setsid` 常驻保持 PoE 供电。

### 一键启动脚本（2026-09-04 追加）

`./scripts/start_camera.sh`：把 PoE 拉高 + eth1 网络 + 摄像头探测 + 显示器
demo 启动全部包进一条命令：

    cd /home/seeed/workspace/seg_demo
    ./scripts/start_camera.sh            # 一条命令
    ./scripts/start_camera.sh --dtype fp16   # 透传 demo 参数

- 摄像头配置在 **`configs/ip_camera.env`**（URL/账号/IP/网口/PoE 引脚/密码均可改）
- 启动即弹窗（DISPLAY=:0 自动探测），左键点击目标开始跟踪，q 退出
- 程序崩溃/退出会自动重启（3 秒间隔），Ctrl-C 全停
- 串口登录时也能跑（脚本自行发现 X 会话）

验证记录：GUI 窗口正常（disp~48fps）、点击跟踪进入即用、进程双保险存活。

## 8. 叉车后方测距碰撞预警系统（warn_app）— 2026-09-04

独立测距预警子系统，用法见 `docs/warning.md`。

- 结构：`app/warning/`（backend/过滤/地面/ROI/障碍/时序/速度TTC/风险/
  报警/日志/截图/UI/标定）+ `app/warn_app.py` + `configs/warning.yaml`
- 一键启动：`scripts/run_warning.sh`（PoE+摄像头探测+全屏）
- 模型：DAV2 Metric Small（checkpoint 已在 NX）→ ONNX opset16 →
  TRT 8.5 FP16 引擎（trtexec 原生 26.5ms/帧 @518）
- 实测：12/12 单测、合成场景全链 gate PASS、warn_app TRT 端到端 25 帧
  跑通（DANGER+蜂鸣触发）；PyTorch 回退 79ms/帧
- TRT 踩坑：opset≤16（无 LayerNorm importer）、静态模型禁显式 shapes、
  binding 按 engine 顺序 + GPU buffer（host 指针重复调用会 illegal
  memory access）、torch/TRT 上下文冲突 → fallback 懒构造
- 状态：真机摄像头未接入，场测（卷尺精度/Test 1-7）BLOCKED；软件链已
  用录像+合成场景验证
