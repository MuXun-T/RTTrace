# `spec.models` Compatibility Guide

更新日期：2026-03-18

## 1. 目的

本说明用于冻结当前 `spec.models` 的职责边界，避免它再次被误用为新的 runtime 模型主入口。

统一规则如下：

1. `parser.models` 是唯一 canonical runtime model 入口。
2. 新的运行态对象与字段演进，必须先落在 `parser.models`。
3. `spec.models` 只允许承担：
   - 与 `parser.models` full-signature 对齐的 compatibility export
   - 显式列入清单的 legacy-only export

## 2. 当前清单

### 2.1 full-signature aligned canonical peer

当前共有 `26` 个模型已与 `parser.models` 保持 dataclass full-signature 对齐（字段名、类型注解、默认值与 `default_factory` 一致）：

1. `Alert`
2. `AnalysisContext`
3. `Bookmark`
4. `CompareScope`
5. `DiffBundle`
6. `DiffDetail`
7. `DiffSummary`
8. `EventCursor`
9. `EventPage`
10. `EvidenceRef`
11. `ExecSlice`
12. `GlobalHeader`
13. `IndexBundle`
14. `IrqSpan`
15. `MetricResult`
16. `PlaybackState`
17. `RebuildBundle`
18. `ResourceGraph`
19. `SegmentMeta`
20. `TaskStateQuery`
21. `TaskStateSeg`
22. `TaskStateViewModel`
23. `TaskStateViewRow`
24. `TaskStateViewSegment`
25. `UnifiedEvent`
26. `UntrustedWindow`

### 2.2 full-signature drift canonical peer

当前共有 `0` 个模型仍被标记为 canonical peer 但存在 full-signature 漂移。

当前 `full-signature drift canonical peer` inventory 已清零。

### 2.3 legacy-only export

当前 legacy-only export 已按 `phase E` 收缩为 `1` 个 live symbol 和 `6` 个 retired symbol：

当前仍作为 live legacy symbol 保留在 `spec.models` 的只有：

1. `Dataset`

以下 `6` 个名字已从 `spec.models` live surface 与 `spec` 顶层包面退役，仅继续保留 owner/deprecation metadata：

1. `ExportJob`
2. `HoldEdge`
3. `MetricSession`
4. `PackageResult`
5. `ReproSession`
6. `WaitEdge`

## 3. 对象级决策矩阵（Phase A 冻结基线）

本节冻结的是 `phase A` 的对象归宿与后续治理边界，不代表实现已经完成。

1. `追平 runtime` 仅表示后续正式合同以当前 `parser.models` 对象为基线；若 runtime 方法或行为继续演进，仍以 `parser.models` 实现为准。
2. `恢复为设计文档最小 DTO` 仅表示后续实现必须把 runtime 私有状态剥离出去，不表示当前 runtime 已经收缩完成。
3. `仅保留 compatibility/legacy 身份` 仅表示该对象不再作为 `spec.models` 的 canonical peer，不等于设计文档缺口已经补齐。
4. `Dataset` 是当前唯一仍留在 `spec.models` 的 live legacy symbol；它的 owner 记录为 `parser.models.DatasetArtifact + desktop.repository.DatasetRecord + DatasetHandle(runtime currently Result[str], explicit DTO still pending)`，因此只保留 deprecated compatibility 身份，不再经 `spec` 顶层包面暴露。
5. `ExportJob / PackageResult / ReproSession / MetricSession / HoldEdge / WaitEdge` 已在 `phase E` 从 `spec.models` live surface 退役；它们的 owner、迁移说明和 retired 状态只通过治理 metadata 与本文档保留。
6. 当前 inventory 中的 `full-signature aligned canonical peer` 表示 dataclass 字段名、类型注解与默认策略已与 `parser.models` 保持一致；若后续要校验行为级兼容性，仍应以 runtime 实现与专项测试为准。

| 对象 | 当前 inventory 分类 | phase A 正式归宿 | 正式合同基线 | phase B 文档动作 | phase C-F 实施边界 |
|---|---|---|---|---|---|
| `Alert` | full-signature aligned canonical peer | 追平 runtime | `parser.models.Alert`；`support_level` 正式入约 | 在设计文档补 `support_level`，并按 runtime 口径说明 `threshold/actual` | `phase C` 对齐 compatibility 层与守卫；不得回退 runtime 诊断语义 |
| `ExecSlice` | full-signature aligned canonical peer | 追平 runtime | `parser.models.ExecSlice`；仅存在字段顺序差异 | 在设计文档注明这是顺序对齐项，不新增语义分叉 | `phase C` 仅做低风险顺序对齐；不得引入位置参数兼容债务 |
| `GlobalHeader` | full-signature aligned canonical peer | 追平 runtime | `parser.models.GlobalHeader`；`run_id?` 冻结为可选追踪字段 | 在设计文档补 `run_id?` 的字段定义与用途说明 | `phase C` 对齐 compatibility 层；不得回退 parser/export 已使用的 lineage 字段 |
| `IndexBundle` | full-signature aligned canonical peer | 追平 runtime | `parser.models.IndexBundle`；旧 `resource_index/irq_index/time_summary` 退出 canonical 口径 | 在设计文档新增正式 `OBJ-IndexBundle` 字段表 | `phase D` 作为高影响正式对象收口；历史字段不得继续占据 canonical peer |
| `PlaybackState` | full-signature aligned canonical peer | 恢复为设计文档最小 DTO | 详细设计 `OBJ-PlaybackState`；正式字段回到 `replay_id/status/rate/cursor/anchor_ref/visible_window/linked_views/trusted` | 在设计文档固定最小 DTO 边界 | `phase D` 将 `current_index/cursor_ts/mode` 下沉到私有运行态；在此之前不得把当前 runtime 混合态视为正式合同 |
| `RebuildBundle` | full-signature aligned canonical peer | 追平 runtime | `parser.models.RebuildBundle`；`dataset_id` 与 `header?` 为正式字段，`segment_metas` 为 provenance/segment-chain 附属正式字段，`index_bundle?` 为派生/缓存附件 | 在设计文档补 `OBJ-RebuildBundle` 字段表并写明字段边界 | `phase C` 只做低风险对齐；不得把 `index_bundle?` 误当 `metric_Ingest` 最小核心合同 |
| `TaskStateQuery` | full-signature aligned canonical peer | 恢复为设计文档最小 DTO | 详细设计 `TaskStateQuery`；正式字段回到 `time_window/lane_group/state_mask/task_filter/anchor_ref/include_summary` | 在设计文档恢复专用 query DTO，并明确 `dataset_id` 不属于正式字段 | `phase D` 通过 normalization/compat 适配过渡；`filter[\"dataset_id\"]` 只能作为临时兼容入口 |
| `TaskStateSeg` | full-signature aligned canonical peer | 追平 runtime | `parser.models.TaskStateSeg`；仅存在字段顺序差异 | 在设计文档注明这是顺序对齐项，不新增语义分叉 | `phase C` 仅做低风险顺序对齐；不得引入位置参数兼容债务 |
| `Dataset` | legacy-only export | 仅保留 compatibility/legacy 身份 | `parser.models.DatasetArtifact + desktop.repository.DatasetRecord + DatasetHandle(runtime currently Result[str], explicit DTO still pending)` | 若未来需要显式 `DatasetHandle` DTO，需在设计文档单独补决策；在此之前仅保留 owner/deprecation 说明 | `phase E` 后只允许保留在 `spec.models`，不得回流到 `spec` 顶层包面，也不得重新升格为 canonical peer |
| `ExportJob` | legacy-only export | 仅保留 compatibility/legacy 身份 | `desktop.services.ExportService.export_Full/export_Clipped -> Result[{job_id}]` | 维持接口级最小载荷写法，不新增共享 DTO；治理文档仅保留 retired owner mapping | `phase E` 已从 `spec.models` 与 `spec` 顶层退役，不得重新引入 public dataclass |
| `HoldEdge` | legacy-only export | 仅保留 compatibility/legacy 身份 | `parser.models.ResourceGraph.hold_edges[*] (dict edge payload)` | 在设计文档持续标注其为 `ResourceGraph` 内部边结构，而非正式共享对象 | `phase E` 已退役；`spec.models.ResourceGraph` 注解必须保持与 runtime 的 dict edge 结构一致 |
| `MetricSession` | legacy-only export | 仅保留 compatibility/legacy 身份 | `metric.core.MetricSession` | 在设计文档和兼容说明中保持真实 owner 唯一指向 `metric.core` | `phase E` 已从 `spec.models` 与 `spec` 顶层退役，不得重新恢复 compatibility dataclass |
| `PackageResult` | legacy-only export | 仅保留 compatibility/legacy 身份 | `desktop.services.ExportService.export_WritePackage -> Result[{package_path, entry_count, snapshot_id}]` | 维持接口级返回载荷写法，不新增共享 DTO；治理文档仅保留 retired owner mapping | `phase E` 已从 `spec.models` 与 `spec` 顶层退役，不得重新引入 public dataclass |
| `ReproSession` | legacy-only export | 仅保留 compatibility/legacy 身份 | `desktop.services.ReproService.repro_OpenPackage -> Result[{package_path, meta, manifest}] ; repro_RestoreContext/repro_LoadAsDataset own the rest` | 维持拆分后的接口级合同，不新增共享 DTO；治理文档仅保留 retired owner mapping | `phase E` 已从 `spec.models` 与 `spec` 顶层退役，不得重新引入 public dataclass |
| `WaitEdge` | legacy-only export | 仅保留 compatibility/legacy 身份 | `parser.models.ResourceGraph.wait_edges[*] (dict edge payload)` | 在设计文档持续标注其为 `ResourceGraph` 内部边结构，而非正式共享对象 | `phase E` 已退役；`spec.models.ResourceGraph` 注解必须保持与 runtime 的 dict edge 结构一致 |

本矩阵仅为 `phase A` 的归宿冻结基线。`phase B` 只补设计文档与合同说明，`phase C-F` 才进入代码实施、兼容收缩与自动化守卫。

## 4. 当前调用点审计

按仓库代码检索，当前直接 `import spec.models` 的调用点已基本退回测试侧：

1. `tests/python/test_spec_models_compat.py`
2. `tests/python/test_pipeline.py`

这说明主运行路径已经收敛到 `parser.models`，compatibility surface 的治理也已收敛到清单维护与自动化守卫。

`phase F` 已把这一点升级为自动化护栏：

1. `tests/python/test_spec_models_compat.py` 新增 `test_no_runtime_module_imports_spec_models`，扫描 `parser / desktop / metric / tool` 运行时代码目录，禁止直接 `import spec.models` 或 `from spec.models import ...`。
2. 测试侧仍允许为了 compatibility 对照而导入 `spec.models`，当前命中保持在：
   - `tests/python/test_spec_models_compat.py`
   - `tests/python/test_pipeline.py`

## 5. 使用规则

后续开发必须遵循以下规则：

1. 新的运行态逻辑、服务接口、GUI 状态和分析链路，统一从 `parser.models` 导入对象。
2. 若某个同名对象在 `spec.models` 与 `parser.models` 间存在 drift，禁止把 `spec.models` 版本当作新的 runtime 事实来源。
3. 只有在以下场景才允许继续使用 `spec.models`：
   - 兼容旧导入路径
   - 编写 compatibility 测试
   - 审核 legacy surface
4. 若必须保留 legacy-only export，必须在文档或测试中显式更新清单。

## 6. Deprecation Plan

### Phase 1 当前已完成

1. 显式标注 `parser.models` 为 canonical runtime 入口。
2. 固化 canonical peer inventory 与 legacy-only inventory，并将 drift 口径提升为 full-signature。
3. 冻结 `15` 个对象的 `phase A` 归宿矩阵与正式台账基线。

### Phase 2 Phase B 文档补齐

1. 按本矩阵补齐 `IndexBundle / PlaybackState / TaskStateQuery / RebuildBundle / GlobalHeader / Alert` 的正式文档合同。
2. 若未来需要显式 `DatasetHandle` DTO，再对 `Dataset` 的正式合同 owner 单独补决策；其余 `6` 个 retired legacy symbol 继续维持接口级 owner 说明，不新增共享 DTO。

### Phase 3 Phase C-F 实施收口

1. 低风险顺序对齐项与 runtime 胜出的正式对象按 `phase C/D` 边界实施，不得与文档补齐混做。
2. `phase E` 已完成 legacy surface 收缩：`spec.models` 只保留 `Dataset` 作为 live legacy symbol，`ExportJob / PackageResult / ReproSession / MetricSession / HoldEdge / WaitEdge` 均已退役为 metadata-only 治理项。

### Phase 4 Phase F 自动化收口

1. `tests/python/test_spec_models_compat.py` 已显式落地以下护栏：
   - `test_no_runtime_module_imports_spec_models`
   - `test_zero_full_signature_drift_or_explicitly_waived`
   - `test_all_canonical_peers_match_full_signature_or_are_explicitly_waived`
   - `test_playback_state_matches_formal_contract`
   - `test_task_state_query_matches_formal_contract`
   - `test_index_bundle_matches_formal_contract`
   - `test_legacy_only_exports_have_owner_mapping`
2. `phase F` 已执行专项回归：
   - `python3 -m unittest tests.python.test_spec_models_compat -q`
   - `python3 -m unittest tests.python.test_pipeline -q`
   - `python3 -m unittest tests.python.test_desktop -q`
   - `python3 -m unittest tests.python.test_acceptance_baseline -q`
   - `python3 -m unittest tests.python.test_desktop_runtime -q`
3. 本轮 smoke 产物已写入 `docs/acceptance_baseline_phaseF_smoke.json`，用于保留收口时的本地基线证据。

## 7. GAP-07 完成判据

后续可按以下标准判断 `GAP-07` 是否真正收口：

1. `spec.models` 不再承担任何新能力演进主入口。
2. 所有 canonical peer 要么 full-signature 对齐，要么被正式移出 canonical peer 清单。
3. 所有 legacy-only export 都有明确 owner mapping、迁移说明或退役路径，且只有 `Dataset` 可以继续作为 `spec.models` 内部的 live legacy symbol 存在。

截至 `2026-03-18`，以上判据已满足，`26` 个 canonical peer 已全部纳入 full-signature 守卫且 drift 清零，`GAP-07` 已从专项治理状态切换为常规自动化守护状态。
