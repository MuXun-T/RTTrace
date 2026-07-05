# 日志驱动内核任务监测与可视化工具详细设计说明书（MVP）

- 项目名称：日志驱动内核任务监测与可视化工具
- 文档类型：详细设计说明书
- 版本：v1.0
- 日期：2026-03-09
- 适用范围：MVP（离线优先，纳入在线输出与在线增量解析基础链路，覆盖回放 / 对比 / 导出 / 复现闭环）
- 设计基线：`需求规格说明书_MVP_v1.3_回放对比复现对齐版.md`、`概要设计说明书_MVP_v1.0.md`、`功能设计.docx`

## 1. 引言

### 1.1 编写目的

本文档位于软件设计 `V` 模型左侧的“详细设计”层，用于在概要设计约束下，把系统进一步细化为可直接编码实现、单元测试、集成测试和评审验收的模块设计、数据结构设计、接口设计、线程设计和异常处理设计。

本文档重点回答以下问题：

1. 采集端、格式端、解析端、统计端、桌面分析端分别由哪些内部子模块组成。
2. 模块之间通过哪些稳定接口和共享数据契约协同。
3. 关键流程如何落地，包括写缓冲、排序、对齐、重建、诊断、回放、对比、导出和复现。
4. 异常、缺口和不可信窗口如何在全链路传播。
5. 各详细设计对象如何映射到右侧测试活动。

### 1.2 适用范围

1. 目标端首个适配平台固定为 `UEOS`。
2. 采集端实现语言固定为 `C/C++`。
3. 分析端实现技术固定为 `Python Qt` 桌面应用。
4. 本版仅覆盖 `MVP` 阶段 `P0` 功能，不扩展到第二个 `RTOS`、集中式在线监控平台、复杂规则引擎或运行时注入式重演。

### 1.3 编写依据

1. `需求规格说明书_MVP_v1.3_回放对比复现对齐版.md`
2. `概要设计说明书_MVP_v1.0.md`
3. `功能设计.docx`

当三份文档口径不一致时，本详细设计按以下优先级收敛：

1. 需求规格说明书
2. 概要设计说明书
3. 功能设计文档

### 1.4 V 模型定位

本详细设计与 `V` 模型关系如下：

```text
需求分析 -------------------------------> 验收测试
   |                                        ^
   v                                        |
概要设计 -------------------------------> 系统测试
   |                                        ^
   v                                        |
详细设计 -------------------------------> 集成测试
   |                                        ^
   v                                        |
编码实现 -------------------------------> 单元测试
```

详细设计阶段的交付目标如下：

1. 向下为编码实现提供决策完备的模块边界、类/结构体、接口和线程模型。
2. 向右为集成测试提供链路级、接口级和异常降级级验证对象。
3. 为单元测试定义结构化测试对象、状态机边界和异常输入集合。

### 1.5 设计对象与接口标识规则

为消除章节重排导致的追踪漂移，本文件中的设计引用统一采用稳定标识：

1. 数据对象：`OBJ-<Name>`，例如 `OBJ-AnalysisContext`、`OBJ-CompareScope`。
2. 接口：`IF-<Name>`，例如 `IF-metric_Ingest`、`IF-viz_QueryEventTable`。
3. 流程：`FLOW-<Name>`，例如 `FLOW-replay`、`FLOW-export`。
4. 规则：`RULE-<Name>`，例如 `RULE-stable_sort`、`RULE-export_padding`。
5. 后续需求追踪、测试映射和评审记录优先引用上述稳定标识，不再依赖章节号。

本轮评审收敛优先引用以下稳定标识：

| 类别 | 标识 | 设计含义 |
|---|---|---|
| 数据对象 | `OBJ-IndexBundle` | 查询加速派生索引结果与摘要合同 |
| 数据对象 | `OBJ-RebuildBundle` | 解析 / 重建阶段对外唯一完整结果包 |
| 数据对象 | `OBJ-CompareScope` | 对比、导出、复现共享的正式比较范围 |
| 数据对象 | `OBJ-AnalysisContext` | 可持久化上下文与临时展示态边界 |
| 数据对象 | `OBJ-TaskStatePreview` | 首屏任务状态摘要预览合同 |
| 数据对象 | `OBJ-TaskStateViewModel` | 任务状态视图独立核心模型 |
| 数据对象 | `OBJ-EventCursor`、`OBJ-EventPage` | 事件表稳定排序与分页契约 |
| 数据对象 | `OBJ-PlaybackState`、`OBJ-TaskStateQuery`、`OBJ-DiffSummary`、`OBJ-DiffDetail` | 回放、任务状态查询与对比最小 DTO 契约 |
| 接口 | `IF-metric_Ingest` | 指标层仅接收完整 `RebuildBundle` |
| 接口 | `IF-cmp_SetScope` | 比较范围正式化与归一化入口 |
| 接口 | `IF-viz_QueryTaskStates`、`IF-viz_QueryEventTable` | 任务状态视图和事件表独立查询契约 |
| 规则 / 流程 | `RULE-stable_sort`、`RULE-context_persist`、`RULE-segment_rollover`、`FLOW-compare`、`FLOW-export`、`FLOW-repro` | 排序、持久化、滚动写入与闭环流程的稳定引用 |

### 1.6 合同层级说明

为避免把 `P0` 必达合同、`P1` 增强合同和长期目标终态混写，本文件统一采用以下层级约定：

| 层级 | 含义 | 使用规则 |
|---|---|---|
| `MVP P0 必达合同` | 当前阶段必须开发、测试、验收闭环的正式对象、接口和协议 | 未单独标注时，本文件条款默认按该层执行 |
| `MVP P1 增强合同` | 在 `P0` 正式合同之上继续增强的能力或更优主路径 | 仅在显式标注时出现，不作为 `P0` 是否达标的判定条件 |
| `目标架构终态` | 长期希望收敛到的理想结构或更高阶能力 | 仅用于约束演进方向，不得回写成当前已交付事实 |

补充约束：

1. 需求规格说明书负责验收口径，概要设计负责架构角色与数据流，本文件负责字段、协议、线程与异常边界的权威定义。
2. 若某能力已存在 preview / staged / fallback 路径，必须显式写清“当前 `P0` 正式合同”与“后续增强方向”的边界，禁止用单一布尔态混写。
3. 历史评审说明、实现迁移注记和 compatibility 说明不得覆盖正式对象定义。

## 2. 设计输入、约束与默认值

### 2.1 范围约束

1. 输入数据源固定为操作系统运行过程中产生的日志 / 追踪事件流。
2. 系统以日志为唯一分析数据源，所有统计、诊断、展示、回放、对比和导出均不得脱离统一事件流口径。
3. 在线能力限定为在线输出和在线增量解析基础链路，不建设在线平台控制面。
4. 回放限定为分析侧数据回放，不将日志重新注入目标系统。
5. 对比能力以双运行基线为主能力；同库多窗口仅为辅助分析视图。
6. 复现能力以导出包 / 快照可重开、可恢复上下文、可复核结论为验收口径。

### 2.2 非功能约束

1. 采集端关键路径不得阻塞、不得动态分配、不得暴露半条记录。
2. 同一日志在 Windows / Linux 上的解析、重建、统计和导出结果必须一致。
3. 同一数据集的离线输入和在线增量输入必须保持统一口径。
4. `Trace` 导出必须来源于 `UnifiedEventStream`，但不绑定外部生态格式名称。
5. `UntrustedWindow`、`EvidenceRef`、`AnalysisContext` 与导出元信息必须共享统一数据基线。

### 2.3 本文默认值

1. 时间戳主单位默认采用纳秒或系统定义的单调时间单位，由 `GlobalHeader.time_unit` 显式声明。
2. 排序规则默认固定为 `timestamp -> core_id -> seq`。
3. 无同步锚点或校准失败时默认降级为稳定排序 + 不可信标记。
4. 事件字典允许内嵌于日志或随包提供伴随文件，但必须以 `dict_ver` 对齐。
5. 书签仅用于轻量导航；不可替代快照和复现包。

## 3. 系统详细设计总览

### 3.1 逻辑分层

系统按“采集域 + 分析域”两大运行边界设计。

#### 3.1.1 目标端采集域

1. **Hook / Trace Adapter 层**：部署于调度器、同步原语、中断入口等关键路径，负责就地快照。
2. **Record Builder 层**：完成事件头和载荷封装。
3. **Per-CPU RingBuffer 层**：负责 `reserve-write-commit` 写入协议。
4. **Flush / Channel 层**：负责文件、串口、网络三类输出。

#### 3.1.2 桌面分析域

1. **Input / Spec 层**：负责日志接入、字典绑定、校验和兼容降级。
2. **Parse / Rebuild 层**：负责解码、排序、对齐、状态重建和索引构建。
3. **Metric / Alert / Compare 层**：负责指标、热点、告警、诊断和双基线差异计算。
4. **Workspace / ViewModel 层**：负责 `AnalysisContext`、多视图联动、回放、书签、导出和复现。
5. **Qt UI 层**：负责视图展示、信号槽和交互命令分发。

### 3.2 推荐代码包结构

```text
collector/
  include/
  core/
  channel/
  hook/
spec/
  dictionary/
  codec/
  compat/
parser/
  input/
  align/
  rebuild/
  index/
metric/
  aggregate/
  alert/
  compare/
desktop/
  app/
  services/
  models/
  widgets/
  jobs/
```

### 3.3 进程与线程模型

#### 3.3.1 目标端采集线程模型

1. **业务 / 内核上下文**：触发 `trace_Record*` 接口，仅做快照和写缓冲。
2. **Per-CPU Flush Worker**：周期性或按阈值搬运缓冲数据到输出通道。
3. **Channel Writer**：对文件 / 串口 / 网络分别封装实际写出，允许阻塞，但只能运行在非关键路径。
4. **Stats Aggregator**：合并多核统计数据，供 `trace_GetStats` 查询。

#### 3.3.2 桌面分析线程模型

1. **UI 主线程**：维护窗口生命周期、`AnalysisContext` 提交、信号槽和渲染。
2. **Input Worker**：负责文件读取、流接收和 Chunk 预扫描；本地 trace/package 路径允许在 `viz_LoadDatasetAsync()` 提交期同步完成轻量 `input_prescan`，但不得等待全量事件表物化。
3. **Parse / Rebuild Worker Pool**：负责解码、校验、排序、重建和索引构建；`load_dataset` 作业的后台阶段固定落入 `parse_rebuild` lane。
4. **Query Worker Pool**：负责时间线、任务状态、事件表、资源图等查询；至少提供 `query_timeline`、`query_task_states`、`query_event_page` 三类 job kind。
5. **Export Worker**：独占一个后台任务，用于快照冻结、导出和清单写出。
6. **Replay Clock**：使用 `QTimer` 驱动回放节拍；需要后台数据拉取时由 Query Worker 执行。
7. **BackgroundJobManager**：统一管理 `input_prescan / parse_rebuild / query_* / export_*` lane、阶段推进和过期状态抑制；任一后台作业都必须显式暴露 `stage + readiness`。

### 3.4 关键链路总流程

```text
采集点触发
  -> Filter/Sampling
  -> Record Builder
  -> Per-CPU RingBuffer
  -> Flush/Channel Output
  -> Input Adapter
  -> Chunk Verify + Decode
  -> Alignment + Stable Merge
  -> UnifiedEventStream
  -> Rebuild + Index
  -> Metric / Alert / Diagnosis / Compare
  -> Workspace Context / ViewModel
  -> Replay / Export / Reproduce
```

### 3.5 公共错误码与结果封装

分析端统一采用以下逻辑返回封装：

`Result<T> = { code, message, data, warnings, untrusted_windows }`

公共错误码定义如下：

| 错误码 | 含义 | 处理原则 |
|---|---|---|
| `OK` | 成功 | 正常返回 |
| `INVALID_ARG` | 参数非法 | 直接拒绝本次调用 |
| `NOT_READY` | 依赖前置状态未完成 | 返回可重试状态 |
| `NO_DICT` | 字典缺失 | 基础字段解析 + 语义降级 |
| `CRC_FAIL` | Chunk 校验失败 | 生成不可信窗口并跳过损坏段 |
| `SEQ_GAP` | 序号缺口 | 生成缺口与不可信窗口 |
| `ALIGN_DEGRADED` | 对齐降级 | 仍返回稳定排序结果 |
| `IO_ERROR` | 输入输出异常 | 限制影响范围到当前任务 |
| `UNSUPPORTED_VERSION` | 版本不支持 | 返回失败并附兼容建议 |
| `CANCELLED` | 后台任务取消 | 保留已完成快照，不污染主状态 |

## 4. 核心共享数据结构设计

### 4.1 全局头 `GlobalHeader`

`GlobalHeader = { magic, endian, time_unit, clock_source, format_ver, dict_ver, producer_ver, run_id? }`

| 字段 | 类型 | 说明 |
|---|---|---|
| `magic` | `u32` | 文件 / 流魔数 |
| `endian` | `enum` | 字节序 |
| `time_unit` | `enum` | 时间单位 |
| `clock_source` | `enum` | 时钟源 |
| `format_ver` | `u16` | 格式版本 |
| `dict_ver` | `u16` | 事件字典版本 |
| `producer_ver` | `string` | 采集端产出版本 |
| `run_id?` | `string` | 可选运行追踪标识，用于解析、导出、复现链路的 lineage 对齐 |

补充设计：

1. `GlobalHeader` 保持最小文件/流级公共字段，不直接承载 segment chain。
2. `run_id?` 为可选头级追踪字段；存在时必须随解析、导出、复现链路透传，但不得替代 `SegmentMeta` 所表达的 segment chain 信息。
3. 当 `format_ver >= 2` 时，每个分段的 `GlobalHeader` 后必须紧跟一个固定宽度 `SegmentMeta`。

### 4.1.1 分段元信息 `SegmentMeta`

`SegmentMeta = { segment_seq, prev_segment_seq, dict_ver, dict_ref_algo, dict_ref_checksum }`

| 字段 | 类型 | 说明 |
|---|---|---|
| `segment_seq` | `u32` | 当前段序号，首段为 `1` |
| `prev_segment_seq` | `u32` | 前驱段序号，首段为 `0` |
| `dict_ver` | `u16` | 当前段对应字典版本 |
| `dict_ref_algo` | `enum` | 字典引用校验算法 |
| `dict_ref_checksum` | `u32` | 字典引用校验值 |

设计要求：

1. `SegmentMeta` 为段级最小连续性对象，必须在每段固定写出。
2. parser 必须兼容“无 `SegmentMeta` 的 v1”与“有 `SegmentMeta` 的 v2”。

### 4.2 事件头 `EventHeader`

`EventHeader = { ver, flags, core_id, event_id, seq, timestamp, payload_len }`

补充设计：

1. `flags` 至少包含 `sampled`、`irq_ctx`、`loss_following`、`dict_inline`、`reserved` 位。
2. `timestamp` 保留原始时间戳和对齐后时间戳两个视角；原始记录仅存原始值，对齐值在分析端附加生成。
3. `payload_len` 是未知事件 / 字段跳读的唯一边界依据。

### 4.3 Chunk 头 `ChunkHeader`

`ChunkHeader = { chunk_start_ts, chunk_end_ts, core_mask/core_id, record_count, seq_range, dict_ver, chunk_crc }`

设计要求：

1. `Chunk` 为最小可校验、可裁剪、可恢复单元。
2. `seq_range` 缺失时允许降级，但 `record_count` 和 `chunk_crc` 仍必须有效。
3. `dict_ver` 与文件级 `dict_ver` 不一致时，解析器生成 `dict_mismatch` 不可信窗口。

### 4.4 事件字典 `EventDictionary`

`EventDictionary = { dict_ver, domain_defs, event_defs, payload_defs, enum_defs }`

其中：

1. `domain_defs` 定义 `Domain -> 语义域`，建议固定为任务 / 调度、同步、中断、完整性、扩展控制等。
2. `event_defs` 定义 `event_id -> event_name / payload_schema / compat_rule`。
3. `payload_defs` 定义字段名、类型、单位、是否可选、默认值。
4. `enum_defs` 定义 `reason_code`、`result`、`state` 等枚举含义。

### 4.5 统一事件与重建对象

#### 4.5.1 统一事件流条目 `UnifiedEvent`

`UnifiedEvent = { event_uid, core_id, seq, timestamp_raw, timestamp_aligned, event_id, task_id?, obj_id?, irq_id?, job_id?, instance_id?, payload, sort_key, trust_tags, chunk_id, ref_key }`

设计说明：

1. `event_uid` 为分析侧稳定主键，建议生成规则：`evt:<dataset_id>:<core_id>:<seq>`。
2. `sort_key = (timestamp_aligned_or_raw, core_id, seq)`。
3. `trust_tags` 用于挂接与事件相交的不可信窗口标识。
4. `ref_key` 供 `EvidenceRef` 和导出回链使用。
5. `job_id / instance_id` 为实例级语义可选字段；`MVP` 不强制所有事件都携带，但涉及响应时间、抖动、截止期违约的精确闭环时优先消费该字段。

#### 4.5.2 执行片段 `ExecSlice`

`ExecSlice = { slice_id, task_id, core_id, job_id?, instance_id?, t_begin, t_end, start_event, end_event, preempted_by, run_reason, trusted }`

#### 4.5.3 任务状态段 `TaskStateSeg`

`TaskStateSeg = { seg_id, task_id, state, job_id?, instance_id?, t_begin, t_end, cause_event, related_obj, trusted }`

#### 4.5.4 资源图 `ResourceGraph`

`ResourceGraph = { nodes, hold_edges, wait_edges, hotspot_stats }`

1. `nodes` 表示资源对象和任务对象。
2. `hold_edges` 表示持有关系。
3. `wait_edges` 表示等待关系。
4. 允许为每条边附带 `evidence_ref` 和 `trusted` 标记。

#### 4.5.5 中断区间 `IrqSpan`

`IrqSpan = { irq_span_id, irq_id, core_id, nesting_depth, t_begin, t_end, delayed_task, trusted }`

#### 4.5.6 索引结果包 `IndexBundle`（OBJ-IndexBundle）

`IndexBundle = { time_index, task_index, core_index, event_type_index, summary }`

| 字段 | 类型 / 角色 | 说明 |
|---|---|---|
| `time_index` | 时间桶索引 | 按时间桶聚合的事件 / 状态摘要，支撑时间线首屏、跳转与局部加载 |
| `task_index` | 任务倒排索引 | `task_id -> event_uid[]` 或等价稳定引用集合 |
| `core_index` | 核倒排索引 | `core_id -> event_uid[]` 或等价稳定引用集合 |
| `event_type_index` | 事件类型倒排索引 | `event_name/type -> event_uid[]` 或等价稳定引用集合 |
| `summary` | 摘要对象 | 最小包含 `event_count / task_count / core_count / time_origin / time_end / bucket_size / bucket_count` |

设计说明：

1. `IndexBundle` 是 `idx_Build()` 产出的正式索引结果，服务于可视化跳转、分页查询与首屏摘要。
2. `IndexBundle` 可作为 `RebuildBundle.index_bundle?` 的可选派生附件复用，但不是 `metric_Ingest` 的最小核心输入。
3. 旧 `resource_index / irq_index / time_summary` 不再属于正式 `IndexBundle` 口径，只允许保留在历史兼容或私有实现语境中。

#### 4.5.7 重建结果包 `RebuildBundle`（OBJ-RebuildBundle）

`RebuildBundle = { bundle_id, dataset_id, event_stream, task_states, exec_slices, resource_graph, irq_spans, untrusted_windows, rebuild_rev, capability_flags, segment_metas, header?, index_bundle? }`

| 字段 | 类型 / 角色 | 说明 |
|---|---|---|
| `bundle_id` | 正式字段 | 当前重建结果包的稳定标识 |
| `dataset_id` | 正式字段 | 数据集身份标识，供查询、导出、复现和证据回链复用 |
| `event_stream` | 正式字段 | 全局稳定排序后的统一事件流 |
| `task_states` | 正式字段 | 任务状态段集合 |
| `exec_slices` | 正式字段 | 执行片段集合 |
| `resource_graph` | 正式字段 | 资源持有 / 等待关系图 |
| `irq_spans` | 正式字段 | 中断区间集合 |
| `untrusted_windows` | 正式字段 | 与当前 bundle 共享口径的不可信窗口 |
| `rebuild_rev` | 正式字段 | 重建修订号 |
| `capability_flags` | 正式字段 | 能力位集合，用于声明实例级语义、对齐能力和闭合程度 |
| `segment_metas` | provenance 附属字段 | 分段链路与字典引用的 provenance 信息 |
| `header?` | 可选追踪字段 | 头级 lineage 信息，用于解析、导出、复现校验与追踪 |
| `index_bundle?` | 派生附件 | 查询加速索引附件，可按需附着或缓存 |

设计说明：

1. `RebuildBundle` 是解析/重建阶段对外暴露的完整结果包，供 `metric_Ingest`、导出、复现和联动查询复用。
2. `dataset_id` 是正式数据集身份字段；`task_states / exec_slices / resource_graph / irq_spans / untrusted_windows` 必须与 `event_stream` 保持同一 `dataset_id`、同一排序基线和同一可信度口径。
3. `header?` 用于保留 `GlobalHeader` 级追踪信息，支撑导出包、复现包与 parser/export 链路的 lineage 校验。
4. `segment_metas` 用于表达 segment chain provenance，是正式可观察附属字段，但不替代核心分析对象。
5. `index_bundle?` 是派生 / 缓存附件，不得写成 `metric_Ingest` 的强制必备输入。
6. `capability_flags` 用于声明实例级语义、对齐能力、资源闭合能力等可用特性，避免上层误用弱语义数据。
7. `capability_flags` 建议固定包含 `job_semantics`、`instance_semantics`、`align_calibrated`、`resource_closed`、`irq_closed` 等能力位，供指标层、导出层和复现校验共享解释。

### 4.6 指标、告警与诊断对象

#### 4.6.1 指标结果 `MetricResult`

`MetricResult = { metric_id, scope, series, distribution, topn, summary, trusted }`

#### 4.6.2 告警 `Alert`

`Alert = { alert_id, type, severity, time_window, object_scope, threshold, actual, evidence_refs, trusted, support_level }`

设计说明：

1. `threshold / actual` 维持 runtime 数值比较语义，用于表达规则阈值与观测值。
2. `support_level=exact` 表示输入语义和证据闭环充分，可给出精确结论。
3. `support_level=degraded` 表示输入不完整或存在降级推断，但仍能提供有限参考。
4. `support_level=unsupported` 表示当前输入不足以支持该类告警结论。

#### 4.6.3 诊断 `Diagnosis`

`Diagnosis = { diag_id, title, diagnosis_type, time_window, object_scope, conclusion, evidence_refs, related_alerts, confidence, support_level }`

设计说明：

1. `support_level` 与 `Alert.support_level` 共享同一正式 taxonomy，至少支持 `exact / degraded / unsupported`。
2. `confidence` 仅用于表达诊断侧的置信描述或展示强弱，不得替代 `support_level` 的对外支持度语义。
3. 当诊断结论由降级推断、部分证据闭环或语义缺失得出时，必须通过 `support_level` 显式表达，而不能只写文本说明。

### 4.7 证据链、不可信窗口与分析上下文

#### 4.7.1 证据引用 `EvidenceRef`

`EvidenceRef = { ref_type, ref_key, t_begin, t_end }`

`ref_type` 最少支持：

1. `event`
2. `slice`
3. `index`

补充稳定主键规范：

1. `event`：`evt:<dataset_id>:<core_id>:<seq>`
2. `slice`：`slice:<dataset_id>:<task_id>:<slice_id>`
3. `index`：`idx:<dataset_id>:<object_type>:<object_id>:<bucket_id>`

#### 4.7.2 不可信窗口 `UntrustedWindow`

`UntrustedWindow = { window_id, source, scope, t_begin, t_end, reason_code, severity }`

设计约束：

1. `source` 至少支持 `crc_fail`、`seq_gap`、`overflow`、`truncate`、`dict_mismatch`、`align_fail`、`open_relation`。
2. `scope` 至少支持 `event`、`rebuild`、`metric`、`alert`、`viz`、`export`。
3. `window_id` 建议生成规则：`uw:<dataset_id>:<source>:<t_begin>:<t_end>:<hash>`。

#### 4.7.3 比较范围 `CompareScope`（OBJ-CompareScope）

`CompareScope = { scope_id, baseline_id, candidate_id, aligned_time_window, filter, dimensions, metric_ids, bucket_size, evidence_policy }`

设计约束：

1. `CompareScope` 是 `cmp_SetScope`、导出元信息和复现上下文共享的正式结构，不允许各模块维护私有比较范围对象。
2. `aligned_time_window` 表示经对齐和裁剪后的正式对比窗口，不等于任一界面拖拽中的临时窗口。
3. `dimensions` 至少支持 `metric / alert / hotspot / interval / task / core / resource / irq`。
4. `bucket_size` 为序列指标与关键区间对齐的稳定参数，未显式指定时由查询层按同一规则推导并写回结构体。
5. `IF-cmp_SetScope` 输入与输出复用同一正式结构；调用方允许省略 `scope_id / aligned_time_window / bucket_size`，由服务端归一化后回填，避免界面层维护私有比较范围草稿。

维度到正式产物映射：

| `dimensions` 输入 | `DiffSummary` 正式产物 | `DiffDetail` 正式产物 | 约束 |
|---|---|---|---|
| `metric` | `metric_changes` | `dimension=metric`，`target.metric_id` | 未请求时不得回填指标差异行 |
| `alert` | `alert_changes` | `dimension=alert`，`target.alert_type` | 必须保留新增/移除/严重度变化语义 |
| `hotspot` | `hotspot_changes` | `dimension=hotspot`，`target.node_id` | 必须允许回链热点对象证据 |
| `interval` | `interval_changes` | `dimension=interval`，`target.interval_type` | 必须表达关键区间 enter/exit/shift 变化 |
| `task` | `task_changes` | `dimension=task`，`target.task_id` | 任务维度关闭时不得生成对应 summary/detail |
| `core` | `core_changes` | `dimension=core`，`target.core_id` | 核维度结果必须与单基线统计口径一致 |
| `resource` | `resource_changes` | `dimension=resource`，`target.resource_id` | 资源维度必须支持等待链/热点对象下钻 |
| `irq` | `irq_changes` | `dimension=irq`，`target.irq_id` | IRQ 维度必须保持与中断统计同口径 |

补充约束：

1. `dimensions` 只决定是否生成对应正式差异产物，不得改变单个维度内部的计算口径。
2. 未请求的维度在 `DiffSummary` 中应保持空集合，在 `DiffDetail` 中不得生成幽灵 detail。

#### 4.7.4 分析上下文 `AnalysisContext`（OBJ-AnalysisContext）

`AnalysisContext = { time_window, filter, selection, zoom_level, focused_view, evidence_anchor, playback_cursor, compare_scope, dataset_role, context_rev, pending_jobs, hover_target, transient_selection, playback_runtime }`

持久化与临时态边界：

1. 可持久化字段固定为 `time_window`、`filter`、`selection`、`zoom_level`、`focused_view`、`evidence_anchor`、`playback_cursor`、`compare_scope`、`dataset_role`。
2. 临时展示态固定为 `context_rev`、`pending_jobs`、`hover_target`、`transient_selection`、`playback_runtime`，仅在进程内维护，不写入 `analysis_context.json`。
3. `playback_cursor` 属于可持久化上下文，用于保证导出快照或复现包重新打开后回到同一证据落点；`playback_runtime` 仅描述播放中/暂停/倍率等瞬态。
4. `context_rev`：每次原子提交递增，用于抑制过期结果覆盖。
5. `pending_jobs`：界面层仅维护后台任务标识，不在持久化快照中导出。
6. `selection` 表示正式联动、导出、复现和书签恢复所使用的稳定选择；`hover_target + transient_selection` 表示 hover / 拖拽 / 框选等进程内预览态，禁止直接污染正式 `selection`。
7. 任一正式上下文提交若覆盖 `time_window / filter / selection / zoom_level / focused_view / evidence_anchor / playback_cursor / compare_scope / dataset_role`，必须同时清理相关临时态，避免把旧 hover 预览带入新的正式上下文。
8. 上述持久化边界统一形成 `RULE-context_persist`，供导出、复现和测试追踪引用。

#### 4.7.5 书签 `Bookmark`

`Bookmark = { bookmark_id, label, time_window, filter, selection, focused_view, evidence_anchor }`

### 4.8 导出对象

#### 4.8.1 导出元信息 `ExportMeta`（OBJ-ExportMeta）

`ExportMeta = { time_unit, clock_source, align_policy, dict_ver, parser_ver, dict_ref, schema_ref, export_scope, export_time, time_window, filter, selection, zoom_level, snapshot_id, run_id, run_batch_id, version_id, experiment_params, analysis_context, compare_scope, evidence_anchor, compare_role, export_source, context_padding_rule, untrusted_windows }`

#### 4.8.2 清单项 `ManifestEntry`（OBJ-ManifestEntry）

`ManifestEntry = { path, category, count, checksum, format, schema_ref, ref_keys, producer }`

#### 4.8.3 导出 / 复现返回合同边界

1. `spec.models.ExportJob`、`spec.models.PackageResult`、`spec.models.ReproSession` 仅保留 legacy / compatibility 身份，不再作为共享正式对象或正式返回 DTO。
2. 正式合同由 `IF-export_Full`、`IF-export_Clipped`、`IF-export_WritePackage`、`IF-repro_OpenPackage` 以接口级返回载荷分别描述。
3. `datasets / restored_context / restore_status / ref_validation` 等组合态不再聚合成新的共享对象；如需对外暴露，由对应接口按最小返回能力分别给出。

### 4.9 展示、回放与对比对象

#### 4.9.0 任务状态查询 `TaskStateQuery`（OBJ-TaskStateQuery）

`TaskStateQuery = { time_window, lane_group, state_mask, task_filter, anchor_ref?, include_summary }`

正式约束：

1. `TaskStateQuery` 是 `IF-viz_QueryTaskStates` 的正式 query DTO，仅用于任务状态视图，不得复用时间线私有查询参数或渲染态。
2. `dataset_id` 不属于正式 query 字段；当前 runtime 对 `query.filter["dataset_id"]` 的消费仅属兼容实现，不得回写为正式合同。
3. 不得把通用 filter bag、工作区定位参数或渲染态混回 `TaskStateQuery`。
4. `IF-viz_QueryTaskStates` 与 `IF-viz_QueryTaskStatesAsync` 必须直接接收上述正式查询对象，避免临时参数组散落到各视图控制器。

#### 4.9.0A 事件表查询 `EventTableQuery`

`EventTableQuery = { time_window, filter, order_by, page_size, page_cursor, column_mask? }`

设计约束：

1. `EventTableQuery.order_by` 默认固定为 `sort_key asc`，`page_cursor` 使用正式 `EventCursor`。
2. `IF-viz_QueryEventTable` 必须直接接收正式事件表查询对象，避免界面层拼装私有分页参数。

#### 4.9.0B staged-load 就绪态 `ReadinessState / LoadPreview`

`ReadinessState = { stage, source, preview, lod_ready, view_ready, fallback_reason }`

`LoadPreview = { dataset_id, header, time_window, chunk_count, record_count, core_ids, lod0_buckets, source, stage, readiness, task_state_preview? }`

最小字段要求：

1. `stage` 至少支持 `queued / preview_ready / parse_rebuild / query_ready`，并在同一作业生命周期内单调推进。
2. `lod_ready` 固定暴露 `lod0 / lod1 / lod2`，用于区分“摘要可用”和“细节可用”。
3. `view_ready` 至少覆盖 `timeline / task_states / event_table / metric_series / resource_graph / alerts` 六类视图。
4. `preview=true` 仅表示首屏预览合同已就绪，不等价于全量 bundle 已可查询。
5. `fallback_reason` 用于标记查询落入退化路径；`LOD2 / EventTable` 走 `materialized_bundle`（完整 `event_stream` 扫描）时必须标记 `bundle_scan`，走 source-backed（`trace_window_scan / package_index`）时保持 `None`。
6. `task_state_preview?` 用于在 `preview_ready` 阶段补充首屏任务状态摘要，但不等价于 `IF-viz_QueryTaskStates` 已进入正式 `query_ready`。

#### 4.9.0C 任务状态预览 `TaskStatePreview`（OBJ-TaskStatePreview）

`TaskStatePreview = { time_window, lane_count, task_ids, state_totals, bucket_summary, readiness, trusted }`

最小字段要求：

1. `readiness.stage` 固定为 `preview_ready`，并复用正式 `ReadinessState`。
2. `lane_count / task_ids / state_totals` 用于表达首屏任务状态摘要，不得要求先物化完整 `TaskStateViewModel.rows`。
3. `bucket_summary[]` 至少包含 `t_begin / t_end / state_counts`，用于在首屏阶段表达最小时间桶级状态分布。
4. `TaskStatePreview` 只能通过 `LoadPreview` 或等价 preview 合同暴露，不得伪装成正式 `IF-viz_QueryTaskStates` 返回值。

#### 4.9.1 任务状态视图模型 `TaskStateViewModel`（OBJ-TaskStateViewModel）

`TaskStateViewModel = { time_window, lane_order, rows, state_legend, summary, cursor_hint, trusted }`

最小字段要求：

1. `rows` 至少包含 `task_id`、`segments[]`、`lane_label`、`state_counts`。
2. `segments[]` 至少包含 `seg_id`、`state`、`t_begin`、`t_end`、`evidence_ref`、`trusted`。
3. `cursor_hint` 用于承接当前证据落点、选择高亮或回放游标，不得与事件表/时间线耦合为同一私有 DTO。
4. `summary.readiness` 必须复用正式 `ReadinessState`，用于显式说明任务状态视图当前是否已进入 `query_ready`。

#### 4.9.2 回放状态 `PlaybackState`（OBJ-PlaybackState）

`PlaybackState = { replay_id, status, rate, cursor, anchor_ref, visible_window, linked_views, trusted }`

最小字段要求：

1. `status` 至少支持 `idle / playing / paused / ended / degraded`。
2. `cursor` 至少包含 `event_uid`、`sort_key`、`timestamp`、`step_index`。
3. `linked_views` 至少标识时间线、任务状态视图、事件表、指标视图的同步状态。
4. `current_index / cursor_ts / mode` 属于 runtime 私有状态，不属于正式返回合同；`replay_*` 系列接口不得要求调用方依赖这些字段。
5. `cursor.step_index` 是外部导航落点，允许保留在正式 `cursor` 内，不视为私有运行态泄露。

#### 4.9.3 事件表游标 `EventCursor`（OBJ-EventCursor）

`EventCursor = { ts, core_id, seq, sort_key }`

最小字段要求：

1. `EventCursor` 必须与 `UnifiedEvent.sort_key` 完全同口径，禁止仅用 `{ts, seq}` 导致排序漂移。
2. 若后续落盘直接采用单字段 `sort_key`，仍需保证可无损还原为 `ts + core_id + seq` 三元组。

#### 4.9.4 事件表分页结果 `EventPage`（OBJ-EventPage）

`EventPage = { items, order_by, cursor_in, next_cursor, prev_cursor, has_more, total_hint, trusted }`

最小字段要求：

1. `items[]` 至少包含 `event_uid`、`timestamp`、`core_id`、`seq`、`event_name`、`task_id?`、`obj_id?`、`trust_tags`。
2. `order_by` 必须显式记录当前排序键，默认固定为 `sort_key asc`。
3. `next_cursor / prev_cursor` 使用 `EventCursor`，并与稳定排序规则一致。
4. `summary.readiness` 必须复用正式 `ReadinessState`，在事件表尚未进入正式查询态时可返回 `NOT_READY` 或 `queued/running` 状态。

#### 4.9.5 差异摘要 `DiffSummary`（OBJ-DiffSummary）

`DiffSummary = { scope, metric_changes, alert_changes, hotspot_changes, interval_changes, task_changes, core_changes, resource_changes, irq_changes, trust_summary }`

最小字段要求：

1. `scope` 使用正式 `CompareScope`。
2. `metric_changes` 至少支持 `delta`、`ratio`、`trend`、`degraded_reason?`。
3. `alert_changes / hotspot_changes / interval_changes` 至少支持 `added`、`removed`、`severity_shift`、`evidence_refs`。

#### 4.9.6 差异明细 `DiffDetail`（OBJ-DiffDetail）

`DiffDetail = { diff_id, scope, target, baseline_view, candidate_view, delta_payload, evidence_refs, related_events, jump_target, trusted }`

最小字段要求：

1. `baseline_view / candidate_view` 至少包含同口径对象摘要、数据集角色和证据锚点。
2. `delta_payload` 至少支持标量差异、序列差异或对象集合差异三类之一。
3. `related_events` 需可回链到 `EventPage` 或时间线落点，避免只返回文本描述。
4. `jump_target` 复用统一证据跳转结构，最少包含 `time_window / selection / evidence_anchor / focused_view`；若两侧基线均有可跳转落点，可附带 `peer_target`。

## 5. 模块详细设计

### 5.1 日志采集与生成模块

#### 5.1.1 模块职责与内部子模块

采集模块由以下内部单元组成：

1. `TraceHookAdapter`：封装 `UEOS` 调度器、同步原语、中断等采集点。
2. `EventBuilder`：构造 `EventHeader + Payload`。
3. `FilterSampler`：执行事件开关、过滤与采样。
4. `PerCpuRingBuffer`：提供每核缓冲和 `reserve-write-commit`。
5. `FlushScheduler`：负责阈值刷出、定时刷出和停采刷出。
6. `ChannelMux`：选择文件、串口、网络通道。
7. `TraceStatsCollector`：统计 `lost / overflow / io_backpressure / write_bytes / flush_bytes / latency`。

#### 5.1.2 关键结构体

| 结构体 | 关键字段 | 说明 |
|---|---|---|
| `trace_init_cfg_t` | `core_count, ring_size, channel_cfg, default_filter, default_sampling, flush_policy` | 初始化配置；`channel_cfg` 支持 `primary + fallback` 目标链与 retry 配置 |
| `trace_filter_rule_t` | `event_mask, core_mask, task_set, obj_set, drop_mode` | 过滤规则 |
| `trace_sampling_policy_t` | `mode, ratio, window_ns, threshold, mark_sampled` | 采样策略 |
| `trace_record_hdr_t` | `ver, flags, core_id, event_id, seq, timestamp, payload_len` | 采集侧事件头 |
| `trace_cpu_ctrl_t` | `write_idx, commit_idx, flush_idx, lost_cnt, overflow_cnt, seq_gen` | 每核控制块 |
| `trace_stats_t` | `lost_total, overflow_total, io_backpressure_total, written_records, flushed_records, flush_latency_ns` | 统计对象 |

#### 5.1.3 关键流程设计

##### （1）初始化流程

1. `trace_Init` 分配或绑定每核环形缓冲区。
2. 初始化 `write_idx / commit_idx / flush_idx / seq_gen`。
3. 注册三类输出通道并加载默认过滤、采样和刷出策略。
4. 输出全局头和字典元信息能力由通道初始化完成。

##### （2）记录流程

1. `TraceHookAdapter` 从当前上下文直接快照 `timestamp / core_id / task_id / obj_id / irq_id`。
2. `FilterSampler` 先执行事件使能，再执行采样命中判断。
3. `EventBuilder` 计算记录长度，生成固定头和事件载荷。
4. `PerCpuRingBuffer.reserve` 原子申请连续空间。
5. `write` 写入头和载荷，不做阻塞 `I/O`，不做动态分配。
6. `commit` 推进 `commit_idx` 并加内存屏障，保证刷出端永远只看到完整记录。
7. 若空间不足，则更新 `lost_cnt / overflow_cnt`，并在后续有空间时补写 `LOSS/OVERFLOW` 语义事件。

##### （3）刷出流程

1. `FlushScheduler` 根据时间阈值、容量阈值或显式 `trace_FlushBuffer` 触发。
2. 当配置 `flush_interval_ms > 0` 时，flush worker 采用 timed wait；到期仅在存在 committed record 或待补 integrity record 时执行写出。
3. 空闲时 worker 只重新进入等待，不做空 chunk 写出，也不 busy loop。
4. 新段创建后必须先写 `GlobalHeader + SegmentMeta`。
5. `ChannelMux` 将已提交区间拆解为 `Chunk`。
6. 为每个 `Chunk` 填充 `ChunkHeader`、计算 `chunk_crc`。
7. `ChannelMux` 先尝试当前活动通道；失败时在 worker 线程内按 `retry_limit / retry_backoff_ms` 重试，超限后切换到下一个 fallback 通道。
8. fallback 切换视为新段，必须先写 `GlobalHeader + SegmentMeta`，并延续逻辑 `segment_seq`。
9. 刷出成功后更新 `flush_idx` 和统计；若所有通道都失败，则返回最终失败状态，并累计 `io_backpressure_total`，后续通过 `OVERFLOW(reason=TRACE_INTEGRITY_REASON_IO_BACKPRESSURE)` 补写正式 integrity 语义。

##### （4）分段与滚动写入规则（RULE-segment_rollover）

1. 触发条件至少支持：达到 `flush_threshold_bytes`、达到 `segment_size_limit`、达到 `segment_duration_limit`、显式停采/刷出、`dict_ver` 变化。
2. 段命名规则建议固定为 `trace_<run_id>_<channel>_<segment_seq>.trace`；若按核拆分，则追加 `_core<core_id>`。
3. 跨分段连续性必须保留 `segment_seq`、`prev_segment_seq` 和每核 `seq_range`，保证分析端可无损拼接稳定排序。
4. 每个分段都必须重复写入最小 `GlobalHeader + SegmentMeta`；`SegmentMeta` 负责承载 `dict_ref_algo / dict_ref_checksum` 等可校验引用信息。
5. 任意滚动切段都不得打断单条记录或 `Chunk`；若切段点位于高频刷出期间，应在当前 `Chunk` 完整提交后切换目标文件。

#### 5.1.3A 运行时协议边界（RULE-collector_runtime_contract）

1. record path（`trace_RecordEvent` 及各专用 `trace_Record*`）只允许执行快照、过滤/采样、`reserve-write-commit`、必要的 integrity 排队和 `RequestFlushAsync()`；不得直接执行 `ChannelMux` 写出、`retry/backoff`、等待 worker 完成或同步 drain。
2. 允许阻塞或等待的路径仅限于后台 flush worker 与控制面接口，如 `trace_Init`、`trace_Disable`、`trace_FlushBuffer`、`trace_Destroy` 所触发的 producer quiesce / flush completion 等非关键路径行为。
3. `auto_flush` 的正式语义是“在 record path 上异步提出 flush 请求”；即使达到阈值或待补 integrity record 积压，也不得把 drain/write 回流为当前 record 调用线程上的同步刷出。
4. `trace_FlushBuffer(handle, mode, deadline_ms)` 的 `deadline_ms` 仅约束调用方等待本次 flush 请求完成的最长时间：
   - `deadline_ms = 0`：等待直到该请求完成；
   - `deadline_ms > 0`：超时即返回失败状态，但 worker 可继续推进后台刷出。
5. `trace_GetStats()` 返回并发可读的原子聚合快照；该快照必须对单个计数器自洽，但不承诺所有 per-core 计数来自同一个全局线性化时刻。
6. `io_backpressure_total`、fallback 和 `OVERFLOW(reason=TRACE_INTEGRITY_REASON_IO_BACKPRESSURE)` 的生成都属于 worker 侧协议；record path 只能观察结果，不得承担重试或通道切换细节。

#### 5.1.4 接口详细设计

采集端接口采用 `C/C++` 逻辑签名描述：

| 接口 | 输入 | 输出 | 线程安全 / 时序约束 | 说明 |
|---|---|---|---|---|
| `trace_Init(cfg, out_handle)` | `trace_init_cfg_t*` | `trace_status_t` | 启动前调用一次 | 初始化采集模块 |
| `trace_Enable(handle, core_mask)` | 句柄、核掩码 | `trace_status_t` | 可重复调用，幂等 | 开启全局或按核采集 |
| `trace_Disable(handle, core_mask, keep_buffer)` | 句柄、核掩码、保留策略 | `trace_status_t` | 与 `Flush` 协同，先停新写入、等待 producer quiesce、drain committed record，再按 `keep_buffer` 决定是否 reset | 关闭采集 |
| `trace_RecordEvent(handle, event_id, payload, payload_len)` | 通用事件参数 | `trace_status_t` | 可在关键路径调用 | 通用记录入口 |
| `trace_RecordTaskSwitch(handle, payload)` | `task_switch_payload` | `trace_status_t` | 可在上下文切换点调用 | 特化接口 |
| `trace_RecordTaskState(handle, payload)` | `task_state_payload` | `trace_status_t` | 可在任务状态变迁点调用 | 特化接口 |
| `trace_RecordIRQ(handle, payload)` | `irq_payload` | `trace_status_t` | 可在中断上下文调用 | 特化接口 |
| `trace_RecordSync(handle, payload)` | `sync_payload` | `trace_status_t` | 可在同步原语路径调用 | 特化接口 |
| `trace_SetFilter(handle, rule)` | `trace_filter_rule_t*` | `trace_status_t` | 由控制线程调用，写时复制生效 | 设置过滤规则 |
| `trace_SetSampling(handle, policy)` | `trace_sampling_policy_t*` | `trace_status_t` | 由控制线程调用，原子切换 | 设置采样策略 |
| `trace_SetDictionaryRef(handle, dict_ref)` | `trace_dict_ref_t*` | `trace_status_t` | 由控制线程调用，必要时标记强制切段 | 更新当前字典元信息 |
| `trace_FlushBuffer(handle, mode, deadline_ms)` | 刷出模式、截止时间 | `trace_status_t` | 非关键路径调用；`deadline_ms` 仅约束调用方等待时长 | 触发批量刷出 |
| `trace_GetStats(handle, out_stats)` | 句柄 | `trace_status_t` | 允许并发读；返回原子聚合快照 | 查询统计信息，至少包含 `lost / overflow / io_backpressure / written / flushed / latency` |

#### 5.1.5 异常与降级设计

1. 缓冲空间不足：更新 `lost / overflow`，生成 `LOSS/OVERFLOW` 事件或缺口标记。
2. 通道异常：文件失败可切换串口或网络；当前通道在 worker 线程内按 `retry_limit / retry_backoff_ms` 重试，超限后切换 fallback；若本次 flush 最终未及时排空，则累计 `io_backpressure_total` 并在后续成功刷出时补写 `OVERFLOW(reason=TRACE_INTEGRITY_REASON_IO_BACKPRESSURE)`。
3. 热更新过滤规则：采用只读快照 + 原子指针切换，不阻塞采集路径。
4. 环回覆盖：必须由 `reserve` 阶段统一判定，禁止写阶段跨界覆盖未刷出数据。

### 5.2 日志事件模型与格式规范模块

#### 5.2.1 模块职责与内部子模块

1. `HeaderLayoutRegistry`：维护头字段布局和偏移。
2. `DictionaryRepository`：维护字典版本和事件定义。
3. `RecordCodec`：实现记录编码与解码。
4. `ChunkVerifier`：校验 `CRC`、条数和序号范围。
5. `CompatPolicyEngine`：处理未知事件、未知字段、字典缺失和版本不匹配。

#### 5.2.2 关键设计规则

##### （1）`EventID` 编码规则

采用 `Domain + Type` 分段编码：

`event_id = (domain << 12) | type`

建议域划分如下：

1. `0x1`：任务 / 调度
2. `0x2`：同步对象 / 资源
3. `0x3`：中断 / 定时器
4. `0x4`：完整性 / 诊断 / 控制

##### （2）载荷编码规则

1. 高频事件采用定长或半定长结构。
2. 低频 / 扩展事件采用 `位图 + TLV`。
3. 解析器只依赖 `payload_len` 做边界控制，不依赖事件语义猜测长度。
4. 同一 `event_id` 演进时优先增加可选字段，不改变已有字段含义。

##### （3）兼容与降级规则

1. 未知 `event_id`：保留头，跳过载荷，生成 `warning`。
2. 未知字段：按 `payload_len` 跳过剩余字段。
3. 字典缺失：仅解析基础头字段和原始载荷字节，挂接 `NO_DICT` 告警。
4. 字典版本不匹配：生成 `dict_mismatch` 不可信窗口。

#### 5.2.3 接口详细设计

| 接口 | 输入 | 输出 | 约束 | 说明 |
|---|---|---|---|---|
| `spec_GetFormatVersion()` | 无 | `format_ver, header_ver` | 无副作用 | 查询格式版本 |
| `spec_GetHeaderLayout(ver)` | 头版本 | `HeaderLayout` | 只读 | 返回字段布局 |
| `spec_GetEventIdRule()` | 无 | `EventIdRule` | 只读 | 返回域和类型编码规则 |
| `spec_GetDictionary(dict_ver)` | 字典版本 | `Result<EventDictionary>` | 可缓存 | 获取指定字典 |
| `spec_EncodeRecord(event_id, payload)` | 事件编号、载荷 | `Result<bytes>` | 供采集侧和测试工具复用 | 编码记录 |
| `spec_DecodeRecord(bytes)` | 原始字节 | `Result<DecodedRecord>` | 只读 | 解码记录 |
| `spec_VerifyChunk(chunk)` | Chunk 字节 | `Result<ChunkVerifyResult>` | 不修改输入 | 校验完整性 |
| `spec_CompatPolicy()` | 无 | `CompatPolicy` | 只读 | 返回兼容 / 降级策略 |

#### 5.2.4 `Chunk` 校验流程

1. 解析 `ChunkHeader`。
2. 检查 `record_count` 与物理长度是否一致。
3. 执行 `chunk_crc` 校验。
4. 若存在 `seq_range`，执行序号连续性预检查。
5. 生成 `ChunkVerifyResult = { ok, gaps, warnings, dict_ver }`。

#### 5.2.5 MVP 必选事件目录（RULE-mvp_event_catalog）

下表定义 `MVP P0` 阶段必须统一落地的事件目录，供采集、解析、重建、测试和导出共用：

| 事件名 | 触发点 | 最小载荷 | 下游依赖 | 是否 P0 必选 |
|---|---|---|---|---|
| `TASK_READY` | 任务进入就绪队列 | `task_id, prio, core_hint?, reason` | `TaskStateSeg`、任务等待指标、时间线 | 是 |
| `TASK_BLOCK` | 任务因资源/事件等待而阻塞 | `task_id, wait_obj_id?, reason, owner_task_id?` | `TaskStateSeg`、`ResourceGraph`、诊断 | 是 |
| `TASK_WAKEUP` | 任务被资源/中断/事件唤醒 | `task_id, wake_src, obj_id?` | `TaskStateSeg`、等待闭环、告警证据 | 是 |
| `TASK_DISPATCH` | 调度器确认某任务待运行 | `task_id, core_id, prio, reason` | 状态闭合回退、对比关键区间 | 是 |
| `TASK_EXIT` | 任务退出 | `task_id, exit_code?` | 生命周期闭合、统计分母 | 是 |
| `CTX_SWITCH` | 发生正式上下文切换 | `core_id, prev_task_id, next_task_id, reason` | `ExecSlice`、时间线、稳定运行边界 | 是 |
| `SCHED_DECISION` | 调度器做出选择或放弃选择 | `core_id, selected_task_id?, rq_len, reason` | 调度诊断、热点分析、对比 | 是 |
| `SYNC_TRY` | 尝试获取同步对象 | `task_id, obj_id, obj_type, timeout?` | `ResourceGraph`、阻塞原因识别 | 是 |
| `SYNC_LOCK` | 成功持有同步对象 | `task_id, obj_id, obj_type` | 持有关系、优先级反转诊断 | 是 |
| `SYNC_UNLOCK` | 释放同步对象 | `task_id, obj_id, obj_type` | 资源闭合、等待时长统计 | 是 |
| `IRQ_ENTER` | 进入中断上下文 | `irq_id, core_id, nesting_depth` | `IrqSpan`、IRQ 持续时间/嵌套指标 | 是 |
| `IRQ_EXIT` | 退出中断上下文 | `irq_id, core_id, nesting_depth` | `IrqSpan`、IRQ 闭合与延迟分析 | 是 |
| `LOSS` | 发现日志丢失或缺口 | `core_id, lost_count, reason` | `UntrustedWindow`、导出说明、测试验收 | 是 |
| `OVERFLOW` | 缓冲区溢出或通道背压 | `core_id, overflow_count, reason` | `UntrustedWindow`、采集诊断、导出说明 | 是 |
| `SYNC_CALIB` | 多核同步校准锚点 | `anchor_id, core_id, ref_ts` | `AlignmentEngine`、`CompareScope` 对齐 | 多核场景 P0 |
| `TS_CALIB` | 时钟校准/漂移校准锚点 | `anchor_id, src_core, dst_core, raw_ts` | `AlignmentEngine`、稳定跨核时间窗 | 多核场景 P0 |

目录约束：

1. 上表事件名、最小载荷和依赖对象必须纳入事件字典与测试基线，禁止实现阶段各自裁剪字段。
2. 单核部署可不产生 `SYNC_CALIB / TS_CALIB`，但必须在元信息中显式声明“未启用跨核对齐”，并按降级路径工作。
3. 若平台存在更细分事件，可作为兼容扩展补充，但不得替代上述 `P0` 目录中的语义主键事件。

#### 5.2.6 MVP 事件字段冻结附表（ANNEX-mvp_event_field_freeze）

本附表冻结 `collector / codec / parser` 在 `MVP P0` 阶段必须共享的关键事件载荷字段。编码顺序默认与表中字段顺序一致；新增字段只能追加在冻结字段之后，并继续受 `payload_len` 保护。

通用兼容规则：

1. 已冻结字段名、单位、语义不得修改；若必须扩展，仅允许新增可选字段或新增枚举值。
2. 必填字段缺失、类型不匹配或与头字段矛盾时，解码器必须返回 `INVALID_PAYLOAD` 并生成 `UntrustedWindow(scope=event)`，不得静默填业务默认值。
3. 可选字段缺失时统一解析为 `null`，重建与指标按降级路径工作。
4. 枚举字段新增取值时，旧版解析器保留原始数值并映射为 `UNKNOWN_*`，不得复用既有取值含义。

##### （1）`TASK_READY`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `task_id` | 是 | `u32` | 进入就绪队列的任务标识 | 主键字段冻结，禁止改名、改位宽或改含义 |
| `prio` | 是 | `i16` | 入队时任务优先级快照 | 保持“数值越小/越大”的平台约定不变，跨版本不得重释 |
| `core_hint` | 否 | `u16` | 调度器给出的目标核提示；单核或未知时可为空 | 缺失统一视为“无提示”，不得补写伪核号 |
| `reason` | 是 | `enum<ready_reason>` | 进入就绪态的直接原因，如创建、唤醒、抢占返回 | 仅允许新增枚举值；旧值语义冻结 |

##### （2）`TASK_BLOCK`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `task_id` | 是 | `u32` | 进入阻塞态的任务标识 | 主键字段冻结，禁止改名或改含义 |
| `wait_obj_id` | 否 | `u64` | 等待对象标识；纯时间等待或未知对象时可为空 | 缺失统一视为“无对象锚点”，不得补零冒充对象 |
| `reason` | 是 | `enum<block_reason>` | 阻塞直接原因，如锁等待、事件等待、延时等待 | 仅允许新增枚举值；旧值语义冻结 |
| `owner_task_id` | 否 | `u32` | 当前持有对象或负责释放资源的任务标识 | 缺失统一视为“未知 owner”，解析端不得反推伪值 |

##### （3）`TASK_WAKEUP`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `task_id` | 是 | `u32` | 被唤醒并重新具备调度资格的任务标识 | 主键字段冻结，禁止改名或改含义 |
| `wake_src` | 是 | `enum<wake_source>` | 唤醒来源，如资源释放、中断、定时器、事件通知 | 仅允许新增枚举值；旧值语义冻结 |
| `obj_id` | 否 | `u64` | 触发唤醒的对象或来源锚点标识 | 缺失统一视为“来源不可定位”，不影响基本闭环 |

##### （4）`CTX_SWITCH`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `core_id` | 是 | `u16` | 发生上下文切换的 CPU 核标识 | 必须与 `EventHeader.core_id` 一致；不一致按坏载荷处理 |
| `prev_task_id` | 是 | `u32` | 被换出的任务标识；空闲任务固定编码为 `0` | `0` 的“idle”语义冻结，不得复用为其他保留值 |
| `next_task_id` | 是 | `u32` | 被换入的任务标识；空闲任务固定编码为 `0` | 与 `prev_task_id` 共用同一 idle 编码约定 |
| `reason` | 是 | `enum<switch_reason>` | 切换原因，如抢占、主动让出、阻塞、IRQ 返回 | 仅允许新增枚举值；旧值语义冻结 |

##### （5）`IRQ_ENTER`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `irq_id` | 是 | `u16` | 进入的中断号 | 主键字段冻结，禁止改名或改含义 |
| `core_id` | 是 | `u16` | 处理中断的 CPU 核标识 | 必须与 `EventHeader.core_id` 一致；不一致按坏载荷处理 |
| `nesting_depth` | 是 | `u8` | 进入后所在的中断嵌套深度，首层为 `1` | 深度计数规则冻结；仅允许扩大位宽时新增头版本 |

##### （6）`IRQ_EXIT`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `irq_id` | 是 | `u16` | 退出的中断号 | 必须与最近未闭合的同核 `IRQ_ENTER` 对齐 |
| `core_id` | 是 | `u16` | 处理中断的 CPU 核标识 | 必须与 `EventHeader.core_id` 一致；不一致按坏载荷处理 |
| `nesting_depth` | 是 | `u8` | 退出前所在的中断嵌套深度 | 与 `IRQ_ENTER` 共用同一深度语义；缺失或倒退按异常栈处理 |

##### （7）`LOSS`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `core_id` | 是 | `u16` | 发现缺口的 CPU 核标识 | 必须与 `EventHeader.core_id` 一致；不一致按坏载荷处理 |
| `lost_count` | 是 | `u32` | 本次确认丢失的记录数或最小缺口条数 | 语义固定为“增量缺口计数”，不得改成累计总数 |
| `reason` | 是 | `enum<integrity_reason>` | 缺口来源，如序号跳变、介质损坏、通道丢包 | 仅允许新增枚举值；旧值语义冻结 |

##### （8）`OVERFLOW`

| 字段名 | 必填 | 类型 | 语义说明 | 兼容策略 |
|---|---|---|---|---|
| `core_id` | 是 | `u16` | 发生溢出或背压的 CPU 核标识 | 必须与 `EventHeader.core_id` 一致；不一致按坏载荷处理 |
| `overflow_count` | 是 | `u32` | 本次确认溢出的记录数或最小受影响条数 | 语义固定为“增量溢出计数”，不得改成累计总数 |
| `reason` | 是 | `enum<integrity_reason>` | 溢出来源，如环形缓冲溢出、刷出背压、通道阻塞 | 仅允许新增枚举值；旧值语义冻结 |

其余 `P0` 事件继续以 `RULE-mvp_event_catalog` 中的最小载荷为基线；若进入 `collector / codec / parser` 开发面并需要新增冻结字段，必须按本附表同一模板补充评审。

### 5.3 日志解析与状态重建模块

#### 5.3.1 模块职责与内部子模块

1. `InputAdapter`：统一离线文件、在线流和导出包输入。
2. `ChunkDecodePipeline`：完成扫描、校验、解码和字典绑定。
3. `AlignmentEngine`：基于同步锚点计算核间偏移与漂移。
4. `StableMergeEngine`：按稳定规则合并多核事件。
5. `RebuildEngine`：重建 `ExecSlice`、`TaskStateSeg`、`ResourceGraph`、`IrqSpan`。
6. `IndexBuilder`：构建时间索引、对象索引和多级摘要。
7. `QueryEngine`：对外提供按时间窗、对象和类型的统一查询。

#### 5.3.2 关键配置与结构

| 结构 / 对象 | 关键字段 | 说明 |
|---|---|---|
| `ParserConfig` | `time_unit, align_policy, compat_policy, chunk_window, online_mode` | 解析配置 |
| `FeedResult` | `decoded_records, gaps, warnings, online_cursor` | 单次馈入结果 |
| `CoreAlignSegment` | `core_id, t_begin, t_end, offset_ns, drift_ppm, confidence` | 校准结果段 |
| `QueryFilter` | `time_window, task_ids, core_ids, event_ids, obj_ids, severity` | 查询条件 |
| `IndexSummary` | `bucket_size, counts, task_bitmap, object_bitmap` | 多级摘要 |

#### 5.3.3 关键流程设计

##### （1）统一输入适配

1. 离线文件：`InputAdapter` 顺序扫描多文件，按文件头和 `dict_ver` 绑定字典。
2. 在线流：以 `Chunk` 为单位持续读取，允许 `partial chunk` 累积后再解码。
3. 导出包：从 `event/` 目录恢复 `UnifiedEventStream`，跳过原始 `Chunk` 校验阶段，但保留 `manifest` 校验和上下文恢复。

##### （2）对齐与排序

1. 默认按 `timestamp -> core_id -> seq` 排序；该稳定排序规则记为 `RULE-stable_sort`。
2. 若存在 `SYNC / TS_CALIB` 锚点，则先求每核对齐段 `CoreAlignSegment`。
3. 对齐值不足时仅在锚点覆盖区间应用偏移 / 漂移修正，其余区间生成 `align_fail` 不可信窗口。
4. 合并时保留 `timestamp_raw` 和 `timestamp_aligned` 双字段。

##### （3）状态重建

1. 任务状态机：`READY -> RUNNING -> BLOCKED / READY -> EXIT`。
2. 切换边界优先由 `CTX_SWITCH` 决定；缺少显式切换边界时，可由 `TASK_DISPATCH` 辅助闭合片段。
3. 资源关系由 `SYNC_TRY / SYNC_LOCK / SYNC_UNLOCK` 建立，未闭合关系生成 `open_relation` 不可信窗口。
4. 中断区间由 `IRQ_ENTER / IRQ_EXIT` 建立，嵌套深度由事件载荷与栈同步维护。

##### （4）索引构建

1. 时间索引：按窗口桶记录事件偏移和片段范围。
2. 对象索引：按 `task_id / core_id / event_id / obj_id / irq_id` 建多维倒排。
3. 视图摘要：为时间线 `LOD`、曲线聚合和事件表分页生成摘要。

#### 5.3.4 接口详细设计

| 接口 | 输入 | 输出 | 约束 | 说明 |
|---|---|---|---|---|
| `prs_Init(cfg, dict)` | `ParserConfig, EventDictionary` | `Result<ParserSession>` | 初始化阶段调用 | 初始化解析器 |
| `prs_FeedChunk(core_id, bytes)` | 核号、Chunk 字节 | `Result<FeedResult>` | 支持离线和在线 | 输入统一 Chunk |
| `prs_Finalize()` | 无 | `Result<ParseSummary>` | 输入完成后调用 | 结束输入并输出摘要 |
| `aln_Calibrate(sync_events)` | 同步锚点集合 | `Result<CoreAlignSegment[]>` | 只对有锚点区间生效 | 计算对齐参数 |
| `aln_Merge(per_core_events)` | 每核事件序列 | `Result<UnifiedEventStream>` | 稳定排序 | 跨核合并 |
| `rb_Rebuild(unified_events)` | 统一事件流 | `Result<RebuildBundle>` | 依赖排序结果 | 重建状态对象 |
| `idx_Build(results)` | 重建结果 | `Result<IndexBundle>` | 可增量 | 构建索引与摘要 |
| `qry_Query(filter, range)` | 查询条件、时间窗 | `Result<QueryResult>` | 只读，支持分页 | 统一查询接口 |

#### 5.3.5 一致性与降级处理

1. `crc_fail / truncate / seq_gap` 直接生成 `event` 级不可信窗口。
2. 与不可信区间相交的重建对象标记 `trusted = false`。
3. 缺口区间禁止输出强推断的资源持有者、响应时间闭环或中断延迟结论。
4. 在线解析与离线解析共用同一解码、排序、重建和索引代码路径，仅输入适配器不同。

### 5.4 运行时指标统计、诊断与对比模块

#### 5.4.1 模块职责与内部子模块

1. `WindowAggregator`：按时间窗聚合指标。
2. `DistributionEngine`：计算均值、最大值、分位数。
3. `HotspotAnalyzer`：计算任务 / 资源 / 中断热点 `TopN`。
4. `AlertEngine`：执行阈值告警。
5. `DiagnosisEngine`：生成轻规则诊断结论。
6. `CompareEngine`：对 `baseline / candidate` 生成同口径差异结果。

#### 5.4.2 指标口径设计

| 指标域 | 核心指标 | 主要输入 |
|---|---|---|
| 核级 | 利用率、空闲率、上下文切换次数、中断占用比例、负载不均衡 | `ExecSlice, IrqSpan` |
| 任务级 | 响应时间、抖动、就绪等待、阻塞时长、运行占比、截止期违约 | `TaskStateSeg, ExecSlice` |
| 资源级 | 竞争次数、平均等待、持有时长、等待队列峰值、热点 TopN | `ResourceGraph` |
| 中断级 | ISR 持续时间、嵌套深度峰值、中断延迟、挤压窗口 | `IrqSpan, UnifiedEventStream` |

实例级语义边界：

1. `MVP` 默认不要求所有平台都产出完备 `job_id / instance_id`，但若输入中存在该语义，必须透传到 `UnifiedEvent / ExecSlice / TaskStateSeg` 并优先用于实例级指标闭环。
2. 当缺少实例级语义时，响应时间、抖动、截止期违约仅可在“显式 `release/finish` 事件完整且单任务不存在多实例并行”的前提下输出；否则结果标记为 `degraded` 或 `unsupported`。
3. 指标层不得基于弱语义数据伪造实例级精确结论，相关能力需通过 `RebuildBundle.capability_flags` 显式暴露。

#### 5.4.3 轻规则诊断设计

1. **截止期违约**：有 `deadline / release / finish` 语义时判定超时。
2. **潜在优先级反转**：高优任务等待某对象，低优任务持有该对象，且期间出现中优任务运行挤压。
3. **异常 IRQ 挤压**：异常窗口内中断持续或密度过高，导致任务响应恶化。
4. 所有规则必须输出 `EvidenceRef[]`，不得只输出文字结论。
5. `Alert` 与 `Diagnosis` 必须共享 `support_level = exact / degraded / unsupported` 的正式 taxonomy；`confidence` 仅用于补充展示，不替代支持度语义。

#### 5.4.4 双基线对比设计

`CompareEngine` 是本次详细设计补齐的关键能力，统一复用单基线统计结果，不建立私有差异口径。

1. 输入角色固定为 `baseline / candidate`。
2. `cmp_SetScope` 产出正式 `CompareScope`，决定对齐时间窗、过滤条件、比较维度和统一桶宽。
3. `metric_Compare` 针对以下对象生成差异：
   - 标量指标：输出 `delta / ratio / trend`
   - 序列指标：按相同桶宽做逐桶差异
   - 热点对象：输出 `enter / exit / shift` 差异
   - 告警集合：输出新增、消失、严重度变化
   - 关键事件区间：按事件类型和对象锚点生成候选差异区间
4. 差异项必须能下钻到各自基线的证据链。

#### 5.4.5 接口详细设计

| 接口 ID | 接口 | 输入 | 输出 | 约束 | 说明 |
|---|---|---|---|---|---|
| `IF-metric_Init` | `metric_Init(cfg)` | `MetricConfig` | `Result<MetricSession>` | 初始化调用 | 初始化统计配置 |
| `IF-metric_Ingest` | `metric_Ingest(rebuild_bundle)` | `OBJ-RebuildBundle` | `Result<void>` | 仅接受完整 `RebuildBundle`；支持增量 | 接入分析基线 |
| `IF-metric_Compute` | `metric_Compute(t_begin, t_end, filter)` | 时间窗、过滤条件 | `Result<MetricResult[]>` | 只读 | 生成指标结果 |
| `IF-metric_Compare` | `metric_Compare(baseline, candidate, scope)` | 双基线、`OBJ-CompareScope` | `Result<DiffBundle>` | 口径必须与单基线一致 | 生成差异统计 |
| `IF-alert_Evaluate` | `alert_Evaluate(t_begin, t_end, filter)` | 时间窗、过滤条件 | `Result<Alert[]>` | 依赖最新指标 | 执行告警检测 |
| `IF-diag_Generate` | `diag_Generate(t_begin, t_end, alert_list)` | 时间窗、告警列表 | `Result<Diagnosis[]>` | 依赖证据链 | 生成诊断结论 |
| `IF-diag_Backtrace` | `diag_Backtrace(diag_id / alert_id)` | 诊断或告警编号 | `Result<EvidenceRef[]>` | 只读 | 返回反查证据 |
| `IF-metric_Export` | `metric_Export(format, scope, t_begin, t_end)` | 导出格式、范围 | `Result<ExportChunk[]>` | 不改写主状态 | 导出指标层结果 |

其中 `IF-metric_Ingest` 的输入闭合规则形成 `RULE-metric_ingest_bundle`：

1. 指标层只接收 `OBJ-RebuildBundle`，不得重新拆回私有 `event_stream + state_seq + exec_slices` 参数组合。
2. 若 `task_states / exec_slices / resource_graph / irq_spans / untrusted_windows` 任一必选通道未就绪，则返回 `NOT_READY` 或 `INVALID_ARG`，不得静默降级为部分指标。
3. 指标、告警、诊断与导出必须共享 `capability_flags` 的解释，不得各自猜测实例级语义能力。
4. 后续开发、联调和自测统一以 `metric_Ingest(rebuild_bundle)` 为标准入口；需求、概要和测试笔记均不得再使用旧的三参数接口口径。

#### 5.4.6 不可信窗口传播规则

1. 与 `UntrustedWindow(scope=metric)` 相交的指标结果必须显式标记 `trusted = false` 或 `confidence = degraded`。
2. 告警不得跨越缺口区间强行闭合时间窗。
3. 对比结果若任一侧存在不可信窗口，则差异摘要和明细都必须继承该标记。

### 5.5 可视化分析、回放、对比、导出与复现模块

#### 5.5.1 Python Qt 分层设计

1. `WorkspaceController(QObject)`：工作区生命周期和命令分发中心。
2. `DatasetRepository`：维护单基线和双基线数据集对象。
3. `ContextStore(QObject)`：唯一 `AnalysisContext` 持有者，负责原子提交和 `context_rev` 递增。
4. `TimelineViewModel`：提供时间线 / 甘特图查询与渲染数据。
5. `TaskStateViewModel(QObject)`：提供独立任务状态视图查询、聚合和证据落点。
6. `MetricSeriesViewModel`：提供曲线和统计摘要。
7. `EventTableModel(QAbstractTableModel)`：支持分页、排序和落点定位。
8. `ResourceGraphViewModel`：提供热点资源和等待链。
9. `AlertPanelModel`：提供告警、证据链和跳转命令。
10. `BookmarkService`：管理书签。
11. `ReplayService`：管理回放会话、步进和播放节拍。
12. `CompareService`：管理双基线数据加载、差异摘要和明细查询。
13. `ExportService`：管理快照冻结、任务调度和导出包写出。
14. `ReproService`：管理复现包校验、上下文恢复和角色装载。
15. `BackgroundJobManager`：统一管理 `input / parse_rebuild / query / export` lane、取消、阶段推进和进度回报。

#### 5.5.2 视图与联动机制

六类核心视图如下：

1. 时间线 / 甘特图
2. 指标曲线
3. 任务状态视图
4. 事件表
5. 资源争用视图
6. 告警面板

联动规则如下：

1. 所有视图只通过 `ContextStore.commit(delta)` 订阅 `AnalysisContext` 变化，不直接互相调用。
2. 单次用户操作只允许产生一次 `Context` 原子提交。
3. 同帧中的缩放、拖拽和选择更新应合并为一次 `context_rev` 递增。
4. 任一视图发起的“跳转到证据链”必须落为统一的 `time_window + selection + evidence_anchor` 更新。
5. hover / 拖拽 / 框选预览必须写入 `hover_target / transient_selection`；跨视图查询联动可优先读取 `transient_selection`，但导出、复现、书签和正式跳转只读取持久化字段中的 `selection`。

#### 5.5.3 大数据量加载设计

1. 时间线采用三级 `LOD`：
   - `LOD0`：全局摘要桶
   - `LOD1`：`ExecSlice + SwitchPoint`
   - `LOD2`：原始事件 / 最小片段
2. 曲线按桶宽自适应加载，并优先返回已有摘要数据。
3. 任务状态视图独立维护 `lane` 分组缓存，不与时间线共享渲染态，但可复用底层 `TaskStateSeg` 查询和摘要索引。
4. 事件表采用 `EventCursor = {ts, core_id, seq, sort_key}` 分页，防止排序漂移。
5. 资源图默认只返回热点 `TopN` 和摘要关系，按需展开等待链。
6. 缓存键固定为：
   - 时间线：`time_window + zoom_level + filter`
   - 任务状态视图：`time_window + lane_group + state_mask + filter`
   - 曲线：`metric_id + time_window + bucket_size + filter`
   - 事件表：`filter + cursor + order_key`
7. `viz_LoadDatasetAsync()` 必须先交付 `LoadPreview`，再推进后台 `parse_rebuild`；禁止把“预览可见”和“全量 bundle 可查询”混为单一布尔态。
8. 后台 load 生命周期固定为：
   - `queued`
   - `preview_ready`
   - `parse_rebuild`
   - `query_ready`
9. `ReadinessState.view_ready` 必须驱动视图可用性：
   - `timeline` 允许在 `preview_ready` 先消费 `LOD0`
   - `LoadPreview.task_state_preview` 允许在 `preview_ready` 先交付任务状态摘要
   - `task_states / event_table` 若尚未进入正式查询态，必须显式返回 `pending` 或 `NOT_READY`
10. `Query Worker Pool` 必须与 `load/export` lane 隔离，避免查询任务挤占解析和导出执行槽。
11. `LOD2` 与事件表分页允许从以下来源返回正式查询结果：
   - `materialized_bundle`
   - `trace_window_scan`
   - `package_index`
12. 若 `LOD2` / 事件表在可回源场景下仍退回完整 `event_stream` 或等价全量 bundle 扫描，必须通过 `fallback_reason` 显式标记，例如 `bundle_scan`。
13. `LOD2 / EventTable` 当前默认主路径策略冻结为：当 `bundle.event_stream` 可用时，优先走 `materialized_bundle`。
14. 当 `bundle.event_stream` 不可用但 source 仍可回源时，`LOD2 / EventTable` 必须切到 source-backed（`trace_window_scan` 或 `package_index`）；仅当 bundle 与 source 都不可用时才允许返回 `NOT_READY`。
15. `fallback_reason` 判定边界固定为：`materialized_bundle` 路径写 `bundle_scan`（过滤导致全量扫描时可写更细 reason，如 `filter_requires_bundle_scan`），source-backed 路径必须保持 `None`。

#### 5.5.4 接口详细设计

##### （1）工作区与单基线分析接口

| 接口 ID | 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|---|
| `IF-viz_InitWorkspace` | `viz_InitWorkspace()` | 无 | `Result<WorkspaceHandle>` | 初始化工作区和默认布局 |
| `IF-viz_LoadDataset` | `viz_LoadDataset(source)` | 日志文件、在线流或导出包 | `Result<DatasetHandle>` | 加载单基线数据集 |
| `IF-viz_LoadDatasetAsync` | `viz_LoadDatasetAsync(source)` | 日志文件、在线流或导出包 | `Result<{ job_id, preview, readiness, stage, view_ready }>` | 提交 staged-load 作业并立即返回首屏合同，`preview` 可选携带 `task_state_preview` |
| `IF-viz_SetContext` | `viz_SetContext(context_delta)` | `time_window / filter / zoom_level / focused_view` 增量 | `Result<AnalysisContext>` | 提交统一上下文 |
| `IF-viz_ApplySelection` | `viz_ApplySelection(selection)` | 任务 / 核 / 资源 / 告警 / 事件选择 | `Result<AnalysisContext>` | 选择并触发联动 |
| `IF-viz_SetHoverTarget` | `viz_SetHoverTarget(hover_target, transient_selection?)` | hover 对象与可选临时选择集 | `Result<AnalysisContext>` | 写入进程内 hover 预览态，不改正式 `selection` |
| `IF-viz_SetTransientSelection` | `viz_SetTransientSelection(selection, hover_target?)` | 临时选择集与可选 hover 目标 | `Result<AnalysisContext>` | 写入拖拽/框选/推演态，供同帧联动查询使用 |
| `IF-viz_CommitTransientSelection` | `viz_CommitTransientSelection()` | 无 | `Result<AnalysisContext>` | 将当前 `transient_selection` 提升为正式 `selection` 并清理临时态 |
| `IF-viz_CancelTransientSelection` | `viz_CancelTransientSelection(clear_hover_target=true)` | 是否同时清理 `hover_target` | `Result<AnalysisContext>` | 取消当前预览态，恢复到正式选择 |
| `IF-viz_QueryTimelineLOD` | `viz_QueryTimelineLOD(scope)` | 时间窗、`LOD`、过滤条件 | `Result<TimelinePayload>` | 查询时间线数据 |
| `IF-viz_QueryTimelineLODAsync` | `viz_QueryTimelineLODAsync(scope)` | 时间窗、`LOD`、过滤条件 | `Result<{ job_id, readiness, stage }>` | 把时间线查询提交到 `Query Worker Pool` |
| `IF-viz_QueryTaskStates` | `viz_QueryTaskStates(query)` | `TaskStateQuery` | `Result<TaskStateViewModel>` | 查询独立任务状态视图，不复用时间线私有 DTO |
| `IF-viz_QueryTaskStatesAsync` | `viz_QueryTaskStatesAsync(query)` | `TaskStateQuery` | `Result<{ job_id, readiness, stage }>` | 把任务状态查询提交到 `Query Worker Pool` |
| `IF-viz_QueryMetricSeries` | `viz_QueryMetricSeries(scope)` | 指标范围、聚合桶宽 | `Result<MetricSeries[]>` | 查询曲线数据 |
| `IF-viz_QueryEventTable` | `viz_QueryEventTable(query)` | `EventTableQuery` | `Result<EventPage>` | 查询事件表，分页游标固定使用 `EventCursor` |
| `IF-viz_QueryEventTableAsync` | `viz_QueryEventTableAsync(query)` | `EventTableQuery` | `Result<{ job_id, readiness, stage }>` | 把事件表查询提交到 `Query Worker Pool` |
| `IF-viz_QueryResourceGraph` | `viz_QueryResourceGraph(scope)` | 时间窗、对象范围 | `Result<ResourceGraphPayload>` | 查询资源关系 |
| `IF-viz_QueryAlerts` | `viz_QueryAlerts(scope)` | 时间窗、过滤、严重度 | `Result<Alert[]>` | 查询告警和锚点 |

##### （2）书签接口（新增补齐接口）

为覆盖 `FR-VIZ-03`，在详细设计中补齐以下工作区接口：

| 接口 ID | 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|---|
| `IF-viz_CreateBookmark` | `viz_CreateBookmark(label, context, evidence_anchor)` | 标签、上下文、证据锚点 | `Result<Bookmark>` | 创建轻量书签 |
| `IF-viz_ListBookmarks` | `viz_ListBookmarks()` | 无 | `Result<Bookmark[]>` | 查询书签列表 |
| `IF-viz_ApplyBookmark` | `viz_ApplyBookmark(bookmark_id)` | 书签编号 | `Result<AnalysisContext>` | 恢复书签上下文 |
| `IF-viz_DeleteBookmark` | `viz_DeleteBookmark(bookmark_id)` | 书签编号 | `Result<void>` | 删除书签 |

##### （3）回放接口组

| 接口 ID | 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|---|
| `IF-replay_Init` | `replay_Init(unified_stream, exec_slices, context)` | 统一事件流、执行片段、上下文 | `Result<ReplayHandle>` | 初始化回放会话 |
| `IF-replay_Play` | `replay_Play(rate)` | 播放倍率 | `Result<PlaybackState>` | 启动播放 |
| `IF-replay_Pause` | `replay_Pause()` | 无 | `Result<PlaybackState>` | 暂停回放 |
| `IF-replay_StepForward` | `replay_StepForward(count)` | 事件步数 | `Result<PlaybackState>` | 按事件前进 |
| `IF-replay_StepBackward` | `replay_StepBackward(count)` | 事件步数 | `Result<PlaybackState>` | 按事件后退 |
| `IF-replay_Seek` | `replay_Seek(target)` | 时间戳、事件编号、`EvidenceRef` 或锚点 | `Result<PlaybackState>` | 精确定位 |
| `IF-replay_GetState` | `replay_GetState()` | 无 | `Result<PlaybackState>` | 查询回放状态 |

回放接口组统一返回最小 `PlaybackState`，而不是 `ReplayService` 的内部运行态快照。

##### （4）对比接口组

| 接口 ID | 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|---|
| `IF-cmp_LoadPair` | `cmp_LoadPair(baseline_source, candidate_source)` | 两份日志或导出包 | `Result<CompareHandle>` | 载入双运行基线 |
| `IF-cmp_SetScope` | `cmp_SetScope(scope)` | `CompareScope` 正式结构，允许 `scope_id / aligned_time_window / bucket_size` 为空 | `Result<CompareScope>` | 归一化并固化正式比较范围，同时回写 `AnalysisContext.compare_scope` |
| `IF-cmp_QueryDiffSummary` | `cmp_QueryDiffSummary()` | 无 | `Result<DiffSummary>` | 查询差异摘要 |
| `IF-cmp_QueryDiffDetail` | `cmp_QueryDiffDetail(target)` | 差异项标识 | `Result<DiffDetail>` | 查询明细和证据锚点 |
| `IF-cmp_LinkAuxView` | `cmp_LinkAuxView(dataset_id, context)` | 数据集、辅助上下文 | `Result<void>` | 建立同库多窗口辅助视图 |

##### （5）导出与复现接口组

| 接口 ID | 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|---|
| `IF-export_Full` | `export_Full(scope)` | 数据集或工作区句柄 | `Result<{ job_id }>` | 提交全量导出作业 |
| `IF-export_Clipped` | `export_Clipped(scope)` | 当前 `AnalysisContext` | `Result<{ job_id }>` | 提交裁剪导出作业 |
| `IF-export_WritePackage` | `export_WritePackage(job_id, output_path)` | 作业编号、输出路径 | `Result<{ package_path, entry_count, snapshot_id }>` | 写多文件导出包，并固化 `dict_ref / schema_ref / compare_scope` |
| `IF-repro_OpenPackage` | `repro_OpenPackage(path_or_stream)` | 路径或流 | `Result<{ package_path, meta, manifest }>` | 打开并校验复现包 |
| `IF-repro_RestoreContext` | `repro_RestoreContext(snapshot_id)` | 快照编号 | `Result<AnalysisContext>` | 恢复正式上下文并校验 `dict_ref / schema_ref / compare_scope` |
| `IF-repro_LoadAsDataset` | `repro_LoadAsDataset(role)` | `single / baseline / candidate` | `Result<DatasetHandle>` | 作为工作区数据集装载 |

接口级返回载荷说明：

1. `job_id` 是导出后台作业的最小可追踪标识。
2. `package_path / entry_count / snapshot_id` 构成写包结果最小返回合同，分别对应包路径、清单条目数和快照标识。
3. `repro_OpenPackage()` 返回中的 `meta` 对齐 `OBJ-ExportMeta`，`manifest.entries[]` 的条目口径对齐 `OBJ-ManifestEntry`。
4. 上述返回载荷仅作为接口级合同存在，不再抽象为 `ExportJob / PackageResult / ReproSession` 共享对象。

#### 5.5.5 回放、对比、导出和复现流程

##### （1）回放流程（FLOW-replay）

1. `replay_Init` 基于 `UnifiedEventStream` 建立事件游标。
2. `ReplayService` 用 `QTimer` 按倍率推进事件索引。
3. 每次步进后更新 `AnalysisContext.playback_cursor`。
4. 时间线、任务状态视图、事件表、指标视图通过 `ContextStore` 收到变更并同步刷新。

##### （2）对比流程（FLOW-compare）

1. `cmp_LoadPair` 分别载入 `baseline / candidate` 数据集。
2. `CompareService` 通过 `IF-cmp_SetScope` 对调用方提交的 `CompareScope` 执行归一化，生成正式 `CompareScope`，并强制共享相同 `time_window / filter / bucket_size`。
3. 归一化后的 `CompareScope` 同步写回 `AnalysisContext.compare_scope`、`meta.json.compare_scope` 和 `context/compare_scope.json`。
4. 对比面板必须支持按 `dimension` 切换 `metric / alert / hotspot / interval / task / core / resource / irq`，并展示统一 detail panel。
5. 点击差异项后可通过 `jump_target` 下钻到基线证据链；若 `peer_target` 存在，则允许继续切换到对侧基线证据。
6. 同库多窗口只读，不生成正式差异结论。

##### （3）导出流程（FLOW-export）

1. `ExportService` 从当前工作区冻结 `snapshot_id`。
2. 固定 `AnalysisContext` 的可持久化字段、`CompareScope`、数据版本号、`dict_ref / schema_ref` 和查询口径。
3. 顺序写出 `event / rebuild / result / context / reference / meta.json / manifest.json`。
4. 写出期间用户继续操作不影响当前导出作业。

##### （4）复现流程（FLOW-repro）

1. `repro_OpenPackage` 校验 `manifest` 和版本兼容性。
2. 解析 `meta.json`、`analysis_context.json` 和 `compare_scope.json`。
3. `repro_RestoreContext` 仅恢复 `AnalysisContext` 的可持久化字段，并校验 `dict_ref / schema_ref / compare_scope`。
4. `repro_LoadAsDataset` 将包内容载入为单基线或双基线工作区。

## 6. 导出包、快照与序列化设计

### 6.1 导出包目录结构

```text
package/
  meta.json
  manifest.json
  event/
    events.jsonl | events.trace
  rebuild/
    exec_slices.csv
    task_states.csv
    resource_graph.json
    irq_spans.csv
  result/
    metrics.csv
    hotspots.csv
    alerts.csv
    diagnosis.json
  context/
    analysis_context.json
    compare_scope.json
    anchors.json
    bookmarks.json
  reference/
    dictionary.json
    schema/
      package.schema.json
      meta.schema.json
      manifest.schema.json
      analysis_context.schema.json
      compare_scope.schema.json
```

### 6.2 `meta.json` 必选字段

| 字段 | 说明 |
|---|---|
| `time_unit, clock_source` | 时间口径 |
| `align_policy, dict_ver, parser_ver, dict_ref, schema_ref` | 解析、字典和模式基线 |
| `export_scope, export_time, snapshot_id` | 导出快照信息 |
| `time_window, filter, selection, zoom_level` | 导出上下文 |
| `run_id, run_batch_id, version_id, experiment_params` | 运行追踪 |
| `analysis_context, compare_scope, evidence_anchor, export_source` | 复现和来源追踪 |
| `compare_role` | `single / baseline / candidate` |
| `context_padding_rule, untrusted_windows` | 裁剪补边和不可信窗口 |

引用字段采用以下推荐结构：

`dict_ref = { path, algo, checksum, dict_ver }`

`schema_ref = { package_schema, meta_schema, manifest_schema, analysis_context_schema, compare_scope_schema }`

约束：

1. `dict_ref.path` 可指向包内 `reference/dictionary.json` 或稳定外部引用，但导出包内必须保留可校验副本或等价哈希。
2. `algo` 当前固定推荐 `sha256`，后续扩展算法必须在 `manifest.package_version` 中显式声明。
3. `schema_ref` 可表示为“名称 -> 摘要引用”映射，但 `meta.json` 与 `manifest.json` 必须至少能独立解析出 `meta / manifest / analysis_context / compare_scope` 四类 `schema`。
4. `IF-repro_OpenPackage` 与脱离界面的二次分析都必须先校验 `dict_ref / schema_ref`，再恢复 `AnalysisContext` 和差异口径。

### 6.3 `manifest.json` 设计

1. 每个文件记录 `path, category, count, checksum, format, schema_ref, producer`。
2. 对 `events.trace` 或 `events.jsonl` 记录 `ref_keys` 摘要，用于 `EvidenceRef` 回链。
3. 包级清单记录 `package_version`、`created_at`、`snapshot_id`、`dict_ref`、`schema_ref`。
4. `entries[].schema_ref` 必须引用 `meta.json.schema_ref` 中的稳定键，不得退化为自由文本版本标签。
5. 若包内包含分段事件文件或多数据集导出，`manifest` 需额外记录 `dataset_role`、`segment_seq`、`prev_segment_id` 或等价连续性字段。

### 6.4 裁剪导出补边规则

1. 对片段型对象，若片段与导出窗口相交，则完整导出该片段。
2. 对状态重建所必需的边界事件，允许在窗口前后各补边 `±Δ`，并在 `context_padding_rule` 中声明。
3. 与导出窗口相交的 `UntrustedWindow` 必须完整导出。

### 6.5 `AnalysisContext` 持久化边界（RULE-context_persist）

1. `context/analysis_context.json` 仅序列化 `AnalysisContext` 的可持久化字段；`context_rev`、`pending_jobs`、`hover_target`、`transient_selection`、`playback_runtime` 一律不落盘。
2. `context/compare_scope.json` 与 `meta.json.compare_scope` 保持同口径，前者供工作区恢复，后者供脱离界面的二次分析。
3. `context/bookmarks.json` 只保存轻量导航上下文，不得覆盖 `analysis_context.json` 的正式复现入口。
4. 任一导出或复现实现都必须按“持久化字段恢复 + 临时字段重建”的策略工作，避免恢复到不同视图落点。

## 7. 可信度传播、异常处理与兼容性设计

### 7.1 `UntrustedWindow` 传播规则

1. **事件层**：`crc_fail / seq_gap / overflow / truncate / dict_mismatch` 在解码阶段生成。
2. **重建层**：与事件层不可信区间相交或出现未闭合关系时，生成 `open_relation`。
3. **指标层**：与不可信区间重叠的指标标记为 `degraded`。
4. **告警层**：若证据链依赖不可信数据，告警必须带可信度说明。
5. **展示层**：时间线、告警面板、导出对话框展示明显提示。
6. **导出层**：`meta.json` 和结果层同步输出不可信窗口。

### 7.2 异常处理原则

1. 不允许因为单个 `Chunk` 损坏导致整个数据集不可用。
2. 不允许因为一次导出失败污染当前工作区数据。
3. 不允许因为校准失败伪造全局绝对时间真值。
4. 不允许因为未知字段 / 未知事件导致崩溃。

### 7.3 兼容性策略

1. `payload_len` 是前向兼容基础；未知内容全部跳读。
2. 头版本和事件版本分开演进。
3. 废弃事件编号不可复用。
4. 旧版本导出包可通过 `manifest + meta` 进行兼容加载；无法兼容时明确报错 `UNSUPPORTED_VERSION`。

## 8. 配置与运行设计

### 8.1 目标端配置项

| 配置项 | 说明 |
|---|---|
| `ring_size_per_cpu` | 每核缓冲区大小 |
| `flush_threshold_bytes` | 刷出阈值 |
| `flush_interval_ms` | 定时刷出周期，`0` 表示禁用周期 flush |
| `dict_ref` | 当前字典版本、校验算法、校验值与路径 |
| `enable_mask` | 事件使能掩码 |
| `sampling_policy` | 采样策略 |
| `channel_cfg` | 文件 / 串口 / 网络通道配置，支持 `target_count`、`targets[3]`、`retry_limit`、`retry_backoff_ms`；`target_count=0` 时兼容 legacy 单目标字段 |

### 8.2 分析端配置项

| 配置项 | 说明 |
|---|---|
| `align_policy` | 对齐策略和降级策略 |
| `lod_bucket_rules` | `LOD` 桶宽规则 |
| `query_page_size` | 事件表分页大小 |
| `event_cursor_mode` | 固定为 `sort_key` 或 `ts+core_id+seq` 三元游标 |
| `metric_window_defaults` | 默认统计窗口 |
| `alert_thresholds` | 告警阈值 |
| `cache_budget_mb` | 时间线 / 任务状态视图 / 事件表共享缓存预算 |
| `analysis_worker_count` | 后台查询与导出线程池并发数 |
| `serialization_batch_size` | `CSV/JSONL` 批量序列化块大小 |
| `export_defaults` | 导出默认格式和路径 |

### 8.3 非功能设计抓手

| 非功能目标 | 设计抓手 | 落地约束 |
|---|---|---|
| `NFR-PERF-03` 首屏 `< 10s` | `IndexBundle` 预构建时间摘要、`LoadPreview / ReadinessState`、`viz_LoadDatasetAsync` 分阶段加载、`Query Worker Pool` 独立执行 | 首屏路径禁止等待全量事件表物化；`stage` 必须可观测 |
| `NFR-PERF-04` 内存 `< 4GB` | `cache_budget_mb` 上限、事件页窗口化物化、`TaskStateViewModel` 与时间线分离缓存 | 超预算时按 `LRU` 回收，禁止无限累积视图副本 |
| `NFR-PERF-05` 时间线/状态视图 `>= 30 FPS` | 图块化渲染、行虚拟化、状态带预聚合、同帧增量刷新 | UI 主线程单帧预算按 `16ms` 控制，重查询放后台池 |
| `NFR-PERF-06` 导出 SLA | `snapshot_id` 冻结、顺序扫描索引、`serialization_batch_size` 批量写出、流式校验和计算 | 导出线程与交互线程隔离，清单校验不得二次全量扫描 |
| `NFR-CONS-02` 稳定排序一致性 | `RULE-stable_sort`、`EventCursor`、导出 `sort_key` 回写 | 事件表、导出、复现必须共享同一排序键 |
| `NFR-CONS-04` 复现一致性 | `AnalysisContext` 持久化边界、`dict_ref/schema_ref`、`CompareScope` 固化 | 复现加载时必须校验引用哈希并恢复正式上下文 |

## 9. V 模型验证映射与测试设计

### 9.1 详细设计与右侧验证活动映射

| 详细设计对象 | 主要验证活动 | 说明 |
|---|---|---|
| 结构体、状态机、编码器 | 单元测试 | 验证局部逻辑和边界条件 |
| 模块接口、数据契约、异常传播 | 集成测试 | 验证跨模块协同正确性 |
| 闭环流程、性能与稳定性 | 系统测试 | 验证端到端能力 |
| 目标达成与复核能力 | 验收测试 | 验证业务目标 |

### 9.2 模块测试映射

| 模块 | 单元测试重点 | 集成 / 系统 / 验收用例 |
|---|---|---|
| 采集模块 | 环形缓冲、`reserve-write-commit`、过滤采样 | `TC-COL-01~08`、`NFR-PERF-01~02` |
| 格式模块 | 头布局、`EventID`、`TLV`、CRC、兼容跳读 | `TC-FMT-01~06`、`NFR-COMPAT-01` |
| 解析重建模块 | 对齐、排序、状态机、索引 | `TC-PRS-01~09`、`NFR-CONS-01~03` |
| 统计诊断模块 | 指标口径、热点、阈值、证据链、对比计算 | `TC-MET-01~08`、`TC-CMP-02`、`NFR-CONS-05` |
| 可视化与导出模块 | `AnalysisContext`、视图联动、回放、导出、复现 | `TC-VIZ-01~07`、`TC-RPY-01~03`、`TC-CMP-01~03`、`TC-EXP-01~04`、`TC-RPR-01~02` |

### 9.3 个人自测清单

1. `metric_Ingest` 入口口径：统计层仅通过 `metric_Ingest(rebuild_bundle)` 接入，确认不存在旧的 `event_stream + state_seq + exec_slices` 私有接线。
2. 关键事件闭环：围绕 `TASK_READY / TASK_BLOCK / TASK_WAKEUP / CTX_SWITCH / IRQ_ENTER / IRQ_EXIT / LOSS / OVERFLOW` 核对字段、排序、证据链和时间边界是否闭合。
3. 状态重建闭环：检查 `TaskStateSeg`、`ExecSlice`、`ResourceGraph`、`IrqSpan` 是否能由同一事件集稳定重建，并与 `capability_flags` 的降级声明一致。
4. 离线 / 在线一致：同一数据分别走离线文件与在线增量链路，对比 `RebuildBundle`、指标结果、不可信窗口和导出结果是否一致。
5. Windows / Linux 一致：同一数据集在 Windows 与 Linux 上复跑，核对稳定排序、状态重建、统计摘要和导出结果是否一致。
6. 回放 / 对比 / 复现：逐事件回放、双基线对比、导出包重开三条链路都要能回到同一 `EvidenceRef`，并保持同一统计结论。
7. 异常窗口传播：人为注入 `crc_fail / seq_gap / truncate / dict_mismatch / align_fail / open_relation`，确认 `UntrustedWindow` 能传播到重建、统计、告警、可视化与导出。

## 10. 补齐接口与实现注意事项

### 10.1 本次详细设计补齐的缺失接口

相较 `功能设计.docx`，本详细设计补齐以下缺失或未展开的接口能力：

1. `metric_Compare`：双基线同口径差异计算。
2. `replay_*`：回放初始化、播放、暂停、步进、定位、状态查询。
3. `cmp_*`：双基线载入、范围设置、差异摘要 / 明细查询、辅助视图联动。
4. `repro_*`：复现包打开、上下文恢复、角色装载。
5. `viz_CreateBookmark / viz_ListBookmarks / viz_ApplyBookmark / viz_DeleteBookmark`：用于覆盖书签能力。
6. `viz_QueryTaskStates`：为任务状态视图提供独立查询契约。
7. `IndexBundle / TaskStateQuery / CompareScope / EventPage / DiffSummary / DiffDetail / PlaybackState`：将仅命名对象补齐为正式结构或最小合同边界。

### 10.2 实现注意事项

1. 补齐接口不得绕开统一数据基线，不得新增私有结果模型。
2. Python Qt 端必须以 `ContextStore` 作为唯一上下文入口，禁止视图之间直接写状态。
3. 对比、回放、导出、复现都必须复用单基线查询和证据链机制。
4. 后台任务必须支持取消、进度汇报和结果过期抑制。

### 10.3 本轮评审闭环矩阵

| 评审关注点 | 设计落点 | 收敛结果 |
|---|---|---|
| `CompareScope` 正式结构化 | `OBJ-CompareScope`、`IF-cmp_SetScope`、`FLOW-compare / FLOW-export / FLOW-repro` | 比较范围统一为正式结构，接口输入输出与导出 / 复现共用同一契约 |
| `run_id? / support_level` 入约 | `GlobalHeader`、`Alert` | 运行追踪字段和告警支持度进入正式外部合同，避免与 runtime 继续漂移 |
| `metric_Ingest` 接完整重建结果 | `OBJ-RebuildBundle`、`IF-metric_Ingest`、`RULE-metric_ingest_bundle` | 指标层只接收完整结果包并复用能力位；需求 / 概要 / 自测统一按该入口实施 |
| 索引与任务状态查询边界收紧 | `OBJ-IndexBundle`、`OBJ-TaskStateQuery`、`IF-viz_QueryTaskStates`、`idx_Build(results)` | 索引结果与任务状态查询对象获得正式 owner 和最小合同边界 |
| 事件表分页游标闭合 | `OBJ-EventCursor`、`OBJ-EventPage`、`RULE-stable_sort`、`IF-viz_QueryEventTable` | 分页游标固定与稳定排序同口径 |
| 任务状态视图独立建模 | `OBJ-TaskStateViewModel`、`IF-viz_QueryTaskStates` | 状态视图拥有独立查询和缓存契约，不依赖时间线私有 DTO |
| `MVP` 必选事件目录 | `RULE-mvp_event_catalog`、`EventDictionary`、`OBJ-RebuildBundle` | 采集、解析、重建、测试共享同一事件目录 |
| `MVP` 关键事件字段冻结表 | `ANNEX-mvp_event_field_freeze`、`EventDictionary`、`RecordCodec` | `collector / codec / parser` 共享字段名、必填性、类型、语义和兼容策略 |
| `job_id / instance_id` 语义边界 | `UnifiedEvent`、`ExecSlice`、`TaskStateSeg`、`OBJ-RebuildBundle` | 可选字段透传并通过能力位控制实例级指标降级 |
| `AnalysisContext` 持久化边界 | `OBJ-AnalysisContext`、`RULE-context_persist` | 可持久化字段与临时展示态显式分离 |
| `dict_ref / schema_ref` 独立校验 | `OBJ-ExportMeta`、`OBJ-ManifestEntry`、`IF-export_WritePackage`、`IF-repro_OpenPackage` | 导出包脱离界面仍可校验并支持二次分析 |
| 采集分段 / 滚动写入规则 | `RULE-segment_rollover` | 切段条件、命名、连续性、头 / 字典重复策略明确 |
| DTO 最小字段闭合 | `OBJ-IndexBundle`、`OBJ-PlaybackState`、`OBJ-TaskStateQuery`、`OBJ-EventPage`、`OBJ-DiffSummary`、`OBJ-DiffDetail` | 仅命名对象全部补成最小字段表或最小合同边界 |
| 9.3 自测改为个人清单 | `9.3`、`RULE-metric_ingest_bundle`、`UntrustedWindow` | 自测聚焦闭环、一致性、回放 / 对比 / 复现与异常传播，不再堆流程性描述 |
| 量化 `NFR` 设计抓手 | `8.3 非功能设计抓手`、`RULE-stable_sort`、`RULE-context_persist` | 首屏、内存、帧率、导出 `SLA` 明确映射到索引、缓存、线程池、序列化策略 |
| 追踪引用稳定化 | `1.5`、本矩阵 | 后续评审与测试优先引用 `OBJ / IF / RULE / FLOW` 标识，不再依赖章节号 |

## 11. 结论

本详细设计在不突破需求和概要设计边界的前提下，将系统细化为可直接编码实现的五大模块、统一数据契约、稳定接口组、线程模型和异常传播规则，并补齐了 `功能设计.docx` 中未落完的回放、对比、复现和书签相关接口。按照本设计实施后，系统可形成“采集 -> 解析 -> 重建 -> 统计 -> 展示 -> 回放 -> 对比 -> 导出 -> 复现”的完整 `MVP` 闭环，并可由右侧测试活动逐层验证。
