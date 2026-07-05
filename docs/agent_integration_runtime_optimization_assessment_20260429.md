# 接入 Agent 以提升运行速率并降低 Runtime 的项目评估与实施建议

生成日期：2026-04-29

## 1. 结论摘要

经审阅 `doc/` 下专利导向需求、概要设计、详细设计、合同冻结附件，以及 `realization/` 下实现代码、formal evidence、性能报告与跨平台验证材料，当前项目已经具备较完整的证据闭包导出主链路，核心技术路线是“依赖侧车索引 + 有界证据闭包 + 预算前置 + 可审计降级”。

项目可以通过接入 agent 提高工程运行效率，但这里的 agent 不应理解为运行时接入 LLM 或非确定性智能体，而应理解为确定性的后台 worker / service agent，用于承担 sidecar 索引、导出写包、解析、formal 批处理调度等可隔离任务。

总体判断如下：

1. 嵌入式采集端不建议接入复杂 agent。collector 已具备 per-core ring buffer、异步 flush worker、过滤与采样机制，热路径应继续保持确定、低抖动、低开销。
2. 桌面分析与 evidence export 链路适合接入确定性 agent。当前主要耗时和内存压力集中在 `1GB` formal 输入下的 sidecar 构建/校验、JSONL 整扫、SQLite index 构建/复用、JSON 包写出与 formal 批量验证。
3. 最优先的优化方向是 `SidecarIndexAgent`。它应在 sidecar build 后生成并持久化可复用索引，使后续 Mode-B evidence export 避免重复整扫 7GB 到 9GB 级 `dependency_sidecar.jsonl`。
4. 不建议把 LLM agent 放入 evidence export 的运行时证明链路。该链路涉及专利证明、跨平台一致性、proof hash、fail-closed 语义和合同冻结，必须保持可复验、确定性和可审计。

## 2. 当前项目情况

### 2.1 项目结构与主链路

`realization/README.md` 已明确代码结构：

1. `collector/`：C++17 collector library、host simulator、collector checks。
2. `parser/`：decode、verify、align、rebuild、index pipeline。
3. `metric/`：metrics、alerts、diagnosis、compare。
4. `desktop/`：CLI-facing service layer 与最小 GUI shell。
5. `tool/`：formal、acceptance、desktop runtime、collector perf 等验证脚本。
6. `docs/`：traceability、formal evidence、性能基线、验收材料。

项目的专利方向已经从普通截取导出收敛为：

1. sidecar 先行。
2. seed 解析。
3. 有界闭包扩张。
4. budget-before-read。
5. window read。
6. proof digest。
7. exact / bounded / degraded 三态 finalize。
8. evidence package 可重开、可审计、可跨平台比较。

### 2.2 已完成能力

根据 `realization/docs/evidence_export_subsystem_development_plan_20260412.md`、`realization/docs/专利10_4正式闭环与剩余验证执行文档_20260416.md`、`realization/docs/result/patent_improvement_metrics_20260429.md`，当前已经完成或具备如下能力：

1. Mode-B standalone two-pass 路径已经落地，允许先装载外部 sidecar/manifest，再执行闭包导出。
2. sidecar 来源已经扩展到 `ref_index / exec_slices / task_states / resource_graph / alerts / diagnoses / anchors / analysis_context`。
3. sidecar validate 已覆盖 `snapshot_id / trace_checksum / dictionary_checksum / schema checksum / entry checksum` 等合同检查。
4. evidence 主路径具备候选窗口规划与局部读取链路，`scan_count / seek_count / window_span_total` 等收益指标来自实际读取结果。
5. A/B/C formal proof groups 已形成证据：
   - A 组：控制面先行，证明 evidence 主路径避免全局重扫。
   - B 组：预算前冻结，证明超预算前置拒绝，不读后再判。
   - C 组：降级可审计，证明 sidecar mismatch、cycle inflation、corrupt segment、I/O guard 等异常可生成合法最小证据包。
6. Windows/Linux parity 已通过，A/B/C 三组 mandatory field set 一致，`metric_diff_count=0`。
7. 当前 final validation index 显示 global formal close 已关闭，open blocker 为 0。

### 2.3 当前可宣称结果

根据 `realization/docs/result/patent_improvement_metrics_20260429.md`，当前项目相对一般“全量 trace 扫描/截取导出/异常人工解释”的方法，已有 formal 证据支撑以下结论：

1. 证据导出体量显著降低。
2. 全局重扫/全量扫描依赖显著降低。
3. 预算控制前置，避免超预算后再读。
4. 异常可审计闭环。
5. Linux/Windows 证据一致性。

关键量化数据如下：

| 指标 | 当前结果 | 说明 |
|---|---:|---|
| formal 输入大小 | 1,118,295,183B | real external dense 1GB 输入 |
| 核心闭包证据大小 | 198B | A 组 `proof_digest.bytes_emitted=198` |
| evidence 输出事件数 | 1 event | A 组 `events_emitted=1` |
| Linux legacy catalog chunk | 97,411 | legacy full prescan 基准 |
| A 组 evidence scan/seek | 42 / 42 | 明显低于 full prescan |
| A 组 sidecar lookup | 257 | 三次 run 均一致 |
| B 组预算场景 | 19/19 | depth/event/byte/rho 四类预算 |
| B 组读前拒绝 | 19/19 | `reject_round_has_read=false` |
| C 组降级场景 | 4/4 | 均生成合法最小证据包 |
| A/B/C parity | pass | mandatory field set 一致，metric diff 为 0 |

### 2.4 当前不宜宣称的结果

当前不宜宣称端到端 wall-clock 速度已经提升。

已有数据表明，当前 formal evidence export 的 wall-clock 耗时仍然较高：

| 项目 | Linux | Windows |
|---|---:|---:|
| A 组三次 evidence export runtime | 1659.216s / 1701.018s / 1663.562s | 1948.177s / 1688.425s / 1717.460s |
| A 组平均 runtime | 1674.598s | 1784.688s |
| B 组 runtime total | 4013.532s | 4503.900s |
| C 组 runtime total | 72.731s | 5.380s |
| Windows clipped baseline runtime | 不适用 | 67.013s |

尤其需要注意：Windows clipped baseline runtime 为 67.013s，而 Windows A 组 evidence 平均 runtime 为 1784.688s。当前 evidence 路径在证据体量、扫描/seek 数、审计能力和一致性方面明显优于 clipped baseline，但端到端耗时还没有形成同合同口径下的速度收益证明。

因此，agent 接入目标应定义为：在不破坏 proof contract、proof hash、三态 finalize 和 fail-closed 语义的前提下，降低 formal evidence export 的重复 I/O、重复 JSONL 解析、重复索引构建、写包序列化和批量验证总耗时。

## 3. 现有运行机制与瓶颈分析

### 3.1 现有后台调度机制

`desktop/services.py` 中已有 `BackgroundJobManager`，并按 job kind 分配到不同 executor group：

1. `input`
2. `parse_rebuild`
3. `query`
4. `sidecar`
5. `evidence_query`
6. `export`
7. `default`

这说明项目已经具备基础 lane 化调度能力，但它目前主要是线程池模型：

1. `parse_rebuild` 使用 `max_workers=2`。
2. `query` 使用 `max_workers=2`。
3. `sidecar` 使用 `max_workers=1`。
4. `export` 使用 `max_workers=1`。

该设计适合 GUI 非阻塞和任务状态管理，但对 CPU 密集型 JSON 解析、JSON dump、sidecar validate、SQLite build、Python 对象序列化帮助有限。Python GIL、单进程内存峰值、整文件 JSONL 扫描仍然会限制 runtime。

### 3.2 sidecar 是当前最核心瓶颈

A 组 formal sidecar 数据规模很大：

| 平台 | sidecar build runtime | sidecar bytes |
|---|---:|---:|
| Linux | 841.354s | 9,302,986,366B |
| Windows | 492.958s | 7,828,103,808B |

当前 Mode-B evidence export 会执行以下步骤：

1. load sidecar manifest。
2. validate manifest metadata。
3. validate dependency sidecar stream。
4. build/open sidecar SQLite index。
5. 每轮闭包通过 selector 查询候选边。

其中 `validate_dependency_sidecar_stream(...)` 会逐行读取 JSONL、计算 checksum、解析每条边、校验 snapshot/trace checksum。对 7GB 到 9GB 文件来说，这一步本身就会形成很高的重复 I/O 和 CPU 负担。

### 3.3 JSON 与包写出存在明显优化空间

`spec/io.py` 中 `json_dump(...)` 默认执行：

1. `serialize(data)` 递归转换。
2. `json.dump(..., indent=2, sort_keys=True)`。

这对小型 control payload 友好，但对 full/clipped/rebuild/result 类大对象成本较高。历史 performance artifact 中也出现过 `json.dump` 导致 MemoryError 的记录，说明大包写出和大对象序列化是明确风险点。

### 3.4 parser/load 链路仍有串行扫描成本

`parser/pipeline.py` 中 trace 文件读取主要按 chunk 顺序 feed decoder。`load_dataset_with_timings(...)` 先 verify，再 align/rebuild/index。当前已经支持 `materialize_event_stream=False`、deferred/minimal index 等优化，但 1GB formal 输入下 parse/load 仍是系统性成本来源。

这部分不适合简单加线程，需要更明确的进程隔离、native decoder、chunk catalog 复用或预索引策略。

### 3.5 collector 热路径不应作为 agent 优先接入点

C++ collector 已经具备：

1. per-core ring buffer。
2. record path guard。
3. 异步 flush worker。
4. flush threshold 与 interval。
5. filter 与 sampling。
6. segment rotate。

collector perf baseline 显示 Linux/Windows 均达到百万级到千万级 events/s。嵌入式端更看重确定性、低延迟和低抖动，在热路径接入复杂 agent 容易引入不可控调度成本。因此 collector 侧只建议继续使用现有 flush worker / sampling / filter 机制，不引入新的智能运行时 agent。

## 4. Agent 接入原则

### 4.1 Agent 的定义

本文中的 agent 指确定性后台 worker 或 service：

1. 有明确输入、输出、状态机和错误码。
2. 输出可校验、可复算、可审计。
3. 不改变 proof hash 规则。
4. 不改变 exact/bounded/degraded 判定语义。
5. 不在失败时静默回退到 legacy full scan。
6. 可通过 manifest、checksum、fingerprint、schema contract 验证。

不建议把 LLM agent 接入运行时 evidence export 主链路。LLM 可以用于离线审计、报告生成、异常解释草稿，但不能参与 proof bundle 的事实生成或自动判定。

### 4.2 接入边界

可以接入：

1. sidecar index 构建与复用。
2. sidecar manifest 和 index ticket 校验。
3. 大 JSON/JSONL 流式写出。
4. parse/rebuild 子进程隔离。
5. formal suite 批处理调度。
6. telemetry 聚合与报告生成。

不建议接入：

1. RTOS trace hook 热路径。
2. proof hash 生成规则。
3. budget-before-read 判定规则。
4. fail-closed 降级判定。
5. sidecar edge 语义推断。
6. 跨平台 parity 必须一致的合同字段自动变更。

## 5. 推荐 Agent 方案

### 5.1 SidecarIndexAgent

优先级：P0

目标：避免每次 Mode-B evidence export 重复整扫 7GB 到 9GB 的 `dependency_sidecar.jsonl`。

职责：

1. 在 sidecar-build 完成后立即生成 SQLite index。
2. 生成 `SidecarIndexTicket`（早期 `sidecar_index_manifest.json` 的等价替代合同，实际 artifact 为 `*.ticket.json`）。
3. 记录以下绑定字段：
   - `sidecar_path`
   - `sidecar_checksum`
   - `sidecar_bytes`
   - `file_fingerprint`
   - `row_count`
   - `snapshot_id`
   - `trace_checksum`
   - `dictionary_checksum`
   - `schema_version`
   - `created_at`
   - `index_build_seconds`
4. evidence export 前优先校验 index ticket。
5. ticket 与文件 fingerprint 一致时，跳过 dependency sidecar 全量 stream validation。
6. ticket 失配时 fail-closed 或显式触发重建，不静默回退到大文件 stream scan。
7. 将 `sidecar_index_open_seconds`、`sidecar_index_reused`、`sidecar_index_rebuilt` 写入 telemetry。

需要修改的模块：

1. `parser/evidence_sidecar_index.py`
2. `parser/evidence_sidecar.py`
3. `desktop/evidence_export.py`
4. `desktop/services.py`
5. `tool/build_evidence_proof_archive.py`
6. `tool/run_formal_a_windows.py`
7. `tool/run_formal_b_windows.py`
8. `tool/run_patent_formal_soak.py`
9. `tests/python/test_evidence_sidecar.py`
10. `tests/python/test_evidence_proof_archive.py`

预期收益：

1. A 组重复 export 中 sidecar validate/index 阶段耗时显著下降。
2. 避免重复读取 7GB 到 9GB JSONL。
3. 降低 Python JSON 解析 CPU 开销。
4. 降低 I/O 负载。
5. 改善 formal A/B 组 runtime。

风险：

1. 必须保证 ticket 不会掩盖 sidecar 文件变化。
2. 必须保留 fail-closed 语义。
3. 必须保证 Linux/Windows SQLite 查询排序一致。
4. proof digest 中需要区分 `sidecar_lookup_count` 和 `sidecar_index_lookup_count`，避免统计口径漂移。

### 5.2 ExportWriteAgent

优先级：P1

目标：降低 package 写出过程中的 JSON 序列化、内存峰值和大对象复制。

职责：

1. 将大列表 JSON 输出改为流式 writer。
2. 对 rebuild/result/context 中可 JSONL 化的数据优先使用 JSONL。
3. 对 manifest checksum 计算使用边写边 hash 或写后 chunk hash。
4. 减少 `serialize(data)` 对大对象的全量递归复制。
5. 为 full/clipped/evidence 三类 export 分别输出 write timing。
6. 对大包写出提供 memory guard 和 blocker artifact。

需要修改的模块：

1. `spec/io.py`
2. `desktop/package_writer.py`
3. `desktop/services.py`
4. `desktop/evidence_export.py`
5. `tests/python/test_evidence_contract_assets_simtool.py`
6. `tests/python/test_repro_evidence.py`
7. `tests/python/test_desktop.py`

预期收益：

1. 降低 full/clipped export 的 MemoryError 风险。
2. 降低 write/rebuild、write/result、write/control 阶段耗时。
3. 降低峰值 RSS。
4. 改善 Windows clipped baseline 与 evidence package 的写包性能。

风险：

1. manifest checksum 不能变化失控。
2. schema contract 需要同步更新或保持兼容。
3. repro open 必须支持新写法。

### 5.3 ParserProcessAgent

优先级：P2

目标：将 parse/rebuild 从 GUI/服务进程中隔离出来，降低主进程峰值内存，并为后续多进程或 native decoder 优化铺路。

职责：

1. parse/rebuild 在独立子进程中运行。
2. 子进程输出 compact artifact、timings、memory snapshots。
3. 主进程只接收 artifact handle 或 package path。
4. 子进程异常时生成明确 error code 和 blocker。
5. 后续可演进为 chunk catalog cache 或 C++ decoder worker。

需要修改的模块：

1. `parser/pipeline.py`
2. `desktop/services.py`
3. `desktop/repository.py`
4. `tool/run_acceptance_baseline.py`
5. `tool/run_desktop_runtime_report.py`
6. `tests/python/test_pipeline.py`
7. `tests/python/test_desktop_runtime.py`

预期收益：

1. 降低主进程长期 RSS。
2. 避免一次 formal run 后内存不归还影响后续 run。
3. 提高长时 soak 稳定性。
4. 为多进程 parse 或 native decoder 提供接口边界。

风险：

1. IPC 和 artifact 序列化可能抵消小输入收益。
2. 需要明确 dataset repository 生命周期。
3. Windows/Linux 子进程行为需 parity 验证。

### 5.4 FormalSuiteSchedulerAgent

优先级：P2

目标：缩短 A/B/C formal suite 批处理总 wall-clock，不改变单次 evidence export runtime。

职责：

1. 按 proof group、case、平台拆分任务。
2. 自动复用 input qualification、sidecar、index、seed selection。
3. 控制并发度，避免磁盘 I/O 饱和。
4. 汇总 formal summary、parity report、readiness index。
5. 失败时保留 raw progress 和 blocker。

需要修改的模块：

1. `tool/run_formal_windows_suite.py`
2. `tool/run_formal_a_windows.py`
3. `tool/run_formal_b_windows.py`
4. `tool/run_formal_c_windows.py`
5. `tool/build_windows_formal_readiness.py`
6. `tool/build_evidence_proof_archive.py`

预期收益：

1. 缩短整套验证等待时间。
2. 减少人工手动串行执行成本。
3. 提升 regression 与 formal packaging 的可重复性。

风险：

1. 并发过高会导致 I/O 争用，反而增加单 case runtime。
2. summary 合并必须保留原始路径和原始命令。

### 5.5 TelemetryReportAgent

优先级：P3

目标：统一 runtime、RSS、scan/seek、sidecar/index、write timings 的采集和汇总，为后续宣称速度收益提供同合同口径数据。

职责：

1. 统一输出 `runtime_seconds`、`peak_rss_mb`、`sidecar_validate_seconds`、`sidecar_index_seconds`、`write_seconds`。
2. 区分 sidecar build、index build、index open、closure、window read、package write。
3. 输出 legacy / clipped / evidence 同合同 benchmark。
4. 生成对外可用的 improvement metrics。

需要修改的模块：

1. `parser/telemetry.py`
2. `desktop/evidence_export.py`
3. `desktop/services.py`
4. `tool/run_acceptance_baseline.py`
5. `tool/build_evidence_proof_archive.py`
6. `tool/summarize_validation_status.py`

预期收益：

1. 消除“速度是否提升”口径不清的问题。
2. 支撑后续专利材料中更严谨的工程收益声明。
3. 便于定位 regression。

## 6. 推荐实施路线

### 阶段 0：冻结 agent 接入口合同

目标：先定义 contract，不直接改大逻辑。

工作：

1. 不新增 `sidecar_index_manifest` schema，冻结 `sidecar_index_ticket.schema.json`；文档中的 `sidecar_index_manifest` 是历史名/别名。
2. 定义 agent 状态机：
   - `AGENT-queued`
   - `AGENT-validating_input`
   - `AGENT-running`
   - `AGENT-artifact_written`
   - `AGENT-completed`
   - `AGENT-failed`
3. 定义错误码：
   - `SIDECAR_INDEX_MISSING`
   - `SIDECAR_INDEX_STALE`
   - `SIDECAR_INDEX_MISMATCH`
   - `SIDECAR_INDEX_BUILD_FAILED`
   - `AGENT_TIMEOUT`
   - `AGENT_CANCELLED`
4. 定义 telemetry 字段：
   - `agent_kind`
   - `agent_runtime_seconds`
   - `agent_peak_rss_mb`
   - `artifact_reused`
   - `artifact_rebuilt`
   - `input_bytes_scanned`
   - `output_bytes_written`

验收：

1. schema tests 通过。
2. 不影响现有 evidence package。
3. 不改变 proof hash。

### 阶段 1：实现 SidecarIndexAgent

目标：解决最大重复成本。

工作：

1. sidecar-build 后自动 build SQLite index。
2. 写出 index ticket。
3. evidence export 优先 open ticket。
4. fingerprint/checksum 一致时跳过 dependency sidecar 全量 validate。
5. ticket 失配时 fail-closed 或显式重建。
6. formal A 组三次重复 export 对比：
   - 无 index ticket。
   - 首次 build index。
   - 后两次 reuse index。

验收：

1. A 组 proof hash 不变。
2. `scan_count / seek_count / sidecar_lookup_count` 不回退。
3. Linux/Windows parity 仍为 pass。
4. sidecar validate/index 阶段耗时下降。
5. 不发生 full global rescan fallback。

### 阶段 2：实现 ExportWriteAgent

目标：降低写包和内存峰值。

工作：

1. 引入流式 JSON writer。
2. 对大数组输出避免中间 list。
3. package manifest checksum 保持一致或通过合同升级说明变化。
4. 将 write timings 分解到 telemetry。

验收：

1. evidence package contract tests 通过。
2. repro open tests 通过。
3. Windows/Linux normalized package compare 通过。
4. 大包写出无 MemoryError。
5. write 阶段 RSS 明显下降。

### 阶段 3：实现 ParserProcessAgent

目标：隔离 parse/rebuild 内存与异常。

工作：

1. 子进程执行 parse/rebuild。
2. 主进程只接收 artifact metadata。
3. formal 和 desktop runtime 使用统一入口。

验收：

1. `tests/python/test_pipeline.py` 通过。
2. `tests/python/test_desktop_runtime.py` 通过。
3. long soak 中 RSS 不持续累积。

### 阶段 4：实现 FormalSuiteSchedulerAgent 与 TelemetryReportAgent

目标：缩短验证批次耗时，并形成可对外声明的数据口径。

工作：

1. A/B/C formal case 并发调度。
2. 自动收集 runtime/RSS/sidecar/index/write 分解指标。
3. 输出同合同口径 benchmark：
   - legacy full-scan
   - clipped baseline
   - evidence no-index
   - evidence indexed
   - evidence indexed + streaming write

验收：

1. formal summary 自动生成。
2. parity report 自动生成。
3. final readiness index 自动更新。
4. 可判断是否可以开始宣称 wall-clock speedup。

## 7. 预期结果

### 7.1 可较高置信预期

1. sidecar 重复整扫显著减少。
2. Mode-B 重复 evidence export runtime 明显下降。
3. A/B formal suite 总耗时下降。
4. 大包 JSON 写出 MemoryError 风险下降。
5. 主进程长期 RSS 更稳定。
6. formal evidence 的 telemetry 更完整。

### 7.2 需要实测后才能宣称

以下结果不能提前承诺，需要通过同合同口径 benchmark 证明：

1. `1GB` bounded evidence export 是否能达到 `<=15s`。
2. `1GB` 全量导出是否能达到 `<=60s`。
3. evidence export 相对 clipped baseline 是否 wall-clock 更快。
4. 峰值内存是否能稳定低于 `<4GB`。

### 7.3 建议目标值

建议分阶段设置工程目标：

| 阶段 | 指标 | 当前观测 | 建议目标 |
|---|---:|---:|---:|
| SidecarIndexAgent | 重复 export 是否整扫 sidecar | 是，存在高成本 validate/index | 后续 run 不整扫 7GB 到 9GB JSONL |
| SidecarIndexAgent | A 组单次 runtime | Linux 平均 1674.598s，Windows 平均 1784.688s | 先降到 600s 以下，再继续优化 |
| ExportWriteAgent | 大 JSON dump 内存风险 | 曾出现 MemoryError 证据 | 大包写出不因 JSON dump OOM |
| ParserProcessAgent | 主进程 RSS | Linux proof digest 记录约 55GB 级 peak RSS | 先隔离进程，再逐步压到 8GB 以下，最终向 4GB 目标靠拢 |
| TelemetryReportAgent | 耗时分解 | 当前分解不足 | sidecar/index/closure/read/write 均有秒级分解 |

## 8. 需要用到什么

### 8.1 技术组件

1. Python 标准库：
   - `sqlite3`
   - `hashlib`
   - `json`
   - `multiprocessing`
   - `concurrent.futures`
   - `subprocess`
   - `pathlib`
2. 现有项目模块：
   - `parser.evidence_sidecar`
   - `parser.evidence_sidecar_index`
   - `parser.evidence_closure`
   - `desktop.evidence_export`
   - `desktop.package_writer`
   - `desktop.services`
   - `tool.run_formal_*`
3. SQLite：
   - 持久化 sidecar index。
   - 保持 `src_ref + rule_family` 查询索引。
   - 固化 metadata 与 fingerprint。
4. JSON/JSONL streaming writer：
   - 避免大对象全量 materialize。
   - 支持边写边 hash。
5. 进程隔离：
   - parse/rebuild worker。
   - export/write worker。
   - formal case runner。

### 8.2 测试与验证材料

需要继续使用和扩展以下测试：

1. `tests/python/test_evidence_sidecar.py`
2. `tests/python/test_evidence_closure.py`
3. `tests/python/test_evidence_window.py`
4. `tests/python/test_evidence_proof_archive.py`
5. `tests/python/test_repro_evidence.py`
6. `tests/python/test_desktop.py`
7. `tests/python/test_desktop_runtime.py`
8. `tests/python/test_pipeline.py`

需要继续保留以下 formal 输入和报告：

1. Linux A/B/C formal summary。
2. Windows A/B/C formal summary。
3. sidecar build report。
4. proof query report。
5. repro open report。
6. parity report。
7. final validation index。

### 8.3 运行环境

建议沿用当前性能验收环境基线：

1. Intel Core i7-12700H 或同等级 CPU。
2. 32GB RAM。
3. SSD。
4. Ubuntu 22.04 / Windows 11。
5. Python 3.10+。
6. CMake/g++ 用于 collector 与 bench。
7. 足够磁盘空间：至少保留原始 1GB trace、7GB 到 9GB sidecar、SQLite index、A/B/C package 和 raw progress。

## 9. 风险与控制措施

| 风险 | 影响 | 控制措施 |
|---|---|---|
| index ticket 掩盖 sidecar 文件变化 | proof 不可信 | fingerprint、checksum、row_count、snapshot_id、trace_checksum 全绑定 |
| SQLite 查询排序不一致 | proof hash 漂移 | 查询后继续使用稳定排序键 |
| 跳过 stream validation 后漏检坏 sidecar | fail-closed 语义受损 | 只有 ticket 与 sidecar build 同源且 fingerprint 未变时才允许 fast path |
| agent 并发导致 I/O 饱和 | runtime 反而增加 | formal scheduler 限制并发度，按磁盘吞吐配置 |
| 流式写包改变 package contract | repro/parity 失败 | schema 先冻结，normalized package compare 后再启用 |
| 子进程隔离导致 artifact 生命周期混乱 | GUI/CLI 状态不一致 | repository 只保存 artifact handle 与 manifest，不共享可变对象 |
| LLM agent 进入证明链路 | 不可复验 | LLM 仅用于离线报告，不参与 proof data 生成 |

## 10. 最终建议

建议按 P0 到 P3 顺序推进：

1. P0：先做 `SidecarIndexAgent`。这是当前最可能显著降低 formal evidence export runtime 的点，也是最符合专利路线的优化，因为它强化了“控制面索引复用”而不是回退到全量扫描。
2. P1：再做 `ExportWriteAgent`。解决 JSON dump、包写出、MemoryError 和 RSS 问题。
3. P2：再做 `ParserProcessAgent` 与 `FormalSuiteSchedulerAgent`。前者降低主进程内存风险，后者降低整体验证等待时间。
4. P3：补齐 `TelemetryReportAgent`。形成同合同口径 benchmark，决定后续能否对外宣称 wall-clock speedup。

短期对外口径建议保持保守：

> 当前项目已经证明 evidence package 体量、scan/seek 数、预算前置拒绝、异常审计闭环和跨平台一致性方面有明确提升；下一阶段通过确定性 worker agent 重点减少 sidecar/index/write/parse 的重复成本，在不破坏 proof contract 的前提下争取形成可量化的 runtime 降低证据。

不建议当前直接宣称：

> 接入 agent 后必然达到 `1GB <= 15s` 或 `wall-clock 提升 X 倍`。

该类结论必须等 `SidecarIndexAgent + ExportWriteAgent` 完成后，通过 legacy / clipped / evidence no-index / evidence indexed / evidence indexed streaming-write 五组同合同 benchmark 实测确认。
