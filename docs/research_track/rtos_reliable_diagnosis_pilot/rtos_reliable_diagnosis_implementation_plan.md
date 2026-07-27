# RTD-Pilot: RTOS 轨迹可信诊断与主动故障边界探索系统的可靠诊断核心子计划

**状态：规划，未授权实现或实验。**  本文是后续独立开发阶段的总纲，不表示本文中任何未来数据、硬件、诊断器、实验结论或论文主张已经实现。除本文件外，本轮未修改代码、测试、Schema、Fixture、历史 Phase 0--7 文档或数据。

## Executive Summary

本路线的目标是建立一套可审计的单核 RTOS Trace 可靠诊断系统：每条 Capture 都有配置能力记录与实际完整性记录，每个诊断派生事实都可追溯到原始事件和边界，每个真实故障标签都独立于 parser、diagnoser 和 Agent，并且缺失条件下的结论使用保守的 `SUPPORTED`、`REFUTED`、`UNKNOWN`、`OOD` 状态。

它不是恢复已否决的 CCF-C 算法路线，也不是继续包装 Agent。论文的中心问题是：在过滤、采样、缓冲溢出、事件丢失、截断和派生关系边界不完整时，Capture Capability Manifest（CCM）和完整 evidence lineage 能否减少错误确认/错误否定，并比“任何 gap 重叠即拒答”更少产生不必要的 `UNKNOWN`。完整系统完成后，预期支撑的是单 RTOS、单板卡、窄故障族的中文核心系统/可靠性应用论文；不保证录用，也不支持通用 RTOS、通用根因或新验证理论的宣称。

推荐阶段名保持为 **RTD-Pilot: RTOS Reliable Diagnosis Independent-Truth and Evidence-Lineage Pilot**。其中 RTD-Pilot 是项目阶段名，不是另一个算法或论文主方法名。

## Relationship to the Overall Graduation System

本文是“RTOS轨迹可信诊断与主动故障边界探索系统”的**可靠诊断核心子计划**，不是整个毕业设计的唯一总计划。总系统的三项技术及其明确交接如下。

| 技术 | 功能 | 输入 | 输出 | 边界与交接 | 本计划承担内容 |
| --- | --- | --- | --- | --- | --- |
| 1. 依赖侧车索引与有界证据闭包导出 | 将诊断、预警和实验计划绑定到原始事件、派生关系、版本和闭包预算，生成可复查证据包。 | `EvidenceExportSeed`、sidecar 索引、闭包预算与版本。 | bounded closure、proof digest、evidence package、external reopen 结果。 | 不产生故障真值；closure/package 状态不得回写诊断。RTD 只交付种子和引用，不重做闭包算法。 | 仅增加诊断/预警目标接入、稳定导出接口及闭包集成合同。 |
| 2. 基于观测完整性与证据血缘的 RTOS 轨迹缺失感知诊断 | 在 CCM、CIR、完整 evidence lineage 和固定故障合同下作出四状态诊断。 | `RTDDiagnosisInputBundle`、统一事件流、`RebuildBundle`、CCM、CIR、Evidence Lineage、RTOS Semantic Profile、诊断合同以及规则/阈值版本。 | `RTDDiagnosticReport`、`EvidenceExportSeed`、冻结的诊断字段。 | Ledger 和 Observer 仅用于 Capture 身份关联、独立真值构建和离线实验评测；运行时诊断器不得读取 `fault_manifested`、`manifestation_interval`、Observer 故障标签或任何独立真值结果。Ledger 中的配置身份字段可供评测和数据管理使用，但不得向诊断器泄露故障显现结果；`RTDDiagnosisInputBundle` 不得包含独立真值标签。`RTDDiagnosticReport` 的 verdict 仅由事件、CCM、CIR、lineage、Semantic Profile 与冻结诊断合同产生。 | **完整实现本技术**，仅限 F1/F2/F3 和单核主 RTOS。 |
| 3. CAPE-RT | 在诊断层上使用 healthy、near-miss、fault 与观测缺陷 CaptureEpisode/CaseEpisodeBundle，进行前兆、边界估计与下一轮实验规划。 | 冻结的诊断报告、`CAPEFeatureBundle`、CaptureEpisode/CaseEpisodeBundle、模型上下文与安全合同。 | warning、边界估计、受 Gate 约束的实验计划及相应证据种子。 | 不能进入诊断真值路径或改写 RTD。完整模型、Agent 与主动学习算法另立 `cape_rt_implementation_plan.md`。 | 仅建立数据、接口与安全交接，不假装完成 CAPE-RT。 |

诊断方法在无 CAPE-RT、无 LLM 条件下必须成立。技术一的 bounded closure、sidecar、proof digest 与 package replay 保持在 truth path 外；技术三只能消费冻结的诊断输出。

## Repository Baseline

本节为只读审计结果，行号对应本次审计时 `main` 的 `START_HEAD=00b599fa62b5704a146e348df86cf56df0d0a94f`。Phase 0--7 的冻结事实和审查文档均保持原样。

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
**必须新增：** 真实板卡与 firmware、独立 Observer、immutable Ledger、CCM、CIR、完整 Evidence Lineage、Case/control/split/mask 合同、四状态确定性 Verifier、硬件采集链以及统计与复现资产。

## Current Implementation and Planned Construction Matrix

下表仅陈述本次仓库审计可见事实；`planned but absent` 指本计划要求、但尚未形成正式实现资产，绝不写成现有能力。

| 对象 | 当前状态 | 审计结论与本计划定位 |
| --- | --- | --- |
| event dictionary | 已实现 | `spec/assets/dictionary.json` 可复用；新 marker、版本绑定和 RTD 合同仍需规划实现。 |
| collector | reusable infrastructure | host collector、filter、buffer、flush 可复用；非已验证板级采集链。 |
| uC/OS hook | scaffold only | 有 hook mapping；不是完整 target backend 或硬件证据。 |
| parser / align / rebuild | reusable infrastructure | decode、对齐、重建与 `UntrustedWindow` 可复用；完整 lineage、CCM/CIR binding 尚缺。 |
| sidecar / closure / package | reusable infrastructure | 既有 sidecar、bounded closure、proof/package/reopen 仅作证据导出基础，绝不产生真值。 |
| CCM | planned but absent | 尚无每 Capture 的正式实现 CCM；只描述配置能力。 |
| CIR | planned but absent | 尚无正式 `rtd_capture_integrity_record` schema/validator；记录实际退化。 |
| 完整 evidence lineage | planned but absent | 现有 bool trust 与局部 evidence ref 不足；Phase 3 建立完整血缘。 |
| 四状态诊断 | planned but absent | 现有 alert/`Diagnosis` 是 baseline，非 `SUPPORTED/REFUTED/UNKNOWN/OOD`。 |
| 真实 Case / Observer / Ledger | planned but absent | 仓库没有真实硬件 Case、独立 Observer 或不可变 Ledger。 |
| CaptureEpisode / CaseEpisodeBundle | planned but absent | 仅为 RTD 到 CAPE-RT 的冻结数据交接，不是当前真值。 |
| CAPE 特征 | future separate plan | `CAPEFeatureBundle` 只定义交接；特征工程与模型训练属于独立 CAPE-RT 计划。 |
| 边界模型 | future separate plan | 本计划不训练或宣称已实现风险/边界模型。 |
| LLM Agent | future separate plan | 仅 Phase 9B 的可选只读规划器；不属于 RTD 核心。 |
| 多 RTOS Adapter | future separate plan | 一个主单核 RTOS 完成完整实验；第二 RTOS 最多有限适配验证。 |
| queue backlog、完整 priority inheritance、deadline release/completion、deadlock、stack/heap/HardFault root cause、multicore causality | explicitly excluded | 不进入首期完整实现或实验；新 Adapter、事件字典、Payload 与故障合同具备完整语义后才可在 Future Extension 另行评估，否则输出 `OOD`。 |

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

* **C1，RTOS Trace 采集能力与证据血缘模型：** CCM 记录每一次 Capture 配置上可观察的事件、过滤/采样、buffer/flush、dictionary/mapping 与时间边界；CIR 记录实际 sequence/loss/overflow/truncation/alignment 退化；派生 task、mutex、IRQ 证据具有完整原始事件血缘。
* **C2，缺失感知保守诊断：** 在使用负面证据、持续时间证据和派生区间证据前，联合检查 event channel、开闭边界、CCM 配置能力、CIR 实际完整性以及完整 Evidence Lineage，输出 `SUPPORTED`、`REFUTED`、`UNKNOWN` 或 `OOD`，并保留 witness、counter-witness、相关完整性记录和版本信息。
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
| CCM 与 CIR | 2/4 | 每 Capture 的 CCM、CIR、collector config hash、实际 loss/overflow/sequence 完整性记录。 |
| 完整派生 lineage | 3 | source/boundary IDs、lineage state、round-trip tests。 |
| 真实三故障族 Cases/controls | 5 | Case definition、observer manifestation、retained non-manifested/invalid captures。 |
| 四状态 deterministic verifier | 6 | predeclared rule/threshold version、auditable reports。 |
| RTD 证据闭包交接 | 6/7 | 稳定 `EvidenceExportSeed`、sidecar/closure/package/reopen 集成合同。 |
| CaptureEpisode / CaseEpisodeBundle / CAPE bridge / safety contract | 9A | CaptureEpisode、CaseEpisodeBundle、特征、模型上下文、Gate 和安全审计，不训练 CAPE 模型。 |
| 公平 baseline 与 split/mask protocol | 7 | frozen baseline registry、split and mask manifests。 |
| 正式数据、统计、论文结果 | 8/10 | Case-level analysis、holdout report、tables/figures and scripts。 |

## Overall Architecture

```text
1. Independent Truth Plane (never reads diagnoser output)
Injection Definition -> Ledger -> Board / Observer

2. Deterministic Diagnosis Plane
Raw Trace -> Decode / Align / Rebuild -> CCM + CIR + Lineage
          -> Four-State Diagnoser -> RTDDiagnosticReport

3. CAPE-RT Downstream Plane
DiagnosticReport + CAPEFeatureBundle + CaptureEpisodes / CaseEpisodeBundles
          -> Local Risk / Boundary Models -> optional LLM Planner
          -> Deterministic Gate -> approved capture/injection plan
          -> new Case / Capture loop

4. Evidence / Reproducibility Plane (outside truth)
DiagnosticReport / Warning / Plan -> EvidenceExportSeed
          -> Sidecar / Bounded Closure -> Evidence Package / Reopen
```

Observer never reads diagnoser output；CAPE-RT never writes diagnostic truth；closure、proof 或 package 不证明故障。经批准的新实验必须重新进入 Ledger、CCM、CIR、Observer 与 raw-trace 链路，不能复用下游推断代替独立事实。

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

CCM 只回答“**配置上能够观察什么**”。每个 `capture_id` 有唯一、版本化 CCM，由冻结的 collector 配置生成，绝不从缺失事件或采集结果反推。Required fields:

```text
capture_id, event_types_enabled, task_filter, resource_filter, irq_filter,
core_filter, sampling_configuration, buffer_capacity, flush_policy,
timestamp_source, clock_resolution, payload_fields, dictionary_version,
mapping_version, collector_version, trace_start_boundary, trace_end_boundary
```

`overflow_count`、`natural_overflow`、实际 `sequence_continuity`、实际 high-watermark、truncation 与 corruption 都是运行结果，必须从 CCM 迁移到 CIR。CCM 仅保留配置和能力字段。

## Capture Integrity Record

CIR 正式回答“**本次采集实际上哪里退化**”。每个 `capture_id` 有唯一、版本化 CIR，来自 collector、decoder、alignment 与 Observer 的不可变输入；它不产生 manifestation truth 或 verdict。至少包含：

```text
capture_id, sequence_continuity, sequence_gaps, LOSS, OVERFLOW, overflow_count,
buffer_high_watermark,
truncation, corruption, alignment_degradation, mapping_mismatch, observer_loss,
affected_time_intervals, affected_core_channel_entity, natural_overflow,
integrity_status, reason_codes
```

每个 Capture 必须唯一绑定 `Ledger + CCM + CIR + Observer Record + Raw Trace Hash`。`UntrustedWindow` 只是 CIR 在重建/诊断中的分析投影，不等于完整 CIR，也不能替代其完整性事实。

CIR 只能记录 Observer 采集通道自身的完整性事实，例如 observer 文件缺失、通道丢失、时间戳损坏、epoch 缺失、对齐退化或时钟漂移超界；`observer_loss` 表示 Observer 通道是否完整，不表示故障是否发生。CIR 不得包含 Observer 对故障是否显现的判断、`fault_manifested`、`manifestation_interval`、故障类型、healthy/near-miss/fault 标签或 Observer manifestation predicate 的布尔结果。Observer manifestation 结果只能保存在独立 Truth/Evaluation Plane，由评测器读取；运行时诊断器只能读取 CIR 中的观测完整性元数据，不能通过 CIR 间接获得故障真值。

```text
Observer epoch missing / observer file truncated / alignment drift exceeded
-> 可以写入 CIR，表示观测通道退化。

Observer confirms fault manifested at interval I
-> 不得写入 CIR，只能写入独立 Observer truth record 并用于离线评测。
```

| 维度 | CCM | CIR |
| --- | --- | --- |
| 回答的问题 | 配置上可以观测什么 | 本次实际发生了何种退化 |
| 产生时机 | Capture 前/冻结配置时 | Capture 后由不可变采集、解码、对齐与 Observer 输入汇总 |
| 典型字段 | enabled event、filter、sampling、buffer/flush 配置、版本、边界 | gap、LOSS、OVERFLOW、实际 watermark、truncation、corruption、alignment/mapping/observer degradation |
| 不得包含 | 实际丢失、实际 sequence、overflow 结果、verdict | 故障真值、诊断相关性裁决、Agent 输出 |

Negative evidence is admissible only when all prerequisite event types and payload fields are enabled, entity/core filters include the target, sampling is disabled or a predeclared valid statistical rule applies, required start/close boundaries are inside capture bounds, CIR confirms continuous relevant channels and no relevant degradation, mapping supports the event semantics, and alignment uncertainty does not consume the threshold margin. Disabled filter or unsupported payload/mapping is `OOD`; sampling, relevant loss, overflow or truncation is generally `UNKNOWN`; absence is never treated as non-occurrence by default. Offline mask is an experimental observation condition and must not be written as natural overflow.

## Evidence Lineage Redesign

Phase 3 replaces the current bool-only trust interpretation while preserving a compatibility adapter for old callers. `TaskStateSeg` (called TaskStateSegment in this plan), `ExecSlice`, wait/hold `ResourceEdge`, `IrqSpan`, ready-but-not-running interval, Alert and Diagnosis evidence all require:

```text
source_event_ids, open_event_id, close_event_id, boundary_event_ids,
derivation_rule_id, derivation_version, interval_start, interval_end,
intersecting_untrusted_window_ids, capture_capability_ref, capture_integrity_ref,
lineage_status
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
          -> CCM + CIR binding
          -> deterministic fault-pattern verifier
          -> auditable SUPPORTED / REFUTED / UNKNOWN / OOD report
```

CCM determines whether the required observation capability was configured; CIR determines whether this Capture actually has sequence gaps, LOSS, OVERFLOW, truncation, corruption, alignment degradation, mapping mismatch or observer-channel degradation. The verifier must use both. A Capture with CCM but no valid CIR cannot enter formal diagnosis. CIR does not contain Observer manifestation truth, and `UntrustedWindow` is only a CIR analysis projection, not a CIR replacement. Every `OOD`、`UNKNOWN`、`REFUTED` or `SUPPORTED` decision records its CCM and CIR references.

`SUPPORTED` requires complete, relevant, capability-supported and integrity-admissible witness evidence meeting the frozen pattern. `REFUTED` requires trustworthy counter-witness, or a necessary pattern demonstrably absent under complete observation. `UNKNOWN` means a required boundary, channel or relation is affected by loss, overflow, sampling, filter, truncation or CIR-recorded degradation. `OOD` means dictionary, mapping, payload, alignment, CCM or CIR fundamentally cannot express the judgement. Priority is `OOD` before `UNKNOWN` when the semantic channel is unavailable, and `UNKNOWN` before `REFUTED` when absence cannot be admitted.

The verifier must use fact-specific relevance, not global temporal overlap. A gap cannot erase independently complete positive evidence; a long derived fact crossing a relevant gap cannot remain complete; irrelevant gaps do not force abstention. It must distinguish natural overflow from offline deletion, reject unsupported mapping as `OOD`, and never inspect Agent confidence, package validity, proof digest or replay status.

Every output records:

```text
case_id, capture_id, fault_family, entity_bindings, candidate_interval, verdict,
witness_event_ids, counter_witness_event_ids, derived_evidence_ids,
lineage_status, CCM_ref, CIR_ref, relevant_window_ids, unknown_reason,
ood_reason, diagnoser_version, rule_version, threshold_version
```

## Canonical Inter-Module Contracts

这些是跨模块的稳定交接合同，而不是当前已存在的 schema。所有版本化字段应能定位相同的 `capture_id`；任何下游消费者均不得反写 Ledger、Observer、CCM、CIR、lineage 或 RTD verdict。

| 合同 | 必填字段 | 交接规则 |
| --- | --- | --- |
| `RTDDiagnosisInputBundle` | capture identity；parsed events/`RebuildBundle`；CCM；CIR；lineage registry；RTOS Semantic Profile；diagnosis contract；rule/threshold version | 是四状态诊断唯一的规范输入；缺失或语义不支持时 fail closed 到 `UNKNOWN`/`OOD`。 |
| `RTDDiagnosticReport` | `case_id`/`capture_id`；`fault_family`/`entity_bindings`/`interval`；verdict；`witness_event_ids`；`counter_witness_event_ids`；`derived_evidence_ids`；`lineage_status`；CCM/CIR refs；`relevant_window_ids`；UNKNOWN/OOD reason；diagnoser/rule/threshold version | 冻结后只读；是闭包和 CAPE 的输入，绝不充当 Ledger/Observer 训练真值。 |
| `EvidenceExportSeed` | `target_type`/`target_id`；`capture_id`/`interval`；`evidence_refs`；`derived_refs`；`lineage_refs`；CCM/CIR refs；rule/version | 交给依赖侧车索引及 bounded closure；可引用 witness、counter-witness、CCM、CIR 和 lineage。 |
| `CAPEFeatureBundle` | normalized trajectory features；feature availability/quality；diagnosis result；evidence gaps；platform/model applicability context；provenance references | 缺失特征禁止静默填零；必须显式保留 unavailable/quality 状态。只可消费冻结 RTD 输出。 |
| `EpisodeCandidate` | `episode_id`、`case_id`、单个 `capture_id` 的 CaptureEpisode，或同一 Case 的 `capture_episode_ids` 所构成 CaseEpisodeBundle；platform/RTOS/firmware/workload identity；injection parameters；observer outcome；diagnosis outcome；state trajectory；evidence refs；CCM/CIR/lineage refs；quality/category status | 仅可表示单个 CaptureEpisode，或同一 Case 下的 CaseEpisodeBundle；不得跨 Case、跨 split 或跨 Scenario Template 聚合。训练标签来自 Ledger 和 Observer，**不来自 diagnosis verdict**。 |

| RTD 输出方向 | 传递对象 | 不允许的反向影响 |
| --- | --- | --- |
| 到依赖侧车/闭包 | `RTDDiagnosticReport -> EvidenceExportSeed -> sidecar/bounded closure -> evidence package/reopen` | exact/bounded/degraded closure、proof/package validity 不能改变 verdict，不能覆盖 `UNKNOWN`/`OOD`，不能生成真值。 |
| 到 CAPE-RT | `RTDDiagnosticReport + CAPEFeatureBundle + EpisodeCandidate` | CAPE 模型、warning、Agent、风险分数或实验计划不能修改诊断、Ledger、Observer、CCM、CIR 或 lineage。 |

## Episode Definition and Categories

`CaptureEpisode` 是一次有效或保留 Capture 的完整运行演化记录，绑定唯一 `capture_id`、CCM、CIR、Observer Record、Ledger Record、Evidence Lineage 与诊断结果；包含 stable baseline、injection enable、degradation/precursor、manifestation 或 recovery 及 capture end。一个 Capture 最多生成一个主 CaptureEpisode；无效 Capture 可保留 `INVALID_CAPTURE` 记录，但不得进入训练主样本。CaptureEpisode 不是独立统计 Case。

`CaseEpisodeBundle` 是同一 `case_id` 下多个独立 CaptureEpisode 的集合，用于描述同一参数化 Case 的重复执行、稳定性和采集噪声。它不得跨不同 Case 聚合；统计主单位仍为 Case。同一 Case 的多个 CaptureEpisode 不得被拆分至 development、validation 与 holdout 的不同集合。

CaptureEpisode 统一使用 `category` 枚举：`HEALTHY`、`NEAR_MISS`、`FAULT`、`OBSERVABILITY_DEFECT`、`INJECTION_ENABLED_NON_MANIFESTED`、`INVALID_CAPTURE`、`WRONG_ENTITY_CONTROL`。

* `FAULT`：独立 Observer 确认预注册故障显现。
* `HEALTHY`：预注册健康控制且 Observer 未确认故障、Capture 有效；它不是 `REFUTED` 的自动映射。
* `NEAR_MISS`：出现预注册退化或前兆，但 Observer 未确认故障，且在 Capture 结束前恢复或保持在安全条件内。
* `OBSERVABILITY_DEFECT`：主要异常来自 filter、sampling、LOSS、OVERFLOW、truncation、alignment 或 mapping。
* `INJECTION_ENABLED_NON_MANIFESTED`：Ledger 显示注入已启用，但 Observer 未确认预注册 manifest；它必须保留，不能被静默当作 healthy 或 invalid。
* `INVALID_CAPTURE`：身份、Observer、校准或实验协议无效。
* `WRONG_ENTITY_CONTROL`：预注册注入影响了错误的 task/resource/IRQ/entity；它是控制，不是目标故障的反例真值。
* `UNKNOWN` 不得自动标为 near-miss；`REFUTED` 不得自动标为 healthy；diagnosis outcome 只能是 CaptureEpisode/CaseEpisodeBundle 字段或特征，不能作为训练真值。

## RTOS Adaptation Boundary

轻量适配架构为 `RTOSAdapter`，由 `CollectorHookAdapter`、`EventMappingAdapter`、`SemanticProfile`、`CapabilityProvider`、`EntityMappingProvider` 和 `DiagnosisContractProvider` 组成。`SemanticProfile` 至少描述 priority direction、preemption、time slicing、IRQ nesting、priority-inheritance capability、core count、supported events/payloads。

一个主单核 RTOS 完成完整实验；第二 RTOS 仅作为未来或有限适配验证，不是本计划强制门槛。不得宣称任意 RTOS 无修改通用。若 Adapter、事件字典、Payload 或故障合同没有完整语义，相关故障族结论为 `OOD`，而不是补猜或扩展到 queue/deadline/multicore/root-cause。

## Data Format Contract

| 资产 | 格式 | 约束 |
| --- | --- | --- |
| raw board trace | binary `.trace` | 不可覆盖；以 raw trace hash 绑定 Capture。 |
| normalized events | JSONL 或内部 `UnifiedEvent` | 必须绑定 Capture、dictionary/mapping/decoder 版本。 |
| Ledger / CCM / CIR / Observer / CaptureEpisode / CaseEpisodeBundle | JSON 或 JSONL | 保留身份、版本和交叉引用。 |
| CAPE features | Parquet，必要时 CSV | 保留 feature schema/version、availability 和 quality。 |
| dependency sidecar index | SQLite | 只保存可追溯引用；不成为真值。 |
| evidence package | 目录包或压缩包 | 包/reopen/proof 只证明制品完整性。 |
| LLM API input | sanitized compact JSON | 不得包含 raw trace、完整 event stream、sidecar rows、路径、密钥、Observer truth 或 holdout 标签。 |

所有 derived artifacts 必须绑定 `capture_id` 和版本。LLM 即使使用市面 API，也只能接收上述脱敏精简 JSON。

## Active Experiment Boundary

主动实验只属于总系统的下游受控交接，不是 RTD truth path。其合同为 `ExperimentActionRegistry`、`HardwareSafetyEnvelope`、`ExperimentBudget`、`ApprovalPolicy`、`RollbackPolicy`、`StopRule` 和 `CaseOrigin`。

* Agent/optimizer 只可在 development 实验空间提出新 Case；validation 与 holdout 的参数、Case 和标签不得被主动学习修改。
* 所有故障注入参数必须落在预注册 `HardwareSafetyEnvelope` 的安全上下限内；每个计划先经确定性 `GateDecision`，再经人工审批。
* 执行后必须重新生成 Ledger、CCM、CIR、Observer 和 raw trace；下游 CaptureEpisode/CaseEpisodeBundle 或模型不能替代这些记录。
* failed、rejected 与 invalid plans 都必须保留。Agent 不得访问 holdout truth，主动实验不得在生产设备执行，只能在受控实验环境。

`CaseOrigin` 区分 preregistered、development-approved-active、validation-frozen 与 holdout-frozen；它是反泄漏审计字段，不是诊断证据。

## Case and Control Design

All labels are defined before diagnosis and checked by Observer/ledger. A diagnosis result is never a label.

| Family | Frozen first-version pattern | injection/observer predicate | controls |
| --- | --- | --- | --- |
| F1 Bounded Task Interference | Target is READY but does not execute within threshold while a same-core higher-priority task occupies CPU for the predeclared interval. It is not infinite starvation. | Workload/injection controls competing task duty/window; GPIO marks target-ready and injector epochs; observer confirms competing interval/milestone. | healthy: no injected occupancy; near-miss: just below duration; wrong-entity: occupancy affects another target/core entity. |
| F2 Mutex Contention / Long Hold | waiter waits on mutex, holder holds it, intervals overlap and wait/hold exceed threshold. Priority inversion is only a pattern subclass. | Lock holder delay/work injection; observer marks lock/critical duration and waiter milestone. | healthy release; near-miss below threshold; wrong holder/resource. |
| F3 IRQ Interference | Long ISR, excessive IRQ frequency or IRQ occupancy overlaps target ready-but-not-running interval. It is not sole causal root cause. | Timer/peripheral IRQ generator with controlled rate/duration; GPIO IRQ enter/exit and target marker observed independently. | healthy rate; near-miss below rate/duration; wrong IRQ or unrelated task. |

For each family, Phase 5 writes a scenario template, parameters, unique Case rationale, injection definition/version, expected trace pattern (not label), Observer manifestation predicate, invalid conditions and all controls. Every Capture intended for CAPE handoff should retain the complete phase sequence `stable baseline -> injection enable epoch -> degradation/precursor interval -> manifestation or recovery -> capture end` with `baseline_interval`、`injection_enable_epoch`、`precursor_candidate_interval`、`manifestation_interval`、`recovery_interval`、`capture_end` 和 `alignment_error_bound`。`precursor_candidate_interval` 是分析结果，不是独立真值。

RTD 核心诊断最低数据规模为 12--18 独立 Cases、每 Case 至少 3 次 Capture（下限为 12 Cases/36 Captures），另保留 controls 与 enabled-but-not-manifested attempts。CAPE 的建议规模是 24--36 独立参数化 Cases、72--108 基础 Captures，并覆盖 healthy、near-miss 与 fault；这不是 RTD Phase 5/8 的强制论文门槛。数据不足时不得训练大型 Transformer，也不得同时宣称复杂分类、精确剩余时间和跨 RTOS 泛化；优先规则、XGBoost/Random Forest、Gaussian Process/Bayesian Optimization 等轻量方法，复杂模型只能进入独立 CAPE-RT 计划。

## Baselines and Ablations

The baseline registry is versioned before holdout access. All evaluated methods receive identical admissible analysis inputs, including the same raw trace, normalized events, fault vocabulary, thresholds, observation masks, CCM, CIR, lineage artifacts, split metadata and valid-Capture policy, except that each registered baseline or ablation may explicitly ignore designated fields according to its frozen definition. No evaluated method receives Case truth, Observer manifestation results, `fault_manifested`, `manifestation_interval`, control labels, Episode category labels or holdout labels as inference inputs. No evaluated method receives independent truth as an inference input, and the proposed method receives no additional admissible analysis information beyond the fields explicitly required by its frozen design. The sealed evaluator may access independent Case truth and Observer records only after all method outputs are frozen, solely to compute evaluation metrics.

```text
Method Inference Plane:
只能读取允许的 Trace、CCM、CIR、Lineage、规则、阈值与配置。

Sealed Evaluation Plane:
仅在方法输出冻结后，通过 case_id 和 capture_id 连接独立真值，并计算指标。
```

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

The hierarchy is `Scenario Template -> Case -> Capture -> CaptureEpisode`; `Case -> CaseEpisodeBundle -> multiple CaptureEpisodes` describes only repeats of that same Case. Observation Condition/Mask and Diagnosis Result are nested annotations of a CaptureEpisode. The principal inferential unit is **Case**. CaptureEpisodes, masks, repeated runs, models and prompts are nested sensitivity observations, never independent Cases. Development/validation/holdout are grouped by template or genuinely distinct Case family so near variants never cross splits; all CaptureEpisodes of one Case remain in the same split. Freeze threshold selection on development/validation; first holdout access occurs only after registry, parameters, scorer and split digests are sealed.

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

* **Goal / preconditions:** register claim boundary, three fault families, RQs, metrics, acceptance tolerance, Case hierarchy, split/stop conditions, data rights checklist and the Phase 1 hardware/RTOS/Observer feasibility test protocol before code. Requires only this plan and reviewer authorization.
* **Allowed / forbidden paths:** add `docs/research_track/rtd_pilot/phase0_*`, `docs/rtd_pilot/contracts/`; forbid `collector/`, `parser/`, `metric/`, `desktop/`, `spec/`, `tests/`, all frozen Phase 0--7 docs and fixtures.
* **Development/docs/schema/code:** freeze claim/method boundary, F1/F2/F3 fault/control vocabulary, threshold ownership, metric denominator definitions, truth/observer separation, and conceptual Ledger/CCM/CIR/lineage fields and authority drafts. Add claim-boundary, experiment-contract, data-management, license/risk and reviewer records. No implementation schema, validator or code change in this phase.
* **Tests / data:** documentation consistency/lint and conceptual-contract review only; do not connect hardware, flash firmware, run GPIO/logic-analyzer/Observer capture, collect data or run experiments. Verify that every claim maps to a future measurable asset.
* **Completion / blockers / stop:** complete when reviewers approve a single-RTOS/single-core scope and Phase 1 test card. Block on unresolved RTOS/data license, undefined Observer predicate or no candidate platform. Stop/defer the paper route if it requires restoring abandoned methods or Agent as a necessary contribution.
* **Freeze / paper / assets:** commit only Phase 0 documents after approval; rollback point is the pre-Phase-0 commit. Supports Sections 1, 3 and 6; produces Claim Boundary Table, initial fault-family table, protocol digest and reviewer report.

### Phase 1 -- Hardware, RTOS and Independent Observer Feasibility

* **Goal / preconditions:** select primary and backup platform through the matrix above and demonstrate observer feasibility, without diagnosis coding. Requires Phase 0 freeze, physical board access, legal BSP/RTOS/toolchain, logic analyzer and operator protocol.
* **Allowed / forbidden paths:** add `docs/rtd_pilot/feasibility/`, `hardware/rtd_pilot/` (schematics, pin map, scripts only after authorization); allow a disposable external build workspace. Forbid parser/metric/schema/test changes, historical datasets, simulated data being labelled hardware, and any experiment claim.
* **Development/docs/schema/code:** audit uC/OS versus FreeRTOS candidate; document toolchain, pins, timer, UART, debug, task/mutex/IRQ controls, collector pressure and license. Implement only board smoke firmware when separately approved, plus GPIO marker wiring and independent observer logging; write platform selection and clock-alignment protocol. No diagnoser.
* **Tests / data:** repeat GPIO epoch capture; timer/serial cross-check; task scheduling, mutex and controllable IRQ smoke checks; measure drift/alignment error and recorder perturbation. Retain all failures but do not call this a paper experiment.
* **Completion / blockers / stop:** pass only when one platform meets every mandatory criterion, observer sees all required markers, clock error bound is measured, and a capture can be declared invalid deterministically. Block on unavailable hardware/toolchain/license. Stop if only simulator/host collector is feasible or observer cannot be isolated from trace analysis.
* **Freeze / paper / assets:** freeze pin map, platform decision, observer protocol and smoke evidence separately; rollback to Phase 0 if selection fails. Supports Sections 4--6; produces architecture and independent-truth flow diagrams, platform table and feasibility log.

### Phase 2 -- Injection Ledger, CCM and Capture Integrity Record

* **Goal / preconditions:** create versioned, validated contracts before new diagnostic behavior. Requires chosen platform, stable Case identifiers and Phase 1 observer protocol.
* **Allowed / forbidden paths:** add `spec/schema/rtd_injection_ledger.schema.json`, `spec/schema/rtd_capture_capability_manifest.schema.json`, `spec/schema/rtd_capture_integrity_record.schema.json` 及其 `spec/assets/schema/` 镜像、validator、测试和文档，以及 `docs/rtd_pilot/contracts/`、`tool/rtd_validate_*`。Forbid modification of old P6/P7 schemas/fixtures and use of package/proof schemas as truth schemas.
* **Development/docs/schema/code:** implement ledger/CCM/CIR validators, version migration policy (`major` incompatible, `minor` additive), immutable append/seal workflow, collector-config snapshot adapter, Capture join validation and data retention policy. CCM 只验证配置能力；CIR 验证 sequence gaps、LOSS/OVERFLOW、实际 watermark、truncation/corruption、alignment/mapping/observer degradation 与原因码。Add schemas for Case definition, observer record, raw artifact inventory and Capture validity; define required/optional fields and stable enum values.
* **Tests / data:** unit validation for required fields, state transitions, hash mismatch, duplicate IDs, changed config, CCM/CIR 字段错置、non-manifested and invalid captures; integration test creates a synthetic contract object only to test validation, not truth. No hardware fault campaign yet.
* **Completion / blockers / stop:** complete when each capture can be uniquely linked to `Ledger + CCM + CIR + Observer Record + Raw Trace Hash`; filter/sampling/integrity semantics validate fail-closed. Block on inability to extract collector config, integrity inputs or immutable storage. Stop if ledger can be rewritten without an auditable revision trail.
* **Freeze / paper / assets:** version/commit schemas, validators and contract examples; preserve migration test vectors. Supports Sections 3--5; produces CCM/CIR/ledger field tables, data-flow figure and validator README.

### Phase 3 -- Full Evidence Lineage Redesign

* **Goal / preconditions:** make every diagnostic derivation traceable and boundary-aware. Requires Phase 2 schemas and frozen lineage semantics.
* **Allowed / forbidden paths:** `parser/models.py`, `spec/models.py`, `parser/rebuild.py`, `parser/codec.py`, `parser/align.py`, `parser/pipeline.py`, isolated new `parser/rtd_lineage.py`, relevant serializers/schema/export adapters and new tests. Forbid historical fixtures/results mutation, completion/automata code and Agent truth integration.
* **Development/docs/schema/code:** type ResourceEdge/wait/hold; add mandatory lineage fields and a versioned lineage registry to RebuildBundle; bind both CCM and CIR through stable capture-scoped references. Every derived object must have valid `capture_capability_ref` and `capture_integrity_ref`: the former points to CCM configuration observability, the latter to CIR actual degradation. Populate source/open/close/boundary/window IDs and derivation rule/version for TaskStateSeg, ExecSlice, resource relations, IrqSpan and ready-not-running. `intersecting_untrusted_window_ids` must resolve to CIR sequence-gap, LOSS, OVERFLOW, truncation, alignment or mapping integrity records. Lineage may only reference CIR and compute `lineage_status` using frozen rules; it must never modify CIR facts. Preserve existing API through explicit compatibility readers only; migrate call sites so bool trust is non-authoritative.
* **Tests / data:** all fifteen mandatory loss/boundary tests listed in Evidence Lineage Redesign; CCM/CIR missing, version mismatch, wrong-Capture reference and one object crossing multiple CIR windows; round-trip serialization; old reader compatibility; source graph acyclic/backtrace validation; full Python regression after focused parser/metric tests. Use synthetic test inputs only for code semantics, never for empirical labels.
* **Completion / blockers / stop:** every derived object has valid, capture-consistent CCM and CIR references; every untrusted-window reference resolves to a CIR integrity record; no object with a missing or mismatched integrity reference is classified as `COMPLETE`; no unlabelled end-of-trace closure is complete; all legacy regression passes. Block on an object type that cannot retain source IDs without redesign. Stop/replan if full lineage requires ambiguous fabricated event IDs or breaks deterministic rebuild parity.
* **Freeze / paper / assets:** dedicated code/schema/docs commit, lineage migration note and regression logs, including capture-consistency evidence; rollback to Phase 2 contract if validation fails. Supports Sections 3--5; produces lineage field table, example lineage graph and loss-induced false-interval figure.

### Phase 4 -- Real Hardware Capture Chain

* **Goal / preconditions:** 建立一个与 Ledger、CCM 和 CIR 唯一绑定、具备独立 Observer 对齐、能够保留自然 LOSS/OVERFLOW 及失败 Capture 的真实硬件采集链。Requires Phases 1--3.
* **Allowed / forbidden paths:** selected `collector/hook/<rtos>/`, target backend/BSP directory, `collector/include/trace_api.h` only when API gap is approved, collector config/export tools, `hardware/rtd_pilot/`, `tool/rtd_capture_*`, schemas/tests/docs. Forbid changing P6/P7 data and diagnosing captures before truth Pilot gate.
* **Development/docs/schema/code:** configure buffer and flush, implement export and per-Capture configuration snapshot, bind filter/sampling configuration to CCM, write actual sequence/LOSS/OVERFLOW/watermark/truncation and degradation outcomes only to CIR, write firmware/config/ELF hashes to ledger, epoch-align observer and trace, preserve failed captures, lock raw trace files read-only, define directory/name form `data/raw/<board>/<session>/<case>/<capture>/` with immutable inventory/hash. Observer manifestation truth must remain in its independent record and must not be written to CIR.
* **Tests / data:** minimum smoke test: healthy scheduling; mutex lock/unlock; controllable IRQ; GPIO epoch; logic-analyzer alignment; loss-free clean capture; controlled buffer pressure; one natural overflow capture. Integration validates raw trace -> codec -> uniquely joined Ledger/CCM/CIR/Observer Record/raw hash without a diagnoser; a Capture with only CCM or only CIR is invalid for Phase 5.
* **Completion / blockers / stop:** complete when clean and natural-overflow captures survive raw-to-parser audit, counters/CIR agree, CCM contains only configuration facts, CIR contains actual degradation only, every valid Capture has unique Ledger/CCM/CIR/Observer Record/raw hash binding, and invalid captures remain accessible. Block on unrecoverable transport loss, unobservable sequence, unstable timing or recorder perturbation. Stop if overflow cannot be attributed or capture changes the fault scenario beyond Phase 0 tolerance.
* **Freeze / paper / assets:** freeze firmware/config/collector versions, raw-data inventory, unique binding inventory and acquisition SOP; no raw file overwrite. Supports Sections 4--6; produces collector/observer architecture figure, capability examples, throughput/buffer data and smoke/failure log.

### Phase 5 -- Independent Truth Pilot

* **Goal / preconditions:** establish whether a real, independently labelled data route exists before building a diagnosis method. Requires Phase 4 chain, Phase 2 ledger and Phase 3 lineage validation.
* **Allowed / forbidden paths:** new `data/rtd_pilot/` (subject to license/data policy), `docs/rtd_pilot/cases/`, ledger/observer/raw manifests, capture tools and validation tests. Forbid classifier/verifier optimization, holdout tuning, P6/P7 relabeling and Agent calls.
* **Development/docs/schema/code:** implement Case templates for F1/F2/F3, controls, injection definitions and Observer predicates; execute only after a per-Case preapproval record. Persist every attempted Capture, including non-manifested and invalid. Preserve baseline, enable, precursor, manifestation/recovery and end intervals plus alignment bound as a CaptureEpisode; `precursor_candidate_interval` remains analysis-only. Same-Case CaptureEpisodes form only that Case's CaseEpisodeBundle. Extend data schemas only for Case/Observer/ledger validation, not diagnosis output.
* **Tests / data:** at least 12 independent Cases (4/family) x 3 captures = 36 raw hardware captures plus controls. Audit observer-to-ledger independence, configuration diversity, source lineage generation, complete CCM/CIR binding, CaptureEpisode/CaseEpisodeBundle identity and split containment, clean capture sufficiency and publication license. Test no labels are read from parser/diagnoser outputs.
* **Completion / blockers / stop:** all Pilot Gate conditions must pass: stable trace, independent observed manifestation, reproducible ledger, complete, valid and uniquely bound CCM and CIR for every retained Capture, generated source lineage, sufficiently complete clean capture, distinguishable controls, real parameter variation, publication rights and retained failures. Each Capture has exactly one valid CCM and one valid CIR; CCM contains configuration capability only, while CIR records actual sequence, LOSS, OVERFLOW, watermark, truncation, corruption, alignment, mapping and observer-channel integrity. Ledger、CCM、CIR、Observer Record、Raw Trace Hash 与 `capture_id` must join uniquely. A missing, duplicate, version-incompatible or identity-inconsistent CCM/CIR makes the Capture invalid and excludes it from Phase 6; Phase 6 may not use a Capture with CCM but no complete CIR. Block/stop on unreliable observer, unstable fault manifestation, unalignable trace/observer, instrumentation effects, license failure or synthetic-only data. No Phase 6 if any gate fails.
* **Freeze / paper / assets:** seal Case IDs/splits reserved for later, Pilot ledger, raw/observer inventory, CaptureEpisodes/CaseEpisodeBundles and closeout; never overwrite raw captures. Supports Sections 5--6; produces Case/control table, Case-CaptureEpisode hierarchy figure, Pilot audit table and failure inventory.

### Phase 6 -- Deterministic Missing-Aware Diagnoser

* **Goal / preconditions:** implement the frozen four-state verifier only after independent-truth Pilot passes. Requires lineage-complete rebuilds and CCM/CIR-bound captures.
* **Allowed / forbidden paths:** new `parser/rtd_diagnosis.py`, `metric/rtd_diagnosis.py`, diagnostic output schemas, report/CLI adapters, deterministic rule files, tests and docs. Forbid `openai_*`, advisor/gate truth use, modification of injection labels, and possible-world/DES/automata mechanisms.
* **Development/docs/schema/code:** encode F1/F2/F3 necessary/sufficient facts, relevance calculation, `OOD -> UNKNOWN -> REFUTED/SUPPORTED` admission flow, counter-witness handling, threshold/rule versioning, `RTDDiagnosisInputBundle`/`RTDDiagnosticReport` and stable `EvidenceExportSeed`. Witness、counter-witness、CCM、CIR 和 lineage 必须可成为闭包引用。The current alert/diag remains a baseline adapter, not silently redefined.
* **Tests / data:** unit-test each verdict and precedence; test absence evidence, unrelated gap, relevant gap, trusted positive witness, long relation crossing a gap, disabled filter, sampling, truncation, unsupported mapping, conflicting evidence and natural-overflow/mask separation. Integration runs fixed Pilot development Captures without changing labels.
* **Completion / blockers / stop:** output is deterministic, fully auditable, every conclusion names source/derived/window/CCM/CIR IDs, emits a stable seed, and no `REFUTED` uses inadmissible absence. Block on an undefined fact relevance rule. Stop/rework if a missed relevant gap yields false confirmation that cannot be corrected without universal abstention.
* **Freeze / paper / assets:** freeze rules/thresholds before validation/holdout, commit code plus rule digest and deterministic reports. Supports Sections 3--5; produces system-state diagram, verdict examples and diagnostic report/schema/seed table.

### Phase 7 -- Baselines, Ablations and Fair Evaluation Framework

* **Goal / preconditions:** produce a fair comparison harness before formal data analysis. Requires Phase 6 deterministic output and Phase 5 Case hierarchy.
* **Allowed / forbidden paths:** `parser/rtd_baselines.py`, `tool/rtd_evaluate.py`, `spec/schema/rtd_*baseline*.json`, test fixtures isolated under `tests/python/fixtures/rtd_pilot/`, docs and analysis skeleton. Forbid access to holdout labels for tuning and extra target-method truth.
* **Development/docs/schema/code:** implement all 12 baselines and forced ablations in the Baselines section; freeze common input adapter, parameters, error/timeout policy, mask protocol, Case/group split, scorer and valid-Case policy. Add optional FO/MTL/TeSSLa only after feasibility/license review and document inability faithfully to reproduce.
* **Tests / data:** adapter equivalence on common raw input; schema validation; baseline failure retention; leakage tests enforcing template grouping; deterministic mask replay. Add the integration contract `diagnostic report -> EvidenceExportSeed -> sidecar/bounded closure -> evidence package -> external reopen`; verify source/witness/CCM/CIR/lineage references remain identical. exact/bounded/degraded closure status must not change diagnostic verdict; proof/package validity must not override `UNKNOWN`/`OOD` or create truth. After every evaluated method has produced and frozen its outputs, the sealed evaluation harness joins method outputs to independent truth records through opaque `case_id` and `capture_id` references. Diagnostic reports, method inputs and baseline adapters must not expose truth labels, Observer manifestation verdicts, `fault_manifested`, `manifestation_interval` or holdout outcomes. The join is performed only inside the evaluation layer for metric computation and audit. `case_id` and `capture_id` may connect identity but must not encode fault class or manifestation state; truth records are not part of `RTDDiagnosisInputBundle`, baseline input or `CAPEFeatureBundle`. The scorer reads only frozen outputs and sealed truth, must not rerun, alter or write back method outputs, and applies the same isolation to development, validation and holdout; holdout truth is accessible only to the final frozen evaluator.
* **Completion / blockers / stop:** pass when every primary baseline has a documented runnable or documented unavailable state, no method has privileged truth, and validation selection is sealed. Block on irreproducible baseline. Stop/downgrade if fair input parity cannot be achieved.
* **Freeze / paper / assets:** commit baseline registry, adapters, parameter/split/mask manifests and first-access log; rollback before holdout access if leakage occurs. Supports Sections 6--7; produces baseline table, ablation matrix, fairness contract and evaluation flow figure.

### Phase 8 -- Formal Dataset, Evaluation and Statistical Analysis

* **Goal / preconditions:** collect/complete the narrow empirical dataset and run a frozen analysis. Requires Phase 7 freeze and no holdout tuning.
* **Allowed / forbidden paths:** `data/rtd_dataset/`, `analysis/rtd_pilot/`, `tool/rtd_analysis_*`, result schemas, immutable manifests, figures/tables sources and docs. Forbid rule/threshold/baseline parameter changes after first holdout access, data deletion and synthetic substitution.
* **Development/docs/schema/code:** target at least 12--18 independent Cases x 3 captures; preferred 18 Cases (6/family) and 54 raw captures plus controls. Apply predeclared conditions: clean, deletion variants, truncation, filter, sampling and separate natural overflow. Retain the full baseline/enable/precursor/manifestation-or-recovery/end stage record and alignment bound as one CaptureEpisode for each CAPE-eligible Capture; aggregate only same-Case CaptureEpisodes into a CaseEpisodeBundle. Generate Case-clustered statistics and per-family failure analysis.
* **Tests / data:** integrity hashes, `Ledger + CCM + CIR + Observer + raw hash + lineage` joins, split leakage audit, mask replay, denominator audit, blinded holdout scorer invocation and result-table regeneration. Hardware repeat checks verify recorder overhead and observer alignment remain within Phase 1 bounds.
* **Completion / blockers / stop:** complete when valid data meet independent Case minimum, all controls/failures are retained, primary comparison and uncertainty are generated from frozen pipeline, and negative results remain reported. Block on missing cases/rights/leakage. Stop/downgrade if full method is not better than simple baselines, improves UNKNOWN only by raising false confirmation, or relies on ignoring relevant gaps.
* **Freeze / paper / assets:** seal raw/normalized data version, analysis environment, result digest and closeout; a correction creates a new dataset version, never modifies frozen source. Supports Sections 6--8; produces main result, loss-condition, ablation, overhead and failure-case tables/figures.

### Phase 9 -- CAPE-RT Integration Bridge and Optional LLM Planner

#### Phase 9A -- CAPE-RT Integration Bridge

* **Goal / preconditions:** establish total-system interfaces after Phase 8 deterministic results are frozen. This is required for system integration, but not an RTD diagnosis-paper core claim; it does not train CAPE-RT.
* **Allowed / forbidden paths:** future isolated CAPE bridge schemas/docs/export adapters and integration tests after separate authorization. Forbid modification of truth-path code/data, Ledger, Observer, CCM, CIR, lineage, diagnostic rules and verdicts.
* **Development/docs/schema/code:** define CaptureEpisode and CaseEpisodeBundle schemas、`CAPEFeatureBundle`、`ModelContext`、Feature Schema/version、`ExperimentActionRegistry`、`HardwareSafetyEnvelope`、`ExperimentBudget`、`ApprovalPolicy`、`StopRule`、`GateDecision`、Warning `EvidenceExportSeed`、Experiment Plan `EvidenceExportSeed`、Parquet/CSV feature export，并将 Case/Capture/CaptureEpisode/CaseEpisodeBundle/Model/Evidence Package identity 绑定。仅消费冻结的 `RTDDiagnosticReport`；特征缺失必须显式表示，不得静默填零。
* **Tests / data:** schema/version and identity-join checks; verify a CaptureEpisode has one Capture, a CaseEpisodeBundle contains only same-Case CaptureEpisodes and no CaseEpisodeBundle crosses a split or Scenario Template; confirm healthy/near-miss/fault/observability-defect/invalid/wrong-entity categories follow Ledger/Observer rules; verify no bridge consumer changes a truth-path object. No CAPE model accuracy or boundary-learning claim.
* **Completion / blockers / stop:** complete when frozen diagnostic reports generate consistent feature bundles and CaptureEpisode/CaseEpisodeBundle candidates, and every warning/plan can seed evidence export. Stop CAPE extension if qualified CaptureEpisodes/CaseEpisodeBundles are unavailable or model applicability is absent; do not alter RTD results.
* **Freeze / paper / assets:** preserve interface/version documents and synthetic contract-only tests separately. The full CAPE-RT model training, boundary learning and active-experiment algorithm must be planned in `cape_rt_implementation_plan.md`.

#### Phase 9B -- Optional LLM Planner

* **Goal / preconditions:** evaluate an optional planner only after Phase 9A safety contracts and frozen local model outputs exist. Core system and RTD paper remain runnable without it.
* **Allowed behavior:** retrieve CaptureEpisodes/CaseEpisodeBundles, organize candidate experiments, generate structured plans, explain local-model outputs and invoke read-only query tools. A commercial API is permitted only with sanitized compact JSON and separate privacy/security authorization.
* **Forbidden behavior:** directly read full raw binary Trace; generate risk numbers or fault truth; modify Ledger, Observer, CCM, CIR or lineage; set a diagnostic verdict; bypass Gate; directly execute fault injection; access holdout truth.
* **Development/docs/schema/code:** numeric risk、precursor stage和boundary必须由本地轻量模型或确定性算法负责。LLM is a proposal/explanation layer only; compare rule-only/Bayesian Optimization/local-model plans against LLM-assisted plans under the same deterministic Gate.
* **Tests / data:** prompt provenance, sanitization, injection resistance, read-only enforcement, Gate/approval enforcement, zero truth-path mutation, cost/latency and blinded utility assessment. No hidden network/LLM calls in mandatory reproduction.
* **Completion / blockers / stop:** if LLM is not better than rule-only or Bayesian Optimization, remove the LLM Planner only; do not automatically remove CaptureEpisode/CaseEpisodeBundle, local boundary model or deterministic active experimentation. If it uses privileged information or bypasses Gate, remove it.

### Phase 10 -- Paper Writing and Reproducibility Package

* **Goal / preconditions:** turn frozen evidence into a reproducible Chinese-core submission package. Requires Phase 8 results; Phase 9 is optional and removable.
* **Allowed / forbidden paths:** `paper/`, `docs/rtd_pilot/reproducibility/`, `analysis/`, release manifests/readmes/license files and CI/scripts. Forbid post-hoc result-changing code/data edits, claim expansion and P6/P7 historical edits.
* **Development/docs/schema/code:** write Sections 1 Introduction, 2 Background/related work, 3 problem definition, 4 system design, 5 implementation, 6 experimental design, 7 results, 8 discussion and 9 conclusion. Package source, build instructions, firmware/config, Case definitions, ledger, CCM, CIR, raw/normalized trace, Observer records, masks, rules and diagnosis version, lineage digest, baselines, analyses, closure status/budget, tables, figures, licenses, failure logs and artifact README. Future CAPE warning/plan may be a new `EvidenceExportSeed` target, never a replacement for RTD evidence.
* **Tests / data:** fresh-machine build/replay drill; raw hash and license audit; scripts regenerate every table/figure; artifact reviewer follows instructions without access to unpublished labels beyond declared package; verify core system works with Agent absent. Proof digest proves artifact integrity only, not diagnostic correctness or fault truth.
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

Planned figures: (1) four-plane overall architecture; (2) CCM/CIR/lineage/verdict relation; (3) independent-truth data flow; (4) Scenario Template--Case--Capture--CaptureEpisode and CaseEpisodeBundle hierarchy; (5) concrete lineage example; (6) LOSS causing an invalid derived interval; (7) four-state diagnostic state machine; (8) RTD-to-closure and RTD-to-CAPE interface flow; (9) experiment process/split/Gate flow.

Planned tables: (1) three-technology input/output handoff; (2) fault families and Case parameters; (3) CCM versus CIR fields; (4) derived evidence lineage fields; (5) canonical inter-module contracts; (6) baselines; (7) RTD/CAPE data scale; (8) primary diagnostic result; (9) results by loss condition; (10) ablations; (11) runtime/memory/collector overhead; (12) failure cases; (13) Claim Boundary; (14) functionality comparison with related methods.

Each figure/table has a generating script, input manifests, output hash and a statement of whether it uses development, validation or holdout data. No table may report P6/P7 synthetic/reference evidence as C3 real-data evidence.

## Reproducibility Package

The final package must include source code; build instructions; firmware/config; board/BSP and toolchain versions; Case definitions; ledger; CCM; CIR; raw and normalized traces; Observer records; CaptureEpisodes and CaseEpisodeBundles; lineage summary; diagnosis/rule/threshold versions; masks; deterministic rules; baseline adapters; closure status/budget; analysis scripts; result tables; figures; licenses/notices; failure logs; environment lock files; artifact README; and a limitations/data-access statement. It must state whether raw hardware traces are public, restricted or reproducibly requestable. Hashes, proof digest and package replay establish file identity/reproduction only; the independent ledger/observer establishes the truth boundary.

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
| Actual integrity not bound | CIR missing or CCM carries runtime facts | require unique Ledger+CCM+CIR+Observer+raw hash join | invalid Capture; no absence claim. |
| Too few independent Cases | repeated variants share template | recruit distinct configurations/templates | <4/family: permanent stop. |
| Leakage | template or hash crosses split | grouped split and access logs | leakage after holdout: new holdout/downgrade. |
| License/data release | board/BSP/raw trace rights unclear | clear before collection, retain registry | unresolved publication rights: stop/downgrade. |
| Baseline unfairness | unequal inputs/parameters | frozen adapter contract and audits | parity impossible: no superiority claim. |
| No gain over global overlap | main metrics equal/worse | report negative, analyze scope | full method not better: downgrade. |
| Fewer UNKNOWN but more false confirmation | primary safety metric worsens | enforce false-confirmation guard | cannot satisfy: reject method claim. |
| Agent has no value | no-Agent parity | omit Agent | never rescue paper with Agent. |
| CAPE inputs/model inapplicable | insufficient qualified CaptureEpisodes/CaseEpisodeBundles or context mismatch | stop CAPE extension; retain frozen RTD | never rewrite RTD or create proxy truth. |
| Active plan unsafe | envelope/budget/Gate/approval failure | retain rejected plan and stop execution | no production execution; no Gate bypass. |
| Contribution too weak | C1/C2 indistinguishable from baseline | narrow to system/empirical report | no clear empirical benefit: engineering report. |

## Stop Conditions

RTD 诊断方法必须在无 Agent 条件下成立；CAPE 本地风险/边界模型与 LLM Planner 是独立下游贡献。若 LLM 不优于 rule-only 或 Bayesian Optimization，删除 LLM Planner，但不自动删除 CaptureEpisode/CaseEpisodeBundle、边界模型或确定性主动实验；若 CAPE 没有合格 CaptureEpisode/CaseEpisodeBundle 或模型不具备适用性，停止 CAPE 扩展，绝不回写或修改 RTD 结果。禁止用 Agent 包装掩盖 RTD 相对简单 baseline 无收益。

The paper route permanently stops, rather than being repaired with wording, when any occurs: (1) no real hardware; (2) no independent observer; (3) no immutable ledger; (4) complete lineage cannot be implemented; (5) only P6/P7 are usable; (6) fewer than four independent Cases per family; (7) performance arises by ignoring relevant gaps; (8) missed-relevant-gap false confirmation cannot be repaired; (9) full method is not better than simple baselines under frozen protocol; (10) license/data release cannot be resolved; or (11) the contribution still depends on Agent packaging. A stopped RTD route may yield an explicitly labelled engineering/provenance report, never a retroactive diagnosis-method claim.

## Commit and Freeze Strategy

Before each authorized Phase, record `START_HEAD`, branch, clean/dirty worktree and allowed-path list. During the Phase, maintain immutable input inventory and a change log. At closeout: run focused tests then full regression appropriate to changed surfaces; run hardware/observer tests where applicable; audit schema/data/license/lineage artifacts; obtain independent review; require `blocking=0` and `major=0`; create one focused commit and a closeout document; record `FINAL_HEAD`; confirm clean worktree. Rollback is to the prior phase freeze or a new corrective commit, never a destructive reset, historical-doc rewrite, raw-data overwrite or silent result deletion.

## Paper-Readiness Gates

| Gate | Required evidence | Failure consequence |
| --- | --- | --- |
| G0 boundary | Phase 0 approved narrow claim、F1/F2/F3 control/evaluation contracts、metrics/splits/stop conditions、conceptual Ledger/CCM/CIR/lineage contracts and Phase 1 test protocol | no hardware, code, schema, validator or data work. |
| G1 feasibility | real board + independent observer + legal toolchain | no Pilot. |
| G2 contracts | immutable ledger + CCM/CIR validators and unique Capture binding | no hardware dataset. |
| G3 lineage | full raw-to-derived lineage and loss tests | no diagnosis implementation. |
| G4 Pilot | 12 Cases/36 captures minimum, controls, retained failures | no method/evaluation claim. |
| G5 safety | deterministic four-state verifier, no false admission tests | no baseline comparison. |
| G6 fairness | frozen baselines/splits/masks/scorer | no holdout access. |
| G7 empirical | Case-level primary results, overhead, failure analysis | downgrade to engineering report. |
| G8 artifact | independently rerunnable data/code/license package | no submission. |
| G9 integration (非论文核心) | `RTDDiagnosticReport` 可生成 `EvidenceExportSeed` 与 `CAPEFeatureBundle`；CaptureEpisode/CaseEpisodeBundle identity、CCM/CIR/lineage、模型上下文与 evidence package refs 一致；无 Agent 条件下核心诊断与证据导出可运行。 | 不改变已完成 RTD 实验结果，但三项技术总系统尚未集成完成。 |

## Final Recommendation

The correct next authorization is a bounded Phase 0 documentation-and-contract request. Phase 0 only freezes the Paper Claim Boundary, fault/control and evaluation contracts, the Phase 1 hardware/RTOS/Observer feasibility test protocol, and the conceptual Ledger/CCM/CIR/Lineage contracts. Phase 0 must not connect hardware, flash firmware, execute Observer capture, collect data, modify implementation schemas, or run experiments. Actual hardware, RTOS and independent Observer feasibility verification belongs exclusively to Phase 1. Phase 2 alone implements the formal Ledger, CCM and CIR schemas and validators; Phase 1 or Phase 2 work must not be brought forward into Phase 0.

## Future Extension

queue backlog、完整 priority inheritance、deadline release/completion、deadlock、stack/heap root cause、HardFault root cause 和 multicore causality只可在新的 RTOS Adapter、事件字典、Payload 与故障合同提供完整语义后另行规划。当前适配或语义不完整时，诊断器必须输出 `OOD`；不得将这些事项纳入 F1/F2/F3 首期实现或实验。

## Revision Summary Against the Three-Technology System

1. 明确本文是技术二的完整实施计划，而非毕业设计总计划；技术一仅接入 `EvidenceExportSeed`，技术三仅获得冻结数据、接口和安全交接。
2. 将 CCM 收紧为配置能力，并新增 CIR 作为实际采集退化的正式合同；规定唯一 `Ledger + CCM + CIR + Observer + Raw Trace Hash` 绑定。
3. 增加 RTD 输入/报告、证据导出、CAPE 特征与 CaptureEpisode/CaseEpisodeBundle 候选五类规范接口，以及 RTD 到闭包、RTD 到 CAPE 的单向交接。
4. 重构 Phase 9 为 CAPE-RT Integration Bridge 与 Optional LLM Planner，保留无 LLM 核心运行、确定性 Gate 与人工审批边界；完整 CAPE-RT 必须另立 `cape_rt_implementation_plan.md`。
5. 扩展四平面架构、数据格式、CaptureEpisode/CaseEpisodeBundle 分类、主动实验安全合同、RTOS Adapter 边界、数据规模、风险、G9、图表与复现包，同时保持 F1/F2/F3、P6/P7、独立 truth 和 Phase 0--8 的硬边界。
