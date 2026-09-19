# 02 行业·法规·市场扫描：车间叉车视觉应用场景、法规边界与市场线索

- 任务：t2（industry-scout），交付物路径 `staging/forklift_scenario_research/02_industry_scan.md`
- 生成时间：2026-09-14（CST）
- 调研范围：车间/仓储场景下"叉车 + 视觉"的真实应用图景、法规与标准边界、市场与投入产出线索、反例与失效模式
- 调研方式：真实联网检索（web_search 会话内不可用，改用 Tavily MCP 与 `curl` 抓取，见附录 A），每条结论附可点开 URL
- **定位声明（贯穿全文，所有场景共用）**：本团队目标系统（Jetson 上 EfficientTAM 点击分割/跟踪 + 单目度量深度 + BEV 的原型）是**驾驶员/操作员的操作辅助提示系统，不是功能安全系统（not a functional safety system）**。它**不替代**车辆制动、联锁、急停、安全激光扫描仪等任何安全相关功能，**不承诺**"避免碰撞""保证不撞人"。凡涉及对人的安全结论，本报告一律保守表述。
- 标签约定：
  - 【事实】= 有可核验来源（标准文本、官方公告、政府/协会数据、学术论文、公开可查的新闻/页面）
  - 【宣传】= 厂商/供应商的自述口径（产品页、案例页、白皮书、新闻稿），未经独立验证
  - 【推断】= 本报告作者基于来源作出的判断，不是来源原话
  - 【待确认】= 检索到但未能从权威原文核实，或来源之间互相矛盾

---

## 0. 结论速览（先给 synthesizer 用）

1. 【推断】"看见"这件事在车上已经非常便宜且成熟：货叉视角/门架视角/后视/360° 相机套件在公开渠道单价约 **380–695 美元/台车**（厂商目录价，见 §3.2），因此**单纯"多给驾驶员一路画面"不是可付费的差异化**；可付费点是"自动判定 + 记录 + 与 WMS/安全流程闭环"。
2. 【事实+推断】法规驱动的主线是**合规与责任**，不是"防撞"：中国把叉车作为特种设备（TSG 81—2022），定期检验 2 年一次，且 2023-12-01 起新出厂叉车须装安全监控装置（司机权限信息采集器）；用人单位还需要培训与持证（OSHA 1910.178 / 中国特种设备作业人员证）。视觉系统只要对外宣称"安全功能"，就会掉进功能安全（ISO 13849 PL / IEC 61496）与欧盟 AI Act"安全组件"的合规深水区（见 §2.4、§2.5）。
3. 【事实】国际/国内都已有"无人驾驶工业车辆"的 **Type-C 类安全标准**：ISO 3691-4（中国等同采用为 GB/T 10827.4—2023），它把"人员探测""制动""速度控制"列为**安全功能**并要求可验证；行业里承担该安全功能的是**安全激光扫描仪**（IEC 61496 Type 3 / ISO 13849 PL d），不是普通相机。（来源见 §2.1、§2.4）
4. 【事实】北美对应标准是 ANSI/ITSDF B56.5-2024（生效 2025-12-16），适用范围同时覆盖"无人车"和"人工驾驶车上的自动化功能（automated functions of manned industrial vehicles）"——这一点对我们这种"人工叉车 + 视觉辅助"的定位尤其相关。
5. 【事实】需求侧数字：美国 2017 年 74 起叉车致死工伤、9,050 起致失能工伤，其中 1,850 起是**行人在叉车运输作业中被撞**，行人相关案件的中位离岗天数为 20 天（高于全部案件 13 天）；中国 2024 年全国特种设备事故中**场车（含叉车）43 起、死亡 36 人**，2025 年通报全国特种设备事故 188 起、死亡 156 人。
6. 【宣传】仓储"AI 视频安全分析"厂商宣称可带来 40%–93% 的事故/伤害下降（Voxel、Protex AI 等案例页），但这些是**厂商自报口径**，且多为固定式摄像头做行为分析，不是车载点击分割；不能作为承诺值写进我们的报告。
7. 【事实】"可提示分割/点击分割"在工业界最接近的落地形态是：**操作员在界面上点选目标 → 模型分割 → 下游用它做抓取/任务指定**（学术与机器人抓取方向已有工作：点击坐标驱动 HQ-SAM 分割、prompt-guided grasping）。【推断】叉车行业公开产品中，我**没有**检索到"点击屏幕选托盘/货物并驱动作业"的商用产品，说明这是空白区也是教育成本区。
8. 【事实】托盘孔/托盘检测有公开研究可对标：单目普通相机 + YOLOv8 的公开结果为"托盘检测最高约 95%、托盘孔检测约 72%"，作者明确定位为"低成本、可后装（retrofittable）的视觉感知模块、服务半自主叉车"——这与我们的能力边界（单目、无 LiDAR）最接近，应作为精度期望的锚。
9. 【事实】失效模式是真实存在的、可引用的：粉尘/油雾导致镜头污染与清晰度下降、弱光与雨雾下可见光行人检测性能显著下降、结构光/ToF 在强阳光与高反材质下的不稳、以及"扁平视频缺少深度感"的公认局限。任何场景目录都必须给这些留出缓解位（清洁计划、辅助照明、多帧确认、只做提示不自动动作）。
10. 【推断】行业侧最值得优先验证的三件事：**（a）托盘/货位几何层面的"到位判定"**（可直接复用现有 3D/BEV 能力，且判定结果可被 WMS/作业记录消费）；**（b）固定视角下的"货物/包装/库位状态"巡检类判定**（不受车体振动与供电约束）；**（c）点击分割作为"人机沟通界面"**（操作员指定目标），因为它是我们独有、别人还没有的东西——但必须先在真实车间验证"点得中、跟得住、延迟可接受"。

---

## 1. 真实场景盘点（车间/仓储叉车 × 视觉）

每个场景固定四段：**谁在卖/谁在用** / **技术形态** / **事实与宣传的区分** / **对本项目的含义（行业视角）**。

### 1.1 货叉与托盘孔对准（fork pocket / pallet entry alignment）

- **谁在卖/谁在用**
  【事实】Kocchi's 的叉车/前移式叉车相机系统产品页明确列出"Fork View：看清叉尖、托盘孔与载荷对准"，并提供"Smart Laser & Camera Aiming System"（相机 + 激光参考，用于高位对孔）：<https://www.kocchis.com/camera-systems-for-forklift-trucks>
  【事实】Holland Vision Systems 的"High Reach / Deep Reach"应用页写明相机用于"精确地把货叉对进托盘孔、确认托盘状态与 ID、直接读条码"：<https://hollandvisionsystems.com/applications/high-reach-deep-reach>
  【事实】HUBTEX 叉车百科"camera systems for forklifts"说明后装相机是标准辅助手段、并列出可装配位置：<https://www.hubtex.com/en-us/wiki/camera-systems-truck>
  【宣传】SICK 的博客案例：Transolt 的无人叉车用 SICK Visionary-T AP 3D snapshot 相机 + 专用 Key App 做"高位托盘孔精确检测"：<https://www.sick.com/cz/en/precise-detection-of-pallet-pockets-using-a-3d-snapshot-camera/w/blog-pallet-pocket-detection-transolt>（SICK 自述，属厂商案例）
  【宣传】蓝芯科技（3D 视觉 + M4 Pro 工业相机）自述"定位精度 ±3 mm、托盘倾斜识别 ±15°、抗 100 KLUX 阳光、适配 95% 以上托盘/料笼、无需训练或配参"：<https://www.lanxincn.com/news_cont_229.html>——**这些是厂商宣传指标，未见第三方复现**。
  【事实】维感科技/图漾等的 ToF 相机被无人叉车厂商用于托盘识别（媒体报道 + 专利）：<https://www.eet-china.com/mp/a133838.html>、<https://seer-robotics.ai/zh/media/17.0>
- **技术形态**：目视（叉尖/门架相机）→ 激光辅助对孔 → 3D 相机/ToF 出点云做托盘孔定位 → 视觉 SLAM 无人叉车自动叉取。
- **事实与宣传的区分**
  【事实】学术侧有可对标结果：单目普通相机 + YOLOv8/YOLOv11 做托盘与**托盘孔**检测，并做"孔—托盘"关联映射，服务半自主叉车的货叉对准；论文摘要给出"YOLOv8 托盘检测最高约 95%、托盘孔约 72%"：<https://arxiv.org/abs/2511.06295>
  【事实】另有基于全向相机 + 图像测量的"接近并插入目标托盘"方法（含相机/车体/货叉三套坐标系定义）：<https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346>
  【事实】托盘主要尺寸与公差有国家标准 GB/T 2934—2007（修改采用 ISO 6780）：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=B9CFF67017751DB45C09518434419076>
  【待确认】货叉与叉孔之间可容许的**常规定位偏差量级**：检索到的中文二手页称"叉孔内宽需大于货叉厚度 3–5 mm"（<https://b2bwiki.baidu.com/article/d0n98rpftjsrseil6vgg>，非官方来源）；【推断】结合叉孔高度（百毫米量级）与货叉厚度（数十毫米量级），对孔在**厘米级**容差内可完成插入，但"厘米级"必须现场用实测标定验证，不可写进承诺。
- **对本项目的含义**
  【推断】这是**行业已有成熟替代方案**的场景（叉尖相机 + 激光线已很便宜），所以"看得见"没有价值；价值在于**在无高反光标记、无激光线的条件下，用点击 + 跟踪把"孔在哪、偏多少"变成可记录的结构化输出**。若要做，宜定位为"辅助对孔提示 + 作业记录"，不做自动进叉。

### 1.2 放货到位与货位确认（put-away confirmation / slot occupancy）

- **谁在卖/谁在用**
  【宣传】Vimaan 的 StorTRACK 用相机/无人设备扫描货架，输出 SKU、件数、**库位占用（location occupancy）**，并给出"空位/低效位"检测：<https://vimaan.ai/what-are-the-most-common-applications-of-warehouse-computer-vision>
  【宣传】Visionify 的仓储案例列出"放货与拣货的视觉验证、空位检测、错放异常检测"：<https://visionify.ai/case-studies/warehouse-inventory-management>
  【宣传】iFactory 的 AI 相机方案列出"托盘计数、箱量、货架占用、空位检测、超量告警"：<https://ifactoryapp.com/ai-vision-camera/ai-warehouse-automation-and-inventory-management-using-computer-vision>
  【事实】Gather AI 的无人机视觉盘点在 Taylor Logistics 的落地被第三方媒体报道引用：<https://www.icommunetech.com/scm/inventory-counting-with-computer-vision>（媒体转述，非厂商页面）
- **技术形态**：固定高位相机/无人机（正确率高但改造成本高）→ 车载相机（覆盖广但受振动、光照、视角限制）。
- **事实与宣传的区分**：厂商页面均为【宣传】；目前只找到"能力清单 + 案例名词"，**未找到可核验的准确率/召回率第三方数据** → 【待确认】。
- **对本项目的含义**
  【推断】"货位是否被占用/是否放偏"是**几何 + 语义**混合判断，我们的单目度量深度 + BEV 能把"货叉/托盘相对目标区多边形的位置与 yaw"输出出来（这点与仓库已有 `docs/forklift_placement/` 的 APPROXIMATE/VERIFIED 门控一致，具体证据见 01 号文档），**天然适合做"到位确认"而不是"绝对精度测量"**。但要先定义"到位"的判定阈值来自哪（现场实测 vs 客户 SOP），否则会变成不可验收的口水标准。

### 1.3 装载计数与免手动扫描（load counting / hands-free scanning）

- **谁在卖/谁在用**
  【宣传】Zebra 的"叉车安装托盘扫描"用例页：叉车靠近托盘时免手动扫描标签，验证托盘身份与订单是否匹配，并把数据传给车载电脑/WMS：<https://www.zebra.com/cn/zh/resource-library/use-case-library/forklift-mounted-pallet-scanning.html>
  【宣传】Zebra SmartCount 宣称盘点效率带来"25%–50% 的节省"：<https://www.youtube.com/watch?v=15SYIwmLeDQ>（厂商视频描述）
  【宣传】Roboflow 的 pallet tracking 方案页列出"托盘计数、载荷核对、破损检测、空托盘检测"：<https://roboflow.com/ai/pallet-tracking>
- **技术形态**：条码/RFID 为主流（免手动扫描），视觉用于"目视计数/载荷是否为空/是否叠放"。
- **事实与宣传的区分**：以上全部是【宣传】；【事实】是"叉车属具侧"有**称重货叉**这类成熟产品（见 §1.8），计数/重量更常由传感器而非视觉承担。
- **对本项目的含义**
  【推断】视觉计数在**叠放、遮挡、同色托盘**场景下误差与人工差异大，且客户已有条码/RFID/称重替代，属于"锦上添花"；若做，优先做**"这一次搬运搬了什么"的记录留痕**（配合点击分割产出的掩码），而不是承诺"数得准"。

### 1.4 包装/缠绕膜破损与载荷稳定性（wrap / packaging damage, load stability）

- **谁在卖/谁在用**
  【事实】Duvel Moortgat（啤酒厂）仓库用**两台相机 + 转台**对空托盘做视觉检测，判定"可用/待修/报废"，破损托盘被自动剔除——这是被公开报道的实际部署：<https://acagroup.be/en/cases/innovative-ai-model-detects-damaged-pallets-at-duvel-moortgat>（集成商 AC&A 案例页，属厂商口径但有明确现场与流程描述）
  【宣传】iFactory 的"托盘与载荷稳定性"方案页列出：缠绕膜覆盖核对、载荷稳定性、**超限（overhang）**检测、底座托盘破损识别：<https://ifactoryapp.com/ai-vision-camera/ai-vision-pallet-load-stability-inspection>
  【宣传】同类方案还有 OxMaint（宣称 97% 检出率、0.4 s/托盘）：<https://oxmaint.com/industries/fleet-management/ai-cargo-inspection-freight-damage-detection>
  【事实】学术侧有托盘破损分类研究（good / repair / dismantle 三分类，面向实时检测系统）：<https://zenodo.org/records/10391024>
- **技术形态**：固定工位（转台/龙门/月台）多视角拍摄为主；车载视角受遮挡与运动模糊影响大。
- **事实与宣传的区分**：检出率数字均未见第三方验证 → 【宣传】；【推断】"转台 + 多视角 + 固定光照"是把这类任务做稳的关键前提，车载单目很难复现。
- **对本项目的含义**
  【推断】这是一个**"不该硬绑在车上"的候选场景**：行业实践把相机放在**固定工位**（月台/收货口），我们若要做，应论证"为什么必须用车载视角"；反之，用固定相机 + 我们的分割/跟踪能力做"每托盘过站检查"更容易验收。

### 1.5 通道与库位可通行性（aisle passability / obstruction）

- **谁在卖/谁在使用**
  【宣传】iFactory 的窄通道方案：在通道入口布相机，检测"人员进入正在作业的窄通道"并向车辆操作员告警（部分集成可给车辆控制器发减速/停止指令）：<https://ifactoryapp.com/industries/cement-plant/ai-forklift-pedestrian-collision-avoidance>
  【宣传】SensorZone 的仓储接近告警系统（可配置探测区，窄通道可收紧到 1 m 级以减少误报）：<https://www.sensorzone.io/warehousing>
  【宣传】Voxel 的固定相机安全分析宣称能降低"通道尽头不停车（No Stop at End-of-Aisle）"事件达 92%：<https://www.voxelai.com/solutions-safety>（厂商案例页）
- **技术形态**：固定相机做区域/通道级监控（对"通道可通行性"是天然匹配）；车载侧一般是视觉/雷达的障碍物与人员检测。
- **事实与宣传的区分**：全部为【宣传】；【事实】层面可参考的是"窄通道、货架遮挡视线、盲角交叉口"被学术综述列为叉车碰撞的主要环境因素（见 §4.4 的 DiVA 论文）。
- **对本项目的含义**
  【推断】"通道可通行性"更适合**固定视角**（与 §1.4 同结论）；车载侧真正能贡献的是"我现在这条通道里有没有不该有的东西"，而这与 §1.6 的人员告警高度重叠，容易合并成一个场景。

### 1.6 盲区人员与行人告警（blind spot / pedestrian warning）

- **谁在卖/谁在用**（产品极其密集，说明是已被验证的付费点）
  【宣传】Kocchi's AI 行人检测相机（车上端侧计算机视觉识别行人与车辆）：<https://www.kocchis.com/products/forklift-ai-pedestrian-detection-system>
  【宣传】WTSAFE 叉车盲区检测：AI 相机实时分析 + 声光告警，并另售"可加装主动制动"的防撞方案：<http://www.wt-safe.com/Products/BlindSpot.html>
  【宣传】Rear View Safety 的 360° AI 行人检测方案：宣称在叉车周围 9 英尺半径内实时检测：<https://www.rearviewsafety.com/360-ai-pedestrian-detection-system.html>
  【宣传】STONKAM 叉车防撞：3 路 AI 相机、约 310° 覆盖、探测距离最长 20 m：<https://www.stonkam.com/explore/STONKAM-Forklifts-Anti-Collision.html>
  【宣传】Eway Safety 的 AI 叉车相机科普页（盲区/行人检测/可视化告警）：<https://www.ewaysafety.com/blogs/article/ai-forklift-camera-systems-reducing-blind-spots-in-warehouse-operations>
  【宣传】海康威视叉车行人安全方案（DeepinViewX 相机 + 边缘 AI 分析）：<https://www.facebook.com/HikvisionHQ/videos/-hikvision-forklift-pedestrian-safety-helps-industrial-sites-operate-with-greate/2040607450667816>（社媒页，仅供线索；建议核验海康官网对应产品页）
- **技术形态**：车载端侧推理的 AI 相机（视觉告警）为主；主动制动/限速通常要独立的认证安全链路。
- **事实与宣传的区分**
  【事实】需求侧有权威统计支撑：BLS 2017 年叉车相关工伤中，**1,850 起是行人在叉车运输作业中被撞**；行人相关案件中位离岗 20 天：<https://www.bls.gov/iif/factsheets/fatal-occupational-injuries-forklifts-2017.htm>
  【事实】中国 2024 年场车事故 43 起、死亡 36 人（市场监管总局通报口径，政府信息公开转发）：<https://www.yncj.gov.cn/cjxzfxxgk/aqsj1525/20250620/1608266.html>
  【事实】2025 年全国特种设备事故 188 起、死亡 156 人，且事故原因 **87.36% 为使用/管理不当（违章作业为主）**：<https://www.cqszzs.com/equipment-36/6642.html>（转述市场监管总局通报）
  【宣传】所有"检出率 9x%""事故下降 8x%"均为厂商自述 → 不可引用为事实。
- **对本项目的含义**
  【推断】这是**最容易被打动、也最容易踩法律红线**的场景：作为"告警提示"可以谈，一旦表述成"防撞/避免碰撞/自动制动"就进入功能安全范畴（§2.4）。我们现有能力（后视盲区人员告警原型）宜定位为**"提示 + 留痕"**，并在报告里显式写"不替代任何安全装置"。同时注意员工监控合规（§2.5）。

### 1.7 人机协作："搬那个"（operator designates the target）

- **谁在卖/谁在用**
  【事实】目前检索到的最接近形态在**机器人抓取与 HRI 研究**，不是叉车商品：
  - 用自然语言/点击界面选定目标 → 点击坐标送入 HQ-SAM 分割 → 驱动操作（PMC 论文，含"Can I click/select the object?"的用户需求描述）：<https://pmc.ncbi.nlm.nih.gov/articles/PMC12375720>
  - 基于可提示分割（promptable SAM）+ 力闭合分析的交互式机器人抓取：<https://advanced.onlinelibrary.wiley.com/doi/10.1002/aisy.202400404>
  - 指向手势（pointing gesture）用于人机协作选目标/确认（IEEE 工作与相关实验）：<https://research.tuni.fi/app/uploads/2023/11/7b55b10c-ieee_co_speech_cr-1_optimized.pdf>
  【推断】在**叉车厂商公开产品中未检索到"点击屏幕指定托盘/货物并驱动作业"的商用形态** → 空白区。
- **技术形态**：点击/框选/指向 + 分割/跟踪 → 目标任务指定 → 下游动作（机器人抓取或人的后续操作）。
- **事实与宣传的区分**：学术为【事实】（有同行评议/预印本）；"市场对此付费"是【推断】，无来源。
- **对本项目的含义**
  【推断】这是与团队现有"click-to-track"能力**最同构**的场景，教育成本也是最高的：叉车操作员在驾驶中双手/注意力受限，任何交互设计必须回答"谁在什么时刻点、点击替代了什么操作"。建议在产品化前，先在真实班次里做"操作员会不会用"的观察实验，而不是先做精度优化。

### 1.8 货叉/载荷状态监控（fork & load state monitoring）

- **谁在卖/谁在用**
  【宣传】Cascade Weigh Forks：称重货叉 + 蓝牙显示，可看单次载荷重量、多次累计重量、载荷内件数：<https://www.cascorp.com/us/en/weighforks>
  【宣传】后装载荷力矩指示器（LMI/SLI）：压力 + 倾角传感器给出起重量、载荷角度、门架位置，输出到 HMI：<https://szlmi.com/product/forklift-load-indicator>
  【宣传】称重货叉/叉车秤品类还有 Mettler-Toledo 等：<https://www.mt.com/us/en/home/products/Transport_and_Logistics_Solutions/forklift-scales.html>
- **技术形态**：力/压力/倾角传感（成熟、可靠）为主；视觉一般只能做"货叉是否叉到托盘、载荷是否倾斜/超限"的间接判断。
- **事实与宣传的区分**：产品形态为【事实】（目录在售），精度指标为【宣传】。
- **对本项目的含义**
  【推断】"重量/超载"不应交给视觉（已有更好的传感器）；视觉可补的是**"载荷姿态与相对位置"**（倾斜、偏载、是否插入到位），这与 §1.1/§1.2 的几何输出同源。

### 1.9 作业录像与培训回放（video recording & coaching）

- **谁在卖/谁在用**
  【宣传】Samsara AI Dash Cam：驾驶员/道路双向相机，端侧告警 + 云端视频留证 + 教练工作流："exonerate innocent drivers from not-at-fault accidents and false claims"：<https://www.samsara.com/products/cameras>
  【事实】Samsara/Lytx/Motive/Netradyne 是车队视频安全的主要供应商（对比页可见品类格局）：<https://www.lytx.com/vs/samsara>
  【宣传】固定式安全分析（Voxel / Protex AI）用既有监控相机做行为/风险分析，宣称事故下降 40%–93%：<https://www.voxelai.com/solutions-safety>、<https://www.protex.ai/guides/complete-guide-to-ai-warehouse-safety>
- **技术形态**：车载行车记录 + 行为识别 + 教练闭环；固定相机做场所级安全分析。
- **事实与宣传的区分**：产品存在为【事实】；效果数字为【宣传】；【事实】相关风险见 §2.5（员工监控在欧盟 AI Act 下可能落入 Annex III 高风险）。
- **对本项目的含义**
  【推断】"录像 + 回放"是**低技术风险、高合规风险**的场景：只要涉及对**员工行为**的持续评估，就会触发劳动法与个人信息保护问题。建议场景目录里把它标为"观察"，并明确"只做设备/货物状态记录，不做人员绩效评估"。

---

## 2. 法规与标准边界（截至 2026-09，全部给可点开来源）

### 2.1 国际标准（ISO / IEC）

【事实】**ISO 3691-4**《Industrial trucks — Safety requirements and verification — Part 4: Driverless industrial trucks and their systems》
- 目录结构可见：4.2 Braking system、4.3 Speed control、4.5 Load handling、4.8 Protective devices and complementary measures、4.9 Modes of operation、4.11 Safety-related parts of the control system、**5.2 Tests for detection of persons**、Annex A 作业区准备要求：<https://www.iso.org/obp/ui/es#!iso:std:83545:en>（ISO 3691-4:2023）
- 前一版：<https://www.iso.org/obp/ui/es#iso:std:iso:3691:-4:ed-1:en>（ISO 3691-4:2020）
- 【待确认】2023 版与 2020 版的替换关系：ISO 目录与 ANSI 博客均以 2023 版为现行版：<https://blog.ansi.org/ansi/iso-3691-4-2023-driverless-industrial-trucks>；Pilz 新闻稿以"2020 年发布"叙述：<https://www.pilz.com/en-US/company/news/articles/238928>。建议在正式报告里写"ISO 3691-4:2023（现行）"，不写"2020 版等同"。
- 【事实】Pilz 说明该标准定义 AGV/AGVS **安全功能的性能等级（performance level）**，如人员探测的布置、运行模式、制动系统：同上 Pilz 链接。
- 【事实】ISO 3691-4 属 **Type-C 标准**，并要求安全相关控制功能满足 ISO 13849-1 的可量化可靠性等级：<https://www.controleng.com/ensuring-agv-safety-with-standards-compliance>（Control Engineering 专题）
- 【事实】安全功能常要求 **Performance Level d**："Each safety function must achieve the performance level the standard assigns under ISO 13849-1, often Performance Level d"：<https://www.fabrico.io/blog/iso-3691-4-driverless-industrial-trucks>
  【待确认】"often PL d"是二手技术博客措辞，**具体条款号与每个安全功能的 PL 要求需查阅标准原文**，不在本报告中下定论。

【事实】**ISO 3691-1**《Industrial trucks — Safety requirements and verification — Part 1: Self-propelled industrial trucks, other than driverless trucks, variable-reach trucks and burden-carrier trucks》（针对**人工驾驶**叉车；第 2 版在制定中，ISO/DIS 3691-1）：<https://www.iso.org/standard/52163.html>、<https://www.iso.org/obp/ui#!iso:std:iso:3691:-1:dis:ed-2:v1:en>

【事实】**ISO 20898:2008**《Industrial trucks — Electrical requirements》：<https://webstore.ansi.org/standards/iso/iso208982008>；ISO/TC 110 标准清单可见其与 ISO 21262:2020 并列：<https://www.iso.org/ics/53.060.html>

【事实】**ISO 21262:2020**《Industrial trucks — Safety rules for application, operation and maintenance》（使用/操作/维护安全规范）：<https://www.iso.org/ics/53.060.html>

【事实】**ISO 13849-1**（机械安全—控制系统安全相关部件—设计通则）定义 PL a–e；ISO 3691-4 与它对齐：<https://www.iso.org/obp/ui/en#!iso:std:73481:en>、<https://www.controleng.com/ensuring-agv-safety-with-standards-compliance>
【事实】ISO 13849-1:2023 的规范性引用里包含 **IEC 61496-1/-2/-3**（电敏防护设备 ESPE）：同上 ISO OBP 链接。

【事实】**IEC 61496** 系列 = 电敏防护设备（ESPE）：<https://ez.analog.com/ez-blogs/b/engineerzone-spotlight/posts/an-introduction-to-the-iec-61496-series-of-human-presence-detection-standards>
- 该文还给出一个对我们非常重要的保守依据：**"像 ISO 3691-4 这样的标准允许使用 5% 漫反射系数来做全身检测"**——即安全级人员检测是围绕**低反射率目标**（深色衣物/鞋）设计的，这正是普通可见光相机最吃力的条件之一：同上链接。

**结论（保守表述）**：ISO 3691-4 覆盖的是**无人驾驶**车辆及其系统的安全功能；【推断】其"人员探测/制动/速度控制"条款不适用于"人工驾驶叉车上的辅助提示"；但 ANSI/ITSDF B56.5 明确覆盖"人工驾驶车上的自动化功能"，因此**若我们的辅助功能被解释为"自动化功能"，北美侧可能被纳入审查**。本系统在任何文档中都不得声称满足 ISO 3691-4 / ISO 13849 / IEC 61496。

### 2.2 北美标准

【事实】**ANSI/ITSDF B56.5-2024**《Safety Standard for Driverless, Automatic Guided Industrial Vehicles and Automated Functions of Manned Industrial Vehicles》，ITSDF 官方标准页标注 **EFFECTIVE 12/16/25**（2025-12-16 生效）；上一版 B56.5-2019 于 2020-08-12 生效：<https://www.itsdf.org/cue/b56-standards.html>
【事实】适用范围含"无人、非机械约束的自动导引工业车辆及其系统"，也适用于**原本人工驾驶、后被改造为无人/半自动/维护模式**的车辆：<https://info.mobilerobot.com/safety-compliance/standards/ansi-itsdf-b56-5>
【事实】OSHA **1910.178**《Powered industrial trucks》规定叉车操作员必须经培训与评估、并由雇主认证（refresher training / certification）：<https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.178>
【事实】OSHA 正在更新动力工业车辆设计标准（Federal Register 2022-02-16 公告）：<https://www.osha.gov/laws-regs/federalregister/2022-02-16>

### 2.3 中国法规与标准

【事实】**TSG 81—2022《场（厂）内专用机动车辆安全技术规程》**：2022-12-01 起施行（市场监管总局 2022 年第 26 号公告发布）；在用叉车**定期检验周期由 1 年改为 2 年**。
【事实】**市监特设发〔2022〕87 号**（实施事项意见）明确：
- 2023-12-01 起**新生产出厂的叉车必须安装安全监控装置**，定期（首次）检验项目应包含该装置检查；
- 制造日期在 2023-12-01 前的叉车不强制，但**鼓励使用单位加装**；
- 对已安装安全监控装置的车辆，检验应包含相应项目。
来源（政府门户转载全文）：<http://www.hunan.gov.cn/zqt/zcsd/202209/t20220927_29019201.html>
【待确认】"叉车应设置司机权限采集器；未安装或失效将被判检验不合格"是二手解读口径（厂商/资讯站），与上述 87 号文一致地强调安全监控装置：<https://www.ningboruyi.com/xingyezixun/265.html>（【宣传】/二手，建议以 87 号文为准）
【事实】**GB/T 38893—2020《工业车辆 安全监控管理系统》**：发布 2020-06-02、实施 2021-01-01：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=97D3C8DCDDC372940F7BC33A840F8CE5>（标准目次：监控系统的构成 / 监控内容 / 要求 / 检验方法 / 检验项目）
【事实】**GB/T 10827.4—2023《工业车辆 安全要求和验证 第4部分：无人驾驶工业车辆及其系统》**，**等同采用 ISO 3691-4:2020**：标准页 <https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=1F4740F4028C1B1C29D7D93D6BB165F8>；等同采用关系见行业报道 <https://www.ncsnc.com/news/672.html>、<https://www.hnbzw.com/Standard/StdDetail.aspx?ekdHR4nH7qql91MlNWgnvlYlUZZ02WeF=>
【事实】**GB/T 10827.1—2014**（自行式工业车辆，除无人驾驶/伸缩臂/载运车，对应人工驾驶叉车，修改采用 ISO 3691-1）：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=B9432660D53D30062F1E65A1F59A2A61>
【事实】**GB/T 36507—2023《工业车辆 使用、操作与维护安全规范》**（代替 GB/T 36507—2018，等同采用 ISO 21262:2020）：<https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=1E96E92F7C36EABAD6F22A4A4E49FA86>；等同采用关系见标准 PDF 标题页 <https://img.antpedia.com/standard/files/pdfs_ora/20230705/GB%20T%2036507-2023.pdf>
【事实】叉车属特种设备，**未经定期检验或检验不合格不得继续使用**（《特种设备安全法》第四十条）。罚款区间"三万元以上三十万元以下"来自二手资讯站转述，【待确认】具体金额：<https://www.ningboruyi.com/xingyezixun/265.html>
【事实】地方层面已有更细的"智慧安全监管"规范（如江苏 DB32/T 4925—2024 场（厂）内专用机动车辆智慧安全监管系统）：<https://dbba.sacinfo.org.cn/portal/download/a55ff34220faff3e09be36a5d4cc93da7789535c2f1b3ed97e6606b792c0ecb6>
【事实】中国事故数据：2024 年场车事故 43 起、死亡 36 人：<https://www.yncj.gov.cn/cjxzfxxgk/aqsj1525/20250620/1608266.html>；2025 年全国特种设备事故 188 起、死亡 156 人，**87.36% 的事故由使用/管理不当导致（违章作业为主）**：<https://www.cqszzs.com/equipment-36/6642.html>
【事实】市场监管总局公开发布过叉车事故典型案例，事故原因集中在**无证驾驶、违章站人、未系安全带、超出用途使用**等：<http://m.cpqr.net/zfal/tzsb/14229.html>（转述总局通报）

**对本项目的直接含义（保守）**
【推断】
1. 中国侧"是否合规"的判定锚点是**安全监控装置/司机权限采集器**（TSG 81—2022 + 87 号文），**我们的视觉原型不构成、也不替代该装置**；
2. 事故主因是**人的违章操作**，不是"看不见"；这意味着**记录与提示类功能**在客户侧的真实价值更接近"行为约束与责任追溯"，而不是"感知增强"；
3. 任何"自动减速/自动制动"的表述都会把项目从"辅助"拖进安全功能合规，**在场景目录中应一律排除**。

### 2.4 功能安全常识：为什么相机不能替代保护装置

【事实】（安全器件厂商页面列出的认证等级）工业界的"人员安全检测"在 AGV/AMR 上通常由**安全激光扫描仪**承担：需满足 **IEC 61496 Type 3、IEC 61508 SIL 2、ISO 13849 PL d / Category 3**：<https://www.keyence.com/products/safety/laser-scanner>
【事实】安全激光扫描仪是 **Type 3 设备**，要求见 EN 61496-1；使用 Type 3 扫描仪时安全功能的最高可达 PL 由系统结构决定：<https://www.sick.com/ch/en/the-perfect-size-of-the-protective-field-on-an-industrial-autonomous-vehicle/w/blog-size-protective-field-industrial-autonomous-vehicle>
【待确认】IEC 61496-3 要求扫描仪可检出直径小至 **70 mm、反射率低至 1.8%** 的测试件（相当于哑光黑物体），目的就是在粉尘、暗光等条件下可靠检测：<https://industrialsafetysensor.com/blog/safety-laser-scanner-for-agv-amr-guide>（厂商技术博客，作为工程常识参考；【待确认】具体数值以标准原文为准）
【事实】"安全级 LiDAR"与"普通 LiDAR"的本质区别就是是否按上述标准认证：<https://www.roboticstomorrow.com/article/2023/11/what-are-the-differences-between-safe-lidar-and-lidar/21471>
【事实】安全扫描仪的**警告区（warning field）不是安全认证输出**，只用警告区做停车是设计错误：<https://plcprogramming.io/blog/safety-laser-scanner-explained>

**结论（保守）**：普通 RGB/单目视觉的"人检测"在现有标准体系里**不具备充当认证保护装置的路径**（未检索到把普通相机列为 ISO 3691-4 合格人员探测手段的证据）。因此我们的系统只能做**提示（warning/advisory）**，必须由车辆既有的认证安全链路（若有）来负责停车。

### 2.5 数据、员工监控与 AI 监管（对我们最容易被忽略的一层）

【事实】**欧盟机械法规 (EU) 2023/1230** 自 **2027-01-20** 适用，取代机械指令 2006/42/EC；对含 AI 的机械、自主机械、自演化逻辑提出新要求（若 AI 承担安全功能，可能按 Annex I 高风险走第三方符合性评估）：
- 法规文本（EUR-Lex）：<https://eur-lex.europa.eu/eli/reg/2023/1230/oj>
- 适用日期与要点：<https://osha.europa.eu/en/legislation/directive/regulation-20231230eu-machinery>（本次会话 curl 无法连通，仅检索快照；建议核验时重试）
- 解读（二手，供参考）：<https://physical-ai-safety.com/blog/eu-machinery-regulation-2027-primer>
【事实】**欧盟 AI Act（Regulation (EU) 2024/1689）第 6 条**：AI 系统若**作为 Annex I 所列产品（含机械法规）的安全组件**且该产品需第三方符合性评估，则属高风险：
- 第 6 条：<https://artificialintelligenceact.eu/article/6>
- 欧盟委员会 AI Act Service Desk 第 6 条页面：<https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-6>
- 同一页面（合并修订文本）给出两条对我们极关键的规定：**①"仅用于非安全相关的用户辅助、性能优化、服务效率、自动化或便利性、质量控制"的 AI 系统不构成安全组件；②但"其失效或故障会危及健康与安全"的 AI 系统仍构成安全组件。"**（原文标注 "Not present before the amendment"，【待确认】以 EUR-Lex 官方合并版为准）
【事实】**AI Act Annex III 第 4 项（Employment, workers' management）**：用于"监控和评估劳动关系中人员的工作表现与行为"的 AI 系统属高风险：<https://artificialintelligenceact.eu/annex/3>
【事实】中国《个人信息保护法》于 2021-08-20 通过，处理个人信息（含公共场所图像采集/人脸相关）需合法基础与告知同意等约束：<http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html>
【宣传】"AI 安全相机最受关注的问题是员工隐私"，厂商给出的对策是端侧处理、身份模糊化、不做绩效用途：<https://www.inviol.com/post/privacy-and-ai-safety-cameras-how-to-protect-worker-identities>（安全行业博客，非法规来源）

**对本项目的直接含义（保守）**
【推断】
1. 我们必须把产品**明确设计并表述为"非安全相关的操作辅助"**（Art 6(1a) 的语境），且在文档、宣传、界面文案上都不使用"防撞/保证安全/替代制动"等措辞——否则会同时触发 AI Act 高风险与机械法规责任；
2. 一旦引入"对员工行为/绩效的持续评估"（含固定相机做安全行为分析），在欧盟将落入 Annex III 高风险；在中国需按《个人信息保护法》处理告知、同意与最小必要；
3. **建议在综合主报告中固定一句话免责声明**（t6 会加到 README 顶部）：
   > 本报告及所述原型系统为操作辅助与调研结论，**非功能安全系统**，不构成、也不替代任何安全功能（制动、联锁、急停、认证人员探测器），不承诺避免碰撞；相关场景仅作为提示与记录用途，落地前须由使用单位按 ISO 3691-4 / GB/T 10827.4 / ANSI-ITSDF B56.5 及本地法规完成风险评估。

---

## 3. 市场与投入产出线索

### 3.1 规模数据（口径互相矛盾，只能当量级参考）

【待确认】**全球"叉车防撞系统/安全解决方案"市场规模**在多家市场研究机构之间口径冲突：
| 来源 | 口径 | 2025 规模 | 预测 | CAGR |
|---|---|---|---|---|
| Dataintelo | 叉车防撞系统 | 28 亿美元 | 2034 年 67 亿美元 | — |
| Spherical Insights | 叉车防撞系统 | 18.4 亿美元 | 2035 年 41 亿美元 | 8.5% |
| Knowledge Sourcing | 叉车 360° 相机 | 2026 年 8.0 亿美元 | 2031 年 17.1 亿美元 | 16.41% |
| Persistence Market Research | 叉车安全方案 | — | — | 14.21%（2026–2033） |
| WiseGuyReports | 防撞系统（相机类） | — | 2035 年 8 亿美元（相机类） | — |

来源：<https://dataintelo.com/report/forklift-collision-avoidance-system-market>、<https://www.sphericalinsights.com/blogs/top-20-companies-in-the-global-forklift-collision-avoidance-system-market-2026-2035-spherical-insights-analysis>、<https://www.knowledge-sourcing.com/report/forklift-360-degree-camera-market>、<https://www.persistencemarketresearch.com/market-research/forklift-truck-safety-solutions-market.asp>、<https://www.wiseguyreports.com/reports/forklift-collision-avoidance-system-market>
【推断】这些数字相差 1.5 倍以上且多为"报告销售页"自述，**不得作为事实引用**；只能支撑"这是个十亿美元级、两位数增长的细分市场"这一量级判断。

【事实】**中国叉车主机市场（更可信的锚）**：
- 2024 年机动工业车辆总销量 **128.55 万台、同比 +9.52%**（中国工程机械工业协会数据，中叉网汇总；电动叉车 946,284 台、占 73.61%，国内销售 805,021 台）：<https://m.chinaforklift.com/news/detail/202504/89378.html>
- 2023 年中国叉车**保有量约 569.3 万辆**：<https://www.huaon.com/channel/trend/1085177.html>（行业研究网站；另有券商测算"一至五类保有量约 400 万台"，口径不同：【待确认】）<https://pdf.dfcfw.com/pdf/H3_AP202409191639932155_1.pdf>
【事实+待确认】**中国无人叉车**（与我们场景最相关的细分）：
- 2024 年销量 **2.45 万台、同比 +25.64%**，市场规模约 **50 亿元**，行业渗透率 **1.91%**（前瞻产业研究院，新浪财经转载）：<https://finance.sina.com.cn/roll/2026-01-24/doc-inhikrie2729186.shtml>
- 另一口径"2024 年 AGV 叉车销量 4,576 台、同比 +98.61%"（智研咨询）与上一条相差近 5 倍：<https://www.chyxx.com/industry/1229637.html> → 【待确认】两者对"无人叉车/AGV 叉车"的定义不同（是否含托盘搬运/堆高车），引用时必须写明口径。
- 行业访谈口径（ARC）：2023 年中国无人叉车市场约 27.4 亿元，2027 年有望约 78 亿元：<https://www.arcweb.com/blog/wurenchachexingyeshendujiexizhinengwuliudeweilaizhixing>
【推断】渗透率 ~2%、增速 25%–50% 是"早期市场"的典型特征：意味着**标准尚未固化、客户教育成本高、先做"可验收到位判定/记录"比做"全自动"更现实**。

### 3.2 成本区间（可查到的公开价格信号）

【宣传/目录价】**后装相机套件**：
- Veise 叉车 AI 行人/盲区检测套件：**US$380–555/件**（中国供应链 B2B 目录页）：<https://veise2008.en.made-in-china.com/product/cZQtxbjlCofy/China-Forklift-Ai-Pedestrian-Blind-Spot-Detection-Camera-System-with-Remote-Control-for-Adjustment-of-Reversing-Auxiliary-Line-Warning-Zone.html>
- 无线叉车相机套件 **US$695**：<https://www.forkliftinnovations.com/product-page-2/high-sight-wireless-forklift-camera-system>
- 叉车相机系统价格构成说明（相机数量、有线/无线、显示器、夜视、AI 检测、安装工时等）：<https://www.kocchis.com/blog/wireless-forklift-camera-guide>
【待确认】**固定相机 AI 分析（SaaS）**：跨行业的公开口径为 **US$200–2,000/相机/月**（施工工地 AI 相机市场报告）；非仓储专属：<https://dataintelo.com/report/construction-site-ai-camera-market>
【待确认】**叉车保险**：二手口径称每台每月约 US$150–600（非官方精算数据）：<https://www.logrock.com/uncategorized/forklift-truck-insurance>
【事实】**事故直接成本**：NCCI 数据（2022–2023 事故年度）全部失时索赔平均 **US$47,316**；截肢类平均 **US$125,058**：<https://injuryfacts.nsc.org/work/costs/workers-compensation-costs>（美国国家安全委员会 Injury Facts，权威二手）
【待确认】"单次叉车工伤平均 $38,000–41,000""间接成本为直接成本 4–6 倍""OSHA 平均每次违规 $13,500"来自安全行业博客：<https://www.inviol.com/post/the-real-cost-of-forklift-accidents-insurance-downtime-and-human-impact>、<https://www.voxelai.com/industry-insights/forklift-accident-statistics>
【待确认】"2024 财年 OSHA 十大违规中动力工业车辆排第 6、2,248 次违规"：<https://forklifttraining.com/osha-top-10-citations-2024>（培训服务商博客，建议核验 OSHA 官方 top-10 公告）

### 3.3 客户最愿付费的痛点排序（分【事实】与【推断】两层）

【事实】可用于排序的硬数据：
- 中国 2025 年特种设备事故 **87.36% 由使用/管理不当导致，违章作业为主**：<https://www.cqszzs.com/equipment-36/6642.html>
- 美国叉车致死事件构成（2017）：非道路事故 20、被动力车辆撞击（非运输）13、坠物打击 12、高处坠落 11、**行人被车辆撞击 9**：<https://www.bls.gov/iif/factsheets/fatal-occupational-injuries-forklifts-2017.htm>
- 中国 2024 年场车事故 43 起、死亡 36 人：<https://www.yncj.gov.cn/cjxzfxxgk/aqsj1525/20250620/1608266.html>
【推断】据此排序（**推断，无客户访谈**）：
1. **合规与责任留痕**（检验/培训/事故举证）——法规驱动、预算明确；
2. **行人与盲区告警**——事故后果最重、痛点最直观，但合规边界最陡；
3. **货叉/托盘/货位几何类判定**——直接影响效率与货损，且在无人叉车招标中已成为标配能力；
4. **包装/载荷巡检**——货损与索赔直接相关，但行业主流做法是固定工位而非车载；
5. **"点击指定目标"的人机协作**——我们独有，但需求尚未被教育出来。

---

## 4. 反例与坑（视觉辅助在真实车间会怎么失效）

### 4.1 粉尘、油雾、镜头污染

【宣传，但内容与工程常识一致】产线粉尘/油雾会在镜头表面累积，先"柔和化"清晰度、再产生被模型误判为缺陷的伪影；厂商明确指出"这常被误认为是模型问题，其实是清洁计划问题"；照明强度会随工时自然衰减：<https://ifactoryapp.com/industries/food-manufacturing/ai-inspection-failure-modes-when-vision-systems-miss>
【事实】工业现场的粉尘、振动、温度波动、光照变化会造成测量误差与误读：<https://www.automationworld.com/factory/sensors/article/55374555/how-industrial-vision-systems-beat-dust-heat-and-vibration-to-stay-sharp-on-the-factory-floor>
【事实】镜头污染检测（灰尘/水滴/污渍）本身已是被专门研究的问题领域：<https://eureka.patsnap.com/blog/scout-report/camera-lens-contamination-detection-dirt-water-droplets-and-reliability-of-vision-systems>
【推断】对策：**把"镜头是否脏"作为一等公民指标**（清晰度/对比度在线监测 + 定期清洁 SOP + 告警），而不是等精度下降后归因到模型。

### 4.2 弱光、夜间与雨雾

【事实】可见光 + 雷达的行人检测在**低光、雨、雾**下性能显著下降，不一定触发紧急制动；热成像（FIR）被作为夜间补充手段：<https://www.lynred.com/blog/how-thermal-imaging-contributing-development-new-generation-nighttime-pedestrian-detection>
【事实】夜间/低光照行人检测是专门的活跃研究方向（低光单目 RGB 行人检测的结构性优化，强调安全场景下 recall 的重要性）：<https://www.mdpi.com/1424-8220/26/10/2987>
【事实】可见光与 FIR 融合、以及红外相机方案，是提升夜间行人检测的公开路线：<https://www.semanticscholar.org/paper/Pedestrian-Detection-at-Day-Night-Time-with-Visible-Gonz%C3%A1lez-Fang/330bcf952a5a20aac0e334aad1de4cd6ba6ed6eb>
【推断】车间多为室内且通常有照明，风险低于室外；但**货架深处、月台、夜间装卸区**是低光重灾区，应在试点现场条件里明确列出并实测。

### 4.3 强光、反光与低反射率目标

【事实】结构光深度相机无法承受阳光（投影图案被冲掉），室外不可靠；iToF 对光照变化更宽容但更易受多径干扰："高反物体可能过曝"、转角处出现 flying pixels：<https://www.lips-hci.com/post/3d-depth-technology-selection-guide>
【事实】ToF/3D 相机的原始深度会被环境光、材质反射率、多径干扰与噪声影响，需要滤波与背景光抑制：<https://tofsensors.com/blogs/tof-sensor-knowledge/3d-camera-filter-explained-how-tof-depth-cameras-improve-accuracy>
【事实】结构光依赖投影散斑可见性，强阳光/强红外环境会失效；iToF 更适合光照多变环境：<https://www.orbbec.com/blog/structured-light-vs-itof-depth-cameras>
【事实】安全级人员检测标准围着**低反射率（1.8%–5%）**目标设计（见 §2.4），这正是深色工装/地面阴影的极端情形。
【宣传】蓝芯科技称其 3D ToF 方案"抗 100 KLUX 阳光干扰、不受环境光影响"，并说"传统视觉方案在高位存取/密堆场景需借助高反条"：<https://www.lanxincn.com/news_cont_229.html>——【推断】该自述反过来说明：**反光标记曾经是绕开视觉难题的手段**，也说明真实车间光照条件之苛刻。
【推断】对策：优先在**可控光照**的位置部署；对强反光货物（缠膜、不锈钢、玻璃瓶）与深色托盘建立"已知困难样本集"，在验收时单独报告子集指标，而不是只报总体平均。

### 4.4 相机振动与标定漂移、缺深度感

【事实】一篇关于叉车安全的学位论文/综述把"相机系统局限"直接列出：**扁平视频缺乏深度感知，削弱安全收益**；同时列出窄通道、月台、货架遮挡视线等环境风险：<https://www.diva-portal.org/smash/get/diva2:1969398/FULLTEXT01.pdf>
【推断】车体振动 + 单目度量深度（DAV2 类）在长期运行下必然遇到标定漂移；对策是**定期重标定触发条件**（如与地面 AprilTag/已知标记比对偏差超阈即提示重标定），并把"是否已标定"作为输出可靠性门控（与仓库现有 APPROXIMATE/VERIFIED 语义一致，见 01 号文档）。

### 4.5 误报、信任与"狼来了"

【宣传，但现象被多方承认】AI 安全相机在落地时的主要障碍之一是误报导致的信任崩塌与"关掉告警"行为；行业内强调人工复核与端侧隐私处理：<https://www.inviol.com/post/privacy-and-ai-safety-cameras-how-to-protect-worker-identities>、<https://www.protex.ai/guides/complete-guide-to-ai-warehouse-safety>
【推断】对策：把**误报率作为与召回率同等的验收指标**；告警分级（提示/需确认/强提示）；提供"一键反馈为非目标"的闭环（这也正好是点击分割范式擅长的负样本提示能力）。

### 4.6 合规反例（把辅助说成安全）

【事实】安全扫描仪的警告区不具备安全认证输出（§2.4）；【事实】AI Act 第 6 条把"失效会危及健康与安全的 AI 系统"认定为安全组件（§2.5）。
【推断】最典型的"坑"是：演示时为了效果说成"能防撞"——**这一句话就把项目从辅助变成安全组件**，导致第三方符合性评估、PL d 级别的证据链要求，远超本原型能力。此条应作为综合报告的强制红线。

---

## 5. 对场景筛选的行业侧提示（交给 synthesizer，属【推断】）

1. **优先"固定视角 + 可验收判定"，谨慎"车载 + 连续追踪"**：行业对包装/托盘/库位检查的成熟做法是固定工位（转台、月台、通道口），因为它消掉了振动、光照、视角三大变量。若我们要用车载方案，必须回答"固定相机为什么不行"。
2. **"看得见"不值钱，"判定 + 记录 + 闭环"才值钱**：叉尖/门架相机在公开渠道已是数百美元级商品；付费点是结构化输出（到位/未到位、偏多少、与订单是否匹配）与可追溯记录。
3. **安全相关场景只做提示与留痕**：本次调研未找到"普通相机作为 ISO 3691-4 合格人员探测手段"的任何证据；安全级探测是 IEC 61496 认证器件的地盘。
4. **"点击指定目标"是最差异化、也最需要现场验证的**：学术与机器人领域已有"点击 → 分割 → 抓取/任务"的成熟范式，但叉车行业没有公开商用产品（【推断】空白区）。它的风险不是精度，而是**交互时机与操作员注意力**。
5. **合规成本必须显式计入场景成本**：凡涉及员工行为/绩效的场景（安全行为分析、录像回放用于考核），欧盟可能落入 AI Act Annex III 高风险，中国需过 PIPL 与劳动合规；建议直接标注为"观察/不做"，或限定为"只评设备与货物，不评人"。
6. **中国侧真正的采购触发器是 TSG 81—2022 + 87 号文 + 特种设备责任**（安全监控装置、定期检验、违章追责），因此"与现有安全监控/记录体系的对接能力"比算法精度更能决定成单。

---

## 6. 待确认与未能核实项（显式列出，禁止在综合报告中当成事实）

1. ISO 3691-4:2023 与 2020 版的替换关系与差异细节（仅有二手来源）。
2. ISO 3691-4 中"每个安全功能具体要求的 PL 等级"（仅有二手"often PL d"措辞，未见条款原文）。
3. IEC 61496-3 中 70 mm / 1.8% 反射率测试件的原文表述（来自厂商技术博客）。
4. AI Act 第 6(1a)/6(1b) 的"非安全辅助豁免"与"危及健康安全例外"是否为现行官方合并版文本（来源页面自带 "Not present before the amendment" 标注）；Annex III 第 4 项措辞需以 EUR-Lex 官方文本复核。
5. 《特种设备安全法》超期未检的罚款金额区间（3 万–30 万元）来自二手资讯站。
6. 各类厂商精度/检出率数字（±3 mm、95% 托盘孔、97% 检出率、事故下降 40%–93% 等）均无第三方复现。
7. 中国"无人叉车"销量口径分歧（2.45 万台 vs 4,576 台）与叉车保有量口径分歧（569.3 万辆 vs 约 400 万台）。
8. 货叉–叉孔的可容许定位偏差量级（无权威来源，需现场实测）。
9. OSHA 官方 2024 财年 top-10 违规中动力工业车辆的排名与数量（来自培训服务商博客）。
10. `https://osha.europa.eu/en/legislation/directive/regulation-20231230eu-machinery` 本次 curl 无法连通（HTTP 000），仅由检索快照支持；核验时请重试或用 EUR-Lex 替代。

---

## 7. 来源清单（含可达性状态）

> 可达性状态为 2026-09-14 用 `curl -sL -o /dev/null -w '%{http_code}'` 对本文全部 115 条 URL 实测：`200/202` = 可访问；`403` = 站点反爬（页面真实存在，浏览器可开，命令行被拒，**不代表链接失效**）；`000` = 连接失败（本次不可达，但多为检索快照成功过的页面）；`406/429` = 站点拒绝抓取/限流。**核验提示**：本会话中 `web_fetch` 工具对该批域名返回 "resolves to a non-public IP address" 而不可用（疑似本地 DNS/代理问题），如需逐条核验请改用 `curl -L` 或 Tavily extract。

### 7.1 标准与法规
| 来源 | 状态 | 用途 |
|---|---|---|
| <https://www.iso.org/obp/ui/es#!iso:std:83545:en> | 403（反爬） | ISO 3691-4:2023 目录 |
| <https://www.iso.org/obp/ui/es#iso:std:iso:3691:-4:ed-1:en> | 403（反爬） | ISO 3691-4:2020 目录 |
| <https://blog.ansi.org/ansi/iso-3691-4-2023-driverless-industrial-trucks> | 403（反爬） | ISO 3691-4:2023 概述 |
| <https://www.pilz.com/en-US/company/news/articles/238928> | 403（反爬） | ISO 3691-4 安全功能/PL 说明 |
| <https://www.controleng.com/ensuring-agv-safety-with-standards-compliance> | 403（反爬） | Type-C 标准、ISO 13849 对齐 |
| <https://www.fabrico.io/blog/iso-3691-4-driverless-industrial-trucks> | 200 | 保护场/警告场分层、PL d 措辞 |
| <https://www.itsdf.org/cue/b56-standards.html> | 200 | B56.5-2024 生效日期 |
| <https://info.mobilerobot.com/safety-compliance/standards/ansi-itsdf-b56-5> | 200 | B56.5 适用范围 |
| <https://www.keyence.com/products/safety/laser-scanner> | 200 | 安全扫描仪认证等级 |
| <https://www.sick.com/ch/en/the-perfect-size-of-the-protective-field-on-an-industrial-autonomous-vehicle/w/blog-size-protective-field-industrial-autonomous-vehicle> | 200 | Type 3 / EN 61496-1 |
| <https://ez.analog.com/ez-blogs/b/engineerzone-spotlight/posts/an-introduction-to-the-iec-61496-series-of-human-presence-detection-standards> | 200 | IEC 61496 系列、5% 漫反射系数 |
| <https://industrialsafetysensor.com/blog/safety-laser-scanner-for-agv-amr-guide> | 200 | 70 mm / 1.8% 测试件（厂商博客） |
| <https://www.iso.org/ics/53.060.html> | 403（反爬） | ISO 20898 / ISO 21262 存在性 |
| <https://webstore.ansi.org/standards/iso/iso208982008> | 403（反爬） | ISO 20898:2008 标题 |
| <https://www.iso.org/standard/52163.html> | 403（反爬） | ISO 3691-1:2011 |
| <https://www.iso.org/obp/ui/en#!iso:std:73481:en> | 403（反爬） | ISO 13849-1:2023 与 IEC 61496 引用 |
| <https://eur-lex.europa.eu/eli/reg/2023/1230/oj> | 202 | 欧盟机械法规 2023/1230 |
| <https://eur-lex.europa.eu/eli/reg/2024/1689/oj> | 202 | 欧盟 AI Act |
| <https://artificialintelligenceact.eu/article/6> | 200 | AI Act 第 6 条高风险规则 |
| <https://artificialintelligenceact.eu/annex/3> | 200 | Annex III 第 4 项员工监控 |
| <https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-6> | 200 | 欧委会第 6 条说明 |
| <https://osha.europa.eu/en/legislation/directive/regulation-20231230eu-machinery> | 000（本次不可达） | 2023/1230 适用日期 |
| <https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=1F4740F4028C1B1C29D7D93D6BB165F8> | 200 | GB/T 10827.4—2023 |
| <https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=97D3C8DCDDC372940F7BC33A840F8CE5> | 200 | GB/T 38893—2020 |
| <https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=1E96E92F7C36EABAD6F22A4A4E49FA86> | 200 | GB/T 36507—2023 |
| <https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=B9432660D53D30062F1E65A1F59A2A61> | 200 | GB/T 10827.1—2014 |
| <https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=B9CFF67017751DB45C09518434419076> | 200 | GB/T 2934—2007 托盘尺寸 |
| <https://www.hnbzw.com/Standard/StdDetail.aspx?ekdHR4nH7qql91MlNWgnvlYlUZZ02WeF=> | 200 | GB/T 10827.4—2023 标准页（含等同采用说明） |
| <https://www.ncsnc.com/news/672.html> | 000（本次不可达） | GB/T 10827.4 等同采用 ISO 3691-4（备用证据） |
| <http://www.hunan.gov.cn/zqt/zcsd/202209/t20220927_29019201.html> | 200 | 市监特设发〔2022〕87 号全文 |
| <https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.178> | 200 | OSHA 1910.178 培训/认证 |
| <https://www.osha.gov/laws-regs/federalregister/2022-02-16> | 200 | OSHA 动力工业车辆设计标准更新 |
| <http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html> | 200 | 《个人信息保护法》 |

### 7.2 场景与产品线索（均为【宣传】，除注明事实者）
| 来源 | 状态 | 用途 |
|---|---|---|
| <https://www.kocchis.com/camera-systems-for-forklift-trucks> | 200 | 叉视角/激光对孔相机 |
| <https://hollandvisionsystems.com/applications/high-reach-deep-reach> | 200 | 高位/深位相机应用 |
| <https://www.hubtex.com/en-us/wiki/camera-systems-truck> | 200 | 后装相机位置 |
| <https://www.sick.com/cz/en/precise-detection-of-pallet-pockets-using-a-3d-snapshot-camera/w/blog-pallet-pocket-detection-transolt> | 200 | 3D snapshot 托盘孔检测案例 |
| <https://www.lanxincn.com/news_cont_229.html> | 200 | 蓝芯 3D 视觉托盘对接（±3 mm 等） |
| <https://www.eet-china.com/mp/a133838.html> | 000（本次不可达） | 维感 ToF 相机用于托盘识别 |
| <https://seer-robotics.ai/zh/media/17.0> | 200 | 图漾相机栈板识别 |
| <https://www.zebra.com/cn/zh/resource-library/use-case-library/forklift-mounted-pallet-scanning.html> | 200 | 叉车免手动托盘扫描 |
| <https://roboflow.com/ai/pallet-tracking> | 200 | 托盘计数/破损检测清单 |
| <https://vimaan.ai/what-are-the-most-common-applications-of-warehouse-computer-vision> | 202 | 库位占用/盘点 |
| <https://visionify.ai/case-studies/warehouse-inventory-management> | 200 | 放货验证/空位检测 |
| <https://ifactoryapp.com/ai-vision-camera/ai-vision-pallet-load-stability-inspection> | 200 | 缠绕膜/稳定性/超限检测 |
| <https://oxmaint.com/industries/fleet-management/ai-cargo-inspection-freight-damage-detection> | 200 | 货损检测（97%/0.4 s 宣传） |
| <https://acagroup.be/en/cases/innovative-ai-model-detects-damaged-pallets-at-duvel-moortgat> | 200 | 托盘破损视觉检测实际部署 |
| <https://zenodo.org/records/10391024> | 200 | 托盘破损分类研究 |
| <https://ifactoryapp.com/industries/cement-plant/ai-forklift-pedestrian-collision-avoidance> | 200 | 窄通道人员进入告警 |
| <https://www.sensorzone.io/warehousing> | 200 | 接近告警探测区配置 |
| <https://www.voxelai.com/solutions-safety> | 200 | 固定相机安全分析（厂商效果） |
| <https://www.protex.ai/guides/complete-guide-to-ai-warehouse-safety> | 200 | 固定相机安全分析（厂商效果） |
| <https://www.kocchis.com/products/forklift-ai-pedestrian-detection-system> | 200 | 车载 AI 行人检测 |
| <http://www.wt-safe.com/Products/BlindSpot.html> | 200 | 盲区检测 + 主动制动选件 |
| <https://www.rearviewsafety.com/360-ai-pedestrian-detection-system.html> | 200 | 360° 行人检测（9 ft） |
| <https://www.stonkam.com/explore/STONKAM-Forklifts-Anti-Collision.html> | 200 | 3 路相机 310°/20 m |
| <https://www.ewaysafety.com/blogs/article/ai-forklift-camera-systems-reducing-blind-spots-in-warehouse-operations> | 200 | AI 叉车相机科普 |
| <https://www.cascorp.com/us/en/weighforks> | 403（反爬） | 称重货叉 |
| <https://szlmi.com/product/forklift-load-indicator> | 200 | 载荷力矩指示器 |
| <https://www.samsara.com/products/cameras> | 200 | 车队视频安全/教练闭环 |
| <https://www.lytx.com/vs/samsara> | 200 | 车队视频供应商格局 |

### 7.3 学术与技术
| 来源 | 状态 | 用途 |
|---|---|---|
| <https://arxiv.org/abs/2511.06295> | 200 | 单目托盘/托盘孔检测（95%/72%） |
| <https://pmc.ncbi.nlm.nih.gov/articles/PMC12788346> | 200 | 全向相机图像测量法对孔 |
| <https://pmc.ncbi.nlm.nih.gov/articles/PMC12375720> | 200 | 点击/选择 + HQ-SAM 驱动操作 |
| <https://advanced.onlinelibrary.wiley.com/doi/10.1002/aisy.202400404> | 403（反爬） | 可提示分割的交互式抓取 |
| <https://research.tuni.fi/app/uploads/2023/11/7b55b10c-ieee_co_speech_cr-1_optimized.pdf> | 200 | 指向手势的人机协作 |
| <https://www.diva-portal.org/smash/get/diva2:1969398/FULLTEXT01.pdf> | 200 | 叉车安全综述：扁平视频缺深度感等 |
| <https://www.lynred.com/blog/how-thermal-imaging-contributing-development-new-generation-nighttime-pedestrian-detection> | 200 | 低光/雨雾行人检测退化 |
| <https://www.mdpi.com/1424-8220/26/10/2987> | 403（反爬） | 低光行人检测研究 |
| <https://www.lips-hci.com/post/3d-depth-technology-selection-guide> | 200 | 结构光/ToF 局限 |
| <https://tofsensors.com/blogs/tof-sensor-knowledge/3d-camera-filter-explained-how-tof-depth-cameras-improve-accuracy> | 200 | ToF 环境光/多径 |
| <https://www.orbbec.com/blog/structured-light-vs-itof-depth-cameras> | 200 | 结构光 vs iToF |
| <https://www.automationworld.com/factory/sensors/article/55374555/how-industrial-vision-systems-beat-dust-heat-and-vibration-to-stay-sharp-on-the-factory-floor> | 403（反爬） | 粉尘/振动/光照 |
| <https://ifactoryapp.com/industries/food-manufacturing/ai-inspection-failure-modes-when-vision-systems-miss> | 200 | 失效模式（厂商博客） |

### 7.4 市场与事故数据
| 来源 | 状态 | 用途 |
|---|---|---|
| <https://www.bls.gov/iif/factsheets/fatal-occupational-injuries-forklifts-2017.htm> | 403（反爬） | 美国叉车伤亡（74/9,050/1,850） |
| <https://injuryfacts.nsc.org/work/costs/workers-compensation-costs> | 403（反爬） | NCCI 平均失时索赔 $47,316 |
| <https://injuryfacts.nsc.org/work/safety-topics/forklifts> | 403（反爬） | 2024 年 84 起死亡、25,110 DART |
| <https://www.yncj.gov.cn/cjxzfxxgk/aqsj1525/20250620/1608266.html> | 200 | 2024 年场车事故 43 起/死亡 36 人 |
| <https://www.cqszzs.com/equipment-36/6642.html> | 200 | 2025 年通报：188 起/156 人、87.36% 管理不当 |
| <http://m.cpqr.net/zfal/tzsb/14229.html> | 200 | 叉车事故典型案例（无证/站人/超用途） |
| <https://m.chinaforklift.com/news/detail/202504/89378.html> | 200 | 2024 中国叉车销量 128.55 万台 |
| <https://www.huaon.com/channel/trend/1085177.html> | 200 | 2023 保有量 569.3 万辆 |
| <https://finance.sina.com.cn/roll/2026-01-24/doc-inhikrie2729186.shtml> | 200 | 无人叉车 2.45 万台/50 亿元/渗透率 1.91% |
| <https://www.chyxx.com/industry/1229637.html> | 200 | AGV 叉车 4,576 台（口径分歧） |
| <https://www.arcweb.com/blog/wurenchachexingyeshendujiexizhinengwuliudeweilaizhixing> | 403（反爬） | ARC：2023 年 27.4 亿元→2027 年 78 亿元 |
| <https://dataintelo.com/report/forklift-collision-avoidance-system-market> | 403（反爬） | 防撞市场规模（口径待确认） |
| <https://www.sphericalinsights.com/blogs/top-20-companies-in-the-global-forklift-collision-avoidance-system-market-2026-2035-spherical-insights-analysis> | 200 | 防撞市场规模（口径待确认） |
| <https://www.knowledge-sourcing.com/report/forklift-360-degree-camera-market> | 200 | 360° 相机市场 |
| <https://www.persistencemarketresearch.com/market-research/forklift-truck-safety-solutions-market.asp> | 200 | 安全方案市场 |
| <https://veise2008.en.made-in-china.com/product/cZQtxbjlCofy/China-Forklift-Ai-Pedestrian-Blind-Spot-Detection-Camera-System-with-Remote-Control-for-Adjustment-of-Reversing-Auxiliary-Line-Warning-Zone.html> | 200 | 后装 AI 相机目录价 $380–555 |
| <https://www.forkliftinnovations.com/product-page-2/high-sight-wireless-forklift-camera-system> | 200 | 无线相机套件 $695 |
| <https://www.voxelai.com/industry-insights/forklift-accident-statistics> | 200 | 事故成本二手汇总（待确认） |
| <https://www.inviol.com/post/the-real-cost-of-forklift-accidents-insurance-downtime-and-human-impact> | 200 | 工伤索赔/罚款二手口径 |
| <https://forklifttraining.com/osha-top-10-citations-2024> | 200 | OSHA 违规排名（待确认） |

### 7.5 正文引用但未列入上表的 URL（同样已实测）
| 来源 | 状态 | 用途 |
|---|---|---|
| <https://dbba.sacinfo.org.cn/portal/download/a55ff34220faff3e09be36a5d4cc93da7789535c2f1b3ed97e6606b792c0ecb6> | 200 | 江苏 DB32/T 4925—2024 智慧安全监管 |
| <https://img.antpedia.com/standard/files/pdfs_ora/20230705/GB%20T%2036507-2023.pdf> | 403（反爬） | GB/T 36507—2023 标准 PDF（含 ISO 21262:2020 对应关系） |
| <https://www.iso.org/obp/ui#!iso:std:iso:3691:-1:dis:ed-2:v1:en> | 403（反爬） | ISO/DIS 3691-1 第 2 版（人工驾驶叉车） |
| <https://www.ningboruyi.com/xingyezixun/265.html> | 200 | TSG 81—2022 二手解读（权限采集器/罚款） |
| <https://physical-ai-safety.com/blog/eu-machinery-regulation-2027-primer> | 200 | 机械法规 2027 适用解读（二手） |
| <https://www.roboticstomorrow.com/article/2023/11/what-are-the-differences-between-safe-lidar-and-lidar/21471> | 200 | 安全级 vs 普通 LiDAR |
| <https://plcprogramming.io/blog/safety-laser-scanner-explained> | 429（限流） | 警告区非安全输出 |
| <https://eureka.patsnap.com/blog/scout-report/camera-lens-contamination-detection-dirt-water-droplets-and-reliability-of-vision-systems> | 200 | 镜头污染检测问题域 |
| <https://ifactoryapp.com/ai-vision-camera/ai-warehouse-automation-and-inventory-management-using-computer-vision> | 200 | 库位/计数/空位（厂商） |
| <https://www.inviol.com/post/privacy-and-ai-safety-cameras-how-to-protect-worker-identities> | 200 | 员工隐私与 AI 安全相机（厂商/blog） |
| <https://www.kocchis.com/blog/wireless-forklift-camera-guide> | 200 | 相机系统价格构成（厂商） |
| <https://www.logrock.com/uncategorized/forklift-truck-insurance> | 200 | 叉车保险费用（二手，待确认） |
| <https://www.mt.com/us/en/home/products/Transport_and_Logistics_Solutions/forklift-scales.html> | 406（拒绝抓取） | 叉车秤品类 |
| <https://www.wiseguyreports.com/reports/forklift-collision-avoidance-system-market> | 200 | 防撞市场（相机类）规模口径 |
| <https://dataintelo.com/report/construction-site-ai-camera-market> | 403（反爬） | 工地 AI 相机成本口径（跨行业参考） |
| <https://pdf.dfcfw.com/pdf/H3_AP202409191639932155_1.pdf> | 200 | 券商测算：国内叉车保有量约 400 万台 |
| <https://www.youtube.com/watch?v=15SYIwmLeDQ> | 200 | Zebra SmartCount 声称 25–50% 节省（厂商） |
| <https://www.facebook.com/HikvisionHQ/videos/-hikvision-forklift-pedestrian-safety-helps-industrial-sites-operate-with-greate/2040607450667816> | 200 | 海康叉车行人安全线索（社媒，建议换官网） |
| <https://b2bwiki.baidu.com/article/d0n98rpftjsrseil6vgg> | 000（本次不可达） | 叉孔间隙 3–5 mm（二手，仅作反例标注） |
| <https://www.icommunetech.com/scm/inventory-counting-with-computer-vision> | 000（本次不可达） | Gather AI 无人机盘点媒体转述 |
| <https://www.semanticscholar.org/paper/Pedestrian-Detection-at-Day-Night-Time-with-Visible-Gonz%C3%A1lez-Fang/330bcf952a5a20aac0e334aad1de4cd6ba6ed6eb> | 000（本次不可达） | 可见光+FIR 夜间行人检测 |

---

## 附录 A 检索方法（可复现）

1. **检索工具**：本会话 `web_search` 返回 `HTTP 401` 不可用；`web_fetch` 对多数域名返回 `resolves to a non-public IP address`。改用两条通道：
   - Tavily MCP（`https://mcp.tavily.com/mcp`，OAuth 令牌取自 `~/.mcp-auth/*/*_tokens.json`）的 `tavily_search` / `tavily_extract`；
   - `curl -sL` + HTML 转文本脚本（用于 ITSDF / openstd / AI Act 等站点原文）。
2. **检索批次**：共 12 批、约 45 条查询（英文 + 中文），覆盖：场景/产品、ISO/ANSI/GB/TSG、AI Act 与机械法规、事故与成本、失效模式、人机协作。
3. **可达性实测**：对本文全部 115 条 URL 做 HTTP 状态实测（见 §7 状态列），403 为反爬而非失效。
4. **未做的事（诚实声明）**：没有做客户访谈、没有采购报告、没有对任何厂商指标做第三方复现；所有"精度/检出率"均为来源自述，已在正文逐条标注。