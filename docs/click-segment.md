# Click-to-Segment（点击分割）— Segment 页

实时 RGB 分割 + 跟踪的最简闭环：**在画面上点击一个目标 → 出现分割掩膜并实时跟随；
目标移出画面/被遮挡时保留“淡影”，重新出现后自动恢复分割**，全程无需重新点击。

## 交互（Segment 页）

- 左键点击 = 正样本点（首次点击选定目标，之后为精修点）
- 右键点击 = 负样本点（排除误分割区域）
- 按钮：`Start segment` / `Stop` / `Clear target`（清除后重新选目标）
- 状态行：`TRACKING` / `LOST · ghost retained · auto-resume armed`（模板已武装）/
  `LOST · ghost retained · auto-resume off (mask too broad)`（掩膜过宽，未武装）/
  `LOADING` / `IDLE`
- 状态行第二行提示：`target_hint`（掩膜过大时引导右键负点）或 `relock_note`
  （自动恢复被身份校验拦截时提示“疑似其它物体”）

## 架构

```
浏览器 Segment 页 (canvas 1280×720)
   │ 点击(归一化坐标)            │ 轮询 250ms
   ▼                            ▼
POST /api/segment/click   GET /api/segment/status
   │                            ▲
   ▼                            │
SegmentService（单例，后台推理线程，只消费 CameraManager.get_latest_frame()）
   │ 单一摄像头（无第二个 VideoCapture）
   ▼
EfficientTAMCameraPredictor（vendored: third_party/efficient_track_anything）
   ├─ select_target()  → load_first_frame + add_new_points_or_box（首次点击）
   ├─ add_point()      → 注册当前帧 + add_new_points_or_box + propagate_in_video_preflight()
   └─ track()          → 流式逐帧跟踪（memory bank，~7 帧滑动窗口）
        → 掩膜 → cv2.findContours + approxPolyDP → 1280×720 多边形 JSON
```

> **关键工程修复：add_point 必须先注册当前帧。** vendored 流式 fork 的 `track()` 只做
> `frame_idx += 1` 并流式取特征，**从不把当前帧写入 `condition_state["images"]`**（该列表
> 只含 `load_first_frame` 的 frame 0）；而 `add_new_points_or_box(frame_idx=N, N≥1)` →
> `_run_single_frame_inference` → `_get_image_feature` 缓存未命中 → `images[N]` 越界
> （`list index out of range`）。后果是**第 0 帧之后的任何加点都会崩溃**——自动再锁定与
> 用户右键精修全部失效（设备日志累计 2986 次，离线 demo 同样崩溃）。修复在 wrapper
> `backend/app/segment/model.py`：`prepare_frame_for_interaction()` 按 `perpare_data` 把当前帧
> 补进 `images`，加点后再调 `propagate_in_video_preflight()` 把新掩膜合成进记忆（否则下一次
> `track()` 不会采用它）。**未改动 vendored 模型代码与权重。**

## 目标身份：质量门 + 验证式再锁定

点击“手拿着的物体”时，SAM 类单点提示会把**包含点击点的整个连通前景**（手+物体）当成
掩膜——目标身份从点击那刻起就是“手”，换任何东西掩膜都贴手。系统不做魔法，而是
**检测 → 提示 → 提供精修工具 → 只在可验证时自动恢复**：

- **掩膜质量门**：面积比 > `MASK_QUALITY_POOR_AREA_RATIO`(0.30) 或 bbox 覆盖任一帧边 >
  `MASK_QUALITY_POOR_BBOX_RATIO`(0.60) → `target_quality="poor"`：状态行提示“掩膜过大，
  可能包含手或背景——请右键加负点排除”，且 `resume_armed=false`（不武装自动恢复）。
- **身份模板（锚定 + 谨慎刷新）**：模板 = 掩膜 bbox 灰度裁剪（48px，保宽高比）；构建前按
  `TEMPLATE_ERODE_PX`(3) 腐蚀掩膜以剔除目标/背景混合轮廓。模板**锚定在首个干净（ok）掩膜**，
  之后仅在候选样本与当前模板相关度 ≥ `TEMPLATE_REFRESH_CORR`(0.8) 时才刷新——避免长时间
  跟踪中掩膜漂移把身份带偏。身份比较用**固定尺寸（48×48）皮尔逊相关**（`_templates_corr`），
  目标重现时宽高比略有变化也不会被判为不匹配。
- **验证式再锁定**（LOST 期间每 `RESEED_INTERVAL_S`(1.5s) 尝试）：模板匹配峰值分数 ≥
  `RESEED_THRESHOLD`(0.65) 且**峰值显著性 PSR ≥ `RESEED_MIN_PSR`(4.0)**（强峰本身即是身份证据）
  → 在峰处**整包重选（reset + 重新锚定）**并立即回到 `TRACKING`。重选后由模型自己继续跟踪；
  换来的物体若与模板外观不符、匹配分数不足，就不会触发，保持 LOST 与淡影。
- **track 恢复**：LOST 期间模型自身输出的掩膜即被视为目标重现——模型记忆/注意力就是身份
  权威（外来物体不会输出掩膜），按两帧滞回直接恢复，不再做刚性模板复核。

## 丢失 / 重现语义（状态机 SegmentStateMachine)

- `IDLE --(点击)--> TRACKING`
- `TRACKING`：掩膜面积比 < `LOST_AREA_RATIO`(0.001) 连续 ≥ `LOST_FRAMES`(6) 帧 → `LOST`
  （滞回防闪烁；期间继续逐帧 track，记忆库保留）
- `LOST`：overlay 显示**最后有效掩膜**的灰白淡影（ghost）
- `LOST` 中掩膜面积比 > `RESUME_AREA_RATIO`(0.002) 连续 ≥ `RESUME_FRAMES`(2) 帧 → `TRACKING`
  （自动恢复，无需重新点击；须通过上面的身份校验）
- `Clear target` / `Stop` → `IDLE`

阈值集中在 `backend/app/segment/config.py`。

## 模型与部署

- 模型：**EfficientTAM-Ti @512×512**（官方权重 `efficienttam_ti_512x512.pt`，~72MB）
- 权重查找顺序：`backend/app/segment/checkpoints/` → 仓库根 `checkpoints/`；
  缺失时 `bash scripts/download_efficienttam.sh`（从 HuggingFace）
- 推理代码：vendored 精简版 GPIOX/EfficientTAM_real_time（流式
  `efficienttam_camera_predictor.py`，无 CUDA 扩展依赖），位于
  `backend/app/segment/third_party/efficient_track_anything/`（随仓库提交；
  仓库根的 `third_party/` 已被 .gitignore 排除，勿在此放补丁）
- 推理配置：`torch.inference_mode()` + `bfloat16` autocast；
  `compile_image_encoder=false`（JetPack torch 无 triton）
- `_C`（编译扩展）不可用时自动安装 numpy 兜底（fill_holes 关闭，正常不会触发）

## 后端 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/segment/status` | 状态 + 多边形 + ghost + fps + mask_area + `target_quality`/`target_hint`/`relock_note`/`resume_armed` |
| POST | `/api/segment/start` | 开始（相机未运行时 409 SEGMENT_CAMERA_NOT_RUNNING） |
| POST | `/api/segment/stop` | 停止并清除目标 |
| POST | `/api/segment/click` | `{x,y ∈[0,1], label∈{0,1}}`：无目标→select；有→add |
| POST | `/api/segment/clear` | 清除目标回 IDLE |

错误码：`SEGMENT_NOT_STARTED / SEGMENT_LOADING / SEGMENT_ERROR /
SEGMENT_CAMERA_NOT_RUNNING / SEGMENT_NO_TARGET / SEGMENT_TOO_MANY_POINTS(16) /
SEGMENT_INVALID_COORDINATES(422) / SEGMENT_NO_FRAME`。

## 前端

- `frontend/src/pages/SegmentPage.tsx`：预览 + 点击 + 覆盖层绘制（含 `resume_armed` /
  `relock_note` / `target_hint` 状态行提示）
- `frontend/src/api/segment.ts`：类型化 API client
- `frontend/src/App.tsx`：导航新增 `Segment` step

## 验收

- 后端单测：`backend/tests/segment/` — **63 项**（状态机 / 多边形 / 服务 / API / 模板 /
  交互帧注册，FakeModel/FakePredictor，无权重下载）；全量 `backend/tests` **297 项全绿**。
- 离线真模型验收：`scripts/segment_offline_demo.py`，合成视频驱动真实模型，两个场景：
  - **场景 A（目标重现）**：`tracking(1-169) → lost(170-240 淡影) → tracking(241-409 自动恢复) → lost(410-459 离场)`，PASS。
  - **场景 B（换物体）**：`tracking(1-169) → lost(170-330) → tracking(331-409) → lost(410-459)`，
    替换物体（240-329）全程保持 LOST、不误锁，原目标在 331 被重锁定，PASS。
    成功再锁定日志：`auto re-lock at (0.592, 0.506) score=0.82 corr=0.91`。
  - 证据产物：`/debug/segment_offline/` 与 `/debug/segment_offline_swap/`（逐帧 `states.jsonl`
    + 每 25 帧叠加图）。
  - 两场景日志 `list index out of range` 计数均为 **0**（修复前设备上累计 2986 次）。
- 设备实测（真机）：EfficientTAM-Ti @512 + bf16，加载 ~5s（含预热）、
  **单目标跟踪 75.7ms/帧 ≈ 13.2 FPS**、显存 ~0.21GB。

### 历史结论更正

此前本文档记录“离线验收中 frame 241 由模板再锁定恢复”属于**错误归因**：当时 `add_point`
因 `condition_state["images"][frame_idx]` 越界而 100% 崩溃（设备日志 2986 次
`auto re-lock failed: list index out of range`，离线 demo 同样崩溃），241 帧的恢复实为
**模型记忆传播的自然恢复**触发了状态机 `recovered`。模板再锁定直到本轮修复后才首次真正
生效（见场景 B 的 `auto re-lock at ... score=0.82 corr=0.91`）。

## 已知边界

- 单目标（多目标、YOLO 自动检测、保存、视频文件输入等在最小化范围内已删除）
- **手持小目标**：单点点击无法从物理上分离接触中的“手”与“物体”（SAM 类模型的能力边界）。
  请在手上/背景右键加负点精修；掩膜收紧后 `target_quality` 转 ok、`resume_armed=true`，
  才具备自动恢复能力。
- **外观相似的新物体**无法区分（身份校验只看外观）：若替换物与原目标的外观相关度 ≥0.6，
  仍可能被当作原目标恢复。
- **旋转/尺度变化过大**时模板相关度下降，可能不恢复；重新点击即可。
- 性能受模型帧率限制（预览 MJPEG 不受影响）；首次点击响应 ≈ 一次 encoder + decode
  （模型加载时已预热 CUDA 内核）。