> ⚠️ **历史快照（2026-09-14 勘察）**：本目录是清理**之前**的仓库能力基线，其中引用的
> `configs/pose.yaml`、`configs/3d_pipeline.yaml`、`app/placement`、`backend/app/pose`、
> `scripts/run_two_demos.sh`、`benchmarks/`、`docs/3d_click_track/` 等路径已在 2026-09-18 的
> 结构整理中移除；结论仍可作场景调研参考，但路径不可复现。

# 车间叉车视觉场景调研主报告（可提示分割 / 点击分割落地评估）

> **定位声明（贯穿全文，请先读）**
> 本报告及所述原型系统为**操作辅助与调研结论**，**不是功能安全系统（not a functional safety system）**。
> 它**不构成、也不替代**任何安全功能（制动、联锁、急停、认证人员探测器），**不承诺**"避免碰撞""保证不撞人"。
> 所有安全相关表述均为保守表述；任何场景落地前须由使用单位按 ISO 3691-4 / GB/T 10827.4 / ANSI-ITSDF B56.5 及本地法规完成风险评估。
>
> **本报告是"能不能做、值不值得做、先做哪一个"的决策文件，不是产品规格书，也不是安全评估文件。**
>
> - 生成日期：2026-09-14（CST）
> - 版本：v1.1 修订（2026-09-14）：`:229` 源机口径限定、§9 指纹表更新、并入现场验收负例 N1–N6、P9/P10 标注修正。
> - 报告作者：synthesizer（t4）｜综合 `01_capability_baseline.md`(t1) / `02_industry_scan.md`(t2) / `03_tech_feasibility.md`(t3)
> - 事实基线权威来源：`01_capability_baseline.md`（repo-scout 只读勘察，每条结论带 `远程路径:行号` 或命令输出）
> - 主报告**不新增任何事实**：凡本报告中出现的能力/精度/FPS/法规编号，均可在 01/02/03 或其后所列 URL 找到出处；无法回溯者一律标 **未验证**。

---

## 0. 阅读约定与证据等级

### 0.1 证据标签

| 标签 | 含义 |
|---|---|
| **【已验收·本机】** | 在**本机 Orin NX 16GB** 上有产物/日志/报告作为证据，且 01 号文档可定位 |
| **【源机已验证·本机未验证】** | 证据来自**迁移前的 AGX Orin 64GB 源机**，本机**无对应产物**，**不得当作本机能力引用** |
| **【代码就绪未上机】** | 代码与单测存在，缺真机验收 |
| **【仅离线/合成验证】** | 只有合成数据或离线视频验证，无真机现场验收 |
| **【事实 / 宣传 / 推断 / 待确认】** | 沿用 t2/t3 的四类标签：有来源可核 / 厂商自述 / 作者判断 / 来源矛盾或未证实 |
| **【未验证】** | 本次调研无法证实，**不做任何推测，不得写成已具备** |

### 0.2 三条写作纪律（本报告自我约束）

1. **禁止把厂商宣传写成事实**：02 号文档中标注【宣传】的精度/检出率/事故下降比例（如 ±3 mm、95% 托盘孔、97% 检出率、40%–93% 事故下降），本报告一概**不作为能力承诺**。
2. **禁止把未验证能力写成已具备**：01 号文档 §3「未验证清单」的 7 项，本报告逐项保留"未验证"字样。
3. **禁止为了好看而承诺精度**：凡精度数字，均标注它是**目标值**还是**实测值**、测在**哪台机器**上、是否需要**卷尺/GT**。

### 0.3 ★ 本报告最重要的一条事实纪律

01 号文档 §0.1 发现：**仓库 `README.md` 描述的不是这台机器**。

- `README.md` 声明：AGX Orin 64GB / JetPack 6.2.1 / CUDA 12.6 / TensorRT 10.3 / Python 3.10.12 / torch 2.11 / 61 GiB。
- 本机实测：**Orin NX 16GB（Seeed Rugged J401）/ JetPack 5.1.3 R35.5.0 / CUDA 11.4 / TensorRT 8.5.2.2 / Python 3.8.10 / torch 2.1.0a0 / 15.87 GiB**（01 §0.1）。

**因此：本报告中所有"20–26 FPS""25.85 FPS"类的数字，均属于 AGX 源机，在本机 Orin NX 上属【源机已验证·本机未验证】。** 本机唯一可用的分割性能事实是 **~12–13 FPS / 75–82 ms 每帧（单目标）**（01 §1.1、§2.3 C12）。

> ⚠️ 这条纪律直接否决了 03 号文档 §2.2「本项目自有实测」表格（25.85 FPS 等）在本机语境下的可引用性——03 已把该表标注为【实测·本仓库】，但它**实测于 AGX 源机**。本报告在涉及本机延迟时，一律改用 01 的本机数字。这是本次综合中发现的**最重要一处跨文档口径冲突**，已在 §4 与各场景小节显式处理。

---

## 1. 一句话结论

> **点击分割（可提示分割/跟踪）在车间叉车上"现在就能做"的是"操作员点击指定目标 → 持续跟踪 + 放置到位/错货位复核"这一组非安全判定场景；"再做一步就能做"的是"盲区人员持续跟踪的辅助预警 + near-miss 留痕"（严格限定为提示与记录）；"不该用视觉做"的是任何替代制动/功能安全的承诺、自动进叉对孔、毫米级测量、10 m 级人员距离自动决策、员工行为绩效评估。**

分三层展开：

| 层次 | 场景 | 一句话理由 |
|---|---|---|
| **现在就能做** | ① 点击指定目标 → 跨遮挡持续跟踪（单目标）；② 放置到位 / 错货位视觉复核；③ 近失事件（near-miss）与危险区滞留留痕 | 复用**本机已验收**的点击分割（~12.3 FPS）+ 现有几何/判定链；非安全功能，合规负担低 |
| **再做一步就能做** | ④ 盲区/危险区人员持续跟踪的**辅助预警**；⑤ 货位占用/错放巡检（固定视角优先） | ④ 需把"跟踪层"接到已验收的后视预警链上，且**表述红线最陡**；⑤ 需改变部署形态（固定工位） |
| **不该用视觉做** | ⑥ 替代制动/功能安全；⑦ 货叉自动进叉对孔闭环；⑧ 毫米级/亚厘米级测量；⑨ 10 m 级人员距离自动决策；⑩ 员工行为绩效评估；⑪ 重量/超载测量 | 分别撞上认证等级（PL d）、单目二次误差、AI Act Annex III 高风险、以及已有更成熟传感器（称重货叉/LMI/安全激光雷达） |

**反面结论（同样重要）**：行业侧"单纯多给驾驶员一路画面"**不是可付费的差异化**——后装叉车相机套件公开目录价约 **US$380–695/台车**（02 §3.2，【宣传/目录价】）；可付费点是**自动判定 + 记录 + 与既有流程闭环**（02 §5.2，【推断】）。

---

## 2. 现有能力与硬约束摘要（严格引自 01，不扩写）

> 本节只做摘要与引用，**不新增结论**。任何 01 标为"未验证"的项，本节原样保留。

### 2.1 本机已验收 / 已上机运行的能力

| 能力 | 成熟度 | 关键证据（引自 01） |
|---|---|---|
| **点击分割 + 单目标流式跟踪**（EfficientTAM-Ti @512，bf16） | **【已验收·本机】** | 离线真模型产物 `states.jsonl` 459 帧，稳态 `model_fps ≈ 12.28–12.30`、`infer_ms ≈ 81.3–81.6`；文档另记 75.7 ms/13.2 FPS；真机 `POST /api/segment/click` 200（01 §1.1） |
| 单目度量深度（DAV2 Metric Indoor Small → TRT FP16 518） | **【已验收·本机】**（warn_app 链路） | TRT fp16 **26.5 ms**（trtexec）/ 端到端 **~46 ms**；PyTorch fp16 回退 79 ms（01 §1.4） |
| 后视碰撞预警 warn_app（深度 + 地面/ROI/障碍 + 时域 + 风险 + 蜂鸣） | **【已验收·本机（合成/录像）】** | 12/12 单测、合成场景全链 gate PASS；**真机场测（卷尺精度 / Test 1–7）BLOCKED**（01 §1.12） |
| 行人检测 YOLOv8n → TensorRT FP16 | **【已上机运行】** | 引擎存在；设备 `events/` 有 person 现场事件（01 §1.13） |
| Web Calibration Studio（棋盘格标定） | **【已验收·本机】** | 有结果文件与 RMS 0.3215；**但标定档位是 `/dev/video25 @1280×720`**（01 §1.15） |
| 远程可视化（MJPEG + JSON 状态） | **【已上机运行】** | Studio 当前在 8000 端口服务（01 §1.18） |

### 2.2 硬约束摘要（不可越界前提，完整表见 01 §2）

| 编号 | 约束（摘要） |
|---|---|
| **C1** | **只有单目 RGB，无 LiDAR、无深度相机**；一切"距离/3D"都来自单目学习模型 |
| **C2/C3** | 单目度量深度是**学习先验**；**深度精度无卷尺 GT**，当前不存在可引用的绝对精度数字 |
| **C5** | 标定必须与运行分辨率严格一致，否则内参/PnP 全部作废 |
| **C6/C7** | **全局只有一个 `cv2.VideoCapture`（单例）**；实测 **Studio 进程 PID 438670 正占用 `/dev/video0` 与 8000 端口** → 与任何需要 USB 相机的 demo 互斥 |
| **C8** | 点击分割**不自己开相机**，相机未运行时 `/api/segment/start` 返回 409 |
| **C9/C10** | 前后摄是两条直连 PoE 网口且**共用一个 `PSE_PWR_EN`**；`eth0/eth3/eth4` DOWN，前摄实际走 **eth2**、后摄走 **eth1** → **`configs/two_cameras.env` 的 `FRONT_IFACE=eth0` 是错的**，一键启动预检必然失败 |
| **C11/C12** | Orin NX 16GB、MAXN；**点击分割本机 ~12–13 FPS / 75–82 ms 是上限，不是下限** |
| **C13–C15** | EfficientTAM **bf16 autocast**、`compile_image_encoder=false`、**未用 TensorRT（设计决策）**；输入固定 512、分割前降采样 max side 640 |
| **C16** | 3D 链路数字测于 AGX 源机，**本机未验证，不得引用为本机能力** |
| **C20–C25** | 落货辅助 = Monocular AI Placement Assistance（**非安全认证测量系统**，未标定必须 APPROXIMATE）；双相机演示 = 产品演示（**非功能安全/非标定制动系统**）；后视预警 = 驾驶辅助原型（**感知不可靠必须 SYSTEM ERROR，绝不默认 SAFE**）；目标区必须是**地面系几何**；**全仓功能安全标准编号 0 命中、无任何认证** |

### 2.3 已知缺口摘要（完整 20 条见 01 §3）

高影响缺口：**G1** 点击分割仅单目标；**G2** 多目标身份保持未解决；**G3** 真机自动再锁定未单独复验；**G4** 3D/placement 全链本机未复现；**G5** 深度无卷尺 GT；**G6** 落货真机卷尺矩阵 A–J 空；**G7** 前视 AprilTag 验收表/30 poses 空；**G9** 双相机网口配置与实际不符；**G10** 3 个必需配置在工作树被删除（`marker_placement.yaml`/`placement.yaml`/`target_zone.yaml`，HEAD 可取回）；**G11** **跨相机时间同步/目标交接全仓 0 命中**；**G12** 相机互斥（演示与标定不能同时跑）；**G13** EfficientTAM 未 TensorRT 化（阈值是 live FPS < 10）；**G14** VLM 语义现场不可用（已从 UI 撤下）；**G15** stress 模式内存增长未根治；**G16** 标定档位与运行设备/分辨率不匹配；**G17** 移动叉车 vs 固定世界地图未解决；**G18** 货叉本身不做分割；**G19** 夜/低照度/反光/透明/黑色物体/镜头污染/强振动下单目深度失效（自陈）；**G20** 无功能安全认证。

### 2.4 未验证清单（明确不做的假设，01 §3 原文 7 项）

1. 3D 点击跟踪（深度融合/XYZ/Kalman/OBB/BEV）**在本机的 FPS、显存、稳定性 —— 未验证**。
2. 落货辅助**在本机的精度 —— 未验证**（真机未做）。
3. 前视 AprilTag 定位精度（P95 ≤5 cm / ≤5°）**—— 未验证**（只是目标值）。
4. 双相机联动（≥10 FPS 每路、P95 告警 <500 ms、2 小时无内存增长）**—— 未验证**。
5. 点击分割在**真实车间**（粉尘/振动/强光/低纹理）下的表现 **—— 未验证**（现有真机证据只有 1 次点击 + 合成视频）。
6. 度量深度的绝对误差量级 **—— 未验证**（无 GT，禁止编造）。
7. USB 相机当前是否可用（出图正常）**—— 未验证**。

### 2.5 可复用接口面（01 §4）

- **唯一已上机的语义输出接口**：`POST /api/segment/click`（body `{x,y∈[0,1], label∈{0,1}}`）→ 掩膜多边形（归一化坐标）。下游场景都从它取"目标掩膜"。
- **度量闸门**：`configs/3d_pipeline.yaml` 的 `camera.intrinsics + calibrated` 是"APPROXIMATE → 真实度量"的唯一开关。
- **状态契约**：`GET /api/segment/status` 返回状态机状态、多边形、ghost、fps、`mask_area`、`model_fps`、`infer_ms`、`target_quality`、`resume_armed` 等。
- **不要复用**：VLM 对象标签（现场判定不可用）。

---

## 3. 场景目录

> 每个场景的字段固定为：**场景名 / 目标用户与价值 / 需要的视觉能力 / 与现有能力的差距 / 精度与延迟要求 / 可验收指标 / 合规与风险 / 落地成本 / 结论**。
> 凡与 01 硬约束冲突者，在"与现有能力的差距"或"合规与风险"中**显式写出冲突点与缓解方案**，不用"后续优化"一笔带过。
> 人日量级均为**【推断】**（03 未给成本口径，02 只有外部价格信号）。

---

### 3.1 场景 P1：放置到位 / 错货位视觉复核（put-away confirmation）

| 字段 | 内容 |
|---|---|
| **场景名** | 放置到位与错货位视觉复核（Placement / slot verification） |
| **目标用户与价值** | 仓库/车间管理者与叉车操作员。价值在于把"这次放货放正了没有、放错货位没有"变成**结构化、可记录、可追溯**的输出，与 02 §1.2 的"到位确认"判断一致；02 §5.2【推断】：付费点在"判定 + 记录 + 闭环"，不在"看得见" |
| **需要的视觉能力** | ① 目标/货物/目标区的**掩码级分割**；② 地面系几何（足印投影 + ΔX/ΔZ/ΔYaw）；③ 度量深度或地面单应提供尺度；④ 阈值判定 + 时域确认 |
| **与现有能力的差距** | **复用**：点击分割（01 §1.1，【已验收·本机】）+ 现有放置判定几何链（01 §1.9，地面系目标区 + PLACEMENT_OK）。**要新增**：把"分割掩码"接成几何链的感知输入（当前合成验证用的是几何而非真实掩码）。**冲突点（必须写）**：<br>• **G4/C16**：3D/placement **全链的 FPS/精度数字全部测于 AGX 源机**，本机**无 `logs/3d_pipeline/`、无 3D 产物** ⇒ 本机属**未验证**，试点必须先在 NX 上复现，否则任何精度承诺无效。<br>• **C7/G12**：Studio（PID 438670）正占用 `/dev/video0`，与本场景的相机使用**互斥**，跑验收前必须停 Studio。<br>• **G10**：`configs/placement.yaml`、`target_zone.yaml`、`marker_placement.yaml` 在工作树**已被删除**，需从 HEAD 取回（01 §2.5）。<br>• **G16**：现存标定是 `/dev/video25@1280×720`，与本机相机/前摄 2304×1296 **不一致**，复用前必须重标（C5）。 |
| **精度与延迟要求** | 精度：**演示级 \|ΔX\|,\|ΔZ\| ≤ 10 cm**；全套标定后才谈 5 cm（01 §1.9 原文）。延迟：判定链 **P95 ≤ 500 ms**（03 §4.2 建议门槛）。<br>⚠️ **本机延迟换算**：03 §3.2 给出的"分割 38–50 ms + 几何 <10 ms ⇒ <100 ms/帧"用的是 **AGX 数字**；本机分割实测 **81 ms**（01 §1.1），故本机预期 **>90–100 ms/帧**，**必须实测，不得直接沿用**。 |
| **可验收指标** | ① 现场**卷尺矩阵 A–J**（模板已存在于 `benchmarks/placement_accuracy.md`，当前**全空**，01 §1.9/G6）逐点记录 \|ΔX\|/\|ΔZ\|/ΔYaw；<br>② **20 cm 偏移的负例必须 0 次 PASS**（False PASS 是最坏失效，03 §6 S2/S3）；<br>③ 负例组（错位 / 错 ID / 画面冻结 / 未人工确认）**必须永不 VERIFIED**（沿用 01 §1.10 的前视门槛）；<br>④ 本机 FPS P50 与 2 小时显存平稳（本仓库已把"2 小时无持续增长"写入验收，03 §4.2）。<br>**P95 目标值**：位置 P95 ≤5 cm、偏航 P95 ≤5°（01 §1.10/G7——**注意这是目标值，不是实测值**）。 |
| **合规与风险** | **非安全功能，合规风险低**。红线：目标区必须是**地面系几何**、不得退化为像素检测（C24）；未标定时必须显示 `APPROXIMATE`（C20）。**不得**表述为"替代对位传感器"或"保证放对"（与 03 §3.1 的精度事实冲突）。风险主要在**工程侧**：True-PASS 依赖标定质量。 |
| **落地成本** | **约 12–18 人日【推断】**：本机复现 3D 链 5–8 + 重标定 2 + 现场采集与卷尺验收 5–8。**不需新硬件**（复用现有 USB 相机 + 前摄 PoE）。 |
| **结论** | ✅ **推荐试点候选（首选）** |

---

### 3.2 场景 P2：盲区 / 危险区人员持续跟踪（辅助预警）+ near-miss 留痕

| 字段 | 内容 |
|---|---|
| **场景名** | 盲区/危险区人员持续跟踪的**辅助预警**与近失事件留痕（advisory only） |
| **目标用户与价值** | 仓库安全负责人与操作员。需求侧硬数据：美国 2017 年叉车工伤中 **1,850 起是行人在叉车运输作业中被撞**（02 §1.6，BLS）；中国 2024 年场车事故 **43 起/死亡 36 人**（02 §1.6）。02 §3.3【推断】把"行人与盲区告警"排在第 2 位（痛点直观但合规边界最陡） |
| **需要的视觉能力** | 行人检测（已有）+ 单目度量深度（已有）+ **目标持续跟踪（跨遮挡 ID 保持）** + TTC/风险链（已有） |
| **与现有能力的差距** | **复用**：后视预警 warn_app 全链（01 §1.12，【已验收·本机（合成/录像）】）+ YOLOv8n 行人检测（01 §1.13，已上机）+ 度量深度 TRT（01 §1.4）。**要新增**：把"点击/自动触发后的目标持续跟踪"接成一层（03 §6 S4）。**冲突点（必须写）**：<br>• **C20–C25 / G20**：本系统**无功能安全认证**，全仓找不到任何功能安全标准编号 ⇒ **若被写成"防撞/安全"即与硬约束直接冲突**。缓解：文档/UI/验收口径全部固定为"提示 + 记录"，界面必须保留 fail-visible（感知不可靠 → SYSTEM ERROR，绝不默认 SAFE，C22）。<br>• **C9/C10**：03 §3.3 提到"双相机组合才达 PL d"，但本机**两条 PoE 共用一个 PSE 使能、且 `two_cameras.env` 网口配置是错的** ⇒ 该路线**当前不可行**，且**本项目不追求安全等级**。<br>• **G11**：跨相机时间同步/目标交接**全仓 0 命中** ⇒ 前后视**不能对同一目标做交接**，只能各自独立告警。<br>• **C3/G5/G19**：深度无 GT，且低照度/强反光/透明/黑色物体/镜头污染/强振动下会失效（自陈）⇒ 距离只能保守使用。 |
| **精度与延迟要求** | 距离：**1.5–3.5 m 内 P95 误差 ≤0.3 m**；**>5 m 不用于任何自动决策**（03 §3.4【推断】，需现场卷尺标定验证）。告警：**P95 <500 ms**。跟踪：遮挡 ≥2 s 后重现 **ID 保持率 ≥95%**、**IDSW/分钟 ≤1**（03 §2.3/§4.2【推断】）。延迟预算：深度 46 ms + 分割 ~81 ms（本机，串行 ~127 ms）【本机数字换算】 |
| **可验收指标** | ① **30 段分类录像**（进入 / 横穿 / 接近 / 双人 / 部分遮挡）上的量化门槛：遮挡 ≥2 s 后重现 **ID 保持率 ≥95%**、**IDSW ≤1 次/分钟**，并同时报告 IDF1（03 §2.3【推断】）；② **1.5–3.5 m 内距离 P95 ≤0.3 m**，>5 m 不用于任何自动决策（03 §3.4）；③ 告警 **P95 <500 ms**（03 §4.2）；④ **10 min 空场景**误报次数单独统计——**误报次数上限待现场标定确定**（02 §4.5 仅要求误报率与召回同等重要，未给出可引用数值）；⑤ **连续 2 小时显存无持续增长**（03 §4.2）。 |
| **合规与风险** | **合规风险高（本报告最高的场景）**。① 未检索到任何"普通相机作为 ISO 3691-4 合格人员探测手段"的证据；安全级探测是 IEC 61496 认证器件的地盘（02 §2.4）；② 唯一有安全认证的 3D 相机 SICK safeVisionary2 单机仅 **PL c / SIL1 / Type 2**，要 PL d 需**双相机**（03 §3.3）；③ 欧盟 AI Act 第 6 条 + Annex III 第 4 项：一旦用于"对员工行为的持续评估"即**高风险**（02 §2.5）。缓解：只做提示与留痕、绝不接制动/联锁、不做员工绩效评估。 |
| **落地成本** | **约 12–20 人日【推断】**（跟踪层接入 6–10 + 录像采集与误报统计 6–10）。**不需新硬件**（复用后摄 + NX）。 |
| **结论** | ⚠️ **推荐试点候选（次选）**，但**必须严格限定为"辅助预警 + 留痕"**；任何"防撞"表述一律否决 |

---

### 3.3 场景 P3：点击指定目标 → 跨遮挡持续跟踪（人机沟通界面 / click-to-track 底座）

| 字段 | 内容 |
|---|---|
| **场景名** | 操作员点击指定目标并持续跟踪（"就是这个"界面） |
| **目标用户与价值** | 操作员与调度/现场管理。价值：把一个"人心里知道、系统不知道"的目标变成系统可跟踪对象，是 P1/P2 的**共同底座**。02 §1.7【推断】：这是与团队现有能力**最同构**的场景，也是行业内**检索不到商用先例**的空白区（教育成本最高） |
| **需要的视觉能力** | 可提示分割 + 时序记忆传播 + 状态机（丢失/重现）+ 身份保持 |
| **与现有能力的差距** | **复用**：点击分割全链（01 §1.1【已验收·本机】）+ 丢失/重现状态机与身份模板（01 §1.2/§1.3，【仅离线验证】/【代码就绪】）。**要新增**：多目标与身份保持（**G1/G2 未解决**）、TensorRT 化（**G13 未做**）。**冲突点（必须写）**：<br>• **G3**：**真机自动再锁定未单独复验**——本机只留下 1 次 `POST /api/segment/click`，**没有真机 re-lock 日志**；离线双场景 PASS 用的是**合成视频** ⇒ 现场鲁棒性属**未验证**。<br>• **G1**：**只有单目标**，多目标、YOLO 自动检测、保存、视频文件输入都在"最小化范围"内被删除。<br>• **C8**：点击分割**不自己开相机**，依赖 Studio 相机存活；**C7**：Studio 正占用 `/dev/video0`（互斥）。<br>• **口径冲突**：**本机 12.3 FPS vs AGX 25.85 FPS** —— 声明本机能力时必须用前者（01 §0.1 纪律）。 |
| **精度与延迟要求** | 精度：短时无遮挡 **J&F 0.75–0.8（对齐 SAM 2.1 small 的 SA-V 文献量级【文献值】，非现场实测）**；遮挡重现 ID 保持率目标 ≥95%。延迟：交互跟踪 **≥10 FPS**（03 §4.2 门槛）；本机实测 **~12.3 FPS / 81 ms**（01 §1.1）——**刚过门槛，余量很小**。 |
| **可验收指标** | ① 30 段现场录像（含 ≥2 s 遮挡再现）；② ID 保持率与 IDF1；③ FPS P95；④ 2 小时显存平稳（**G15：stress 模式内存增长未根治**，需专门测）。 |
| **合规与风险** | **合规风险低**。唯一注意：若跟踪对象是**人**且用于绩效评估，则落入 02 §2.5 的高风险区——**只做"设备与货物状态记录"，不做人员绩效评估**。 |
| **落地成本** | **约 5–10 人日【推断】**（工程化与真实场景复验；多目标另计）。**不需新硬件**。 |
| **结论** | ✅ **推荐试点候选（作为 P1 的组成部分）**；多目标/身份保持列为"观察" |

---

### 3.4 场景 P4：托盘孔 / 货叉对准**辅助提示**（fork pocket alignment advisory）

| 字段 | 内容 |
|---|---|
| **场景名** | 托盘孔与货叉对准的辅助提示（**只提示，不闭环**） |
| **目标用户与价值** | 操作员与高位存取作业。但**行业已有成熟且便宜的替代**：叉尖/门架相机 + 激光对准线（货叉视角相机产品、Smart Laser 对孔，02 §1.1） |
| **需要的视觉能力** | 托盘/孔**专用检测**（非通用分割可解决）+ 掩码精修 + 度量（深度/单应/标记） |
| **与现有能力的差距** | **要新增**：托盘孔检测器（**无公开的叉车托盘孔分割数据集**，03 §4.1【推断】⇒ 必须自建现场数据）+ 位姿/度量模块。**冲突点（必须写）**：<br>• **C1（无 LiDAR）**：03 §3.1 中精度最高的两条路线（LiDAR+ICP、PIRATR 点云 6-DoF）**在本机不可行**，只剩"视觉 + 单目几何 + 标记法"。<br>• **C3/G5**：深度无卷尺 GT ⇒ **绝对精度无法承诺**。<br>• **G18**：货叉本身不做分割（仅按配置几何绘制）。<br>• **精度本质**：03 §3.1 明确"**对位是厘米级问题，且本质上不是分割问题**"——提示分割只给 2D 掩码，**不给尺度**。 |
| **精度与延迟要求** | 几何起点【事实】：EPAL 托盘总高 **144 mm (+7/-0)**、**货叉入口高度 100 mm (+5/-0)**、倒角 17×45°（03 §3.1，EPAL 官方数据表）；Lang2Lift 操作容差 **横向 ±0.05 m / 垂向 ±0.04 m**（03 §3.1）。参考值：合成数据检测 mAP50 0.995、位置误差 <4.2 cm、旋转 8.2°（5 m 正视条件下，03 §3.1）。<br>⚠️ **最有力的一条反证**【事实】：一篇**同行评审**的"图像测量插入货叉"研究给出允许误差 **Y 向 ≤50 mm、偏航 ≤3°、俯仰 ≤1°**，而其**实车 6 次插叉只成功 3 次**，失败原因是检测区混入货架立柱强边缘、大转角时模板匹配失败——**并存在把托盘推倒的风险**（03 §3.1，[arXiv:2602.16178](https://arxiv.org/abs/2602.16178) / [PMC 全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346/)）。**延迟**：分割 + 位姿——注意 03 §3.1【事实】给出"**延迟主因是 6D 位姿而非分割**"（分割 0.04 s/25 Hz vs 位姿 0.83 s/1.2 Hz，整链 1.05 s，且为**研究原型配置**）。 |
| **可验收指标** | 位置 P95 ≤5 cm、偏航 P95 ≤5°（**目标值**）；**托盘/孔检测必须单独报告子集指标**，不与总体平均混报（02 §4.3 的建议）。 |
| **合规与风险** | **合规风险中**。不得宣称"替代对位传感器"（与 03 §3.1 事实冲突）；不得把"辅助提示"包装成"自动对孔能力"。 |
| **落地成本** | **约 25–40 人日【推断】**（自建数据集 10–20 + 检测器训练 10 + 度量集成 5–10）。**可能需新硬件**（若走 3D/ToF 路线）。 |
| **结论** |  **观察（不做自动进叉；最多做"提示 + 作业记录"）** —— 依据：① 03 §3.1 判定"对位不是分割问题"；② **同行评审的实车研究 6 次插叉仅成功 3 次**且存在推倒托盘风险；③ 本项目无 LiDAR、深度无 GT |

---

### 3.5 场景 P5：货位占用 / 错放巡检（**固定视角优先**）

| 字段 | 内容 |
|---|---|
| **场景名** | 货位占用与错放巡检（location occupancy / put-away audit） |
| **目标用户与价值** | 仓库管理者。02 §1.2【推断】：天然适合做"到位确认"而不是"绝对精度测量"；但**行业主流做法是固定高位相机/无人机**（改造成本高、正确率高） |
| **需要的视觉能力** | 固定视角下的检测/分割 + 货位格网映射 + 二值判定 |
| **与现有能力的差距** | **要新增**：固定工位部署形态与格网标定。**冲突点（必须写）**：<br>• **C6/C7/C9/C10**：项目现有硬件是**车载单目 + 两条 PoE 网口共用使能**，固定多机位部署会直接撞上供电与相机互斥约束；本场景若落地，**应独立于车载方案另行部署**，不能假设复用现有 4 路网口。<br>• **02 §5.1【推断】**：行业把这类任务放在固定工位，正是因为消掉了振动/光照/视角三大变量——车载版本要额外回答"固定相机为什么不行"。 |
| **精度与延迟要求** | **判定阈值必须先定义来源（现场实测 vs 客户 SOP）**，否则会变成不可验收的口水标准（02 §1.2【推断】）。延迟不敏感（可分钟级）。 |
| **可验收指标** | 占用/空位判定的混淆矩阵（TP/FP/FN）+ 错放检出率；负例必须不 PASS。**注意 02 §1.2 未找到可核验的第三方准确率数据（【待确认】）**。 |
| **合规与风险** | **低**（不涉及人员）。 |
| **落地成本** | **约 15–25 人日【推断】**；**需新硬件**（固定机位 + 独立供电/网络）。 |
| **结论** | 🔍 **观察**（盈利形态成立，但**不在本项目车载硬约束范围内**） |

---

### 3.6 场景 P6：包装 / 缠绕膜破损与载荷稳定性（固定工位）

| 字段 | 内容 |
|---|---|
| **场景名** | 缠绕膜/包装破损与载荷稳定性、超限（overhang）检查 |
| **目标用户与价值** | 库内质量与货损管理。行业实践是**固定工位（转台/龙门/月台）多视角**：Duvel Moortgat 用**两台相机 + 转台**对空托盘做视觉检测并自动剔除（02 §1.4，集成商案例页） |
| **需要的视觉能力** | 多视角/转台图像 + 破损/缠绕膜/超限分类 |
| **与现有能力的差距** | **要新增**：专用分类模型 + 固定工位硬件。**冲突点（必须写）**：<br>• **C4**：车载前/后摄是 **2304×1296 @ ~19–20 fps H.265**，**运动模糊 + 单视角 + 遮挡**会严重削弱破损判定能力；02 §1.4【推断】明确"车载单目很难复现固定工位的效果"。<br>• **C7**：与 Studio 相机互斥。 |
| **精度与延迟要求** | 厂商口径 0.4 s/托盘、97% 检出率均为【宣传】（02 §1.4），**不可引用为承诺**。延迟不敏感。 |
| **可验收指标** | **必须按"已知困难样本集"单独报告**（02 §4.3）：强反光（缠膜/不锈钢/玻璃瓶）、深色托盘。 |
| **合规与风险** | **低**。 |
| **落地成本** | **约 20–30 人日【推断】**；**需换硬件**（转台/固定多机位）。 |
| **结论** | ❌ **不做（车载版本）** ／ 🔍 **观察（固定工位另立项）** |

---

### 3.7 场景 P7：装载计数与免手动扫描

| 字段 | 内容 |
|---|---|
| **场景名** | 装载计数 / 免手动扫描（count & identify） |
| **目标用户与价值** | 出入库核对。但**主流方案是条码/RFID/称重**（Zebra 叉车免手动托盘扫描、称重货叉，02 §1.3/§1.8），视觉是补充 |
| **需要的视觉能力** | 计数分割/检测 + 与订单匹配 |
| **与现有能力的差距** | **要新增**：计数模型 + WMS 对接。**冲突点**：<br>• **G1/G2**：现有分割**只有单目标**且**多目标身份保持未解决** ⇒ 计数所需的"多实例 + 唯一 ID"**当前不具备**；03 §2.2【事实+推断】指出 SAM 2 系多目标**成本近似线性增长**，10 个目标时（**源机 AGX 的 25 FPS 量级**）会掉到个位数【口径：源机 AGX，本机未验证】。 |
| **精度与延迟要求** | **无第三方可核验的准确率/召回率数据**（02 §1.3【待确认】）。 |
| **可验收指标** | 计数误差分布——但在**叠放/遮挡/同色托盘**下误差与人工差异大（02 §1.3【推断】）。 |
| **合规与风险** | **低**。 |
| **落地成本** | — |
| **结论** | ❌ **不做**（客户已有条码/RFID/称重替代；视觉计数不可靠） |

---

### 3.8 场景 P8：货叉 / 载荷状态监控（重量、超载、偏载）

| 字段 | 内容 |
|---|---|
| **场景名** | 载荷重量/超载/偏载监控 |
| **目标用户与价值** | 安全与合规管理。**重量类指标已有更成熟的专用传感器**：称重货叉（Cascade Weigh Forks）、载荷力矩指示器 LMI/SLI（压力 + 倾角）（02 §1.8） |
| **需要的视觉能力** | 视觉只能做间接判断（是否插入到位、是否倾斜/偏载） |
| **与现有能力的差距** | **要新增**：姿态/偏载估计；**重量不可由视觉承担**（02 §1.8【推断】）。 |
| **精度与延迟要求** | 视觉重量估计无可行路径；姿态类继承 P1 的精度边界。 |
| **可验收指标** | 姿态/偏载的几何指标，并入 P1。 |
| **合规与风险** | **中**（若被误当作"防止超载"的安全功能）。 |
| **落地成本** | — |
| **结论** | ❌ **不做（重量/超载）**；姿态/偏载并入 P1 的观察项 |

---

### 3.9 场景 P9：作业录像与培训回放 / 员工行为分析

| 字段 | 内容 |
|---|---|
| **场景名** | 作业录像、回放与驾驶员行为分析 |
| **目标用户与价值** | 车队/安全管理。车载行车记录 + 行为识别 + 教练闭环是成熟品类（Samsara 等，02 §1.9） |
| **需要的视觉能力** | 录像 + 行为事件识别 |
| **与现有能力的差距** | 现有警告链已记录事件（`events/`）；**行为分析需新模块**。 |
| **精度与延迟要求** | 不是精度问题。 |
| **可验收指标** | **不适用（不做）**——员工行为/绩效评估整体不做，无验收指标 |
| **合规与风险** | **高**。02 §2.5【事实】：AI Act **Annex III 第 4 项**把"监控和评估劳动关系中人员工作表现与行为"的 AI 系统列为**高风险**；中国需按《个人信息保护法》处理告知、同意与最小必要。02 §5.5 建议直接标注为"观察/不做"，或**限定为"只评设备与货物，不评人"**。 |
| **落地成本** | **不适用（不做）** |
| **结论** | ❌ **不做（员工行为/绩效评估）**；只保留"设备与货物状态记录" |

---

### 3.10 场景 P10：通道与库位可通行性

| 字段 | 内容 |
|---|---|
| **场景名** | 通道可通行性与障碍/人员进入告警 |
| **目标用户与价值** | 车间安全。02 §1.5：**固定视角**（通道口相机）是天然匹配形态 |
| **需要的视觉能力** | 区域/通道级检测（固定视角）或车载障碍检测 |
| **与现有能力的差距** | 车载段与 **P2 高度重叠**（02 §1.5【推断】："容易合并成一个场景"）。 |
| **精度与延迟要求** | 同 P2。 |
| **可验收指标** | **不适用（不做）**——本场景不独立立项（车载段并入 P2，其验收见 §3.2） |
| **合规与风险** | 同 P2（人身安全红线）。 |
| **落地成本** | **不适用（不做）** |
| **结论** | ❌ **不做为独立场景**（车载段并入 P2；固定视角属 P5 的范畴） |

---

## 4. 优先级矩阵：价值 × 技术就绪度 × 合规风险

### 4.1 就绪度定义（按契约口径）

| 档次 | 含义 |
|---|---|
| **已具备** | 本机已验收，可直接用（可能只是工程化） |
| **小改可用** | 复用现有层，接一个新输入即可 |
| **需新模块** | 需新增检测/跟踪/融合模块或重新训练 |
| **需换硬件** | 需相机/固定工位/安全器件等新硬件 |

### 4.2 排序表

| 排名 | 场景 | 价值 | 技术就绪度 | 合规风险 | 冲突点（与 01 硬约束） | 结论 |
|---|---|---|---|---|---|---|
| **1** | **P1 放置到位/错货位复核** | 高 | **小改可用** | 低 | G4/C16（3D 链本机未验证）、C7/G12（相机互斥）、G10（配置被删）、G16（标定不匹配） | ✅ 试点① |
| **2** | **P2 盲区人员持续跟踪辅助预警** | 高 | **需新模块**（跟踪层；检测/深度层已具备） | **高** | C20–C25/G20（无功能安全）、C9/C10（多路供电/网口错）、G11（无跨相机同步） | ⚠️ 试点②（严格限"提示+留痕"） |
| **3** | **P3 点击指定目标→持续跟踪** | 中 | **已具备**（单目标） | 低 | G3（真机再锁定未复验）、G1（仅单目标）、C8/C7 | ✅ 并入试点① |
| **4** | **P4 托盘孔/货叉对准提示** | 中 | 需新模块 | 中 | C1（无 LiDAR）、C3/G5（深度无 GT）、G18 | 🔍 观察（不做闭环） |
| **5** | **P5 货位占用/错放巡检（固定视角）** | 中 | 需新模块 | 低 | C6/C7/C9/C10（部署形态与车载约束不兼容） | 🔍 观察（另立项） |
| **6** | **P6 包装/缠绕膜破损** | 中 | 需换硬件 | 低 | C4（车载运动模糊/单视角） | ❌ 不做（车载） |
| **7** | **P7 装载计数** | 低 | 需新模块（多实例+ID 不具备） | 低 | G1/G2、SAM2 多目标线性成本 | ❌ 不做 |
| **8** | **P8 载荷重量/超载** | 低 | 需换硬件（称重货叉/LMI 已解决） | 中 | 视觉无可行路径 | ❌ 不做 |
| **9** | **P9 录像/行为分析** | 低 | 需新模块 | **高**（AI Act Annex III） | — | ❌ 不做 |
| **10** | **P10 通道可通行性（车载）** | 低-中 | 需新模块 | 中 | 与 P2 重叠 | ❌ 并入 P2 |

### 4.3 排序理由（三条）

1. **P1 排第一不是因为"最容易"，而是因为"价值高 + 合规低 + 复用最多"**：它复用本机**唯一已验收**的语义输出（点击分割掩码）+ 一套现成的几何/判定链，且**不被任何安全红线捆住**。它的风险全部是工程风险（本机未复现），可以靠"先复现再承诺"消化。
2. **P2 价值同样高但就绪度更差、合规风险最陡**：它的"就绪"部分（后视预警链、行人与深度）确实已验收，但**跟踪层是新的**，且**表述错一句话就会把项目从"辅助"变成"安全组件"**（02 §4.6 称之为最典型的坑）——因此它排第二而非第一。
3. **矩阵与试点建议自洽**：排 1 与排 3 合并成试点①（同一套验证动作），排 2 作为试点②；排 4 及以上全部不进试点。**矩阵中没有任何"高价值 + 已具备"的场景被排除在试点之外**。

> **一处必须点明的口径冲突（本报告的诚实记录）**：03 §8 建议的排序是 "S1 > S3 > S2 > S4 > S5"，其中 S1（点击跟踪）被列为"已具备、仅需工程化"。本报告把**放置复核（S3）提到与 S1 并列的第一位**，原因有二：① 03 的 S1 延迟数字（38–50 ms / 20–26 FPS）来自 **AGX 源机**，本机实测仅 **81 ms / 12.3 FPS**，**S1 的"余量"比 03 描述的更小**（12.3 FPS 刚过 10 FPS 门槛）；② 单独一个"点击跟踪"不产生业务价值，价值只在接到 P1/P2 之后才出现。**本报告不因此否定 03 的技术判断，只调整其在"业务优先级"上的位置。**

---

## 5. 试点建议

> 只推荐 **2 个**试点，二者可**共享同一批现场录像**，总窗口 2–4 周。
> 所有最小验证方法都要求**在现有 Jetson（Orin NX 16GB）+ 现有相机上**完成，不采购新硬件。

### 5.1 试点①（首选）：点击跟踪 + 放置到位复核（对应 P1 + P3）

**要回答的 4 个验证问题**

| # | 问题 | 为什么必须问 |
|---|---|---|
| Q1 | 3D/placement 全链能否在**本机 NX** 上复现，并达到"交互 ≥10 FPS、判定 P95 ≤500 ms"？ | G4 明确本机无产物；**不复现则一切精度数字无效** |
| Q2 | 在真实车间光照与反光下，分割掩码能否达到 **SR@0.75 ≥0.8**（喂几何的门槛）？ | 03 §3.1 消融证明：掩码边界质量差会**直接毁掉几何**（SR@0.75 从 52.7% 掉到 8.5%） |
| Q3 | 有卷尺 GT 时，真实误差量级是多少？是否落在 **≤10 cm 演示级**？ | C3/G5：现状**零个可引用的绝对精度数字**；这是必须补的第一张表 |
| Q4 | 操作员在什么时刻会点、点击替代了什么操作？ | 02 §1.7【推断】：本场景风险**不是精度而是交互时机与注意力** |

**最小验证方法（不新增硬件）**

1. **释放相机**：停掉 Studio（PID 438670），解除 `/dev/video0` 独占（C6/C7）；同时确认可接受"演示期间不能跑标定"（G12）。
2. **恢复配置**：从 HEAD 取回 `configs/placement.yaml`、`configs/target_zone.yaml`、`configs/marker_placement.yaml`（G10）。
3. **重标定**：对实际运行分辨率重新标定（现存 `camera_calibration.yaml` 是 `/dev/video25@1280×720`，与前摄 2304×1296 不符，G16/C5）。
4. **本机复现 3D 链**：跑通"点击 → 掩码 → 深度融合 → 地面系 XYZ → ΔX/ΔZ/ΔYaw → PLACEMENT_OK"，记录本机 FPS/显存/延迟（补 G4）。
5. **现场采集**：≥30 段分类录像（含遮挡与重现）+ 10 组卷尺实测位姿 + 10 min 空场景。
6. **交互观察**：跟车观察 1–2 个班次，记录"操作员是否会点、何时点、是否打断作业"。

**成功判据（全部满足才算成功）**

- 本机 FPS **P50 ≥10**；判定链 **P95 ≤500 ms**；
- 掩码 **SR@0.75 ≥0.8**（现场自建集）；
- 卷尺 **|ΔX|、|ΔZ| P95 ≤10 cm**（演示级）；
- **20 cm 偏移的 False PASS = 0 次**；负例（错位/错 ID/冻结流/未人工确认）**永不 PASS**；
- 连续 **2 小时显存无持续增长**。

**失败判据（任一命中即触发 go/no-go 决策）**

- 本机 FPS **<10** → 触发 G13 的既定阈值（`README.md:329`：live FPS < 10 才考虑 TensorRT 化）→ 决策：做 memory 侧优化 / ROI 裁剪 / TensorRT 化，而不是继续加场景；
- False PASS **≥1** → 判定链不可上线，回退到"只提示不判定"；
- 深度误差 **>20 cm 且标定无法收敛** → 放弃自动判定，只做可视化辅助；
- 操作员**零使用意愿** → 场景作废（价值假设被否证）。

**需要提前准备的现场条件**

卷尺与地面标记（模板已存在）；≥30 段录像的采集窗口；10 min 空场景；2 小时稳定性窗口；**两个不同时段的光照**（02 §4.2/§4.3 要求单独报告光照子集）；允许停 Studio；允许重标定前摄。

---

### 5.2 试点②（次选）：后视/盲区人员持续跟踪的**辅助预警** + near-miss 留痕（对应 P2）

**要回答的 4 个验证问题**

| # | 问题 |
|---|---|
| Q1 | 遮挡 ≥2 s 后重现，**ID 保持率能否 ≥95%**？IDSW/分钟能否 ≤1？ |
| Q2 | **1.5–3.5 m 内距离 P95 能否 ≤0.3 m**？>5 m 是否真的不该用于任何自动决策？ |
| Q3 | **10 min 空场景的误报率**是否在可接受范围？（02 §4.5：误报导致的信任崩塌比漏报更致命） |
| Q4 | 告警 **P95 <500 ms** 是否可达（本机深度 46 ms + 分割 81 ms 串行 ≈ 127 ms 预算内）？ |

**最小验证方法**

1. 复用**已验收**的 warn_app 链（01 §1.12）+ 行人检测（01 §1.13）+ 度量深度 TRT（01 §1.4）；
2. 在其上接入目标持续跟踪层（03 §6 S4），**只输出提示与事件记录**；
3. 采集 30 段"进入/横穿/接近/双人/部分遮挡"录像 + 10 min 空场景；
4. **明确不接**制动、不接联锁、不接急停（C20–C25）。

**成功判据**：ID 保持率 ≥95%；1.5–3.5 m 距离 P95 ≤0.3 m；10 min 空场景误报率被现场接受；告警 P95 <500 ms；感知不可靠时**必须进入 SYSTEM ERROR 而非 SAFE**（fail visible，C22）。

**失败判据**：ID 保持率 <80%（跟踪层无增量价值）；空场景误报率高到操作员会关掉告警（02 §4.5）；任何"提高阈值以消除误报"的动作导致漏报到不可接受 → 场景降级为纯录像留痕。

**需要提前准备的现场条件**：后摄可用（`ping`/RTSP 已通，01 §1.11）；允许后摄与 Studio **分时使用**（C6/C7）；现场有可辨识的**光照最差时段**（货架深处/月台/夜间装卸区，02 §4.2）。

---

### 5.3 现场验收负例（N1–N6）

> **来源**：tech-scout 提供（`tmp/forklift_negative_cases.md`），依据均已列在 `03_tech_feasibility.md` —— N1/N2 ← SAM 2 官方 Limitations + MOSE（SOTA J&F 59.4%）+ IDF1/HOTA；N3 ← `warning.md` 的 SYSTEM ERROR 与失效条件清单 + 反光地面/眩光深度可靠性文献；N4 ← 合成数据托盘检测「亮度降 80% → mAP50 3%」；N5 ← 仓库 `forklift_dual_demo.md` 负例清单与 `placement_accuracy.md` 模板；N6 ← PMC12788346 + OCaMo 在线标定监控。
> **判据数值原样保留，未新增任何数字。** 两个试点的验收都必须**同时通过对应负例**：负例不通过即该轮验收不通过。

| 负例 | 触发条件 | 期望系统行为 | 判定判据 |
|---|---|---|---|
| **N1 遮挡后重现的身份保持**（对应 S1 点击跟踪） | 目标被叉车/货架完全遮挡 ≥2 s 后在同一区域重现；同时段存在外观相近的干扰目标 | 恢复同一 ID 并重建掩码；不得把干扰目标继承为该 ID | 30 段现场录像上 ID 保持率 ≥95%，IDSW ≤1 次/分钟，并同时报告 IDF1；只用 mask IoU 验收视为不合格 |
| **N2 相似外观双目标交叉互换**（对应 S1/S4） | 两名着装相近人员（或两个同规格托盘）在视野内交叉/相互遮挡 | 两个 ID 全程不互换，交叉后各自延续 | 交叉事件中 ID 互换 0 次；HOTA/IDF1 一并报告；出现 1 次互换即该轮验收不通过 |
| **N3 反光/镜面/镜头污染导致单目深度失效**（对应 S4 盲区人员跟踪） | 强日照镜面反射、抛光/积水地面、镜头污渍，或连续 3 帧深度无效 | 进入 SYSTEM ERROR（fail visible），拒绝输出 SAFE 与距离结论；不得静默降级后继续给 SAFE | 整个失效区间内 0 次 SAFE 输出；10 min 空场景误报次数单独统计并给出上限 |
| **N4 亮度骤降/逆光下的自动判定**（对应 S1/S2/S3） | 照度下降至约 20%（对应文献中亮度降 80% 档）或强逆光 | 分割/检测置信度下降时显式降级——允许人工点选继续跟踪（S1），但 S2/S3 的自动 PASS 必须拒答 | 暗光下自动判定 0 次 PASS；拒答后提示操作员可在 ≤10 s 内通过重新点选恢复跟踪 |
| **N5 错位/错货位/冻结帧不得产生 False PASS**（对应 S3 放置复核，最坏失效） | 托盘位置偏移 20 cm、偏航 20°、标签 ID 错误、视频冻结、无操作员确认 | 一律不得 VERIFIED；进入 LOST/异常态并拒绝自动判定 | 上述任一情形 False PASS 率必须为 0（沿用仓库既有 A–J 负例模板，C/D/E/G/H/I/J 行全通过） |
| **N6 振动/标定漂移与大转角测量崩溃**（对应 S3/S4） | 运行中轻推相机、连续 30 min 振动后、或路径点 90° 转弯后的首次测量 | 重投影残差超阈或模板匹配失败时，拒绝自动判定并要求重标定/重测，不得沿用上一帧位姿继续执行 | 触发重标定/重测提示 ≥1 次且期间 0 次自动 PASS；标定有效期内地面单应测距 1.5–3.5 m 的 P95 ≤0.3 m，超区间一律拒答 |

---

## 6. 6–12 周路线图与"不做"清单

### 6.1 路线图（12 周，含 go/no-go 门）

| 周次 | 工作 | 出口判据（Gate） |
|---|---|---|
| **W1** | 本机环境复现：停 Studio 释放相机、从 HEAD 取回 3 个配置、重标定到实际运行分辨率、跑通 3D/placement 链 | **G1 门**：本机 FPS P50 与延迟实测数字落表（**用本机数字，禁止沿用 AGX**） |
| **W2–W3** | 试点①现场采集（≥30 段录像 + 卷尺 + 空场景）+ 掩码 SR@0.75 评估 | **G2 门**：SR@0.75 ≥0.8？ |
| **W4** | 试点①判定与卷尺验收；交互可用性观察 | **G3 门（go/no-go）**：False PASS = 0 且 P95 ≤10 cm？ |
| **W5–W6** | 若 G1 门未过：memory 侧优化 / ROI 裁剪 / EfficientTAM TensorRT 化；**同时修复 `configs/two_cameras.env` 的 `FRONT_IFACE`（eth0→eth2）** | FPS ≥10 或明确"该场景放弃" |
| **W7–W8** | 试点②（后视跟踪 + near-miss），严格"提示+留痕"口径 | ID 保持率 ≥95%、误报可接受 |
| **W9–W10** | 现场数据集建设：托盘孔 / 遮挡重现 / 反光地面各 100–300 段 + 标注 | 数据集可复现、含困难子集 |
| **W11** | **决策点**：是否升级 SAM 3/3.1（须先做 1 帧延迟实测；环境需 Python ≥3.12 / CUDA ≥12.6 / 门控权重）；是否引入安全激光雷达（若引入，视觉职责降为"冗余第二信息源"） | 书面决策 + 依据 |
| **W12** | 复盘：产品化路径（WMS/安全监控装置对接）、成本核算、是否进入产品化立项 | 复盘报告 |

### 6.2 "不做"清单（明确排除，不接受"后续优化"式回避）

1. **任何替代制动 / 联锁 / 急停 / 功能安全的承诺**；不宣称满足 ISO 3691-4 / ISO 13849 / IEC 61496 / GB/T 10827.4 / ANSI-ITSDF B56.5；不宣称任何 PL / SIL / Type 等级（依据：C25 全仓 0 命中 + G20；03 §7.1）。
2. **不做货叉自动进叉 / 自动对孔闭环**（依据：03 §3.1"对位不是分割问题"+ C1 无 LiDAR + C3 无 GT）。
3. **不做毫米级 / 亚厘米级测量**（依据：单目误差随距离二次增长，03 §3.4）。
4. **不做 10 m 级人员距离的自动决策**（依据：03 §3.4 有效距离上限约 3–5 m）。
5. **不做员工行为 / 绩效评估**，不把录像用于考核（依据：AI Act Annex III 第 4 项，02 §2.5）。
6. **不做 LiDAR / 点云路线**（依据：C1 本机无 LiDAR）。
7. **不承诺"零维护"标识方案**（AprilTag 依赖标签完好与摆放，G8）。
8. **不把厂商宣传当事实**：±3 mm、95% 托盘孔、97% 检出率、40%–93% 事故下降一律不作为承诺（02 §3.2/§6 第 6 条）。
9. **不在本机未复现前引用 AGX 源机的 FPS/精度数字**当作本机能力（01 §0.1 纪律、C16）。
10. **不为舞台效果承诺精度**：任何精度数字必须带 GT 与方法说明，否则不写。

---

## 7. 开放问题与需用户决策项

| # | 问题 | 为什么必须由用户/团队决定 | 影响 |
|---|---|---|---|
| 1 | **是否升级到 SAM 3 / 3.1？** | 官方仓库要求 **Python ≥3.12 / PyTorch ≥2.7 / CUDA ≥12.6 / 门控权重**，本机现为 Python 3.8.10 / JetPack 5.1.3；**Jetson 实时性完全未验证**（03 §1.9/§9.1） | 决定"点击"能否降级为"选类别"，也决定是否重构环境 |
| 2 | **现场是否已有安全级器件（安全激光雷达）？** | 决定 P2 的职责边界：有则视觉做冗余第二信息源；无则 P2 **必须**降级为"辅助预警"并写入文档（03 §9.2） | 决定 P2 是"可落地"还是"只能做提示" |
| 3 | **是否有真车验收时间窗？** | 现有 placement/warning 验收模板**全部标注未执行/待真机**（G6/G7） | 无窗口 ⇒ 所有精度只能停留在"演示级/未验证" |
| 4 | **是否允许自建数据采集与标注？** | 无公开的叉车托盘孔分割数据集（03 §4.1）；S2/S3 的精度若没有现场数据**只能停在演示级** | 决定 P4/P5 是否可评估 |
| 5 | **多路相机同时供电/带宽是否可行？** | C9/C10：两条 PoE 共用一个使能、`two_cameras.env` 网口配置错误 | 决定 03 §3.3 的"双相机路线"是否连讨论的前提都不成立 |
| 6 | **试点期能否停 Studio 独占 `/dev/video0`？** | C6/C7/G12：唯一 VideoCapture 单例、Studio 正在占用 | 不能停 ⇒ 试点①无法开始 |
| 7 | **是否允许重标定前摄为 2304×1296？** | G16/C5：现存标定档位与运行分辨率不符 | 不重标 ⇒ 所有 PnP/度量结果错误 |
| 8 | **"到位"的判定阈值来自哪里？** | 现场实测 vs 客户 SOP（02 §1.2） | 决定验收标准可不可签 |
| 9 | **是否需要与 WMS / 安全监控装置（TSG 81—2022 + 87 号文）对接？** | 02 §2.3/§5.6【推断】：中国侧真正的采购触发器是合规与责任留痕 | 决定结构化输出的格式与集成成本 |
| 10 | **是否接受"只做提示 + 记录"的产品定位？** | 这是全部红线场景（P2/P4/P6/P9）能否推进的前提 | 不接受 ⇒ 只剩 P1/P3 可做 |

---

## 8. 参考来源

> 汇总口径：**只收录 02 / 03 中实测可达（HTTP 200/202）的 URL**；反爬（403/406）、限流（429）与不可达（000）单独列出且**不作为关键结论的唯一支撑**。
> 完整逐条状态清单见 `02_industry_scan.md` §7 与 `03_tech_feasibility.md` §11.2。可达性为 2026-09-14 实测值。

### 8.1 标准、法规与合规（可达）

- ISO 3691-4:2023 官方预览 PDF（术语 3.5/3.18、规范性引用 13849-1、61496-2/-3）：<https://cdn.standards.iteh.ai/samples/83545/a3d9d057a08d4f9c8e8e87cdc947583c/ISO-3691-4-2023.pdf>
- ITSDF B56 标准页（ANSI/ITSDF B56.5-2024，2025-12-16 生效）：<https://www.itsdf.org/cue/b56-standards.html>
- ANSI/ITSDF B56.5 适用范围说明：<https://info.mobilerobot.com/safety-compliance/standards/ansi-itsdf-b56-5>
- IEC TS 61496-4-3:2022（视觉保护装置 VBPD，含立体视觉）：<https://webstore.iec.ch/en/publication/63436>
- SICK safeVisionary2 产品资料 PDF（PL c / SIL1 / Type 2；≤2 m 保护域；双相机才到 PL d）：<https://cdn.sickcn.com/media/docs/2/12/112/product_information_safevisionary2_safety_camera_sensors_en_im0103112.pdf>
- SICK safeVisionary2 产品页：<https://www.sick.com/us/en/catalog/products/safety/safe-3d-cameras/safevisionary2/c/g568562>
- ISO 3691-4 解读（PL d、0.3 m/s、200×600 与 70×400 测试体）【二手·厂商博客】：<https://www.fabrico.io/blog/iso-3691-4-driverless-industrial-trucks>
- 安全激光扫描仪认证等级（IEC 61496 Type 3 / SIL 2 / PL d）：<https://www.keyence.com/products/safety/laser-scanner>
- Type 3 保护域与 EN 61496-1：<https://www.sick.com/ch/en/the-perfect-size-of-the-protective-field-on-an-industrial-autonomous-vehicle/w/blog-size-protective-field-industrial-autonomous-vehicle>
- IEC 61496 系列导论（含 5% 漫反射系数）：<https://ez.analog.com/ez-blogs/b/engineerzone-spotlight/posts/an-introduction-to-the-iec-61496-series-of-human-presence-detection-standards>
- 安全扫描仪测试件 70 mm / 1.8% 反射率（厂商博客，【待确认】）：<https://industrialsafetysensor.com/blog/safety-laser-scanner-for-agv-amr-guide>
- 安全级 vs 普通 LiDAR：<https://www.roboticstomorrow.com/article/2023/11/what-are-the-differences-between-safe-lidar-and-lidar/21471>
- 安全扫描仪警告区非安全输出：<https://plcprogramming.io/blog/safety-laser-scanner-explained>（429 限流）
- 欧盟机械法规 (EU) 2023/1230：<https://eur-lex.europa.eu/eli/reg/2023/1230/oj>
- 欧盟 AI Act (EU) 2024/1689：<https://eur-lex.europa.eu/eli/reg/2024/1689/oj>
- AI Act 第 6 条（高风险规则）：<https://artificialintelligenceact.eu/article/6>
- AI Act 第 6 条（欧委会 Service Desk）：<https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-6>
- AI Act Annex III 第 4 项（员工监控高风险）：<https://artificialintelligenceact.eu/annex/3>
- 机械法规 2027 适用解读（二手）：<https://physical-ai-safety.com/blog/eu-machinery-regulation-2027-primer>
- GB/T 10827.4—2023（等同采用 ISO 3691-4:2020）：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=1F4740F4028C1B1C29D7D93D6BB165F8>
- GB/T 10827.4—2023 标准页（含等同采用说明）：<https://www.hnbzw.com/Standard/StdDetail.aspx?ekdHR4nH7qql91MlNWgnvlYlUZZ02WeF=>
- GB/T 10827.1—2014（自行式工业车辆）：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=B9432660D53D30062F1E65A1F59A2A61>
- GB/T 38893—2020《工业车辆 安全监控管理系统》：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=97D3C8DCDDC372940F7BC33A840F8CE5>
- GB/T 36507—2023（等同采用 ISO 21262:2020）：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=1E96E92F7C36EABAD6F22A4A4E49FA86>
- GB/T 2934—2007（托盘尺寸，修改采用 ISO 6780）：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=B9CFF67017751DB45C09518434419076>
- 市监特设发〔2022〕87 号全文（2023-12-01 起新出厂叉车须装安全监控装置）：<http://www.hunan.gov.cn/zqt/zcsd/202209/t20220927_29019201.html>
- 江苏 DB32/T 4925—2024 场车智慧安全监管系统：<https://dbba.sacinfo.org.cn/portal/download/a55ff34220faff3e09be36a5d4cc93da7789535c2f1b3ed97e6606b792c0ecb6>
- TSG 81—2022 二手解读（权限采集器/罚款，【待确认】）：<https://www.ningboruyi.com/xingyezixun/265.html>
- OSHA 1910.178（动力工业车辆培训/认证）：<https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.178>
- OSHA 动力工业车辆设计标准更新（Federal Register 2022-02-16）：<https://www.osha.gov/laws-regs/federalregister/2022-02-16>
- 《个人信息保护法》：<http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html>

### 8.2 学术与技术（可提示分割/跟踪）

- SAM：<https://arxiv.org/abs/2304.02643>
- SAM 2：<https://arxiv.org/abs/2408.00714> ｜ 仓库 <https://github.com/facebookresearch/sam2>
- SAM 3：<https://arxiv.org/abs/2511.16719> ｜ 仓库 <https://github.com/facebookresearch/sam3>
- EfficientTAM：<https://arxiv.org/abs/2411.18933> ｜ 仓库 <https://github.com/yformer/EfficientTAM>
- MobileSAM：<https://arxiv.org/abs/2306.14289> ｜ 仓库 <https://github.com/ChaoningZhang/MobileSAM>
- EfficientSAM：<https://arxiv.org/abs/2312.00863>
- EdgeSAM：<https://arxiv.org/abs/2312.06660>
- FastSAM：<https://arxiv.org/abs/2306.12156>
- NanoSAM（NVIDIA，**Jetson 时延表**）：<https://github.com/NVIDIA-AI-IOT/nanosam>
- SAMURAI：<https://arxiv.org/abs/2411.11922>
- SAM2Long：<https://arxiv.org/abs/2410.16268> ｜ 仓库 <https://github.com/Mark12Ding/SAM2Long>
- XMem：<https://arxiv.org/abs/2207.07115>
- Cutie：<https://arxiv.org/abs/2310.12982>
- DEVA：<https://arxiv.org/abs/2309.03903>
- SAM2 空间 re-ID 扩展：<https://github.com/MaanaRajesh/sam2-spatial-reid>
- Ultralytics SAM 2 文档（含"约 44 FPS"【宣传】，硬件未注明）：<https://docs.ultralytics.com/models/sam-2/>
- YOLOv8+SAM 两阶段不稳定的报告：<https://arxiv.org/abs/2402.07098>

### 8.3 Jetson / 边缘实时性

- NVIDIA Jetson 官方基准：<https://developer.nvidia.com/embedded/jetson-benchmarks>
- SAM2 在 AGX Orin 的社区提速实测（含失败项）：<https://github.com/sstc-aiteam/sam2-speedup/blob/main/bench/RESULTS.md>
- NVIDIA 论坛 SAM2 on AGX Orin（用户报 2 FPS）：<https://forums.developer.nvidia.com/t/sam2-segmentation-on-jetson-agx-orin/325069>
- 第三方 NanoSAM 汇总（class C，延迟为倒数推导）：<https://edgeaistack.ai/benchmarks/jetson-orin-nano/nanosam/resnet18/>
- Jetson Orin Nano 上 SAM2 环境搭建（社区博客）：<https://ryogayuzawa.github.io/jetson-sam2-setup/>

### 8.4 叉车/AGV 子任务、托盘与失效模式

- Lang2Lift（户外叉车，SAM-2 + 6D 位姿，含容差与失败案例）：<https://arxiv.org/abs/2508.15427>
- 广角相机测托盘倾斜并自动插叉：<https://arxiv.org/abs/2602.16178>
- ICP 托盘跟踪（斜坡卸货，**需 LiDAR**）：<https://arxiv.org/abs/2602.16744>
- PIRATR（点云 6-DoF 参数化检测，**需 LiDAR**）：<https://arxiv.org/abs/2602.05557>
- 多专家 RL（叉车长时任务）：<https://arxiv.org/abs/2601.07304>
- 合成数据托盘检测与定位（mAP50/位置/旋转精度）：<https://arxiv.org/abs/2503.22965>
- EPAL 欧洲托盘官方产品资料（144 mm 总高、100 mm 入口高度、17×45° 倒角）：<https://www.epal-pallets.org/fileadmin/user_upload/ntg_package/images/mediathek/DU_GB_EPAL_1_Produktdatenblatt_low.pdf>
- 全向相机图像测量法对孔：<https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346>
- 单目托盘/托盘孔检测（95%/72%）：<https://arxiv.org/abs/2511.06295>
- 点击/选择 + HQ-SAM 驱动操作：<https://pmc.ncbi.nlm.nih.gov/articles/PMC12375720>
- 指向手势的人机协作：<https://research.tuni.fi/app/uploads/2023/11/7b55b10c-ieee_co_speech_cr-1_optimized.pdf>
- LOCO 物流数据集（叉车/托盘检测，**仅检测框无掩码**）：<https://github.com/tum-fml/loco>
- 货叉线激光对准辅助（【宣传】）：<https://meijer-handling-solutions.com/products/kooi-next/laser/>
- 单应地面测距误差二次增长：<https://arxiv.org/abs/2604.10805>
- 反光地面/眩光下的深度可靠性融合：<https://arxiv.org/abs/2606.03421>
- 学习式立体深度抗透明/镜面（D3RoMa）：<https://pku-epic.github.io/D3RoMa/>
- 强日照镜面导致系统性立体深度偏置（社区）：<https://community.stereolabs.com/t/specular-lighting-causing-systematic-stereo-depth-bias-drift/10371>
- 在线相机-LiDAR 标定监控与旋转漂移跟踪（OCaMo）：<https://github.com/moravecj/OCaMo>
- Depth Anything V2：<https://arxiv.org/abs/2406.09414> ｜ Depth Pro：<https://arxiv.org/abs/2410.02073>
- 单目深度评测指标（Eigen 等）：<https://arxiv.org/abs/1406.2283>
- 结构光 vs iToF 深度相机：<https://www.orbbec.com/blog/structured-light-vs-itof-depth-cameras>
- ToF 环境光/多径与滤波：<https://tofsensors.com/blogs/tof-sensor-knowledge/3d-camera-filter-explained-how-tof-depth-cameras-improve-accuracy>
- 3D 深度技术选型（结构光/ToF 局限）：<https://www.lips-hci.com/post/3d-depth-technology-selection-guide>
- 叉车安全综述（扁平视频缺深度感、窄通道/月台/货架遮挡）：<https://www.diva-portal.org/smash/get/diva2:1969398/FULLTEXT01.pdf>
- 低光/雨雾行人检测退化与热成像补充：<https://www.lynred.com/blog/how-thermal-imaging-contributing-development-new-generation-nighttime-pedestrian-detection>
- 镜头污染检测问题域：<https://eureka.patsnap.com/blog/scout-report/camera-lens-contamination-detection-dirt-water-droplets-and-reliability-of-vision-systems>

### 8.5 指标与数据集

- DAVIS 2017（J/F/J&F）：<https://arxiv.org/abs/1704.00675> ｜ <https://davischallenge.org/>
- MOSE（复杂场景 VOS，SOTA 仅 59.4% J&F）：<https://arxiv.org/abs/2302.01872>
- LVOS（长时 VOS）：<https://arxiv.org/abs/2211.10181>
- IDF1：<https://arxiv.org/abs/1609.01775> ｜ HOTA：<https://arxiv.org/abs/2009.07736>
- MOT16：<https://arxiv.org/abs/1603.00831>
- DanceTrack（专测 ID 关联）：<https://arxiv.org/abs/2111.14690> ｜ <https://github.com/DanceTrack/DanceTrack>
- SA-V 数据集 README：<https://github.com/facebookresearch/sam2/blob/main/sav_dataset/README.md>（429 限流，路径有效）

### 8.6 行业、市场与事故数据

- Kocchi's 叉车相机系统（叉尖视角/激光对孔）：<https://www.kocchis.com/camera-systems-for-forklift-trucks>
- Kocchi's AI 行人检测相机（【宣传】）：<https://www.kocchis.com/products/forklift-ai-pedestrian-detection-system>
- Holland Vision Systems 高位/深位应用：<https://hollandvisionsystems.com/applications/high-reach-deep-reach>
- HUBTEX 叉车相机百科：<https://www.hubtex.com/en-us/wiki/camera-systems-truck>
- SICK 3D snapshot 托盘孔检测案例（【宣传】）：<https://www.sick.com/cz/en/precise-detection-of-pallet-pockets-using-a-3d-snapshot-camera/w/blog-pallet-pocket-detection-transolt>
- 蓝芯科技 3D 视觉托盘对接（±3 mm 等，【宣传】）：<https://www.lanxincn.com/news_cont_229.html>
- 图漾相机栈板识别（【宣传】）：<https://seer-robotics.ai/zh/media/17.0>
- Zebra 叉车免手动托盘扫描（【宣传】）：<https://www.zebra.com/cn/zh/resource-library/use-case-library/forklift-mounted-pallet-scanning.html>
- Roboflow pallet tracking（【宣传】）：<https://roboflow.com/ai/pallet-tracking>
- Vimaan 仓储视觉应用（【宣传】）：<https://vimaan.ai/what-are-the-most-common-applications-of-warehouse-computer-vision>
- Visionify 仓储库存视觉案例（【宣传】）：<https://visionify.ai/case-studies/warehouse-inventory-management>
- iFactory 托盘载荷稳定性检查（【宣传】）：<https://ifactoryapp.com/ai-vision-camera/ai-vision-pallet-load-stability-inspection>
- iFactory 窄通道人员进入告警（【宣传】）：<https://ifactoryapp.com/industries/cement-plant/ai-forklift-pedestrian-collision-avoidance>
- iFactory 仓储自动化清单（【宣传】）：<https://ifactoryapp.com/ai-vision-camera/ai-warehouse-automation-and-inventory-management-using-computer-vision>
- iFactory 视觉失效模式（【宣传】）：<https://ifactoryapp.com/industries/food-manufacturing/ai-inspection-failure-modes-when-vision-systems-miss>
- OxMaint 货损检测（97%/0.4 s，【宣传】）：<https://oxmaint.com/industries/fleet-management/ai-cargo-inspection-freight-damage-detection>
- AC&A：Duvel Moortgat 托盘破损视觉检测实际部署（集成商案例）：<https://acagroup.be/en/cases/innovative-ai-model-detects-damaged-pallets-at-duvel-moortgat>
- 托盘破损分类研究：<https://zenodo.org/records/10391024>
- SensorZone 仓储接近告警（【宣传】）：<https://www.sensorzone.io/warehousing>
- Voxel 固定相机安全分析（【宣传】）：<https://www.voxelai.com/solutions-safety>
- Protex AI 仓储安全指南（【宣传】）：<https://www.protex.ai/guides/complete-guide-to-ai-warehouse-safety>
- WTSAFE 叉车盲区检测（【宣传】）：<http://www.wt-safe.com/Products/BlindSpot.html>
- Rear View Safety 360° 行人检测（【宣传】）：<https://www.rearviewsafety.com/360-ai-pedestrian-detection-system.html>
- STONKAM 叉车防撞 3 路相机（【宣传】）：<https://www.stonkam.com/explore/STONKAM-Forklifts-Anti-Collision.html>
- Eway Safety AI 叉车相机科普（【宣传】）：<https://www.ewaysafety.com/blogs/article/ai-forklift-camera-systems-reducing-blind-spots-in-warehouse-operations>
- 载荷力矩指示器 LMI/SLI（【宣传】）：<https://szlmi.com/product/forklift-load-indicator>
- Kocchi's 无线叉车相机价格构成（【宣传】）：<https://www.kocchis.com/blog/wireless-forklift-camera-guide>
- Samsara 车载 AI 相机/教练闭环（【宣传】）：<https://www.samsara.com/products/cameras>
- Lytx vs Samsara（车队视频格局）：<https://www.lytx.com/vs/samsara>
- 员工隐私与 AI 安全相机（厂商博客）：<https://www.inviol.com/post/privacy-and-ai-safety-cameras-how-to-protect-worker-identities>
- 叉车工伤/成本二手汇总（【待确认】）：<https://www.inviol.com/post/the-real-cost-of-forklift-accidents-insurance-downtime-and-human-impact>
- Voxel 叉车事故统计（【待确认】）：<https://www.voxelai.com/industry-insights/forklift-accident-statistics>
- 叉车保险费用（二手，【待确认】）：<https://www.logrock.com/uncategorized/forklift-truck-insurance>
- OSHA top-10 违规（培训服务商博客，【待确认】）：<https://forklifttraining.com/osha-top-10-citations-2024>
- 后装 AI 相机目录价 US$380–555（B2B 目录）：<https://veise2008.en.made-in-china.com/product/cZQtxbjlCofy/China-Forklift-Ai-Pedestrian-Blind-Spot-Detection-Camera-System-with-Remote-Control-for-Adjustment-of-Reversing-Auxiliary-Line-Warning-Zone.html>
- 无线叉车相机套件 US$695：<https://www.forkliftinnovations.com/product-page-2/high-sight-wireless-forklift-camera-system>
- 2024 年中国叉车销量 128.55 万台：<https://m.chinaforklift.com/news/detail/202504/89378.html>
- 2023 年中国叉车保有量约 569.3 万辆：<https://www.huaon.com/channel/trend/1085177.html>
- 券商测算国内叉车保有量约 400 万台（口径不同）：<https://pdf.dfcfw.com/pdf/H3_AP202409191639932155_1.pdf>
- 2024 年中国无人叉车 2.45 万台/约 50 亿元/渗透率 1.91%：<https://finance.sina.com.cn/roll/2026-01-24/doc-inhikrie2729186.shtml>
- AGV 叉车 4,576 台（口径分歧）：<https://www.chyxx.com/industry/1229637.html>
- 2024 年场车事故 43 起/死亡 36 人：<https://www.yncj.gov.cn/cjxzfxxgk/aqsj1525/20250620/1608266.html>
- 2025 年特种设备事故 188 起/156 人、87.36% 管理不当：<https://www.cqszzs.com/equipment-36/6642.html>
- 叉车事故典型案例（无证/站人/超用途）：<http://m.cpqr.net/zfal/tzsb/14229.html>
- 防撞市场规模（口径互相矛盾，【待确认】）：<https://www.sphericalinsights.com/blogs/top-20-companies-in-the-global-forklift-collision-avoidance-system-market-2026-2035-spherical-insights-analysis> ｜ <https://www.knowledge-sourcing.com/report/forklift-360-degree-camera-market> ｜ <https://www.persistencemarketresearch.com/market-research/forklift-truck-safety-solutions-market.asp> ｜ <https://www.wiseguyreports.com/reports/forklift-collision-avoidance-system-market>

### 8.7 反爬 / 限流 / 不可达来源（**不作为关键结论的唯一支撑**）

| URL | 状态 | 说明 |
|---|---|---|
| <https://www.iso.org/obp/ui/es#!iso:std:83545:en> | 403 | ISO OBP 目录；**哈希锚点内容无法被命令行证实**（verifier 抽查确认 `r.jina.ai` 仅返回平台首页），ISO 3691-4 内容改由官方预览 PDF 佐证 |
| <https://www.iso.org/standard/83545.html> | 403 | ISO 商店页（Cloudflare 反爬，非死链） |
| <https://blog.ansi.org/ansi/iso-3691-4-2023-driverless-industrial-trucks> | 403 | ISO 3691-4:2023 概述 |
| <https://www.pilz.com/en-US/company/news/articles/238928> | 403 | ISO 3691-4 安全功能/PL 说明 |
| <https://www.controleng.com/ensuring-agv-safety-with-standards-compliance> | 403 | Type-C 标准、ISO 13849 对齐 |
| <https://webstore.ansi.org/standards/ansi/ansiitsdfb562024> | 403 | ANSI 商店页 |
| <https://www.bls.gov/iif/factsheets/fatal-occupational-injuries-forklifts-2017.htm> | 403 | 美国叉车伤亡（74/9,050/1,850）——**verifier 已用 `r.jina.ai` 取回正文并核对三个数字**，内容可核 |
| <https://injuryfacts.nsc.org/work/costs/workers-compensation-costs> | 403 | NCCI 平均失时索赔 $47,316 |
| <https://injuryfacts.nsc.org/work/safety-topics/forklifts> | 403 | 2024 年 84 起死亡、25,110 DART |
| <https://www.ifm.com/gb/en/shared/productnews/2024/sps/complete-solution-for-pallet-pocket-recognition> | 403 | ifm 托盘孔识别（规格未验证） |
| <https://osha.europa.eu/en/legislation/directive/regulation-20231230eu-machinery> | curl 000 | 02 自报"不可达"；**verifier 实测经 `r.jina.ai` 可取回正文（200）→ 02 自报过悲观** |
| <https://www.ncsnc.com/news/672.html> | 000 / 422 | **两条通道均失败 → 确认为失效源**（仅作 GB/T 10827.4 等同采用的备用证据） |
| <https://www.eet-china.com/mp/a133838.html> | 405 | 反爬（非失效），可经阅读器取回 |
| <https://www.mdpi.com/1424-8220/26/10/2987> | 403 | 低光行人检测研究 |
| <https://www.automationworld.com/factory/sensors/article/55374555/how-industrial-vision-systems-beat-dust-heat-and-vibration-to-stay-sharp-on-the-factory-floor> | 403 | 粉尘/振动/光照 |
| <https://www.cascorp.com/us/en/weighforks> | 403 | 称重货叉 |
| <https://advanced.onlinelibrary.wiley.com/doi/10.1002/aisy.202400404> | 403 | 可提示分割的交互式抓取 |

---

## 9. 本报告的已知局限（供 t5 独立核验）

1. **本报告未做任何客户访谈**（沿用 02 附录 A 的诚实声明）；02 §3.3 的痛点排序是【推断】。
2. **本报告未在本机运行任何代码、未复现任何 FPS/精度数字**——所有本机数字均引自 01 的既有产物（`states.jsonl`、`logs/studio_server.log` 等），其中"测试全绿"类是**引用文档结论，非本次复现**（01 §1.2 自陈）。
3. **参考来源的可达性状态继承自 02/03 的自测与 verifier 的独立抽查**，本报告未重新逐条实测；已知 02 的 `000` 类存在**误报**（verifier §3 条目 B/G），本报告已在 §8.7 如实标注。
4. **一处跨文档口径冲突已显式处理**：03 的"本项目自有实测 25.85 FPS"实测于 **AGX 源机**，本报告在涉及本机能力时一律改用 01 的 **12.3 FPS**（详见 §0.3、§3.1、§4.3）。
5. **人日成本量级均为本报告【推断】**，02/03 未提供成本口径；外部价格信号（$380–695/台车）为【宣传/目录价】。
6. **输入稿在综合期间处于并发写入状态**（03 在 t3 终态后又被追加，字节数 56,725 → 57,884 → **66,727**，现已冻结于 2026-09-14 18:20:38）。本报告引用的是**写作时刻的快照**；对写入后新增且影响结论的一条事实（同行评审实车 6 次插叉成功 3 次）已回填到 §3.4。**建议 t5 以"本报告 + 输入稿快照指纹"为准进行核验**，快照指纹（2026-09-14，完整 SHA-256）：

   | 文件 | 字节数 | 完整 SHA-256 |
   |---|---|---|
   | `01_capability_baseline.md` | 44,590 | `195a11fd3ab255316dee55186f46ed37f514c06f2d0d875c1d07d1c770ddc685` |
   | `02_industry_scan.md` | 68,526 | `9b63ba3f20cc722ac9ce4dad53b775f1eb014e80029bab15d6f1cd191548028e` |
   | `03_tech_feasibility.md` | 66,727 | `52d7e3c69870ec64fd4b8aef77796c399caf7c237607f36fd31e9cad47e8af49` |
   | `README.md`（本报告） | 见文件本身 | 交付后由 t5 记录 |

   `03_tech_feasibility.md` 冻结于 2026-09-14 18:20:38（t3 终态后被追加，增量见其变更记录小节）；README 结论基于 18:13 综合快照，增量经 t5 判定不改变结论。