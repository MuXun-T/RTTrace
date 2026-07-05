# 证据闭包导出子系统开发计划

日期：2026-04-12  
对应专利方向：`基于依赖侧车索引的有界证据闭包导出`

## 1. 文档依据

本计划基于以下文档与当前实现基线收敛：

1. `doc/需求规格说明书_专利导向_基于依赖侧车索引的有界证据闭包导出_v1.0.md`
2. `doc/概要设计说明书_专利导向_基于依赖侧车索引的有界证据闭包导出_v1.0.md`
3. `doc/详细设计说明书_专利导向_基于依赖侧车索引的有界证据闭包导出_v1.0.md`
4. `doc/simtool_专利导向评审与重构路线图_20260407.md`
5. `doc/plan/patent_plan_revised_v2.docx`
6. `doc/plan/patent_plan_trimmed_v3.docx`
7. `doc/plan/基于依赖侧车索引的有界证据闭包导出-专利调研.docx`

## 2. 当前实现定位与差距结论

当前 `realization` 已经完成一条可运行的 evidence package 主路径，具备以下基础：

1. `export_Evidence()` / `export_WriteEvidencePackage()` 已接入 `desktop/services.py`
2. evidence package 已写出 `control/dependency_sidecar.jsonl`、`frontier_snapshot.json`、`proof_digest.json`、`sidecar_manifest.json`
3. 已支持 `exact / bounded / degraded` 三态、基础 proof 校验与 fixture 读包
4. 已有最小闭包引擎、seed 解析、sidecar schema、repro 侧控制面打开能力

以下差距项反映的是 `2026-04-12` 制定本计划时的基线视角；其后续完成状态已在 `2.1 / 2.2` 中冻结回写：

1. `Mode-B: standalone two-pass` 入口与状态流已形成并在 `P1-1~P1-4` 迭代中持续收紧（见 `realization/docs/serial_iterations/group67~70`）；contract assets 与正式文档已在 `P1-5` 完成收口，并在 repo fixture 中冻结
2. sidecar 来源目前主要是事件序邻接与 `alert/diagnosis` 证据链，尚未覆盖 `task_states / exec_slices / resource_graph / anchors / analysis_context / ref_index` 的完整矩阵
3. “candidate edges -> window plan -> local read” 主链路已按 `P1-2` 推进并补齐命中对账与遥测口径；样例包与 contract assets tests 已在 `P1-5/P1-6` 完成冻结与门禁锁定
4. evidence 导出已接入候选窗口局部读取与命中对账，不再仅依赖已物化 `bundle.event_stream` 选事件；相关冻结夹具与门禁测试已形成基线
5. sidecar 一致性校验不再仅依赖 repro 打开侧，导出侧已具备校验/降级链路；当前已与正式文档与 contract assets 对齐为同一口径

结论：

`P1-5` 及其后续“操作性拆分”阶段已完成收口；截至 `2026-04-15`，整改清单内的 `P2-1 ~ P2-5` 也已完成冻结回写与对外口径收口。当前剩余工作不再属于本计划内阶段，而是范围外的 external formal validation 补证，例如真实 external dense `1GB` 输入、跨平台 parity 与长时 soak artifact 的持续补充。

### 2.1 状态回写（截至 2026-04-15）

截至 `2026-04-15`，本计划中与 `P1` 收口相关的关键状态如下：

1. `P1-1 ~ P1-4` 已完成并形成回归基线（见 `realization/docs/serial_iterations/group67~70`）。
2. `P1-5` 已完成：固定 4 包矩阵（`Mode-A exact`、`Mode-B exact`、`bounded`、`degraded`）并对齐 contract assets tests 与正式文档口径（见 `realization/docs/serial_iterations/group71_worker03_execution.md`）。
3. 由于整改清单正文缺失 `P1-6/P1-7` 条目，本仓库以“操作性拆分”承接尾段资产冻结：repo fixture 已完成可消费性修复并冻结 `P1-3` 扩展字段（见 `realization/docs/serial_iterations/group72_worker03_execution.md`）。
4. `P2-1` 已完成：proof archive 已固定 evidence export 主链路 `1GB` prevalidation direct measured/blocker 双态结构，并完成全量 Python 回归（见 `realization/docs/serial_iterations/group74_worker03_execution.md`）。
5. `P2-2` 已完成：`peak_rss_mb / round_count / window_hit_rate / diagnosis_preservation_rate / proof_consumer_mode` 已形成 package/archive 可回收指标与回归门禁（见 `realization/docs/serial_iterations/group75_worker03_execution.md`）。
6. `P2-3` 已完成：`Mode-B` 六类 seed 成功路径、多 `rule_family` 组合与对象边扩张成功路径已固定为集成测试矩阵（见 `realization/docs/serial_iterations/group76_worker03_execution.md`）。
7. 整改清单内的 `P2-1 ~ P2-5` 已全部完成；对应执行证据见 `realization/docs/serial_iterations/group74_worker03_execution.md` ~ `realization/docs/serial_iterations/group78_worker03_execution.md`。当前剩余工作转为范围外补证：真实 external dense `1GB` formal close、Windows/Linux parity 与长时 soak artifact 的持续补充。

### 2.2 W0-W4 状态回写（截至 2026-04-15）

本计划中的 `W0 ~ W4` 为 `2026-04-12` 的原始实施基线。其执行状态截至 `2026-04-15` 已全部完成，保留原计划正文用于回溯，不再作为“待实施”条目阅读：

1. `W0` 已完成：合同冻结增量、`alerts/result_validity` 正式合同与进入后续实施的门禁已落地（见 `realization/docs/evidence_export_subsystem_development_plan_20260412.md:273`，以及 `realization/docs/serial_iterations/group64_worker03_execution.md`、`realization/docs/serial_iterations/group65_worker03_execution.md`、`realization/docs/serial_iterations/group66_worker03_execution.md`）。
2. `W1` 已完成：`Mode-B` standalone two-pass 入口、导出侧 sidecar 校验/降级链路与 seed 依赖裁剪已形成门禁（见 `realization/docs/serial_iterations/group67_worker03_execution.md`）。
3. `W2` 已完成：sidecar 来源覆盖已扩展到 `ref_index / exec_slices / task_states / resource_graph / alerts / diagnoses / anchors / analysis_context`，并通过 schema/sidecar 合同测试冻结（见 `realization/docs/serial_iterations/group65_worker03_execution.md` 与 `realization/tests/python/test_evidence_sidecar.py`）。
4. `W3` 已完成：candidate edges -> window plan -> local read 主链路、命中对账与 blocker/read telemetry 已形成稳定口径（见 `realization/docs/serial_iterations/group68_worker03_execution.md`）。
5. `W4` 已完成：proof/telemetry 合同、`result_validity` 下游消费链、4 包矩阵、fixture 冻结与 CLI 入口均已收口（见 `realization/docs/serial_iterations/group66_worker03_execution.md`、`realization/docs/serial_iterations/group69_worker03_execution.md`、`realization/docs/serial_iterations/group70_worker03_execution.md`、`realization/docs/serial_iterations/group71_worker03_execution.md`、`realization/docs/serial_iterations/group72_worker03_execution.md`、`realization/docs/serial_iterations/group73_worker03_execution.md`）。

## 3. 本轮开发目标

以下 `W0 ~ W4` 小节保留为 `2026-04-12` 的原始计划基线。其实现状态已在前述“状态回写”中冻结，当前阅读这些小节时应将其视为回溯性实施计划，而非仍待执行事项。

本轮目标不是重写整个导出体系，而是在保持现有 `full/clipped export` 不回退、现有 evidence package 基本合同不破坏的前提下，把 evidence 主路径从“最小可运行”推进到“更接近详细设计主实施例”的工程形态。

本轮冻结目标如下：

1. 为 evidence 导出增加 `Mode-B: standalone two-pass` 的正式请求入口，允许先装载外部 sidecar/manifest，再执行闭包导出
2. 扩展 sidecar 抽取覆盖到 `ref_index / exec_slices / task_states / resource_graph / alerts / diagnoses / anchors / analysis_context`
3. 新增 sidecar validator，使导出前即可执行 `snapshot_id / trace_checksum / dictionary_checksum / schema checksum / entry checksum` 校验，并在失配时 fail-closed
4. 把窗口规划改为真正面向 `candidate edges`，并补上按窗口对 trace 做局部读取、命中 `delta_refs`、回填 telemetry 的链路
5. 保持 package 合同与 repro 消费兼容，补足针对 standalone mode、sidecar 失配、窗口读取、扩张来源覆盖的自动化测试

补充说明：

本轮编码前必须先补一个“合同冻结增量”小步，不另开大阶段，但要明确修补当前实现仍与详细设计存在偏差的三项合同：

1. `EvidenceExportRequest` 增补 `embodiment_mode` 与 sidecar 装载输入
2. `result/result_validity.json` 从当前文件级声明提升到对象级声明
3. `result/alerts.json` 进入条件白名单，避免与详细设计的默认结果白名单冲突

## 4. 本轮不做

为保证本轮可交付，本计划明确以下内容不并入本次编码范围：

1. 不引入数据集级 full sidecar 打包或 sidecar 持久化服务平台
2. 不重写 GUI，只保持已有 evidence/repro 接口兼容
3. 不把 `rebuild/result` 白名单扩展到完整 subset 重算体系
4. 不重写现有 `full/clipped export` 路径

## 5. 工作流拆分

### W0 合同冻结增量

目标：在进入主编码前，把当前实现与详细设计之间仍未对齐的最小合同差异先冻结，避免边写边漂移。

实施项：

1. 扩充 `EvidenceExportRequest` 运行时合同，明确 `embodiment_mode`
2. 为 `Mode-B` 增加 `sidecar_source / sidecar_manifest_source` 或等价字段
3. 更新 `result_validity` schema 与写包结构，使其支持：
   - `object_kind`
   - `object_id`
   - `validity_scope`
   - `derivation_mode`
4. 冻结 `result/alerts.json` 的默认保留策略与 manifest/schema 写法
5. 评估并更新最小 fixture / 契约测试，确保文档、schema、样例三者一致
6. 形成进入 W1-W4 的门禁清单：
   - schema 已更新
   - `alerts/result_validity` 断言已更新
   - 最小 fixture 或 contract assets 已同步

完成判据：

1. 本轮后续编码不再依赖临时字段命名
2. `result_validity` 不再停留在文件级总声明
3. 计划中的 `Mode-B` 输入合同和结果白名单有明确落点
4. W1-W4 不会在旧 schema 上继续推进

### W1 控制面：standalone two-pass 入口与 sidecar 校验

目标：让 `Mode-B` 成为正式可执行路径，而不是文档口径。

实施项：

1. 扩展 `EvidenceExportRequest` 或 evidence job payload，显式表达 `embodiment_mode`
2. 支持 `sidecar_source` / `sidecar_manifest_source` 一类输入，允许 evidence 作业装载预生成 sidecar
3. 在 job 冻结阶段把外部 sidecar 的 `snapshot_id / trace_checksum` 固定到作业上下文
4. 在 `parser/evidence_sidecar.py` 中补 `sdg_Validate(...)` 与 sidecar 加载/裁切能力
5. 在 `desktop/evidence_export.py` 中按 `Mode-B / Mode-A` 分流：
   - `Mode-B`：装载并校验已持久化 sidecar，再裁切出本次 evidence job 工作集
   - `Mode-A`：从冻结分析对象构建 sidecar，再执行相同校验与后续闭包
6. `Mode-B` 的 evidence `snapshot_id` 以外部 sidecar manifest 为冻结基线，避免作业内部重新生成冲突 snapshot
7. validator 覆盖：
   - `snapshot_id`
   - `trace_checksum`
   - `dictionary_checksum`
   - `schema_checksums`
   - `entry_checksums`
8. 当校验失败且允许降级时，输出 `degraded + blocker_artifact`；不允许降级时直接失败返回
9. fail-closed 分支必须显式映射到 `SIDECAR_MISMATCH` 一类 halt/exception code，不允许只留日志

范围约束：

本轮 `Mode-B` 只要求支持“外部已持久化 sidecar + 当前 trace 源”的离线装载，不引入独立的 sidecar_build 服务或数据集级管理平面。

完成判据：

1. 导出侧而非仅 repro 侧能识别 `SIDECAR_MISMATCH`
2. `Mode-B` 与 `Mode-A` 都走统一的校验后闭包链路
3. package 里仍仅写当前 evidence job 工作集 sidecar
4. 外部 sidecar 校验语义与包内 sidecar 写出语义分离：前者绑定原始 trace，后者重绑定 package `events.trace`

### W2 控制面：sidecar 依赖抽取补全

目标：把当前 sidecar 从“事件邻接 + alert/diagnosis”提升到更接近详细设计 5.2.2 的覆盖面。

实施项：

1. 为 `ref_index` 行生成稳定事件提示边，补全基线 hints
2. 为 `ExecSlice.start_event/end_event` 生成对象边，来源标记为 `exec_slice_boundary`
3. 为 `TaskStateSeg.cause_event` 生成对象边，来源标记为 `task_state_cause`
4. 为 `ResourceGraph.wait_edges / hold_edges` 中的 `evidence_ref` 生成对象边，来源标记为 `resource_wait_edge / resource_hold_edge`
5. 为 `anchors[].evidence_anchor` 与 `AnalysisContext.evidence_anchor` 生成 `ref_anchor` 边
6. 允许 sidecar 工作集包含 `event ref <-> object ref` 双向边，使 `slice/state/resource/alert/diagnosis/anchor` 可通过对象节点参与扩张
7. 统一整理 `relation_kind / rule_family / provenance / priority / cycle_guard_token`
8. 冻结最小 rule family 映射：
   - `ref_index` -> `ref_ref`
   - `exec_slices / task_states / resource_graph` -> `ref_object`
   - `alerts` -> `ref_alert`
   - `diagnoses` -> `ref_diagnosis`
   - `anchors / analysis_context` -> `ref_anchor`
9. 冻结主要 `relation_kind` 与 `cycle_guard_token` 生成模式，确保不同来源可重复稳定
10. 统一 hints 与 `estimate_events / estimate_bytes` 生成规则，缺少主路径所需字段的边直接丢弃

完成判据：

1. 新 sidecar 行可解释、可排序、可投影、可窗口化
2. 至少具备文档要求的核心来源矩阵覆盖
3. 事件 ref 与对象 ref 混合扩张不会破坏闭包稳定性
4. 循环抑制与 priority 排序保持稳定

### W3 数据面：候选窗口规划与局部读取

目标：把数据面推进到“candidate edges -> window plan -> read windows -> stable merge”的正式链路。

实施项：

1. 将 `build_window_plan()` 改为以 `candidate_edges / delta_refs / segment_metas` 为输入，而不是依赖已选事件
2. 按 `segment_hint -> core_hint -> time_hint` 聚合 span，并保留 `target_refs`
3. 引入窗口读取函数，并明确复用现有 decoder/chunk API：
   - `TraceDecodeSession.feed(...)`
   - `GLOBAL_HEADER_STRUCT / SEGMENT_META_STRUCT / CHUNK_HEADER_STRUCT`
   - 基于 chunk 时间范围与 `window_plan` 做最小必要片段扫描
4. 基于原始 trace 源路径对候选窗口做有界 chunk 扫描
5. 读取后仅保留命中 `delta_refs` 的事件，允许极少量必需上下文事件进入稳定归并
6. 回填 `scan_count / seek_count / window_span_total / bytes_read / matched_refs`
7. `scan_count / seek_count / window_span_total` 必须来自实际读取结果，而不是仅由 `window_plan` 估算
8. 遇到损坏段或 I/O 守卫时返回 fail-closed 结果，并由 finalize 决定 `degraded`
9. fail-closed blocker artifact 必须能明确落到 `CORRUPT_SEGMENT / TRACE_IO_GUARD` 等 code

范围约束：

本轮窗口读取复用现有 trace/chunk 解码能力和时间窗跳读逻辑，不承诺一次性引入新的物理随机访问索引；只要能把扫描范围收敛到候选窗口集合并保留可审计 telemetry，即视为达到本轮目标。

完成判据：

1. evidence 主路径不再仅依赖 `bundle.event_stream` 直接挑选 selected events
2. `proof_digest` 中收益指标来自实际窗口规划与读取
3. 候选窗口读取可用测试证明其扫描范围受 hints 约束
4. 即使尚未做到最优物理 seek，本轮也必须做到“只扫描候选窗口覆盖的 chunk/span”，不再退回全源扫描主路径

### W4 编排与写包收口

目标：让服务层、闭包层、写包层与新增控制/数据面链路衔接稳定。

实施项：

1. 调整 `execute_evidence_closure()`，把 round projection、candidate edge sample、窗口读取结果纳入状态对象
2. 让 `write_evidence_package()` 使用读取后的命中事件与 telemetry 写出 `proof_digest / frontier_snapshot / blocker_artifact`
3. 保持 `repro_evidence.py` 对新增 `Mode-B` 包兼容
4. evidence package 默认补齐 `result/alerts.json` 与 `result/diagnoses.json`
5. `result/result_validity.json` 对保留的 `alert` / `diagnosis` 对象逐项写出：
   - `object_kind=alert` + `object_id=alert_id`
   - `object_kind=diagnosis` + `object_id=diag_id`
   - `validity_scope=source_snapshot`
   - `derivation_mode=reused_context`
6. 只在必要范围内补 `meta.control_refs`，避免本轮扩面过大
7. 明确 evidence job 不复用 `full/clipped export` 写包路径，避免 regression 污染

完成判据：

1. `control/` 合同不回退
2. `proof_digest` 与 `frontier_snapshot` 的 telemetry、halt 信息来自统一状态
3. evidence package 的 `alerts/result_validity` 不再低于详细设计最低口径
4. repro 打开与已有 fixture 校验仍可通过

## 6. 实施顺序

按以下顺序执行，避免返工：

1. 先改开发计划与测试目标，冻结本轮范围
2. 先做 `W0` 的合同冻结增量，避免后续字段再漂移
3. 再做 `W1` 的 mode/validator 骨架，确保 standalone two-pass 入口成立
4. 然后做 `W2` 的 sidecar 抽取扩展，保证控制面数据足够驱动后续窗口读取
5. 再做 `W3` 的窗口规划与局部读取，替换当前“已物化事件挑选”主路径
6. 最后做 `W4` 的写包收口与回归修补

## 7. 测试与回归基线

本轮至少补齐并执行以下验证：

1. `tests/python/test_desktop.py`
   - 新增 standalone two-pass evidence 导出成功场景
   - 新增 sidecar 校验失配进入 `degraded` 的场景
   - 新增窗口读取只命中候选 refs、收益指标回填的场景
   - 新增 evidence package 保留 `alerts + diagnoses + object-level result_validity` 的场景
   - 保留一条 `Mode-A` 回归场景，避免现有 evidence 最小闭环被打断
2. `tests/python/test_evidence_contract_assets_simtool.py`
   - 保证 evidence package 合同、schema、checksum、proof 基线不回退
3. 新增或扩展 parser 层单测
   - `tests/python/test_evidence_seed.py`
   - sidecar 来源抽取矩阵
   - candidate edge 排序与 cycle guard
   - window plan 聚合与 span 合并
   - chunk-aligned window read 与实际 telemetry
   - 六类 seed 入口覆盖
4. 新增或扩展 evidence 专项测试
   - `tests/python/test_evidence_sidecar.py`
   - `tests/python/test_evidence_window.py`
   - `tests/python/test_repro_evidence.py`
5. 现有 evidence/repro 相关回归
   - exact / bounded / degraded
   - frozen fixture package open

## 8. 风险与主 agent 介入点

1. 若 `Mode-B` 输入 contract 与现有 `ExportService` job 结构耦合过深，优先保证请求兼容和测试可落地，不做过度抽象
2. 若窗口读取无法在当前 trace API 上做到真正物理 seek，只要能把扫描范围收敛到候选窗口集合并留下清晰 telemetry，本轮可接受
3. 若 sidecar 某些来源缺少完整 hints，应宁可 fail-closed，也不回退到 legacy 全源扫描主路径
4. 若 schema 或 fixture 更新牵动面过大，优先保证 `W0` 合同增量与最小 fixture 一致，不把“先编码再补合同”当作捷径

## 9. 本轮完成定义

本轮完成需同时满足以下条件：

1. `W0` 合同冻结已完成，`alerts` 与对象级 `result_validity` 已进入 evidence package 正式合同
2. evidence 导出存在可测试的 `Mode-B: standalone two-pass` 路径
3. sidecar 抽取覆盖明显扩展，且能通过新增测试证明来源与 hints 完整性
4. evidence 主路径具备候选窗口规划与局部读取链路，收益指标来自实际读取
5. `exact / bounded / degraded`、proof bundle、repro 打开与合同测试全部通过
6. `full/clipped export` 现有行为不回退
