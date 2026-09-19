# 叉车后方测距碰撞预警（warn_app）

单目 RGB（Depth Anything V2 Metric Small）实时距离感知 + 碰撞预警系统，
运行于 NX（Orin NX 16GB / JetPack 5.1.3 / Py3.8 / torch 2.1 / TRT 8.5）。

定位：**驾驶辅助原型**（见文末安全边界）。

## 运行方式

后视预警是总控的三条推理之一，由总控托管（子进程 `app/warn_app.py`，
端口 **8080 仅回环**，浏览器只经 `/api/hub/stream/rear` 访问）：

```bash
cd /home/seeed/workspace/seg_demo
./scripts/run_visual_hub.sh                       # 启动总控（或 systemctl start visual-hub）
curl -X POST http://127.0.0.1:8000/api/hub/modules/rear/start
curl -X POST http://127.0.0.1:8000/api/hub/modules/rear/stop
```

或不经总控直接单跑（开发/调试用；**不要**在总控已启动后占用 8080）：

```bash
.venv/bin/python -u app/warn_app.py \
    --camera rtsp://USER:PASSWORD@CAMERA_IP:554/ --fullscreen
.venv/bin/python -u app/warn_app.py --camera video:x.mp4 --headless --max-frames 200
```

## 按键

| 键 | 功能 |
|---|---|
| Q / ESC | 退出 |
| D | 切 debug 视图 1..6（RGB / 深度叠加 / 深度图 / 地面 / 障碍 / ROI） |
| C | 标定 UI（trackbar：高度、俯仰、告警距离；鼠标拖动 ROI 四顶点） |
| S | 保存标定到 configs/warning.yaml |
| F | 全屏切换（启动脚本默认全屏） |
| 空格 | 暂停 |
| R | 录像开关（预留） |

## 状态机

`SAFE → WARNING → DANGER`，进出阈值带迟滞（WARNING 进 3.0m 退 3.3m；
DANGER 进 1.5m 退 1.8m）。障碍需 3/5 帧连续出现才生效（一张噪点不足以
触发）。测距采用**各障碍区域 10% 百分位**而非 min()。速度 = 最近 10 帧
距离-时间线性回归斜率；TTC = d/v（仅当接近速度 > 0.10 m/s）。

**SYSTEM ERROR**（红/品红）：相机失联 >5s、连续 3 帧深度无效、推断
后端故障——出现时绝不显示 SAFE（fail visible）。

## 性能（NX 实测 / 2304×1296 输入）

| 后端 | 推断 | 总延迟(引擎内) | 说明 |
|---|---|---|---|
| PyTorch fp16 | 79 ms | ~113 ms | eager，无需引擎 |
| TensorRT fp16（518） | 26.5 ms（trtexec 原生） | ~46 ms（含预处理+回放） | scripts/build_engine.sh |

## 标定

配置文件统一是 `configs/warning.yaml`（全部阈值 / ROI / 滤波 / 时序 / 标定参数）。

1. **内参**：棋盘格标定，支持 PoE RTSP 与 USB 源：

   ```bash
   scripts/calibrate_camera_intrinsics.py --source usb:0 \
       --board 9x6 --square-mm 25 --out configs/camera_calibration.yaml
   ```

   把输出的 `fx / fy / cx / cy / k1..k3 / p1 / p2` 填进
   `configs/warning.yaml` 的 `camera.intrinsics`，并将 `camera.calibrated` 置 `true`
   （当前为 `false` → 使用 60° 水平 FOV 估算，距离只具备序关系意义）。
2. **安装几何**：app 内按 `C` → trackbar 调 `camera_mount.height_m / pitch_deg`，
   拖动 ROI 四顶点，`S` 保存。
3. **距离校正**：`configs/warning.yaml` 的 `depth_calibration`（`enabled/scale/offset`，
   `corrected = raw*scale + offset`）。卷尺法：目标置于 0.5/1/2/3/5 m，读取
   稳定深度后自行拟合填入。

## 测试

```bash
.venv/bin/python -m pytest -q tests/test_warning_core.py    # 14 项单元
.venv/bin/python tests/test_warning_synthetic.py            # 合成场景 gate（main 脚本）
```

## 安全边界

单目深度在低照度、强反光、透明物体、黑色物体、镜头污染、强振动的场景会
失效。本系统为**原型 / 辅助提示**，不能替代驾驶员观察与操作纪律；任何
感知不可靠状态必须显示 SYSTEM ERROR，绝不默认 SAFE。