# RTD-Pilot: RTOS 可靠诊断独立真值、采集能力与证据血缘 Pilot 实施规划

**状态：规划，未授权实现或实验。**  本文是后续独立开发阶段的总纲，不表示本文中任何未来数据、硬件、诊断器、实验结论或论文主张已经实现。除本文件外，本轮未修改代码、测试、Schema、Fixture、历史 Phase 0--7 文档或数据。

## Executive Summary

本路线的目标是建立一套可审计的单核 RTOS Trace 可靠诊断系统：每条 Capture 都有实际采集能力记录，每个诊断派生事实都可追溯到原始事件和边界，每个真实故障标签都独立于 parser、diagnoser 和 Agent，并且缺失条件下的结论使用保守的 `SUPPORTED`、`REFUTED`、`UNKNOWN`、`OOD` 状态。

它不是恢复已否决的 CCF-C 算法路线，也不是继续包装 Agent。论文的中心问题是：在过滤、采样、缓冲溢出、事件丢失、截断和派生关系边界不完整时，Capture Capability Manifest（CCM）和完整 evidence lineage 能否减少错误确认/错误否定，并比“任何 gap 重叠即拒答”更少产生不必要的 `UNKNOWN`。完整系统完成后，预期支撑的是单 RTOS、单板卡、窄故障族的中文核心系统/可靠性应用论文；不保证录用，也不支持通用 RTOS、通用根因或新验证理论的宣称。

推荐阶段名保持为 **RTD-Pilot: RTOS Reliable Diagnosis Independent-Truth and Evidence-Lineage Pilot**。其中 RTD-Pilot 是项目阶段名，不是另一个算法或论文主方法名。

## Repository Baseline

本节为只读审计结果，行号对应本次审计时 `main` 的 `START_HEAD=9444f1369fb65096e7a27c787bd0db649c1e15ae`。Phase 0--7 的冻结事实和审查文档均保持原样。

### Capture 与事件层

| 审计项 | 已核验位置 | 状态 | 结论与未来用途 |
| --- | --- | --- | --- |
| Event dictionary | `spec/assets/dictionary.json:1-153` | implemented | 已有 task、sync、IRQ、`LOSS`、`OVERFLOW`、`SYNC_CALIB`、`TS_CALIB` 定义；可作为硬件事件词典起点，须版本化扩展 Capture/observer marker。 |
| Collector API | `collector/include/trace_api.h`；`collector/core/trace_collector.cpp:1010-1238,1384-1765` | reusable infrastructure | 已有 Init/Enable/Disable、任务/同步/IRQ emit、filter、snapshot、flush。它是 host reference backend，不是已验证落板链路。 |
| Filter | `collector/core/trace_collector.cpp:1092-1097,1395-1398,1690-1697` | reusable infrastructure | collector 可以拒绝事件并保存运行时 filter state；当前没有每次 Capture 可发表的 capability history/manifest。 |
| Sampling | 未在 collector API 或核心实现发现采样策略/相位 | completely missing | 必须加入 explicit sampling contract；不得把“不存在采样字段”解释为全量观测。 |
| Sequence、LOSS、OVERFLOW | `parser/codec.py:542-585`；`spec/assets/dictionary.json:129-139` | reusable infrastructure | decoder 可检测 per-core seq gap，并转为 untrusted window；LOSS/OVERFLOW 可解码。缺少硬件端完整的 Capture-level counter 账本和自然 overflow 归因闭环。 |
| Truncation | `parser/codec.py:494-526,1064-1077` | implemented | 可识别截断 event/payload/chunk，形成 `UntrustedWindow`；须绑定到 CCM 和派生 lineage。 |
| Timestamp、calibration | `parser/models.py:77-94,229-247`；`parser/align.py:75-161` | reusable infrastructure | 原始/对齐时间、anchor 和 degraded alignment 已有；没有 observer 对齐误差上界或硬件 epoch 合同。 |
| uC/OS hook | `collector/hook/ucos3/README.md:1-56`；`collector/hook/ucos3/*.c` | scaffold only | 已有 uC/OS-III mapping，默认单核，非默认构建，且明确要求 target-side backend；不是板级实证。 |
| FreeRTOS parser | `parser/freertos_btf_parser.py:1-30`；`parser/freertos_vcd_parser.py:1-35` | synthetic/reference-only | 是受限 BTF/VCD 文本 parser；没有 repo-owned FreeRTOS collector、板级 hook 或独立 truth。 |
| Host buffer/flush | `collector/core/trace_collector.cpp:1107-1165,1245-1371` | reusable infrastructure | 已有 ring buffer、auto/timed/sync flush、统计和 pending integrity record；硬件 transport、存储故障处理和 retention 仍缺失。 |

### Parser、派生数据与信任层

| 审计项 | 已核验位置 | 状态 | 缺口 |
| --- | --- | --- | --- |
| `UnifiedEvent` | `parser/models.py:77-155` | implemented | 有稳定 event UID/ref key、seq、raw/aligned timestamp 和 trust tags；没有 capture manifest reference、原始字节位置或 observation capability binding。 |
| `UntrustedWindow` | `parser/models.py:66-74` | implemented | 仅 source/scope/time/reason/severity；没有受影响 event channel、confidence、observer/capability relation。 |
| Align | `parser/align.py:75-161`；`parser/pipeline.py:151-224` | reusable infrastructure | 可产生 `ALIGN_DEGRADED` window；未记录独立对齐误差界，也未与 Capture contract 绑定。 |
| Task state | `parser/models.py:187-198`；`parser/rebuild.py:146-179,207-245,358-371` | must be modified | 只有 `cause_event` 和 `trusted`；缺 close event、boundary IDs、rule/version、window IDs、capability ref 和 lineage status。 |
| `ExecSlice` | `parser/models.py:159-172`；`parser/rebuild.py:248-282,373-388` | must be modified | 有 start/end event，但无完整 source set、window relation 或 capability/lineage status。 |
| resource wait/hold edge | `parser/rebuild.py:210-241,283-306,404-435` | must be modified | 是 dict，只有单 `evidence_ref`、`trusted` 和时间；close event/loss crossing 未保留，open relation 仅生成 window。 |
| IRQ span | `parser/models.py:209-218`；`parser/rebuild.py:307-345,390-403` | must be modified | enter/exit 配对但只存 bool；缺 enter/exit source IDs、boundary list 和 lineage status。 |
| open relation | `parser/rebuild.py:335-344,392-435` | reusable infrastructure | 可标记 IRQ/lock/wait 未闭合；必须从“全局 warning”提升为派生对象级不完整血缘。 |
| `RebuildBundle` | `parser/models.py:250-267`；`parser/rebuild.py:454-467` | must be modified | 已汇集 event stream、派生对象、windows、capability flags；没有 CCM/lineage registry/derivation versions。 |
| trust 计算 | `parser/rebuild.py:204-205`；`metric/core.py:636-899` | must be modified | `trusted = not event.trust_tags` 并向派生对象折叠为 bool；这不能表达 filter、sampling、边界缺失、mapping 和无关 gap。 |
| source event ID 保存 | `parser/models.py:121-155`；`parser/rebuild.py:140-144` | reusable infrastructure | 原始 event UID 可用，但派生事实没有完整 source lineage；是本路线首批核心改造。 |

### Diagnosis、报告与前端层

| 审计项 | 已核验位置 | 状态 | 正确定位 |
| --- | --- | --- | --- |
| Alert generation | `metric/core.py:636-899` | implemented baseline | 当前告警基于 `_trusted`/`support_level` 产生。可作为 baseline，不是缺失感知诊断器。 |
| `Diagnosis` / `diag_Generate` | `parser/models.py:296-306`；`metric/core.py:899-980`；`metric/service.py:75-85` | implemented baseline | 有 evidence refs、related alerts、confidence、`exact/degraded` support；没有四状态 verdict、counter-witness、CCM 或 lineage status。 |
| Existing support semantics | `desktop/services.py:546-551`；`metric/core.py:952-955` | must be modified | 现有 `exact/degraded/unsupported` 是工程支持等级，不能替代 `SUPPORTED/REFUTED/UNKNOWN/OOD`。 |
| EvidenceRef/report/export | `parser/models.py:58-63,282-306`；`desktop/services.py:4745-4790` | reusable infrastructure | 可显示 alert/diagnosis/evidence refs；须扩展为可回溯 witness/counter-witness 和 diagnostic audit report。 |
| CLI/desktop | `desktop/app/cli.py:130-195`；`desktop/app/gui.py:928-946,2197-2209` | reusable infrastructure | 有 CLI 和告警 UI；晚于确定性核心，不能承担 truth 或 verdict。 |
| Advisor/runtime action/gate | `parser/openai_advisor_client.py`；`parser/runtime_optimization_gate.py`；相关 schema 由 `spec/schema/meta.schema.json:61-100` 引用 | reusable infrastructure, outside truth path | 可作为未来受限前端/报告实验；Agent、gate、manifest、checksum、proof digest 和 package replay 均不得决定真实诊断真值。 |

### 数据与实证层

| 资产 | 已核验位置 | 分类 | 允许用途 | 禁止用途 |
| --- | --- | --- | --- |
| P6 Cases | `docs/phase6_p6_4_diagnosis_report.md:3-11,67-87`；`tests/python/fixtures/rtos_diagnosis/` | synthetic/reference-only | unit/contract/regression fixture | hardware Case、independent fault truth、accuracy/recall 主结果。 |
| P7 external traces | `docs/phase7_external_trace_sources/acquisition_report.md:3-33`；`docs/phase7_p7_2_licensed_external_trace_plan.md:36-56` | synthetic/reference-only / external reference | format parser、normalization、replay、provenance test | diagnosis truth、真实硬件/RTOS 泛化、实验主数据。 |
| hardware Cases | `docs/phase6_p6_4_diagnosis_report.md:82-87` | completely missing | 无 | 当前为零。 |
| independent observer | `docs/research_track/completion_robust_final_decision.md:81-89` | completely missing | 无 | 不得由 parser/diagnoser/Agent 代替。 |
| immutable injection ledger | 同上；`docs/research_track/oov_rt_ccfc_publication_decision.md:62-70` | completely missing | 无 | 当前 manifest/checksum/proof digest 不能代替。 |
| Case split、mask manifest、baseline execution | WARDS/OOV 审查指出缺失；P6/P7 只有冻结 fixture/replay合同 | completely missing | 无 | 不得事后按 Capture/mask 拆分，也不得以 regression 代替评测。 |
| Licenses | `docs/phase7_external_trace_sources/acquisition_report.md:7-29` | reusable provenance only | 外部样本的格式/许可证审计 | 新硬件 Case 数据发布许可、truth 数据集许可。 |

**可直接复用：** dictionary/codec/align/rebuild pipeline、host collector 的格式和 buffer 机制、uC/OS hook scaffold、metric/CLI/desktop 的界面骨架、P6 的单元测试风格、P7 的 source/license/provenance 审计方式。

**只能作为 baseline：** 当前 alert/diagnosis、`trusted`、`exact/bounded/degraded`、RebuildBundle、全局 untrusted window、bounded closure、package/replay/proof-gate。

**绝不进入 truth path：** P6/P7 标签、manifest/checksum/proof digest/package replay、Agent/LLM/gate 自信度、外部 demo expected behavior。
**必须新增：** 真实板卡与 firmware、observer、immutable ledger、CCM、完整 lineage、Case/control/split/mask 合同、四状态确定性 verifier、硬件采集和统计/复现资产。

## Inherited Negative Boundaries

以下结论是本路线的硬边界，不得以换名、包装或子模块方式恢复：

1. bounded dependency closure 是 `REFRAME`；identity-bound evidence package 是 `ABANDON`（`publication_route_assessment_closeout.md:1-38`；`completion_robust_repository_asset_mapping.md:23-34`）。
2. WARDS 未获实现授权且最终为 `NO-GO`；MILP/WARDS-Select 不进入本路线（`wards_rt_preimplementation_decision.md:16-58`）。
3. completion-robust diagnosis quotient 是 `ABANDON`；possible-world completion、DES product、automata/right-congruence quotient 与诊断保持压缩均不再作为论文主线（`completion_robust_final_decision.md:71-133`）。
4. VEC-RT 是 `REFRAME`；Evidence Contract、Agent diagnosis、LLM/RAG/Gate、三值监控不能单独构成核心创新（`vec_rt_publication_potential_and_decision.md` 的最终决定）。
5. OOV-RT 作为 CCF-C 方法为 `REFRAME`；它指出当前派生事实完整血缘、observer、ledger 和真实 Cases 都缺失（`oov_rt_ccfc_publication_decision.md:32-70,129-159`）。
6. P6 synthetic/reference-only Cases 不是真实诊断真值；P7 external traces 只用于格式、解析、回放和 provenance，且固定 `hardware_validation=false`（`docs/phase6_p6_4_diagnosis_report.md:3-11,82-87`；`docs/phase7_p7_2_licensed_external_trace_plan.md:38-56`）。
7. regression 只能证明工程完整性，不能证明故障真值、准确率、真实 RTOS 行为或论文结论。
8. manifest、checksum、proof digest、package replay 只服务 integrity/reproducibility，不进入 diagnostic truth path。
9. Agent 仅可在 Phase 9 作为可移除的前端/排序附加项；不能生成标签、覆盖 verdict 或成为论文成立条件。

## Final Paper Positioning

### 题目选择

| 候选题目 | 评价 | 决定 |
| --- | --- | --- |
| 基于采集能力与证据血缘的 RTOS 轨迹缺失感知诊断方法 | 明确点出可检验的系统构件与核心问题，避免泛化为通用因果 RCA。 | **主标题** |
| 面向不完整观测的 RTOS 轨迹可靠诊断系统设计与实现 | 更符合工程系统论文，但方法焦点较弱。 | **备选标题** |
| 一种面向事件丢失的 RTOS 轨迹可信诊断方法 | 简洁，但“事件丢失”窄于 filter/sampling/truncation，“可信”容易被误解为 proof/integrity。 | 不作为首选。 |

### 论文问题与主命题

论文只回答：在 RTOS Trace 存在过滤、采样、缓冲溢出、事件丢失、截断和派生关系边界不完整时，CCM、完整 evidence lineage 和保守诊断状态是否能降低错误确认和错误否定，同时避免全局 gap-overlap 的过度拒答。

## Research Questions

* **RQ1：** CCM 是否能正确区分“可用缺席证据”“通道未观测”和“不支持判断”？
* **RQ2：** 完整 lineage 是否能避免由缺失 open/close boundary 造成的错误可信 task/resource/IRQ 区间？
* **RQ3：** 在真实独立真值下，lineage + capability 是否相对 rule-only、global gap-overlap 和 current `_trusted` 同时降低 false-confirmation/false-refutation，并以可接受的 UNKNOWN 代价保持 Case-level recall？
* **RQ4：** 该可靠性收益的 runtime、memory、采集 overhead 和可复现成本是否适用于一个单核、单 RTOS 的窄应用范围？

建议冻结主命题为：

> 在冻结的单核、单 RTOS、三类故障模式和独立 Observer 真值范围内，相对于不使用 Capture Capability 与完整血缘的相同规则诊断器，RTD 系统降低 false-confirmation 和 false-refutation；相对于 global gap-overlap，在不增加 false-confirmation 的预注册容差内减少 unnecessary `UNKNOWN`/abstention。

该命题须在 Phase 0 冻结：比较对象、阈值、容差、主指标、Case-level 主统计单位和拒绝/降级规则。它不应承诺每种 loss 都改善，也不应把 `UNKNOWN` 减少本身当作成功。

## Claim Boundary

**支持范围：** 一个指定真实 RTOS、单核、一个经过审计的板卡/firmware 配置；task、mutex、IRQ；有限时间区间；三种预注册 fault-pattern；对缺失感知的保守判断。
**明确排除：** queue backlog、完整 priority inheritance、deadline release/completion、multicore causality、通用因果 root cause、任意 RTOS、任意 loss 模型、无观测条件下的故障否定。

**禁止论文表述：** “首次提出三值监控”“首次提出 Agent RCA”“首次提出 Evidence Contract”“新通用运行时验证理论”“通用根因诊断”“任意 RTOS”“任意 loss”“契约成立等于真实因果根因”“P6/P7 证明真实诊断准确”“增加 Case 数可修复方法显然性”“Agent confidence 代表可靠性”“proof digest 证明诊断正确”。

## Contributions

* **C1，RTOS Trace 采集能力与证据血缘模型：** CCM 记录每一次 Capture 实际可观察的事件、过滤/采样、buffer/sequence、dictionary/mapping、时间边界与对齐能力；派生 task、mutex、IRQ 证据具有完整原始事件血缘。
* **C2，缺失感知保守诊断：** 在使用负面、持续时间和派生区间证据前检查 event channel、边界、CCM 与 lineage，输出 `SUPPORTED`、`REFUTED`、`UNKNOWN` 或 `OOD`，并保留 witness/counter-witness。
* **C3，真实 RTOS 可靠性实验：** 在 independent Observer、immutable injection ledger、healthy/near-miss/wrong-entity controls 下，衡量不同缺失/采集条件的错误确认、错误否定和拒答。

## Existing Asset Reuse Matrix

| 资产 | 状态 | 复用规则 | 不能承担的角色 |
| --- | --- | --- | --- |
| `collector/core` / `trace_api.h` | reusable infrastructure | 新目标端 backend、record format、filter/buffer/flush 测量的起点 | 已验证硬件 recorder 或 truth。 |
| `collector/hook/ucos3` | scaffold only | uC/OS-III 主平台候选的 hook mapping 参考 | 默认可构建 BSP、完整 wakeup/IRQ 语义或数据。 |
| dictionary/codec/align | reusable infrastructure | 原始 trace decode、seq/truncation/loss window、事件 ID | CCM 事实来源或 observer alignment proof。 |
| rebuild | baseline + must modify | 重建 API、对象 ID 和关系配对起点 | 单一可信重建、完整 bloodline 或真实故障标签。 |
| metric/desktop/CLI | baseline + reusable interface | baseline runner、报告显示、审计输出渠道 | C2 verdict、truth path 或 Agent 决策。 |
| P6 synthetic suite | synthetic/reference-only | lineage/capability 单测的最小 fixture 风格 | Pilot/formal experiment truth。 |
| P7 FreeRTOS/Zephyr external materials | synthetic/reference-only | parser/format/replay/provenance regression | 真实硬件、accuracy 或跨 RTOS 主结果。 |
| evidence closure/sidecar/proof/replay | baseline only | reproducibility package 完整性辅助、对照实现 | diagnosis truth、C1/C2 核心方法或论文创新。 |

## Missing Asset Matrix

| 缺失项 | 首次必须出现的 Phase | 验收证据 |
| --- | --- | --- |
| 真实板卡、BSP、firmware build | 1/4 | board inventory、build log、firmware/ELF/config hash。 |
| 独立 Observer 与对齐协议 | 1/5 | logic-analyzer/timer/secondary channel records、误差界、失效记录。 |
| immutable injection/capture ledger | 2 | append-only ledger、签名/锁定策略、schema validator、hash ledger。 |
| CCM | 2/4 | per-capture JSON、collector config hash、自然 loss/overflow 计数。 |
| 完整派生 lineage | 3 | source/boundary IDs、lineage state、round-trip tests。 |
| 真实三故障族 Cases/controls | 5 | Case definition、observer manifestation、retained non-manifested/invalid captures。 |
| 四状态 deterministic verifier | 6 | predeclared rule/threshold version、auditable reports。 |
| 公平 baseline 与 split/mask protocol | 7 | frozen baseline registry、split and mask manifests。 |
| 正式数据、统计、论文结果 | 8/10 | Case-level analysis、holdout report、tables/figures and scripts。 |

## Overall Architecture

```text
Independent plane (never reads diagnoser output)
Injection definition -> immutable Injection Ledger <- observer records
                                  |                         |
                                  v                         v
Capture Capability Manifest <- board collector -> raw trace + GPIO/timer epochs
                                  |
Analysis plane (deterministic)
raw trace -> decoder -> align -> rebuild with lineage -> capability binding
         -> fault-pattern verifier -> {SUPPORTED|REFUTED|UNKNOWN|OOD}
         -> auditable report (witness, counter-witness, windows, rule/version)

Reproducibility plane (outside truth): hashes, package/replay, build scripts,
licenses, frozen result tables, failure logs.  Optional Agent may query reports
only after the deterministic verdict is fixed.
```

## Hardware and Observer Plan

### Platform feasibility decision

Phase 1 must score actual availability before modifying code. The initial comparison is deliberately conditional; no board is asserted to be present.

| Candidate | hook reuse | GPIO/timer/UART/debug | priority/mutex/controllable IRQ | overflow/throughput | toolchain/license | recommendation rule |
| --- | --- | --- | --- | --- | --- | --- |
| A. uC/OS-III single-core target | Highest source reuse through `collector/hook/ucos3` | Must be verified per board; adapter needs target backend | API mapping exists; IRQ wrappers are manual | Unknown until backend/transport measured | RTOS/BSP redistribution and board access must clear | **Primary** only if a licensed, flashable board and backend are available within Phase 1, and hook semantic gaps are documented. |
| B. FreeRTOS single-core Cortex-M board | No collector hook; parser is not a collector | Common GPIO/timer/UART/SWD support, to be audited per board | Strong task/mutex/IRQ controllability expected but must be demonstrated | Likely practical for pressure tests; must be measured | FreeRTOS/MIT plus BSP/toolchain audit required | **Backup** if uC/OS target lacks backend, license, IRQ control, or reproducible toolchain. |
| C. laboratory available single-core RTOS board | Reuse depends on RTOS | Must have exposed pins and independent logic-analyzer access | Must support all three F families | Must create observable natural overflow | Must have reproducible build and data rights | Eligible only if it meets the same checklist; not a silent platform substitution. |

**Selection threshold:** select the first candidate that passes all: single core; legal RTOS/BSP; board physically available; reproducible compiler; GPIO epoch pins; hardware timer/counter; serial/trace export; debug recovery; priority/mutex APIs; controllable IRQ source; measurable collector pressure; operator can preserve raw data. **Abandon the selected platform** and choose backup if any mandatory capability fails after one bounded feasibility iteration. **Stop the paper route** if no candidate passes without simulation-only substitution.

### Observer design and truth separation

1. The firmware emits GPIO markers for Case start/end, injection enable/disable, IRQ entry/exit and selected fault milestones. A logic analyzer with its own clock records edges; it does not consume parser output.
2. A hardware timer/counter snapshot or a secondary serial milestone channel records a second independent timeline. At least one observer modality must remain when the trace transport fails.
3. The Capture emits an epoch marker visible in trace and observer data. Alignment estimates a mapping and records a conservative `alignment_error_bound`; Phase 1 chooses the numerical bound from measured repeated epochs, rather than inventing a value.
4. Observer records manifestation predicates, not diagnosis labels. Ledger supplies what was injected/enabled; Observer verifies whether and when the intended physical/runtime manifestation occurred. The diagnoser may never create or amend either.
5. A Capture is invalid when epoch mapping fails, observer file is incomplete, clock drift exceeds bound, pins are ambiguous, firmware/ELF/config hash differs from ledger, raw trace is unreadable, or observer shows collector instrumentation changed the scenario beyond predeclared tolerance. Invalid captures remain retained with `invalid_reason`.

## Injection Ledger

Phase 2 defines an append-only, capture-bound JSONL or SQLite-plus-export ledger, with immutable raw exports. Each record contains at least:

```text
case_id, scenario_template_id, fault_family, fault_variant, control_type,
board_id, session_id, capture_id, firmware_hash, ELF_hash, config_hash,
source_commit, RTOS_name, RTOS_version, compiler, compiler_flags, workload_seed,
injection_definition, injection_version, injection_parameters,
injection_enable_epoch, collector_config_hash, observer_hash, raw_trace_hash,
fault_manifested, manifestation_interval, invalid_capture, invalid_reason,
operator, timestamp, ledger_version
```

`control_type` is one of `healthy_control`, `near_miss`, `wrong_entity`, `fault`; `fault_manifested` is independent from `injection_enabled`. The ledger must distinguish: healthy control; near-miss; wrong-entity; manifested fault; enabled-but-not-manifested injection; and acquisition invalid. A monotonic append number, previous-record digest and sealed per-session digest prevent silent editing; this integrity mechanism records provenance only and never establishes diagnosis truth by itself.

## Capture Capability Manifest

Each `capture_id` has exactly one versioned CCM, generated from frozen collector configuration plus measured final counters, not inferred from missing events. Required fields:

```text
event_types_enabled, task_filter, resource_filter, irq_filter, core_filter,
sampling_enabled, sampling_rate, sampling_phase, buffer_capacity,
buffer_high_watermark, overflow_count, sequence_continuity, timestamp_source,
clock_resolution, alignment_error_bound, payload_fields, dictionary_version,
mapping_version, collector_version, trace_start_boundary, trace_end_boundary,
truncated, natural_overflow
```

Negative evidence is admissible only when all prerequisite event types and payload fields are enabled, entity/core filters include the target, sampling is disabled or a predeclared valid statistical rule applies, required start/close boundaries are inside capture bounds, sequence is continuous for relevant channels, no relevant natural overflow/truncation/window affects the fact, mapping supports the event semantics, and alignment uncertainty does not consume the threshold margin. Disabled filter or unsupported payload/mapping is `OOD`; sampling, relevant loss, overflow or truncation is generally `UNKNOWN`; absence is never treated as non-occurrence by default. Offline mask is an experimental observation condition and must not be written as natural overflow.

## Evidence Lineage Redesign

Phase 3 replaces the current bool-only trust interpretation while preserving a compatibility adapter for old callers. `TaskStateSeg` (called TaskStateSegment in this plan), `ExecSlice`, wait/hold `ResourceEdge`, `IrqSpan`, ready-but-not-running interval, Alert and Diagnosis evidence all require:

```text
source_event_ids, open_event_id, close_event_id, boundary_event_ids,
derivation_rule_id, derivation_version, interval_start, interval_end,
intersecting_untrusted_window_ids, capture_capability_ref, lineage_status
```

`lineage_status` is an ordered, explicit enum: `COMPLETE`, `INCOMPLETE_OPEN`, `INCOMPLETE_CLOSE`, `LOSS_AFFECTED`, `CAPABILITY_UNSUPPORTED`, `MAPPING_INVALID`, `ALIGNMENT_DEGRADED`. A relation may additionally retain multiple reason codes but may not silently collapse them into `trusted=False`. `trusted` remains a deprecated compatibility projection only; it must never be the sole diagnostic admission check.

Derivation requirements:

* State segments record transition-causing open event and closing transition event; final/end-of-trace closures are `INCOMPLETE_CLOSE`, not inferred normal closes.
* Exec slices retain dispatch/ctx-switch source IDs, all preemption boundary IDs, and any window crossing their interval.
* Wait/hold relations become typed dataclasses. Lock/block is the open boundary; unlock/wakeup/acquire is close boundary; an omitted close cannot produce a complete duration fact.
* IRQ spans retain matching enter/exit IDs and nesting relation; unmatched exit/enter produces object-level incomplete lineage as well as a window.
* ready-but-not-running is a new explicitly derived interval with READY source, dispatch/non-dispatch boundary sources, candidate competing execution/IRQ sources and all intersecting windows.
* Alerts and diagnoses reference derived evidence IDs plus raw witness/counter-witness IDs. They do not duplicate or mutate source lineage.

Mandatory focused tests: lost READY; lost DISPATCH; lost BLOCK; lost WAKEUP; lost LOCK; lost UNLOCK; hidden `UNLOCK+LOCK` inside a gap; lost IRQ ENTER; lost IRQ EXIT; trace truncation; event filter disabled; sampling skips a boundary; unsupported mapping; source-ID backtrace; and one derived object crossing multiple windows. Acceptance: every derived fact backtraces to raw events; missing boundary never becomes a complete trusted interval; old regression remains passing; each lineage status is covered.

## Deterministic Diagnosis Design

The Phase 6 path is fixed:

```text
Raw Trace -> Parser -> Align -> Rebuild with full lineage
          -> CCM binding -> deterministic fault-pattern verifier
          -> auditable SUPPORTED / REFUTED / UNKNOWN / OOD report
```

`SUPPORTED` requires complete, relevant, capability-supported witness evidence meeting the frozen pattern. `REFUTED` requires trustworthy counter-witness, or a necessary pattern demonstrably absent under complete observation. `UNKNOWN` means a required boundary, channel or relation is affected by loss, overflow, sampling, filter or truncation. `OOD` means dictionary, mapping, payload, alignment or CCM fundamentally cannot express the judgement. Priority is `OOD` before `UNKNOWN` when the semantic channel is unavailable, and `UNKNOWN` before `REFUTED` when absence cannot be admitted.

The verifier must use fact-specific relevance, not global temporal overlap. A gap cannot erase independently complete positive evidence; a long derived fact crossing a relevant gap cannot remain complete; irrelevant gaps do not force abstention. It must distinguish natural overflow from offline deletion, reject unsupported mapping as `OOD`, and never inspect Agent confidence, package validity, proof digest or replay status.

Every output records:

```text
case_id, capture_id, fault_family, entity_bindings, candidate_interval, verdict,
witness_event_ids, counter_witness_event_ids, derived_evidence_ids,
lineage_status, capability_manifest_id, relevant_window_ids, unknown_reason,
ood_reason, diagnoser_version, threshold_version
```

## Case and Control Design

All labels are defined before diagnosis and checked by Observer/ledger. A diagnosis result is never a label.

| Family | Frozen first-version pattern | injection/observer predicate | controls |
| --- | --- | --- | --- |
| F1 Bounded Task Interference | Target is READY but does not execute within threshold while a same-core higher-priority task occupies CPU for the predeclared interval. It is not infinite starvation. | Workload/injection controls competing task duty/window; GPIO marks target-ready and injector epochs; observer confirms competing interval/milestone. | healthy: no injected occupancy; near-miss: just below duration; wrong-entity: occupancy affects another target/core entity. |
| F2 Mutex Contention / Long Hold | waiter waits on mutex, holder holds it, intervals overlap and wait/hold exceed threshold. Priority inversion is only a pattern subclass. | Lock holder delay/work injection; observer marks lock/critical duration and waiter milestone. | healthy release; near-miss below threshold; wrong holder/resource. |
| F3 IRQ Interference | Long ISR, excessive IRQ frequency or IRQ occupancy overlaps target ready-but-not-running interval. It is not sole causal root cause. | Timer/peripheral IRQ generator with controlled rate/duration; GPIO IRQ enter/exit and target marker observed independently. | healthy rate; near-miss below rate/duration; wrong IRQ or unrelated task. |

For each family, Phase 5 writes a scenario template, parameters, unique Case rationale, injection definition/version, expected trace pattern (not label), Observer manifestation predicate, invalid conditions and all controls. The minimum Pilot is one board, 3 families, at least 4 independently configured Cases/family, 3 independently restarted Captures/Case: at least 12 Cases and 36 raw hardware Captures, plus retained controls and enabled-but-not-manifested attempts.

## Baselines and Ablations

The baseline registry is versioned before holdout access. All methods receive identical raw trace, Case truth, fault vocabulary, thresholds, masks, CCM, split, Observer evaluation and valid-Case set; target method gets no extra truth.

1. Current alert/diagnosis behavior.
2. Full clean-trace reference under the same deterministic fault rules (upper information reference, not ground truth).
3. Rule-only without loss handling.
4. Global gap overlap.
5. Manual footprint.
6. Provenance/backward closure baseline.
7. Lineage only.
8. Capability only.
9. Lineage + capability.
10. Full proposed system.
11. Universal abstain.
12. Optional generic FO/MTL/TeSSLa baseline only if a licensed, faithful adapter is feasible without displacing primary work.

Forced ablations remove source lineage; open/close boundary; filter/sampling capability; natural-overflow distinction; treat every gap as relevant; merge `UNKNOWN`/`OOD`; remove conflicting evidence; use only current `_trusted`; and use only current `RebuildBundle`. Any baseline failure, incompatibility or parameter deviation is reported and retained, never silently excluded.

## Statistical Protocol

### Units, metrics and split

The hierarchy is `Scenario Template -> Case -> Capture -> Observation Condition/Mask -> Diagnosis Result`. The principal inferential unit is **Case**. Captures, masks, repeated runs, models and prompts are nested sensitivity observations, never independent Cases. Development/validation/holdout are grouped by template or genuinely distinct Case family so near variants never cross splits. Freeze threshold selection on development/validation; first holdout access occurs only after registry, parameters, scorer and split digests are sealed.

Primary metrics: false-confirmation rate; false-refutation rate; missed-relevant-gap rate; confirmed precision; Case-level diagnosis recall; appropriate abstention; unnecessary `UNKNOWN` rate; lineage completeness; loss attribution accuracy; runtime and memory. Report denominators, per-family and macro summaries, Case-clustered confidence intervals, paired Case-level comparison where applicable, and raw contingency counts. Do not pool masks to claim significance. Predeclare how `OOD`, invalid Capture, non-manifested injection and no-applicable-pattern Cases enter each denominator.

### Observation conditions and masks

Clean Capture, random deletion, burst deletion, critical-event deletion, truncation, filter and sampling are separate observation conditions. Natural overflow is measured separately, never generated by a mask and never merged with deletion. Every offline mask has:

```text
mask_id, mask_type, rate, window_count, window_length, seed,
selected_event_ids, selection_algorithm, source_capture_hash, mask_hash
```

Use a frozen mask manifest and deterministic reapplication. Offline masks evaluate robustness only; they do not make a trace physically lost nor alter ledger/observer facts.

## Phase 0--11 Plan

All phases use this governance sequence: write phase plan; independent reviewer audit; explicit authorization; implementation or collection; focused tests; full regression; artifact audit; reviewer re-audit; freeze only at `blocking=0` and `major=0`; one dedicated commit; clean worktree; closeout document. “Allowed paths” below are future authorization envelopes, not authorization granted by this plan. Every phase forbids historical Phase 0--7 mutation, P6/P7 relabeling, truth generation by parser/Agent, and hidden data deletion.

### Phase 0 -- Paper Boundary, Feasibility and Contract Freeze

* **Goal / preconditions:** register claim boundary, three fault families, RQs, metrics, acceptance tolerance, Case hierarchy, data rights checklist and the Phase 1 feasibility protocol before code. Requires only this plan and reviewer authorization.
* **Allowed / forbidden paths:** add `docs/research_track/rtd_pilot/phase0_*`, `docs/rtd_pilot/contracts/`; forbid `collector/`, `parser/`, `metric/`, `desktop/`, `spec/`, `tests/`, all frozen Phase 0--7 docs and fixtures.
* **Development/docs/schema/code:** freeze claim/method boundary, fault/control vocabulary, threshold ownership, metric denominator definitions, truth/observer separation, and draft ledger/CCM/lineage schemas. Add claim-boundary, experiment-contract, data-management and reviewer records. No implementation schema or code change in this phase.
* **Tests / data:** documentation consistency/lint, schema design review, no hardware or data collection. Verify that every claim maps to a future measurable asset.
* **Completion / blockers / stop:** complete when reviewers approve a single-RTOS/single-core scope and Phase 1 test card. Block on unresolved RTOS/data license, undefined Observer predicate or no candidate platform. Stop/defer the paper route if it requires restoring abandoned methods or Agent as a necessary contribution.
* **Freeze / paper / assets:** commit only Phase 0 documents after approval; rollback point is the pre-Phase-0 commit. Supports Sections 1, 3 and 6; produces Claim Boundary Table, initial fault-family table, protocol digest and reviewer report.

### Phase 1 -- Hardware, RTOS and Independent Observer Feasibility

* **Goal / preconditions:** select primary and backup platform through the matrix above and demonstrate observer feasibility, without diagnosis coding. Requires Phase 0 freeze, physical board access, legal BSP/RTOS/toolchain, logic analyzer and operator protocol.
* **Allowed / forbidden paths:** add `docs/rtd_pilot/feasibility/`, `hardware/rtd_pilot/` (schematics, pin map, scripts only after authorization); allow a disposable external build workspace. Forbid parser/metric/schema/test changes, historical datasets, simulated data being labelled hardware, and any experiment claim.
* **Development/docs/schema/code:** audit uC/OS versus FreeRTOS candidate; document toolchain, pins, timer, UART, debug, task/mutex/IRQ controls, collector pressure and license. Implement only board smoke firmware when separately approved, plus GPIO marker wiring and independent observer logging; write platform selection and clock-alignment protocol. No diagnoser.
* **Tests / data:** repeat GPIO epoch capture; timer/serial cross-check; task scheduling, mutex and controllable IRQ smoke checks; measure drift/alignment error and recorder perturbation. Retain all failures but do not call this a paper experiment.
* **Completion / blockers / stop:** pass only when one platform meets every mandatory criterion, observer sees all required markers, clock error bound is measured, and a capture can be declared invalid deterministically. Block on unavailable hardware/toolchain/license. Stop if only simulator/host collector is feasible or observer cannot be isolated from trace analysis.
* **Freeze / paper / assets:** freeze pin map, platform decision, observer protocol and smoke evidence separately; rollback to Phase 0 if selection fails. Supports Sections 4--6; produces architecture and independent-truth flow diagrams, platform table and feasibility log.

### Phase 2 -- Injection Ledger and Capture Capability Manifest

* **Goal / preconditions:** create versioned, validated contracts before new diagnostic behavior. Requires chosen platform, stable Case identifiers and Phase 1 observer protocol.
* **Allowed / forbidden paths:** add `spec/schema/rtd_injection_ledger.schema.json`, `spec/schema/rtd_capture_capability_manifest.schema.json`, mirrored `spec/assets/schema/`, `docs/rtd_pilot/contracts/`, `tool/rtd_validate_*`, and isolated tests. Forbid modification of old P6/P7 schemas/fixtures and use of package/proof schemas as truth schemas.
* **Development/docs/schema/code:** implement ledger/CCM validators, version migration policy (`major` incompatible, `minor` additive), immutable append/seal workflow, collector-config snapshot adapter, Capture/ledger join validation and data retention policy. Add schemas for Case definition, observer record, raw artifact inventory and Capture validity; define required/optional fields and stable enum values.
* **Tests / data:** unit validation for required fields, state transitions, hash mismatch, duplicate IDs, changed config, non-manifested and invalid captures; integration test creates a synthetic contract object only to test validation, not truth. No hardware fault campaign yet.
* **Completion / blockers / stop:** complete when each capture can be uniquely linked to ledger, CCM, observer and raw hashes; filter/sampling/overflow semantics validate fail-closed. Block on inability to extract collector config or immutable storage. Stop if ledger can be rewritten without an auditable revision trail.
* **Freeze / paper / assets:** version/commit schemas, validators and contract examples; preserve migration test vectors. Supports Sections 3--5; produces CCM and ledger field tables, data-flow figure and validator README.

### Phase 3 -- Full Evidence Lineage Redesign

* **Goal / preconditions:** make every diagnostic derivation traceable and boundary-aware. Requires Phase 2 schemas and frozen lineage semantics.
* **Allowed / forbidden paths:** `parser/models.py`, `spec/models.py`, `parser/rebuild.py`, `parser/codec.py`, `parser/align.py`, `parser/pipeline.py`, isolated new `parser/rtd_lineage.py`, relevant serializers/schema/export adapters and new tests. Forbid historical fixtures/results mutation, completion/automata code and Agent truth integration.
* **Development/docs/schema/code:** type ResourceEdge/wait/hold; add mandatory lineage fields and registry to RebuildBundle; bind CCM; populate source/open/close/boundary/window IDs and derivation rule/version for TaskStateSeg, ExecSlice, resource relations, IrqSpan and ready-not-running. Preserve existing API through explicit compatibility readers only; migrate call sites so bool trust is non-authoritative.
* **Tests / data:** all fifteen mandatory loss/boundary tests listed in Evidence Lineage Redesign; round-trip serialization; old reader compatibility; source graph acyclic/backtrace validation; full Python regression after focused parser/metric tests. Use synthetic test inputs only for code semantics, never for empirical labels.
* **Completion / blockers / stop:** every derived object passes lineage validator, no unlabelled end-of-trace closure is complete, all legacy regression passes. Block on an object type that cannot retain source IDs without redesign. Stop/replan if full lineage requires ambiguous fabricated event IDs or breaks deterministic rebuild parity.
* **Freeze / paper / assets:** dedicated code/schema/docs commit, lineage migration note and regression logs; rollback to Phase 2 contract if validation fails. Supports Sections 3--5; produces lineage field table, example lineage graph and loss-induced false-interval figure.

### Phase 4 -- Real Hardware Capture Chain

* **Goal / preconditions:** turn selected hooks/backend into a capture chain whose outputs are ledger/CCM-bound, loss-accounted and immutable. Requires Phases 1--3.
* **Allowed / forbidden paths:** selected `collector/hook/<rtos>/`, target backend/BSP directory, `collector/include/trace_api.h` only when API gap is approved, collector config/export tools, `hardware/rtd_pilot/`, `tool/rtd_capture_*`, schemas/tests/docs. Forbid changing P6/P7 data and diagnosing captures before truth Pilot gate.
* **Development/docs/schema/code:** configure buffer and flush, implement export and per-Capture configuration snapshot, verify sequence continuity and LOSS/OVERFLOW, bind filter/sampling config to CCM, write firmware/config/ELF hashes to ledger, epoch-align observer and trace, preserve failed captures, lock raw trace files read-only, define directory/name form `data/raw/<board>/<session>/<case>/<capture>/` with immutable inventory/hash.
* **Tests / data:** minimum smoke test: healthy scheduling; mutex lock/unlock; controllable IRQ; GPIO epoch; logic-analyzer alignment; loss-free clean capture; controlled buffer pressure; one natural overflow capture. Integration validates raw trace -> codec -> CCM/ledger join without a diagnoser.
* **Completion / blockers / stop:** complete when clean and natural-overflow captures survive raw-to-parser audit, counters/CCM agree, and invalid captures remain accessible. Block on unrecoverable transport loss, unobservable sequence, unstable timing or recorder perturbation. Stop if overflow cannot be attributed or capture changes the fault scenario beyond Phase 0 tolerance.
* **Freeze / paper / assets:** freeze firmware/config/collector versions, raw-data inventory and acquisition SOP; no raw file overwrite. Supports Sections 4--6; produces collector/observer architecture figure, capability examples, throughput/buffer data and smoke/failure log.

### Phase 5 -- Independent Truth Pilot

* **Goal / preconditions:** establish whether a real, independently labelled data route exists before building a diagnosis method. Requires Phase 4 chain, Phase 2 ledger and Phase 3 lineage validation.
* **Allowed / forbidden paths:** new `data/rtd_pilot/` (subject to license/data policy), `docs/rtd_pilot/cases/`, ledger/observer/raw manifests, capture tools and validation tests. Forbid classifier/verifier optimization, holdout tuning, P6/P7 relabeling and Agent calls.
* **Development/docs/schema/code:** implement Case templates for F1/F2/F3, controls, injection definitions and Observer predicates; execute only after a per-Case preapproval record. Persist every attempted Capture, including non-manifested and invalid. Extend data schemas only for Case/Observer/ledger validation, not diagnosis output.
* **Tests / data:** at least 12 independent Cases (4/family) x 3 captures = 36 raw hardware captures plus controls. Audit observer-to-ledger independence, configuration diversity, source lineage generation, clean capture sufficiency and publication license. Test no labels are read from parser/diagnoser outputs.
* **Completion / blockers / stop:** all Pilot Gate conditions must pass: stable trace, independent observed manifestation, reproducible ledger, complete CCM, generated source lineage, sufficiently complete clean capture, distinguishable controls, real parameter variation, publication rights and retained failures. Block/stop on unreliable observer, unstable fault manifestation, unalignable trace/observer, instrumentation effects, license failure or synthetic-only data. No Phase 6 if any gate fails.
* **Freeze / paper / assets:** seal Case IDs/splits reserved for later, Pilot ledger, raw/observer inventory and closeout; never overwrite raw captures. Supports Sections 5--6; produces Case/control table, Case-Capture hierarchy figure, Pilot audit table and failure inventory.

### Phase 6 -- Deterministic Missing-Aware Diagnoser

* **Goal / preconditions:** implement the frozen four-state verifier only after independent-truth Pilot passes. Requires lineage-complete rebuilds and CCM-bound captures.
* **Allowed / forbidden paths:** new `parser/rtd_diagnosis.py`, `metric/rtd_diagnosis.py`, diagnostic output schemas, report/CLI adapters, deterministic rule files, tests and docs. Forbid `openai_*`, advisor/gate truth use, modification of injection labels, and possible-world/DES/automata mechanisms.
* **Development/docs/schema/code:** encode F1/F2/F3 necessary/sufficient facts, relevance calculation, `OOD -> UNKNOWN -> REFUTED/SUPPORTED` admission flow, counter-witness handling, threshold/rule versioning and audit report fields. The current alert/diag remains a baseline adapter, not silently redefined.
* **Tests / data:** unit-test each verdict and precedence; test absence evidence, unrelated gap, relevant gap, trusted positive witness, long relation crossing a gap, disabled filter, sampling, truncation, unsupported mapping, conflicting evidence and natural-overflow/mask separation. Integration runs fixed Pilot development Captures without changing labels.
* **Completion / blockers / stop:** output is deterministic, fully auditable, every conclusion names source/derived/window/CCM IDs, and no `REFUTED` uses inadmissible absence. Block on an undefined fact relevance rule. Stop/rework if a missed relevant gap yields false confirmation that cannot be corrected without universal abstention.
* **Freeze / paper / assets:** freeze rules/thresholds before validation/holdout, commit code plus rule digest and deterministic reports. Supports Sections 3--5; produces system-state diagram, verdict examples and diagnostic report schema/table.

### Phase 7 -- Baselines, Ablations and Fair Evaluation Framework

* **Goal / preconditions:** produce a fair comparison harness before formal data analysis. Requires Phase 6 deterministic output and Phase 5 Case hierarchy.
* **Allowed / forbidden paths:** `parser/rtd_baselines.py`, `tool/rtd_evaluate.py`, `spec/schema/rtd_*baseline*.json`, test fixtures isolated under `tests/python/fixtures/rtd_pilot/`, docs and analysis skeleton. Forbid access to holdout labels for tuning and extra target-method truth.
* **Development/docs/schema/code:** implement all 12 baselines and forced ablations in the Baselines section; freeze common input adapter, parameters, error/timeout policy, mask protocol, Case/group split, scorer and valid-Case policy. Add optional FO/MTL/TeSSLa only after feasibility/license review and document inability faithfully to reproduce.
* **Tests / data:** adapter equivalence on common raw input; schema validation; baseline failure retention; leakage tests enforcing template grouping; deterministic mask replay. Integration compares development/validation Captures and validates all reports share the same ledger truth IDs.
* **Completion / blockers / stop:** pass when every primary baseline has a documented runnable or documented unavailable state, no method has privileged truth, and validation selection is sealed. Block on irreproducible baseline. Stop/downgrade if fair input parity cannot be achieved.
* **Freeze / paper / assets:** commit baseline registry, adapters, parameter/split/mask manifests and first-access log; rollback before holdout access if leakage occurs. Supports Sections 6--7; produces baseline table, ablation matrix, fairness contract and evaluation flow figure.

### Phase 8 -- Formal Dataset, Evaluation and Statistical Analysis

* **Goal / preconditions:** collect/complete the narrow empirical dataset and run a frozen analysis. Requires Phase 7 freeze and no holdout tuning.
* **Allowed / forbidden paths:** `data/rtd_dataset/`, `analysis/rtd_pilot/`, `tool/rtd_analysis_*`, result schemas, immutable manifests, figures/tables sources and docs. Forbid rule/threshold/baseline parameter changes after first holdout access, data deletion and synthetic substitution.
* **Development/docs/schema/code:** target at least 12--18 independent Cases x 3 captures; preferred 18 Cases (6/family) and 54 raw captures plus controls. Apply predeclared conditions: clean, deletion variants, truncation, filter, sampling and separate natural overflow. Generate Case-clustered statistics and per-family failure analysis.
* **Tests / data:** integrity hashes, ledger/CCM/observer/lineage joins, split leakage audit, mask replay, denominator audit, blinded holdout scorer invocation and result-table regeneration. Hardware repeat checks verify recorder overhead and observer alignment remain within Phase 1 bounds.
* **Completion / blockers / stop:** complete when valid data meet independent Case minimum, all controls/failures are retained, primary comparison and uncertainty are generated from frozen pipeline, and negative results remain reported. Block on missing cases/rights/leakage. Stop/downgrade if full method is not better than simple baselines, improves UNKNOWN only by raising false confirmation, or relies on ignoring relevant gaps.
* **Freeze / paper / assets:** seal raw/normalized data version, analysis environment, result digest and closeout; a correction creates a new dataset version, never modifies frozen source. Supports Sections 6--8; produces main result, loss-condition, ablation, overhead and failure-case tables/figures.

### Phase 9 -- Optional Agent/LLM Add-on

* **Goal / preconditions:** test an optional front-end only after Phase 8 deterministic results exist. Requires separate authorization, privacy/security review and frozen deterministic verdicts.
* **Allowed / forbidden paths:** `parser/rtd_agent_frontend.py`, UI/report adapters, optional test fixtures and isolated experiment docs. Forbid raw trace mutation, CCM/lineage mutation, label creation, verdict overwrite, `CONFIRMED` setting or use of self-confidence as a metric.
* **Development/docs/schema/code:** Agent may rank precomputed candidates, suggest entity/time bindings, query read-only evidence or draft natural-language reports. Compare no-Agent, Agent and oracle binding using identical frozen evidence. Deterministic verifier always makes final state.
* **Tests / data:** prompt/input provenance, injection resistance, zero truth-path mutation, deterministic re-verification of every Agent suggestion, cost/latency and blinded utility assessment where approved. No hidden network/LLM calls in mandatory reproduction.
* **Completion / blockers / stop:** retain only if measurable benefit is significant under frozen protocol without reliability regression. Block on data/privacy approval. Stop/remove from title/contributions if benefit is absent or only appears through privileged/oracle information.
* **Freeze / paper / assets:** separate optional commit and experiment artifact; the core paper/reproduction package remains runnable without it. Supports optional Section 5/8 appendix only; produces optional comparison table and safety boundary.

### Phase 10 -- Paper Writing and Reproducibility Package

* **Goal / preconditions:** turn frozen evidence into a reproducible Chinese-core submission package. Requires Phase 8 results; Phase 9 is optional and removable.
* **Allowed / forbidden paths:** `paper/`, `docs/rtd_pilot/reproducibility/`, `analysis/`, release manifests/readmes/license files and CI/scripts. Forbid post-hoc result-changing code/data edits, claim expansion and P6/P7 historical edits.
* **Development/docs/schema/code:** write Sections 1 Introduction, 2 Background/related work, 3 problem definition, 4 system design, 5 implementation, 6 experimental design, 7 results, 8 discussion and 9 conclusion. Package source, build instructions, firmware/config, Case definitions, ledger, CCM, raw/normalized trace, Observer records, masks, rules, baselines, analyses, tables, figures, licenses, failure logs and artifact README.
* **Tests / data:** fresh-machine build/replay drill; raw hash and license audit; scripts regenerate every table/figure; artifact reviewer follows instructions without access to unpublished labels beyond declared package; verify core system works with Agent absent.
* **Completion / blockers / stop:** every sentence maps to frozen evidence and the Claim Boundary; all artifacts have rights and documented omissions. Block on redistribution restriction, irreproducible build or missing raw/observer linkage. Stop/downgrade to engineering report if a principal result cannot be regenerated or only synthetic/reference data is publishable.
* **Freeze / paper / assets:** archival release candidate, manuscript version, artifact manifest and independent reproduction report; rollback is a new version, not silent rewrite. Supports all sections; produces final figures/tables, artifact README and submission checklist.

### Phase 11 -- Final Freeze and Pre-submission Audit

* **Goal / preconditions:** independently verify that no claim exceeds data, no historical route was revived and all release assets are intact. Requires Phase 10 candidate package.
* **Allowed / forbidden paths:** review reports, errata/addenda, submission metadata and release tags only after explicit authorization. Forbid source/data/schema changes during audit except approved errata in new files; forbid auto-commit/push/tag.
* **Development/docs/schema/code:** perform claim-to-evidence audit, truth-path isolation audit, privacy/license audit, baseline parity audit, split/leakage audit, data integrity audit, reproducibility rerun and reviewer checklist. Record every accepted risk and unresolved limitation.
* **Tests / data:** full regression; end-to-end artifact rerun; independent raw-to-table provenance sample; randomly sampled observer/ledger/Capture cross-check; confirm all failure Captures/control conditions are accounted for.
* **Completion / blockers / stop:** `blocking=0`, `major=0`; all permanent stops below false; contribution text remains C1--C3 only. Block on any unverifiable result. Stop submission and downgrade if core evidence is missing, fairness breaks, truth is analyzer-derived or Agent is necessary.
* **Freeze / paper / assets:** create closeout and release/submission record only after approval; preserve exact HEAD, environment and artifact hashes. Supports final Section 8 limitations and submission package; produces paper-readiness gate report and final decision record.

## Paper Figure/Table Plan

Planned figures: (1) system overall architecture; (2) trace/CCM/lineage/verdict relation; (3) independent-truth data flow; (4) Scenario Template--Case--Capture--Mask hierarchy; (5) concrete lineage example; (6) LOSS causing an invalid derived interval; (7) four-state diagnostic state machine; (8) experiment process/split flow.

Planned tables: (1) fault families and Case parameters; (2) CCM fields; (3) derived evidence lineage fields; (4) baselines; (5) data scale; (6) primary diagnostic result; (7) results by loss condition; (8) ablations; (9) runtime/memory/collector overhead; (10) failure cases; (11) Claim Boundary; (12) functionality comparison with related methods.

Each figure/table has a generating script, input manifests, output hash and a statement of whether it uses development, validation or holdout data. No table may report P6/P7 synthetic/reference evidence as C3 real-data evidence.

## Reproducibility Package

The final package must include source code; build instructions; firmware/config; board/BSP and toolchain versions; Case definitions; ledger; CCM; raw and normalized traces; Observer records; masks; deterministic rules; baseline adapters; analysis scripts; result tables; figures; licenses/notices; failure logs; environment lock files; artifact README; and a limitations/data-access statement. It must state whether raw hardware traces are public, restricted or reproducibly requestable. Hashes and package replay establish file identity/reproduction only; the independent ledger/observer establishes the truth boundary.

## Risk Matrix

| Risk | Early signal | Mitigation / decision | Stop or downgrade trigger |
| --- | --- | --- | --- |
| Hardware unavailable | no flashable board/toolchain | switch to preapproved backup | no real board: permanent stop. |
| Observer unstable | epoch drift/ambiguous pins | improve wiring, timer/serial redundancy | no independent Observer: permanent stop. |
| Fault not manifested | enabled injections lack observer predicate | tune only on development, retain attempts | cannot stably manifest: Pilot stop. |
| Trace perturbs system | observer/clean behavior diverges | measure overhead, buffer/flush tuning | perturbation exceeds contract: stop/reselect collector. |
| Overflow uncontrollable | counters cannot be reproduced | pressure protocol, direct counter audit | un-attributable overflow: no loss claim. |
| Lineage breaks regression | derived IDs missing/compatibility errors | incremental typed migration and validators | cannot retain complete lineage: permanent stop. |
| Historical filter/sampling unrecoverable | config not serialized | CCM generated at capture start/end | incomplete CCM: invalid Capture. |
| Too few independent Cases | repeated variants share template | recruit distinct configurations/templates | <4/family: permanent stop. |
| Leakage | template or hash crosses split | grouped split and access logs | leakage after holdout: new holdout/downgrade. |
| License/data release | board/BSP/raw trace rights unclear | clear before collection, retain registry | unresolved publication rights: stop/downgrade. |
| Baseline unfairness | unequal inputs/parameters | frozen adapter contract and audits | parity impossible: no superiority claim. |
| No gain over global overlap | main metrics equal/worse | report negative, analyze scope | full method not better: downgrade. |
| Fewer UNKNOWN but more false confirmation | primary safety metric worsens | enforce false-confirmation guard | cannot satisfy: reject method claim. |
| Agent has no value | no-Agent parity | omit Agent | never rescue paper with Agent. |
| Contribution too weak | C1/C2 indistinguishable from baseline | narrow to system/empirical report | no clear empirical benefit: engineering report. |

## Stop Conditions

The paper route permanently stops, rather than being repaired with wording, when any occurs: (1) no real hardware; (2) no independent observer; (3) no immutable ledger; (4) complete lineage cannot be implemented; (5) only P6/P7 are usable; (6) fewer than four independent Cases per family; (7) performance arises by ignoring relevant gaps; (8) missed-relevant-gap false confirmation cannot be repaired; (9) full method is not better than simple baselines under frozen protocol; (10) license/data release cannot be resolved; or (11) the contribution still depends on Agent packaging. A stopped route may yield an explicitly labelled engineering/provenance report, never a retroactive diagnosis-method claim.

## Commit and Freeze Strategy

Before each authorized Phase, record `START_HEAD`, branch, clean/dirty worktree and allowed-path list. During the Phase, maintain immutable input inventory and a change log. At closeout: run focused tests then full regression appropriate to changed surfaces; run hardware/observer tests where applicable; audit schema/data/license/lineage artifacts; obtain independent review; require `blocking=0` and `major=0`; create one focused commit and a closeout document; record `FINAL_HEAD`; confirm clean worktree. Rollback is to the prior phase freeze or a new corrective commit, never a destructive reset, historical-doc rewrite, raw-data overwrite or silent result deletion.

## Paper-Readiness Gates

| Gate | Required evidence | Failure consequence |
| --- | --- | --- |
| G0 boundary | Phase 0 approved narrow claim/metrics/splits | no code/data work. |
| G1 feasibility | real board + independent observer + legal toolchain | no Pilot. |
| G2 contracts | immutable ledger + CCM validators | no hardware dataset. |
| G3 lineage | full raw-to-derived lineage and loss tests | no diagnosis implementation. |
| G4 Pilot | 12 Cases/36 captures minimum, controls, retained failures | no method/evaluation claim. |
| G5 safety | deterministic four-state verifier, no false admission tests | no baseline comparison. |
| G6 fairness | frozen baselines/splits/masks/scorer | no holdout access. |
| G7 empirical | Case-level primary results, overhead, failure analysis | downgrade to engineering report. |
| G8 artifact | independently rerunnable data/code/license package | no submission. |

## Final Recommendation

The correct next authorization is not a diagnoser implementation. It is a bounded **Phase 0** request covering: Paper Claim Boundary, Hardware/RTOS/Observer Feasibility, and Ledger/Capability Contract Freeze. Its purpose is to determine whether the real-system evidence path exists. Only a passed independent-truth Pilot permits Phase 6 deterministic diagnosis work. The route is credible for a narrow Chinese-core application paper only if C1--C3 all receive real evidence; it is not a promise of acceptance.
