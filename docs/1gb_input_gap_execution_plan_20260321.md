# `1GB` 输入缺口执行方案（2026-03-21，修订版）

## 1. 目的

本执行方案用于承接：

1. `tmp/1GB输入相关缺口优化修补计划_20260320.md`
2. `docs/public_rtos_1gb_prevalidation_20260321.md`

并在“当前没有现场真实 external 输入、但需要先把仓库内 `1GB` 相关缺口真正修补到可执行、可定位、可交接”的前提下，明确：

1. 当前 public-RTOS `1GB` 样本能解决什么、不能解决什么。
2. 在再次跑 `1GB` 前，仓库内还必须先补哪些执行面缺口。
3. 何时算“仓库内 `1GB` 缺口已修补到位”，何时才算最终 formal close。

本文件为当前主执行方案；`tmp/1gb_input_gap_execution_plan_20260321.md` 保留同步副本。

---

## 2. 当前基线

### 2.1 已经具备

1. 仓库内已经有 `formal_input` manifest、OS 级峰值内存字段、`desktop_perf_acceptance --acceptance-scope perf_only`、large-input preflight。
2. 仓库内已经落地 public-RTOS `>= 1GB` 预验证样本：
   - `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_baseline.trace`
   - `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_candidate.trace`
3. 主状态文档已经回到真实口径：
   - `desktop_1gb_first_screen_lt_10s = pending_external`
   - `desktop_peak_memory_lt_4gb = pending_external`
   - `NFR-PERF-06 = pending_external`
4. 主文档已经明确：public 样本只能做 prevalidation，不得冒充 final formal close。

### 2.2 已确认的关键偏差

以下问题已经在代码与现有 artifact 中得到确认，必须纳入执行方案本体，而不能再视为“已完成”：

1. `perf_only` 还不够“瘦”：
   - 当前 timed run 仍会做：
     - preview load 一次
     - baseline 完整 load 一次
     - candidate 完整 load 一次
     - baseline/candidate 各一次 `sha256`
     - baseline/candidate 各一次 provenance 扫描
   - 这与“`perf_only` 只保留一次完整 load”的目标不一致。
2. 当前首屏 preview 仍会做整文件 task-state preview 解码：
   - `prs_Prescan()` 不只是 header/chunk 头快扫；
   - 它还会走 `_build_task_state_preview()`，对整文件做逐块解码。
   - 因此当前 `first_screen` 观测更接近“preview + 全文件轻解码”，而不是严格意义上的 lightweight first paint。
3. large-input preflight 的“本地磁盘提示”存在误判：
   - 当前共享目录 `fuse.vmhgfs-fuse` 被误判为 `scratch_local_disk_hint=true`。
   - 这会削弱“先 staging 到本地 SSD / ext4 再计时”的 fail-fast 护栏。
4. 当前 repo 内 public 样本只适合 fast prevalidation：
   - 现有样本是 `public_rtos_seeded_padded`
   - 实际 real seed 仅 `8MB`
   - 后半段主要依赖 zero filler chunk 扩展到 `1GB`
   - candidate 直接镜像 baseline
   - 它适合修补控制面、文档口径、artifact 语义和 blocker 定位，不足以单独代表真实 event-dense `1GB` 输入成本。

### 2.3 当前最重要的判断

当前仓库的真实问题已经不是“缺一份 `1GB` 文件”，而是：

**还没有把 `1GB` 执行链路修到“可最小成本复跑、可定位瓶颈、可稳定交接”的状态。**

因此，本方案不再把“再跑一次 public `1GB`”视为唯一下一步，而是调整为：

**先补执行面护栏与阶段化观测，再用 public 样本分层预验证，最后再接真实 external 输入做 formal close。**

---

## 3. public 样本的使用边界

### 3.1 可以用 public 样本修补的缺口

1. `formal_input` / provenance / summary 状态机是否正确。
2. `perf_only` 路径是否真的瘦身到位。
3. preflight、scratch、timeout、磁盘空间、artifact 命名与 runbook 是否正确。
4. parse / rebuild / metric / export / desktop load 的阶段化 blocker 定位。
5. “public prevalidation != formal close”的文档与报告口径是否一致。

### 3.2 不可以用 public 样本关闭的缺口

1. `formal_1gb_verified=true`
2. `desktop_1gb_first_screen_lt_10s=closed`
3. `desktop_peak_memory_lt_4gb=closed`
4. `NFR-PERF-06=closed`
5. `WP-02-02` / 历史 `WP-08-01` 的最终 formal close

原因：

1. 当前样本 provenance 仍是 `public_rtos_seeded_padded`
2. 当前样本不是现场真实 external 输入
3. 当前 fast 样本的事件密度不足以单独代表真实 `1GB` 运行压力

---

## 4. 修订后的执行目标

## 4.1 仓库内“`1GB` 缺口修补到位”的定义

仓库内阶段完成时，应达到以下状态：

1. large-input preflight 能正确识别非本地磁盘、空间不足、timeout 过小等 blocker。
2. `desktop_perf_acceptance --acceptance-scope perf_only` 真正变成 single-load timed path。
3. `desktop_perf_acceptance` 能输出可审计的阶段化耗时与 blocker artifact。
4. public `1GB` 样本至少形成一份：
   - prevalidation perf artifact，或
   - prevalidation blocker artifact
5. 主状态文档仍保持：
   - public 样本只能是 `pending_external`
   - 不得误标为 formal close

### 4.2 最终 formal close 的定义

只有在以下条件全部满足后，才进入最终 formal close：

1. 替换为真实 external `>= 1GB` 输入
2. `formal_input.formal_1gb_verified=true`
3. `desktop_1gb_first_screen_lt_10s`
4. `desktop_peak_memory_lt_4gb`
5. `NFR-PERF-06`

以上三项进入正式 `closed` 或正式 `failed` verdict，而不再是 `fixture_only / pending_external`。

---

## 5. 分阶段执行

### Phase A：先补执行面硬缺口，再允许再次跑 `1GB`

当前状态：`P0，必须先完成`

#### A1. 修正 large-input preflight 的本地磁盘判定

必须补齐：

1. 把 `fuse.vmhgfs-fuse`、`fuse.vmhgfs` 一并纳入非本地盘识别。
2. 对当前 repo `example/` 路径，preflight 应给出：
   - `scratch_dir_not_local_disk`
   - 而不是 `scratch_local_disk_hint=true`
3. 补回归测试，防止后续再次把共享目录当作本地 SSD。

退出条件：

1. `example/public-rtos-1gb-prevalidation/` 作为 scratch 时，preflight 不再给出 ready。
2. 只有 staging 到本地 SSD / ext4 后，preflight 才允许进入 ready。

#### A2. 把 `perf_only` 收敛为真正的 single-load timed path

必须补齐：

1. `perf_only` 只保留：
   - 一次 preview
   - 一次主输入完整 load
   - 一次事件页查询
   - 一次 `LOD2` 查询
   - 一次 full export
   - 一次 clipped export
2. candidate 不再参与 timed full load。
3. `sha256` / provenance / size 校验从 timed run 主路径中拆出，优先由：
   - preflight
   - sidecar manifest
   - 或非计时准备步骤
   提供。
4. `formal_input` 仍要完整，但不允许为生成 manifest 而在 timed run 中重复顺序扫描两份 `1GB` 文件。

退出条件：

1. `perf_only` 的实际行为与 `tmp/1GB输入相关缺口优化修补计划_20260320.md` 中定义一致。
2. timed run 的 I/O 成本不再被 candidate load 与重复全文件扫描放大。

2026-03-22 仓库内执行注记：

1. `tool/run_acceptance_baseline.py` 已将 `desktop_perf_acceptance --acceptance-scope perf_only` 收敛为：
   - 一次 lightweight preview
   - 一次 baseline timed load
   - 一次首次事件页查询
   - 一次首次 `LOD2` 查询
   - 一次 full export
   - 一次 clipped export
2. candidate load、`sha256`、provenance 等 formal_input 审计动作已拆到非计时 audit 步骤。

#### A3. 为大输入运行补阶段化观测与 blocker artifact

必须补齐：

1. 至少输出以下阶段耗时：
   - `preview_seconds`
   - `load_seconds`
   - `rebuild_seconds`
   - `metric_seconds`
   - `export_full_seconds`
   - `export_clipped_seconds`
2. 记录 OS 级峰值 RSS。
3. 记录 query/cache 关键观测：
   - cache entry count
   - cache total bytes
   - 首次 event page / LOD2 查询来源
4. 若运行失败或超时，仍输出 blocker artifact，而不是只留下人工描述。

建议 blocker artifact 文件名：

1. `docs/public_rtos_1gb_desktop_perf_blocker_20260321.json`
2. 或按实际日期滚动命名

退出条件：

1. 再次超时也能回答“卡在哪一段”。
2. 不再出现“只知道 3 分 20 秒没跑完，但不知道慢在哪”。

2026-03-22 仓库内执行注记：

1. `desktop_perf` 节点现已输出：
   - `preview_seconds`
   - `load_seconds`
   - `rebuild_seconds`
   - `metric_seconds`
   - `export_full_seconds`
   - `export_clipped_seconds`
2. 失败或超时时可通过 `tool/run_acceptance_baseline.py --blocker-output <path>` 落独立 blocker artifact。

#### A4. 把 preview 口径拆成 fast-first-screen 与 full-preview 两层

必须补齐：

1. 大输入场景下提供 lightweight preview 选项，例如：
   - header/chunk-header only
   - 或显式关闭 full task-state preview
2. 保留现有 richer preview 作为非 timed / 调试路径。
3. `first_screen` 合同优先使用 lightweight preview 口径，避免把全文件 preview decode 混入首屏时间。

退出条件：

1. `NFR-PERF-03` 的观测基础不再依赖整文件 task-state preview。
2. public `1GB` rerun 可以区分：
   - 轻首屏问题
   - 全量重建问题

2026-03-22 仓库内执行注记：

1. `parser.prs_Prescan(..., include_task_state_preview=False)` 已提供 lightweight preview 口径。
2. 默认 `prs_Prescan()` 仍保留 `task_state_preview`，以保证 GUI 和既有测试默认行为不回归。

### Phase B：分层使用 public 样本，而不是只靠当前 fast 样本

当前状态：`P0，A 完成后执行`

#### B1. 保留 repo 内 fast 样本的职责边界

当前 `example/public-rtos-1gb-prevalidation/` 中的 fast 样本继续用于：

1. 控制面验证
2. runbook 交接
3. preflight 验证
4. blocker artifact 基线复跑

不再把它单独视为“真实 `1GB` 压力代表样本”。

2026-03-22 仓库内执行注记：

1. `example/public-rtos-1gb-prevalidation/` 继续只保存 fast 留档样本。
2. Phase C 的 first rerun 仍应先用 fast 样本验证护栏、单次 load 和 blocker artifact。

#### B2. 增加 dense public sample 生成口径

必须在执行方案中新增第二层 public prevalidation 样本：

1. dense sample 不要求入库原始 `1GB` 文件
2. 但必须固定生成口径，例如：
   - `real_seed_target_bytes >= 128MB`
   - candidate 不再简单镜像 baseline
3. 该样本主要用于更接近真实事件密度的 parse / rebuild / metric / export 压测

建议做法：

1. repo 内保留：
   - seed
   - builder
   - manifest 模板
2. dense `1GB` 文件在本地 scratch 或外部执行机生成，不强制入库

退出条件：

1. public prevalidation 拥有 fast / dense 两层样本策略
2. 不再把 fast 样本误用为唯一代表性样本

2026-03-22 仓库内执行注记：

1. `tool/build_public_rtos_large_input.py` 现已支持：
   - `--sample-tier fast|dense`
   - `--candidate-mode mirror|timestamp_jitter`
2. repo 内新增 dense 模板：
   - `example/public-rtos-1gb-prevalidation/public_rtos_1gb_dense_template.json`
3. dense 默认口径固定为：
   - `real_seed_target_bytes >= 128MB`
   - candidate 不再简单镜像 baseline

### Phase C：重新执行 public `1GB` 预验证

当前状态：`P0，A/B 完成后执行`

#### C1. 先做 staging

执行要求：

1. 仅以 `example/` 为仓库留档源。
2. 真正运行前，必须复制到本地 SSD / ext4 scratch。
3. 记录 staging 后：
   - 路径
   - `sha256sum`
   - 文件大小
   - 文件系统类型

#### C2. 先跑 fast sample，验证工具链修补是否生效

命令目标：

1. 验证 preflight 护栏是否正确
2. 验证 `perf_only` 是否已经 single-load
3. 验证阶段化观测与 blocker artifact 能否正常产出

预期结果二选一：

1. 成功产出 `public_rtos_*` provenance 的 prevalidation perf artifact
2. 失败但成功产出 blocker artifact

#### C3. 再跑 dense sample，确认真实瓶颈落点

只有 fast sample 证明工具链修补已经生效后，才进入 dense sample。

预期结果：

1. 若 dense sample 可跑通：
   - 说明 repo 内 `1GB` 缺口已基本修补到位
2. 若 dense sample 仍卡住：
   - 根据 blocker artifact 决定是否进入 Phase D

退出条件：

1. 至少形成一份可审计的 public `1GB` prevalidation perf artifact 或 blocker artifact
2. 已明确回答瓶颈主要位于：
   - preview
   - parse/rebuild
   - metric
   - export
   的哪一段

### Phase D：仅在 blocker 明确后推进结构优化

当前状态：`P1，条件触发`

只有在 Phase C 证明当前实现仍无法在可接受范围内完成 public dense `1GB` 预验证时，才推进以下结构优化：

1. source-backed / hybrid load 默认化
2. metrics 的窗口化访问
3. export 流式写出

触发原则：

1. 如果 blocker 主要在 preview：
   - 优先继续收 lightweight preview
2. 如果 blocker 主要在 parse/rebuild 常驻内存：
   - 优先收 hybrid load / thin bundle
3. 如果 blocker 主要在 metric：
   - 优先收窗口索引 / 惰性事件访问
4. 如果 blocker 主要在 export：
   - 优先收流式写包

退出条件：

1. public dense `1GB` 预验证可稳定完成
2. 可以进入真实 external 输入 formal run 准备阶段

2026-03-22 仓库内执行注记：

1. `parser/rebuild.py` 已把 `task_states / exec_slices / irq_spans / windows` 的 `sorted(...)` 副本改为 in-place `sort()`，降低 rebuild 末端排序副本峰值。
2. `parser/pipeline.py::_artifact_from_parsed()` 已在 `align_events(...)` 成功后释放 `parsed.data["events"]` 的可达引用，减少 align 后到 rebuild 前的同时常驻列表。
3. `tool/run_acceptance_baseline.py` 已新增 `--memory-limit-mb`（默认关闭），在 Linux + `desktop_perf_acceptance --acceptance-scope perf_only` 下通过 `RLIMIT_AS` 启用受控内存护栏，并把 `memory_guard` 写入 perf/blocker artifact。
4. dense rerun 现已产出 tool-generated blocker：
   - `docs/public_rtos_1gb_dense_desktop_perf_blocker_20260322_phaseD.json`
   - 关键字段：`status=failed`、`last_completed_stage=preview`、`error.type=MemoryError`、`memory_guard.limit_mb=3200.0`
5. 这意味着 dense 已不再是“外部终止且无脚本产物”，而是“脚本内可定位 blocker”；但 `Phase D` 的正式退出条件（public dense `1GB` 可稳定完成）尚未满足，后续若继续推进，应优先围绕 parse/decode/align 的进一步降峰值。
6. 在进入 `thin-event / hybrid-load` 前，可先做一轮低风险对象压降与观测守护，例如共享空 `trust_tags`、保留兼容的 hotspot 统计，并让失败时 artifact 带出更完整的 `partial_chunk / last_progress` 信息；该阶段用于降低后续结构重构噪声，不单独构成 `P0-2` close。
   - 2026-03-23 补充：相关 failure hotspot / breaker artifact 字段已通过回归守护固化，用于确保 dense rerun 即使失败也能携带可审计的 decode 进度与热点信息。
7. 2026-03-23 已完成 thin parse 贯通的下一步：
   - `parser/codec.py::TraceDecodeSession.finalize()` 现已真正尊重 `materialize_events`
   - `parser/pipeline.py::load_dataset_with_timings()` 这条大输入专用路径可让 `DecodedEvent` 贯通到 `align_events -> rb_Rebuild`
   - `parser/rebuild.py` 默认仍在输出边界 materialize `bundle.event_stream`
   - 默认 `load_dataset()` / GUI / compare / replay / export 合同保持不变
8. 因此后续 `Phase D` 的结构优化应收敛为：
   - 先在 `desktop_perf_acceptance --acceptance-scope perf_only` 上落 thin bundle / hybrid-load
   - 复用现有 source-backed query 能力承接 `event_stream` 缺失场景
   - 再在 `3.2GB` guard 下重跑 dense public sample，确认 blocker 是否从 `prs_Verify` 前移到 `align_events / rb_Rebuild / idx_Build`
9. 2026-03-23 已完成上述 `perf_only` thin 收口：
   - `load_dataset_with_timings(materialize_event_stream=False)` 已能返回 thin bundle
   - `perf_only` 已改为使用 prescan `time_window` + source-backed event table / LOD2 查询
   - `perf_only` thin 模式下 export probe 已显式 skipped，不把正式 export 路径一起拉入本轮
10. 因此 `Phase D` 的剩余动作收敛为：
   - 补 thin mode 直接产物契约测试，覆盖 `stage_observer/load_breakdown/event table/LOD2`
   - 在 `3.2GB` guard 下重跑 dense public sample，验证 blocker 是否前移
11. 2026-03-24 已执行一次 dense public sample 重跑尝试：
   - preflight 产物 `docs/public_rtos_1gb_dense_preflight_20260324_item5.json` 记录 `desktop_runtime_dependencies_not_ready`
   - acceptance 进程在 `_capture_perf_only_desktop_perf` 调用链内因致命 `MemoryError` 直接中止，未生成新的 blocker/report json
12. 因此 `Phase D` 尚未形成“blocker 已前移”的正式证据；后续需先补齐 desktop runtime 依赖，并复现出可落盘的 dense blocker/report，再继续判定是否跨过 `prs_Verify`
11. 2026-03-24 已完成 `Item 4` 契约测试闭环（测试增强，无功能面改造）：
   - 覆盖 thin 直产 `stage_observer`
   - 覆盖 thin `perf_only` 成功路径 `load_breakdown`
   - 覆盖 thin 直产 `viz_QueryEventTable` 与 `viz_QueryTimelineLOD(lod=2)`
   - 均基于 `load_dataset_with_timings(materialize_event_stream=False)` 真实产物
   - 默认 `load_dataset()` / GUI / compare / replay / export 语义保持不变
12. 因此 `Phase D` 下一步聚焦为：
   - 在 `3.2GB` guard 下重跑 dense public sample，验证 blocker 是否已从 `prs_Verify` 前移到 `align_events / rb_Rebuild / idx_Build`
13. 2026-03-24 已完成不强杀 worker 的 dense public sample 自然 rerun：
   - preflight：`docs/public_rtos_1gb_dense_preflight_20260324_natural.json`
   - blocker：`docs/public_rtos_1gb_dense_desktop_perf_blocker_20260324_natural.json`
   - 关键字段：
     - `load_breakdown.current_stage = rb_Rebuild`
     - `load_breakdown.completed_stages = ["prs_Load", "prs_Verify", "align_events"]`
     - `error.type = TimeoutError`
   - 结论：`Phase D` 已拿到正式证据，证明 dense blocker **不再**停在 `prs_Verify`
14. 2026-03-24 已在限定文件边界内完成 parser 大输入路径两轮减压：
   - 首轮：去掉 thin finalize 列表复制、align 原地化、rebuild 多次全表扫描、pipeline 重建后立即释放 aligned events
   - 次轮：thin rebuild 按消费顺序释放源事件
15. 两轮对应 dense rerun 结果：
   - `docs/public_rtos_1gb_dense_desktop_perf_blocker_20260324_round1.json`
     - `load_breakdown.current_stage = rb_Rebuild`
     - `error.type = MemoryError`
     - `error.traceback` 终点：`parser/rebuild.py::task_states.sort(...)`
   - `docs/public_rtos_1gb_dense_desktop_perf_blocker_20260324_round2.json`
     - `load_breakdown.current_stage = idx_Build`
     - `load_breakdown.completed_stages = ["prs_Load", "prs_Verify", "align_events", "rb_Rebuild"]`
     - `error.type = MemoryError`
     - `timings.rb_Rebuild_seconds = 22.116648`
   - 结论：dense blocker 已从 `prs_Verify/_decode_chunk` 前移到 `idx_Build`
16. 因此 `Phase D` 已达到“先让 blocker 前移，再决定是否切下一优先级”的退出条件：
   - 当前主优先级可转入 `P0-3` 的 export SLA 计量拆分
   - 若后续继续留在 parser 侧，应直接针对 `idx_Build / parser/index.py`，而不是回到 decode 早期结论
17. 2026-03-24 已完成当前轮次的 `Phase 0` 基线冻结：
   - dense build manifest：`docs/public_rtos_1gb_dense_build_manifest_20260324.json`
   - preflight：`docs/public_rtos_1gb_dense_preflight_20260324_phase0.json`
   - 固定路径：
     - baseline：`/var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_baseline.trace`
     - candidate：`/var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_candidate.trace`
     - scratch：`/var/tmp/rttrace-1gb/20260324/run`
   - 关键字段：
     - `large_input_preflight.ready_for_large_input_perf = true`
     - `scratch_fs_type = ext4`
     - `blocking_reasons = []`
18. 2026-03-24 已完成当前轮次的 `Phase 1` timed-path 收口：
   - `parser/pipeline.py::load_dataset_with_timings()` 已新增 `index_build_mode=full|minimal|deferred`
   - `desktop_perf_acceptance --acceptance-scope perf_only` 已固定使用 `index_build_mode=minimal`
   - 新 artifact 合同已固定为 `export_contract_version=v2`
   - 证据：`docs/public_rtos_1gb_dense_perf_only_worker_result_20260324_phase1_queryfix.json`
   - 关键字段：
     - `status = completed`
     - `desktop_perf.resolved_stage = query_ready`
     - `load_stage_completed = ["prs_Load", "prs_Verify", "align_events", "rb_Rebuild", "idx_Build"]`
     - `idx_Build_seconds = 3.31043`
     - `event_table_first_page_source = trace_window_scan`
     - `lod2_first_window_source = trace_window_scan`
   - 结论：dense rerun 已不再卡在 `idx_Build`，`Phase 1` 退出条件已满足；下一阶段应转入 export 计量拆分与正式 export 路径收口。

### Phase E：替换为真实 external 输入执行 formal close

当前状态：`外部依赖，最后执行`

执行要求：

1. 使用真实 external `>= 1GB` 输入
2. 运行环境符合需求基线
3. 使用已修好的：
   - preflight
   - `perf_only`
   - 阶段化观测
   - summary 口径

目标：

1. 产出正式 Linux `desktop_perf_acceptance_*_1gb.json`
2. 刷新 `final_validation_status`
3. 对以下节点做正式 verdict：
   - `desktop_1gb_first_screen_lt_10s`
   - `desktop_peak_memory_lt_4gb`
   - `NFR-PERF-06`

---

## 6. 建议交付顺序

1. 先把本方案同步落到 `docs/`，`tmp/` 仅保留副本。
2. 修 preflight 本地盘识别与测试。
3. 修 `perf_only`，去掉 candidate timed load 与重复全文件扫描。
4. 补阶段化观测与 blocker artifact。
5. 当前继续推进时，以 `docs/public_rtos_1gb_dense_perf_only_worker_result_20260324_phase1_queryfix.json` 为最新 timed-path 基线，不再回退到 `docs/public_rtos_1gb_dense_desktop_perf_blocker_20260324_round2.json`。
5. 补 lightweight preview 口径。
6. 用 repo fast sample 在本地 ext4 staging 上复跑。
7. 用 dense public sample 做第二轮 prevalidation。
8. 只有在 dense sample blocker 明确后，才做结构优化。
9. 最后替换为真实 external `>= 1GB` 输入做 formal close。

---

## 7. 关闭判据

### 7.1 对“仓库内 `1GB` 缺口修补到位”

以下条件全部满足才算完成：

1. `docs/1gb_input_gap_execution_plan_20260321.md` 为主方案且引用路径有效。
2. `tmp/1gb_input_gap_execution_plan_20260321.md` 与 `docs/` 版本同步。
3. large-input preflight 能正确阻止共享目录 / 非本地盘 timed run。
4. `perf_only` 已收敛为 single-load timed path。
5. 已有阶段化观测与 blocker artifact 契约。
6. public `1GB` prevalidation 已至少形成一份 perf artifact 或 blocker artifact。
7. summary 仍能正确把 public 样本裁定为 `pending_external`，而非 `closed`。

2026-03-23 状态：

1. `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_preflight.json` 已按当前代码口径重刷，repo 共享目录会返回 `scratch_dir_not_local_disk`。
2. `docs/final_validation_status_20260314.json` 已切换到 `docs/public_rtos_1gb_fast_desktop_perf_20260322.json` 的 public prevalidation 口径，`desktop_1gb_first_screen_lt_10s`、`desktop_peak_memory_lt_4gb` 与 `NFR-PERF-06` 均为 `pending_external`。
3. 因此以上 `7` 条当前均已满足；repo 内 `1GB` 缺口已修补到位，后续工作转入 dense blocker 优化或真实 external formal close。

### 7.2 对最终 formal close

以下条件全部满足才算完成：

1. 真实 external `>= 1GB` 输入替换完成
2. `formal_input.formal_1gb_verified=true`
3. `desktop_1gb_first_screen_lt_10s`
4. `desktop_peak_memory_lt_4gb`
5. `NFR-PERF-06`

三项全部进入正式 verdict

---

## 8. 一句话结论

当前最合理的路径不是“继续盲跑现有 public `1GB` fast 样本”，而是：

**先把 preflight、`perf_only`、阶段化观测、lightweight preview 这四个执行面硬缺口补齐，再分层使用 public fast/dense 样本收敛 blocker，最后再接真实 external `>= 1GB` 输入做 formal close。**
