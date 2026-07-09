# Phase 6 启动前规划审计与批准前开发计划 2026-07-09

## 目的

本文档把 2026-07-09 的 Phase 6 启动前规划审计结论固化到仓库，供后续新窗口直接查阅，并作为后续 P6.0/P6.1 的参考输入。本文档只记录规划、边界、测试纪律和批准门槛，不代表 Phase 6 已经进入实现，也不表示 P6.2+ 已批准实现。

本文档中的 P6.2-P6.7 内容均为后续候选计划，不表示 known-root-cause benchmark、evidence package replay equivalence、human feedback schema 已经完成，也不表示 LLM/advisor 可以进入 truth path 或 Phase 6 已达到通用 anomaly detection SOTA。

## 启动审计时基线记录

- 工作分支：`main`
- 审计时 `HEAD`：`c3f51f5 phase5 harden verifier-gated advisor sandbox`
- 审计时工作区状态：`git status --short` 为空，未发现未提交改动
- 审计结论：审计时仓库处于 Phase 5 冻结提交之后的 clean 工作区；这不是当前工作区状态声明

## 启动门槛

- 现在只建议进入 `P6.0` 和 `P6.1`
- `P6.2+` 不应直接启动，必须先完成以下冻结项并经过主 agent 人工审阅：
  - Phase 6 claim boundary
  - Phase 6 case schema / suite schema
  - replay equivalence 的严格定义
  - top-k root cause 的 `report_only` 边界
- Phase 6 应优先新增独立 schema / runner / report，不直接改写现有 runtime optimization benchmark 合约
- 优先使用静态、小型、可重放 fixture；只有静态 fixture 难以维护时，才补最小生成脚本

## 当前可复用基础

- evidence closure：`parser/evidence_closure.py`
- sidecar / index：`parser/evidence_sidecar.py`、`parser/evidence_sidecar_index.py`
- proof digest / evidence export：`desktop/evidence_export.py`、`parser/evidence_models.py`
- evidence package replay / completeness：`desktop/repro_evidence.py`、`desktop/clipped_completeness.py`
- advisor / retrieval / gate：`parser/runtime_advisor.py`、`parser/runtime_optimization_gate.py`、`parser/advisor_retrieval.py`
- runtime cost / telemetry：`parser/runtime_cost_graph.py`、`parser/telemetry.py`
- runtime benchmark 现状：`parser/benchmark_matrix.py`、`spec/schema/benchmark_scenario.schema.json`
- schema 基础设施：`spec/schema_loader.py`、`spec/schema_validator.py`
- 现有相关测试：`tests/python/test_runtime_optimization_advisor.py`、`tests/python/test_runtime_optimization_agents.py`、`tests/python/test_repro_evidence.py`、`tests/python/test_schema_validator.py`、`tests/python/test_pipeline.py`

## 4. Phase 6 批准前开发计划

### P6.0：Phase 6 baseline audit and scope lock

**目标**

确认 Phase 5 之后工作区 clean；冻结 Phase 6 的 claim boundary、truth-path boundary、测试纪律和风险登记口径。

**涉及文件**

`docs/`、`docs/paper_baseline_20260706/`、只读审阅 `parser/evidence_models.py`、`desktop/evidence_export.py`、`parser/runtime_advisor.py`

**预计新增文件**

- `docs/phase6_scope_lock_YYYYMMDD.md`
- `docs/phase6_implementation_checklist_YYYYMMDD.md`
- `docs/phase6_risk_register_YYYYMMDD.md`

**预计修改文件**

- 无硬性要求；优先只新增文档

**不允许修改的文件**

- `parser/evidence_models.py`
- `spec/schema/proof_digest.schema.json`
- `spec/assets/schema/proof_digest.schema.json`
- `collector/`
- Phase 5 sandbox / adversarial closeout 归档内容

**具体实现步骤**

1. 记录当前分支、HEAD、工作区 clean 状态。
2. 冻结三类 claim：`claimable`、`report_only`、`not_claimable`。
3. 写明 Phase 6 红线：advisor 不能进入 trace truth path，不能生成 parse / align / rebuild / sidecar / index / proof facts。
4. 写明 `top-k root cause` 仅 `report_only`。
5. 写明 `human helpfulness` 只有数据后才能宣称。

**新增测试**

- 无代码测试
- 文档审阅检查项：红线、claim class、批准门槛是否完整

**targeted regression 命令**

```bash
git -C /media/zzq/新加卷/patent/realization status --short
git -C /media/zzq/新加卷/patent/realization log --oneline -1
```

**验收标准**

- Phase 6 作用域、红线、claim boundary、测试纪律写入文档
- 明确写出“只建议先启动 P6.0 / P6.1”
- 没有任何代码或 schema 语义改动

**风险**

- 边界写得过宽，后续实现时被误读为“可直接进入 P6.2+”
- 把 `top-k root cause` 写成强 claim

**fallback / rollback 策略**

- 如果范围描述仍含糊，只保留 scope lock 文档，不进入任何代码开发
- 如发现边界表述与 Phase 5 冻结冲突，先修正文档，不继续下阶段

**是否需要主 agent 人工审阅后才能进入下一步**

是，必须。

### P6.1：RTOS diagnosis case schema / metadata schema

**目标**

设计最小 known-root-cause case schema 和 suite schema；schema 只描述 benchmark metadata，不改变 proof digest 语义。

**涉及文件**

`spec/schema_loader.py`、`spec/schema_validator.py`、`spec/schema/`；`spec/assets/schema/` 仅在仓库实际需要镜像接入时考虑，不作为默认要求

**预计新增文件**

- `spec/schema/rtos_diagnosis_case.schema.json`
- `spec/schema/rtos_diagnosis_suite.schema.json`
- 可选：`spec/assets/schema/rtos_diagnosis_case.schema.json`，仅在仓库实际需要镜像接入时新增
- 可选：`spec/assets/schema/rtos_diagnosis_suite.schema.json`，仅在仓库实际需要镜像接入时新增

**预计修改文件**

- `spec/schema_loader.py`
- `tests/python/test_schema_validator.py`

**不允许修改的文件**

- `parser/evidence_models.py`
- `spec/schema/proof_digest.schema.json`
- `spec/assets/schema/proof_digest.schema.json`
- `spec/schema/benchmark_scenario.schema.json`
- `spec/schema/benchmark_report.schema.json`

**具体实现步骤**

1. 定义 case 级字段：`case_id`、`root_cause`、`affected_entity`、`baseline_trace_ref`、`candidate_trace_ref`、`expected_diagnosis`、`expected_evidence_refs`。
2. 定义 suite 级字段：`suite_id`、`cases`、`claim_boundary`、`replay_policy`、`advisor_modes`。
3. 保持 schema 最小化，不提前引入 UI、统计聚合、评分扩展字段。
4. 在 `spec/schema_loader.py` 注册新 schema。
5. 如仓库实际需要 `spec/assets/schema` 镜像，再接入并同步；否则不作为默认要求。

**新增测试**

- `tests/python/test_rtos_diagnosis_schema.py`
- 扩展 `tests/python/test_schema_validator.py`

**targeted regression 命令**

```bash
python3 -m pytest tests/python/test_schema_validator.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_schema.py -q
```

**验收标准**

- 新 schema 能通过最小合法 suite / case fixture
- 缺失必填字段、错误枚举值、错误 evidence ref 时能被拒绝
- proof digest schema、现有 runtime benchmark schema 无变化

**风险**

- schema 过度设计，提前长出 report / UI / user-study 字段
- 忘记同步 `spec/assets/schema`

**fallback / rollback 策略**

- 若 report schema 需求不明确，只先落 `case` 和 `suite` 两个 schema
- 若现有 benchmark schema 复用代价过高，明确改为 Phase 6 独立 schema，不兼容迁移旧 benchmark

**是否需要主 agent 人工审阅后才能进入下一步**

是，必须。

以下 `P6.2` 到 `P6.7` 为后续候选计划，不属于当前执行范围；每个阶段都需要另行主 agent 审阅批准后才能实现。

### P6.2：Synthetic RTOS regression case fixture

**目标**

构造至少 8 类 known-root-cause synthetic cases，优先使用小型静态 fixture，不引入真实硬件依赖。

**涉及文件**

`tests/python/`、`desktop/sample_data.py`、`tool/`、`docs/runtime_optimization_case_bank/`

**预计新增文件**

- `tests/python/fixtures/rtos_diagnosis/phase6_suite.json`
- `tests/python/fixtures/rtos_diagnosis/*.json`
- `tests/python/fixtures/rtos_diagnosis/*.trace`
- 可选：`tool/build_phase6_rtos_diagnosis_fixtures.py`

**预计修改文件**

- `tests/python/test_pipeline.py`
- 可选最小修改：`desktop/sample_data.py`

**不允许修改的文件**

- `collector/`
- `parser/evidence_models.py`
- `desktop/evidence_export.py`
- `parser/runtime_optimization_gate.py`

**具体实现步骤**

1. 先固定 8 类 case 名称和 expected root cause 枚举。
2. 先做静态、小 traces 的 baseline / candidate 对。
3. `corrupt_segment`、`stale_sidecar`、`missing_calibration` 优先复用现有 degraded archive / sidecar fixture 思路，不新造通用故障框架。
4. 每个 case 至少绑定：baseline trace、candidate trace、expected root cause、expected affected entity、expected evidence refs。
5. 控制 fixture 尺寸，避免把 benchmark 做成大数据集。

**新增测试**

- `tests/python/test_rtos_diagnosis_fixtures.py`
- 如触达 pipeline 诊断路径，补最小 `tests/python/test_pipeline.py` 覆盖

**targeted regression 命令**

```bash
python3 -m pytest tests/python/test_rtos_diagnosis_fixtures.py -q
python3 -m pytest tests/python/test_pipeline.py -q
```

**验收标准**

- 至少 8 类 case 存在且可加载
- 不依赖真实硬件、真实板卡、外部网络
- 每个 case 都有完整 expected metadata
- fixture 总量保持小型，适合 CI

**风险**

- synthetic benchmark 被质疑不够真实
- fixture 过大导致 CI / regression 变慢
- `stale_sidecar` 或 `corrupt_segment` 样例污染其他正常测试

**fallback / rollback 策略**

- 若某类 case 过于脆弱，先用静态失效样例替代生成脚本
- 若案例规模过大，拆成 smoke suite 和 extended suite，默认只跑 smoke suite

**是否需要主 agent 人工审阅后才能进入下一步**

是，建议必须；至少需要对 case realism 和 fixture 尺寸做一次人工把关。

### P6.3：Evidence package and proof replay validation

**目标**

对每个 case 生成或引用 evidence package，验证 evidence package replay 与 full trace diagnosis 的一致性，并验证 proof digest 不随 advisor mode 漂移。

**涉及文件**

`desktop/repro_evidence.py`、`desktop/clipped_completeness.py`、`parser/evidence_closure.py`、`tests/python/test_repro_evidence.py`

**预计新增文件**

- `parser/rtos_diagnosis_replay.py`
- `tests/python/test_rtos_diagnosis_replay.py`

**预计修改文件**

- 可选最小修改：`desktop/repro_evidence.py`
- 可选最小修改：`desktop/clipped_completeness.py`

**不允许修改的文件**

- `parser/evidence_models.py`
- `desktop/evidence_export.py` 的 proof digest 语义
- `spec/schema/proof_digest.schema.json`
- `spec/assets/schema/proof_digest.schema.json`

**具体实现步骤**

1. 先书面定义 replay equivalence：比较哪些字段必须相同，哪些字段只做 report-only。
2. 用现有 reopen / completeness 机制复验每个 case 的 evidence package。
3. 对比 full trace diagnosis 与 reopened diagnosis 的 root cause、affected entity、result validity。
4. 对比 advisor mode 切换前后的 proof digest 漂移，目标为 0。
5. 单独记录 `evidence closure retention`，不要把 explanation 字段纳入 proof 事实。

**新增测试**

- `tests/python/test_rtos_diagnosis_replay.py`
- 扩展 `tests/python/test_repro_evidence.py`

**targeted regression 命令**

```bash
python3 -m pytest tests/python/test_repro_evidence.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_replay.py -q
```

**验收标准**

- `evidence package replay pass = 100%`
- `proof drift across advisor modes = 0`
- replay equivalence 定义清晰且被测试固定

**风险**

- full trace 与 replay 结果不一致
- 为了补 replay 字段误改 proof digest 语义
- 把 advisor explanation 混入 replay 对比事实

**fallback / rollback 策略**

- 若 full trace 与 replay 对齐标准不清，先冻结成文档定义，不继续实现
- 若 export 侧缺少只读元数据，优先新增外层 report 对比逻辑，不改 proof 模型

**是否需要主 agent 人工审阅后才能进入下一步**

是，必须。

### P6.4：Diagnosis metrics and report output

**目标**

增加 Phase 6 独立 diagnosis benchmark report，输出 top-k root cause hit、false positive count、package size、closure mode、replay pass，并明确 claim class。

**涉及文件**

`spec/schema_loader.py`、`spec/schema/`、`spec/assets/schema/`、`parser/telemetry.py`、`tool/`

**预计新增文件**

- `spec/schema/rtos_diagnosis_report.schema.json`
- `spec/assets/schema/rtos_diagnosis_report.schema.json`
- `tool/run_rtos_diagnosis_benchmark.py`
- `tests/python/test_rtos_diagnosis_benchmark.py`

**预计修改文件**

- `spec/schema_loader.py`
- 可选最小修改：`parser/telemetry.py`

**不允许修改的文件**

- `parser/benchmark_matrix.py` 的现有 runtime optimization 合约
- `spec/schema/benchmark_scenario.schema.json`
- `spec/schema/benchmark_report.schema.json`
- `parser/evidence_models.py`

**具体实现步骤**

1. 新建 Phase 6 专用 report schema，不复用旧 benchmark report 合约。
2. 报告字段显式带上 `claim_class`。
3. `top-k root cause` 固定标记为 `report_only`。
4. `human helpfulness` 若无真实数据，写 `not_measured` 或不出现，不造数。
5. report 输出优先 JSON，先不做 UI。

**新增测试**

- `tests/python/test_rtos_diagnosis_benchmark.py`
- 扩展 `tests/python/test_schema_validator.py`

**targeted regression 命令**

```bash
python3 -m pytest tests/python/test_schema_validator.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_benchmark.py -q
```

**验收标准**

- report schema 可校验
- `top-k root cause`、`false positive count`、`replay pass`、`package size`、`closure mode` 输出齐全
- claimable / report_only / not_claimable 区分明确

**风险**

- diagnosis top-k 被误写成强 claim
- 复用旧 benchmark contract 导致语义污染

**fallback / rollback 策略**

- 若 report schema 与旧 benchmark 系统耦合过深，先落独立 JSON runner，不接入旧矩阵
- 若 telemetry 改动会扩大范围，改为 report 端直接计算

**是否需要主 agent 人工审阅后才能进入下一步**

是，建议必须。

### P6.5：Advisor-assisted review variants

**目标**

对比 `no advisor`、`heuristic advisor`、`retrieval-grounded advisor`、`LLM explanation only` 四类 review 变体；advisor 只做 review / explanation，不进入 truth path。

**涉及文件**

`parser/runtime_advisor.py`、`parser/runtime_optimization_gate.py`、`parser/advisor_retrieval.py`、`tests/python/test_runtime_optimization_advisor.py`、`tests/python/test_runtime_optimization_agents.py`

**预计新增文件**

- `parser/rtos_diagnosis_review.py`
- `tests/python/test_rtos_diagnosis_advisor_review.py`

**预计修改文件**

- 可选最小修改：`parser/runtime_advisor.py`
- 可选最小修改：`parser/advisor_retrieval.py`

**不允许修改的文件**

- `parser/runtime_optimization_gate.py` 的 accept/reject 语义
- `parser/evidence_models.py`
- `desktop/evidence_export.py` 的 proof digest 语义
- parse / align / rebuild / sidecar / index truth path

**具体实现步骤**

1. 把四种 advisor mode 都建模成 review 层，而不是事实生成层。
2. `LLM explanation only` 只能解释 deterministic diagnosis 结果。
3. 对每种 mode 记录 proof drift，目标必须为 0。
4. retrieval-grounded mode 只能引用已存在证据，不创造新事实。
5. 缺 retrieval、schema 不合规或 gate 不接受时，必须 fail-closed 到 `no advisor` 或 heuristic 模式。

**新增测试**

- `tests/python/test_rtos_diagnosis_advisor_review.py`
- 继续运行 `tests/python/test_runtime_optimization_advisor.py`
- 继续运行 `tests/python/test_runtime_optimization_agents.py`

**targeted regression 命令**

```bash
python3 -m pytest tests/python/test_runtime_optimization_advisor.py -q
python3 -m pytest tests/python/test_runtime_optimization_agents.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_advisor_review.py -q
```

**验收标准**

- 四种 mode 都不改变 proof digest
- explanation 不写入 proof digest
- advisor 输出不生成 parse / align / rebuild / sidecar / index / proof facts

**风险**

- advisor explanation 被误当成 root cause truth
- Phase 6 改动误伤 Phase 5 sandbox / gate 边界
- retrieval-grounded mode 被写成事实生成器

**fallback / rollback 策略**

- 若 LLM 模式难以严格隔离，先只保留 `no advisor`、`heuristic advisor`、`retrieval-grounded advisor`
- 若运行时 advisor 接口改动过大，新增外层 Phase 6 review wrapper，不侵入现有 gate

**是否需要主 agent 人工审阅后才能进入下一步**

是，必须。

### P6.6：Human feedback schema and minimal review fixture

**目标**

增加最小 human feedback schema 和测试 fixture，只覆盖 `accepted / rejected`、`helpful / not helpful`、`unsafe / overclaim`、`missing evidence`，不做复杂 UI，不做真实实验系统。

**涉及文件**

`spec/schema_loader.py`、`spec/schema/`、`spec/assets/schema/`、`tests/python/`

**预计新增文件**

- `spec/schema/rtos_diagnosis_human_feedback.schema.json`
- `spec/assets/schema/rtos_diagnosis_human_feedback.schema.json`
- `tests/python/fixtures/rtos_diagnosis/human_feedback_minimal.json`
- `tests/python/test_human_feedback_schema.py`

**预计修改文件**

- `spec/schema_loader.py`
- 可选最小修改：`spec/schema/rtos_diagnosis_report.schema.json`

**不允许修改的文件**

- `desktop/`
- `collector/`
- `parser/evidence_models.py`
- 任意 UI / 服务端框架入口

**具体实现步骤**

1. 定义最小 feedback record schema。
2. 保持 feedback 与 diagnosis truth 分离，只作为 review artifact。
3. 不接数据库、不做账号系统、不做真实标注平台。
4. 如需关联 report，只做可选引用，不做强耦合。

**新增测试**

- `tests/python/test_human_feedback_schema.py`

**targeted regression 命令**

```bash
python3 -m pytest tests/python/test_human_feedback_schema.py -q
python3 -m pytest tests/python/test_schema_validator.py -q
```

**验收标准**

- 最小 schema 能校验通过
- 可表达 accepted / rejected、helpful / not helpful、unsafe / overclaim、missing evidence
- 不出现 UI、实验平台、数据库扩展

**风险**

- human feedback schema 变成过度开发
- feedback 被误当作诊断 truth

**fallback / rollback 策略**

- 若 report 耦合不清，先把 feedback 作为独立 JSON artifact
- 若字段继续膨胀，回退到最小枚举集

**是否需要主 agent 人工审阅后才能进入下一步**

是，建议必须。

### P6.7：Final Phase 6 regression and closeout

**目标**

完成 Phase 6 targeted tests、现有 advisor / agent tests、full python regression、工作区审计和冻结报告。

**涉及文件**

`tests/python/`、`docs/`、必要时只读检查 `tests/cpp/`

**预计新增文件**

- `docs/phase6_freeze_summary_YYYYMMDD.md`
- `docs/result/phase6_rtos_diagnosis_*.json`

**预计修改文件**

- 无硬性要求；优先新增 closeout 文档和结果文件

**不允许修改的文件**

- `parser/evidence_models.py`
- `collector/`
- 已冻结的 Phase 3 / 4 / 5 closeout 归档

**具体实现步骤**

1. 每完成一个子阶段，先做 targeted regression。
2. 全部通过后再跑现有 advisor / agent tests。
3. 最后跑 full python regression。
4. 复查 `git status --short`，确认只含预期 Phase 6 文件。
5. 写 freeze summary，明确 claimable / report_only / not_claimable。

**新增测试**

- 无新增测试类型；汇总运行前述所有 Phase 6 测试

**targeted regression 命令**

```bash
python3 -m pytest tests/python/test_runtime_optimization_advisor.py -q
python3 -m pytest tests/python/test_runtime_optimization_agents.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_schema.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_fixtures.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_replay.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_benchmark.py -q
python3 -m pytest tests/python/test_human_feedback_schema.py -q
python3 -m pytest tests/python -q
git -C /media/zzq/新加卷/patent/realization status --short
```

**验收标准**

- Phase 6 相关 targeted tests 全绿
- 现有 runtime optimization advisor / agent tests 全绿
- `python3 -m pytest tests/python -q` 全绿
- 工作区审计通过，freeze summary 完成

**风险**

- benchmark 数据过大导致回归时间失控
- 最终阶段才暴露 Phase 5 sandbox 回归
- 把 Phase 6 扩展成通用 anomaly detection 系统

**fallback / rollback 策略**

- 若全量测试过慢，开发阶段保持 smoke suite + targeted suite；冻结前仍必须跑全量 Python regression
- 若发现 Phase 5 回归，先回退相关 Phase 6 变更，不带着回归冻结

**是否需要主 agent 人工审阅后才能进入下一步**

是，最终冻结前必须审阅。

## 全局测试纪律

- 每完成一个小阶段就运行 targeted regression
- 不允许全部开发完才测试
- 所有 benchmark cases 必须有 unit 或 integration tests
- 所有 evidence package replay 必须可复验
- 所有 advisor mode 对比必须验证 `proof drift = 0`
- 所有 report claim 必须区分 `claimable`、`report_only`、`not_claimable`
- 最终必须运行：
  - `python3 -m pytest tests/python/test_runtime_optimization_advisor.py -q`
  - `python3 -m pytest tests/python/test_runtime_optimization_agents.py -q`
  - `python3 -m pytest tests/python -q`

## 不可越界清单

- 不修改 proof hash / proof digest 语义
- 不让 LLM / advisor 进入 trace truth path
- 不让 LLM / advisor 生成 parse / align / rebuild / sidecar edge / index / proof digest 的事实
- 不把 LLM explanation 写入 proof digest
- 不重写 collector 热路径
- 不引入通用 multi-agent framework
- 不做通用 anomaly detection SOTA
- 不宣称支持所有 RTOS、所有 trace schema、所有商业 trace 格式
- 不宣称 LLM 提高 trace 事实正确性
- 不宣称 Phase 6 完成后就是通用根因定位 SOTA

## 当前建议

- 可以开始的只有 `P6.0` 和 `P6.1`
- `P6.2+` 需要主 agent 先审阅 schema、claim boundary 和 replay equivalence 定义
- 后续实施仍应禁止自动提交；建议继续采用“规划 agent 产出计划 -> 主 agent 审阅 -> 实现 agent 分阶段执行”的方式
