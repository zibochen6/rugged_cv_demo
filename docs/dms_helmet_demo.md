# DMS 头盔演示（USB 摄像头疲劳检测 + 是否佩戴头盔检测）

> **诚实口径（必须与代码/UI 中的标注一致）**
> 演示级实现：未做 PERCLOS 标定；无近红外相机时夜间/强逆光不可用；结果不构成安全认证。
>
> - 疲劳路：Haar 人脸 + 眼带/嘴部**暗区占比代理量**，**不是 EAR、不是 PERCLOS**，未做任何标定。
> - 头盔路：人体框 → 头部 ROI 的**颜色启发式**，按人输出 `佩戴 / 未佩戴 / 未知`（三值）。
> - 本 demo **没有任何标注数据集**，因此**不给出**任何百分比式的识别结论；文档中也不会出现这类数字。
> - 该实现**不得**用于合规判定、门禁、处罚或任何安全认证用途。

- 目标仓库：`/home/seeed/workspace/seg_demo`（远程 Jetson，`seeed@100.109.1.72`）
- 解释器：`.venv/bin/python`（Python 3.8.10；`cv2 4.5.4`；`ultralytics 8.3.40`；`torch 2.1.0`）
- 契约来源：`t1` 冻结的 `staging/dms_helmet/00_requirements.md`（旧 r1，UI 章节部分被取代）+ **UI v2 冻结规格 `ui-v2-r3`**（冻结副本 `staging/dms_ui_declutter/00_ui_requirements_v2_r3_FROZEN.md`，内容哈希 `05fb0bb88882556e14f99418e4c9abf9`；r1/r2 已作废，本次 UI 章节以 r3 为准）；本文件记录**实际落地的实现**与偏差

---

## 1. 交付物

| 路径 | 说明 |
|---|---|
| `app/dms/__init__.py` | 包文档（演示级口径 + 模块清单） |
| `app/dms/config.py` | `configs/dms.yaml` 加载 + `DEFAULTS` 兜底（缺文件/坏 yaml 不崩） |
| `app/dms/camera.py` | 相机源：`usb:` / `rtsp://` / `video:` / `image:` / `synthetic` + 明确错误 |
| `app/dms/state.py` | `DmsRuntime`：两个开关的唯一真源 + `/state` 快照 + 推理计数 |
| `app/dms/face.py` | Haar 目录解析（本机无 `cv2.data`）+ 主脸检测/跟踪 + ROI/暗区工具 |
| `app/dms/fatigue.py` | 疲劳信号 + 去抖状态机（`DISABLED/UNKNOWN/NORMAL/DROWSY_WARN/DROWSY_ALARM`） |
| `app/dms/helmet.py` | 头盔三值启发式 + 三道门控 + 按 `track_id` 多数票平滑 |
| `app/dms/events.py` | `logs/dms_events.jsonl` 事件日志（一行一 JSON） |
| `app/dms/render.py` | 画面叠加 + 画面内复选框（纯 ASCII）+ 命中矩形/hit-test |
| `app/dms/web.py` | 零额外依赖 MJPEG 服务（自行实现，**不 import** `app/web_stream.py`） |
| `app/dms_app.py` | CLI 入口（`--mode web|display`、开关、退出码） |
| `configs/dms.yaml` | 全部阈值与开关默认值 |
| `scripts/run_dms_demo.sh` | 一键启动（web 端口预检 / display 选屏） |
| `tests/dms/*.py` | 不依赖相机的单测（af218ab 实测 `.venv/bin/python -m pytest tests/dms -q` → **55 passed**；UI v2 另新增 `test_hud_layout.py`） |
| `docs/dms_helmet_demo.md` | 本文件 |

未改动任何既有文件（`app/warn_app.py`、`app/web_stream.py`、`app/warning/**`、`backend/**`、
`frontend/**`、`third_party/**`、`README.md`、既有 `configs/*.yaml`、既有 `scripts/*` 一律零改动）。

---

## 2. 启动方式

### 2.1 一键脚本

```bash
cd /home/seeed/workspace/seg_demo

# web 模式（默认；端口 8010，契约 §12 冻结）：浏览器看 MJPEG + 两个复选框
./scripts/run_dms_demo.sh

# 显示器模式（本地窗口，画面内可点击复选框 + 热键 1/2）：脚本会自动 source gui_display.sh 选屏
./scripts/run_dms_demo.sh --mode display

# 零相机冒烟（确定性合成帧）
DMS_CAMERA=synthetic ./scripts/run_dms_demo.sh --headless --max-frames 120

# 换端口 / 换源 / 强制选屏
DMS_PORT=8011 ./scripts/run_dms_demo.sh
DMS_CAMERA=usb:0 ./scripts/run_dms_demo.sh
DMS_MODE=display GUI_DISPLAY=:0 ./scripts/run_dms_demo.sh
```

环境覆盖：`DMS_MODE`（`web|display`）、`DMS_CAMERA`、`DMS_PORT`（默认 **8010**）、
`DMS_HEADLESS`（`1` = 只服务不开窗）、`GUI_DISPLAY`（display 模式强制选屏 `:N`）。

### 2.2 直接调用 CLI

```bash
.venv/bin/python -u app/dms_app.py --help

# web：服务 + 叠加画面
.venv/bin/python -u app/dms_app.py --mode web --camera usb:0 --port 8010

# web：只服务，不创建窗口（自动化/无 X 环境）
.venv/bin/python -u app/dms_app.py --mode web --headless --camera synthetic --run-seconds 5

# display：本地窗口
DISPLAY=:0 .venv/bin/python -u app/dms_app.py --mode display --camera usb:0

# 开关的 CLI 初始值（三态，显式取值；不传就取 configs/dms.yaml）
.venv/bin/python -u app/dms_app.py --mode web --camera synthetic --fatigue off --helmet on
```

参数表（`app/dms_app.py`）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--mode {web,display}` | `web` | web = MJPEG 页面；display = `cv2.imshow` 窗口 |
| `--headless` | off | 仅 web：不创建窗口（`--mode display --headless` → 退出码 2） |
| `--camera SRC` / `--source SRC`（同义写法，`add_argument("--camera", "--source", dest="camera")`） | `configs/dms.yaml: camera.source` | `usb:<idx>` / `rtsp://...` / `video:<file>` / `image:<path>` / `synthetic[:scene|lowlight|eyes_closed|head_yellow]` |
| `--config PATH` | `configs/dms.yaml` | 配置路径（缺失/损坏 → 用默认值并打印一行诊断） |
| `--port N` / `--host H` | `8010` / `0.0.0.0` | web 绑定；被占用 → 退出码 5 |
| `--fatigue {on,off}` / `--helmet {on,off}` | 不传 = 取 yaml | CLI 初始开关（三态，避免隐式语义） |
| `--max-frames N` / `--run-seconds S` | 0（不限） | 自动化用停止条件 |
| `--fullscreen` | off | display 窗口全屏 |
| `--debug` | off | **打开 UI 调试层**（display 画面出现 `DBG` 块；web 页面 `#debug` 区初始可见，否则默认 `hidden`），并打印调试信息（含 camera 共享访问提示、traceback 附加）。运行中也可切换：display 窗口热键 `d`、web 页面按键 `d` |
| `--out PREFIX` | — | 兼容占位；本 demo 只写事件 JSONL，不写 CSV |

启动时会打印契约 §4.9 冻结的几行摘要（每行的 `source:`/`from:` 都是 **provenance**，
取值 `cli|config|default`，便于脚本断言与抗漂移）：

```text
[dms] switches: fatigue=ON helmet=ON (source: config)
[dms] camera: usb:0 (source: config)                                          # 契约 §4.9:559 冻结形式
[dms] camera: usb:0 negotiated 1280x720@30 MJPG (requested 1280x720; source: config)
[dms] camera: usb:0 1280x720 (camera_source: usb:0; from: config)             # 信息更全的并存行
[dms] web listening on 0.0.0.0:8010 (source: config)
[dms] web: mode=web headless=True
```

- `switches.source` = 两个开关初值的来源；`web listening on ….source` = 端口/主机来源；
  `negotiated ….source` = 请求分辨率/FPS 的来源；
  `camera: <label> (source: <provenance>)` = **契约 §4.9:559 冻结形式**（为满足契约断言而保留）；
  `camera: <label> <WxH> (camera_source: <源字符串>; from: <provenance>)` = 信息更全的**并存行**
  （`--camera/--source` → `cli`，yaml `camera.source` → `config`，DEFAULTS → `default`）。

### 2.3 退出码

| 码 | 含义 | 触发条件 |
|---|---|---|
| 0 | 正常结束 | `--max-frames` / `--run-seconds` 达到、`q`/ESC、Ctrl-C |
| 2 | 配置/参数错误 | `--mode display --headless`；`fatigue.backend`/`helmet.backend` 选了未实现值 |
| 3 | 相机源不可用 | 源打不开（`image:`/`video:` 不存在、RTSP 不可达）或首帧超时（8s） |
| 4 | display 无可用 X | `--mode display` 且 `DISPLAY` 不可达；提示改用 `--mode web` |
| 5 | web 端口被占用 | `bind()` 抛 `OSError`；提示换 `--port`/`DMS_PORT` 或停占用者 |

任何非零退出都是**单行结论 + 可操作建议**，不把 Python traceback 作为用户可见错误
（traceback 只在 `--debug` 下作为附加内容）。

---

## 3. 双模式 UI

界面口径为 **UI v2（冻结规格 `ui-v2-r3`，内容哈希 `05fb0bb88882556e14f99418e4c9abf9`）**：**单槽位**——每个语义在同一个界面里只出现一次；默认界面只保留 4 类信息（疲劳状态 / 头盔结论 / 相机与人脸可用性 / 两个开关）+ 1 次诚实标注；其余字段全部进入**调试层**（`--debug` 或热键 `d`）。

### 3.0 两模式一览

| 维度 | `--mode web` | `--mode display` |
|---|---|---|
| 载体 | 浏览器页面（`/` + `/stream` MJPEG） | 本地窗口 `DMS helmet demo`（`cv2.imshow`） |
| 默认画面文字 | **零像素文字**：`/stream`、`/frame` 只画检测框 + 每框 1 行短标签 | HUD 3 行 + 控件行 2 行 + 底部 notice 条（纯 ASCII） |
| 状态载体 | 页面 DOM（`#fatigueState` / `#helmetState` / `#status`） | 画面内 HUD 底板 |
| 开关控件 | HTML 复选框 `id="fatigueEnabled"` / `id="helmetEnabled"`（中文「疲劳检测」「头盔检测」） | 画面内控件行 `[1] FATIGUE: ON` / `[2] HELMET: ON`（方框由 `cv2.rectangle` 画，**不带** `state=` 后缀） |
| 鼠标 | 点击复选框（`change` → `POST /api/dms_config`） | `EVENT_LBUTTONDOWN` + hit-test（与绘制**同一次调用**返回的控件矩形） |
| 热键 | 无开关热键；页面内 `d` 切换调试区 | `1` 切疲劳、`2` 切头盔、`d` 切调试层、`q`/ESC 退出 |
| 状态回读 | 前端每秒轮询 `/state` 回填（用户正在拖动的控件不回填） | 每 2.0s 向 stdout 打印一行 `[dms:state] {…}`（与 `/state` 的 `dms` 子树同构） |
| 诚实标注 | `#notice`（中文，全页 **1 次**） | 画面底部 ASCII 条（**1 次**） |
| 调试层 | `#debug`（默认 `hidden`；`--debug` 或页面按 `d` 打开） | 画面内 `DBG` 底板（`--debug` 或窗口按 `d` 打开） |
| 旧版状态栏 | **不存在**（af218ab 的 `#bar` 实测 **13 项**，本轮删除） | — |
| 推流缩放的文字影响 | 无（文字在 DOM 里，CSS 字号不受推流缩放） | 无（display 不经推流） |

> 旧版口径已作废（取代记录见 §11）：web 页面**不再有** `#bar` 12 项状态栏；**推流画面不再有** HUD 文字、调试文字、notice 条与画面内复选框；display 的控件行**不再带** `state=`。

---

### 3.1 display 模式的窗口可见性（现场实测，重要）

- 本机 Xorg `:0` 的 **`HDMI-0 disconnected`**（`xrandr -display :0`）：X 是可达的、根窗口也能截图，
  但**没有任何窗口会被 map**。实测：cv2 建的窗口 `Map State: IsUnMapped`、
  `cv2.getWindowProperty(WND_PROP_VISIBLE)` 返回 `-1.0`；对照实验里**原生 `xeyes`/`xclock` 同样
  `IsUnMapped`** → 这是"console 接了 X 但没有显示器"的环境状态，不是本 demo 的问题。
- 因此 display 模式必须在**真正可见的显示**上验证，三条可用路径：
  1. `./scripts/run_dms_demo.sh --mode display`：脚本会 source `scripts/gui_display.sh` 自动选
     RDP（`:10`）/ xpra（`:100`）/ 有显示器时的 console 显示；
  2. 显式指定：`GUI_DISPLAY=:10 DMS_MODE=display ./scripts/run_dms_demo.sh`；
  3. 自建虚拟 X（无需显示器、可截图、可注入输入）：
     `Xvfb :99 -screen 0 1280x800x24 &` 然后
     `DISPLAY=:99 GUI_DISPLAY=:99 DMS_MODE=display ./scripts/run_dms_demo.sh --fatigue on --helmet off`。
- 本次实测证据（Xvfb `:99`，截图见团队工作区 `staging/dms_helmet/evidence/`）：
  `xwininfo -name "DMS helmet demo"` → `Width: 640 Height: 480 Map State: IsViewable`；
  XTEST 真实点击 helmet 复选框 → `[dms] switch: helmet=ON (runtime) fidx=304`；
  XTEST 真实按键 `1` → `[dms] switch: fatigue=OFF (runtime) fidx=305`；
  再真实点击 fatigue 复选框 → `[dms] switch: fatigue=ON (runtime) fidx=306`。
- 窗口内按 `d`（或 `--debug` 启动）会打开**调试层**：HUD/控件/notice 之外再画一块 `DBG` 底板，其中是全部 12 类调试字段（§3.5）。再按 `d` 即关；`d` 只翻转展示布尔，不触碰状态真源、不影响推理计数与事件日志。
- **两个复选框在 display 窗口内可点击**：`EVENT_LBUTTONDOWN` 用与绘制**同一次调用**返回的控件矩形做 hit-test（r3 命中中心见 §3.1.1；1280 宽画布下第 1 行 `(18,129)`、第 2 行 `(18,157)`），点击/按键后 stdout 出现 `[dms] switch: fatigue=… (runtime) fidx=…`。
- `--debug` 会额外打印 `[dms] mouse: window=(x,y) image_rect=... canvas=(x,y) hit=<name>`，
  便于验证"点哪儿命中什么"。

**窗口画布坐标换算（r3 冻结语义；控件命中可用性见 §3.1.1）**：display 模式创建窗口后先 `cv2.resizeWindow(win, W, H)`
让窗口与画布 **1:1**（鼠标坐标与画布坐标一致，命中判定稳定）；`EVENT_LBUTTONDOWN` 时对
`draw_dms_frame(surface="display", …)` 返回的 `layout.controls`（**同一次调用**的同一份矩形）做 hit-test。
考虑到"窗口被缩放时后端可能已经把坐标换算回画布空间、也可能没有"这一风险（契约 R6），实现会先用
原始坐标试一次、再用 `getWindowImageRect` 换算后的坐标试一次，命中即算——两种解释用的是同一份矩形，
不会给出与画面不一致的结论。

### 3.1.1 控件命中可用性（r3 新增；直接对应用户诉求「复选框画在画面上却点不动」）

**目标**：画面内两个复选框不仅"看得见"，而且"点得中、点得准"——无论窗口是否被 WM 缩放或留下黑边。

| 项 | 冻结规则（r3） |
|---|---|
| 可点目标高度下限 | `ROW_H_MIN = 28`（px，**硬要求**）。每档画布的命中矩形高度 `ROW_H = max(BOX + 8, 28)` 都必须 ≥28；0.8 及以下各档均为 28，1.0 档为 32 |
| 复选框视觉边长 | `BOX = max(glyph_h(hud_scale) + 2, 18)`（0.45~0.7 → 18；0.8 → 20；1.0 → 24），复选框在行内**垂直居中**：`box_y(i) = panel_y + i*ROW_H + (ROW_H - BOX)//2` |
| 相邻命中区零重叠 | 命中矩形竖向**不再用 ±4 余量**：`ControlRect(i) = (PANEL_X - 4, panel_y + i*ROW_H, BOX + 18 + text_w, ROW_H)`。因此第 1 行下沿 == 第 2 行上沿，**严格相接、零重叠**，消除"点在第二行上沿却命中第一行"的歧义（旧口径 `y = row_y - 4`、`h = BOX + 8` 会让相邻命中区重叠 8px） |
| 横向余量 | 仍保留 `x = PANEL_X - 4`、`w = BOX + 18 + text_w` |
| 缩放 / 留边（letterbox）正确换算 | 命中判定必须按 `cv2.getWindowImageRect` 给出的**图像实际显示区域**做线性换算（`map_mouse_to_canvas` 签名不变，语义改为"按实际显示区域换算"） |
| 取不到 image rect | `image_rect` 为 `None` 或 `rw <= 0 / rh <= 0` → 视为不可用，**退回画布坐标（恒等换算）** |
| 换算后越界 | 换算后落在 `[0,W) × [0,H)` 之外（点在留边/窗口空白处）→ **不命中** |
| **不得保留 raw-first 兜底** | `image_rect` 可用时**只**用换算后的画布坐标做 hit-test，**禁止**再用原始窗口坐标兜底——"先试原坐标再试换算坐标"正是留边场景**假命中**的根因，r3 明确删除 |

**判定接口（r3 新增，可单测）**：`resolve_control_hit(rects, mx, my, image_rect, canvas_shape) -> Optional[str]`，冻结语义为：

```python
def resolve_control_hit(rects, mx, my, image_rect, canvas_shape):
    """窗口坐标 -> 命中的控件名（留边/缩放安全）。"""
    if image_rect and len(image_rect) >= 4 and image_rect[2] > 0 and image_rect[3] > 0:
        cx, cy = map_mouse_to_canvas(mx, my, image_rect, canvas_shape)
        h, w = int(canvas_shape[0]), int(canvas_shape[1])
        if not (0 <= cx < w and 0 <= cy < h):
            return None                      # 点在留边/窗口空白处 -> 不命中
        return hit_test(rects, cx, cy)
    return hit_test(rects, int(mx), int(my))  # 拿不到 image_rect -> 退回画布坐标
```

**主循环接线（r3 冻结）**：鼠标回调 `on_mouse` 只调用 `resolve_control_hit(mouse_state["rects"], mx, my, mouse_state["image_rect"], mouse_state["shape"])`；命中则走同一个 `runtime.set_flag()`。`image_rect` 每帧由 `cv2.getWindowImageRect(WINDOW_NAME)` 更新（失败 → `None`）；`--debug` 的既有鼠标日志行（`[dms] mouse: window=… image_rect=… canvas=… hit=…`）**保留**。

`hit_test` 语义不变（返回**第一个**命中的 `name`）；既有断言 `hit_test(rects, r.x+2, r.y+2)` 仍命中（落在各自行内，不与相邻行重叠）。

**留边场景的可对照例子**（`test_resolve_control_hit_letterbox`）：`image_rect=(0,60,1280,720)`、画布 640×480 时，窗口 `(200,222)` → 画布 `(100,108)` 命中第 1 行；窗口 `(200,30)` → 画布 `(100,-45)` 越界**不命中**；窗口 `(200,770)` → 画布 `(100,473)` 在画布内但不在控件内**不命中**。

- **对应的 r3 失败模式 E10**（WM 缩放 / 纵横比留边导致命中偏移或假命中）：① `image_rect` 有效时**只**用换算后的画布坐标做 hit-test；② 换算后越界 → 不命中；③ `image_rect` 为 `None` 或 `rw<=0/rh<=0` → 退回画布坐标（恒等）；④ 命中矩形高度 ≥28px（C8）；⑤ 相邻行命中区零重叠。真机复核：把 640×480 画布的窗口强制成 1280×600（含竖向留边）后，点击控件可见位置命中、点击留边空白不命中。

两模式一致：**控件矩形只出现在可交互表面**（本地窗口）。web 推流画面**没有**任何控件矩形，开关由页面复选框承担——所以"画出来却点不动"的情况在 web 侧不存在。



---

### 3.2 display 模式：默认界面结构

按画布尺寸**自上而下自动堆叠**（不再用 `ui.panel_x/panel_y/line_h/font_scale` 的固定像素），三块矩形两两不相交、全部落在画布内：

| 位置 | 块 | 内容 |
|---|---|---|
| 左上 | HUD 底板（半透明深色） | 3 行：`CAM:*  FACE:*` / `FATIGUE: <状态>` / `HELMET: <结论>` |
| HUD 下方 | 控件行（2 行，**带方框**） | `[1] FATIGUE: ON|OFF` / `[2] HELMET: ON|OFF` |
| 底部 | notice 条（整宽、居中、按词折行） | `configs/dms.yaml: notices.ascii` |
| 控件行下方 | 调试块（**仅调试层打开时**） | 6 条固定行 + 逐人行（§3.5） |

- 调试块永远排在控件行**之后**：开/关调试层**不会移动开关热区**，hit-test 稳定。
- 检测框与短标签画在画面上（人脸：`face id<N>` / `FACE LOST`；人体：`id<N> <WORN|NOT WORN|UNKNOWN>`），与上述 4 块互不占用。

### 3.2.1 display 默认界面字段表（纯 ASCII）

| 槽位 | 冻结文案 | 字段来源 | 颜色 |
|---|---|---|---|
| HUD 行 1 | `CAM:OK  FACE:OK`（两段之间固定 2 个空格） | `meta.cam_ok`；`snapshot.fatigue.enabled/face_present` | OK 绿 / LOST 红 / N/A 灰 |
| HUD 行 2 | `FATIGUE: <STATE_TEXT_ASCII[state]>`（冒号后固定 1 个空格） | `snapshot.fatigue.state` | 状态色（`STATE_COLOR`） |
| HUD 行 3 | `HELMET: <HELMET_SUMMARY_ASCII[x]>` | §3.3 的聚合结论 | 结论色（`VERDICT_COLOR`）/ 关闭灰 |
| 控件行 1 | `[1] FATIGUE: ON` / `[1] FATIGUE: OFF` | `snapshot.fatigue.enabled` | ON 绿 / OFF 灰 |
| 控件行 2 | `[2] HELMET: ON` / `[2] HELMET: OFF` | `snapshot.helmet.enabled` | ON 绿 / OFF 灰 |
| 底部条 | `cfg.notices_ascii`（一字不改） | config | `COLOR_TEXT` |
| 框上短标签 | 人脸 `face id<N>` 或 `FACE LOST`；人体 `id<N> <结论>` | `frame_result` | 与状态/结论同色 |

- `CAM:` = `OK`（取 `meta.cam_ok`）| `LOST`；`FACE:` = `OK`（本帧有脸）| `LOST`（疲劳路在跑但人脸丢失）| `N/A`（`fatigue.enabled == False`，该路未运行，如实标注"不适用"）。
- **默认 HUD 内不出现** `score=`/`infer=`/`fidx=`/`eye=`/`mouth=`/`wn/u=`/`persons=` 等任何调试 token（§3.6.1 的单槽位判定）。

> **[诚实缺口 G1，本次不修]** `CAM:` 的取值来自既有 `meta.cam_ok`，而主循环在**1–9 次**读帧失败时仍传
> `cam_ok=True`（失败计数只在**连续 10 次**读帧失败时才抛 `CameraUnavailable`，以退出码 3 结束）。
> 因此 **`CAM:LOST` 在真机上目前不可触发**——它只能通过单元测试（构造 `meta={"cam_ok": False}`）验证显示行为。
> 本次只改 UI，**不改 `cam_ok` 的取值语义**（改动 `/state` 的值语义超出"UI 精简"边界，登记为后续任务）。
> 换句话说：默认界面上的 `CAM:OK` 是"主循环还在出帧"，不等于"相机链路始终健康"，用户读到 `CAM:OK`
> 时应结合画面判断。该项与 §7「已知失败模式」的 `unknown` 口径一致：宁可如实标注不足，不包装成结论。

---

### 3.3 头盔结论：默认界面只给一个聚合值

默认 HUD 与 web `#helmetState` 显示的是**同一个聚合结论**（纯展示聚合，**不是检测逻辑**；逐人的 `verdict ∈ {worn, not_worn, unknown}` 仍由头盔引擎产生，规则不变）：

| 优先级 | 结果 | display（ASCII） | web（中文） |
|---|---|---|---|
| 头盔开关关闭 | `disabled` | `DISABLED` | `已关闭` |
| 头盔路开着但**无人体框** | `no_person` | `NO PERSON` | `无人体框` |
| 任一人 `not_worn` | `not_worn` | `NOT WORN` | `未佩戴` |
| 否则任一人 `unknown` | `unknown` | `UNKNOWN` | `无法判定` |
| 其余（全部 `worn`） | `worn` | `WORN` | `已佩戴` |

- 优先级固定为 **`not_worn` > `unknown` > `worn`**（保守优先：宁可显示"未佩戴/无法判定"，不把不确定说成"已佩戴"）。
- **逐人**结论只出现在两处：各自的检测框短标签、调试层（§3.6）；默认 HUD/控件行/`#status`/`#notice` 都不罗列逐人明细。

---

### 3.4 web 模式：默认 DOM 与推流画面

### 3.4.1 默认可见 DOM（顺序固定）

```html
<img src="/stream" alt="live DMS canvas">
<div id="ctl">
  <label><input type="checkbox" id="fatigueEnabled"> 疲劳检测 <span id="fatigueState">-</span></label>
  <label><input type="checkbox" id="helmetEnabled"> 头盔检测 <span id="helmetState">-</span></label>
</div>
<div id="status"><span id="camState">-</span> · <span id="faceState">-</span></div>
<div id="debug" hidden> … 调试层 12 类字段（§3.6）… </div>
<div id="notice">NOTICE_CN</div>
```

| 槽位 | 内容 | 唯一字段 |
|---|---|---|
| `#fatigueState` | 疲劳状态中文 5 值（已关闭/未知/正常/疲劳预警/疲劳报警） | 疲劳状态 |
| `#helmetState` | §3.3 的结论中文 5 值 | 头盔结论 |
| `#camState` | `相机: 正常` / `相机: 中断` | 相机可用性 |
| `#faceState` | `人脸: 正常` / `人脸: 丢失` / `人脸: 不适用` | 人脸可用性 |
| 两个 checkbox | `疲劳检测` / `头盔检测`（id 冻结） | 两个开关 |
| `#notice` | `NOTICE_CN`（全页 **1 次**） | 诚实标注 |

- **页面不再有 `#bar`**（af218ab 的 `#bar` 实测 **13 项**，本轮删除）；`#ctl` 内**不再**出现相机/人脸/评分/计数；`#status` 内**不再**出现开关或评分。
- 状态文字在 **DOM** 里（CSS 显式 px 字号），因此不受 MJPEG 推流缩放影响。

### 3.4.2 推流画面（`/stream`、`/frame`）

- 画面上**只允许**：检测框 + 每框 1 行短标签。
- **禁止**出现在推流画面上的元素：HUD 文字、调试文字、notice 条、控件矩形（ASCII 与中文都不允许）。
- 因此 `WEB_MAX_W = 960` 的推流缩放**不再影响任何文字可读性**（画面里已经没有文字）；开关改由页面复选框承担，**不再有"画在画面上却点不动"的复选框**。
- 这是 r3 **S11** 的显式取代：`--mode web` 的推流画布 = 干净画面 + 检测框与短标签；状态与开关**全部由页面 DOM 呈现**（`#ctl`/`#status`/`#notice`/`#debug`）。`--mode web` 的模式定义与 `--headless` 语义**不受影响**；web 模式下若创建本地窗口，该窗口仍按 display 表面渲染（含底部 notice 条与可点控件）。

### 3.4.3 诚实标注的 5 处落点（r3 写死；含"推流画布不画 notice 是允许的"）

| # | 落点 | 内容 | "只出现一次"的判定 | 本轮状态 |
|---|---|---|---|---|
| 1 | `configs/dms.yaml` 顶部注释 + 末尾 `notices:` 段 | 中文 + ASCII 两条诚实标注 | 文件与 af218ab 逐字节相同 | **不动** |
| 2 | web 页面 `#notice`（中文） | `cfg.notices_cn` | 页面内 `NOTICE_CN` 出现次数 == 1 | **保留，且是 web 界面唯一一处** |
| 3 | display 画面底部整宽 notice 条（纯 ASCII） | `cfg.notices_ascii` | 捕获文本中 notice 行 == 1 | **保留** |
| 4 | `GET /state` 的 `notices` 数组（中文） | `notices: [NOTICE_CN]` | schema 断言保留 | **不动** |
| 5 | 本文件（中文，顶部显著位置 + §7 已知失败模式） | 见文件头诚实口径 | `grep -c PERCLOS docs/dms_helmet_demo.md` ≥1 | **保留** |

- **明确许可**：**web 模式的推流画布内不再绘制 notice 条是允许且唯一正确的**——第 2 处落点就是 web 页面的 `#notice`，同一次会话里 web 界面仍恰好出现一次。display 模式（以及 web 模式下创建的本地窗口）**仍必须**保留底部 notice 条。
- **禁止解释**：不得把"每个界面只出现一次"读成"某处可以不存在"。5 处**全部存在**；"每界面恰好一次"两者同时成立。
- 判定方式：`curl -s localhost:8010/ | grep -c PERCLOS` == 1（页面 1 次）；`/frame` 与亮底 fixture 差分（推流画面 0 次）；display 截图底部条 1 次。



---

### 3.5 调试层：打开方式与呈现位置

| 模式 | 打开 | 关闭 / 切换 | 语义约束 |
|---|---|---|---|
| display | CLI `--debug`（初始打开）；窗口内热键 `d` | 再按 `d` 即关 | `d` **只翻转一个展示布尔**：不调用 `set_flag`、不触碰状态真源、不影响推理计数与事件日志 |
| web | CLI `--debug`（`#debug` 初始无 `hidden`）；页面内按键 `d` | 页面按 `d` 翻转 `#debug.hidden` | 同上；页面侧用 `debugForced` 标志避免每秒轮询把它覆盖回服务端初值；`INPUT`/`TEXTAREA` 内的按键不处理 |

**debug 字段全表（12 类 + 2 项现状既有字段）**：

| # | 字段（`/state` 路径） | display 调试行 | web `#debug` 元素 |
|---|---|---|---|
| 1 | `mode` | `D1  DBG ON  mode=<web\|display>` | `#dbgMode`（模式） |
| 2 | `source` | `D1  … src=<label>`（先截到 40 字符、超长以 `..` 结尾） | `#dbgSource`（来源） |
| 3 | `fps` | `D2  DBG fps=<%.1f>` | `#dbgFps`（帧率） |
| 4 | `cam_ok` | `D2  … cam=ok\|lost` | `#dbgCam`（相机） |
| 5 | `dms.fatigue.score` | `D3  DBG fat score=%.2f` | `#dbgScore`（疲劳评分） |
| 6 | `dms.fatigue.eye_dark_ratio` | `D3  … eye=<%.2f\|none>` | `#dbgEye`（眼部暗占比） |
| 7 | `dms.fatigue.mouth_open_ratio` | `D3  … mouth=<%.2f\|none>` | `#dbgMouth`（嘴部张开占比） |
| 8 | `dms.fatigue.infer_count` | `D4  … infer=<int>` | `#dbgFatigueInfer`（疲劳推理次数） |
| 9 | `dms.fatigue.last_infer_fidx` | `D4  … fidx=<int>` | `#dbgFatigueFidx`（最近推理帧号） |
| 10 | `dms.fatigue.last_infer_ms` | `D4  … <%.1f>ms` | `#dbgFatigueMs`（最近推理耗时） |
| 11 | `dms.helmet.persons[]` 明细 | `D7`（逐人，最多 3 行）+ `D8  DBG p +N more` | `#dbgPersons`（人体数）+ `#dbgPersonList`（逐人行） |
| 12 | `dms.helmet.verdict_counts` | `D5  DBG helm n=<人体数>  wn/u=<worn>/<not_worn>/<unknown>` | `#dbgCounts`（结论计数） |
| 13 | `dms.helmet.infer_count` / `last_infer_fidx` / `last_infer_ms` | `D6  DBG helm infer=<int>  fidx=<int>  <%.1f>ms` | `#dbgHelmetInfer` / `#dbgHelmetFidx` / `#dbgHelmetMs` |
| 14 | 顶层 `fidx` | `D2  … fidx=<int>` | `#dbgFidx`（帧序号） |

display 调试块的行序（`D1` → `D8`；`D7` 每人一行、最多 3 行，`D8` 仅在人数 > 3 时出现）：

```text
D1  DBG ON  mode=display  src=usb:0
D2  DBG fps=12.3  fidx=1234  cam=ok
D3  DBG fat score=0.00  eye=0.31  mouth=0.06
D4  DBG fat infer=185  fidx=180  4.1ms
D5  DBG helm n=2  wn/u=120/30/210
D6  DBG helm infer=185  fidx=180  18.6ms
D7  DBG p id3 WORN/color_evidence/mid 0.62/0.08/0.15 a=42
D8  DBG p +2 more
```

- 小画布上按可用高度**从 D1 起**保留前 `nfit` 行（`nfit < 2` 时整个调试块不绘制，并在 `--debug` 下打印 `[dms] debug layer suppressed: …`）。**640×480 与 1280×720 必须显示全部 6 条固定行 + 逐人行**。

- 上表覆盖规格 §5.1 要求的 **12 类调试字段**：`mode` / `source` / `fps` / `cam_ok` / `score`（疲劳评分）/ `eye_dark_ratio`（眼部暗占比）/ `mouth_open_ratio`（嘴部张开占比）/ 疲劳的 `infer_count`、`last_infer_fidx`、`last_infer_ms` / 头盔 `persons[]` 明细 / `verdict_counts` / 头盔的 `infer_count`、`last_infer_fidx`、`last_infer_ms` / 顶层 `fidx`。后两项（头盔推理证据与顶层帧序号）是规格里未单列但现状已有的字段，同样不许丢：共 **14 项**，一个不少。

### 3.5.1 `--debug` 的双重语义（r3 写死；两者并存，不是二选一）

| 语义 | 行为 | 本轮处置 |
|---|---|---|
| ①**日志 / traceback 详细度**（既有语义） | `usb:` 源打开时打印设备共享访问提示；鼠标事件打印 `[dms] mouse: window=… image_rect=… canvas=… hit=<name>`；未预期异常时**附加** traceback（用户可见错误仍是单行结论） | **原样保留，不得删除或改写** |
| ②**UI 调试层开关**（本轮新增） | 初始打开调试层：display 表面画 `D1..D8` 调试块、web 页面 `#debug` 不带 `hidden` | 新增，见 §3.5 |

- **运行时等价**：display 窗口内热键 `d` 与 `--debug` 的 **②** 等价（可随时开/关）；热键**不改变 ①**（不能"关掉 traceback 附加"或"关掉鼠标日志"）。
- web 页面内的按键 `d` 只切换 DOM 的 `#debug`（客户端行为，服务端 `debug` 标志不变）。
- 判定：`--debug` 启动时仍能在日志里看到鼠标调试行与（异常时）traceback，**同时**画面/页面出现调试层；两者缺一即视为回归。

---

### 3.6 清晰度指标与去冗余（可客观度量）

| ID | 指标 | 冻结值 |
|---|---|---|
| C1 | HUD 字号分档（字形高度下限） | `w>=1920`→1.0（≥22px）；`1280≤w<1920`→**0.8（≥18px）**；`960≤w<1280`→0.7（≥16px）；`640≤w<960`→**0.6（≥14px）**；`480≤w<640`→0.5（≥12px）；`w<480`→0.45（≥10px） |
| C2 | 文字底板 | 一律画在深色底板上，不透明度 **≥0.60（实现值 0.65）**；白底渲染后底板区域内中位亮度 **≤110** |
| C3 | 对比 | 文字像素亮度 ≥200 且 ≥100 px；对比度 ≥4.5 |
| C4 | 矩形互不相交 | HUD / 控件块 / 调试块 / notice 四块两两交集面积为 0，垂直间距 ≥ `GAP`（宽度 ≥480 时 8px，否则 6px） |
| C5 | 全部落在画布内 | 每个矩形 `0<=x, 0<=y, x+w<=W, y+h<=H`；每行文字 `x + text_w + 文字内边距 <= W` |
| C6 | web 推流画面零像素文字 | 无检测时 `surface="stream"` 输出与输入**逐字节相同** |
| **C8** | **控件可点目标高度下限** | 命中矩形高度 `ROW_H = max(BOX + 8, ROW_H_MIN) >= ROW_H_MIN = 28 px`（每档都必须 ≥28）；且**相邻两行命中区零重叠**（`rects[0].y + rects[0].h <= rects[1].y`） |
| **C9** | **命中与画面显示对齐（含 letterbox / WM 缩放）** | 按 `cv2.getWindowImageRect` 的**图像实际显示区域**做线性换算；取不到（`None`/退化）→ 退回画布尺寸（恒等）；换算后落在画布外 → **不命中**；`image_rect` 可用时**不得**再用原始窗口坐标兜底（详见 §3.1.1） |

**两张交付尺寸的期望值**：

| 项 | 1280×720 | 640×480 | 320×240（紧凑档） |
|---|---|---|---|
| HUD 行数 / 字形高 / 行高 | 3 / 18px / 29 | 3 / 14px / 22 | 2（缩写 `FAT:`/`HELM:`）/ 10px / 16 |
| HUD 底板 | `(8, 8, ~327, 99)` | `(8, 8, ~250, 78)` | `(4, 4, 293, 40)` |
| 控件行尺寸 | `panel_y=115`；`BOX=20`、`ROW_H=28`；控件块 `(4, 111, 259, 64)` | `panel_y=94`；`BOX=18`、`ROW_H=28`；控件块 `(4, 90, 204, 64)` | `panel_y=50`；`BOX=18`、`ROW_H=28`；控件块 `(0, 46, 164, 64)` |
| 控件行 1 命中矩形 / 中心 | `(4, 115, 244, 28)` / **(18, 129)** | `(4, 94, 191, 28)` / **(17, 108)** | `(0, 50, 152, 28)` / **(13, 64)** |
| 控件行 2 命中矩形 / 中心 | `(4, 143, 251, 28)` / **(18, 157)** | `(4, 122, 196, 28)` / **(17, 136)** | `(0, 78, 156, 28)` / **(13, 92)** |
| 命中高度 | **28 px = `ROW_H_MIN`** ✓ | **28 px** ✓ | **28 px** ✓ |
| debug 块（打开时） | `y=179`，可用 `nfit=22` 行 | `y=158`，可用 `nfit=13` 行 | `y=112`，可用 `nfit=3` 行（保留 `D1..D3`） |
| notice 条 | `scale=0.6`、1 行、`(0, 686, 1280, 34)` | `scale=0.5`、2 行、`(0, 430, 640, 50)` | `scale=0.45`、3 行、`(0, 184, 320, 56)` |

- 紧凑档（`w<480` 或 `h<360`，如 320×240）：HUD 压成 2 行并缩写为 `FAT:`/`HELM:`，notice/debug 用 0.45，调试行由可用高度决定（320×240 实测保留 `D1..D3`）；`BOX` 有 18px 下限、`ROW_H` 有 28px 下限，因此**小画布上复选框同样点得中**；**任何尺寸下 notice 都存在**，四块仍不相交。
- **推流前缩小到 960px 的影响**：UI v2 后 web 画面已无文字，缩放不再影响可读性（旧版 1280→960 会把 13px 字形等效压到约 9.75px，该问题已消除）。

### 3.6.1 单槽位分配表（同一语义只允许出现在一个地方）

| 语义 | 唯一槽位（display） | 唯一槽位（web） | 禁止出现于 |
|---|---|---|---|
| 疲劳状态 | HUD 行 2 | `#fatigueState` | 控件行、notice、短标签、关闭时的调试层 |
| 头盔结论 | HUD 行 3 | `#helmetState` | 控件行、notice、短标签（短标签只表达**逐人**结论） |
| 开关 ON/OFF | 控件行 1/2 | 两个 checkbox 的勾选态 | HUD、notice、推流画面 |
| 相机可用性 | HUD 行 1 `CAM:` | `#camState` | 其它任何位置 |
| 人脸可用性 | HUD 行 1 `FACE:` | `#faceState` | 其它任何位置 |
| 诚实标注 | 底部 notice 条（1 次） | `#notice`（1 次） | 推流画面、调试层、HUD |
| 12 类调试字段 | 调试层（仅打开时） | `#debug`（默认 `hidden`） | 默认界面任何位置 |
| 逐人结论 | 该人框上的短标签 | 同左（画面短标签） | HUD / 控件行 / `#status` / notice |
| 帧级 meta（mode/source/fps/fidx） | 调试层 | `#debug` | 默认界面任何位置 |

### 3.6.2 默认界面：两模式文案对照（display 纯 ASCII ↔ web 中文）

| 语义 | display（纯 ASCII） | web（中文） |
|---|---|---|
| 相机可用 / 中断 | `CAM:OK` / `CAM:LOST` | `相机: 正常` / `相机: 中断` |
| 人脸可用 / 丢失 / 不适用 | `FACE:OK` / `FACE:LOST` / `FACE:N/A` | `人脸: 正常` / `人脸: 丢失` / `人脸: 不适用` |
| 疲劳状态（5 值） | `DISABLED` / `UNKNOWN` / `NORMAL` / `DROWSY WARN` / `DROWSY ALARM` | `已关闭` / `未知` / `正常` / `疲劳预警` / `疲劳报警` |
| 头盔结论（5 值） | `DISABLED` / `NO PERSON` / `WORN` / `NOT WORN` / `UNKNOWN` | `已关闭` / `无人体框` / `已佩戴` / `未佩戴` / `无法判定` |
| 开关 | `ON` / `OFF`（`[1] FATIGUE: …`、`[2] HELMET: …`） | checkbox 勾选态（标签「疲劳检测」「头盔检测」） |
| 人脸丢失 | `FACE LOST` | `人脸丢失` |
| 佩戴（逐人） | `WORN` | `已佩戴` |
| 未佩戴（逐人） | `NOT WORN` | `未佩戴` |
| 未知（逐人） | `UNKNOWN` | `未知` |
| 人脸短标签 | `face id3` / `FACE LOST` | 同左（两模式共用同一份画面渲染，短标签保持 ASCII） |
| 逐人结论短标签 | `id3 WORN` / `id3 NOT WORN` / `id3 UNKNOWN` | 同左 |
| 诚实标注 | `NOTICE_ASCII`（底部条 1 次） | `NOTICE_CN`（`#notice` 1 次） |

- 本表是旧契约 §6.5 冻结对照表的**严格超集**：原 13 行（`[1] FATIGUE`/`[2] HELMET`、`ON`/`OFF`、`UNKNOWN`、`NORMAL`、`DROWSY WARN`、`DROWSY ALARM`、`DISABLED`、`WORN`、`NOT WORN`、`无法判定`、`FACE LOST`）一个语义不少，只新增默认界面的槽位与新增语义（相机/人脸/无人体框）。表中每一行两侧**同时存在或同时不存在**（不允许某模式少一个语义）。
- **纯 ASCII 硬边界**：display 画面文字只允许 ASCII 可打印字符（中文只允许出现在 web DOM、`docs/**`、`tests/**` 与 `/state.notices`），文本统一经 `render._put_text()` 出口，非 ASCII 会被替换为 `?` 以防乱码。

---

### 3.7 两模式一致的东西（冻结）

开关语义（都只经 `DmsRuntime.set_flag()`）、状态取值集合、强调色（ON 绿 `(80,220,120)` / OFF 灰 `(140,140,140)` / `DROWSY_WARN` 黄 `(60,200,255)` / `DROWSY_ALARM` 红 `(60,60,240)` / `worn` 绿 / `not_worn` 红 / `unknown` 黄 / `DISABLED` 灰）、阈值来源（都读 `configs/dms.yaml`；运行时可改的只有两个布尔开关）、§3.3 的头盔聚合优先级、诚实标注。

---
## 4. 开关契约

### 4.1 优先级

```
运行时切换（web / display 交互）  >  CLI 参数  >  configs/dms.yaml  >  DEFAULTS
```

- 运行时切换**只改内存**，不回写 yaml（yaml 只是初始值真源）。
- 阈值**不可热改**（只能改 yaml 重启），避免两模式漂移。

### 4.2 关闭某一路时**真正停止**该路推理

| 动作 | 必须发生 | 必须**不**发生 |
|---|---|---|
| 关闭疲劳 | `fatigue_engine.set_enabled(False)`（清空滑窗/EMA/跟踪）→ `/state` 的 `dms.fatigue.state == "DISABLED"` → `dms.fatigue.infer_count` 冻结 → 写 `kind="switch"` 事件 | 只"不画叠加"；把状态伪装成 `NORMAL`/`UNKNOWN`；继续跑 Haar |
| 关闭头盔 | `helmet_engine.reset()` → `dms.helmet.enabled == false`、`persons == []` → `dms.helmet.infer_count` 冻结 → 写 `kind="switch"` 事件 | 继续跑人体检测（人体检测在**头盔分支内部**，属于头盔路的唯一入口） |
| 重新打开 | 疲劳从 `UNKNOWN`、头盔从 `warming_up` 重新累积；`infer_count` 恢复增长 | 沿用关闭前的旧窗口/旧多数票 |

证据字段（`/state` 的 `dms.<scope>` 下，冻结）：
`infer_count`、`last_infer_fidx`、`last_infer_ms`。这三个字段只在**真正调用**了该路引擎时更新，
因此"计数不再增长"就是"推理真的停了"的证据。关闭头盔时 `persons` 必须为空数组。

---

## 5. web 接口与 `/state` 字段

| 路由 | 方法 | 响应 |
|---|---|---|
| `/` | GET | `text/html; charset=utf-8`，中文页面（含 `fatigueEnabled` / `helmetEnabled` 两个复选框与诚实标注） |
| `/stream` | GET | `multipart/x-mixed-replace; boundary=frame` MJPEG；**没有客户端时不编码** |
| `/frame` | GET | 单帧 `image/jpeg`（尚无帧时返回占位图） |
| `/state` | GET | `application/json`（下表） |
| `/api/dms_config` | POST | 白名单 `{"fatigue_enabled": bool, "helmet_enabled": bool}`；返回 `{"ok": true, "fatigue": {...}, "helmet": {...}, "dms": {...}}`（应用后的完整快照，无需二次轮询） |
| `/health` | GET | `{"ok": true, "frames": N}` |
| `/favicon.ico` | GET | 204 |
| 其它 | 任意 | 404 `text/plain` |

非法 JSON / 未知键 / 非布尔值一律**被忽略并保持原值**（不抛异常、不 500）。

`GET /state`（字段名冻结）：

```json
{
  "fidx": 123, "ts": 1700000000.123, "fps": 24.7, "mode": "web",
  "cam_ok": true, "source": "synthetic",
  "dms": {
    "fatigue": {
      "enabled": true, "state": "NORMAL", "score": 0.12, "face_present": true,
      "face_box": [420, 180, 160, 160], "eye_dark_ratio": 0.18,
      "mouth_open_ratio": 0.07, "infer_count": 987,
      "last_infer_fidx": 123, "last_infer_ms": 4.1
    },
    "helmet": {
      "enabled": true, "infer_count": 987, "last_infer_fidx": 123,
      "last_infer_ms": 18.6,
      "verdict_counts": {"worn": 12, "not_worn": 3, "unknown": 210},
      "persons": [
        {"track_id": 3, "bbox": [400, 150, 200, 420],
         "head_roi": [430, 150, 140, 126], "verdict": "worn",
         "reason": "color_evidence", "conf": "mid",
         "helmet_color_ratio": 0.62, "skin_ratio": 0.08, "dark_ratio": 0.15,
         "age_frames": 42}
      ]
    }
  },
  "notices": ["演示级实现：未做 PERCLOS 标定；无近红外相机时夜间/强逆光不可用；结果不构成安全认证。"]
}
```

- `fatigue.state ∈ {DISABLED, UNKNOWN, NORMAL, DROWSY_WARN, DROWSY_ALARM}`；`enabled=false` ⇒ 必须是 `DISABLED`。
- `persons[].verdict ∈ {worn, not_worn, unknown}`；`persons[].conf ∈ {low, mid}`；
  头盔判定原因（`reason`）取值：`color_evidence`、`skin_evidence`、`no_helmet_color_evidence`、
  `insufficient_evidence`、`insufficient_evidence`(多数票不成)、`smoothed_majority`、
  `warming_up`、`person_too_small`、`head_roi_clipped`、`head_too_small`。
- **判定依据（`conf` 的来源）**：`conf` 只由"证据量与阈值的距离"决定（>0.10 记 `mid`，否则 `low`），
  **不是**统计意义下的概率；`verdict` 由颜色/肤色/暗区三个占比按冻结规则短路得到。
  `helmet_color_ratio`/`skin_ratio`/`dark_ratio` 就是判定用到的三个证据占比，可自行核对。
- **禁止字段名**：契约 §7「禁止词汇」清单里的那一组结论式词（本文件不重复列出），
  一律不得出现在代码、UI、日志、文档与输出里；`tests/dms/test_web_contract.py` 与
  `tests/dms/test_switches_runtime.py` 会对 `/state` 响应体与快照做断言。

### 5.1 事件日志 `logs/dms_events.jsonl`

一行一 JSON：`{"ts": float, "kind": str, "payload": dict}`，
`kind ∈ {session, switch, fatigue_state, fatigue_face_lost, helmet_verdict, error}`。
写失败只打印一行，**绝不**中断主循环。

---

## 6. 相机与互斥（Calibration Studio）

- 默认源是 `configs/dms.yaml` 的 `camera.source`（默认 `usb:0`）。Calibration Studio 常驻在 **8000** 端口，
  本 demo 的 web 默认走契约 §12 最终值 **8010**（实测空闲），并使用 `--port`/`DMS_PORT` 覆盖；**绝不**使用 8000，也**不**使用 `warn_app` 的既有默认端口（避免撞车）。
- Studio 的相机独占是**它进程内**的机制，跨进程没有任何可查询的锁；因此本 demo 只能"探测式互斥"：
  **打开设备并读首帧**。
- **真机模式协商（契约 §12 C1d，已实测）**：`app/dms/camera.py` 对 `usb:` 源**自己**显式请求
  分辨率 + FOURCC + FPS（**不修改** `app/camera_source.py`，只读复用它打开源的语义）：
  `camera.width x camera.height`（`configs/dms.yaml` 默认 **1280x720**）+ `FOURCC=MJPG` +
  `camera.fps`（默认 30）；**设置顺序为 FOURCC → W/H → FPS**（部分 V4L2 驱动在切换像素格式时会把帧尺寸
  重置回默认，因此宽高必须放在 FOURCC 之后；verifier 独立实测与本机实测顺序一致），随后**回读协商结果**并打印一行：

  ```text
  [dms] camera: usb:0 negotiated 1280x720@30 MJPG (requested 1280x720; source: config)
  ```

  协商不足（设备只给更小的模式）时追加一行 **WARNING 并继续运行**：

  ```text
  [dms] camera: usb:0 negotiated 1920x1080@30 MJPG (requested 3840x2160; source: config)
  [dms] WARNING: camera negotiated 1920x1080 (< requested 3840x2160) — helmet may report many 'unknown'; raise --camera resolution or move closer
  ```

  **硬约束**：绝不为了少出 `unknown` 而动态放宽 `min_person_h_px` / `min_face_h_px` /
  `min_head_area_px` 等像素门控——那等于把 `unknown` 伪装成判断，违反诚实口径。
  真机冒烟判据（契约 §9 R12）：**能出图 + 不崩 + 诚实输出 `unknown`** 即算通过，
  不因 `unknown` 多而判失败。
- **`usb:` 源 open 失败时的病因提示（契约 §3.5 类 2 语义）**：本机驱动在 **open 阶段**就拒绝第二个
  打开者，因此当 `--camera usb:<idx>` 的 open 失败时，除类 1 的用法提示外，还会额外打印类 2 的两行
  "设备很可能被别的进程占着 / 停掉占用者或改用 video:、image:、synthetic"——退出码仍是 **3**、
  ERROR 仍是单行、无 traceback。设备节点本身不存在（如 `usb:9`）时同为国 1 + 用法提示。
- 两类失败都会给出单行结论（退出码 3，无 traceback）：

  ```text
  # 类 1：源根本打不开
  [dms] ERROR: camera source 'video:/tmp/nope.mp4' open failed: cannot open source
  [dms]   use --camera usb:<idx> | rtsp://... | video:<file> | image:<path> | synthetic

  # 类 2：源能打开但读不到首帧（设备被别的进程占着 / 不支持该格式）
  [dms] ERROR: camera source 'usb:0' open failed: no first frame within 8.0s
  [dms]   /dev/video0 is probably held by another process (Calibration Studio uses it by default).
  [dms]   Stop that consumer, or use --camera video:<file> / image:<path> / synthetic.
  ```

- **不确定性（诚实记录）**：这台 UVC 相机是否允许两个进程同时打开**未验证**。因此两种行为都必须正确：
  驱动拒绝 → 走类 2；驱动允许共享 → 正常出图，并在 `--debug` 下打印
  `[dms] camera: opened usb:0 — device allowed shared access (another consumer may be active)`。
- 运行结束（含 Ctrl-C）都会 `cap.release()`、关闭窗口、停止 web 线程并写一条 `session` 事件。

---

## 7. 已知失败模式（必须接受 `unknown`）

| 场景 | 期望行为 |
|---|---|
| 侧脸 / 口罩 / 眼镜反光 → Haar 漏检 | 疲劳状态 `UNKNOWN`（**绝不**伪装成 `NORMAL`） |
| 无近红外相机 + 夜间 / 强逆光 | 眼带/嘴部暗区占比失真；状态多为 `UNKNOWN`；文档与 UI 明确写"不可用" |
| 深色/灰色头巾、棒球帽、发量大 | 头盔多判 `unknown`（这是**正确**行为，不是 bug） |
| 手持黄色/橙色物体举到头附近 | 可能误判 `worn`（颜色启发式的固有缺陷） |
| 亮色中性区域（灰墙、白帽、白发） | 冻结规则会判 `not_worn`（"无安全帽颜色且不暗"）；属启发式边界，已如实记录 |
| 浅肤色 / 低饱和肤色（V≥170、S≤40） | 会同时命中 `white` 低饱和色带，`helmet_color_ratio` 可能为 1.0；冻结规则靠 `skin_ratio > worn_skin_max` 兜住，最终判 `not_worn`（`reason=skin_evidence`） |
| 头部 ROI 完全没有像素（越界被裁空） | `unknown`（`insufficient_evidence`）；零证据时不声称"未佩戴" |
| console `:0` 未接显示器（HDMI-0 disconnected） | 窗口 `IsUnMapped`，人看不到画面（原生 X 程序同样如此）；改用 `gui_display.sh` 选屏或用 Xvfb |
| Haar 在椅子/纹理上偶发误检 | 真机截图里出现过 `face id1` 落在椅背（人脸框为假阳性）；此时眼/嘴阈值未达，状态仍按阈值判 `NORMAL`（`score=0.00`）。HAAR 的已知局限，不宣称"有人脸"等于真实人脸 |
| 运行时切换开关后画面更新的时机 | 开关在**下一个循环边界**生效；真机 1280×720 双路（且与 t3 并发跑时）实测约 2–6fps，画面可见切换可能滞后 ~0.5–1s，`[dms:state]` 每 2s 一行因此用轮询日志判定更可靠 |
| 背对相机 / 侧脸 / 头盔只露边缘 | `unknown` |
| 人体框被画面边缘截断 / 人太小 | `unknown`（`person_too_small` / `head_roi_clipped` / `head_too_small`） |
| 刚出现的 track（`age_frames < 5`） | `unknown`（`warming_up`，多数票还没攒够） |
| 疲劳 `UNKNOWN` 期间若分数仍高 | 按契约 §3.1.3，只有"回落 ≤ exit"才回到 `NORMAL`；此时会**停留在 `UNKNOWN`**（宁可不判，也不误报） |
| 眼镜反光/单侧光 | 眼带暗区占比阈值（0.55）**未标定**，只用于演示 |

---

## 8. 权重与依赖决策记录（本次**没有**下载任何权重）

- 本次实现**只使用仓库里已有的**资源：`/usr/share/opencv4/haarcascades/*.xml`（Haar）与
  `checkpoints/yolov8n.pt` / `models/tensorrt/yolov8n_person_fp16.engine`（人体检测，`ultralytics` 8.3.40 自带运行时）。
- **没有** `pip install` 任何新依赖；**没有**下载/生成任何权重文件；没有创建下载脚本。
  理由：`t1` 契约 §3.3 的 E1–E5 增强候选在契约中全部标注为【推断·未联网核实】，
  必须由 captain 逐条核实 URL/体积/许可证并取得用户批准后才能下载；t2 的验收条目本身也写明
  "未下载任何模型权重"。因此**当前实际生效的路径就是纯离线主路径**：
  `fatigue.backend=haar_proxy` + `helmet.backend=heuristic`。
- **人体模型预加载（仅在头盔启用时）**：`app/dms_app.py` **只当 `helmet.enabled=true`** 时才在**创建窗口之前**
  加载人体检测器（`helmet.person_model` → 回退 `checkpoints/yolov8n.pt`）——关闭状态下不付 torch/TRT 的
  启动与显存代价，也**不写**任何 `kind="error"` 事件（不给已关闭的路留误导日志）。
  * 若启动时关着、之后在运行时打开头盔：模型会在那一刻加载（打印 `[dms] helmet: loading person model ...`），
    显示模式下可能阻塞主循环数秒（真机实测 5–10s）；`scripts/run_dms_demo.sh` 的
    `LD_PRELOAD=libGLdispatch.so.0` 用于规避 Jetson 上 GTK+TRT 的 static-TLS 问题。
  * 预加载只决定"模型何时载入"，不影响"真停止"：关闭头盔时一律不进入推理分支，`infer_count` 仍然冻结。
- **GTK + torch/TRT 的 static-TLS 规避**：`scripts/run_dms_demo.sh`、`scripts/run_visual_hub.sh`
  以及桌面入口 `scripts/run_visual_hub_gui.sh` 一致地在 `exec` 前
  `export LD_PRELOAD=/lib/aarch64-linux-gnu/libGLdispatch.so.0`（Jetson 上插件
  `dlopen` "cannot allocate memory in static TLS block" 的既有手法）。
- **默认端口**：契约 §12 最终值 **8010**（实测空闲；8000 属总控 Visual Hub，`warn_app` 的既有默认端口也不同，均不用）。
  `--port` / `DMS_PORT` 可覆盖；被占用时报错退出 5，不抢占、不静默换端，
  也**不会**去监听别的端口（t3 可断言 `:8011–:8029` 无监听）。
- **可选的权重下载尝试（本轮实测结论：不做）**：captain 允许在离线主路径完成后"最多尝试一次"
  候选权重（时间盒 20 分钟），前提是能自行核实 URL 可达 + 许可证明确 + 能加载 + SHA-256 可记录。
  实测本机（Jetson）出网情况：`https://github.com` → 200、`https://pypi.org` → 200，
  但 **`https://huggingface.co` DNS 解析到一个不可达地址、连接超时（`curl: (28)`、http=000）**；
  而 E1/E3 候选的头盔权重只在 HuggingFace（E2 的 Roboflow 需要账号导出）。
  因此在时间盒内**无法核实**候选，本轮**不下载任何权重**，把候选列为"后续可选"。
  将来若要启用，除下载之外还必须：把权重放 `models/onnx/` 或 `checkpoints/`（仅新增权重文件本身、不入 git）、
  在本文档记录 URL/许可证/体积/SHA-256、并**实现** `helmet.backend: yolo`（今天选它一律报错退出 2）。
- 如果将来批准下载第三方头盔模型：把权重放到 `models/onnx/` 或 `checkpoints/`（仅新增权重文件本身），
  在 `docs` 里补 URL / 许可证 / 体积 / SHA-256，并把 `helmet.backend` 切到 `yolo` 的实现；
  **未实现的后端值必须报错退出（退出码 2），禁止静默降级**（当前 `--helmet`/`fatigue.backend` 已如此）。
- 本机实测：`import app.warning.*` 在**非仓库工作目录**下会被
  `.pth` 注入的 `third_party/EfficientTAM` 目录里的 `app.py` 抢占（`ModuleNotFoundError: gradio`）。
  `app/dms_app.py` 与既有 `app/warn_app.py` 一样在导入前把**仓库根目录插到 `sys.path[0]`**，
  所以从仓库根运行（脚本或 `.venv/bin/python -m`）不受影响；本 demo **不修改** `.pth` 或 `third_party/**`。

---

## 9. 关键配置项（`configs/dms.yaml`）

| 分组 | 键 | 作用 |
|---|---|---|
| `camera` | `source` / `width` / `height` / `fps` / `open_timeout_s` | 源、真机协商请求的分辨率（默认 **1280x720**，`usb:` 源还会显式请求 MJPG + fps）与首帧等待时间（超时 → 退出码 3） |
| `runtime` | `state_dump_interval_s` | display 模式 `[dms:state]` 行间隔（默认 2.0s） |
| `fatigue` | `enabled` / `backend` / `haar_dir` | 开关、后端（仅 `haar_proxy`）、Haar 目录（空 = 自动解析） |
| `fatigue.face` | `scale_factor` / `min_neighbors` / `min_size_px` / `min_face_h_px` / `face_lost_warn_s` | 人脸检测与可信度门控 |
| `fatigue.roi` | `eye_x/eye_y/mouth_x/mouth_y` | 眼带/嘴部 ROI（相对人脸框的归一化坐标） |
| `fatigue.signal` | `eye_dark_thresh` / `mouth_dark_thresh` / `*_enter` / `cascade_crosscheck` | 暗区阈值与进入阈值；交叉验证只写调试字段 |
| `fatigue.state_machine` | `window_frames` / `ema_alpha` / `w_*` / `warn_enter` / `alarm_enter` / `exit_enter` / `*_confirm_frames` / `warn_hold_s` | 去抖与迟滞（未标定，可调） |
| `helmet` | `enabled` / `backend` / `person_model` / 人体检测器分数阈值键（契约 §4.10 的冻结键名） / `person_iou` / `person_imgsz` | 开关、后端、人体模型（TRT 失败自动回退 `checkpoints/yolov8n.pt`） |
| `helmet.head_roi_*` | `head_roi_frac` / `head_roi_w_frac` / `head_roi_min_px` | 头部 ROI 几何 |
| `helmet` 门控 | `roi_margin_px` / `min_person_h_px` / `min_head_area_px` | 三道门控（不满足 → `unknown`） |
| `helmet.rule` / `colors` / `skin` | 阈值、色带、肤色带 | 冻结判定规则与证据 |
| `helmet.smooth` | `k_verdict_frames` | 按 `track_id` 多数票窗口 |
| `web` | `port` / `host` | 默认 **8010**（契约 §12 最终值；8000 属总控 Visual Hub、`warn_app` 的默认端口也不同，均不用） |
| `ui` | `panel_x` / `panel_y` / `line_h` / `font_scale` / `hotkey_*` | **UI v2：布局键 `panel_x`/`panel_y`/`line_h`/`font_scale` 已 accepted-but-ignored（仍被读取以避免 KeyError，但不再参与画面布局——布局改由画布尺寸自动堆叠，见 §3.6）；`hotkey_fatigue`/`hotkey_helmet` 继续生效；新热键 `d` 写死在代码常量里（yaml 不得改）** |
| `logger` | `enabled` / `dir` / `events_file` | 事件日志位置 |

> **UI v2 的配置行为变更（用户可感知，不得视为静默失效）**：`configs/dms.yaml` 的 `ui.panel_x`、`ui.panel_y`、`ui.line_h`、`ui.font_scale` 四个键**已变为 accepted-but-ignored**——仍会被读取（避免 `KeyError`），但**不再参与画面布局与字号**：布局位置与字号一律由画布宽度分档自动决定（§3.6），调试块由可用高度裁剪。`ui.hotkey_fatigue` / `ui.hotkey_helmet` **仍然生效**；新热键 `d`（调试层）**写死在代码常量里**，`configs/dms.yaml` 本轮不得改动，因此 yaml 里看不到它。
| `notices` | `cn` / `ascii` | 诚实标注（web 与 display 各一份，语义一致） |

---

## 10. 验收命令（可复现）

```bash
cd /home/seeed/workspace/seg_demo
PY=.venv/bin/python

# A1 单测（不依赖相机、不触碰设备节点）
$PY -m pytest tests/dms -q -p no:cacheprovider          # af218ab 基线实测 55 passed（UI v2 新增 test_hud_layout.py 后应 ≥55 且全绿）
grep -rn "VideoCapture\|/dev/video" tests/dms ; echo "grep_exit=$?   # 期望 1（无匹配）"

# A2 开关"真停止"（零相机，确定性）
$PY -u app/dms_app.py --mode web --headless --camera synthetic --port 8010 --max-frames 100000 > /tmp/dms_web.log 2>&1 &
DMS_PID=$!; sleep 4
curl -s localhost:8010/state | $PY -c 'import json,sys;d=json.load(sys.stdin);print("C1",d["dms"]["fatigue"]["infer_count"],d["dms"]["helmet"]["infer_count"],d["dms"]["fatigue"]["state"])'
curl -s -X POST localhost:8010/api/dms_config -H 'Content-Type: application/json' -d '{"fatigue_enabled":false}' | $PY -m json.tool | head -20
sleep 3; curl -s localhost:8010/state | $PY -c 'import json,sys;d=json.load(sys.stdin);f=d["dms"]["fatigue"];print("C2",f["infer_count"],f["state"]);assert f["state"]=="DISABLED"'
sleep 3; curl -s localhost:8010/state | $PY -c 'import json,sys;d=json.load(sys.stdin);f=d["dms"]["fatigue"];print("C3",f["infer_count"])'
curl -s -X POST localhost:8010/api/dms_config -H 'Content-Type: application/json' -d '{"fatigue_enabled":true}' >/dev/null
sleep 3; curl -s localhost:8010/state | $PY -c 'import json,sys;d=json.load(sys.stdin);f=d["dms"]["fatigue"];print("C4",f["infer_count"],f["state"]);assert f["infer_count"]>0'
curl -s localhost:8010/frame -o /tmp/dms.jpg && file /tmp/dms.jpg
tail -n 20 logs/dms_events.jsonl
kill $DMS_PID

# A5 相机不可用 → 明确报错而非崩溃（零相机）
set +e
$PY -u app/dms_app.py --mode web --headless --camera image:/tmp/definitely_missing.jpg --port 8010 --max-frames 30 > /tmp/dms_badcam_a.log 2>&1; echo "exit_a=$?   # 期望 3"
$PY -u app/dms_app.py --mode web --headless --camera video:/tmp/definitely_missing.mp4 --port 8010 --max-frames 30 > /tmp/dms_badcam_b.log 2>&1; echo "exit_b=$?   # 期望 3"
grep -q Traceback /tmp/dms_badcam_a.log && echo "FAIL: traceback" || echo "OK: no traceback"

# A7 端口冲突 → 退出码 5
$PY -c "import socket,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',8010));s.listen(1);time.sleep(20)" &
sleep 1; set +e
$PY -u app/dms_app.py --mode web --headless --camera synthetic --port 8010 > /tmp/dms_port.log 2>&1; echo "exit=$?   # 期望 5"; cat /tmp/dms_port.log

# A6 双模式（display 需要 X；无 X 用 web）
DISPLAY=:0 GUI_DISPLAY=:0 timeout 25 $PY -u app/dms_app.py --mode display --camera synthetic --max-frames 300 > /tmp/dms_display.log 2>&1
grep -c "\[dms:state\]" /tmp/dms_display.log
./scripts/run_dms_demo.sh --mode web --headless --camera synthetic --max-frames 120

# A8 非回归 + 只新增路径
$PY -m pytest tests -q -p no:cacheprovider                       # 期望 2 failed / >=161 passed（两个既有 placement 失败）
git status --porcelain | grep -v '^??'                            # 既有被跟踪文件必须与改动前一致
git diff --stat -- app/warn_app.py app/web_stream.py app/warning backend frontend third_party README.md scripts configs   # 期望为空
```

> 说明：`--camera synthetic` 与缺失源（`image:`/`video:`）让 A1/A2/A5/A7 完全**不碰相机、不动 Studio**。
> 上面的端口用契约 §12 的最终值 **8010**；显式 `--port` 也可覆盖，但请避开 `warn_app` 的既有默认端口，
> 以免同时跑另一个 demo 时撞车。

---

## 11. 与 `t1` 冻结契约的偏差记录（诚实列出）

### 11.1 本次 UI 精简的取舍记录（UI v2）

**一句话**：默认界面从"把全部遥测都当默认信息"改成**单槽位**（每个语义只出现一次），把 12 类调试字段整体搬进调试层；**没有删除任何字段、没有任何检测语义变化**。

| 项 | 旧版（af218ab） | UI v2 | 去向 / 依据 |
|---|---|---|---|
| 相机、人脸可用性 | 只在 web `#bar` 里（画面无） | HUD 行 1 `CAM:*  FACE:*`；web `#status` | 新增语义槽位（规格 §3.2.1 / §4.5） |
| 疲劳状态 | 画面叠加行 + 控件行 `state=` + web `#ctl` + `#bar`（最多 4 次） | 只 HUD 行 2 / `#fatigueState`（**1 次**） | 单槽位（§3.6.1） |
| 头盔结论 | 画面逐人 + 控件行 + web `#ctl` 逐人列表 + `#bar` | 只 HUD 行 3 / `#helmetState`（**1 次**），结论用保守聚合（§3.3） | 逐人明细移到框短标签与调试层 |
| 开关 ON/OFF | 画面叠加、复选框行、状态栏各一份 | 只在控件行 / checkbox 勾选态 | 不再重复 |
| `score` / `eye_dark_ratio` / `mouth_open_ratio` | 默认永远显示 | **调试层** `D3` 行 / `#dbgScore`·`#dbgEye`·`#dbgMouth` | 字段名保留，不再当结论展示 |
| `infer_count` / `last_infer_fidx` / `last_infer_ms`（疲劳、头盔各一组） | 默认永远显示 | **调试层** `D4` / `D6` 行与对应 `#dbg*` 元素 | 同上 |
| 顶层 `fidx` / `fps` / `mode` / `source` | 默认永远显示 | **调试层** `D1` / `D2` 行与 `#dbgFidx`·`#dbgFps`·`#dbgMode`·`#dbgSource` | 同上 |
| `persons` 明细 | 默认永远显示 | **调试层** `D7` 逐人（最多 3 行）+ `D8  DBG p +N more`；web `#dbgPersonList` + `+N 更多` | 多人时高度可控 |
| `verdict_counts` | 默认永远显示 | **调试层** `D5` 行 `wn/u=…` / `#dbgCounts` | 同上 |
| web `#bar`（af218ab 实测 **13 项**） | 默认可见 | **删除** | 状态改由 `#ctl`/`#status` 承担，避免第 3 份重复 |
| 画面内复选框（web） | 画在推流画面上但点不动 | **删除**；开关只由页面 checkbox 承担 | 消除"画出来却不可交互" |
| notice（诚实标注） | display 画面 + web 推流画面 + `#notice`（同屏 2–3 次） | display 底部条 1 次、web `#notice` 1 次 | 每界面各 1 次（5 处落点见 §3.4.3）；推流画布不再画 notice 是**允许**的，因为第 2 处落点就是页面 `#notice` |

**为什么精简不降低可验证性**：调试字段只是**默认不显示**，不是被删除，四条取回路径都还在——

1. **窗口/页面内打开调试层**：display 按 `d`（或 `--debug` 启动）→ HUD/控件/notice 之外出现 `DBG` 底板，`D1..D8` 覆盖上表全部字段；web 同理打开 `#debug`（`--debug` 时初始即展开）。
2. **`[dms:state]` 转储**：display 模式每 2.0s 向 stdout 打印一行与 `/state` 的 `dms` 子树同构的 JSON，`score`/`eye_dark_ratio`/`mouth_open_ratio`/`infer_count`/`last_infer_fidx`/`last_infer_ms`/`persons`/`verdict_counts` 全部在内（间隔由 `runtime.state_dump_interval_s` 控制）。
3. **`GET /state`**：字段名与嵌套结构**未变**（本次改动不触碰 `state.py` 与路由），任何既有脚本/断言照旧可用。
4. **事件日志 `logs/dms_events.jsonl`**：一行一 JSON，未改动。

**本轮取代/收窄的条款（r3 逐条对照）**：

| ID | 被取代/收窄的旧条款 | 本轮口径 |
|---|---|---|
| S8 | 旧契约 §2.2「既有 `tests/**` 一个字节都不许改」 | **收窄为**仅 `tests/dms/test_render_overlay.py` 与 `tests/dms/test_web_contract.py` 允许改写（UI v2 改了画面文本与页面骨架，不更新这两份断言就无法验收）；其余既有 `tests/**`（含 `tests/placement/**`）仍一字节不许改 |
| S9 | 旧契约 A8「变更集必须全部为新增路径」 | **取代为**变更集必须 ⊆ 本轮 7 个授权路径；允许修改其中 6 个既有文件，唯一新增文件是 `tests/dms/test_hud_layout.py` |
| S11 | 旧契约 §6.1「`--mode web`：画面叠加渲染在推流画布内」 | **显式取代**为：推流画布 = 干净画面 + 检测框与短标签；不得有 HUD 像素文字、画面内复选框、notice 条；状态与开关全部由页面 DOM 呈现（见 §3.4.2） |

**其他取舍**（均已在 §3 写明）：`ui` 的布局键 `panel_x`/`panel_y`/`line_h`/`font_scale` 变为 **accepted-but-ignored**（`configs/dms.yaml` 属不得改动清单，键保留但不参与布局，布局改由画布尺寸自动堆叠）；热键 `d` 写死在代码常量里，不新增 yaml 键；`WEB_MAX_W = 960` 保持不变（画面已无文字，缩放不再影响可读性，推流带宽/CPU 无变化）；紧凑档（`w<480` 或 `h<360`）调试行按可用高度截断，逐人明细在极小画布上可能看不到，但字段仍可由 `/state` 与 web `#debug` 取回。

---
> **端口权威链（避免误判为偏差）**：captain 的最终裁决是 **默认端口 = 8010**
> （理由：8000 属总控 Visual Hub；`warn_app` 的既有默认端口与本 demo 不同，避免撞车）；
> 本文件所有端口取值以契约 §12 C1a 的最终值 **8010** 为准（历史表述以契约为准）。
> **本实现取 8010**（`configs/dms.yaml: web.port`），`--port` / `DMS_PORT` 可覆盖，
> 且验收命令**一律显式传 `--port`**，因此 t3/t4 的结论与默认值无关。
> 下面这张表只列仍然存在的实现级取舍；端口一项不算偏差。

| # | 契约 | 实际实现 | 原因/证据 |
|---|---|---|---|
| 1 | 信号先 `cv2.equalizeHist` 再按阈值判暗（§3.1.2） | 直接按**原始灰度**阈值判暗（= 契约信号表的定义式） | 本机 OpenCV 4.5.4 实测：`equalizeHist` 会把**常量 ROI**（全黑/全白）都映射为 255，"全黑 → 1.0 / 全白 → 0.0"的冻结期望不再成立；保持定义式才能保持可解释性。逆光不可用的限制在文档与 UI 中已如实标注 |
| 2 | `UNKNOWN -> NORMAL` 仅当 `score_ema <= exit_enter`（§3.1.3） | 严格照此实现；因此"分数仍高时的 `UNKNOWN`"会停留（见 §7 末行） | 选择"宁可不判，也不误报"；如要改成 UNKNOWN→DROWSY_WARN，需更新契约版本 |
| 3 | `app/dms/*.py` 最小集合 | 未新增 `app/dms/` 之外的文件；`FrameResult` 放在 `render.py` | 保持 inScope 路径集合不变 |
| 5 | §6.4 未规定初始窗口尺寸与坐标空间 | 建窗后 `cv2.resizeWindow(win, W, H)` 使窗口与画布 1:1；hit-test 对"原始坐标/换算坐标"两种解释都用同一份矩形试一次 | 契约 R6 已把"窗口缩放导致 hit-test 偏移"列为风险；Xvfb 实测窗口被缩到 320x240 时，后端已把坐标换算回画布空间，仅按一种解释会漏点 |
| 6 | §4.10 的 `camera.width/height` 未被既有 `open_source()` 使用 | 本模块在打开真实设备后 best-effort `set(FRAME_WIDTH/HEIGHT)`，并把**实际**分辨率打印出来 | 否则 yaml 里的 1280x720 是死配置；驱动可能只给最接近的模式，所以必须如实打印实际值（实测 usb:0 → 1280x720） |
| 4 | 头盔冻结规则：无彩色且不暗 → `not_worn`（§3.2.3） | **有有效 ROI** 时严格照此；ROI 为空（零像素证据）时返回 `unknown`/`insufficient_evidence` | 冻结规则隐含"拿到了一张有效头部 ROI"这一前提；零证据下声称"未佩戴"属于无根据结论，与"门控不满足一律 unknown、绝不猜测"的原则冲突 |

---

## 12. 现场记录（本次实现时实测）

- `resolve_haar_dir()` → `/usr/share/opencv4/haarcascades`（`cv2.data` 不存在，已按契约规避）。
- **真机模式协商实测（§12 C1d）**：默认请求 1280x720 → 设备回报
  `negotiated 1280x720@30 MJPG (requested 1280x720)`（无 WARNING）；把配置临时改成
  3840x2160 再跑 → 设备只给 1920x1080，打印
  `negotiated 1920x1080@30 MJPG (requested 3840x2160)` + 上面那行 WARNING，**进程继续、
  `/health` 正常、不退出**（协商不足只告警）。
- **真机 usb:0 实测（verifier 口径确认：Studio 只占 8000 端口，不占相机）**：`/dev/video0` 空闲；
  按 `camera.width/height` 请求 1280×720 成功（启动日志与 `/state` 都显示 1280x720）；
  真实画面下 Haar **能检出真人脸**（`face_present=true`、`eye_dark_ratio≈0.31`、`mouth_open_ratio≈0.06`
  → `score=0.00`、状态 `NORMAL`），头盔路对人体框输出 `persons=1` + 头部 ROI + 三值判定。
- **修掉一个只在真机出现的崩溃（重要）**：`cv2.CascadeClassifier.detectMultiScale` 在**有检出**时返回
  ndarray，而 `face.py` 原先写了 `for face in faces or ():` → numpy 布尔求值歧义异常
  （`ValueError: The truth value of an array with more than one element is ambiguous`），
  真机一旦有人脸就会退出（合成帧无人脸所以单测没暴露）。已改为 `len()` 判空（`cascade_hits` 同修），
  并补了 3 个回归单测（ndarray / 空 ndarray / `()` / `None` 四种返回形态）。
- **两个消费者实测**：占位进程先取流后，本机（cv2 + CAP_V4L2）第二个打开者在 **open 阶段**就失败 →
  我们给出 `open_failed` 单行错误 + `exit 3` + 无 traceback。契约要求的另一条路径"能 open 但取不到首帧"
  （V4L2 允许重复 open、占用在 REQBUFS/STREAMON 才失败）由**确定性单测**覆盖：打桩的假采集对象
  `isOpened()=True` 但 `read()` 永远失败 → `CameraUnavailable(reason="no_first_frame")` 且必须释放对象。
- 真机 USB 相机（`usb:0`，Studio 未占用时）：`camera: usb:0 1280x720`，web/headless 下
  `cam_ok=true`；画面里没有人时 `fatigue.state=UNKNOWN`（诚实）、`helmet.persons=[]`
  （没有人体框就不猜）。
- 帧率实测（两路都开，`--debug` 的 `last_infer_ms`；**数值随系统负载波动**，t3 并发跑时明显变慢）：
  * 真机 `usb:0` 1280×720：安静时约 5fps（疲劳 Haar ≈145ms/帧）；与 t3 并发时约 2fps（Haar ≈400–430ms/帧）；
  * 合成源 640×480：约 14–18fps（疲劳 ≈9–19ms/帧）；
  * 头盔 YOLO ≈17–35ms/帧（1280×720 输入，`person_imgsz=640`）。
  旧记录：
  * `camera.width/height = 1280x720`：**fps≈5.4**，疲劳 Haar **≈145ms/帧**（大头），头盔 YOLO ≈23ms/帧；
  * `camera.width/height = 640x480`：**fps≈11.4**，疲劳 Haar ≈60ms/帧；
  * `--camera synthetic`（640x480，无人脸/无人体的确定性画面）：**fps≈18**，疲劳 ≈9–19ms/帧。
  这与契约 R5 一致：**疲劳 Haar 是全 1280x720 帧上的成本大头**；需要更高帧率时改
  `camera.width/height`（或头盔路的 `helmet.person_imgsz`），**不改契约字段**。
- 设备被别的进程占用时（占位进程先打开 `/dev/video0`）：本机驱动在 `open()` 阶段就**拒绝第二个打开者**，
  于是走"类 1"：单行错误 + `exit 3`、无 traceback（实测 `exit=3`）。
  "类 2（能打开但读不到首帧）"的文本保留给"能打开却不出图"的设备。
- web UI 的验证方式（本次实测）：`curl` 取 `GET /`（含两个冻结 id 与 notice）、
  `GET /stream`（真实 `multipart/x-mixed-replace`，4s 抓到 ≈3.4MB 帧）、`GET /frame`（真实相机 JPEG）、
  `POST /api/dms_config` 后立刻 `GET /state` 回读一致；并从**另一台机器**
  （`http://100.109.1.72:8010/`，跨网访问）重复了同一组请求，确认 LAN 浏览器可达。
  本次在一台无法直连该 tailnet 地址的浏览器环境里尝试过渲染级验证（连既有 Studio 的 8000 也失败，
  说明是浏览器侧网络/代理限制），因此**渲染级"肉眼可见"验证建议在能访问该地址的浏览器上做一次**：
  浏览器打开 `http://100.109.1.72:8010/`，应当看到实时 MJPEG 画面 + 两个复选框，勾选即生效。
- `--debug` 下打开 `usb:` 会打印
  `[dms] camera: opened usb:0 — device allowed shared access (another consumer may be active)`：
  因为跨进程无法判断是否真有别的占用者（契约 H4），所以这句话带 "may be" 的诚实限定。
- TRT 人体引擎加载 ≈ 2.8s，首次推理 ≈ 1.0s，之后单帧 ≈ 25–30ms（640×480 输入，`--debug` 可见
  `last_infer_ms`）；两者都远低于 33ms 的目标（契约 R5）。
- 颜色证据实测（OpenCV BGR→HSV/YCrCb）：纯黄 `BGR(0,255,255)` → `H=30,S=255,V=255`（黄带命中，肤色带不命中）；
  低饱和肤色 `BGR(170,180,200)` → `H=10,S=38,V=200`（`S<70` 不命中黄/橙带，但 `S<=40 & V>=170` **命中 white 低饱和带**，故
  `helmet_color_ratio=1.0`，最终靠 `skin_ratio=1.0 > worn_skin_max` 判 `not_worn`）；暗灰 `BGR(30,30,30)` → `V=30`（暗区）→ `unknown`。
- display 模式真机开窗、web 模式浏览器可访问的证据见 t2 的任务输出与 t3 的独立验证报告。