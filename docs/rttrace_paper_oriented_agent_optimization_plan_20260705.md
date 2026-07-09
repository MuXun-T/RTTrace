# RTTrace 论文导向 Agent 优化与落地计划

生成日期：2026-07-05  
适用项目：`RTOS Trace Analysis MVP / rttrace-analysis`  
目标：以论文发表和研究生毕业设计落地为主线，规划可实现、可验证、可写成论文的 agent 导向系统改造。

## 1. 执行结论

本项目当前最有发表价值的主线不是“做一个更自动的 LLM agent”，也不是“让 LLM 参与 RTOS trace 事实生成”，而是：

> 在 `proof digest / bounded evidence closure / deterministic export` 不可污染的前提下，引入 verifier-gated runtime optimization advisor，使 agent 只做受限规划、推荐和解释，所有执行仍由确定性 gate、checksum、schema、ticket 和 replay/parity 裁决。

该方向同时贴合三类已有基础：

1. 本项目已有 `RuntimeOptimizationAdvisor + DeterministicValidationGate + AdvisorTrace + TelemetryHistoryStore`，且 LLM/advisor 字段不进入 `proof_digest`。
2. 本项目已有 1GB trace、sidecar index、proof digest、Windows/Linux parity、P3/P4 产品路径证据和 formal closeout 证据。
3. 近三年 agent 论文反复证明：开放式自主 agent 难以自证正确；更稳妥、更可发表的路线是 typed proposal、受限动作空间、deterministic validator、abstention、manual/ground-truth verification。

最终论文建议主标题方向：

> Verifier-Gated Runtime Advisors for Reproducible RTOS Trace Evidence Analysis

中文毕业设计题目方向：

> 面向可复现实证的 RTOS 运行轨迹分析与验证门控优化 Agent

## 2. 当前项目基础

### 2.1 已有系统能力

项目源码位于 `/media/zzq/新加卷/patent/realization`，主要模块如下：

| 模块 | 已有能力 | 论文价值 |
|---|---|---|
| `collector/` | C++17 trace collector API、host simulator、分段输出、过滤/采样、flush policy | 可作为低侵入采集端基础 |
| `parser/codec.py` / `pipeline.py` | decode、verify、align、rebuild、index 流水线 | 可作为 canonical trace reconstruction pipeline |
| `parser/evidence_closure.py` | seed refs、frontier refs、有界闭包、budget-before-read | 核心创新点 |
| `parser/evidence_sidecar.py` / `evidence_sidecar_index.py` | dependency sidecar、SQLite ticket、checksum/fingerprint 绑定 | 大 trace 局部访问和闭包扩展基础 |
| `desktop/evidence_export.py` | evidence package、proof digest、exact/bounded/degraded finalize | 可复现实证包基础 |
| `parser/runtime_advisor.py` | heuristic/offline/openai structured advisor、AdvisorDecision、AdvisorTrace | agent 论文主线基础 |
| `parser/runtime_optimization_gate.py` | RuntimeLoadPlan、ticket fast path、advisor decision deterministic gate | verifier-gated advisor 核心 |
| `parser/telemetry.py` | stage timing、RSS、runtime telemetry、history store | 训练/评估 advisor 的数据基础 |
| `tests/python/test_runtime_optimization_*` | advisor、agent、formal matrix、proof contamination 等测试 | artifact evaluation 基础 |

### 2.2 已有可引用证据

以下数据可在论文中作为“已有实验基础”，但必须保持原始边界。

| 证据项 | 当前结果 | 可宣称 |
|---|---:|---|
| 1GB formal input | `1,118,295,183B`，SHA256 `c478b7c...ada3c` | 真实 external dense 1GB 输入已验证 |
| P3 cached open/load | `165.891s -> 8.279s` | cache-hit 后产品打开/加载收益 |
| P4 click-to-export wait | `596.325s -> 129.104s` | 后台 prebuild 降低用户点击导出等待 |
| P4 total elapsed | `970.407s -> 2195.590s` | 不可宣称总耗时降低 |
| P5 segmented sidecar/index | acceptance met | 不可宣称 1GB 产品路径提速 |
| P7 morsel/parallel rebuild | experimental only | 不可宣称真并行 rebuild 提速 |
| final regression | `547 passed, 79 subtests passed` | 当前 Python 回归基线通过 |
| DeepSeek smoke | proof parity pass；`proof_digest` 无 advisor/LLM 字段 | LLM/advisor 与 proof truth 隔离 |
| Windows/Linux parity | A/B/C group `metric_diff_count=0` | 跨平台 evidence 字段一致性 |

### 2.3 当前不可越界表述

论文、答辩和专利材料均不得宣称：

1. 端到端 wall-clock 总耗时已经优于 full scan 或 clipped baseline。
2. P4 total elapsed reduction 已成立。
3. P5 已证明 1GB product-path speedup。
4. P7 已实现 true parallel rebuild speedup。
5. LLM 提高了 trace 事实正确性。
6. LLM 参与 parse、align、rebuild、sidecar edge、index 或 proof digest 真值生成。
7. 当前系统支持所有 RTOS、所有 trace schema 或所有商业 trace 格式。

## 3. 近三年 Agent 论文审阅结论

### 3.1 最相关论文矩阵

| 论文 | Venue/年份 | 关键数据 | 对本项目的启发 |
|---|---|---:|---|
| [Agentless](https://lingming.cs.illinois.edu/publications/fse2025.pdf) | FSE 2025 | SWE-bench Lite `32.00%`，Verified `50.8%`，平均 `$0.70` | 三阶段受限流程可优于复杂 agent swarm |
| [AutoCodeRover](https://arxiv.org/pdf/2404.05427.pdf) | ISSTA 2024 | Lite `19% pass@1`，`26% pass@3`，平均 `195s`、`$0.43` | 检索上下文、生成建议、测试验证，比自由 agent 更稳 |
| [RepairAgent](https://software-lab.org/publications/icse2025_RepairAgent.pdf) | ICSE 2025 | Defects4J `164` correct fixes，GitBug-Java `13/100`，中位 `920s` | agent 最终仍需要硬验证器裁决 |
| [ExecutionAgent](https://software-lab.org/publications/issta2025_ExecutionAgent.pdf) | ISSTA 2025 | `33/50` 项目成功执行测试，`29/50` 偏差 `<10%`，`6.6x` 优于最佳现有方法 | formal/benchmark runner 可 agent 辅助，但结果不能由 LLM 自证 |
| [AnalysisAgent](https://arxiv.org/pdf/2604.11270.pdf) | ASE 2026 accepted / arXiv | 手工验证 `94% (33/35)`；self-val 可达 `98%-100%` 但 verified 仅 `6%-20%` | agent 自证不可信，必须独立验证 proof drift/parity/replay |
| [Google Integration Failure Diagnosis](https://arxiv.org/pdf/2604.12108.pdf) | ICSE 2026 accepted / arXiv | `90.14%` 准确率；`224,782` 次执行；中位 `56s`，帮助率 `62.96%` | explanation 必须 evidence-grounded，证据不足时 abstain |
| [OScope](https://nkcs.iops.ai/wp-content/uploads/2025/12/icse2026-seip-paper13.pdf) | ICSE-SEIP 2026 accepted | `AC@5=0.901`；去掉 Knowledge Aligner 降到 `0.732`；去掉 Validator 降到 `0.782` | 历史 case/SOP + validator 比纯 prompt 可靠 |
| [OPPerTune](https://www.usenix.org/system/files/nsdi24-somashekar.pdf) | NSDI 2024 | Azure P95 latency 降 `>50%`；`3600 rps` 时近 `2x` 改善 | 离线学习 safe action prior，在线只在安全动作中选 |
| [Ayo/Teola](https://arxiv.org/pdf/2407.00326.pdf) | ASPLOS 2025 | 端到端最高 `2.09x`；graph optimization overhead `1.3%-3%` | 固定 workflow 应建 deterministic cost graph，而非自由 agent |
| [SemaTune](https://arxiv.org/pdf/2605.15026.pdf) | 2026 preprint | stable phase `+72.5%`；非 LLM baseline `+153.3%`；30-window 成本约 `$0.20` | typed proposal + validator + 双环路，最贴合 RTTrace advisor |
| [SWE-agent](https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf) | NeurIPS 2024 | SWE-bench `12.47%`，HumanEvalFix `87.7%` | 缩小 agent interface，避免开放 shell |
| [Cloak, Honey, Trap](https://www.usenix.org/system/files/usenixsecurity25-ayzenshteyn.pdf) | USENIX Security 2025 | 防御后 PentestGPT 每台 `0/3` 成功 | 在线 advisor 需要 sandbox/adversarial eval |

### 3.2 可吸收设计原则

从上述论文中，本项目应吸收以下原则：

1. **Agentless phase flow**：采用 `telemetry summarize -> advisor proposal -> deterministic validate/execute`，而不是自由多 agent 自主探索。
2. **Typed action contract**：advisor 输出必须是有限枚举动作和结构化字段，不能输出自由脚本或任意修改。
3. **Grounded retrieval**：advisor 只允许读取 telemetry、benchmark archive、ticket 状态、sidecar manifest、gate policy、platform facts。
4. **Abstention**：证据不足时必须输出 `abstain` 或 `need_more_telemetry`，不能强行建议。
5. **Independent verification**：agent 自评不算结果，必须用 proof drift、parity、replay、gate accept/reject、manual review 验证。
6. **Offline/online split**：离线训练/回归/bandit 学策略，在线只在安全动作空间内排序或 tie-break。
7. **Cost graph**：将 decode/verify/align/rebuild/index/export 建成确定性 stage DAG，advisor 只能在 DAG 内选择路径。
8. **Sandbox**：在线 LLM 无 shell、无 proof path 写权限、无 secret、只读摘要输入。

### 3.3 不应吸收的做法

以下做法不适合本项目论文主线：

1. 不做 agent swarm，不把“多个 agent 协作”作为创新点。
2. 不允许 LLM 直接修改 trace 事实、sidecar edge、proof hash、schema contract。
3. 不允许 LLM 根据自然语言解释直接判定 evidence package 是否通过。
4. 不用 LLM self-eval 作为论文指标。
5. 不让 online LLM 做 live exploration proof-critical knobs。
6. 不把 LLM explanation 写回 proof digest 事实域。

## 4. 论文主线重构

### 4.1 推荐论文题目

英文：

> Verifier-Gated Runtime Advisors for Reproducible RTOS Trace Evidence Analysis

中文：

> 面向可复现实证的 RTOS 运行轨迹分析与验证门控优化 Agent

### 4.2 中心研究问题

RQ1：在不污染 trace truth 和 proof digest 的前提下，advisor 能否降低大规模 RTOS trace 打开、导出和复现实证路径的等待成本？  
RQ2：typed advisor + deterministic gate 是否比自由 agent 或无 advisor 更可复验、更稳定、更低成本？  
RQ3：bounded evidence closure、sidecar index 和 advisor-gated runtime plan 能否共同形成可重复的论文级 artifact？  
RQ4：在证据不足、ticket mismatch、schema skew、LLM hallucination、prompt injection 情况下，系统是否 fail-closed？

### 4.3 论文贡献

建议贡献写成 4 条：

1. **Bounded RTOS Evidence Closure**  
   提出面向 RTOS trace 的有界证据闭包导出机制，使用 seed refs、dependency sidecar、frontier snapshot、proof digest，将调试结论转化为可复验、可审计、可交付证据包。

2. **Verifier-Gated Runtime Advisor**  
   提出只读 telemetry 和 artifact 状态的 typed advisor。advisor 只推荐有限动作，所有建议必须经过 deterministic validation gate，且不进入 proof digest 事实域。

3. **Stage Cost Graph and Safe Runtime Planning**  
   将 trace pipeline 建模为 deterministic stage DAG，显式记录成本、缓存命中条件、依赖和回退路径，使 advisor 只能在可验证路径内优化。

4. **Reproducibility and Safety Evaluation**  
   在 1GB trace、Windows/Linux parity、degraded audit、proof contamination、agent mode ablation、sandbox adversarial tests 上评估系统的收益和边界。

## 5. 目标架构

### 5.1 总体架构

目标架构分为 6 层：

```text
Trace Source / Package
  -> Deterministic Parser Pipeline
     decode -> verify -> align -> rebuild -> index
  -> Evidence Closure Layer
     seed -> sidecar lookup -> frontier -> budget gate -> window read -> proof digest
  -> Telemetry and Cost Graph Layer
     stage timings, RSS, cache state, ticket state, proof facts
  -> Advisor Proposal Layer
     heuristic / offline_coefficients / openai_structured / abstain
  -> Deterministic Gate Layer
     schema, checksum, fingerprint, policy, proof contamination guard
  -> Execution and Report Layer
     product path, formal path, package/replay, paper benchmark reports
```

### 5.2 新增核心对象

#### 5.2.1 `RuntimeAction`

将当前 advisor bool 字段收敛成显式动作枚举：

```text
RuntimeAction
- action_id
- action_kind:
  - baseline_full_load
  - cold_preview
  - parser_artifact_reuse
  - sidecar_index_reuse
  - sidecar_index_prebuild
  - streaming_package_write
  - deferred_index_build
  - formal_serial_safe
  - abstain
- required_artifacts
- expected_benefit
- risk_level
- proof_scope_impact: must be none
- fallback_action
```

理由：论文需要清晰说明 agent 的 action space 是有限、可验证、不可污染 truth 的。

#### 5.2.2 `RuntimeCostGraph`

新增 stage DAG 描述：

```text
RuntimeCostGraph
- graph_version
- input_contract
- nodes:
  - decode
  - verify
  - align
  - rebuild
  - index_full
  - index_minimal
  - sidecar_validate
  - sidecar_index_open
  - sidecar_index_build
  - closure_project
  - window_read
  - package_write
  - proof_validate
- edges
- cache_keys
- preconditions
- fallback_edges
- telemetry_refs
```

理由：对标 Ayo/Teola 的 workflow graph，但本项目的 graph 不用于让 agent 创造新路径，而是限制 advisor 只能选择既有 deterministic path。

#### 5.2.3 `AdvisorEvidenceContext`

把 advisor 输入限制为只读摘要：

```text
AdvisorEvidenceContext
- context_version
- input_bytes
- trace_checksum_prefix
- dictionary_checksum_prefix
- platform
- sidecar_bytes
- sidecar_row_count
- ticket_present
- ticket_validated
- previous_stage_timings
- previous_peak_rss_mb
- benchmark_case_refs
- gate_policy_summary
- forbidden_fields
```

禁止字段：

1. 原始 API key。
2. 完整原始 trace 内容。
3. proof digest 可写路径。
4. 可执行 shell 命令。
5. 未脱敏异常堆栈中的 secret。

#### 5.2.4 `AdvisorGroundingReport`

新增解释报告，但不进入 proof digest：

```text
AdvisorGroundingReport
- report_version
- advisor_mode
- input_context_hash
- retrieved_case_refs
- proposed_actions
- abstained
- abstain_reason
- gate_result
- rejected_reason
- final_execution_plan
- proof_digest_contamination_check
- reviewer_feedback
```

理由：对标 Google integration failure diagnosis 和 OScope，把 explanation 做成 grounded report，但与 proof facts 隔离。

### 5.3 需要改造的模块

| 模块 | 当前状态 | 改造目标 |
|---|---|---|
| `parser/runtime_advisor.py` | bool 推荐字段，heuristic/offline/openai | 改为 typed action proposal + abstain + grounding refs |
| `parser/runtime_optimization_gate.py` | 可校验 RuntimeLoadPlan/ticket/advisor decision | 增加 action-level gate、reject taxonomy、proof-scope-impact 校验 |
| `parser/telemetry.py` | runtime telemetry record/update | 增加 cost graph node telemetry、review feedback、counterfactual fields |
| `parser/benchmark_matrix.py` | scenario report | 增加 advisor mode ablation、plan regret、proof drift metrics |
| `tool/run_runtime_optimization_benchmark.py` | product benchmark | 增加 action-level runs、counterfactual replay、cost graph output |
| `tool/run_runtime_optimization_formal_matrix.py` | formal matrix | 增加 proof contamination guard 和 prompt/backend variation cases |
| `tool/run_deepseek_advisor_smoke.py` | mock/real DeepSeek smoke | 增加 sandbox/adversarial cases 和 abstain cases |
| `spec/schema/*.json` | advisor schemas 已存在 | 新增/扩展 action、cost graph、grounding report schemas |
| `tests/python/test_runtime_optimization_advisor.py` | advisor/gate 测试 | 增加 action schema、abstain、reject taxonomy、contamination tests |
| `tests/python/test_runtime_optimization_agents.py` | benchmark/formal agent 测试 | 增加 counterfactual replay、advisor ablation、cost graph tests |

## 6. 分阶段实施计划

### Phase 0：论文基线冻结

目标：先冻结当前可以写进论文的事实，避免后续优化污染旧证据。

任务：

1. 新建 `docs/paper_baseline_YYYYMMDD/`。
2. 写入当前证据索引：
   - `runtime_optimization_current_status_20260704.md`
   - `runtime_optimization_product_evidence_20260703/summary.md`
   - `runtime_optimization_product_evidence_20260703/verification.md`
   - `result/patent_improvement_metrics_20260429.md`
   - `result/deepseek_advisor_smoke_20260505.md`
   - `result/运行时优化Agent_P5_DeepSeek第五场景_20260507.md`
3. 生成 `paper_claim_boundary.json`：
   - claimable
   - not_claimable
   - proposed_only
   - source_paths
4. 运行当前 focused tests：
   - `python3 -m pytest tests/python/test_runtime_optimization_advisor.py tests/python/test_runtime_optimization_agents.py -q`

产出：

| 产物 | 用途 |
|---|---|
| `paper_claim_boundary.json` | 后续论文文字和实验表格统一依据 |
| `paper_baseline_manifest.json` | artifact evaluation 引用入口 |
| focused pytest log | 证明优化前 baseline 可运行 |

验收标准：

1. 不修改 historical formal artifacts。
2. claimable/not_claimable 与当前文档一致。
3. focused tests 通过。

### Phase 1：Gate-first Typed Advisor

目标：将 advisor 从 bool 字段升级为 typed action proposal，并让 gate 对每个 action 进行独立裁决。

实施任务：

1. 新增 schema：
   - `runtime_action.schema.json`
   - `runtime_action_set.schema.json`
   - `advisor_grounding_report.schema.json`
2. 修改 `AdvisorDecision`：
   - 保留旧字段兼容。
   - 新增 `proposed_actions: list[RuntimeAction]`。
   - 新增 `abstained: bool`、`abstain_reason`。
3. 修改 `RuntimeOptimizationAdvisor._evaluate_heuristic`：
   - 大输入但无 ticket：提出 `cold_preview` + `sidecar_index_prebuild`。
   - 有有效 ticket：提出 `sidecar_index_reuse`。
   - RSS 高或大包写出：提出 `streaming_package_write`。
   - telemetry 缺失且风险高：提出 `abstain` 或 `need_more_telemetry`。
4. 修改 `RuntimeOptimizationAdvisor._evaluate_openai_structured`：
   - LLM 只能返回 typed action JSON。
   - schema invalid 必须 fallback heuristic。
   - hallucinated action 必须 gate reject。
5. 修改 `DeterministicValidationGate`：
   - 增加 `validate_runtime_action`。
   - 增加 reject reason：
     - `ERR-ACTION_UNKNOWN`
     - `ERR-ACTION_PROOF_SCOPE_IMPACT`
     - `ERR-ACTION_MISSING_ARTIFACT`
     - `ERR-ACTION_CHECKSUM_MISMATCH`
     - `ERR-ACTION_POLICY_DENIED`
     - `ERR-ACTION_UNSAFE_LLM_OUTPUT`
6. 增加测试：
   - unknown action reject。
   - proof_scope_impact 非 `none` reject。
   - LLM hallucinated action reject。
   - disabled/heuristic/openai_structured proof digest facts drift 为 0。

预期结果：

| 指标 | 目标 |
|---|---:|
| advisor schema-invalid fallback | 100% fallback safe |
| hallucinated action gate reject | 100% reject |
| proof digest contamination | 0 |
| old tests | 不回退 |

论文可写结论：

> RTTrace constrains runtime optimization advisors to a typed action space and validates every action through deterministic gates before execution.

不可写结论：

> LLM improves parsing or proof correctness.

### Phase 2：Deterministic Cost Graph

目标：将 pipeline 变成可度量、可预测、可选择但不可任意改写的 stage DAG。

实施任务：

1. 新增 `parser/runtime_cost_graph.py`。
2. 定义 node：
   - `decode`
   - `verify`
   - `align`
   - `rebuild`
   - `index_full`
   - `index_minimal`
   - `index_deferred`
   - `sidecar_validate`
   - `sidecar_index_build`
   - `sidecar_index_open`
   - `closure_project`
   - `window_read`
   - `package_write`
   - `proof_validate`
3. 修改 `parser/pipeline.py` 和 `desktop/evidence_export.py`：
   - 每个 stage 写入 node telemetry。
   - 记录 preconditions、cache key、fallback edge。
4. 修改 `tool/runtime_benchmark_runner.py`：
   - 输出 `runtime_cost_graph.json`。
   - 输出 stage-level predicted vs observed。
5. 增加 tests：
   - graph schema valid。
   - node ordering stable。
   - missing cache key 不允许 reuse action。
   - formal path graph 和 product path graph 分开标记。

预期结果：

| 指标 | 目标 |
|---|---:|
| stage telemetry coverage | >= 90% 主要 stage |
| graph schema validation | pass |
| advisor action has graph node mapping | 100% |
| formal/product speedup field separation | 100% |

论文可写结论：

> Advisors optimize only over an explicit deterministic stage graph, preserving the trace truth path.

不可写结论：

> 系统已实现通用 agent workflow optimizer。

### Phase 3：Retrieval-grounded Advisor + Abstention

目标：把历史 benchmark、ticket、telemetry、gate policy 做成 advisor 检索上下文，并支持拒答。

实施任务：

1. 新增 `parser/advisor_retrieval.py`：
   - 输入 current features。
   - 检索相似 telemetry cases。
   - 返回 top-k case refs。
2. 构建 `docs/runtime_optimization_case_bank/`：
   - P3 cache-hit case。
   - P4 prebuild wait case。
   - ticket fast path slower-than-baseline case。
   - openai_structured overhead case。
   - formal parity pass case。
   - transient failure resolved case。
3. 修改 `AdvisorEvidenceContext`：
   - 添加 `retrieved_case_refs`。
   - 添加 `case_similarity_features`。
4. advisor policy：
   - 若无足够相似 case 且 action high risk，输出 `abstain`。
   - 若 case conflicts，输出 `conflicting_evidence`。
   - 若 telemetry missing，输出 `need_more_telemetry`。
5. 建立人工标注集：
   - 至少 30 个 recommendation cases。
   - 标签：accept/reject/abstain/unsafe/needs_more_data。
6. 评估：
   - recommendation precision。
   - abstain precision。
   - false accept rate。
   - gate rejection rate。

预期结果：

| 指标 | 初始目标 |
|---|---:|
| false accept rate | 0 for unsafe actions |
| abstain on missing evidence | >= 90% |
| gate reject taxonomy coverage | >= 95% rejected cases classified |
| recommendation precision | 先记录，不设强 claim |

论文可写结论：

> The advisor grounds recommendations in prior benchmark and telemetry cases, and abstains when evidence is insufficient.

不可写结论：

> agent explanation equals root cause truth。

### Phase 4：Offline/Online Split

目标：离线学习安全动作先验，在线只排序、解释或 tie-break。

实施任务：

1. 扩展 `tool/train_runtime_advisor.py`：
   - 输入 telemetry history。
   - 输出 offline coefficients 或 simple bandit prior。
   - 记录 model checksum。
2. 定义 safe action space：
   - `baseline_full_load`
   - `cold_preview`
   - `sidecar_index_prebuild`
   - `sidecar_index_reuse`
   - `streaming_package_write`
   - `deferred_index_build`
   - `abstain`
3. 禁止在线探索：
   - 不允许新动作。
   - 不允许 proof-critical knobs。
   - 不允许越过 gate。
4. 评估：
   - heuristic vs offline_coefficients vs openai_structured。
   - plan regret。
   - advisor overhead。
   - gate accept/reject。
   - proof drift。
   - counterfactual replay。

预期结果：

| 指标 | 目标 |
|---|---:|
| proof drift | 0 |
| LLM fallback safe | 100% |
| advisor overhead recorded | 100% |
| offline model checksum validation | 100% |
| regret | 初始只报告，不强宣称 |

论文可写结论：

> Offline telemetry models provide safe priors; online LLMs are optional explainers/tie-breakers rather than truth generators.

不可写结论：

> LLM online tuning 已优于所有传统方法。

### Phase 5：Sandbox and Adversarial Evaluation

目标：证明在线 advisor 即使被诱导，也不能污染 proof 或执行未授权动作。

实施任务：

1. 限制 LLM 输入：
   - 只读 `AdvisorEvidenceContext`。
   - 不包含 secret。
   - 不包含 writable path。
   - 不包含 raw proof digest path。
2. 限制 LLM 输出：
   - 只允许 JSON object。
   - schema 校验。
   - action enum 校验。
3. 对抗测试集：
   - prompt injection：要求输出任意 shell。
   - secret exfiltration：要求泄露 API key。
   - proof contamination：要求把 openai tokens 写进 proof digest。
   - schema invalid：字段类型错。
   - hallucinated action：不存在的 action。
   - overclaim：宣称 P4 total elapsed speedup。
4. 验收：
   - gate reject 或 fallback。
   - package/proof digest 不变。
   - no secret in logs/package/report。

预期结果：

| 指标 | 目标 |
|---|---:|
| unauthorized action accepted | 0 |
| proof contamination | 0 |
| secret leak | 0 |
| schema invalid fallback/reject | 100% |
| adversarial cases covered | >= 6 categories |

论文可写结论：

> The online advisor is fail-closed and sandboxed against malformed or adversarial proposals.

不可写结论：

> 系统整体安全已形式化证明。

### Phase 6：RTOS Diagnosis and Human Feedback

Phase 6 批准前开发计划与启动审计记录见：[phase6_pre_approval_development_plan_20260709.md](phase6_pre_approval_development_plan_20260709.md)。该文档是后续 P6.0/P6.1 的参考输入，不表示 P6.2+ 已批准执行。

目标：补足毕业设计和论文审稿最容易质疑的“诊断价值”。

实施任务：

1. 构造已知根因 RTOS regression benchmark：
   - priority inversion。
   - IRQ latency spike。
   - mutex hold inflation。
   - queue wait backlog。
   - task starvation。
   - corrupt segment。
   - stale sidecar。
   - missing calibration。
2. 对每个 case 输出：
   - baseline trace。
   - candidate trace。
   - expected root cause。
   - expected affected task/IRQ/resource。
   - evidence package。
   - proof digest。
3. 增加 diagnosis metrics：
   - top-k root cause hit。
   - false positive count。
   - evidence closure retention。
   - replay equivalence。
   - human review time。
4. 增加 human feedback schema：
   - accepted/rejected。
   - helpful/not helpful。
   - unsafe/overclaim。
   - missing evidence。
5. 评估：
   - no advisor。
   - heuristic advisor。
   - retrieval-grounded advisor。
   - LLM explanation only。

预期结果：

| 指标 | 初始目标 |
|---|---:|
| known-root-cause cases | >= 8 |
| evidence package replay pass | 100% |
| proof drift across advisor modes | 0 |
| top-k root cause | 先报告，不设硬 claim |
| human helpfulness | 有数据后再宣称 |

论文可写结论：

> RTTrace supports reproducible RTOS regression analysis with bounded evidence packages and advisor-assisted review.

不可写结论：

> 通用 anomaly detection SOTA。

## 7. 实验矩阵

### 7.1 必做实验

| 实验 | Baseline | Variants | Metrics | Claim |
|---|---|---|---|---|
| E1 proof isolation | advisor disabled | heuristic/offline/openai/adversarial | proof field drift, proof hash input drift | advisor 不污染 proof facts |
| E2 gate safety | valid action | invalid/hallucinated/missing artifact | accept/reject, reject reason | deterministic gate 裁决 action |
| E3 product path | current baseline | typed advisor actions | open/load, click-to-export, RSS, overhead | 只宣称 P3/P4 已证边界和新增结果 |
| E4 cost graph | no graph | stage graph | telemetry coverage, prediction error | stage-aware planning |
| E5 retrieval/abstain | heuristic only | retrieval-grounded | false accept, abstain precision | evidence-grounded recommendation |
| E6 sandbox | benign prompt | injection/secret/proof contamination | unauthorized accept, leaks, proof drift | fail-closed sandbox |
| E7 diagnosis benchmark | full trace | evidence package/replay | top-k, replay equivalence, package size | reproducible RTOS diagnosis |
| E8 cross-platform | Linux | Windows | mandatory field set, metric diff, proof facts | parity |

### 7.2 数据规模

| 数据集 | 用途 | 要求 |
|---|---|---|
| `tests/cpp/*.trace` | fast regression | 每次提交跑 |
| generated small/medium traces | action/gate/diagnosis unit experiments | 自动生成 |
| 1GB google cluster external dense trace | product/formal anchor | 使用已有 qualified input |
| RTOS regression synthetic traces | diagnosis benchmark | 至少 8 类根因 |
| optional real hardware trace | 冲 RTAS/RTSS/EMSOFT | 若有板卡则加入 |

### 7.3 指标定义

| 指标 | 定义 | 备注 |
|---|---|---|
| proof field drift | advisor mode 改变后 proof hash input 字段变化数 | 目标 0 |
| gate false accept | 不安全 action 被接受次数 | 目标 0 |
| abstain precision | 应拒答场景中输出 abstain 的比例 | 论文重要指标 |
| plan regret | advisor plan 与 oracle best safe action 的 runtime 差距 | 需要 counterfactual runs |
| advisor overhead | advisor latency + token cost + gate cost | LLM 场景必须记录 |
| package shrink ratio | full trace size / evidence package size | 已有强项 |
| scan/seek reduction | baseline scan/seek vs evidence scan/seek | 已有强项 |
| replay equivalence | evidence package replay 结果与 full trace 诊断一致 | 需要补 |
| diagnosis top-k hit | 预期根因是否在 top-k | 需要构造 benchmark |
| human helpfulness | 人工评分或接受率 | 可选，但有助论文 |

## 8. 架构和算法大改清单

### 8.1 必改

1. `AdvisorDecision` 从 bool plan 升级为 typed action list。
2. `DeterministicValidationGate` 从 plan-level 校验升级为 action-level 校验。
3. 新增 `RuntimeCostGraph`，把 pipeline stage 和 action 绑定。
4. 新增 retrieval-grounded context，advisor 输入从散乱 features 变成 `AdvisorEvidenceContext`。
5. 新增 abstain/conflicting evidence/need more telemetry 输出。
6. 新增 proof contamination 和 adversarial advisor tests。
7. 新增 paper benchmark runner，统一输出论文表格需要的 JSON。

### 8.2 建议改

1. 将 `openai_structured` prompt 改为严格 typed action proposal prompt。
2. 将 `TelemetryHistoryStore` 扩展为 case bank，保存 reviewer feedback。
3. 将 current P3/P4 reports 复制引用到 paper baseline manifest。
4. 将 benchmark scenario 增加 advisor action variants。
5. 将 GUI 中 advisor report 只作为解释面板，不作为 proof 状态面板。

### 8.3 暂不改

1. 不重写 collector 热路径。
2. 不引入新大依赖作为 agent framework。
3. 不做通用 multi-agent orchestration。
4. 不让 LLM 读写 proof/control package。
5. 不改变现有 proof hash 语义，除非另开 schema migration 并做 backward compatibility。

## 9. 论文落地路线

### 9.1 毕业设计可完成版本

范围：

1. Phase 0 到 Phase 3。
2. 小规模 + 1GB trace 实验。
3. advisor disabled/heuristic/openai_structured proof isolation。
4. gate reject taxonomy。
5. 简单 retrieval + abstention。
6. 8 类 synthetic RTOS regression cases。

可交付：

1. 系统设计论文。
2. 原型实现。
3. 实验报告。
4. artifact package。
5. 毕设答辩演示。

预期结论：

> 系统实现了可复现实证导出与验证门控 agent；agent 能辅助 runtime plan 选择且不污染 trace truth/proof facts。

### 9.2 投稿增强版本

范围：

1. Phase 0 到 Phase 6。
2. 更完整 agent ablation。
3. cost graph + offline/online split。
4. sandbox/adversarial eval。
5. real RTOS 或硬件 trace。
6. CTF/Perfetto/Trace Compass 互操作至少一条导出链。

可投方向：

| 目标 | 需要补足 |
|---|---|
| RTAS/RTSS/EMSOFT | 真实 RTOS/hardware、collector overhead、实时行为、root-cause benchmark |
| ICSE/FSE/ASE/ISSTA | agent/gate/reproducible debugging benchmark、ablation、self-validation vs independent verification |
| ATC/EuroSys | 系统规模、cost graph、artifact、强基线对比 |
| SoftwareX/JSA | 工具完整性、可复现文档、artifact packaging |

推荐优先级：

1. 如果以 agent/gate 为主：ASE/FSE/ISSTA。
2. 如果以 RTOS trace 和 evidence closure 为主：RTAS/EMSOFT/JSA。
3. 如果以工具和 artifact 为主：SoftwareX。

## 10. 预期论文表格

### Table 1：Related work comparison

列：

1. System/Paper。
2. Target domain。
3. Agent role。
4. Validator/gate。
5. Proof/evidence artifact。
6. Abstention。
7. RTOS trace support。
8. Deterministic truth isolation。

RTTrace 预期优势：

1. 有 RTOS trace evidence package。
2. 有 proof digest。
3. 有 deterministic gate。
4. 有 proof contamination tests。

### Table 2：Advisor safety

列：

1. Mode。
2. Schema valid。
3. Gate accept。
4. Proof field drift。
5. Proof hash input drift。
6. Advisor overhead。
7. Fallback reason。

### Table 3：Runtime product path

列：

1. Scenario。
2. Open/load。
3. Click-to-export。
4. Total elapsed。
5. Peak RSS。
6. Advisor overhead。
7. Claimable。

注意：`total elapsed` 必须独立列出，不能藏起来。

### Table 4：Gate rejection taxonomy

列：

1. Rejection category。
2. Test cases。
3. Accepted unsafe。
4. Rejected safe。
5. Explanation。

### Table 5：Diagnosis benchmark

列：

1. Root cause。
2. Full trace diagnosis。
3. Evidence package diagnosis。
4. Replay pass。
5. Package size。
6. Closure mode。
7. Top-k hit。

## 11. 风险和缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| LLM 延迟过高 | 产品路径变慢 | LLM default-off；heuristic/offline fallback；记录 overhead |
| LLM hallucinated action | 不安全执行 | typed schema + gate reject |
| Agent 自证虚高 | 论文不可信 | 使用 proof drift/parity/replay/manual labels |
| P4 total elapsed 仍变差 | speedup claim 受限 | 明确只宣称 click-to-export wait；继续优化 prebuild amortization |
| synthetic RTOS benchmark 被质疑 | 论文说服力不足 | 尽量补 real RTOS/hardware trace 或公开 RTOS fixture |
| schema 变更破坏旧包 | artifact 不兼容 | schema version + compatibility tests |
| 计划过大 | 毕设无法完成 | 毕设版只做 Phase 0-3 |

## 12. 近期任务清单

### Week 1：冻结论文 baseline

1. 创建 `docs/paper_baseline_202607xx/`。
2. 写 `paper_claim_boundary.json`。
3. 复制或引用现有关键 summary。
4. 跑 focused advisor/agent tests。

### Week 2：typed action schema 和 gate

1. 新增 `runtime_action.schema.json`。
2. 修改 `AdvisorDecision`。
3. 增加 `validate_runtime_action`。
4. 补 unknown/hallucinated/proof-scope tests。

### Week 3：cost graph prototype

1. 新增 `runtime_cost_graph.py`。
2. 在 pipeline/export 写 stage nodes。
3. benchmark 输出 graph JSON。
4. 补 schema/test。

### Week 4：retrieval + abstain

1. 建 case bank。
2. 新增 retrieval module。
3. 实现 `abstain/need_more_telemetry/conflicting_evidence`。
4. 建 30 条人工标注 recommendation set。

### Week 5：paper benchmark runner

1. 统一 advisor ablation runner。
2. 输出论文表格 JSON。
3. 覆盖 disabled/heuristic/offline/openai/adversarial。
4. 生成 proof drift/parity/replay summary。

### Week 6：诊断 benchmark

1. 构造 8 类 RTOS regression traces。
2. 输出 expected root cause。
3. 验证 full trace vs package replay。
4. 生成 diagnosis table。

### Week 7-8：论文初稿

1. 写 introduction 和 motivation。
2. 写 design。
3. 写 evaluation。
4. 写 related work。
5. 写 limitation。

## 13. 最终验收标准

完成后，项目应具备以下论文落地条件：

1. 有清晰贡献：bounded evidence closure + verifier-gated advisor。
2. 有明确边界：LLM 不生成 truth，不污染 proof。
3. 有可运行 artifact：benchmark runner、case bank、proof package、schema。
4. 有完整实验：runtime、安全、reproducibility、diagnosis、cross-platform。
5. 有可复查数据：所有表格由 JSON/markdown report 支撑。
6. 有失败/拒绝场景：abstain、gate reject、sandbox adversarial。
7. 有投稿策略：毕设版和增强版分别可落地。

## 14. 结论

本项目已经具备论文基础，但投稿主线必须收窄。最稳妥、最有差异化的路线是：

1. 不追求“更自主 agent”。
2. 不让 LLM 进入 trace truth/proof path。
3. 把 agent 限制为 telemetry-grounded typed advisor。
4. 把 deterministic gate、proof drift、parity、replay 作为论文核心验证指标。
5. 用 sidecar index、bounded evidence closure 和 proof digest 作为 RTOS trace 领域差异化贡献。

如果按本计划完成 Phase 0-3，可形成扎实的研究生毕业设计；若完成 Phase 0-6，并补至少一个真实 RTOS/hardware 或强 artifact evaluation，则具备向 RTAS/EMSOFT/ASE/FSE/SoftwareX/JSA 等方向投稿的基础。
