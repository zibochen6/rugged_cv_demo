# 点击分割：目标找回问题排查复盘与经验沉淀

> 面向后续维护者/自己：这份文档记录"点击分割（Segment 页）"在 **EfficientTAM-Ti 流式推理**
> 下反复出现"目标丢失后找不回来 / 换东西还在追手"的完整排查链路、根因、修法，以及
> **以后别再踩的坑**。所有结论都以真实日志 / 离线双场景验收 / 单测为证据。

---

## 1. 背景与技术栈

- 设备：Jetson Orin NX 16GB，JetPack 5.1.3（R35.5.0），Python 3.8，torch 2.1（JetPack 预编译）。
- 模型：**EfficientTAM-Ti @512×512**（官方权重 `efficienttam_ti_512x512.pt`），
  推理走 vendored 简版 GPIOX/EfficientTAM_real_time 流式 fork（`backend/app/segment/third_party/`，
  随仓库提交）。
- 关键性能：bf16 `torch.autocast("cuda", dtype=torch.bfloat16)`，单目标跟踪 ~75.7ms/帧 ≈ 13 FPS。
- 交互：前端 Segment 页点击目标 → 实时掩膜；丢失保留"淡影"；重现自动恢复。
- 相关文件：
  - `backend/app/segment/model.py`：模型封装（load/select/add_point/track + 帧注册修复）。
  - `backend/app/segment/service.py`：状态机 + 质量门 + 身份模板 + 验证式再锁定。
  - `backend/app/segment/config.py`：全部阈值集中地。
  - `backend/tests/segment/`：单测（FakeModel/FakeCamera，无权重下载）。
  - `scripts/segment_offline_demo.py`：真实模型离线双场景验收（合成视频）。

---

## 2. 现象 → 根因 → 修法（按时间线，三轮）

### 第 1 轮："换了一个东西，还在追我手里的东西"

- **现象**：手里拿耳机 → 点击分割跟踪 → 耳机消失、换成别的东西 → 掩膜仍贴着手。
- **根因**：SAM 类模型**单点正提示会把包含点击点的整个连通前景当成掩膜**。耳机与手接触 →
  掩膜 = 手 + 耳机，**目标身份从点击那一刻起就是"手"**，换任何东西掩膜都不变。
- **证据**：冒烟测试单点点击得到 `mask_area ≈ 0.996`（近乎全画面掩膜）。
- **修法**（不是换模型、不是写代码"拆分"，而是**检测 → 提示 → 给工具 → 可验证才自动恢复**）：
  - **掩膜质量门**：面积比 > `MASK_QUALITY_POOR_AREA_RATIO`(0.30) 或 bbox 覆盖帧边 >
    `MASK_QUALITY_POOR_BBOX_RATIO`(0.60) → `target_quality="poor"`，UI 提示"掩膜过大，可能包含手或背景，
    请右键加负点排除"，且 `resume_armed=false`（不武装自动恢复）。
  - 右键负点（已支持）收紧掩膜后转 ok，才武装自动恢复。

> **经验 1**：单点分割无法物理上分离接触中的"手"和"物体"——这是模型能力边界，不是代码 bug。
> 正确姿势是**检测过量分割 + 引导用户负点精修**，而不是幻想一个自动"只圈物体"的后处理。

### 第 2 轮：add_point 100% 崩溃，自动再锁定从未生效

- **现象**：设备日志出现 **2986 次** `auto re-lock failed: list index out of range`；右键精修也从未成功。
- **根因（关键）**：vendored 流式 fork 的 `track()` 只 `frame_idx += 1` 并流式算特征，
  **从不把当前帧写进 `condition_state["images"]`**（该列表只含 `load_first_frame` 的 frame 0）。
  `add_new_points_or_box(frame_idx=N≥1)` → `_run_single_frame_inference` → `_get_image_feature`
  缓存未命中 → `images[N]` 越界 → IndexError。
- **影响面**：第 0 帧之后的**任何加点**（自动再锁定 + 用户右键精修）全部崩溃。
- **修法**（`model.py`，不改 vendored 代码/权重）：
  - `prepare_frame_for_interaction(predictor, img)`：先 `perpare_data` 当前帧，再
    `while len(images) <= frame_idx: images.append(img)` 补齐，消除越界。
  - `add_point()` 再调用 `propagate_in_video_preflight()` 把新掩膜合成进记忆。

> **经验 2（最重要）**：用第三方流式 fork 前，务必确认"交互式补点"路径是否真的重算了当前帧特征。
> 这类 fork 的 `track()` 与 `add_new_points_or_box()` 对 `condition_state["images"]` 的维护常常不一致，
> 表现为**第一帧 OK、后续必崩**。任何"第 0 帧后加提示"的功能都要先验证帧注册。

### 第 3 轮（本次）：改完还是"找不回目标"

这一轮暴露了**三个叠加的自伤问题**，逐个定位：

**3a. 我给"模型自然恢复"加的身份门，误杀了正确识别**
- 现场日志：`resume rejected: identity corr=0.0239` / `-0.008`。
- 根因：模型在 LOST 期间自己输出的掩膜（它已经通过记忆/注意力"认出了"目标）又拿刚性模板卡一遍；
  真实小物体（耳机在手里、低纹理）的相关度就是 ~0，被误判成"其它物体"而永久拦截。
- 关键认知：**模型的内存/注意力本身就是身份权威**——外来物体它本来就不会输出掩膜
  （离线场景 B 里换物体全程 LOST、且无 `resume rejected`），所以这个门纯属误伤。
- 修法：**删掉 track 自然恢复的身份复核**，直接信任 `track()` 输出、按两帧滞回恢复。

**3b. reseed 用 add_point 补点，补完撑不住**
- 现象：删掉 3a 的门后，离线场景 B 出现了 4 次 `auto re-lock at`（corr 0.91）却仍 `lost(170-459)`。
- 根因：`add_point`+`preflight` 在该流式 fork 里没有可靠地把新掩膜写进记忆，下一次 `track()` 仍输出空，
  `present_streak` 被反复清零，永远到不了"2 帧恢复"。
- 修法：**reseed 命中强峰后整包重选（reset + 重新锚定）**，即调用 `model.select(...)` 而不是 `add_point`，
  并 `sm.begin_target()` 立即回 `TRACKING`（已验证的身份无需再走两次滞回）。

**3c. 再锁定"同一位置确认两次"饿死移动目标**
- 根因：要求两次尝试（间隔 1.5s）落在同一位置（容差 0.03），移动目标两次间隔位移远超容差 → 永远不确认。
- 修法：改为**单次强峰（分数 + PSR）即尝试**，靠峰值显著性 + 重选身份锚定兜底。

**3d. 模板每帧无条件覆盖 → 身份漂移**
- 根因：`_template` 每帧用"最后掩膜裁剪"覆盖，长时间跟踪中掩膜 bbox 乱跳，模板漂移成无关区域，
  同一物体回来时相关度变负值（-0.15）。
- 修法：**锚定首个干净掩膜**，仅在候选样本与当前模板相关度 ≥ `TEMPLATE_REFRESH_CORR`(0.8) 时才谨慎刷新；
  身份比较用**固定尺寸（48×48）皮尔逊相关**（`_templates_corr`），避免宽高比变化导致的 None/误判。

---

## 3. 最终修法速览（对应代码位置）

| 问题 | 修法 | 位置 |
|---|---|---|
| add_point 越界崩溃 | 补 `images` + `propagate_in_video_preflight()` | `model.py` `prepare_frame_for_interaction` / `add_point` |
| 手+物体过分割 | 质量门 + 负点提示 + 可验证才武装 | `service.py` `_commit_mask`、`config.py` |
| 自然恢复被误杀 | 删除刚性模板复核，信任 `track()` | `service.py` `_commit_mask` |
| reseed 撑不住 | 命中强峰后 **re-select（reset+重新锚定）** | `service.py` `_maybe_reseed` |
| 移动目标饿死 | 去掉"同位确认"，单次强峰即尝试 | `service.py` `_maybe_reseed` |
| 身份漂移 | 锚定 + 谨慎刷新（≥0.8）+ 固定尺寸比较 | `service.py` `_commit_mask`、`_templates_corr` |

---

## 4. 阈值清单（`config.py`）

```
MASK_QUALITY_POOR_AREA_RATIO = 0.30   # 掩膜面积比超过 → poor
MASK_QUALITY_POOR_BBOX_RATIO = 0.60   # 掩膜 bbox 覆盖任一帧边超过 → poor
TEMPLATE_ERODE_PX   = 3               # 建模板前腐蚀掩膜（剔除手/背景混合轮廓）
TEMPLATE_SIZE      = 48               # 模板最大边（px）
TEMPLATE_REFRESH_CORR = 0.8           # 谨慎刷新：候选与当前模板相关度 ≥ 此值才替换
RESEED_INTERVAL_S  = 1.5              # LOST 期间模板搜索周期
RESEED_THRESHOLD   = 0.65             # 模板匹配最小分数
RESEED_MIN_PSR     = 4.0              # 峰值显著性 PSR 门限
RESEED_SUPPRESS_S  = 5.0              # 拒绝位置冷却（残留，见注意事项）
LOST_AREA_RATIO = 0.001 / LOST_FRAMES = 6
RESUME_AREA_RATIO = 0.002 / RESUME_FRAMES = 2
```

> 注意：`RESEED_SUPPRESS_S`、`IN_MATCH_THRESHOLD`、`NATURAL_RESUME_MATCH_THRESHOLD`、`_pending_relock`
> 在本次简化后已基本不再参与决策（reject 分支被移除），清理时一并处理；保留无害但建议后续删除以免误导。

---

## 5. 验收 / 复现方法（改这里必须先跑）

### 单测
```
cd /home/seeed/workspace/seg_demo
.venv/bin/python -m pytest backend/tests/segment -q -p no:cacheprovider      # segment 63 项
.venv/bin/python -m pytest backend/tests -q -p no:cacheprovider             # 全量 297 项（需先停 studio 释放摄像头，否则 4 个 camera 测试 CameraBusyError）
```

### 离线真模型双场景（合成视频，不占摄像头，改后端行为必须重跑）
```
.venv/bin/python scripts/segment_offline_demo.py --scene all
```
- 场景 A（目标重现）：断言 `tracking → lost(淡影) → tracking(自动恢复)`。
- 场景 B（换物体再换回）：断言**换物体窗口全程 LOST 不误锁**、**原目标重现后恢复**。
  预期时间线：B = `tracking(1-169) → lost(170-330) → tracking(331-409) → lost(410-459)`。
- 成功再锁定日志应为一行 `auto re-lock at (…) score=…`（re-select 一次即恢复并持续跟踪）；
  若出现**多条 re-lock 仍不恢复**，说明又退回"补点撑不住"的坑。
- `grep -c 'list index out of range' logs` 必须为 **0**。

### 日志判读要点
- `resume rejected: identity corr=…`：若此日志在"同一物体重现"时疯狂出现，说明又把模型自然恢复误杀了。
- `auto re-lock failed: list index out of range`：帧注册没做好的 IndexError。
- `auto re-lock rejected`：旧 reject 分支（本轮已移除）。

---

## 6. 以后再碰这类问题，先问自己这几句

1. **目标身份是不是从点击那一刻就错了？**（单点过分割 → 看 `mask_area`/`target_quality`，别去调跟踪参数）
2. **"第 0 帧后加提示"会不会越界崩溃？**（流式 fork 的 `images[]` 维护；看有没有 `list index out of range`）
3. **我是不是在模型已经认对的情况下，又拿刚性模板/阈值把它拦了？**（信任 `track()`，false-negative 比 false-positive 更伤）
4. **re-lock 之后，下一帧真的能"撑住"吗？**（离线场景 B 最直观：多条 re-lock 仍 LOST = 没重锚定）
5. **阈值会不会饿死移动目标？**（"同位确认"类逻辑对移动/旋转目标不成立）
6. **合成 demo 的对象够不够"难"？**（高对比白球 ≠ 低纹理手持物体；真机现象必须以真机日志为准）

---

## 7. 附带工程经验（运维/协作）

- **摄像头单例**：全系统只有一个 `cv2.VideoCapture()`；跑 camera 测试或离线 demo 前，先停 studio
  服务（`kill -INT <pid>`），否则 CameraBusyError / GPU 争用。
- **SSH 管道**：`ssh host 'cmd' <<'PY'` 的 heredoc 会破坏 `sshpass` 密码注入（Permission denied）。
  正确做法：本地写脚本 → `scp` 上传 → 远程 `.venv/bin/python /tmp/x.py`。
- **日志诚实 + 限流**：别把 `None` 打印成 `-1.0` 这种伪造值；逐帧日志要限流（否则每帧刷 10 行）。
- **提交卫生**：仓库有大量历史遗留未暂存改动，**只 `git add` 相关路径**，绝不 `git add -A`。
- **文档归因诚实**：发现早期结论是错的（如"frame 241 模板再锁定生效"实为模型自然恢复），要及时修正，
  不要为了"验收通过"而维持错误归因。