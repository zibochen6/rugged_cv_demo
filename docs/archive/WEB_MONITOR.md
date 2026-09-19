# Web 监控远程访问（PC 经 SSH 连接 Jetson）

叉车后视碰撞系统的实时监控画面走 `warn_app.py --web`（MJPEG）。

- 监控面：画布 + 底部 NEAREST | TTC | STATUS + 顶部遥测条（level/fps/dist/ttc 等）。
- 调试面：加 `--debug` 后画布上直接叠加调试面板。
- 数据面：`GET /state` 返回 JSON，自动化监控可直接轮询。
- **录制开关**：顶部多了一个 `录制事件` 复选框（默认 **关**）。开启后，每次进入 DANGER 时把渲染画布写到 `configs/_runtime_overrides.yaml:save_dir`（默认 `events/`），文件名 `danger_YYYYMMDD_HHMMSS_<ms>.jpg`。状态持久化在 `configs/_runtime_overrides.yaml`，重启后保留上次选择。

## 1. 在 Jetson 上启动监控

```bash
cd /home/seeed/workspace/seg_demo
RTSP_URL=rtsp://admin:admin@192.168.1.10:554/ ./scripts/run_web.sh --debug
# 或按默认配置（脚本会自行发现相机）： ./scripts/run_web.sh --debug
# 无显示器时自动 --headless（只推流不弹窗）；有显示器则同时显示本地窗口。
```

启动后 Jetson 会打印可达地址（形如 `http://192.168.6.84:8080/`）。

---

## 2. 从你的 PC 打开

### 方案 A：同网段直连（零配置）

浏览器打开：`http://192.168.6.84:8080/`
（前提：网络放行 8080 入站；不放行就用方案 B。）

### 方案 B：SSH 隧道（推荐，任何网络必通，且不把 8080 暴露给局域网）

在 **PC 终端**执行：

```bash
ssh -N -f -L 8080:127.0.0.1:8080 seeed@192.168.6.84
```

浏览器打开：`http://127.0.0.1:8080/`

脚本一键版（仓库里已带，PC/Jetson 上都能运行）：

```bash
./scripts/web_tunnel.sh          # 开监控隧道
./scripts/web_tunnel.sh -k       # 撤掉以上隧道
# 覆盖默认：HOST=user@host ./scripts/web_tunnel.sh
```

安全说明：`-L` 把远端端口只转发到 **PC 本机** 127.0.0.1，浏览器/脚本访问时不会经过局域网其他设备；隧道进程结束即断。

---

## 3. 页面字段说明

| 区域 | 内容 |
|---|---|
| 顶栏 | FPS、CAM/AI 健康点、当前风险等级（SAFE/WARNING/DANGER/ERROR） |
| 控制栏 | 报警距离 slider、蜂鸣器开关、**录制事件** 开关（off/on） |
| 画面 | 实时后视 + 危险 ROI + 深度 inset；`--debug` 时另叠加调试面板 |
| 底线 | NEAREST · TTC · STATUS |
| /state | 全量 JSON，含 level/fps/distance/ttc/vel/cam_ok/ai_ok/engine/buzzer/danger_m/recording/save_dir/stream_fidx/stream_age_ms |

### 录制事件（recording）

- 关闭时（默认）：DANGER/WARNING 触发不会写盘，`events/` 目录不会增长。
- 开启时：每次进入 DANGER 时把画布写到 `save_dir/danger_*.jpg`，受 `configs/warning.yaml:events.min_interval_s` 节流（默认 2.0s）。
- 状态持久化：每次 UI toggle 都写一次 `configs/_runtime_overrides.yaml`（顶层 `runtime:` 段），重启后自动恢复。
- 运行时改保存目录：直接编辑 `configs/_runtime_overrides.yaml` 的 `runtime.save_dir`，下次 DANGER 触发生效。

### `POST /api/config` 字段白名单

| Key | 类型 | 说明 |
|---|---|---|
| `danger_m` | float [0.1, 5.0] | 报警距离阈值（slider 同步） |
| `buzzer` | bool | 蜂鸣器 |
| `recording` | bool | 录制事件开关 |

未知键被静默忽略。

---

## 4. 常见问题

- **相机打不开（GStreamer static TLS）**：本板需预加载 libGLdispatch（`run_warning.sh` / `run_web.sh` 已自动处理）。手动直跑 `python app/warn_app.py --web` 时请加 `LD_PRELOAD=/lib/aarch64-linux-gnu/libGLdispatch.so.0`。
- **录制开关没有效果**：检查 `configs/_runtime_overrides.yaml` 是否被生成、权限是否可写；终端应能看到 `[warn:web] persist failed: ...`。
- **录制文件没有出现**：先看 `events/` 目录是否被 `.gitignore` 过滤（不会被 commit 是正常的），再用 `ls -la events/danger_*.jpg | tail` 验证落盘。
- **8080 被占**：说明已有监控实例在跑；换端口 `WEB_PORT=9001 ./scripts/run_web.sh`，隧道对应 `MONITOR_PORT=9001`。
