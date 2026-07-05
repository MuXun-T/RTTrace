# 公开 RTOS 1GB 预验证样本方案（2026-03-21）

## 1. 目的

本方案用于在“暂无现场真实输入、但需要先做 `>= 1GB` 缺口收口验证”的前提下，为当前仓库补一条**公开 RTOS 样本预验证链路**。

定位说明：

1. 该方案服务于 `tmp/1GB输入相关缺口优化修补计划_20260320.md` 中的“控制面硬化 + 大输入执行链路瘦身 + 正式 1GB 验收回填”思路。
2. 该方案的目标是**先用公开 RTOS 语义样本把链路压通、收缺陷、做预验证**。
3. 该方案**不是**最终 formal external evidence。真实使用时仍需替换为现场真实采集输入。

## 1.1 当前仓库内已提供的样本

当前仓库已直接落地一套 repo-local 样本：

1. `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_baseline.trace`
2. `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_candidate.trace`
3. `example/public-rtos-1gb-prevalidation/nuttx_public_seed.systrace`
4. `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_manifest.json`
5. `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_preflight.json`

当前校验值：

1. baseline/candidate 大小均为 `1073741880` bytes
2. baseline/candidate `sha256` 均为 `f445be02657c6d24ac8d419323d634ee226b751b855ff4a7b6155bc5156fd245`
3. seed `sha256` 为 `e2c4ed9ba56e47c5edcfbde225ded7b766848e8c2e953055d72fba51375967c0`

注意：

1. 仓库中的这份样本用于留档与交接。
2. checked-in 的 `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_manifest.json` 是 `2026-03-21` 历史生成过程留档；它的字段集可能落后于当前 `tool/build_public_rtos_large_input.py --report` 输出，不应作为当前 builder 输出 schema 的权威来源。
3. 当前 fast/dense 契约应以本文件中的命令行、`tool/build_public_rtos_large_input.py` 的 CLI 参数和 `example/public-rtos-1gb-prevalidation/public_rtos_1gb_dense_template.json` 为准。
4. 真正跑耗时/内存验收时，建议先把同一份字节内容 staging 到本地 SSD / `ext4` 工作区，再执行 timed run。
5. 自 `2026-03-24` 起，新的 timed/export 相关 artifact 统一带 `export_contract_version=v2`；不要再把旧 fast artifact 中的 `export_full_seconds=108.039036` 与新合同产物直接混比。

## 2. 公开来源选择

本轮选择 **Apache NuttX 官方 Task Trace** 作为公开 RTOS 语义来源，原因如下：

1. NuttX 是公开可获取的 RTOS，且官方文档明确提供 task trace / syscall / IRQ trace 机制。
2. NuttX task trace 的核心事件包括：
   - `sched_switch`
   - `sched_wakeup_new`
   - `sched_waking`
   - `irq_handler_entry`
   - `irq_handler_exit`
   - `sys_*` 进入/返回
3. 这些事件可以稳定映射到本仓库当前正式事件模型中的：
   - `TASK_READY`
   - `TASK_BLOCK`
   - `TASK_WAKEUP`
   - `TASK_DISPATCH`
   - `CTX_SWITCH`
   - `SYNC_TRY / SYNC_LOCK / SYNC_UNLOCK`
   - `IRQ_ENTER / IRQ_EXIT`

## 3. 与当前正式事件模型的对齐

当前仓库正式事件模型见：

1. `spec/dictionary/event_dictionary.json`
2. `spec/events.py`

本轮新增工具 `tool/build_public_rtos_large_input.py` 采用如下最小对齐规则：

1. `sched_wakeup_new` -> `TASK_READY`
2. `sched_waking` / `sched_wakeup` -> `TASK_WAKEUP`
3. `sched_switch` -> `TASK_DISPATCH + CTX_SWITCH`，必要时补 `TASK_BLOCK`
4. `irq_handler_entry` -> `IRQ_ENTER`
5. `irq_handler_exit` -> `IRQ_EXIT`
6. `sys_sem_* / sys_mutex_* / sys_mq_* / sys_event_* / sys_flag_*` -> `SYNC_*`

说明：

1. 这是**语义保真优先**的映射，不追求对 NuttX 全部 note record 做一比一搬运。
2. 其目标是让当前仓库的 RTOS 事件模型在公开样本下被真实压到，而不是继续停留在 repo synthetic scenario。

## 4. 1GB 样本构造策略

新增工具：

```bash
python3 tool/build_public_rtos_large_input.py \
  --seed-systrace /abs/path/to/nuttx_public_seed.systrace \
  --sample-tier dense \
  --baseline-output /abs/path/to/public_rtos_1gb_baseline.trace \
  --candidate-output /abs/path/to/public_rtos_1gb_candidate.trace \
  --target-size-bytes 1073741824 \
  --candidate-mode timestamp_jitter \
  --report docs/public_rtos_1gb_prevalidation_manifest_20260321.json
```

构造逻辑：

1. 读取公开 NuttX task trace / systrace 文本 seed。
2. 映射成当前仓库正式 RTOS 事件。
3. 先扩展出一段“真实 RTOS 语义 seed”。
4. 再在保持 trace 可解析的前提下扩展到 `>= 1GB`。

推荐默认值：

1. fast: `real_seed_target_bytes = 8MB`，`candidate_mode = mirror`
2. dense: `real_seed_target_bytes = 128MB`，`candidate_mode = timestamp_jitter`
3. `target_size_bytes = 1GB`

这样做的意义：

1. 首屏、预览、第一页事件和首个 `LOD2` 时间窗会先接触到真实 RTOS 语义事件。
2. 文件体量满足 `>= 1GB`，可以提前压 `1GB` 相关控制面、执行面和 artifact 留档链路。
3. 又不会因为完全依赖“全真实事件撑满 1GB”而把预验证阶段成本拉到不可控。

## 5. provenance 口径

本轮新增两个 provenance：

1. `public_rtos_derived`
   - 输入来自公开 RTOS trace 语义，未做零填充扩展。
2. `public_rtos_seeded_padded`
   - 输入前段来自公开 RTOS 语义 seed，但为达到 `>= 1GB` 做了可识别的扩展。

这两类输入都**不能**进入 `formal_1gb_verified=true`。

原因：

1. 它们用于当前阶段的**预验证 / 收缺陷 / 压链路**。
2. 最终 formal closure 仍要求真实外部输入，即 `input_provenance=real_external`。

## 6. 推荐执行路径

### 6.1 准备公开 seed

推荐 seed 形式：

1. NuttX `trace dump` 输出文本
2. NuttX `tools/parsetrace.py` 生成的 systrace 文本

### 6.2 生成 1GB 预验证输入

```bash
python3 tool/build_public_rtos_large_input.py \
  --seed-systrace /abs/path/to/nuttx_public_seed.systrace \
  --sample-tier dense \
  --baseline-output /abs/path/to/public_rtos_1gb_baseline.trace \
  --candidate-output /abs/path/to/public_rtos_1gb_candidate.trace \
  --target-size-bytes 1073741824 \
  --candidate-mode timestamp_jitter \
  --report docs/public_rtos_1gb_prevalidation_manifest_20260321.json
```

### 6.3 跑 preflight

```bash
python3 tool/desktop_validation_preflight.py \
  --baseline-input /abs/path/to/public_rtos_1gb_baseline.trace \
  --candidate-input /abs/path/to/public_rtos_1gb_candidate.trace \
  --scratch-dir /abs/path/to/big_disk_scratch \
  --load-timeout-s 600 \
  --output docs/public_rtos_1gb_preflight_20260321.json
```

### 6.4 跑 desktop perf acceptance

```bash
python3 tool/run_acceptance_baseline.py \
  --mode desktop_perf_acceptance \
  --acceptance-scope perf_only \
  --profile medium \
  --soak-iterations 1 \
  --baseline-input /abs/path/to/public_rtos_1gb_baseline.trace \
  --candidate-input /abs/path/to/public_rtos_1gb_candidate.trace \
  --load-timeout-s 600 \
  --workdir /abs/path/to/big_disk_scratch \
  --memory-limit-mb 3200 \
  --blocker-output docs/public_rtos_1gb_desktop_perf_blocker_20260321.json \
  --output docs/public_rtos_1gb_desktop_perf_20260321.json
```

`2026-03-24` 的 repo 内固定执行基线：

```bash
python3 tool/build_public_rtos_large_input.py \
  --seed-systrace example/public-rtos-1gb-prevalidation/nuttx_public_seed.systrace \
  --baseline-output /var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_baseline.trace \
  --candidate-output /var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_candidate.trace \
  --sample-tier dense \
  --report docs/public_rtos_1gb_dense_build_manifest_20260324.json

python3 tool/desktop_validation_preflight.py \
  --baseline-input /var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_baseline.trace \
  --candidate-input /var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_candidate.trace \
  --scratch-dir /var/tmp/rttrace-1gb/20260324/run \
  --load-timeout-s 900 \
  --output docs/public_rtos_1gb_dense_preflight_20260324_phase0.json
```

说明：

1. timed run 不再使用当前共享工作目录作为 scratch；统一固定到 `/var/tmp/rttrace-1gb/20260324`。
2. `docs/public_rtos_1gb_dense_preflight_20260324_phase0.json` 已记录 `ready_for_large_input_perf=true`。
3. 后续新的 perf/export 相关 artifact 统一视为 `export_contract_version=v2`。

预期口径：

1. `formal_input` 节点完整
2. `input_provenance=public_rtos_derived` 或 `public_rtos_seeded_padded`
3. `formal_1gb_verified=false`
4. summary 会把它视为 `pending_external`，而不是 `closed`
5. `perf_only` 的计时主路径使用 lightweight preview + single baseline load；candidate 审计与 `sha256` / provenance 落在非计时 audit 步骤
6. 若失败或超时，`--blocker-output` 对应的 blocker artifact 仍应落盘
7. 在 4GB 级 Linux 主机上，可显式增加 `--memory-limit-mb 3200`，把外部 OOM kill 转成脚本内 `MemoryError` / blocker artifact；该参数默认关闭，且只建议用于 `perf_only` 预验证

### 6.5 dense 模板

repo 内当前建议直接参考：

1. `example/public-rtos-1gb-prevalidation/public_rtos_1gb_dense_template.json`

它用于明确 dense sample 的生成契约，而不是保存 dense `1GB` 原始文件本体。
checked-in 的 fast manifest 仅用于历史生成留档；当前交接时如果需要确认字段与默认值，应优先参考本文件和 dense template。

## 6.6 当前仓库建议用法

如果当前只是做仓库内预验证，可直接以 `example/public-rtos-1gb-prevalidation/` 为源，并按 `docs/1gb_input_gap_execution_plan_20260321.md` 先做本地 staging，再执行 `perf_only`。

## 6.7 2026-03-22 仓库内执行结果

当前仓库内已形成如下 public `1GB` 预验证证据：

1. fast 样本 perf artifact：
   - `docs/public_rtos_1gb_fast_desktop_perf_20260322.json`
2. dense 样本 Phase D blocker artifact：
   - `docs/public_rtos_1gb_dense_desktop_perf_blocker_20260322_phaseD.json`

当前结论：

1. fast 样本已证明 preflight、`perf_only`、lightweight preview、阶段化观测链路可正常工作。
2. dense 样本在当前主机上已不再表现为“外部终止无脚本产物”，而是脚本内 `MemoryError` + tool-generated blocker。
3. dense blocker 的当前证据更靠近 parse/decode 早期内存压力，而不是 export/metric 类后段问题。
4. public RTOS 预验证样本仍只用于 `pending_external` 预验证，不构成 `formal_1gb_verified=true`。

## 6.8 2026-03-24 Phase 0 / Phase 1 收口结果

本轮新增 repo 内证据：

1. dense build manifest：
   - `docs/public_rtos_1gb_dense_build_manifest_20260324.json`
2. Phase 0 preflight：
   - `docs/public_rtos_1gb_dense_preflight_20260324_phase0.json`
3. Phase 1 timed-path worker 结果：
   - `docs/public_rtos_1gb_dense_perf_only_worker_result_20260324_phase1_queryfix.json`
   - `docs/public_rtos_1gb_dense_perf_only_worker_progress_20260324_phase1_queryfix.json`

关键结论：

1. Phase 0 已冻结本地 `ext4` 基线：
   - baseline: `/var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_baseline.trace`
   - candidate: `/var/tmp/rttrace-1gb/20260324/public_rtos_1gb_dense_candidate.trace`
   - scratch: `/var/tmp/rttrace-1gb/20260324/run`
2. `desktop_validation_preflight` 已返回：
   - `ready_for_large_input_perf = true`
   - `scratch_fs_type = ext4`
   - `blocking_reasons = []`
3. Phase 1 已把 timed thin path 的索引模式固定为：
   - `index_build_mode = minimal`
   - `export_contract_version = v2`
4. `docs/public_rtos_1gb_dense_perf_only_worker_result_20260324_phase1_queryfix.json` 已记录：
   - `status = completed`
   - `last_completed_stage = export_skipped`
   - `desktop_perf.resolved_stage = query_ready`
   - `load_stage_completed = ["prs_Load", "prs_Verify", "align_events", "rb_Rebuild", "idx_Build"]`
   - `idx_Build_seconds = 3.31043`
   - `event_table_first_page_source = trace_window_scan`
   - `lod2_first_window_source = trace_window_scan`
5. 因此 Phase 1 的 repo 内退出条件已经满足：
   - dense rerun 不再卡在 `idx_Build`
   - thin `perf_only` timed path 已进入 `query_ready`
   - 新 blocker 若继续出现，应转入 query/export/audit 这类后继阶段，而不是回到 full index timed path

## 7. 当前可收口与不可收口边界

### 可以先收口的

1. 公开 RTOS 语义样本到正式事件模型的转换链路
2. `1GB` 输入 manifest / provenance / artifact 留档
3. `desktop_perf_acceptance` 在 `perf_only` 档位下的执行稳定性
4. 首屏 / 首次事件页 / 首个 `LOD2` 查询的预验证

### 仍不能视为最终完成的

1. `formal_1gb_verified=true`
2. `external_status.desktop_1gb_first_screen_lt_10s=closed`
3. `external_status.desktop_peak_memory_lt_4gb=closed`
4. `NFR-PERF-06` 的最终 formal closure

原因很简单：最终 formal closure 仍必须替换为真实 external input。
