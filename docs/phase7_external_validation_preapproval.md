# Phase 7 外部证据包重放与真实 RTOS 验证预审批开发计划

状态：Pre-Approval Only

实现状态：Not Started

代码开发授权：Not Approved

自动提交：Disabled

人工可读日期：2026-07-11。本文件的日期不是未来 canonical manifest、
package identity、proof digest 或任何确定性产物的输入。

## 1. 文档目的

本文件只冻结 Phase 7 的候选范围、合同、风险、文件边界、实验口径和
准入条件，供后续独立的 P7.1 合同冻结计划使用。它不是实现授权。

- 本文件完成不表示 Phase 7 已开始实现。
- 本文件完成不表示任何 replay 已通过、已有真实硬件数据或 proof parity。
- 本文件完成不表示论文实验已完成，也不表示可作 proof correctness 声明。
- Phase 6 的历史结果和语义保持原样；Phase 7 只能生成新的独立结果。

## 2. 当前仓库基线

| 项目 | 已核对事实 |
| --- | --- |
| 仓库 | `/media/zzq/新加卷/patent/realization` |
| 分支 | `main` |
| 冻结 HEAD | `d3bcee0e40dbe7da933b067f1a6affb740fef26f` |
| Phase 6 最终提交 | `d3bcee0 phase6 finalize closeout and reproducibility manifest` |
| 启动工作区 | tracked/staged/untracked 均为空；仅有被忽略的 pytest 缓存，已按审计清理 |
| P6.4 canonical SHA-256 | `fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e` |
| Phase 6 回归基线 | 由 `docs/phase6_reproducibility_index.md` 与 P6.7 closeout manifest 记录；本轮未重跑 |

冻结的 P6.4/P6.7 数值为：`cases_total=8`、`replay_pass_count=0`、
`replay_fail_count=0`、`reference_only_count=8`、`not_evaluated_count=0`、
`proof_drift_count=0`、`all_replay_passed=false`。

未解决缺口包括：完整 evidence-package replay 与 proof parity、完整 raw-trace
reconstruction、真实 RTOS/hardware/workload、性能与扩展性测量、外部 baseline、
empirical diagnosis/root-cause/replay correctness，以及真实参与者研究。P6.4 的
`proof_drift_count=0` 只表示没有提供的 deterministic facts 发生差异；它不表示
proof correctness 或 proof parity。

## 3. 阶段名称

Phase 7：External Evidence-Package Replay and Real-RTOS Validation

中文：Phase 7：外部证据包重放与真实 RTOS 验证。

`External` 要求来源、许可证、provenance 和环境可独立审计；
`Evidence-Package` 限定验证对象是带合同的包而不是任意文件；`Replay` 要求
确定性 reopen、验证和明确状态；`Real-RTOS Validation` 仅指在受控协议下获得的
真实系统证据。名称不预设成功，不使用 `Full Replay Success`、`Proof-Correct`
或 `Complete Real-RTOS Generalization`。

## 4. 阶段目标

1. 定义外部 evidence package 合同与 package reopen 行为。
2. 定义 artifact identity、checksum integrity、evidence closure 和
   deterministic replay 的分层验证。
3. 为独立的 Phase 7 replay artifact 定义严格状态机和等价条件。
4. 在批准的数据与限定平台上获取真实或有许可证的外部 RTOS trace。
5. 固化 workload、性能、provenance 和公平 baseline 的实验协议。
6. 保持 Phase 6 truth、proof、export 和 collector 边界不变。

## 5. 非目标

Phase 7 不修改 Phase 6 canonical artifacts、proof hash/digest 语义、collector
热路径或 `desktop/evidence_export.py`。它不重写 evidence export，不让 LLM
或 advisor 进入 trace truth、root-cause truth 或 replay 判定，不让 human feedback
判定 correctness，也不进行 autonomous remediation。

本阶段不承诺 diagnosis accuracy 或 root-cause correctness 提升、通用 RTOS
泛化、anomaly-detection SOTA、真实参与者研究、论文录用率或 proof correctness。
Synthetic 结果不改写为真实 RTOS 实证，simulator 不改写为真实硬件。

## 6. 研究问题

**RQ1。** 在不修改 proof semantics 的条件下，外部 evidence package 能否被
确定性 reopened 和验证？

**RQ2。** 在什么明确条件下，某一 Phase 7 独立 case 可被标为 `replay_pass`？
这不会回写 Phase 6；P6 的 8 条 `reference_only` 历史记录保持不变。

**RQ3。** artifact identity、package completeness、evidence closure 与
semantic replay 能否分层验证且独立报告？

**RQ4。** 在指定板卡、RTOS、workload 和 trace 协议下，采集、包、reopen 与
验证的时间、内存、存储开销是多少？

**RQ5。** 外部 baseline 如何在相同 trace、case、truth boundary 和指标口径下
进行公平比较？

## 7. 核心概念分层

| 层次 | 定义 | 不自动推出 |
| --- | --- | --- |
| Package existence | 包引用或目录存在 | 可读、完整或可信 |
| Package readability | 受支持版本可解析 | required artifact 存在 |
| Package completeness | 合同 required artifact 全部存在 | identity 或 closure |
| Artifact identity | 合同指定的身份字段与来源绑定一致 | 内容语义等价 |
| Checksum integrity | 受支持校验和与声明匹配 | identity 或 replay |
| Evidence closure | required evidence refs 按合同可解析且闭合 | trace reconstruction |
| Trace reconstruction | 合同范围内可从 trace/artifact 重建所需输入 | semantic replay |
| Semantic replay | 确定性管线产生合同定义的语义输出 | proof parity |
| Replay result equivalence | actual 与 contract expected outputs 等价 | proof correctness |
| Proof drift | 可比较的声明 proof facts 的差异计数 | proof parity 或 correctness |
| Proof parity | 经冻结定义的全部可比 proof representation 等价 | proof correctness |
| Proof correctness | proof 对外部事实的正确性 | 不能由任一较低层自动得出 |

前一层成立不代表后一层成立。特别是 artifact identity 不等于 semantic replay，
semantic replay 不等于 proof correctness，`proof_drift_count=0` 不等于 proof parity。

## 8. Evidence Package 合同草案

P7.1 才能冻结版本化合同；本节只列出最小审查字段。未来 package 可能包含
package manifest/version、source trace identity/checksum、trace segment、metadata、
sidecar/index/dictionary/calibration references、diagnosis case reference、expected
deterministic fields、replay contract version、artifact checksums、provenance、
tool/version、unsupported/optional fields、privacy classification 与
redistribution/license classification。

字段分类必须是 `required`、`optional`、`external-reference-only` 或 `forbidden`。
绝对路径不得作为 reproducible identity；identity 应使用稳定的相对 logical ID、
来源标识和冻结的校验字段。`external-reference-only` 不足以满足完整 closure。

包不得包含 API key、secret、PII、raw human feedback、writable proof path、
LLM/advisor correctness 自评，或未经许可的真实数据。未来写入前需验证 schema、
版本、字段白名单、路径规范化、许可和 privacy class；失败必须 fail closed。

## 9. Replay 状态机

主要状态互斥：`not_evaluated`、`reference_only`、`replay_pass`、`replay_fail`。
每条结果还必须有 machine-readable reason code，但 reason code 不是第二个主状态。
`replay_blocked` 和 `unsupported_version` 只可表示尚无受支持的 deterministic
validator/comparison profile 时的 `not_evaluated` 原因；`invalid_package` 不是可
升级的状态，已被 deterministic validation 检出的 malformed schema、缺失 required
artifact 或任何完整性失败必须是 `replay_fail`。

| 状态 | 含义 |
| --- | --- |
| `not_evaluated` | 尚未执行、环境/硬件未准备、数据未批准或测试未运行；无结论。 |
| `reference_only` | 仅 metadata/有限引用可验证，或完整 pipeline/artifact/许可不足。 |
| `replay_fail` | 可执行验证但 identity、checksum、closure、invariant 或 expected output 不一致。 |
| `replay_pass` | 满足下述全部 P7.1 冻结的 deterministic 条件。 |

`replay_pass` 的最低必要条件草案：schema valid；全部 required artifacts
present；checksums valid；source/package identity valid；package version supported；
package 成功 reopened；全部 required deterministic stages 已执行；expected outputs
可用；actual 与合同定义的 expected outputs 等价；source 未变异；无未经授权变异；
结果完全由 deterministic code 生成；不依赖 LLM、advisor 或 human feedback。
P7.1 应冻结 precise comparison domain、serialization、tolerances（若有）和
error taxonomy；没有 comparison profile、canonicalization 或已声明 tolerance 的
case 不得判为 pass。本轮不冻结实现细节。

`replay_fail` 必须可区分 artifact identity mismatch、checksum mismatch、
deterministic output mismatch、closure violation 和 replay invariant violation。
状态优先级是：未能开始受支持 deterministic comparison 时为 `not_evaluated`；一旦
validation 已开始，schema/required-artifact/checksum/identity/closure/invariant/output
mismatch 都是 `replay_fail`，即使后续 semantic stage 无法继续；`reference_only`
仅适用于合同明确是有限/legacy/external-reference-only 检查、全部可用检查成功且
没有完整性或不变量失败的结果。它不能掩盖可检测篡改。P7.1 必须以冻结的
required-artifact manifest 定义 mutation comparison 的 artifact 集合和采样时点，
并把任一声明 artifact 的前后 checksum/identity 漂移编码为 `replay_fail`。
没有 `replay_fail` 不等于 `replay_pass`。

## 10. Phase 6 与 Phase 7 artifact 隔离

Phase 6 fixture、report、manifest、canonical hash、schema、历史 expected output
和 benchmark evidence 均不可修改。Phase 7 必须使用独立目录、独立 replay
report 与独立 canonical hash，且只能只读引用 P6 artifact，不能回写或更新其
状态。特别是 P6.3 的 8 个 `reference_only` 永远不改为 `replay_pass`。

Phase 7 的新 hash 不能改变 P6 hash 语义或成为 P6 proof-digest write path 的输入。
新结果必须标记 source phase、contract version、replay scope 和 provenance，以免
将新阶段结论误投射到 P6 历史结果。

## 11. 数据来源方案

**方案 A：真实硬件 RTOS。** 当前仓库没有经审计可用的板卡、运行 RTOS 版本或
capture；不得宣称具备硬件。`RTOS/uC-OS3-develop` 的存在仅表示源码树存在，
不表示板卡已运行、版本已验证或 trace 已采集。P7.2 前应以实际 inventory 冻结
板卡候选、RTOS/source commit 或 checksum、BSP/board revision、trace-producing
firmware 的完整 compiler/linker/build flags、firmware image hash、trace recorder/
configuration、host capture transport/tool version、workload、clock、IRQ 配置、
capture count、raw-trace checksum、provenance 和可公开性。capture record 必须将
raw-trace checksum 绑定到上述 build 和采集身份；缺少任一项不能称
real-hardware validation。

**方案 B：有许可证的公开 RTOS trace。** 先记录来源 URI/版本、许可证、再分发
权限、文件 checksum、完整性、ground truth 可用性及 replay-contract 适配性。若
不满足合同，只能是 `reference_only` 或 `not_evaluated`，不能补写 pass。

**方案 C：准真实受控 trace。** 可考虑 QEMU、RTOS simulator、host-based RTOS
trace 或受控 external fixture，但所有报告必须标记 `quasi-real / externally generated`，
不得表述为真实硬件验证。当前未批准任何具体数据或平台。

## 12. Workload 设计

候选维度为任务数、优先级层级、preemptive/cooperative policy、IRQ frequency/burst、
mutex contention、queue depth、producer/consumer imbalance、task starvation、long
critical section、trace corruption、stale sidecar 和 missing calibration。

workload 必须分别标记为 functional、stress、failure-injection 或 scalability。
最小可行矩阵先为每个已批准平台选择一个 functional workload、一个 stress
workload 和一个 failure-injection case；只在重复可用后增加 task/IRQ/trace-size
scale。scalability workload 只能在至少一个平台完成可重复 capture、已冻结
provenance、并通过最小功能和 failure-injection protocol 后启用。扩展矩阵可逐项
扩大，不要求一开始覆盖所有组合。每个 case 要固定 input、expected observable、
truth boundary、seed（若适用）、采集协议与停止条件，并标注 truth-label 来源是
fault-injection known label、external annotation 或独立 observation；没有独立
truth 时只能报告 behavior/replay，不能形成 diagnosis correctness 比较。

## 13. 性能指标与协议

报告 trace bytes、event count、package bytes、package shrink ratio、acquisition
overhead、export/reopen/replay/total validation elapsed、peak RSS、disk/temp storage、
read/seek count、checksum/index build cost 和 repeated-run variance。advisor overhead
必须单列，且不得参与 replay correctness。

每个测量记录硬件、OS、RTOS、compiler、配置、clock、存储介质、采样工具和版本；
warm/cold 分开；预先规定重复次数；报告中位数及离散度；保留原始结果；不隐藏
变慢指标，也不只报告最优运行。固定环境与数据集后才能比较结果。

采集开销合同必须在 P7.6 前冻结：同一 source-identical firmware、同一 board 和
同一 workload 采用 trace-disabled/trace-enabled 配对运行；on-target acquisition
与 host-side export/reopen/replay 分别计时，不得合并成单一“采集”数字。合同应预先
给出重复次数、warm-up、计时源与分辨率、采样方法、逐次原始样本保留位置、异常/
失败运行处理规则和不剔除结果的报告方式。缺少这些定义只能报告观察记录，不能称为
可比较的 acquisition-overhead 测量。每个 paired run 还必须定义 trace-off/on 的
counterbalanced 或 alternating 执行顺序，并在每次配对前复位到等价初始状态，以降低
温度、cache 和背景负载造成的时序偏差。

## 14. External baseline 设计原则

比较必须使用相同 trace、case、workload、ground truth 或明确相同 truth boundary、
输入范围、测量环境和指标定义。baseline 不支持某能力时标为“不适用”，而非错误；
解释文本质量不能作为 diagnosis correctness，package 功能差异不能伪装成 accuracy
优势。

P7.7 的能力矩阵必须包含可比性列（trace、case、truth boundary、environment 和
metric 是否相同）与预注册排除规则；不能重建相同 truth boundary 的工具不得参与
横向 correctness/accuracy 排名。

候选类别仅供 P7.7 调研：RTOS trace viewer、Trace Compass、Perfetto、RTOS vendor
trace tool、custom full-trace baseline、no-evidence-package baseline。这里不声称任何
候选工具已验证具备特定能力或已被选定。

## 15. 文件范围白名单草案

路径遵循现有 `parser/`、`tool/`、`tests/python/` 和 `spec/schema/` 结构。本轮的
唯一实际新增文件是本文件。

| 分类 | P7.1 以后候选路径 | 本轮权限 |
| --- | --- | --- |
| `allowed_new` | `docs/phase7_*.md`、`parser/external_validation_contract.py`、`parser/external_evidence_replay.py`、`tool/run_external_evidence_replay.py`、`tests/python/test_external_validation_contract.py`、`tests/python/test_external_evidence_replay.py`、`tests/python/fixtures/external_validation/`、`spec/schema/external_*.schema.json`、`spec/assets/schema/external_*.schema.json` | 只有本文件 |
| `allowed_modify` | P7.1 前默认为空；任何既有文件修改须由独立计划逐路径批准 | 无 |
| `read_only_dependency` | `parser/rtos_diagnosis_replay.py`、`parser/rtos_diagnosis_report.py`、`parser/rtos_diagnosis_advisor_review.py`、`parser/rtos_diagnosis_human_feedback.py`、P6 fixtures/reports/manifests、现有 schema mirrors | 只读 |
| `forbidden` | 见下一节 | 禁止 |

上述是未来白名单草案，不是 P7.1 实施授权，也不授权修改现有 loader、proof 或
export 代码。任何新 schema 都需先有镜像一致性方案，且本轮不得创建 schema。

## 16. 禁止修改范围

禁止修改 `collector/`、`desktop/evidence_export.py`、`parser/evidence_models.py`、
`spec/schema/proof_digest.schema.json` 与 `spec/assets/schema/proof_digest.schema.json`，
以及所有 proof digest/hash 语义。也禁止修改：

- `tests/python/fixtures/rtos_diagnosis/generated/phase6_synthetic_suite.json` 与其 artifacts；
- `tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json`；
- `tests/python/fixtures/rtos_diagnosis/advisor_reviews/phase6_advisor_review.json`；
- `tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_*.json`；
- `tests/python/fixtures/rtos_diagnosis/closeout/phase6_closeout_manifest.json`；
- `docs/phase6_*.md`、historical expected outputs 和 historical benchmark evidence；
- `parser/rtos_diagnosis_*.py`、`tool/*rtos_diagnosis*.py`、
  `tests/python/test_rtos_diagnosis_*.py`；
- `spec/schema/rtos_diagnosis_*.schema.json`、
  `spec/assets/schema/rtos_diagnosis_*.schema.json`；
- `tool/build_phase6_closeout_manifest.py`、`tool/run_rtos_diagnosis_replay.py` 与
  `tool/build_rtos_diagnosis_report.py`。

## 17. 子阶段规划

所有子阶段默认不自动提交；任何提交均须在独立审计后显式批准。

| 阶段 | 输入与任务 | 输出与候选范围 | 测试、风险、fallback、claim boundary、冻结 |
| --- | --- | --- | --- |
| P7.0 | P6 frozen evidence；审计和预审批 | 本文件；`docs/` | 双轮审阅、Git audit；无代码/无 schema/fixture/test；仅计划可 claim；冻结须 blocking/major 为零。 |
| P7.1 | P7.0 计划；冻结 contract、state machine、identity/closure/equivalence 与 schema plan；不采集、不实现完整 replay | 仅经批准的合同文档；原则上 `docs/phase7_*` | 文档 consistency review；风险是语义混淆，fallback 为维持未批准；只可声称合同已冻结。 |
| P7.2 | 经批准 source；获取 trace、provenance、license、platform/workload | 独立 external data metadata，不修改 P6 | provenance/license checks；无硬件则 B/C 并降级；只报告指定来源。 |
| P7.3 | P7.1 contract 与批准 package；读取、version/required artifact 检查、missing/corrupt/stale/partial handling | 独立 reopen result；未来 `parser/external_*` | contract/fixture/CLI tests；已检测 invalid package 为 `replay_fail`，未能启动受支持比较为 `not_evaluated`/`replay_blocked`，仅预声明有限合同且无失败才是 `reference_only`；不判 proof correctness。 |
| P7.4 | P7.3 合格 package；deterministic replay runner 与状态 | 独立 replay report | deterministic/repeated-run tests；LLM/advisor/feedback 禁止；只报告合同范围 replay。 |
| P7.5 | P7.4 results；identity/checksum/closure/equivalence/drift 分层 | 独立 validation report/hash | invariant tests；无法 proof parity 时分别报告层次；proof parity 仅有充分条件才评估。 |
| P7.6 | 批准 workload/环境；时间、内存、存储、规模与波动矩阵 | benchmark evidence | cold/warm/repeat tests；波动时报告中位数与离散度；只 report-only。 |
| P7.7 | 合同相同的 trace/case/truth boundary；公平 baseline | capability/comparison matrix | measurement audit；不公平则只做能力矩阵；不作 accuracy/SOTA claim。 |
| P7.8 | 所有批准结果；focused/full regression、repro CLI、deterministic manifest、claim/risk review | 独立 closeout artifact | P6 no-mutation plus P7 tests；不足则不 close；仅独立冻结提交经批准后允许。 |

每行的“输出与候选范围”均需在对应子阶段开始前细化为输入、任务、输出、文件范围、
测试、风险、fallback、claim boundary 和 freeze condition 的可审计清单。P7.0 不授予
P7.1，也不创建任何未来产物。

## 18. 多 Agent 分工

P7.0 初稿与修订必须接受互不共享第一轮结论的只读审阅：

| 角色 | 审阅重点 |
| --- | --- |
| Agent A | P6 frozen boundary、`reference_only`、canonical hash、proof/export/collector 越界。 |
| Agent B | `replay_pass` 充分性、状态互斥、identity/closure/semantic replay/proof parity 区分、fail-open 漏洞。 |
| Agent C | 数据来源真实性、硬件可行性、workload/metrics、license/provenance、generality 越界。 |
| Agent D | 子阶段 claim boundary、report-only 内容、baseline 公平性、SOTA/accuracy/录用率越界。 |

每位审阅者输出总体结论、blocking/major/minor/accepted risks、建议修改与是否允许进入
P7.1。主审建立问题表，修复全部 blocking/major，再让 A-D 独立复审。任何审阅者
不得写文件；Codex 审阅只替代本轮人工文档审阅，不能替代外部数据、真实硬件、伦理或
论文同行评审。

## 19. 未来测试计划

本轮不创建或运行 P7 tests。未来计划包括：unit（state transition、checksum、identity、
path normalization、version、error taxonomy）；contract（required/optional/forbidden、
schema、mirror consistency、deterministic serialization）；fixture（valid/missing/corrupt/
checksum mismatch/stale/partial/unsupported/reference-only）；CLI（deterministic output、
exit code、output path、no absolute path leak、repeat byte equality）；integration（reopen,
replay, closure, report, manifest）；real-hardware（provenance、repeated capture、workload,
clock/config）；performance（runtime/RSS/storage/variance/cold/warm/scale）；regression
（P6 focused/full Python、P6 artifact mutation=0、P6.4 hash unchanged）。

## 20. 风险与 fallback

| 风险 | fallback | 禁止的错误升级 |
| --- | --- | --- |
| 无真实硬件 | 有许可证公开 trace，或 QEMU/simulator，并标 `external/quasi-real` | 将 simulator 写成真实硬件 |
| trace 不完整 | 保持 `reference_only` 并记录缺失 artifact | 降低 pass 条件 |
| package 无法 reopen | 已检测 invalid package 为 `replay_fail`；无受支持比较为 `not_evaluated`/`replay_blocked`；有限合同且无失败才为 `reference_only` | 强制变为 pass |
| proof parity 不可得 | 分别报告 identity/completeness/closure/semantic replay/drift | 声称 proof correctness |
| 真实数据不能公开 | 公开 schema/script/hash/脱敏 metadata/protocol，原始数据不入库 | 无许可再分发 |
| baseline 不公平 | 只做能力矩阵，不做 accuracy ranking | 将不适用判错 |
| 性能波动 | 固定环境、重复、报告中位数/离散度和原始结果 | 选择最好运行 |
| 范围过大 | 优先 P7.0+P7.1+一个 external trace+package reopen prototype | 同时扩展所有 RTOS/板卡/baseline |

## 21. Claim Boundary

完成相应受控子阶段后，可能支持的内容仅为：指定 package version 的成功 reopen；
指定平台/trace/workload 的 deterministic replay；artifact identity 与 closure 的
检查结果；以及有限性能测量。

仅 report-only：单板卡、单 RTOS workload、单公开数据集、package size、replay
elapsed、RSS 和单场景 drift 数值。

仍不能自动支持：通用 RTOS、diagnosis/root-cause correctness 提升、proof correctness、
anomaly detection SOTA、human usability、live LLM quality、autonomous remediation safety
或论文录用概率。任何这些声明需要额外、明确设计的外部证据。

P7.4/P7.5 的未来报告必须显式分列 `replay result equivalence`、`proof parity` 和
`proof correctness`；未满足对应合同或没有外部 ground truth 时后两项必须写作
“未评估”，不得由较低层验证结果推断。

## 22. P7.0 冻结条件与准入判断

P7.0 只能在以下条件下冻结：本轮 Git 改动仅为本文件；无实现代码、schema、fixture 或
test；无未经核查的硬件事实；不重解释 P6；state machine 和严格 pass 条件清晰；P6
canonical hash/proof semantics 受保护；白名单/禁止范围明确；测试矩阵、风险 fallback
和 claim boundary 完整；A-D 双轮审阅完成；blocking=0 且 major=0；`git diff --check`
通过；不自动提交。

满足上述条件时，本文件只建议允许进入“P7.1 的独立规划与合同冻结”，不得直接进入
完整 replay 开发、数据采集或 P7.2 以后任何工作。否则状态保持 `Pre-Approval Only /
Not Approved`。
