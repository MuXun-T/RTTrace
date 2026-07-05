# 需求规格说明书（MVP）v1.3（回放/对比/复现对齐版）

- 项目名称：日志驱动内核任务监测与可视化工具
- 版本：v1.3
- 日期：2026-03-08
- 适用范围：MVP（离线优先，纳入在线输出与在线增量解析基础链路，并纳入回放/对比/复现闭环）

## 1. 文档目标

本文档定义 MVP 阶段可直接开发、测试与验收的需求基线，作为设计、开发、测试与评审依据。

本版基于以下输入进行审阅修订并形成统一需求口径：

1. 原始需求基线 `v1.0`。
2. 功能设计文档 `功能设计.docx`。
3. 上一版需求基线 `需求规格说明书_MVP_v1.2_审阅修订版.md`。

### 1.1 审查结论与本版修订原则

经审查，上一版已基本完成与设计稿的能力对齐，但仍存在以下需要在需求层明确固化的问题：

1. `v1.0` 中“在线流式监测链路不做”与设计稿中“支持在线输出与在线增量解析基础链路”存在边界冲突，需要统一。
2. 设计稿中存在部分偏实现细节的描述，需要在需求文档中收敛为“接口、契约、行为、验收”四类稳定要求，避免与详细设计混写。
3. 不可信窗口、导出快照一致性、输入形态一致性、`Trace` 导出边界等关键约束已被隐含使用，但尚未完全固化为需求级条目。
4. 设计稿提及的“日志回放 / 版本对比 / 参数对比 / 实验复现”需要以最小可落地定义纳入本阶段 `MVP P0` 验收，并明确不外扩为集中式在线平台、复杂规则引擎或运行时注入式重演。

本版据此执行以下修订原则：

1. 统一为“离线优先 + 在线基础链路纳入 MVP”，但不扩展为在线运维/监控平台。
2. 仅保留对接口、数据契约、验收口径有影响的设计内容，不将详细实现算法直接上升为需求。
3. 对所有结论型能力补足证据链、不可信标记和跨模块一致性要求。
4. 将“回放 / 对比 / 复现”纳入 `P0`，但限定为：回放是分析侧逐事件步进；对比是双运行基线同口径对比；复现是导出包/快照可重开并恢复分析上下文。
5. 强化需求可测性，使每个 `P0` 条目都能对应明确的测试或验收方法。

### 1.2 合同层级说明

为避免把当前阶段的必达合同、后续增强项和长期目标终态混写，本文件统一采用以下口径：

1. 未单独标注时，条款默认表示 `MVP P0` 必达合同，是当前开发、测试与验收的正式基线。
2. `MVP P1` 仅表示 `P0` 之上的增强方向，不作为 `P0` 是否达标的判定条件。
3. `目标架构终态` 仅用于约束长期演进方向，不得回写成当前已交付事实。
4. 需求文档只固化用户可观察、可验收的最小合同；字段表、线程模型和协议细节以下游设计文档为准。

## 2. 范围与边界

### 2.1 In Scope（MVP）

1. 多 RTOS 通用采集抽象层，首个适配平台固定为 UEOS。
2. 采集端结构化日志输出，支持文件、串口、网络三类输出通道。
3. 离线日志文件解析与在线流式输入的增量解析基础链路，并要求同一数据集在两种输入形态下输出统一数据口径。
4. 统一数据契约：全局头、事件头、Chunk 头、事件字典、兼容策略、证据引用与不可信窗口。
5. 采集端每核无锁环形缓冲、`reserve-write-commit` 写入机制。
6. 按事件/任务/核/对象维度的过滤、采样、开关控制与统计。
7. 完整性处理：CRC 校验、`seq` 连续性检查、截断恢复、缺口/不可信窗口标注。
8. 跨核稳定排序与基于同步锚点的时间对齐校准。
9. 状态重建与查询：`UnifiedEventStream`、`ExecSlice`、`TaskStateSeg`、`ResourceGraph`、`IRQInterval/IrqSpan`、时间索引与对象索引。
10. 指标统计、热点分析、轻规则诊断、阈值告警与证据链反查。
11. 桌面分析端多视图联动、`LOD` 展示、增量加载、书签与证据链跳转。
12. 日志回放：基于 `UnifiedEventStream`、`ExecSlice` 与 `AnalysisContext` 的分析侧逐事件播放、暂停、逐事件前进/后退与证据锚点定位，并要求时间线/事件表/指标同步联动。
13. 双运行基线对比：支持两份日志或两份导出包按 `baseline/candidate` 载入，对不同运行批次、不同版本、不同参数基线的指标、告警、热点与关键事件区间进行同口径差异对比；同库多窗口仅作为辅助视图。
14. 实验复现：导出包/快照可重新载入，恢复 `time_window / filter / selection / zoom_level` 与证据链锚点，并复现同一告警、同一统计结果和同一导出结论。
15. 全量导出与裁剪导出，导出格式覆盖 `CSV + JSON + Trace`，包含 `meta.json`、分层数据、分析上下文与 `manifest.json`。
16. Windows 与 Linux 双平台运行与结果一致性验收。

### 2.2 Out of Scope（本阶段不做）

1. 第二个 RTOS 适配。
2. 复杂规则引擎、自动根因推理、智能诊断编排。
3. 面向运营化的集中式在线监控平台能力，例如多用户协同、集中告警推送、远程多节点统一管控。
4. 面向目标系统的运行时注入式日志重演、调度执行重演与自动化实验脚本编排。

## 3. 总体目标与成功标准

1. 打通“采集 → 格式 → 解析/对齐 → 重建 → 统计/诊断 → 展示 → 导出”闭环。
2. 所有告警与诊断结论均可反查到事件证据链，并能定位到时间窗、对象与关键事件范围。
3. 同一输入在 Windows 与 Linux 上输出一致结果；同一输入多次解析排序结果稳定一致。
4. 对同一数据集，离线输入与在线增量输入在排序、重建、指标、告警与导出层保持统一口径，允许仅在展示层存在定义容差。
5. 支持对问题窗口进行分析侧逐事件回放，并保证时间线、事件表、指标与证据链锚点同步联动。
6. 支持两份日志或导出包作为 `baseline/candidate` 做同口径对比，并能输出可举证的差异结果。
7. 导出包/快照可重新打开并恢复分析上下文，复现同一告警、统计结果与导出结论。
8. 满足本文件第 7 章非功能门槛与第 8 章验收条件。

## 4. 关键角色

1. 内核开发人员：验证调度、同步、中断与实时性行为，比较不同版本/参数下的性能与行为差异，定位性能问题。
2. 测试人员：复现实验条件，回放问题窗口，执行双基线对比、一致性与性能验收。
3. 分析人员：统计指标、定位异常、对比差异、导出复盘数据与复现包并形成结论。

## 5. 功能需求（FR）

| ID | 需求项 | 优先级 | 需求说明 | 验收标准 |
|---|---|---|---|---|
| FR-COL-01 | 采集点覆盖 | P0 | 覆盖任务生命周期、调度切换、同步原语、中断、完整性/异常事件，形成后续重建所需最小事件闭环。 | `TC-COL-01~03`、`TC-COL-07` 通过；关键字段齐全并可重建因果链。 |
| FR-COL-02 | 低侵入写入 | P0 | 采集路径采用每核无锁环形缓冲与 `reserve-write-commit`；关键路径不得阻塞，不得动态分配。 | `TC-COL-04~05` 通过；关键路径不读到半条记录。 |
| FR-COL-03 | 输出通道与滚动写入 | P0 | 支持文件落盘、串口、网络输出；支持分段/滚动写入与批量 `Flush`。 | `TC-COL-08` 通过；三类通道至少完成基础通路验证。 |
| FR-COL-04 | 过滤与采样 | P0 | 支持按事件、任务、核、对象过滤与采样，运行时生效并保留采样标记。 | `TC-COL-06` 通过；过滤/采样结果可追溯。 |
| FR-COL-05 | 缺口与完整性标记 | P0 | 缓冲不足、覆盖、采样丢弃等场景需累计 `lost/overflow` 并输出缺口语义。 | `TC-COL-07` 通过；解析端能识别不可信窗口。 |
| FR-FMT-01 | 统一记录模型 | P0 | 采用 `Record = Header + Payload`，支持 `payload_len` 跳读未知事件与字段。 | `TC-FMT-01`、`TC-FMT-04` 通过；未知事件/字段不导致崩溃。 |
| FR-FMT-02 | 全局头与事件字典 | P0 | 日志文件/流必须携带或可关联获取全局头与事件字典，明确字节序、时间单位、时钟源、版本与字典信息。 | `TC-FMT-02`、`TC-FMT-06` 通过；缺字典时可降级但需告警。 |
| FR-FMT-03 | `EventID` 与版本演进 | P0 | `event_id` 采用 `Domain + Type` 编码；事件与载荷版本可演进且旧解析器可降级解析。 | `TC-FMT-02~03` 通过；兼容规则可执行。 |
| FR-FMT-04 | Chunk 封装与 CRC | P0 | 采用分块封装，支持块级 CRC 与损坏/截断识别。 | `TC-FMT-05` 通过；损坏区间被标注且不参与强推断。 |
| FR-PRS-01 | 离线多文件解析 | P0 | 支持滚动分片日志、多文件批量解析并输出统一全局事件流。 | `TC-PRS-01` 通过；文件边界不影响结果。 |
| FR-PRS-02 | 在线增量解析 | P0 | 支持持续到来的 Chunk 增量解码、索引更新与查询；与离线解析保持统一数据口径。 | `TC-PRS-02` 通过；在线输入输出同口径数据基线。 |
| FR-PRS-03 | 跨核排序与同步锚点校准 | P0 | 稳定排序规则为 `timestamp → core_id → seq`；具备 `SYNC/TS_CALIB` 等同步锚点校准能力。 | `TC-PRS-04`、`TC-PRS-06` 通过；校准失败区间标注不可信。 |
| FR-PRS-04 | 状态重建 | P0 | 重建 `ExecSlice`、`TaskStateSeg`、资源持有/等待关系、中断区间与嵌套关系。 | `TC-PRS-05`、`TC-PRS-07`、`TC-PRS-08` 通过。 |
| FR-PRS-05 | 索引与查询 | P0 | 提供时间索引、对象索引、多级摘要与查询接口，支撑“从图到日志 / 从日志到图”定位。 | `TC-PRS-09` 通过；按任务/核/类型/对象查询可用。 |
| FR-MET-01 | 核心指标统计 | P0 | 统计利用率、响应时间、抖动、就绪等待、阻塞、上下文切换、中断占用/延迟等指标。 | `TC-MET-01~04`、`TC-MET-06` 通过；指标口径与重建结果一致。 |
| FR-MET-02 | 分布、热点与对比统计 | P0 | 支持分位统计、`TopN` 热点、任务/核/时间窗对比、核间负载不均衡分析，并支持不同运行批次、不同版本、不同参数基线的同口径对比统计。 | `TC-MET-05`、`TC-MET-07`、`TC-CMP-02` 通过；热点/分布/对比结果正确。 |
| FR-MET-03 | 轻规则诊断 | P0 | 支持截止期违约、潜在优先级反转窗口、异常 IRQ 挤压等轻规则诊断，不引入复杂规则引擎。 | `TC-MET-07`、`TC-MET-08` 通过；诊断结果附证据链。 |
| FR-ALT-01 | 阈值告警与反查 | P0 | 告警条目必须附 `EvidenceRef`，支持跳转到时间窗、对象片段与关键事件范围。 | `TC-MET-06`、`TC-VIZ-05` 通过。 |
| FR-ALT-02 | 不可信窗口传播 | P0 | 缺口、损坏、截断、校准失败、未闭合关系等不可信状态需传播到重建、统计、告警、可视化上下文与导出结果。 | `TC-PRS-03`、`TC-EXP-03` 通过。 |
| FR-VIZ-01 | 多视图联动 | P0 | 提供时间线/甘特图、指标曲线、任务状态视图、事件表、资源争用视图、告警面板六类核心视图并联动。 | `TC-VIZ-01~05` 通过；任一视图操作能同步联动。 |
| FR-VIZ-02 | LOD 与增量加载 | P0 | 支持时间线 `LOD`、曲线按桶聚合、事件表分页、资源视图按需展开。 | `TC-VIZ-06` 通过；大日志下交互可用。 |
| FR-VIZ-03 | 书签与证据链跳转 | P0 | 支持对当前 `time_window + filter + selection` 建立书签，并支持从书签与告警直接回到证据链；书签为轻量导航能力，不替代复现快照。 | `TC-VIZ-07` 通过；书签回跳上下文一致。 |
| FR-VIZ-04 | 日志回放 | P0 | 基于 `UnifiedEventStream`、`ExecSlice` 与 `AnalysisContext` 支持播放、暂停、逐事件前进/后退与证据锚点定位；回放过程中时间线、事件表与指标必须同步联动。 | `TC-RPY-01~03` 通过；回放步进正确且联动一致。 |
| FR-CMP-01 | 双运行基线载入 | P0 | 支持两份日志或两份导出包按 `baseline/candidate` 载入，统一时间窗、过滤条件、统计口径与证据锚点上下文。 | `TC-CMP-01` 通过；双基线载入与范围对齐正确。 |
| FR-CMP-02 | 差异对比与举证 | P0 | 支持对指标、告警、热点与关键事件区间进行同口径差异对比，并可对差异结果下钻到证据链；同库多窗口对比仅作为辅助分析能力。 | `TC-CMP-02~03` 通过；差异结果可举证且口径一致。 |
| FR-EXP-01 | 全量导出 | P0 | 导出 `Event/Rebuild/Result` 三层数据及元信息，输出格式覆盖 `CSV + JSON + Trace`，且与界面口径一致。 | `TC-EXP-01`、`TC-EXP-04` 通过；结果与界面一致且可校验。 |
| FR-EXP-02 | 裁剪导出 | P0 | 按当前 `time_window/filter` 导出最小数据包，并保留必要上下文用于语义还原。 | `TC-EXP-02` 通过；边界一致并保留必要上下文。 |
| FR-EXP-03 | 快照一致性与清单校验 | P0 | 导出时锁定分析上下文快照，输出 `manifest` 行数/条数/校验和与版本信息，并记录导出来源、运行基线标识、版本标识与实验参数。 | `TC-EXP-03` 通过；导出过程中用户操作不影响结果一致性。 |
| FR-EXP-04 | 复现包导入与上下文恢复 | P0 | 导出包/快照必须可作为复现输入重新加载，恢复 `time_window / filter / selection / zoom_level` 与证据链锚点，并复现同一告警、同一统计结果和同一导出结论。 | `TC-RPR-01~02` 通过；重开后上下文与结果一致。 |

## 6. 接口与数据契约要求

### 6.1 采集端接口（C/C++）

| 接口 | 说明 |
|---|---|
| `trace_Init` | 初始化采集模块、缓冲区、输出通道与默认配置。 |
| `trace_Enable` | 开启采集，支持全局或按核开启。 |
| `trace_Disable` | 关闭采集并保留或清理缓冲区内容。 |
| `trace_RecordEvent` | 通用事件记录接口。 |
| `trace_RecordTaskSwitch` | 任务切换/抢占专用记录接口。 |
| `trace_RecordTaskState` | 任务状态变迁记录接口。 |
| `trace_RecordIRQ` | 中断进入/退出/嵌套记录接口。 |
| `trace_RecordSync` | 同步原语记录接口。 |
| `trace_SetFilter` | 设置按事件/任务/核/对象维度的过滤规则。 |
| `trace_SetSampling` | 设置比例、窗口或阈值采样策略。 |
| `trace_FlushBuffer` | 批量刷出缓冲区记录到输出通道。 |
| `trace_GetStats` | 查询丢包、溢出、写入量、输出量、延迟等统计。 |

补充要求：

1. `trace_Record*` 关键路径只允许执行快照、过滤/采样和 `reserve-write-commit` 写入，不得执行同步 `I/O`、动态分配、retry/backoff 或等待 flush 完成。
2. `auto_flush` 只能异步提出 flush 请求，不得把 drain/write 回流到当前记录调用线程。
3. `trace_FlushBuffer(handle, mode, deadline_ms)` 的 `deadline_ms` 只约束调用方等待刷出完成的最长时间，不改变 record path 的实时边界。
4. `trace_GetStats()` 必须返回并发可读的聚合快照，用于表达 `lost / overflow / io_backpressure / written / flushed / latency` 等统计。

### 6.2 格式规范接口

| 接口 | 说明 |
|---|---|
| `spec_GetFormatVersion` | 返回文件/事件头版本号。 |
| `spec_GetHeaderLayout` | 返回头字段布局、偏移、长度与对齐。 |
| `spec_GetEventIdRule` | 返回 `EventID` 编码规则。 |
| `spec_GetDictionary(dict_ver)` | 获取指定版本事件字典。 |
| `spec_EncodeRecord(event_id, payload)` | 按规范编码记录。 |
| `spec_DecodeRecord(bytes)` | 按规范解码记录。 |
| `spec_VerifyChunk(chunk)` | 校验 Chunk 完整性并返回缺口信息。 |
| `spec_CompatPolicy` | 返回兼容策略、跳读策略与降级规则。 |

### 6.3 解析与重建接口

| 接口 | 说明 |
|---|---|
| `prs_Init(cfg, dict)` | 初始化解析器与字典。 |
| `prs_FeedChunk(core_id, bytes)` | 输入离线/在线统一 Chunk。 |
| `prs_Finalize()` | 完成输入并输出缺口/损坏统计。 |
| `aln_Calibrate(sync_events)` | 基于同步锚点计算核间偏移/漂移。 |
| `aln_Merge(per_core_events)` | 跨核合并与稳定排序，生成 `UnifiedEventStream`。 |
| `rb_Rebuild(unified_events)` | 状态重建，生成执行片段、状态段、资源关系与中断区间。 |
| `idx_Build(results)` | 构建时间索引、对象索引与多级摘要。 |
| `qry_Query(filter, range)` | 查询指定时间窗与条件下的事件/片段/对象结果。 |

### 6.4 统计与诊断接口

| 接口 | 说明 |
|---|---|
| `metric_Init(cfg)` | 初始化指标、阈值与规则配置。 |
| `metric_Ingest(rebuild_bundle)` | 接入完整 `RebuildBundle`，作为统计层统一开发、联调与验收入口。 |
| `metric_Compute(t_begin, t_end, filter)` | 生成序列、分布、`TopN` 等指标。 |
| `metric_Compare(baseline, candidate, scope)` | 生成双运行基线的同口径差异指标、热点与关键区间结果。 |
| `alert_Evaluate(t_begin, t_end, filter)` | 执行阈值与轻规则检测并输出告警。 |
| `diag_Generate(t_begin, t_end, alert_list)` | 生成诊断结论并挂接证据链。 |
| `diag_Backtrace(diag_id / alert_id)` | 返回异常反查所需证据引用。 |
| `metric_Export(format, scope, t_begin, t_end)` | 导出指标、热点、告警结果。 |

补充要求：

1. 后续实现、联调、验收和测试记录统一以 `metric_Ingest(rebuild_bundle)` 为标准入口，不再接受旧的 `event_stream + state_seq + exec_slices` 私有组合接口。
2. `rebuild_bundle` 至少包含统一事件流、任务状态段、执行片段、资源图、中断区间、不可信窗口和能力位说明，确保统计层不依赖隐式侧信道。

### 6.5 可视化、回放、对比与导出接口

> 说明：需求文档统一采用 `viz_InitWorkspace` 命名；设计文档中的 `iz_InitWorkspace` 视为笔误。

#### 6.5.1 工作区与单基线分析接口

| 接口 | 说明 |
|---|---|
| `viz_InitWorkspace` | 初始化工作区与默认视图布局。 |
| `viz_LoadDataset` | 加载统一数据基线。 |
| `viz_LoadDatasetAsync` | 提交 staged-load 作业并立即返回首屏 preview/readiness 合同。 |
| `viz_SetContext` | 设置 `time_window/filter/zoom_level` 等联动上下文。 |
| `viz_ApplySelection` | 应用任务、核、资源、告警或事件选择。 |
| `viz_QueryTimelineLOD` | 查询指定缩放层级的时间线数据。 |
| `viz_QueryTaskStates` | 使用任务状态视图专用查询对象查询独立任务状态视图。 |
| `viz_QueryMetricSeries` | 查询指标曲线序列。 |
| `viz_QueryEventTable` | 分页查询事件表。 |
| `viz_QueryResourceGraph` | 查询资源争用关系与等待链。 |
| `viz_QueryAlerts` | 查询告警列表与证据锚点。 |

补充要求：

1. `viz_LoadDatasetAsync` 必须先交付 `LoadPreview / ReadinessState`，显式区分 `preview_ready` 与 `query_ready`。
2. 在 `preview_ready` 阶段允许只暴露首屏摘要，不得把 preview 能力伪装成全量 bundle 已可查询。
3. `LoadPreview.task_state_preview` 只表示首屏任务状态摘要能力，不等价于正式 `viz_QueryTaskStates` 返回值。

#### 6.5.2 回放接口组

| 接口 | 说明 |
|---|---|
| `replay_Init(unified_stream, exec_slices, context)` | 基于统一事件流、执行片段与分析上下文初始化回放会话。 |
| `replay_Play(rate)` | 按指定速率启动分析侧播放。 |
| `replay_Pause()` | 暂停当前回放会话。 |
| `replay_StepForward(count)` | 按事件粒度向前步进。 |
| `replay_StepBackward(count)` | 按事件粒度向后步进。 |
| `replay_Seek(target)` | 按时间戳、事件 ID、`EvidenceRef` 或锚点定位回放光标。 |
| `replay_GetState()` | 返回最小回放状态，用于联动、证据定位与复核。 |

补充要求：

1. 回放正式返回合同只要求最小 `PlaybackState`，不得要求调用方依赖内部运行态实现细节。

#### 6.5.3 对比接口组

| 接口 | 说明 |
|---|---|
| `cmp_LoadPair(baseline_source, candidate_source)` | 载入双运行基线，支持日志文件或导出包作为输入。 |
| `cmp_SetScope(time_window, filter, dimensions)` | 设置双基线对比范围、过滤条件与比较维度。 |
| `cmp_QueryDiffSummary()` | 查询指标、告警、热点与关键区间的差异摘要。 |
| `cmp_QueryDiffDetail(target)` | 查询指定差异项的明细、证据锚点与关联事件区间。 |
| `cmp_LinkAuxView(dataset_id, context)` | 建立同库多窗口辅助对比视图，但不替代双基线结论。 |

补充要求：

1. `CompareScope.dimensions` 必须真实驱动正式差异产物，不能只作为输入枚举保存。
2. 未请求的维度不得在 `DiffSummary` 或 `DiffDetail` 中生成对应差异结果。

#### 6.5.4 导出与复现接口组

| 接口 | 说明 |
|---|---|
| `export_Full` | 提交全量导出并返回可追踪的导出作业标识。 |
| `export_Clipped` | 提交裁剪导出并返回可追踪的导出作业标识。 |
| `export_WritePackage` | 将导出结果写为多文件数据包，并返回包路径、条目数和快照标识。 |
| `repro_OpenPackage(path_or_stream)` | 打开复现包并返回包路径及其元信息/清单。 |
| `repro_RestoreContext(snapshot_id)` | 恢复快照中的 `AnalysisContext`、证据锚点与比较角色。 |
| `repro_LoadAsDataset(role)` | 将复现包按单基线或 `baseline/candidate` 角色载入工作区。 |

### 6.6 数据契约（必选）

#### 6.6.1 全局头字段（必选）

`magic endian time_unit clock_source format_ver dict_ver producer_ver run_id?`

要求：

1. 全局头必须出现在日志文件头或流元信息起始位置。
2. 解析器必须依据全局头识别字节序、时间单位、时钟源与格式版本。
3. 事件字典可随文件携带（例如 `DICT` 块）或以伴随文件形式提供，但必须能与 `dict_ver` 对齐。
4. 若 trace 提供 `run_id`，系统必须在解析、导出与复现链路中保持其可追踪性。

当 `format_ver >= 2` 时，分段 trace 还必须提供段级最小元信息：

`segment_seq prev_segment_seq dict_ref_algo dict_ref_checksum`

要求：

1. 每个分段都必须能恢复前驱关系。
2. 每个分段都必须能独立校验其字典引用。

#### 6.6.2 事件头最小字段（必选）

`ver flags core_id event_id seq timestamp payload_len`

补充要求：

1. `event_id` 采用 `Domain + Type` 编码。
2. `timestamp` 必须单调递增，并明确单位与时钟源。
3. 遇到未知事件或未知字段时，解析端必须依赖 `payload_len` 跳读。
4. 可选字段允许包含 `magic`、`header_crc`，但不得替代块级 CRC。

#### 6.6.3 Chunk 头字段（必选）

`chunk_start_ts chunk_end_ts core_mask/core_id record_count chunk_crc`

补充要求：

1. 推荐可选字段：`seq_range dict_ver`。
2. Chunk 必须是最小可校验/可截断恢复单元。
3. CRC 失败、截断或 `seq` 跳变必须生成缺口或损坏标记。

#### 6.6.4 证据引用结构（必选）

`EvidenceRef = { ref_type, ref_key, t_begin, t_end }`

要求：

1. `ref_type` 至少支持 `event`、`slice`、`index` 三类。
2. 告警、诊断、书签、回放锚点与导出元信息必须可关联 `EvidenceRef`。
3. `ref_key` 必须能够回溯到统一事件流、重建片段或索引对象中的稳定标识。

#### 6.6.5 不可信窗口结构（必选）

`UntrustedWindow = { window_id, source, scope, t_begin, t_end, reason_code, severity }`

要求：

1. `source` 至少支持 `crc_fail`、`seq_gap`、`overflow`、`truncate`、`dict_mismatch`、`align_fail`、`open_relation`。
2. `scope` 至少支持 `event`、`rebuild`、`metric`、`alert`、`viz`、`export`。
3. 不可信窗口必须能够被解析、重建、统计、可视化与导出链路共享，禁止各模块各自定义不兼容标记。
4. 裁剪导出时必须保留与导出窗口相交的 `UntrustedWindow` 信息。

#### 6.6.6 MVP P0 事件字段冻结备注（必选）

`TASK_READY`、`TASK_BLOCK`、`TASK_WAKEUP`、`CTX_SWITCH`、`IRQ_ENTER`、`IRQ_EXIT`、`LOSS`、`OVERFLOW` 为当前 `MVP P0` 的关键冻结事件。

| 事件 | 冻结最小字段 | 要求 |
|---|---|---|
| `TASK_READY` | `task_id, prio, core_hint?, reason` | 字段名、必填/可选、类型、语义和兼容策略以详细设计附表为准 |
| `TASK_BLOCK` | `task_id, wait_obj_id?, reason, owner_task_id?` | `collector / codec / parser / test` 不得再派生私有变体 |
| `TASK_WAKEUP` | `task_id, wake_src, obj_id?` | 缺失可选字段时按降级路径工作，不得补伪值 |
| `CTX_SWITCH` | `core_id, prev_task_id, next_task_id, reason` | `core_id` 需与事件头一致，切换语义不得重释 |
| `IRQ_ENTER / IRQ_EXIT` | `irq_id, core_id, nesting_depth` | 中断嵌套深度口径冻结，跨平台保持一致 |
| `LOSS / OVERFLOW` | `core_id, lost_count/overflow_count, reason` | 缺口/溢出口径冻结，并向不可信窗口传播 |

补充要求：

1. 新增字段只能以后向兼容方式追加为可选字段，不得改变既有字段含义。
2. 详细设计中的字段冻结附表是 `collector / codec / parser` 当前实现与联调的权威基线。

#### 6.6.7 导出包结构（必选）

1. `meta.json`
2. `event/`（`JSONL` 或 `Trace`）
3. `rebuild/`（`CSV/JSON`）
4. `result/`（`CSV/JSON`）
5. `context/`（`analysis_context.json`、`anchors.json`，用于复现与复核）
6. `manifest.json`（文件清单、行数/条数、校验和、版本信息）

补充要求：

1. `Trace` 导出必须来源于统一 `UnifiedEventStream`，不得与界面口径脱节。
2. `Trace` 导出内容必须可与 `manifest.json` 和 `EvidenceRef.ref_key` 建立回链关系。
3. 导出包必须允许独立脱离界面进行校验、二次分析与重新导入复现。
4. 复现包必须保留恢复分析上下文与证据锚点所需的最小信息集合。

#### 6.6.8 导出元信息要求（必选）

`meta.json` 至少包含以下字段：

`time_unit clock_source align_policy dict_ver parser_ver export_scope export_time time_window filter selection zoom_level snapshot_id run_id run_batch_id version_id experiment_params analysis_context evidence_anchor compare_role export_source`

裁剪导出还必须记录：

`context_padding_rule untrusted_windows`

要求：

1. `snapshot_id` 必须标识一次不可变导出快照。
2. `align_policy` 必须声明是否采用同步锚点校准及其降级策略。
3. `untrusted_windows` 必须与 `UntrustedWindow` 结构保持一致口径。
4. `run_id / run_batch_id / version_id / experiment_params` 必须支撑运行批次、版本与参数基线的可追踪性。
5. `compare_role` 取值至少支持 `baseline`、`candidate`、`single`；来自双基线工作区的导出必须保留角色信息。
6. `analysis_context`、`evidence_anchor` 与 `export_source` 必须支撑复现包重新载入、上下文恢复与导出来源追踪。

#### 6.6.9 分析上下文结构（必选）

`AnalysisContext = { time_window, filter, selection, zoom_level, focused_view, evidence_anchor, playback_cursor, compare_scope }`

要求：

1. 回放、导出、复现与可视化联动必须共享统一 `AnalysisContext` 口径。
2. `compare_scope` 需至少记录 `baseline_id`、`candidate_id`、比较维度与对齐时间窗。
3. 书签可只保存轻量上下文用于导航，但不得替代导出快照或复现包。
4. 复现包重新载入后必须能够恢复到可复核、可举证的分析状态。

#### 6.6.10 staged-load 就绪合同（必选）

`ReadinessState = { stage, source, preview, lod_ready, view_ready, fallback_reason }`

`LoadPreview = { dataset_id, header, time_window, chunk_count, record_count, core_ids, lod0_buckets, source, stage, readiness, task_state_preview? }`

`TaskStatePreview = { time_window, lane_count, task_ids, state_totals, bucket_summary, readiness, trusted }`

要求：

1. `viz_LoadDatasetAsync` 必须先返回 `LoadPreview / ReadinessState`，不得把“预览可见”和“全量可查询”折叠为单一布尔态。
2. `stage` 至少应支持 `queued / preview_ready / parse_rebuild / query_ready`，并在同一 load 作业生命周期内单调推进。
3. `view_ready` 至少应覆盖 `timeline / task_states / event_table / metric_series / resource_graph / alerts` 六类视图。
4. `preview_ready` 阶段允许 `timeline` 消费 `LOD0` 摘要，允许通过 `task_state_preview` 暴露任务状态首屏摘要；但正式任务状态查询和事件表查询若未 ready，必须显式返回 `pending` 或 `NOT_READY`。
5. `fallback_reason` 必须用于标记偏离正式主路径的查询/加载回退，不得静默降级。

#### 6.6.11 `CompareScope` 最小合同（必选）

要求：

1. `CompareScope` 必须至少表达 `baseline_id / candidate_id / aligned_time_window / filter / dimensions`，并作为对比、导出和复现共享的正式范围对象。
2. `dimensions` 至少应支持 `metric / alert / hotspot / interval / task / core / resource / irq`。
3. `dimensions` 与正式差异产物的最小映射如下：

| 维度 | 差异摘要 | 差异明细 |
|---|---|---|
| `metric` | `metric_changes` | `dimension=metric` |
| `alert` | `alert_changes` | `dimension=alert` |
| `hotspot` | `hotspot_changes` | `dimension=hotspot` |
| `interval` | `interval_changes` | `dimension=interval` |
| `task` | `task_changes` | `dimension=task` |
| `core` | `core_changes` | `dimension=core` |
| `resource` | `resource_changes` | `dimension=resource` |
| `irq` | `irq_changes` | `dimension=irq` |

4. 未请求的维度不得生成对应 summary/detail 结果。

#### 6.6.12 `RebuildBundle` 最小合同（必选）

要求：

1. 系统必须通过 `RebuildBundle` 对外表达数据集身份，供查询、导出、复现和证据回链共享使用。
2. 当导出、复现或 lineage 校验需要时，`RebuildBundle` 必须能够携带头级追踪信息与分段 provenance 信息。
3. 查询加速索引允许作为 `RebuildBundle` 的可选附件存在，但不得被要求为统计入口的强制必备字段。

#### 6.6.13 `PlaybackState` 最小合同（必选）

`PlaybackState` 对外最少需要表达 `replay_id / status / rate / cursor / anchor_ref / visible_window / linked_views / trusted`。

要求：

1. 回放接口必须返回足以支持联动、证据定位和复核的最小状态。
2. 调用方不得被要求依赖任何内部运行态实现细节才能消费回放接口。

#### 6.6.14 `TaskStateQuery` 专用 DTO 约束（必选）

要求：

1. `viz_QueryTaskStates` 必须使用任务状态视图专用查询对象，不得复用时间线私有查询参数。
2. 数据集定位参数不得定义为 `TaskStateQuery` 的正式字段。
3. 任务状态查询对象不得混入渲染态或仅供工作区内部使用的临时参数。

#### 6.6.15 `IndexBundle` 最小能力（必选）

要求：

1. 系统必须支持时间、任务、核、事件类型四类索引及其摘要能力，用于可视化、跳转和分页查询。
2. `IndexBundle` 的定位是查询加速派生结果，不得替代统一事件流或完整重建结果包。

#### 6.6.16 `Alert / Diagnosis.support_level` 要求（必选）

要求：

1. 告警和诊断结果必须同时暴露严重度/结论与支持度。
2. `support_level` 至少应区分 `exact`、`degraded`、`unsupported` 三类对外语义。
3. `support_level` 不得被 `confidence`、备注文本或私有状态字段替代。

#### 6.6.17 导出与复现最小返回能力（必选）

要求：

1. `export_Full` 与 `export_Clipped` 必须返回可追踪的 `job_id`。
2. `export_WritePackage` 必须返回 `package_path`、`entry_count` 与 `snapshot_id` 三类最小结果信息。
3. `repro_OpenPackage` 必须返回 `package_path` 以及与复现包对应的 `meta / manifest` 信息。
4. 上述返回能力属于接口级合同，不得要求调用方依赖 `ExportJob / PackageResult / ReproSession` 之类共享正式 DTO 名称。

## 7. 非功能需求（NFR）

| ID | 指标 | 目标 |
|---|---|---|
| NFR-PERF-01 | 采集吞吐 | `>= 1,000,000 events/s` |
| NFR-PERF-02 | 采集路径开销 | 关键路径不阻塞、不做动态分配、不暴露半条记录 |
| NFR-PERF-03 | `1GB` 日志首屏就绪 | `< 10s` |
| NFR-PERF-04 | 内存占用 | `< 4GB` |
| NFR-PERF-05 | `10` 万级图块时间线渲染 | `>= 30 FPS` |
| NFR-PERF-06 | 导出 SLA | `1GB` 全量导出 `<= 60s`；`10` 分钟窗口裁剪导出 `<= 15s` |
| NFR-STAB-01 | 长稳测试 | `24h` 无崩溃、无不可恢复错误 |
| NFR-CONS-01 | 跨平台一致性 | 同一日志在 Windows/Linux 解析结果一致（容差内） |
| NFR-CONS-02 | 稳定排序一致性 | 同一输入多次解析的排序与导出结果一致 |
| NFR-CONS-03 | 输入形态一致性 | 同一数据集的离线与在线增量结果一致（容差内） |
| NFR-CONS-04 | 复现一致性 | 同一导出包/快照重开后上下文、告警、统计与导出结论一致 |
| NFR-CONS-05 | 对比口径一致性 | 双运行基线差异结果与各自单独统计结果保持同口径 |
| NFR-COMPAT-01 | 兼容性 | 未知事件/字段可跳读降级；字典缺失时基础字段可解析并告警 |

性能验收默认测试环境基线：`Intel Core i7-12700H / 32GB RAM / SSD / Windows 11 或 Ubuntu 22.04`，并使用设计文档定义的 `1GB` 级日志、复杂争用场景与高频采集场景。

## 8. 测试与验收

### 8.1 功能验收用例基线

统计链路相关测试统一通过 `metric_Ingest(rebuild_bundle)` 接入数据，测试记录中不得再引用废弃的三参数统计入口。

| ID | 测试项目 | 验收要点 |
|---|---|---|
| TC-COL-01 | 上下文切换与调度事件覆盖 | `CTX_SWITCH/TASK_DISPATCH` 关键字段完整，序号递增。 |
| TC-COL-02 | 阻塞/唤醒闭环覆盖 | `TASK_BLOCK/TASK_WAKEUP` 与 `SYNC_*` 组合后可还原等待关系。 |
| TC-COL-03 | 中断进出与嵌套覆盖 | `IRQ_ENTER/EXIT` 成对，嵌套深度正确。 |
| TC-COL-04 | `reserve-write-commit` 正确性 | 刷出端不可见半条记录。 |
| TC-COL-05 | 环回与覆盖处理 | 回绕正确，越界受控，必要时写缺口语义。 |
| TC-COL-06 | 过滤与采样生效 | 被禁用事件不落盘，采样命中可标识。 |
| TC-COL-07 | 缓冲不足与缺口标记 | `lost/overflow` 统计与后续不可信窗口一致。 |
| TC-COL-08 | 文件/串口/网络输出 | 三类通道至少完成一条基础输出通路。 |
| TC-FMT-01 | Header 对齐与必填字段 | 必选字段齐全且可解析。 |
| TC-FMT-02 | `EventID` 域+类型编码 | 可按域分类统计，未知域可降级。 |
| TC-FMT-03 | 版本演进与跳读兼容 | 旧解析器可跳过新增字段继续工作。 |
| TC-FMT-04 | `TLV/位图` 扩展载荷 | 未知 `TLV` 可跳读且不影响后续记录。 |
| TC-FMT-05 | Chunk CRC 校验 | 损坏区间被识别并标记。 |
| TC-FMT-06 | 全局头/字典缺失与版本不匹配 | 基础字段可解析，语义降级并产生告警。 |
| TC-PRS-01 | 离线多文件解析 | 文件边界不影响全局事件流与重建结果。 |
| TC-PRS-02 | 在线增量解析与滚动索引 | 持续输入时增量解码与查询可用，且与离线结果同口径。 |
| TC-PRS-03 | CRC/序号连续性与缺口标注 | 缺口区间不输出错误状态推断。 |
| TC-PRS-04 | 跨核稳定排序 | 按 `timestamp → core_id → seq` 稳定排序。 |
| TC-PRS-05 | 状态轨迹与 `ExecSlice` 重建 | “运行—阻塞—唤醒—抢占”边界正确。 |
| TC-PRS-06 | 同步锚点校准 | `SYNC/TS_CALIB` 校准生效，失败区间标记不可信。 |
| TC-PRS-07 | 资源持有/等待关系重建 | `holder/waiter` 关系与等待链正确。 |
| TC-PRS-08 | 中断嵌套与延迟重建 | `IrqSpan`、嵌套层级与延迟计算正确。 |
| TC-PRS-09 | 索引与查询 | 可按时间窗、任务、核、类型、对象快速定位。 |
| TC-MET-01 | 核利用率计算 | 与执行片段总时长一致。 |
| TC-MET-02 | 响应时间与抖动 | 均值/最大值/分位数正确。 |
| TC-MET-03 | 就绪等待/阻塞拆分 | `ReadyWait` 与 `Blocked` 归因正确。 |
| TC-MET-04 | 上下文切换次数 | 按核与时间窗聚合正确。 |
| TC-MET-05 | 资源争用热点识别 | 热点对象 `TopN` 正确。 |
| TC-MET-06 | 阈值告警与异常反查 | 告警附时间窗与证据锚点。 |
| TC-MET-07 | 分布统计/负载不均衡/截止期违约 | 分位数、核间差异、截止期违约检测正确。 |
| TC-MET-08 | 优先级反转窗口/IRQ 挤压诊断 | 轻规则诊断结果可解释并可举证。 |
| TC-VIZ-01 | 时间线展示（按核/按任务） | 缩放、拖拽、切换正确。 |
| TC-VIZ-02 | 指标曲线与时间线联动 | 曲线峰值可跳到时间线窗口。 |
| TC-VIZ-03 | 事件表检索/过滤/跳转 | 点击事件行可定位到对应时间线位置。 |
| TC-VIZ-04 | 资源争用视图等待链 | 等待链与持有关系正确。 |
| TC-VIZ-05 | 告警面板联动举证 | 时间线、事件表、资源视图同步高亮。 |
| TC-VIZ-06 | `LOD` 与增量加载 | 大日志下分页、摘要、异步补齐生效。 |
| TC-VIZ-07 | 书签与上下文回跳 | `time_window/filter/selection` 恢复一致。 |
| TC-RPY-01 | 日志回放逐事件步进 | 播放、暂停、逐事件前进/后退正确，回放光标与事件顺序一致。 |
| TC-RPY-02 | 回放联动同步 | 回放过程中时间线、事件表与指标视图同步更新。 |
| TC-RPY-03 | 证据锚点定位回放 | 从告警/证据锚点进入回放后定位一致，可回到同一证据链落点。 |
| TC-CMP-01 | 双运行基线载入与范围对齐 | 两份日志或导出包可按 `baseline/candidate` 载入，`time_window/filter` 对齐正确。 |
| TC-CMP-02 | 指标/告警/热点差异对比 | 差异结果与两侧单独统计一致，并可下钻举证。 |
| TC-CMP-03 | 关键事件区间与辅助对比 | 关键区间差异可展示；同库多窗口辅助对比保持同口径。 |
| TC-EXP-01 | 全量导出一致性 | 导出结果与界面统计一致。 |
| TC-EXP-02 | 裁剪导出范围正确 | 边界一致并保留必要上下文。 |
| TC-EXP-03 | 导出快照一致性与 `manifest` 校验 | 导出期间用户操作不影响当前包内容；清单校验通过。 |
| TC-EXP-04 | `Trace` 导出与回链校验 | `Trace` 内容可由 `manifest` 与 `EvidenceRef` 回链到统一事件流。 |
| TC-RPR-01 | 复现包重开与上下文恢复 | 重新打开导出包后，`time_window/filter/selection/zoom_level` 与证据链落点恢复一致。 |
| TC-RPR-02 | 复现结果一致性 | 告警、统计结果与导出结论与原快照一致。 |

### 8.2 一致性验收

1. 同一日志在 Windows 与 Linux 上解析、排序、重建、统计与导出结果一致，允许仅在浮点展示值上存在定义容差。
2. 同一输入多次解析输出稳定一致，`timestamp → core_id → seq` 排序不可漂移。
3. 离线输入与在线增量输入在同一数据集下产生同口径结果。
4. 同一复现包/快照在重复重开后，分析上下文、告警、统计与导出结论一致。
5. 同一对 `baseline/candidate` 输入在重复载入后，对比差异结果保持稳定且与各自单独统计口径一致。

### 8.3 异常与鲁棒性验收

1. CRC 损坏、`seq` 跳变、截断块、字典缺失/版本不匹配、校准失败场景必须标注不可信区间并输出提示。
2. 缺口、不可信区间、未闭合等待链、异常 IRQ 栈不得生成错误状态推断或误告警。
3. `unknown event / unknown field` 场景必须跳读降级，不得崩溃。
4. 无同步锚点或锚点不足时，不得伪造全局绝对时间真值；系统仅可输出稳定排序结果并显式声明对齐降级状态。

### 8.4 联动、回放、对比与导出验收

1. 曲线峰值、告警条目、事件行、书签四类入口均可完成证据链跳转。
2. 回放必须支持播放、暂停、逐事件前进/后退与证据锚点定位，且时间线、事件表、指标同步更新。
3. 双基线工作区必须支持指标、告警、热点与关键事件区间差异展示，并可对差异结果下钻举证。
4. 导出包必须包含 `meta.json`、分层结果、分析上下文与 `manifest.json`，并可作为复现输入重新打开。
5. 裁剪导出必须保留与导出窗口相交的 `UntrustedWindow` 与必要上下文。

### 8.5 性能验收

1. 按第 7 章门槛达标并输出性能报告。
2. 性能报告必须说明测试环境、日志规模、事件密度、视图场景与导出口径。

## 9. 需求追踪矩阵

### 9.1 功能需求追踪矩阵

| 需求 ID | 设计文档章节 | 验收用例 |
|---|---|---|
| FR-COL-01 | `4.2.2`、`6.1.5.1`、`6.1.5.2` | `TC-COL-01~03`、`TC-COL-07` |
| FR-COL-02 | `4.2.2`、`6.1.4`、`6.1.5.3` | `TC-COL-04~05` |
| FR-COL-03 | `4.2.2`、`6.1.5.4` | `TC-COL-08` |
| FR-COL-04 | `4.2.2`、`6.1.4`、`6.1.5.3` | `TC-COL-06` |
| FR-COL-05 | `4.2.2`、`6.1.5.3`、`6.1.5.4` | `TC-COL-07` |
| FR-FMT-01 | `4.2.3`、`6.2.5.1`、`6.2.5.2` | `TC-FMT-01`、`TC-FMT-04` |
| FR-FMT-02 | `4.2.3`、`6.2.3`、`6.2.4`、`6.2.5.4` | `TC-FMT-02`、`TC-FMT-06` |
| FR-FMT-03 | `4.2.3`、`6.2.5.1`、`6.2.5.2` | `TC-FMT-02~03` |
| FR-FMT-04 | `4.2.3`、`6.2.3`、`6.2.5.4` | `TC-FMT-05` |
| FR-PRS-01 | `4.2.4`、`6.3.5.1` | `TC-PRS-01` |
| FR-PRS-02 | `4.2.4`、`6.3.2`、`6.3.5.1` | `TC-PRS-02` |
| FR-PRS-03 | `4.2.3`、`6.2.5.3`、`6.3.4`、`6.3.5.2` | `TC-PRS-04`、`TC-PRS-06` |
| FR-PRS-04 | `4.2.4`、`6.3.5.3`、`6.3.5.4` | `TC-PRS-05`、`TC-PRS-07`、`TC-PRS-08` |
| FR-PRS-05 | `4.2.4`、`6.3.4`、`6.3.5.5` | `TC-PRS-09` |
| FR-MET-01 | `4.2.5`、`6.4.5.1`、`6.4.5.3` | `TC-MET-01~04`、`TC-MET-06` |
| FR-MET-02 | `4.2.5`、`6.4.2`、`6.4.5.1`、`6.4.5.2` | `TC-MET-05`、`TC-MET-07`、`TC-CMP-02` |
| FR-MET-03 | `4.2.5`、`6.4.5.2`、`6.4.5.3`、`6.4.5.4` | `TC-MET-07`、`TC-MET-08` |
| FR-ALT-01 | `4.2.5`、`6.4.3`、`6.4.5.4` | `TC-MET-06`、`TC-VIZ-05` |
| FR-ALT-02 | `6.2.5.3`、`6.2.5.4`、`6.3.5.4`、`6.5.5.5` | `TC-PRS-03`、`TC-EXP-03` |
| FR-VIZ-01 | `4.2.6`、`6.5.5.2`、`6.5.5.3` | `TC-VIZ-01~05` |
| FR-VIZ-02 | `4.2.6`、`6.5.5.4` | `TC-VIZ-06` |
| FR-VIZ-03 | `1.1`、`4.2.6`、`6.5.5.3` | `TC-VIZ-05`、`TC-VIZ-07` |
| FR-VIZ-04 | `4.2.6`、`6.5.5.5` | `TC-RPY-01~03` |
| FR-CMP-01 | `3`、`4.2.1`、`4.2.5`、`6.4.2` | `TC-CMP-01` |
| FR-CMP-02 | `4.2.5`、`4.2.6`、`6.4.2`、`6.5.5.5` | `TC-CMP-02~03` |
| FR-EXP-01 | `4.2.6`、`6.5.5.5` | `TC-EXP-01`、`TC-EXP-04` |
| FR-EXP-02 | `4.2.6`、`6.5.5.5` | `TC-EXP-02` |
| FR-EXP-03 | `6.5.5.5` | `TC-EXP-03` |
| FR-EXP-04 | `3`、`4.2.1`、`4.2.6`、`6.5.5.5` | `TC-RPR-01~02` |

### 9.2 非功能需求追踪矩阵

| 需求 ID | 设计文档章节 | 验收方法 |
|---|---|---|
| NFR-PERF-01 | `7.3.2` | 高频采集吞吐测试报告 |
| NFR-PERF-02 | `6.1.5.3`、`7.3.2` | 关键路径开销与写入正确性测试 |
| NFR-PERF-03 | `7.3.2` | `1GB` 日志首屏加载测试 |
| NFR-PERF-04 | `7.3.2` | 内存占用测试 |
| NFR-PERF-05 | `6.5.5.4`、`7.3.2` | 时间线渲染性能测试 |
| NFR-PERF-06 | `7.3.2` | 导出性能测试 |
| NFR-STAB-01 | `7.3.3` | `24h` 长稳测试 |
| NFR-CONS-01 | `7.1`、`7.3.2` | Windows/Linux 一致性比对 |
| NFR-CONS-02 | `6.2.5.3`、`6.3.5.2` | 重复解析稳定性比对 |
| NFR-CONS-03 | `6.3.5.1`、`6.5.5.5`、`7.1` | 离线/在线同数据集结果一致性比对 |
| NFR-CONS-04 | `4.2.6`、`6.5.5.5`、`7.1` | 复现包重复重开一致性比对 |
| NFR-CONS-05 | `4.2.5`、`6.4.2`、`6.5.5.5` | 双基线与单基线统计口径一致性比对 |
| NFR-COMPAT-01 | `6.2.5.2`、`6.2.5.4` | 未知事件/字段/字典异常兼容测试 |

## 10. 里程碑与交付物

1. `M1`：需求基线冻结（本文件、事件字典草案、`metric_Ingest(rebuild_bundle)` 接口口径说明、P0 事件字段冻结附表、验收矩阵、回放/对比/复现修订清单）。
2. `M2`：采集与格式闭环（UEOS 采集库、文件/串口/网络输出、Chunk 输出、字典/全局头）。
3. `M3`：解析、对齐、重建、索引、指标、差异计算与告警。
4. `M4`：桌面分析界面、多视图联动、`LOD`、书签、回放与双基线对比界面。
5. `M5`：导出与复现能力、回放/对比/复现测试、性能测试、稳定性测试、发布包。

## 11. 假设、默认值与实现约束

### 11.1 假设与默认值

1. 首个适配平台固定为 UEOS。
2. MVP 输入以离线日志文件为主，同时纳入在线输出与在线增量解析基础链路。
3. 导出格式在 MVP 阶段 `CSV + JSON + Trace` 为必选。
4. 事件字典支持随文件携带或伴随文件模式，但 `dict_ver` 必须可追踪。
5. 设计文档中的 `iz_InitWorkspace` 在本需求基线中统一修正为 `viz_InitWorkspace`。
6. 无同步锚点或校准失败时，系统默认降级为稳定排序 + 不可信标注，不宣称获得跨核绝对时间真值。
7. `Trace` 导出在本版中要求“可校验、可回链、与统一事件流同口径”，但不绑定到特定外部生态格式名称。
8. 回放限定为分析侧数据回放，不要求将日志重新注入目标系统或重演调度执行。
9. 版本对比/参数对比以双运行基线为主能力，同库多窗口只作为辅助视图，不替代双基线对比。
10. 实验复现以“导出包/快照可重开并复核结果”为验收口径，不要求自动化实验脚本编排。
11. 统计层接口基线冻结为 `metric_Ingest(rebuild_bundle)`；关键 `P0` 事件字段以详细设计附表为准。

### 11.2 实现约束

1. 采集端使用 `C/C++`。
2. 分析端使用 `Python Qt`。
3. 采集端关键路径禁止动态分配与阻塞式 `I/O`。
4. 解析、重建、指标、导出链路必须共享统一数据契约与版本口径。
5. 不可信窗口、证据引用、导出元信息必须来自统一数据基线，禁止各子系统各自维护不一致副本。
6. 回放、对比与复现能力的实现范围限定在桌面分析端闭环内，不扩展为集中式在线平台。

## 12. 变更管理

1. 本版为 `v1.3`，属于对 `v1.2` 的修订对齐版。
2. `v1.3` 相对 `v1.2` 的主要变更包括：将日志回放、双运行基线对比、实验复现纳入 `MVP P0`；补充回放/对比/复现接口组；扩展导出包与 `meta.json` 契约；新增复现一致性与对比口径一致性要求；更新追踪矩阵、里程碑与验收用例。
3. 小改动升级副版本（`v1.4`、`v1.5` ...），大改动经评审后升级主版本（`v2.0`）。
4. 每次变更必须附变更说明、影响范围、回归测试清单与受影响需求 ID。
