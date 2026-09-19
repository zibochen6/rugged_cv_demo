# 03 技术可行性：可提示分割/跟踪（Promptable Segmentation & Tracking）在叉车子任务上的路线、门槛与失败模式

- 任务：t3（负责人 tech-scout）
- 交付物：本文件（本地草稿），供 t4（综合主报告）引用
- 写作日期：2026-09-14
- 写作语言：中文，保留英文术语
- 范围：只回答"技术上能不能做、门槛在哪、会怎么失败"；不替代 t1 的仓库/设备硬约束盘点，也不替代 t2 的行业·法规·市场扫描

## 0. 阅读指引、证据分级与本次调研的方法限制

### 0.1 三类标签

本文件所有关键结论都带标签：

- **【事实】**：可在公开来源（论文/官方仓库/标准页面/厂商产品资料）中直接核对的陈述，附 URL。
- **【宣传】**：厂商或项目自述的性能/能力，未经独立验证，附 URL 并显式标注来源性质。
- **【推断】**：本文作者基于已知事实做的工程推算或判断，未在真实设备上验证。
- **【实测·本仓库】**：来自 `staging/forklift_scenario_research/_remote_evidence/`（repo-scout 抓取的远程 Jetson 仓库证据）中记录的实测数字。这类数字属于"仓库自述实测"，**不是**本文作者实测，且仓库文档中多处明确标注"验收目标尚未执行/待真机验证"，引用时保留原限定条件。

### 0.2 检索方法与工具限制（重要，请核验者注意）

本次调研中，会话内置的 `web_search` 与 `web_fetch` 工具**不可用**：

- `web_search` 返回 `HTTP 401 Authentication Fails`（搜索端点 `https://api.deepseek.com/anthropic/v1/messages` 配置错误，属插件配置问题，只有用户能改）。
- `web_fetch` 报 `URL hostname resolves to a non-public IP address`（本机 DNS 处于 fake-IP 代理网段 198.18.0.0/15，被工具的非公网 IP/SSRF 防护拦截）。

替代方法（本文件实际使用）：

1. **正文级抓取（首选顺序）**：
   - `https://r.jina.ai/<原始URL>`：本环境验证可用的**内容级兜底通道**（返回 200 并给出 Markdown 正文）。本次已用它复核原先直连被拦的来源，详见 §11.2。
   - Tavily MCP 的 `tavily_search` / `tavily_extract` / `tavily_crawl`（`https://mcp.tavily.com/mcp`，用未过期的 OAuth token）；
   - 直连 `curl -sL`（对 arXiv / PMC / GitHub raw / 官方 PDF 通常一次成功）。
2. **可达性判定**：`curl -sL -o /dev/null -w '%{http_code}' <url>`；**直连失败不等于失效**，必须用 `r.jina.ai` 复测后再下结论。本文件的状态标注只分四类：`200 可达` / `403 反爬（非失效）` / `405 方法不允许（用 r.jina.ai 复核）` / `000 两通道均失败=失效`。
3. **检索**：`curl` 调 DuckDuckGo HTML 端点（POST）与 `cn.bing.com`（两者都有反爬/限流，DuckDuckGo 会返回 anomaly 页，需退避重试），必要时用 Tavily MCP 搜索。检索覆盖度不如正常搜索引擎。
4. **本地校验**：抓到的 PDF 用 `pdftotext -layout` 提取后人工比对（EPAL、ISO 3691-4 预览、SICK 三份 PDF 均如此处理）。

Tavily MCP 的 token 陷阱（已复现）：`~/.mcp-auth/mcp-remote-v1/*_tokens.json` 的 token 有效（exp 2026-09-16）；`mcp-remote-0.1.37/` 与 `mcp-remote-0.2.1/` 两份**已过期**，用它们会报 `Missing mcp-session-id header`。流程：`initialize`（取响应头 `mcp-session-id`）→ `notifications/initialized` → `tools/call`。

**引用纪律**：所有被引用 URL 均在 2026-09-14 实测（结果见 §11.2）。`403` 一律标注为"站点反爬，非失效"，其支撑内容改用同主题的官方 PDF / 官方页面交叉佐证，或经 `r.jina.ai` 复核；站内仍有反爬的，**只引用标题/摘要能支撑的部分，不据此写精确数字**；`000`（两通道均失败）视为失效，**不用于支撑结论**。本文件不存在任何"经 `web_fetch` 验证"的表述——该工具在本会话不可用。

局限：

- 搜索引擎受限，检索不可能穷尽；本文件是"足以支撑决策的抽样"，不是系统性综述。
- ISO 商店页即使在 `r.jina.ai` 下仍返回 Cloudflare 挑战页（仅得标题），因此 ISO 3691-4 的条款细节只能引用 ISO 官方预览 PDF + 二手解读，二手解读已单独标注。
- 文献中的 FPS/精度数字硬件条件各异（A100 / iPhone / Jetson Orin Nano / Orin NX / AGX Orin），本文一律保留原始硬件与精度条件，禁止跨硬件直接比较。

## 1. 结论速览（TL;DR）

1. **"点击分割/跟踪"这一层能力已经具备**：本仓库已有 EfficientTAM-Ti 512×512 在 Jetson AGX Orin 64GB 上的在线 click-to-track（单次点击 → 时序记忆传播）。实测表：MAXN FP32 38.7 ms/帧（25.85 FPS），FP16 49.1 ms/帧（20.35 FPS）；README 快速开始另称"约 25 FPS 批量 / 约 15 FPS 在线"（口径未在实测表中展开）。**该能力可以直接作为"操作员指定目标 → 持续跟踪"的通用积木**，不需要新硬件。【实测·本仓库】
2. **瓶颈不在图像编码，而在时序记忆注意力**：AGX Orin MAXN FP32 下 memory_attention 占 18 ms（47%）、image_encoder 13.5 ms（35%）；换成更小的图像编码器收益有限，**要提帧率应优先压缩/复用 memory 侧**（滑动窗口 + 剪枝已经能把显存压平到 ~0.4 GB）。【实测·本仓库】【推断】
3. **通用可提示分割的"零样本能用"和"工业级可用"之间有明确落差**：在真实户外叉车数据上（Lang2Lift，Florence-2 + SAM-2），整体 mIoU 0.587、IoU≥0.75 成功率仅 52.7%；**遮挡条件下 mIoU 掉到 0.332–0.481**；复杂遮挡场景的 VOS 基准 MOSE 上，SOTA 的 J&F 只有 59.4%（同一批方法在 DAVIS 上约 90%）。**结论：可提示分割适合做"目标锁定/ROI/掩码精修"，不能单独承担安全停止或高精度对位。**【事实】
4. **叉车对位是"厘米级+角度级"问题，且本质上不是分割问题**：EPAL 欧洲托盘货叉入口高度 100 mm（+5/-0）；独立同行评审的"图像测量插入货叉"研究给出**允许误差：Y 向位置 ≤50 mm、偏航 ≤3°、俯仰倾角估计 ≤1°**，而其**6 次实车插叉只成功 3 次**（失败原因是检测区混入货架立柱强边缘、大转角时投影图模板匹配失败等）；Lang2Lift 的操作容差为横向 ±0.05 m、垂向 ±0.04 m；合成数据训练的专用检测器在 5 m 正视条件下可做到位置误差 <4.2 cm、旋转 8.2°、单托盘 mAP50 0.995。**提示分割可以给出托盘/货物边界，但对位精度由深度/标定/位姿模块与机械时序决定。**【事实】
5. **把 SAM-2 塞进对位回路，延迟主因不是分割而是 6D 位姿**：Lang2Lift 实测分割 0.04 s（25 Hz），位姿估计与调整 0.83 s（1.2 Hz），整条感知链 1.05 s（0.95 Hz），并明确说明该时间对应**研究原型配置（research prototype configuration），不是在嵌入式计算平台上的部署结果**，边缘部署被列为未来工作。**"分割很快、位姿很慢"是本项目选路线时最重要的时间预算事实。**【事实】
6. **行人检测/安全停止不能由本项目现有相机栈承担**：ISO 3691-4:2023 把"人员检测装置（personnel detection means）"定义为"检测叉车路径上人员的系统"，并引用 ISO 13849-1 与 IEC 61496 系列；市面上唯一有安全认证的 3D 相机 SICK safeVisionary2 只到 **PL c / SIL1 / Type 2**，要达 PL d 需要**两台相机组合**。**结论：视觉可做"辅助预警/盲区可见化"，安全停止必须由安全激光雷达等安全级器件承担；且任何"用点击分割替代制动"的说法都必须明确拒绝。**【事实】
7. **失败模式高度集中且可枚举**：SAM 2 官方限制清单（跨镜头切换、拥挤场景、长时间遮挡、长视频、细结构快速运动、外观相似邻近物体、多目标各自独立无交互）+ 反光/镜面（主动立体与 ToF 的 IR 失效、强日照镜面导致系统性深度偏置）+ 低照度（合成数据训练的托盘检测器在亮度降 80% 时 mAP50 掉到 3%）+ 相机振动/标定漂移（OCaMo 类在线监控）。**每一种都有可观测信号与工程缓解手段，见 §5。**【事实】
8. **最匹配"可提示分割"的 5 个子问题**（详见 §6）：① 操作员点击指定目标后的持续跟踪（含越遮挡重现）；② 托盘/货物/货位的掩码级 ROI 提取与边界精修（喂给位姿/测量模块）；③ 放置到位与"是否正确货位"的视觉复核（二值判定，非安全功能）；④ 盲区/危险区的人员与车辆持续跟踪（辅助预警）；⑤ 类别+点击混合的目标检索（用 SAM 3 的 Promptable Concept Segmentation 替代逐个点击）。**【推断，基于 §2–§5 证据】
9. **升级路线不是免费的**：SAM 3（2025-11）/SAM 3.1（2026-03）带来开放词汇概念分割与"所有匹配实例的唯一 ID"，但官方仓库要求 **Python ≥3.12、PyTorch ≥2.7、CUDA ≥12.6、门控 checkpoint、可选 flash-attn-3**；本仓库现有环境是 Python 3.10 + JetPack 6.2.1。**"要不要升级"应作为显式决策项，而不是默认动作**（升级后需重新做实时性实测）。【事实 + 推断】

## 2. 可提示分割/跟踪的技术现状与能力边界

### 2.1 方法谱系（按"图像提示分割 / 视频跟踪"两类）

| 方法 | 类型 | 关键能力 | 公开性能/规模（原文献口径） | 来源 |
|---|---|---|---|---|
| SAM | 图像，可提示 | point/box/mask 提示，零样本迁移；SA-1B（11M 图、1B mask） | 提出"可提示分割"任务范式 | [arXiv:2304.02643](https://arxiv.org/abs/2304.02643) |
| SAM 2 / 2.1 | 图像+视频，可提示 | streaming memory，PVS 任务（masklet），可任意帧补提示修正 | 图像比 SAM 更准且快 6×；视频交互次数少 3×；2.1 系列 tiny 38.9M/91.2 FPS、small 46M/84.8 FPS、base+ 80.8M/64.1 FPS、large 224.4M/39.5 FPS（**A100 + bf16 AMP + torch.compile**），SA-V test J&F 76.5/76.6/78.2/79.5 | [arXiv:2408.00714](https://arxiv.org/abs/2408.00714)、[github README](https://github.com/facebookresearch/sam2) |
| EfficientTAM | 视频，可提示 | 用普通轻量 ViT 替换多级编码器 + 高效 memory 模块 | 与 SAM 2(HieraB+) 精度相当，A100 快 ~2×、参数少 ~2.4×；图像任务比 SAM 快 ~20×、参数少 ~20×；**iPhone 15 Pro Max ~10 FPS** | [arXiv:2411.18933](https://arxiv.org/abs/2411.18933)、[github](https://github.com/yformer/EfficientTAM) |
| MobileSAM | 图像，可提示 | 解耦蒸馏 TinyViT 编码器 | 比原 SAM 小 60×+，单 GPU ~10 ms/图（编码 8 ms + 解码 4 ms） | [arXiv:2306.14289](https://arxiv.org/abs/2306.14289) |
| EfficientSAM | 图像，可提示 | SAMI 掩码图像预训练 | 零样本实例分割比同类快模型 +~4 AP（COCO/LVIS） | [arXiv:2312.00863](https://arxiv.org/abs/2312.00863) |
| EdgeSAM | 图像，可提示 | 纯 CNN 编码器 + prompt-in-the-loop 蒸馏 | 比原 SAM 快 37×；边缘设备上比 MobileSAM/EfficientSAM 快 7×+；iPhone 14 上 >30 FPS | [arXiv:2312.06660](https://arxiv.org/abs/2312.06660) |
| FastSAM | 图像，"先分割再提示" | 用 YOLOv8-seg 把任务转成实例分割 | 用 1/50 SA-1B 训练，精度与 SAM 相当，速度 50× | [arXiv:2306.12156](https://arxiv.org/abs/2306.12156) |
| NanoSAM | 图像，可提示 | MobileSAM 编码器蒸馏成 ResNet18 → TensorRT engine | **Jetson 上有公开时延表**（见 §2.2） | [github](https://github.com/NVIDIA-AI-IOT/nanosam) |
| SAMURAI | 视频跟踪 | 运动感知 memory 选择，无需重训 | LaSOT_ext AUC +7.1%、GOT-10k AO +3.5%；实时 | [arXiv:2411.11922](https://arxiv.org/abs/2411.11922) |
| SAM2Long | 视频，训练免 | 约束树搜索维持多条分割路径，抗误差累积 | 24 组对比平均 +3.0，长视频 SA-V/LVOS 上 J&F 最多 +5.3 | [arXiv:2410.16268](https://arxiv.org/abs/2410.16268) |
| XMem | 视频，训练免 | 三级记忆（sensory/working/long-term）+ memory potentiation | 长视频 SOTA 级、避免记忆爆炸 | [arXiv:2207.07115](https://arxiv.org/abs/2207.07115) |
| Cutie | 视频，训练免 | 对象级 memory reading（object query） | MOSE 上比 XMem +8.7 J&F，比 DeAOT +4.2 J&F 且快 3× | [arXiv:2310.12982](https://arxiv.org/abs/2310.12982) |
| DEVA | 视频，解耦 | 任务特定图像分割 + 任务无关双向时序传播 | 数据稀缺任务上优于端到端 | [arXiv:2309.03903](https://arxiv.org/abs/2309.03903) |
| **SAM 3 / 3.1** | 图像+视频，概念提示 | Promptable Concept Segmentation：**文本短语/图像示例 → 全实例掩码 + 唯一身份**；presence head；detector–tracker 解耦 | SA-CO 基准（270K 概念，比现有基准多 50×）达人类 75–80%；"图像与视频 PCS 精度翻倍"；3.1 用共享记忆做多目标联合跟踪，"显著更快" | [arXiv:2511.16719](https://arxiv.org/abs/2511.16719)、[github](https://github.com/facebookresearch/sam3) |

**【事实】**上表性能均为原文献/官方仓库口径，硬件条件不同，不可直接横向比较（尤其注意 SAM 2.1 的 FPS 是 A100+bf16+compile，EfficientTAM 的 10 FPS 是 iPhone，二者与 Jetson 无关）。

**【推断】**对本项目最现实的三条路线：

- **路线 A（现状延续）**：EfficientTAM-Ti/S 继续作为"点击跟踪"主模型，增益靠 memory 侧优化与工程管线（TensorRT、异步、ROI 裁剪）。
- **路线 B（能力升级）**：引入 SAM 3/3.1 的文本+示例提示，把"操作员每次点击"降级为"选类别 + 必要时点一次"，并拿到跨目标的唯一身份。代价是环境门槛（Python 3.12/CUDA 12.6/门控权重）与未知的 Jetson 实时性（**未验证**）。
- **路线 C（任务级专用化）**：对高价值子任务（托盘孔、货位）训专用检测/分割，用可提示分割只做 mask 精修与交互兜底。文献提示这条路线**不能用"YOLO+SAM 两阶段"草率拼接**：有研究明确报告 YOLOv8+SAM 两阶段检测器"性能不稳定"。【事实，[arXiv:2402.07098](https://arxiv.org/abs/2402.07098)】

### 2.2 边缘/嵌入式实时性的公开数据（本项目最关心）

**强证据（厂商官方，硬件明确）：NanoSAM / MobileSAM on Jetson**

| 模型 | Orin Nano 编码器 | Orin Nano 全流程 | AGX Orin 编码器 | AGX Orin 全流程 | 精度（mIoU, all/small/medium/large） |
|---|---|---|---|---|---|
| MobileSAM | 未提供 | 146 ms | 35 ms | 39 ms | 0.728 / 0.658 / 0.759 / 0.804 |
| NanoSAM (ResNet18) | 未提供 | 27 ms | 4.2 ms | 8.1 ms | 0.706 / 0.624 / 0.738 / 0.796 |

- 来源：[NVIDIA-AI-IOT/nanosam README](https://github.com/NVIDIA-AI-IOT/nanosam)（表内单位 ms，TensorRT engine，**图像单帧提示分割，无时序跟踪**）
- **【事实】**注意其代价：NanoSAM 比 MobileSAM 快约 5×（AGX Orin 全流程 8.1 vs 39 ms），但 mIoU 从 0.728 降到 0.706；**且它是图像模型，不含 SAM 2 的时序记忆，"跟踪"必须另接跟踪器**。

**第三方汇总（可作参考，非一手）：**[edgeaistack.ai NanoSAM ResNet18 基准页](https://edgeaistack.ai/benchmarks/jetson-orin-nano/nanosam/resnet18/) 记录 FP16 下 Orin Nano 8GB 37 fps、AGX Orin 64GB 123.5 fps，自标 class C（=发布方 NVIDIA 的公开数据），并声明延迟为 `1000/fps` 倒数推导而非实测。【宣传/二手】

**社区实测（条件写得很清楚，但因非同行评审，仅作工程参考）：**[sstc-aiteam/sam2-speedup RESULTS.md](https://github.com/sstc-aiteam/sam2-speedup/blob/main/bench/RESULTS.md)：
- 设备：Jetson AGX Orin 64GB，JetPack 6 / L4T 36.4.7，CUDA 12.6，TensorRT 10.3，PyTorch 2.9.1。
- 图像预测器（1800×1200，sam2_hiera_small）：fp32 223.2 ms → fp16+torch.compile(encoder) 77.1 ms（12.97 FPS，2.9×），最佳掩码 IoU 0.9996（与 fp32 对比，"effectively lossless"）。
- 视频预测器（200 帧）：bf16 autocast eager 9.18 FPS（108.9 ms/帧）；`vos_optimized=True`（整模型编译）在该环境**失败**（PyTorch 2.9 inductor/CUDA-graphs bug）。【事实（社区自述）】

**社区故障报告（负面证据，值得记录）：**NVIDIA 开发者论坛有帖报告 SAM 2 在 AGX Orin 64GB 上只有 **2 FPS**（未说明分辨率/精度/后端）。[论坛链接](https://forums.developer.nvidia.com/t/sam2-segmentation-on-jetson-agx-orin/325069) 【事实（用户自述）】——与上一条相差 5–6×，说明"同样叫 SAM 2 on AGX Orin"的数字高度依赖实现（分辨率、精度、是否 TensorRT、是否编译），**任何方案都必须现场实测，不能引用单一数字**。【推断】

**本项目自有实测（最相关）：**【实测·本仓库】
- 设备：Seeed reComputer mini / J501 + **AGX Orin 64GB**，JetPack 6.2.1 (L4T R36.4.4)，CUDA 12.6，TensorRT 10.3，torch 2.11。
- 模型：EfficientTAM-Ti 512×512（69 MB checkpoint），eager（无 torch.compile / Triton / TensorRT）。
- 结果（dog.mp4，80 帧，512×512 输入）：

| dtype | 30W 模式 | MAXN（`nvpmodel -m 0` + `jetson_clocks`） | MAXN FP32 瓶颈 |
|---|---|---|---|
| FP32 | 130.9 ms / 7.64 FPS | 38.7 ms / **25.85 FPS** | memory_attention 18 ms(47%) + image_encoder 13.5 ms(35%) |
| FP32+TF32 | — | 38.9 ms / 25.74 FPS | |
| BF16 | 66.5 ms / 15.05 FPS | 49.3 ms / 20.29 FPS | |
| FP16 | 66.4 ms / 15.07 FPS | 49.1 ms / 20.35 FPS | |

- 资源：CUDA 0.474 GB（批量）/ ~0.4 GB（在线）；RSS 1.4–1.8 GB；温度 ~48 °C。
- **关键发现（仓库自述）**：MAXN 是必需的（30W 模式 FP32 掉 3.4×）；在 MAXN 上**纯 FP32 eager 反而最快**，BF16/FP16 autocast 更慢（小算子 dtype 转换开销）；TF32 无收益；`num_maskmem=7` 只限制注意力 bank，显存随时间仍需滑动窗口+剪枝来压平（已验证 171+ 帧显存平稳，但**仓库明确标注 30 分钟长时稳定性测试尚未 PASS**）。

**【推断】**把上述数字换算成"叉车子任务可用性"：

- **有 20–26 FPS@512×512 的"目标级跟踪"是够用的**（对位/放置判定是 1–5 Hz 级决策，见 §3）。
- 但如果要**同时跟踪多目标**（多行人 + 多个托盘），SAM 2 系按对象独立跑 memory 与 decoder（官方文档明示"每个对象单独跑全部非编码器组件"），**成本近似线性增长**；10 个目标时 25 FPS 会掉到个位数。这是选型时最容易被忽略的硬约束。【事实（官方实现）+ 推断】

### 2.3 多目标与身份保持（identity / ID switch）

**SAM 2 的官方限制（直接引用其 Limitations 章节）**【事实】：模型会
1. 在镜头切换（shot change）时分割失败；
2. 在**拥挤场景、长时间遮挡后、长视频**中丢失目标或混淆目标；
3. 对**细/薄结构且快速运动**的目标跟踪不佳；
4. 在外观相似的邻近物体（作者举例"多个相同的杂耍球"）上困难；
5. **多目标时"每个对象独立处理"，对象之间没有通信**（只共享每帧图像特征），作者自己指出"加入对象级共享上下文有助于效率"。

**已有的缓解路线（均有公开实现）**【事实】：

| 失败模式 | 缓解方法 | 来源 |
|---|---|---|
| 长时间遮挡/重现、长视频误差累积 | SAM2Long：帧内不确定性 + 约束树搜索保持多条路径（训练免） | [arXiv:2410.16268](https://arxiv.org/abs/2410.16268)、[github](https://github.com/Mark12Ding/SAM2Long) |
| 拥挤/快速运动/自遮挡下的跟踪漂移 | SAMURAI：运动感知 memory 选择（训练免、实时） | [arXiv:2411.11922](https://arxiv.org/abs/2411.11922) |
| 外观相似/干扰物 | Cutie 对象级 memory reading；XMem 长时记忆 | [arXiv:2310.12982](https://arxiv.org/abs/2310.12982)、[arXiv:2207.07115](https://arxiv.org/abs/2207.07115) |
| 遮挡后身份碎片化（fragmentation） | 显式空间 re-ID 扩展 | [sam2-spatial-reid](https://github.com/MaanaRajesh/sam2-spatial-reid) |
| 任务级解耦（不同类别/任务） | DEVA：图像模型 + 通用时序传播 | [arXiv:2309.03903](https://arxiv.org/abs/2309.03903) |
| 多目标身份 + 全实例 | **SAM 3/3.1 的 PCS 直接返回唯一 identity**，3.1 用共享记忆联合跟踪 | [arXiv:2511.16719](https://arxiv.org/abs/2511.16719) |

**评估指标（身份保持必须用 ID 类指标，不能只看 IoU）**【事实】：

- VOS 侧：**J（region similarity = IoU）**、**F（contour accuracy）**，DAVIS 2017 的最终指标是两者均值 **J&F**（[arXiv:1704.00675](https://arxiv.org/abs/1704.00675)；赛事页 [davischallenge.org](https://davischallenge.org/)）。
- MOT 侧：**IDF1**（强调正确身份匹配，[arXiv:1609.01775](https://arxiv.org/abs/1609.01775)）、**HOTA**（把检测/关联/定位统一成一个指标并可分解 5 类错误，[arXiv:2009.07736](https://arxiv.org/abs/2009.07736)）。Iou-only 无法暴露 ID switch。
- 数据集：MOT16/MOT17（[arXiv:1603.00831](https://arxiv.org/abs/1603.00831)）、**DanceTrack**（100 段视频，外观统一、运动多样，专门惩罚靠外观做关联的跟踪器，[arXiv:2111.14690](https://arxiv.org/abs/2111.14690)、[github](https://github.com/DanceTrack/DanceTrack)）。

**【推断】**本项目的身份保持应这样验收：同一目标在"被叉车/货架遮挡 ≥2 s 后重现"时，ID 不变率 ≥ 95%（在 30 段现场录像上统计），并同时报告 IDF1；**只用 mask IoU 验收跟踪一定会掩盖真实失效**。

## 3. 叉车/AGV 关键子任务的技术路线对比

### 3.1 托盘孔（pocket）检测与货叉对准

**几何与容差的客观起点**【事实】：

- EPAL 欧洲托盘官方产品资料：800×1200 mm，**总高 144 mm (+7/-0)**，**货叉入口高度 100 mm (+5/-0)**，下边缘倒角 17 mm × 45°，安全工作载荷 1500 kg。[EPAL 官方数据表 PDF](https://www.epal-pallets.org/fileadmin/user_upload/ntg_package/images/mediathek/DU_GB_EPAL_1_Produktdatenblatt_low.pdf)（本地已下载并 pdftotext 校验）
- 因此**名义垂向可用间隙 ≈ 100 mm 减去货叉厚度**（常见叉厚 40–50 mm）→ 约 **50–60 mm**，倒角又给回一部分容错。这解释了为什么工业界把容差定在"厘米级"而不是"毫米级"。
- Lang2Lift（真实户外叉车，AIT）给出的操作容差：**横向 ±0.05 m、垂向 ±0.04 m**（并说明其来自"托盘几何标准 + 叉车尺寸约束"）。[arXiv:2508.15427](https://arxiv.org/abs/2508.15427)

**路线对比**（"视觉 / 深度 / 激光 / 标记法"）：

| 路线 | 代表做法 | 公开精度/能力 | 主要弱点 |
|---|---|---|---|
| 纯视觉检测 + 位姿（学习） | 合成数据 + 侧脸几何特征 | 单托盘 **mAP50 0.995**；5 m 内正视条件下**位置误差 <4.2 cm、旋转误差 8.2°**【事实，[arXiv:2503.22965](https://arxiv.org/abs/2503.22965)】 | 依赖训练分布；暗光、堆叠、遮挡显著退化：另一篇报告**亮度降 80% → mAP50 仅 3%**，且 **YOLOv8+SAM 两阶段"性能不稳定"**【事实，[arXiv:2402.07098](https://arxiv.org/abs/2402.07098)】 |
| 视觉 + 单目几何（广角、倾斜测量） | 在 reach 叉车后靠背装广角相机，测托盘俯仰与相对高度，并给出相机-货叉标定方法；PMC/Sensors 版给出完整实车实验 | **允许误差：Y 向位置 ≤50 mm、偏航 ≤3°、俯仰倾角 ≤1°**；实车 6 次插叉 3 成功 3 失败；俯仰测量与倾角仪偏差示例 0.6°（未载货 5 个倾角样本的 Esti.−GT 约 −0.05…0.84°） | 只覆盖俯仰/高度维度；相机-货叉外参标定是前提且**对光照 ON/OFF 敏感**；检测区混入背景强边缘会给出错误角度并把托盘推倒【事实】[arXiv:2602.16178](https://arxiv.org/abs/2602.16178) / [Sensors 26(1):154 全文（PMC，开放获取，200）](https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346/) |
| 3D 点云 / LiDAR + ICP | 从托盘上部区域点云实时 ICP 跟踪相对位姿与姿态差 | 面向"斜坡上卸货、抽叉不拖拽托盘"，仿真 + 真车验证 | 需要 LiDAR —— **本仓库现有证据中未见 LiDAR（仅 USB/V4L2 相机），请以 t1 的硬约束结论为准**；点云遮挡敏感【事实】[arXiv:2602.16744](https://arxiv.org/abs/2602.16744) |
| 端到端 3D 参数化检测 | PIRATR：从遮挡点云联合估计 6-DoF 位姿与类别参数（货叉开口等） | 自动化叉车平台上合成训练、真机 LiDAR 零微调 **mAP 0.919** | 依然是 LiDAR 路线【事实】[arXiv:2602.05557](https://arxiv.org/abs/2602.05557) |
| 激光/光学对准辅助 | 货叉前端线激光投到托盘孔（KOOI Laser、SmartFork Laser、Holland Integra-Z） | 给驾驶员/系统一个可视对准线，"减少手动调整" | **这是人因辅助，不是感知测量**；无精度指标【宣传】[Meijer KOOI Laser](https://meijer-handling-solutions.com/products/kooi-next/laser/) |
| 商用托盘孔识别系统 | ifm PDS（Pallet Detection System）：2D/3D 相机头 + 视频处理单元 + 识别软件，自动识别所有"双孔标准托盘"并接管货叉导航 | 厂商原文称"识别全部双孔标准托盘、无论其位置，并把货叉导航接管到**厘米级（down to the centimetre）**"；称相机头质量与高重复率保证在动态/困难工况下仍有可用 3D 点云，并能快速检测托盘意外移动以实现货叉跟踪（本站经 `r.jina.ai` 取回正文，直连 403） | 该句是**厂商自述**，未给测试条件、误差定义与样本量，**不得作为事实引用**；本项目若使用需自行定义测量方法【宣传】[ifm 产品页](https://www.ifm.com/gb/en/shared/productnews/2024/sps/complete-solution-for-pallet-pocket-recognition) |
| 标记法（AprilTag/二维码） | 本仓库现有路线：地面标签 0–3 定地面坐标系 + 托盘标签 10 定托盘位姿 | 复用现有 PnP，无需相机外参；验收目标：**P95 平移误差 ≤ 托盘短边 5%、偏航 ≤5°**（仓库自标"验收目标，未执行"） | 依赖现场贴码与维护；托盘更换/标签污损即失效【实测·本仓库】 |

**与"可提示分割"的关系**【推断】：

- 提示分割**不能替代**对位所需的度量精度（它输出 2D 掩码，不给尺度）。它能做的是：**把托盘/货物从杂乱背景中切出来 → 给位姿/测量模块一个干净 ROI**。Lang2Lift 的消融实验正好量化了这件事的价值：去掉 SAM-2（用 Florence-2 的框填成矩形掩码）后，**严格重叠成功率 SR@0.75 从 52.71% 掉到 8.53%**，说明"掩码边界质量对后续 6D 位姿与安全插入至关重要"。【事实】
- 若项目要做"货叉对准"，**最小可行组合**是：专用托盘/孔检测（可用合成数据训练）+ 可提示分割做掩码精修 + 深度或地面单应做度量 + 现有 AprilTag 方案作为标定/兜底。**单独依赖可提示分割做对位一定是错的。**【推断】

### 3.2 载荷到位判定 / 放置确认

- Lang2Lift 用 6D 位姿 + 容差判定（±5 cm 横向 / ±4 cm 垂向），并在接近过程中用 25 Hz 的位姿跟踪维持平滑估计；**其位姿估计模块本身是 1.2 Hz**，靠高频子模块在两次 6D 更新之间保持新鲜度。【事实】
- 本仓库已有"放置判定"链路的**合成几何验证**（N=200 随机场景，ΔX/ΔZ ∈ ±0.30 m、yaw ∈ ±30°，几何链 lossless、0 次 False PASS），并明确写出"**所有真实误差来自感知层**（内参、手工地面外参、单目度量深度、掩码/立方体拟合），且**真车卷尺验收尚未执行**"；目标定在"演示级均值 |ΔX|,|ΔZ| ≤ 10 cm，标定完成后 5 cm"。【实测·本仓库】
- **【推断】**这条子任务是最适合"可提示分割 + 现有几何链"组合的：分割提供 cargo/zone 掩码 → 现有足印投影与阈值逻辑不变 → 只替换感知输入。预期延迟 <100 ms/帧（分割 38–50 ms + 几何 <10 ms），演示级精度 10 cm 级、标定后 5–10 cm 级（**推断，未经真机验证**）。风险点不是分割精度而是**单目度量深度的尺度误差**（见 §3.4）。

### 3.3 行人检测与盲区覆盖

**监管底线（必须写清楚）**【事实】：

- **ISO 3691-4:2023**（第二版，2023-06）：定义 3.18 "personnel detection means = system to detect persons in the path of a truck"；定义 3.5 "virtual bumper = 非接触式 ESPE，带一个或多个检测区，触发后叉车可停车/改道/降速"；规范性引用 **ISO 13849-1:2023**（安全相关控制系统性能等级）、**ISO 13849-2:2012**、**IEC 61496-2/-3**（AOPD / AOPDDR）。[ISO 3691-4:2023 官方预览 PDF（15 页，本地已校验）](https://cdn.standards.iteh.ai/samples/83545/a3d9d057a08d4f9c8e8e87cdc947583c/ISO-3691-4-2023.pdf)、[ISO 商店页](https://www.iso.org/standard/83545.html)（商店页 403 Cloudflare，属反爬非死链）
- **ANSI/ITSDF B56.5-2024**（美国对应标准，2025-12-16 生效）：[ITSDF B56 标准页](https://www.itsdf.org/cue/b56-standards.html)（官方页面可读）、[ANSI 商店页](https://webstore.ansi.org/standards/ansi/ansiitsdfb562024)（403 反爬）
- **IEC TS 61496-4-3:2022**：使用**立体视觉（VBPD_ST）**的视觉保护装置（VBPD）要求。[IEC Webstore](https://webstore.iec.ch/en/publication/63436)（200 OK）
- 二手解读（厂商博客，条款细节需回到标准原文）：人员检测需**性能等级 PL d**；在人员检测被静默/不完整有效时（如对接）**速度不得超过 0.3 m/s**；验证用两个测试体：**直径 200 mm × 高 600 mm 的竖直圆柱**与**直径 70 mm × 长 400 mm 的水平圆柱**；保护域随速度动态切换。【二手·[fabrico.io 解读](https://www.fabrico.io/blog/iso-3691-4-driverless-industrial-trucks/)】

**相机到底能不能做安全人员检测？——认证器件的现实边界**【事实】：

- SICK **safeVisionary2** 官方产品资料：3D ToF 安全相机，**PL c (ISO 13849-1) / SIL1 (IEC 61508) / Type 2 (IEC 61496-3)**；保护域范围 ≤2 m（增程扫描模式 4 m），警告域 7.3 m，视场 68°×42°，响应时间 ≥55 ms，最多 24 个域。资料里另有一句关键信息：**"By combining two cameras, the PL d performance level required by IEC 63227-2021"**（即：单相机只有 PL c，要 PL d 需双相机组合）。[SICK safeVisionary2 产品资料 PDF（本地已 pdftotext 校验）](https://cdn.sickcn.com/media/docs/2/12/112/product_information_safevisionary2_safety_camera_sensors_en_im0103112.pdf)、[SICK 产品页](https://www.sick.com/us/en/catalog/products/safety/safe-3d-cameras/safevisionary2/c/g568562)
- **【推断/结论】**：
  1. **用普通 RGB 相机 + SAM 系模型做安全停止是不可行的**——没有安全认证、无确定性时延、无诊断覆盖率，不符合 ISO 13849 PL d 的架构与验证要求。
  2. 视觉的合理定位是**非安全功能的辅助预警 / 盲区可见化 / near-miss 记录**，以及**给安全级器件做冗余的第二信息源**。
  3. 项目对外表述必须固定在"驾驶辅助原型"上（本仓库 warning.md 已经这样写，应延续）。

**技术路线对比**【事实+推断】：

| 方案 | 覆盖特性 | 已知问题 | 来源 |
|---|---|---|---|
| 后向单目 RGB + 单目深度（本项目现状） | 低成本、覆盖后向；**但 >3 m 后精度迅速下降** | 尺度误差随距离二次增长；低照度/强反光/透明/黑色物体/镜头污染/振动失效（仓库自己列出） | 【实测·本仓库】warning.md |
| 深度相机（立体/ToF） | 有度量，近距好 | 反光地板/玻璃产生空洞与尖刺；强日照镜面导致**系统性深度偏置/漂移**；透明物体无回波 | 【事实】[StereoLabs 社区帖](https://community.stereolabs.com/t/specular-lighting-causing-systematic-stereo-depth-bias-drift/10371)、[D3RoMa](https://pku-epic.github.io/D3RoMa/)、[arXiv:2606.03421](https://arxiv.org/abs/2606.03421) |
| 鱼眼/环视多相机 | 盲区覆盖好、成本可控 | 畸变大、边缘分辨率低、标定与融合复杂（本项目 CSI 相机尚未支持，只支持 USB/V4L2） | 【推断】+【实测·本仓库】 |
| 安全激光雷达 | **PL d / SIL2 可达**，保护域验证成熟 | 单线平面，**低矮/躺倒目标与立体障碍受限**；反光地面同样有伪影 | 【事实】[fsddsk 安全扫描仪说明](https://www.fsddsk.com/agv-amr-obstacle-avoidance-safety-lidar-5m-vs-20m)（厂商博客）、[BRIDZA 产品页](https://auto.bridza.com/products/safety-laser-scanner-agv-amr)（厂商） |
| 雷达/毫米波 | 抗光照、测速好 | 角分辨率低、静态目标与人体分类弱 | 【推断】 |

**本项目可行的最小增量**【推断】：现有后向单目 + 单目深度做 **near-miss 预警与记录**；把"人物持续跟踪"（点击/自动触发后由 EfficientTAM 跟踪）用于**跨遮挡保持目标**与**危险区域滞留判定**；安全停止不在范围内。

### 3.4 地面平面单目测距的误差量级

- **理论/文献**【事实】：单应（homography）地面映射的距离误差**随真实距离近似二次增长**；该文用 >1900 万样本仿真验证，并给出两种修正（回归拟合二次误差函数 / 直接梯度下降优化单应），指出"改善几何标定的收益往往大于提升模型复杂度"。[arXiv:2604.10805](https://arxiv.org/abs/2604.10805)
- **推导（本文）**【推断】：针孔 + 地面平面模型下 `d = f·h / (v − v0)`，故 `|δd| ≈ (d² / (f·h))·δv`。取 2304×1296 相机、约 60° 水平视场（f ≈ 2000 px）、相机高 h = 1.2 m：
  - d = 3 m：每 1 px 垂直误差 ≈ 9/(2000×1.2) = **3.8 mm**
  - d = 6 m：每 1 px ≈ 36/2400 = **15 mm**
  - d = 10 m：每 1 px ≈ **42 mm**
  即：**"地面单应测距"在 3 m 内可以是厘米级，在 6–10 m 迅速变成几十厘米级**，与文献的二次增长一致。加上俯仰角误差、地面不平、相机振动，实际误差会更差。
- **学习式单目深度的精度量级**【事实】：Depth Anything V2 提供 25M–1.3B 参数族，"比基于 SD 的方法快 10×+ 且更准"，并可用度量深度标签微调；Depth Pro 声称 0.3 s 生成 2.25 MP 度量深度图（标准 GPU），无需相机内参。[arXiv:2406.09414](https://arxiv.org/abs/2406.09414)、[arXiv:2410.02073](https://arxiv.org/abs/2410.02073)
- **本项目实测（最相关）**【实测·本仓库】：warning.md 记录单目 RGB + **Depth Anything V2 Metric Small** 在 **Orin NX 16GB**（JetPack 5.1.3）上：PyTorch fp16 推断 79 ms、总延迟 ~113 ms；**TensorRT fp16（518 输入）26.5 ms、引擎内总延迟 ~46 ms**；测距采用障碍区域 10% 百分位、3/5 帧确认、TTC 仅在接近速度 >0.10 m/s 时给出；并有卷尺标定流程（`calibrate_distance.py`，0.5/1/2/3/5 m 拟合 a·x+b）。
- **【推断】门槛建议**：若某场景要求"距离误差 ≤10 cm"，则**单目方案的有效距离上限约 3–5 m**（需现场标定 + 地面平整），超出范围必须换立体/ToF/激光；本项目中"后向盲区预警"（1.5–3 m 阈值）落在可信区间内，"前方 10 m 行人测距"不在。

### 3.5 其它可选子任务（简述，供 t4 取舍）

- **货位空满 / 库位占用判定**：可由掩码 + 单应把"足印"投到地面格网，二值判定；本仓库已有 zone 判定几何。【推断】
- **货物计数 / 堆叠完整性**：SAM 3 的 PCS（文本+示例 → 全实例 + 唯一 ID）天然适配，但 Jetson 实时性**未验证**。【事实+推断】
- **装载/超限检查（是否超高/超宽）**：需要度量，仍回到 §3.4 的尺度问题。
- **异常事件记录（near-miss/越界/滞留）**：跟踪 + 几何，非安全功能，工程价值高、合规负担低。【推断】

## 4. 公开数据集与评测指标（含达标门槛）

### 4.1 数据集清单

| 数据集 | 领域 | 规模/标注 | 与本项目的关系 | 来源 |
|---|---|---|---|---|
| SA-1B | 图像分割 | 11M 图、1B mask | SAM/EfficientTAM 训练数据 | [arXiv:2304.02643](https://arxiv.org/abs/2304.02643) |
| SA-V | 视频分割 | **51K 视频、643K masklets**（191K 人工辅助 + 452K 自动生成并经人工核验），平均 12.61 masklets/视频，平均分辨率 1401×1037，**类无关、无类别标签**，CC BY 4.0（经 `r.jina.ai` 取回 Meta 官方数据卡正文核对） | SAM 2/EfficientTAM 训练数据；含遮挡、重现场景。**注意"无类别标签"意味着不能直接用 SA-V 训"托盘"这一类别**，只能做类无关分割/跟踪 | [Meta SA-V 数据卡](https://ai.meta.com/datasets/segment-anything-video/)、[SAM2 sav_dataset README](https://github.com/facebookresearch/sam2/blob/main/sav_dataset/README.md)、[arXiv:2408.00714](https://arxiv.org/abs/2408.00714) |
| DAVIS 2017 | 半监督 VOS | 视频对象分割基准 | **指标来源（J、F、J&F）**；对象显著、单一，≈90% J&F 属"简单档" | [arXiv:1704.00675](https://arxiv.org/abs/1704.00675)、[davischallenge.org](https://davischallenge.org/) |
| MOSE | 复杂场景 VOS | 2,149 段视频、5,200 对象、431,725 mask | **拥挤+遮挡的"困难档"**：SOTA 仅 59.4% J&F | [arXiv:2302.01872](https://arxiv.org/abs/2302.01872) |
| LVOS | 长时 VOS | 220 段视频、421 分钟，含长期重现与跨时相似物 | 长时跟踪/重现验收的合适基准 | [arXiv:2211.10181](https://arxiv.org/abs/2211.10181) |
| LOCO | 物流场景检测 | 37,988 图（5 个物流环境），其中 5,593 图人工标注、152,421 标注框；类别含**叉车、托盘搬运车、托盘、小载具、料架** | **唯一直接对口的公开物流数据集**；但只有检测框，**无分割掩码** | [tum-fml/loco](https://github.com/tum-fml/loco) |
| DanceTrack | MOT | 100 段视频，外观统一、运动多样 | 专测 ID 关联能力（外观不可靠时） | [arXiv:2111.14690](https://arxiv.org/abs/2111.14690)、[github](https://github.com/DanceTrack/DanceTrack) |
| MOT16/17 | MOT | 行人跟踪基准 | IDF1/HOTA 的常用评测集 | [arXiv:1603.00831](https://arxiv.org/abs/1603.00831) |
| NYU/KITTI（度量深度） | 单目深度 | 室内/驾驶 | abs_rel、δ<1.25³ 等指标来源与大样本评测 | [arXiv:1406.2283](https://arxiv.org/abs/1406.2283)、[arXiv:2406.09414](https://arxiv.org/abs/2406.09414) |

**【事实/缺口】**：**没有公开的"叉车托盘孔分割/对位"数据集**（本次检索未发现；LOCO 只有检测框）。这意味着任何托盘孔相关的分割精度**必须自建现场数据**，并且不能用 SA-V/DAVIS 的分数类推。**【推断】**

### 4.2 指标与达标门槛

| 任务 | 指标 | 定义/出处 | 建议门槛（本项目） |
|---|---|---|---|
| 提示分割（图像） | IoU / mIoU、SR@0.5、SR@0.75 | 与掩码交集/并集；Lang2Lift 用 SR@0.5/0.75 报告 | 目标识别：SR@0.5 ≥ 0.9；**喂给测量/位姿的掩码：SR@0.75 ≥ 0.8**（低于此，边界误差会污染几何）【推断，参考 Lang2Lift 的 SR@0.75 掉到 8.53% 时几何受损】 |
| 视频跟踪（VOS） | J（IoU）、F（边界）、J&F | DAVIS 2017 | 现场自建集上 J&F ≥ 0.75（短时、无遮挡）、≥0.60（含遮挡重现）【推断：对齐 SAM 2.1 small 的 SA-V 76.6 / MOSE 71.8–73.5 量级】 |
| 多目标身份 | IDF1、HOTA、ID switch 计数 | IDF1（arXiv:1609.01775）、HOTA（arXiv:2009.07736） | 遮挡 ≥2 s 后重现 ID 保持率 ≥95%；IDSW/分钟 ≤1【推断】 |
| 单目测距 | 绝对误差/相对误差 | 地面单应二次增长（arXiv:2604.10805）；深度指标 abs_rel、RMSE、δ<1.25/1.25²/1.25³（Eigen 等，arXiv:1406.2283） | 1.5–3.5 m 内 **P95 误差 ≤0.3 m**；>5 m 不用于任何自动决策【推断，需现场卷尺标定验证】 |
| 托盘检测/位姿 | mAP50、位置误差、旋转误差 | arXiv:2503.22965 报告 0.995 mAP50 / <4.2 cm / 8.2°（5 m 正视） | 位置 P95 ≤5 cm、偏航 P95 ≤5°（对齐本仓库现有验收目标与 Lang2Lift 容差）【事实（目标值）+ 推断】 |
| **货叉插入（final）** | Y 向位置误差、偏航误差、俯仰倾角误差 | 同行评审实车研究给出的允许误差：**Y ≤50 mm、yaw ≤3°、pitch ≤1°**（[PMC12788346](https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346/)） | 直接采用上列允许误差作为"插入前"门槛；**并额外要求连续 N 帧落在容差内**（该研究单次判定 6 次仅 3 次成功的教训）【事实+推断】 |
| 实时性 | 端到端延迟 P95、稳定帧率、长时显存 | — | 交互跟踪 ≥10 FPS；任何自动判定链 P95 ≤500 ms；**连续 2 小时显存无持续增长**（本仓库已把"2 小时无持续增长"写入验收）【实测·本仓库】 |

**【事实】**评测时必须区分"论文里的**相对**深度指标（scale-invariant）"与"机器人需要的**绝对**尺度"：Depth Anything V2 主模型族输出的是相对深度，度量版本需要额外用度量标签微调（[arXiv:2406.09414](https://arxiv.org/abs/2406.09414)）；Depth Pro 则以"无需内参的绝对尺度"为卖点（[arXiv:2410.02073](https://arxiv.org/abs/2410.02073)）。**用相对深度模型做距离判定是常见致命错误，必须显式说明所用是 metric 版本。**【推断】

## 5. 失败模式与缓解（重点章节，共 15 项：F1–F11 为模型/感知类，F12–F15 为实车工程类）

> F12–F15 来自一篇**同行评审、开放获取、有真车实验**的插叉研究（Sensors 26(1):154 / PMC12788346），其价值在于它证明了"即使算法与标定都做对，实车仍会以这四种方式失败"。

| # | 失败模式 | 观察到的现象/根因（带来源） | 可观测信号 | 缓解手段 | 残余风险 |
|---|---|---|---|---|---|
| F1 | **遮挡后重现 / 丢失目标** | SAM 2 官方：长时遮挡后、长视频中会丢或混淆；拥挤场景最差 | mask 面积突变→消失；confidence 掉；ID 跳变 | 多帧确认 + 重提示；SAM2Long 树搜索路径；SAMURAI 运动感知 memory；显式 re-ID 模板 | 完全遮挡期间无信息，重现后身份仍可能错【事实/推断】 |
| F2 | **身份切换（ID switch）** | SAM 2 多目标各自独立、无对象间通信；外观相似邻近物混淆 | IDSW 计数、IDF1 掉；掩码在两目标间"跳动" | 用 IDF1/HOTA 做验收而非 IoU；SAM 3.1 共享记忆联合跟踪；空间 re-ID | 落一次错分后 memory 会持续污染后续帧（error accumulation）【事实】 |
| F3 | **低纹理 / 反光 / 镜面** | 主动立体与 ToF 靠 IR 反射：强日照镜面 → **系统性深度偏置/漂移**；反光地板/玻璃 → 空洞与尖刺；透明物体无回波 | 深度图上零值/尖刺比例；地面平面拟合残差 | 可靠性加权融合（DRM-Net 类）而非补全；D3RoMa 类学习式立体；偏振/多曝光；**检测到不可靠就进 SYSTEM ERROR（fail visible）** | 无解的极端镜面下只能降级或换传感器【事实】+【实测·本仓库 warning.md 的安全边界】 |
| F4 | **低照度 / 逆光** | 合成数据训练的托盘检测器在亮度降 80% 时 mAP50 掉到 3%；低光下可提示分割的文本/示例提示反而能帮忙（Lang2Lift 低光 mIoU 0.805） | 图像均值/直方图、检测置信度分布漂移 | 补光（主动红外/可见光）、曝光策略、域随机化训练、文本提示辅助、夜间单独验收 | 逆光下轮廓光晕会导致边界外扩【事实】 |
| F5 | **尺度剧变 / 远距离退化** | 立体深度误差随距离二次增长；单目地面单应距离误差同样二次增长 | 目标像素高度、距离估计的置信区间 | 只在有效距离内自动决策；接近过程用高帧率跟踪；远距离只做"发现"不做"测量" | 远距精度无法靠算法补齐【事实+推断】 |
| F6 | **粉尘、水汽、镜头污染、雨雪** | Lang2Lift 报告 snowy 条件 mIoU 0.458（低于 sunny 的 0.398? 见下注）；镜头污染在 warning.md 中被列为单目失效条件 | 图像清晰度指标、对比度、深度有效率 | 定期自检 + 遮挡/污染检测（报警而非静默降级）、防护罩/气帘 | 现场维护纪律问题，算法无法根除【事实+实测·本仓库】 |
| F7 | **相机振动 / 标定漂移** | 叉车/AGV 振动导致外参漂移，直接影响测距与对位 | 重投影误差、地面平面拟合残差、特征匹配一致性 | 在线标定监控（OCaMo：LiDAR-相机外参在线监控与旋转漂移跟踪，IEEE T-RO 2024）；**重标定触发条件**（残差超阈 → 拒绝自动判定并要求重标定）；AprilTag 地面板每帧解 PnP 的形式本身就是自标定 | 缓慢漂移可能长期不被发现 → 需要周期性验证【事实】[OCaMo](https://github.com/moravecj/OCaMo) |
| F8 | **多目标线性成本** | SAM 2 每个对象独立跑 memory + decoder，仅共享图像特征 | 单帧耗时随目标数增长 | 限制同时跟踪目标数（如 ≤5）；目标用 ROI 裁剪降低输入分辨率；分层（检测器负责发现、分割只负责选中的少数目标） | 跟踪 10+ 目标时不可能保持 20+ FPS【事实+推断】 |
| F9 | **细/薄结构 + 快速运动** | SAM 2 官方：细薄结构与快速运动跟踪不佳；叉车场景的叉齿、绑带、网罩属此类 | 掩码边界抖动、细部丢失 | 更高分辨率 ROI、边界感知损失微调、后处理；必要时不用分割而用检测框 | 叉齿级掩码精度不现实【事实】 |
| F10 | **镜头切换/画面重置** | SAM 2 官方明确会跨 shot change 失败 | 帧间内容突变检测 | 场景变化即清空 memory 并要求重提示（本仓库已有 R/C 重置语义） | 无【事实】 |
| F11 | **"把宣传当事实"的元风险** | 厂商页面普遍不给精度条件（激光对准线、盲区摄像头、AI 预警系统）；本项目若引用这些数字会污染决策 | — | 本文件强制三类标签；t4 综合时**禁止**把【宣传】升级为【事实】 | 需要 verifier 抽检【推断】 |
| F12 | **检测区混入背景强边缘 → 角度测错 → 推倒托盘** | 实车实验：托盘被左移约 5 cm 后，测量把货架立柱边缘当成目标边，俯仰角估成 −6.6935°（误差 >3°），插叉时把托盘推走 | 估计值突变/超出容差、检测直线长度或强度异常、与上一帧角度不一致 | 检测区与边缘选择加约束（前景分割/可提示分割给 ROI）+ 合理性门限（|pitch| 超限即拒绝）+ 多帧一致性 | 背景与托盘纹理相近时仍可能误选【事实，[PMC12788346](https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346/)】 |
| F13 | **相机-货叉外参标定对光照/背景敏感** | 同一场景只切换天花板灯 ON/OFF，标定出的目标位姿就发生变化（position 1.14431,0.0151662,−1.08514 → 1.15054,0.0148959,−1.09401；姿态轴角亦改变）；论文指出该标定误差**直接决定**俯仰角测量与插叉成败 | 重复标定的位姿离散度、重投影误差 | 标定与运行时固定光照条件；标定复核（多次标定取一致区间）；在线标定监控（OCaMo 思路） | 车间照明变化与窗口自然光很难完全固定【事实】 |
| F14 | **惯性导致的"测量时机错位"** | 叉车在驱动力置 0 后仍会因惯性前进**数厘米到数十厘米**，且距离随地面状况随机变化，导致"到达目标点瞬间"的图像不再是插入时刻的位姿 | 电机指令与图像时间戳对齐误差、停下位置分布 | 测量与控制在时间上对齐（停下后重测、或接近段连续测量）；用高帧率跟踪维持估计新鲜度 | 地面/负载变化使停位不可预测【事实】 |
| F15 | **大转角/投影平面模板匹配失败** | 实车第二次路径点 90° 转弯结束时，垂直投影平面图上的模板匹配失败，位姿测量崩溃 | 匹配得分骤降、跟踪置信度掉 | 增大投影图宽度/多假设；退化时进入安全态并要求重测（fail visible） | 视野边界与货架遮挡【事实】 |

> 注 F6（引用纪律）：Lang2Lift 的 Table I 中 snowy（mIoU 0.458）**反而高于** sunny（0.398），而该组数据里 open-vocab "pallet" 查询在 sunny 下是 0.586。作者的解释是提示方式与数据分布共同作用。**引用时不得简化成"雪天比晴天更准"**；可靠的说法是"天气/光照引起的分布偏移会显著改变表现，且不同提示策略的敏感性不同"。【事实】

## 6. 与"可提示分割"最匹配的 3–5 个应用子问题（MVP 与预期区间）

> 全部为 **【推断】** 的工程建议，除标注的文献值外未经真机验证；精度/延迟区间以 **AGX Orin 64GB + 现有 USB 相机 + 现有 EfficientTAM-Ti 512** 为基准。

| 子问题 | 为什么匹配可提示分割 | 最小可行算法（MVP） | 预期精度 | 预期延迟 | 验收方式 |
|---|---|---|---|---|---|
| **S1 操作员点击 → 目标持续跟踪**（人员/托盘/货物/车辆） | 类无关、无需训练数据、一次交互即建立 memory | EfficientTAM-Ti 512 在线跟踪（已有实现）+ 滑动窗口 + memory 剪枝；单目标 | 短时无遮挡 J&F 0.75–0.8（对齐 SAM 2.1 small 在 SA-V 76.6 的文献量级【文献值】）；遮挡重现 ID 保持率目标 ≥95% | **38–50 ms/帧（20–26 FPS）@512**；多目标线性增长【实测·本仓库】 | 30 段现场录像：遮≥2s 重现 ID 保持率、FPS P95、2 小时显存平稳 |
| **S2 托盘/货物掩码精修 → 喂给位姿与测量** | 提示分割的强项是边界；消融显示去掉 SAM-2 后 SR@0.75 从 52.7%→8.5% | 检测/文本提示给 box → 可提示分割出掩码 → 现有足印/几何链不变 | 掩码 SR@0.75 ≥0.8 为门槛；对位精度由位姿/标定决定（位置 P95 ≤5 cm、偏航 ≤5°，对齐 Lang2Lift 容差 ±5cm/±4cm【文献值】） | 分割 40–50 ms + 几何 <10 ms | 真车卷尺验收（本仓库已有模板）；20 cm 偏移必须不 PASS（False PASS 是最坏失效） |
| **S3 放置到位/错货位视觉复核** | 是二值判定，可提示分割给 cargo 与 zone 两个掩码即可 | 点击/自动掩码 + 地面单应 + 现有阈值链（含 3/5 帧确认与迟滞） | 演示级 ≤10 cm、标定后 5–10 cm（本仓库目标值【实测·本仓库】） | <100 ms/帧 | 同上模板 + 负例（错位/错 ID/画面冻结/未确认）必须不 PASS |
| **S4 危险区/盲区人员与车辆持续跟踪（辅助预警）** | 跨遮挡保持同一 ID 是点击分割相对普通检测+跟踪的增量价值 | 后向摄像头 + 单目 metric 深度（已有 TRT fp16 26.5 ms 实现）+ 可提示分割跟踪选中的目标 + TTC | 1.5–3.5 m 内 P95 距离误差 ≤0.3 m；IDSW/分钟 ≤1 | 深度 46 ms + 分割 40–50 ms（串行 ~90 ms） | 30 段"进入/横穿/接近/双人/部分遮挡"录像 + 10 min 空场景误报统计（本仓库已有验收脚本目标） |
| **S5 类别+示例混合检索（升级项）** | SAM 3 的 PCS 返回**所有匹配实例 + 唯一 ID**，可把"逐个点击"降为"选概念" | SAM 3.1 on Jetson（**未验证**）：需 Python 3.12 / CUDA ≥12.6 / 门控权重 | 未知（**未验证**） | 未知（**未验证**）；模型远大于 EfficientTAM-Ti，先做 1 帧延迟实测再谈 | 只在 S1–S4 稳定后再评估 |

**明确不匹配可提示分割的子问题（应交给别的模块）**【推断】：

- 货叉孔**对位测量**（需要尺度与外参，不是分割问题）；
- **安全停止/人员保护**（需要 PL d 认证器件）；
- **毫米级尺寸测量**；
- **高速动态避障**（毫秒级硬实时）。

## 7. 不能只靠视觉做的事（边界清单，供 t4 的"不做清单"）

1. **替代制动/功能安全**：任何形式的"用点击分割/跟踪做安全停止"都必须被拒绝（ISO 3691-4 → ISO 13849-1 PL d；认证相机单机仅 PL c，需双相机才到 PL d）。【事实】
2. **毫米级/亚厘米级测量**：单目本质上受 §3.4 的二次误差限制。
3. **10 m 级人员距离的自动决策**：超出单目可信区间。
4. **无维护的标识方案**：AprilTag 方案依赖标签完好与摆放；不能承诺"零维护"。
5. **用论文分数承诺现场精度**：SA-V/DAVIS 的 J&F 与现场"遮挡 + 反光 + 粉尘"场景不可比（MOSE 59.4% vs DAVIS ~90% 就是证据）。

## 8. 对 t4（综合主报告）的输入建议

- **优先级排序建议**（技术就绪度维度）：S1（已具备，仅需工程化）> S3（小改可用，依赖现有几何链）> S2（小改可用，但精度依赖标定）> S4（需新模块：单目深度 + 跟踪融合，非安全功能）> S5（需换环境/新模块，未验证）。
- **冲突点必须显式写出**（t4 的规则）：S4 若被误写成"防碰撞/安全"即与 ISO 3691-4 冲突；S2 若被写成"替代对位传感器"即与 §3.1 的精度事实冲突；S5 若写成"已具备"即与"未验证"冲突。
- **建议试点**：S1 + S3 组合（点击跟踪 + 放置复核），因为它复用现有全部能力、验收指标现成、False PASS 风险可控。
- **需要提前准备的现场条件**：卷尺/地面标记（真车验收模板已存在）、≥30 段分类录像（含遮挡与重现）、10 min 空场景、长时（2 h）稳定性测试窗口、光照变化的两个时段。

## 9. 开放问题（需用户/团队决策）

1. **是否升级到 SAM 3/3.1**？（环境：Python 3.12 / CUDA ≥12.6 / 门控权重；收益：开放词汇 + 唯一身份 + 3.1 联合跟踪。代价与风险：Jetson 实时性完全未知，需先做单帧延迟实测。）
2. **现场是否有安全级器件（安全激光雷达）？** 若有，视觉与它的职责边界如何划分；若无，"行人保护"应降级为"辅助预警"并写入文档。
3. **是否有可用的真车验收时间窗**？本仓库的 placement/warning 验收模板目前都标注"未执行/待真机"。
4. **是否有 LiDAR？** 若有，§3.1 的 LiDAR 路线（ICP、PIRATR）显著提升对位可行性；若无，只能走"视觉 + 深度 + 标记法"。
5. **是否允许自建数据采集与标注**（托盘孔/遮挡重现/反光地面各 100–300 段）？没有现场数据，S2/S3 的精度只能停留在"演示级"。
6. **多路相机同时供电/带宽是否可行**（t1 的硬约束）：§3.3 的"双相机达 PL d"路线会直接撞上这条约束。

## 10. 与其它任务的接口

- **依赖 t1**：本文多处引用"现有能力/硬约束"，凡未在 t1 中出现证据的，本文均标为【推断】或【未验证】。t1 若给出新的硬约束（供电、CSI、散热、Model 型号），需回填 §3.3、§6 的选型建议。
- **输送给 t4**：§1（结论）、§6（场景↔技术匹配）、§7（不做清单）、§8（排序与冲突点）。
- **给 t5 的核验提示**：① 状态标注已按四类口径（`200 可达` / `403 反爬（非失效）` / `405 方法不允许` / `000 两通道均失败=失效`），本次**无 000**，唯一 403 是 ISO 商店页（仅标题级，条款内容改由官方预览 PDF 支撑）；② 核验所有标为【事实】的数字是否与来源一致，重点：SAM 2.1 FPS 的 A100+bf16+compile 条件、NanoSAM 表的列含义、EPAL 的 100 mm 入口高度、SICK 的 PL c（而非 PL d）、PMC12788346 的"Y ≤50 mm / yaw ≤3° / pitch ≤1°"与 6 次 3 成功；③ 任何"经 `web_fetch`/`web_search` 验证"的表述在本环境都不成立，若发现请判为不合格。

## 11. 参考来源

### 11.1 按主题分组

**可提示分割/跟踪方法与实现**

- SAM — https://arxiv.org/abs/2304.02643
- SAM 2 — https://arxiv.org/abs/2408.00714 ｜ 仓库 https://github.com/facebookresearch/sam2 ｜ SA-V 数据 https://github.com/facebookresearch/sam2/blob/main/sav_dataset/README.md
- SAM 3 — https://arxiv.org/abs/2511.16719 ｜ 仓库 https://github.com/facebookresearch/sam3
- EfficientTAM — https://arxiv.org/abs/2411.18933 ｜ 仓库 https://github.com/yformer/EfficientTAM
- MobileSAM — https://arxiv.org/abs/2306.14289 ｜ 仓库 https://github.com/ChaoningZhang/MobileSAM
- EfficientSAM — https://arxiv.org/abs/2312.00863
- EdgeSAM — https://arxiv.org/abs/2312.06660
- FastSAM — https://arxiv.org/abs/2306.12156
- NanoSAM（NVIDIA，Jetson 时延表） — https://github.com/NVIDIA-AI-IOT/nanosam
- SAMURAI — https://arxiv.org/abs/2411.11922
- SAM2Long — https://arxiv.org/abs/2410.16268 ｜ https://github.com/Mark12Ding/SAM2Long
- XMem — https://arxiv.org/abs/2207.07115
- Cutie — https://arxiv.org/abs/2310.12982
- DEVA — https://arxiv.org/abs/2309.03903
- SAM2 空间 re-ID 扩展 — https://github.com/MaanaRajesh/sam2-spatial-reid
- Ultralytics SAM 2 文档（含"约 44 FPS"自述，硬件未注明 →【宣传】） — https://docs.ultralytics.com/models/sam-2/

**Jetson / 边缘实时性**

- NVIDIA Jetson 官方基准 — https://developer.nvidia.com/embedded/jetson-benchmarks
- SAM2 在 AGX Orin 的社区提速实测（含数字与失败项） — https://github.com/sstc-aiteam/sam2-speedup/blob/main/bench/RESULTS.md
- NVIDIA 论坛 SAM2 on AGX Orin 帖（用户报 2 FPS） — https://forums.developer.nvidia.com/t/sam2-segmentation-on-jetson-agx-orin/325069
- 第三方 NanoSAM 汇总（class C / 延迟为倒数推导） — https://edgeaistack.ai/benchmarks/jetson-orin-nano/nanosam/resnet18/
- Jetson Orin Nano 上 SAM2 环境搭建（社区博客） — https://ryogayuzawa.github.io/jetson-sam2-setup/

**叉车/AGV 子任务与托盘**

- Lang2Lift（户外叉车，SAM-2 + 6D 位姿，含容差与失败案例分析） — https://arxiv.org/abs/2508.15427
- 广角相机测托盘倾斜并自动插叉 — https://arxiv.org/abs/2602.16178
- **同工作的同行评审全文（开放获取，含允许误差与实车失败案例，本文 F12–F15 来源） — https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346/**
- ICP 托盘跟踪（斜坡卸货） — https://arxiv.org/abs/2602.16744
- PIRATR（点云 6-DoF 参数化检测，叉车平台） — https://arxiv.org/abs/2602.05557
- 多专家 RL（叉车长时任务） — https://arxiv.org/abs/2601.07304
- 合成数据托盘检测与定位（mAP50/位置/旋转精度） — https://arxiv.org/abs/2503.22965
- 合成数据托盘检测（亮度降 80% → 3% mAP50；YOLOv8+SAM 两阶段不稳定） — https://arxiv.org/abs/2402.07098
- LOCO 物流数据集（叉车/托盘检测） — https://github.com/tum-fml/loco
- EPAL 欧洲托盘官方产品资料（144 mm 总高、100 mm 入口高度、17×45° 倒角） — https://www.epal-pallets.org/fileadmin/user_upload/ntg_package/images/mediathek/DU_GB_EPAL_1_Produktdatenblatt_low.pdf
- 货叉线激光对准辅助（【宣传】） — https://meijer-handling-solutions.com/products/kooi-next/laser/
- ifm 托盘孔识别系统（【宣传】；直连 403，正文经 `r.jina.ai` 取回） — https://www.ifm.com/gb/en/shared/productnews/2024/sps/complete-solution-for-pallet-pocket-recognition

**标准与合规**

- ISO 3691-4:2023 官方预览 PDF（术语 3.5/3.18、规范性引用 13849-1、61496-2/-3） — https://cdn.standards.iteh.ai/samples/83545/a3d9d057a08d4f9c8e8e87cdc947583c/ISO-3691-4-2023.pdf
- ISO 商店页（403 反爬，两通道均仅得标题 → 仅标题级） — https://www.iso.org/standard/83545.html
- ITSDF B56 标准页（ANSI/ITSDF B56.5-2024，2025-12-16 生效） — https://www.itsdf.org/cue/b56-standards.html
- ANSI 商店页（直连 403；`r.jina.ai` 200 但正文为 cookie 公告 → 仅标题级） — https://webstore.ansi.org/standards/ansi/ansiitsdfb562024
- IEC TS 61496-4-3:2022（视觉保护装置 VBPD，含立体视觉） — https://webstore.iec.ch/en/publication/63436
- SICK safeVisionary2 产品资料 PDF（PL c / SIL1 / Type 2；≤2 m 保护域；双相机才到 PL d） — https://cdn.sickcn.com/media/docs/2/12/112/product_information_safevisionary2_safety_camera_sensors_en_im0103112.pdf
- SICK 产品页 — https://www.sick.com/us/en/catalog/products/safety/safe-3d-cameras/safevisionary2/c/g568562
- ISO 3691-4 二手解读（PL d、0.3 m/s、200×600 与 70×400 测试体）【二手·厂商博客】 — https://www.fabrico.io/blog/iso-3691-4-driverless-industrial-trucks/
- 安全激光扫描仪 vs 相机（厂商博客，均实测 200） — https://www.fsddsk.com/agv-amr-obstacle-avoidance-safety-lidar-5m-vs-20m ｜ https://auto.bridza.com/products/safety-laser-scanner-agv-amr

**失败模式与度量**

- 单应地面测距误差二次增长（>1900 万样本） — https://arxiv.org/abs/2604.10805
- 反光地面/眩光下的深度可靠性融合（RealSense D435 + Jetson Orin Nano） — https://arxiv.org/abs/2606.03421
- 学习式立体深度抗透明/镜面（D3RoMa） — https://pku-epic.github.io/D3RoMa/
- 强日照镜面导致系统性立体深度偏置（StereoLabs 社区） — https://community.stereolabs.com/t/specular-lighting-causing-systematic-stereo-depth-bias-drift/10371
- 在线相机-LiDAR 标定监控与旋转漂移跟踪（OCaMo, IEEE T-RO 2024） — https://github.com/moravecj/OCaMo
- 单目深度评测基准与指标（Eigen 等） — https://arxiv.org/abs/1406.2283
- Depth Anything V2 — https://arxiv.org/abs/2406.09414 ｜ Depth Pro — https://arxiv.org/abs/2410.02073

**指标与数据集**

- DAVIS 2017（J/F/J&F） — https://arxiv.org/abs/1704.00675 ｜ https://davischallenge.org/
- MOSE — https://arxiv.org/abs/2302.01872 ｜ LVOS — https://arxiv.org/abs/2211.10181
- IDF1 — https://arxiv.org/abs/1609.01775 ｜ HOTA — https://arxiv.org/abs/2009.07736
- MOT16 — https://arxiv.org/abs/1603.00831 ｜ DanceTrack — https://arxiv.org/abs/2111.14690 ｜ https://github.com/DanceTrack/DanceTrack
- SA-V 官方数据卡（直连 400，经 `r.jina.ai` 200，正文级） — https://ai.meta.com/datasets/segment-anything-video/

**本仓库证据（未公开 URL，来源为本地快照）**

- `staging/forklift_scenario_research/_remote_evidence/README_root.md`（EfficientTAM Jetson 实测、环境、benchmark）
- `.../docs_remote/warning.md`（单目深度预警；Orin NX TRT 26.5 ms；安全边界）
- `.../docs_remote/forklift_dual_demo.md`、`forklift_dual_demo_field_calibration.md`（双相机演示与验收目标）
- `.../code/benchmarks/placement_accuracy.md`（放置精度合成验证与真车验收模板）

### 11.2 URL 可达性实测（2026-09-14；状态分四类；直连失败项一律经 `r.jina.ai` 复测）

统计（70 条引用 URL）：`200 可达` 69 条、`403 反爬（非失效）` 1 条、`405 方法不允许` 0 条、`000 两通道均失败` 0 条。

| 类别 | 条目 | 复核结论 |
|---|---|---|
| **200 可达（69）** | 其中 **65 条直连即可**（全部 arXiv/PMC/GitHub/官方 PDF/ITSDF/IEC 等），**4 条仅经 `r.jina.ai`**：ANSI 商店页、ifm 产品页、`sam2/sav_dataset/README.md`、Meta SA-V 数据卡 | 直连失败 ≠ 失效；经 `r.jina.ai` 复测均取回内容（见下） |
| **403 反爬（1，非失效）** | `www.iso.org/standard/83545.html`（Cloudflare 挑战页，`r.jina.ai` 亦只得 "Just a moment..." 标题） | 只用它确认标准存在与版本；条款细节改用 **ISO 官方预览 PDF**（`cdn.standards.iteh.ai`，200，15 页，已 `pdftotext` 校验）+ ITSDF 官方页 + 二手解读 |
| **405/其他方法不允许（0）** | 本次未出现 | 若下游遇到，先按 captain 口径用 `r.jina.ai` 复核再判定 |
| **000 两通道均失败（0）** | 无 | 本次**没有任何引用 URL 被判定为失效**；此前被记为"不可达"的 `ai.meta.com/datasets/segment-anything-video/` 经 `r.jina.ai` 复测为 200 且正文完整（20.8 KB），已升级为数据卡级来源 |

三处曾直连失败、经 `r.jina.ai` 复核后升级的来源：

- `webstore.ansi.org/standards/ansi/ansiitsdfb562024`：直连 403；`r.jina.ai` 返回 200，**标题可读**（"ITSDF B56.5-2024 - Safety Standard for Driverless, Automatic Guided Industrial Vehicles and Automated Functions of Manned Industrial Vehicles"），正文被 cookie 公告占据 → **仅标题级来源**，只用于确认标准名称/版本/存在性，条款内容引用 ITSDF 官方页。
- `www.ifm.com/...pallet-pocket-recognition`：直连 403；`r.jina.ai` 返回 200 且正文可读 → 按【宣传】引用其"厘米级（down to the centimetre）货叉导航"自述（见 §3.1），并标注无测试条件、不得当事实。
- `ai.meta.com/datasets/segment-anything-video/`：直连 400；`r.jina.ai` 返回 200 且正文完整 → SA-V 的 51K/643K、191K+452K、类无关无标签、CC BY 4.0 均据此核对（见 §4.1）。

> 完整逐条状态码（含 `r.jina.ai` 复测）：`tmp/tech-scout/url_status.txt`（会话工作区内，非交付物）。SICK 产品页直连返回 200 但正文为 JS 渲染（1.2 KB），其能力数据取自同厂商 PDF 产品资料（200，已 `pdftotext` 校验）。

## 12. 变更记录

- 2026-09-14 初版（tech-scout / t3）：完成 §1–§12；证据分级与 URL 可达性自测已内嵌。
- 2026-09-14 修订 r2（按 captain 的口径要求）：① §0.2 改写为"检索方法与工具限制"，如实记录 web_search 401 / web_fetch 非公网 IP 拦截、实际改用 Tavily MCP（含有效 token 路径与 session 流程）+ curl 检索抓取、以及 200/403/429 引用纪律；② 新增同行评审实车来源（Sensors 26(1):154 / PMC12788346），据此在 §1、§3.1、§4.2 补入"允许误差 Y ≤50 mm、yaw ≤3°、pitch ≤1°"与"6 次实车仅 3 次成功"，并新增失败模式 F12–F15（背景强边缘、标定对光照敏感、惯性测量时机、大转角模板匹配失败）；③ §11.2 状态统计更新为 65×200 / 3×403 / 1×429。
- 2026-09-14 修订 r3（按 captain 的 `r.jina.ai` 与四类状态口径）：① §0.2 增列 `https://r.jina.ai/<URL>` 内容级抓取通道与"直连失败不等于失效、须复测"的判定规则，明确状态只分 `200 可达`/`403 反爬`/`405 方法不允许`/`000 两通道均失败=失效` 四类；② 用 `r.jina.ai` 复测原先直连失败项：ANSI 商店页、ifm 产品页、`sam2/sav_dataset/README.md`、Meta SA-V 数据卡均取回内容，ISO 商店页仍仅有标题 → §11.2 改为 70 条中 69×`200 可达` / 1×`403 反爬` / 0×`405` / 0×`000`，**本次无任何引用 URL 判为失效**；③ 据此升级 SA-V 与 ifm 两条引用为正文级（SA-V 补 191K+452K、类无关无标签、CC BY 4.0；ifm 补"厘米级货叉导航"自述并标【宣传】），并把"不得出现经 web_fetch 验证的表述"写入 §10 核验提示。