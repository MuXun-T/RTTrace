# CCF-B Research Validation Track 总体规划

## 1. Document Status

| 项目 | 值 |
| --- | --- |
| 状态 | **Planning Baseline Conditionally Approved** |
| 修订日期 | 2026-07-17（Asia/Shanghai） |
| 分支 / START_HEAD / 当前 HEAD | `main` / `5c257ed83a19086898a0173678c456410f55616d` / `5c257ed83a19086898a0173678c456410f55616d` |
| Phase 7 freeze | `5c257ed83a19086898a0173678c456410f55616d` |
| Task Type | Planning Document Correction |
| Implementation Authorization | **Not Granted** |
| Experiment Execution / Prior-Art Search Execution | **Not Started** / **Not Started** |
| Automatic Commit / Phase 7 Historical Mutation | **Disabled** / **Forbidden** |

本文是冻结工程之外的 Research Validation Track / Paper Evidence Track 基线，不是实施授权、查新结论、实验结果、论文正文或录用保证。工程完成、研究问题登记、prior-art 核验、最终 claim freeze、实验完成、claim 支持、投稿准备和实际录用是不同状态，不能互相替代。非目的：修改 Phase 0--7、代码/schema/fixture/test、正式 benchmark、硬件采集、正式查新、数据生成、历史重写、commit/push/tag。

## 2. Executive Decision

项目尚非 CCF-B-ready。冻结工程支持可审计合同、有限 host observation、evidence closure/sidecar/index、package reopen、identity/checksum、deterministic report 和 fail-closed validation；它没有独立 diagnosis ground truth、最近邻学术方法的公平比较、多 case 统计证据、真实硬件或第二独立来源，也没有已验证的文献差异。

推荐主线保持为 **Bounded and Auditable Evidence Closure for Reproducible RTOS Trace Analysis**。研究问题是：在明确预算、依赖合同和 truth boundary 下，选择性 evidence closure 能否比 full scan/fixed clipping 以更低净成本保留所需诊断证据/结果，并对不完整或错配 package fail closed。Agent/LLM 保持 truth/proof 路径外的 appendix 安全扩展，不是主贡献。

最终论文 claim 不得在查新前冻结：R1A 仅登记候选，R2 核验最近邻并作 `PROCEED`/`REFRAME`/`ABANDON`。只有 R2 对**当前最终候选**给出 `PROCEED` 才能进入 R1B；`REFRAME` 必须回到 R1A 登记新候选并重新 R2，`ABANDON` 终止此主线。R1B 才能最终冻结问题、claims、truth/output contract、cut list 和 venue family。最近邻学术 baseline 是 novelty evidence 的必要部分；内部消融不是 prior-art superiority 或 novelty proof。同一 trace 的重复处理只能测性能方差，不能代替独立 case；四个 fault family 必须构造成多 case、带 holdout 的 benchmark。

## 3. Frozen Project Baseline

P7.8 记录完整回归 `843 passed`、`1195 subtests`、`2038` JUnit cases、零 error/failure/skip；这是冻结记录，非本轮重跑。Phase 7 支持冻结 artifact 的清单、校验、合同范围 reopen/replay/validate，P7.6 的有限 host observation 与 P7.7 capability/comparability inventory（量化 baseline 仍为 `not_evaluated`）。

不支持 proof correctness/parity、diagnosis/root-cause accuracy、通用 RTOS、真实硬件采集影响、外部工具排名、SOTA、可用性提升或录用。P6 synthetic/reference-only 不能转为独立 truth。保护范围是所有 P6/P7 fixture、artifact、schema mirror、manifest、report、hash 和 closeout。R0 仅规划如何处理 P7.8 的漏 `--output`、focused-test 数量不一致、文档 HEAD 滞后三项 provenance 问题，绝不改写历史。

## 4. Skill Acquisition and Audit

上一轮已在仓库外下载并审计：`paper-novelty-design-v1` 来自 `LaVineLeo/Paper-novelty-design` @ `9434234aa44d303102a6619cbb91e7ab7a92869a`；`novelty-check`、`experiment-plan`、`research-refine-pipeline` 来自 `wanshuiyin/Auto-claude-code-research-in-sleep` @ `c5f3d5bfc694a812012729841e9697223e4f2130`，均为 MIT，并安装在 `/home/zzq/.codex/skills/` 下的同名目录（另有 `research-refine`、`shared-references` 依赖）。本轮不调用会写入项目、联网查新或执行实验的第三方流程。

审计已记录：ARIS workflows 可使用 Bash 和写项目；`verify_papers.py` 可读取可选 `ARIS_VERIFY_EMAIL`、访问 arXiv/Crossref/Semantic Scholar 并写缓存/输出。因此未来查新必须显式授权、使用审计网络策略及仓库外输出或 `--no-cache`。本规划吸收 Problem--Method--Insight、可证伪、claim-to-evidence 和最小实验原则；它们不是同行评审结论。

## 5. Research Problem Reframing

| Layer | Planning statement |
| --- | --- |
| Problem | 大型 RTOS trace 的 full scan/export 成本高，固定窗口可能遗漏远距离依赖；当前工程未证明选择性 package 在声明 truth boundary 内保留诊断证据，也未证明其能可信拒绝错配输入。 |
| Method | 对预注册 seed、规则族、identity 和多维 budget 使用稳定排序、budget-before-read、frontier freeze、sidecar/index 局部选择和 identity/checksum/closure validation。 |
| Insight | 仅当依赖图足以覆盖声明 boundary 时，选择性导出才可成为带显式缺口/拒绝状态的可审计证据合同，而非启发式裁剪。 |

一句话：在独立 truth boundary 下检验有界 evidence closure 是否以更低净成本保留 RTOS diagnosis 所需证据。错误定位：不是 LLM/Agent 论文、proof correctness、通用 RTOS replay、普通 index 加速、仅 shrink ratio 或“第一个”。

## 6. Engineering Asset vs Research Contribution Matrix

| Existing asset | Engineering value | Candidate scientific value | Missing evidence | Paper role |
| --- | --- | --- | --- | --- |
| evidence closure/frontier/budget-before-read | 选择与显式缺口 | C1 mechanism | independent truth、prior-art delta | primary candidate |
| sidecar/workset | dependency lookup | C1 isolation | recall/cost | ablation |
| sidecar index/ticket | validated indexed access | C3 conditional | total-cost break-even | optional support |
| proof digest/canonical manifest | binds declared fields | provenance audit | semantic/proof validity | infrastructure only |
| package reopen/identity/checksum | contract integrity | C2 mechanism | mutation corpus | supporting candidate |
| deterministic replay | repeated frozen output | repeat stability | diagnosis/external validity | controlled baseline |
| degraded/fail-closed | non-success state | C2 safety boundary | false accept/reject | ablation |
| advisor/agent | read-only overlay | none currently | disabled/heuristic/LLM safety evidence | appendix only |

## 7. Candidate Claims and Falsifiability

R1A registers the following **candidate** claims only; R1B may retain, shrink or delete them after R2.

| Claim | Required experimental unit and coverage | Required comparator/evidence | Refuting result | Status |
| --- | --- | --- | --- | --- |
| C1 candidate primary: bounded closure lowers net package/read/I/O/resource cost while preserving declared evidence and diagnosis result | diagnosis case and capture run for preservation; processing run for cost; every approved family has a holdout | B0/B1, independent labels, holdout; B2 according to the decision table | required-evidence/label loss, no repeatable net benefit, no material R2 delta, or B2 decision requires deletion | unverified |
| C2 candidate support: binding/checksum/dependency validation fail closed on modeled invalid packages | diagnosis case/capture variant; mutation variants are attack-condition coverage, not new D cases | validated vs controlled unvalidated comparator; valid/invalid denominator and variant-generation hash ledger | any silent invalid acceptance/complete state | unverified |
| C3 conditional support: index has scale-dependent net benefit | trace x query workload x processing run; holdout scale | stream-sidecar comparator and B2 if relevant | no break-even after build/storage, recall loss, or no novelty | delete if failed |
| Anti-claim | every reporting layer | boundary audit | treating agreement/replay as diagnosis/proof correctness | mandatory |

Internal ablation evidence **does not equal** prior-art superiority evidence and neither alone proves novelty. C1/C2/C3 remain pending until R8; C3 is cut rather than padded if novelty or break-even fails.

## 8. Prior-Art Search Plan and R2 Output

Future R2 covers RTOS/embedded trace analysis; dynamic/backward/causal slicing; selective capture/export/query; reproducible debugging/replay; provenance/evidence package/dependency closure; indexing/selective I/O; integrity/identity/fail-closed validation; embedded trace tools. Sources are ACM DL, IEEE Xplore, USENIX, DBLP, Crossref, Semantic Scholar, Google Scholar, arXiv and appropriate official venue proceedings, not a default ML list.

For each candidate claim, run exact-term, synonym, mechanism, problem and citation-chain queries, then backward/forward snowballing. R2 records source URL, metadata source, abstract/method inspection and `verified`/`partially verified`/`unverified`/`excluded` status; no DOI/title/result is invented. R2 must output verified nearest prior work; differences in Problem, Representation, Mechanism, Safety and Evidence; candidate claims deleted or narrowed; material novelty delta; and `PROCEED`, `REFRAME` or `ABANDON`. `REFRAME` re-enters R1A with a new registry and re-runs R2; `ABANDON` ends this main line. It also emits candidate B2 baselines, not a preselected paper.

## 9. Novelty and Baseline Matrix Specification

| Prior work / Baseline ID | Type | Source | Problem | Representation | Mechanism | Safety | Evidence | Faithfulness | Same input/truth/output | Quantitative eligibility | Key delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0-full / B0-clip | behavioral | project protocol |  |  |  |  |  | exact | required | yes | basic contrast |
| B1-* | internal ablation | project protocol |  |  |  |  |  | exact | required | yes | mechanism isolation |
| B2-TBD | closest prior work | only R2-verified source | R2 only | R2 only | R2 only | R2 only | R2 only | pending | pending | pending | R2 only |

B2 may enter a quantitative comparison only when public runnable code/license, faithful reproduction, audited mechanism-equivalent implementation, or a faithful same-contract adapter exists. A faithful adapter requires documented mechanism/parameter/output mapping, behavior validation against an author artifact or public example when available, bounded and disclosed deviations, independent review, and failure of any required condition means capability-only. Record paper/code/version/license, deviations, unsupported features, parameters, environment, failed runs and differences from original work. Never delete an unreproducible B2 or substitute an obviously weaker self-made baseline. External viewers remain capability-only unless the same-contract gate passes.

| B2 decision | Quantitative use | C1 / superiority boundary | R1B, R5--R9 consequence |
| --- | --- | --- | --- |
| `quantitative eligible` | pre-registered B2 comparison under same contract | may report the specified comparison; still not novelty proof alone | R1B pre-registers this branch; R5 freezes version/adapter/parameters before holdout; R6/R8/R9 include it |
| `capability-only` or `not-comparable` | no ranking, accuracy or superiority result | retain B2 in novelty matrix; C1 may only be narrowed to B0/B1 specified-contract preservation/cost observation, or deleted; never present as prior-art superiority | R1B pre-registers the downgraded claim/venue branch; R5 records the trigger but cannot change the final claim; R6 excludes B2; R8/R9 disclose/defer; a different claim returns to R1A/R2 |

The C1 net-cost contract fixed in R1B includes closure/read/serialize/reopen/validate time, bytes read/package bytes, seek/RSS/temp storage, checksum/identity validation, sidecar/index build/storage and a declared cold/warm reuse-amortization model. C3 cannot silently redefine this ledger.

## 10. Formal Model Plan

Let `G=(V,E)` contain raw events, derived observations, artifacts, packages/manifests and external-reference-only nodes. Nodes carry source/identity/SHA-256, availability, corruption/staleness and vector cost. Edges are causal/dependency, contains, or identity-binds; each is required, optional or external-reference-only. `S` is immutable seeds from query/symptom/time/task/IRQ/mutex/queue/label. `B=(bytes,events,depth,window,artifacts,host-IO)` is a vector, never one conflated budget.

`C_B(S)` must freeze rule family, total order, tie break, required-dependency priority and budget-before-read; it emits canonical `exact`, `bounded`, or `degraded`, with frozen missing/truncated frontier. Required missing/mismatch is non-success. Fixed complete immutable inputs plus total order/policy/serializer can support determinism and termination; declared binding can support integrity under collision-resistance assumptions. Preservation, false accept/reject, I/O/break-even, repeat stability and hardware effects are empirical. Soundness, completeness, minimality, proof correctness and cross-RTOS behavior are not currently claimable.

Budget monotonicity is not assumed. It needs componentwise order, prefix-stable selection, budget-independent expansion and no error-dependent branch; R3 proves a restricted property, narrows it, or records it false.

## 11. Multi-Case Diagnosis Benchmark Plan

Benchmark hierarchy is **Fault Family -> Scenario Template -> Parameterized Case -> Independent Capture Run -> Trace Artifact**. A case is not a changed random seed: it has a distinct configuration, ground truth and trace family. Every approved family has at least three substantively distinct cases and at least one parameterized scale; its split must contain **at least one development case, one validation case and one holdout case**. Capture or processing repeats never substitute for those D-level cases. If a pilot needs a further validation case, the family case count increases; a development or holdout case is not relabeled or reused.

| Family | Independent observer / expected evidence | Substantive variation examples |
| --- | --- | --- |
| task starvation | scheduler counter/GPIO/watchdog, runnable-not-scheduled interval | task count, priority hierarchy, load, duration, IRQ load, scheduling policy |
| priority inversion | holder/waiter interval, declared inheritance configuration | task count/gap, mutexes, critical section, inheritance, nested locking, load |
| IRQ latency | timer/GPIO stimulus-to-ISR timestamp | frequency/burst, masking, critical section, nesting, load, threshold |
| mutex contention or queue overflow | block interval or watermark/drop counter | rates, depth, hold time, contenders, drop/timeout/priority policy |

Every case freezes source/firmware/ELF hash, compiler flags, injection parameter, initial state, workload, independent-label author/version and creation timestamp, label source type and injection binding, labeler-versus-analyzer role separation/blinding, required evidence, raw checksum, provenance/license, ambiguity/adjudication policy and exclusion rule. The analyzer output cannot be ground truth. Store valid/corrupt/missing/stale variants separately, retaining each mutation generation hash and valid/invalid denominator.

R4A freezes split assignment and hashes before tuning: development is for rule/sidecar/index/metric/adapter debugging; validation is for pilots, predeclared selection, budget and preliminary break-even; holdout is for final preservation, recall, false accept/reject and primary tables. Baseline source/version/license, adapter mapping, preprocessing, fixed-window policy, closure seeds/budgets, B2 parameters and failure policy are developed on development, use validation only for predeclared selection, then version/hash-frozen before any holdout trace or label is exposed. A one-shot, read-only holdout runner records first access, command/version/hash and access log. A post-holdout change is a registered protocol failure requiring a new holdout; ground truth and failed/negative cases are never edited/deleted because of holdout performance.

Future reporting gives processing-run, capture-run, per-case, per-family, macro average and, only when useful, micro average; it includes worst family/case, failures, ambiguity, drops and exclusions with reasons.

## 12. Data Source Strategy

**Real hardware:** no audited board currently exists. R7 must inventory then freeze board revision, RTOS/version, firmware/ELF hash, compiler flags, clock/timer/IRQ setup, recorder/transport, trace-off/on workload, raw checksum, dropped events, license/provenance/privacy. Missing metadata blocks “real hardware”. **Second source:** prefer a licensed public RTOS trace with adequate labels; otherwise second RTOS, QEMU, simulator, host RTOS or external fixture must be labeled `quasi-real`, `simulator` or `externally generated`, never hardware. A source is not diagnosis truth without independent labels.

## 13. Fair Baseline Plan

Baseline layers are:

| Layer | Baselines | Purpose |
| --- | --- | --- |
| B0 basic behavior | full raw-trace scan; fixed-window clipping pre-registered before results | full-information and naive selection controls |
| B1 internal mechanisms | closure without sidecar/index; sidecar closure; sidecar+index; controlled degraded/fail-open negative comparator | isolate closure/sidecar/index/safety |
| B2 closest prior work | one or more R2-verified trace slicing/dynamic/causal/dependency/provenance/selective/indexed methods | academic nearest-neighbor comparison |

All quantitative baselines require same raw SHA-256, case/workload, preprocessing, truth boundary, output contract, environment identity, metrics, split and failure policy. Fail-open is controlled negative-only. Trace Compass/Perfetto/vendor tools are capability matrix entries unless they meet the same-contract test. No internal B1 win may be written as superiority over prior work.

## 14. Ablation Plan

| Ablation | Claim | Required result |
| --- | --- | --- |
| B0/B1 and quantitative-eligible B2 under frozen contract | C1 | per-case/family preservation and total cost |
| separate bytes/events/depth/window/artifact budgets; ordering/depth/overbuilt variant | C1/formal | no implied monotonicity/minimality |
| no identity/checksum/closure validation; controlled fail-open/degraded | C2 | false accept/reject and reason codes |
| stale sidecar, missing calibration, corruption, mismatch | C2 | non-success or explicitly measured failure |
| cold/warm index with build/storage/reuse count | C3 | stated break-even or C3 cut |

## 15. Metric Contract and Experimental Units

Three statistical levels must never be conflated.

| Metric | Experimental unit | Repetition level | Independence assumption | Aggregation |
| --- | --- | --- | --- | --- |
| elapsed, bytes read, seek, RSS, package size | processing run of a frozen trace | repeated processing runs, cold/warm controlled | runs share trace; only processing variation | within trace/case, then case summary |
| index break-even | trace x query workload x processing run | repeated processing runs | query/run clustering retained | trace/query summary then case/family |
| acquisition CPU/latency/buffer/drop | capture run | independent reset/workload/injection/capture | distinct capture identity | case then family |
| diagnosis agreement, evidence/dependency recall | diagnosis case or capture run as claim declares | multiple cases/captures | distinct configuration/ground truth; captures nested in case | per case, family macro |
| false accept/reject | case x mutation/capture variant | valid and invalid attack-condition variants | variants cluster within case/capture, not new diagnosis cases | per case/family and attack class |

Level P is a processing run on one frozen trace and supports timing/I/O/RSS only. Level C is a fresh firmware/workload/fault/capture run with new capture identity/checksum and supports capture variation/overhead/preservation. Level D is a distinct diagnosis case with configuration, ground truth and trace family and supports case/family evidence. Prohibited: treating 30 replays as 30 diagnosis cases; pooling repeated captures of one configuration as generalization; mixing P/C/D in one significance test; masking low D with many P; pooled-only reporting.

Raw future results must contain `fault_family_id`, `case_id`, `capture_run_id`, `trace_id`, `processing_run_id`, `baseline_id`, `seed`, `environment_id`, `cold_warm_state`, `result_status`, `method_version`, `parameter_contract_hash`, `split_access_log`, `holdout_first_access`, `post_holdout_change`, `mutation_attack_class`, and `variant_generation_hash`. Choose paired, case-blocked, hierarchical bootstrap, mixed-effects, cluster-robust interval, or per-case-effect/family aggregation based on the final nesting; the final model must represent trace, capture, case and family grouping.

## 16. Statistical Protocol

Pilots estimate feasibility and variance only; they do not support the primary claim and use validation cases, never the final holdout. Sample planning is separate: processing-run count for P-level stability; capture-run count for C-level variation/acquisition; independent case count for D-level preservation/generalization. Formal `n` is determined from primary endpoint, independent unit, pilot variance and power; no universal “20--30” target exists, and extra P repeats cannot replace D cases.

Fix/record seeds; block by case/trace; counterbalance mode and trace-off/on order; record clock/resolution/environment/storage/cache state without claiming cache reset. Retain raw/failed runs; predefine technical invalidity; never silently exclude. Report per-unit distributions, median/IQR or MAD/min/max for P, paired effects where paired, 95% interval appropriate to clustering, family macro and case-level effects; Holm correction or an explicit pre-registered primary endpoint. Preserve raw results, scripts, environments and negatives.

## 17. Pilot Experiments

| Pilot | Validation-only scope | Success gate | Non-success decision |
| --- | --- | --- | --- |
| P1 preservation | one validation starvation/inversion case; full vs closure; independent label/evidence list; P repeats measure only processing variance | all declared required dependencies and outcome preserved over approved validation captures, plus signal of cost reduction | one contract redesign; persistent loss removes diagnosis-preservation C1 |
| P2 index | validation trace/query workload; stream sidecar vs validated index, cold/warm | stated reuse count has net I/O/time gain after build/storage, no recall loss | cut C3 on no break-even/RSS dominance/stale bypass |
| P3 safety | validation package plus corrupt/missing/stale variants | zero false accept in approved mutation suite and stable reason code; valid false reject measured | any silent accept stops scale-out; narrow/fix contract before retest |

Pilot success is a scale-up decision, not claim support; repeated processing of a single trace remains P-level evidence only.

## 18. Research Validation Track Phases

`R0 Phase 7 Non-Destructive Provenance Errata -> R1A Provisional Problem and Claim Registration -> R2 Verified Prior-Art Review -> R1B Final Claim and Venue Freeze -> R3 Formal Evidence Closure Model -> R4A Benchmark/Label/Split Construction and Freeze -> P1/P2/P3 -> R4B Complete Multi-Case Benchmark Expansion -> R5 Internal and Closest-Prior-Work Baselines -> R6 Ablation, Scalability and Statistical Validation -> R7 Real Hardware or Independent External Validation -> R8 Paper Evidence Freeze -> R9 Manuscript and Submission Readiness Review`.

These are future gates, not execution authorization.

## 19. Per-Phase Contract

### R0 -- Phase 7 Non-Destructive Provenance Errata
Status: planned. Objective/RQ: plan a correction ledger for the P7.8 command/count/HEAD discrepancies without altering history. Inputs/preconditions: frozen closeout/repro/manifest and clean audit baseline. Tasks: locate records, specify corrected future command/evidence wording, preserve originals. Outputs: errata plan only. allowed_new/modify: future isolated planning material only / no historical file; read-only: P6/P7; forbidden: result/log/hash/history rewrite. Data/baseline/metrics/tests/statistics: historical consistency checks only. Risk/fallback/stop: ambiguity remains report-only; stop if an edit to historical evidence is proposed. Gate/claim/acceptance/freeze: independent agreement, discrepancy-to-source map and hashes. Dependencies/effort: none; small.

### R1A -- Provisional Problem and Claim Registration
Status: planned. Objective/RQ: create falsifiable search inputs, not contributions. Inputs: R0 and this plan. Preconditions: frozen boundary understood. Tasks: register candidate problem, C1/C2/C3, anti-claim, search terms, possible refuters, preliminary truth boundary and cut list. Outputs: provisional registry. allowed_new/modify: isolated research registry only; read-only: frozen project; forbidden: `final claim`, `contribution frozen`, novelty established, thesis finalized, Agent truth role. Data/baselines/metrics: declared only. Risk/fallback/stop: vague/unfalsifiable candidate -> narrow/cut. Gate: enough specificity for R2. Claim boundary: no novelty/correctness. Acceptance/freeze: registry complete, explicitly provisional. Dependencies/effort: R0; small.

### R2 -- Verified Prior-Art Review
Status: planned. Objective/RQ: determine whether material delta survives. Inputs/preconditions: R1A registry, verified search protocol, citation log. Tasks: multi-source/chained review, method confirmation, novelty matrix, nearest work, B2 candidate and `PROCEED`/`REFRAME`/`ABANDON`. Outputs: verified corpus/matrix; Problem/Representation/Mechanism/Safety/Evidence deltas; deleted/narrowed claims; baseline feasibility record. allowed_new/modify: isolated bibliography/matrix only; read-only: project; forbidden: fabricated citations or final claim without evidence. Data/baselines: prior work/B2 candidates. Metrics/tests/statistics: source/metadata verification. Risk/fallback/stop: `REFRAME` returns to R1A then a fresh R2; no material delta -> abandon. Gate: only `PROCEED` for the current registry advances. Claim boundary: no “first”. Acceptance/freeze: reviewer-readable matrix. Dependencies/effort: R1A/external access; 1--2 person-weeks conditional.

### R1B -- Final Claim and Venue Freeze
Status: planned. Objective/RQ: freeze a paper that the current R2 `PROCEED` permits. Inputs/preconditions: R2 `PROCEED` for the same R1A registry; `REFRAME` must not enter R1B. Tasks: freeze final problem, maximum one primary/two support claims, anti-claim, truth/output contract, success/refutation criteria, total net-cost ledger, cut list, target venue family/main/backup/downgraded target, and both pre-registered B2 eligibility branches. Outputs: final claim/venue contract. allowed_new/modify: isolated contract only; read-only: R2/frozen assets; forbidden: C3 without novelty/break-even, unfalsifiable claim, Agent main contribution. Data/baselines: B0/B1 and B2 decision branches recorded. Metrics/tests/statistics: endpoint/unit/split/holdout-access contract declared. Risk/fallback/stop: R5 may trigger only a pre-registered B2 branch; a new claim returns to R1A/R2. Gate: final falsifiable scope. Claim boundary: no supported result yet. Acceptance/freeze: independent claim-to-evidence/B2 audit. Dependencies/effort: current R2 `PROCEED`; small.

### R3 -- Formal Evidence Closure Model
Status: planned. Objective/RQ: specify `G,S,B,C_B`, state semantics and valid properties. Inputs/preconditions: R1B final contract. Tasks: total order, counterexamples, proof/property-test obligations, monotonicity audit. Outputs: formal specification, no production code. allowed_new/modify: isolated design only / future isolated model after authorization; read-only: existing closure; forbidden: retrofit claims. Data/baselines: toy counterexamples. Metrics/tests/statistics: determinism/termination conditions. Risk/fallback/stop: property prerequisites fail -> narrow/remove. Gate/claim/acceptance: every theorem condition explicit; no soundness/minimality/completeness. Dependencies/effort: R1B; 1--3 weeks conditional.

### P1/P2/P3 -- Validation Pilots
Status: planned. Objective/RQ: decide whether to scale C1/C2/C3. Inputs/preconditions: R3 and R4A-frozen validation cases/labels/splits. Tasks/outputs: Section 17 pilots and gate reports. allowed_new/modify: future isolated pilot materials only; read-only: P6/P7; forbidden: holdout access, claim support language, formal benchmark substitution. Data/baselines: R4A validation cases/B0/B1 only, B2 if ready. Metrics/statistics: P/C levels declared, no D generalization. Risk/fallback/stop: Section 17. Acceptance/freeze: pass/fail preserves all outputs. Dependencies/effort: R3/R4A; small conditional.

### R4A -- Benchmark, Label and Split Construction and Freeze
Status: planned. Objective/RQ: create the lawful validation source before any pilot. Inputs/preconditions: R1B/R3 and label/license governance. Tasks: create all four families with at least three substantial configuration-distinct cases each; assign each family one development, one validation and one holdout case; bind independent observer/label roles; freeze case/capture IDs, split assignment/hashes, valid/negative/mutation variants and raw checksum rules. Outputs: isolated R4A manifests, independent ground-truth ledger and split/access protocol. allowed_new/modify: future R-track benchmark only / new material after authorization; read-only: P6/P7; forbidden: P6 relabeling, analyzer-derived labels, holdout trace/label exposure to pilots. Data/baselines: Section 11 hierarchy. Metrics/tests/statistics: label timestamp/source/injection/blinding/adjudication audit, split leakage, capture integrity, P/C/D IDs. Risk/fallback/stop: non-independent truth or a family without all three split roles -> lower scope/no correctness claim. Gate: pilot validation cases exist without holdout access. Claim boundary: no pilot result yet. Acceptance/freeze: `family_id -> {development, validation, holdout}` nonempty, configurations non-overlapping, IDs/splits/hashes frozen and negatives retained. Dependencies/effort: R3; 2--5 person-weeks conditional.

### R4B -- Complete Multi-Case Benchmark Expansion
Status: planned. Objective/RQ: expand and stabilize the benchmark after the pilots without disturbing R4A split governance. Inputs/preconditions: R4A and P1/P2/P3 decision. Tasks: add approved capture repetitions, scale instances and any additional cases with declared split before use; preserve initial holdout; complete provenance/license/raw traces. Outputs: full benchmark release candidate. allowed_new/modify: future isolated R-track benchmark only; read-only: R4A/P6/P7; forbidden: reassigning an exposed case to holdout, deleting negative cases, retuning on holdout, or using additions to replace/dilute any R4A family holdout. Data/baselines: R4A plus declared additions. Metrics/tests/statistics: family coverage, capture variation and provenance audit. Risk/fallback/stop: required family/split coverage lost -> do not enter R5/R6. Gate: complete case/capture inventory. Claim boundary: no broad generalization. Acceptance/freeze: all additions have pre-exposure split, hashes and independent labels; additions contributing to C1 family macro meet the same substantive D-level distinction and all-family split-coverage audit. Dependencies/effort: R4A/pilots; 1--4 person-weeks conditional.

### R5 -- Internal and Closest-Prior-Work Baselines
Status: planned. Objective/RQ: construct fair B0/B1/B2 comparison without holdout tuning. Inputs/preconditions: R2 B2 selection, R1B output contract, R4B benchmark. Tasks: adapters, B2 reproduction/licensing/deviation log, metric collector, capability matrix; develop on development only and select only predeclared options on validation. Outputs: baseline registry/runners plus versioned parameter contract. allowed_new/modify: future isolated tooling only; read-only: frozen assets; forbidden: P7 edits, weak substitute/deletion of unreproducible B2, any holdout trace/label access before version/parameter/failure-policy hash freeze. Data/baselines: B0/B1/B2, development/validation only until freeze. Metrics/tests/statistics: same-input/truth/output/split/failure gate and faithful-adapter audit. Risk/fallback/stop: B2 irreproducible -> capability decision and R1B claim/venue downgrade; baseline leakage -> new holdout. Gate: quantitative eligibility per row and first-access log setup. Claim boundary: B1 win is internal only. Acceptance/freeze: source/version/license, adapter mapping, preprocessing, windows/seeds/budgets, B2 parameters and failure policy hash-frozen before holdout. Dependencies/effort: R2/R1B/R4B; 2--6 weeks conditional.

### R6 -- Ablation, Scalability and Statistical Validation
Status: planned. Objective/RQ: test surviving claims with correct experimental units and one-shot holdout access. Inputs/preconditions: R4B/R5 hashes and B2 decision. Tasks: ablation, scale/budget/depth/order, mutation, P/C/D-aware analysis, one-shot holdout tables. Outputs: raw results, statistical analysis, negative ledger. allowed_new/modify: isolated research results/runners only; read-only: frozen assets; forbidden: pseudoreplication, pooled-only reporting, selective deletion, parameter change/rerun after holdout except registered protocol failure plus new holdout. Data/baselines: frozen split, B0/B1 and B2 only if quantitative eligible. Metrics/tests/statistics: Sections 14--16, per-case/family macro and attack-class denominators. Risk/fallback/stop: C1/C2/C3 refuter or B2 limitation -> cut/lower claim; absent parameter/access freeze evidence rejects results. Gate: holdout, independence and baseline-access audit. Claim boundary: conditions only. Acceptance/freeze: raw IDs, method/parameter hash, first-access/access log and analysis traceable. Dependencies/effort: R5; 3--8 weeks conditional.

### R7 -- Real Hardware or Independent External Validation
Status: planned. Objective/RQ: test acquisition/external validity. Inputs/preconditions: R4B/R5 stable contracts; audited board/source. Tasks: paired capture or second source, provenance/type labels, overhead and preservation. Outputs: capture ledger/hashes/external result set. allowed_new/modify: isolated future configs/data only; read-only: P7; forbidden: simulator as hardware. Data/baselines: approved R4B/R5 contract. Metrics/statistics: C/D units, acquisition metrics. Risk/fallback/stop: no board/license -> explicitly downgraded source/venue. Gate: complete source metadata. Claim boundary: one board is not general RTOS. Acceptance/freeze: provenance/license/hashes. Dependencies/effort: R4B/R5; 2--8+ weeks.

### R8 -- Paper Evidence Freeze
Status: planned. Objective/RQ: lock evidence for surviving claims. Inputs/preconditions: R2--R7 must-run results and B2 decision. Tasks: claim-to-result ledger, raw/script/environment/figure linkage, parameter/access hashes, negative retention, independent audit and B2 limitation disclosure. Outputs: immutable evidence bundle. allowed_new/modify: isolated future evidence metadata only; read-only: frozen P7; forbidden: post-hoc data/claim deletion. Metrics/statistics: reproducibility, clustered-analysis and holdout-access audit. Gate: every claim evidence-backed and no prior-art superiority wording when B2 is non-comparable. Acceptance/freeze: hashes, zero blocking/major in audit. Dependencies/effort: R6/R7; 1--3 weeks.

### R9 -- Manuscript and Submission Readiness Review
Status: planned. Objective/RQ: assess readiness, not acceptance. Inputs/preconditions: R8 and verified literature. Tasks: method/related-work/experiments/limitations/artifact appendix, simulated review, venue fit and B2 comparability disclosure. Outputs: readiness report. allowed_new/modify: future manuscript only after authorization; read-only: evidence freeze; forbidden: evidence changes/acceptance promise. Metrics/statistics: claim-to-table/unit/split/access audit. Risk/fallback/stop: missing B2 quantitative eligibility or external validity -> defer or use the R1B downgraded positioning. Gate: all must-run. Acceptance/freeze: independent review zero blocking/major. Dependencies/effort: R8; 2--6 weeks.

## 20. Dependency Graph and Critical Path

Critical path: `R0 -> R1A -> R2 -> R1B -> R3 -> R4A -> P1/P2/P3 -> R4B -> R5 -> R6 -> R7 -> R8 -> R9`; an R2 `REFRAME` loops to `R1A -> R2`, while `ABANDON` ends the line. R2 metadata organization may overlap late R0, but R1B cannot begin before `PROCEED` for the current registry. R7 hardware inventory may start early, while formal capture waits for R4B/R5 contracts. B2 license/code investigation may begin late R2. R5 runner skeleton may be prepared after R1B but may access only development/validation until its parameter/access contract freezes; B2 quantitative comparison waits for R5 eligibility.

## 21. Must-Run vs Nice-to-Have

**Must-Run:** R0 errata plan; R1A/R2/R1B; verified nearest B2 with either quantitative comparison **or** audited non-comparability/capability decision and consequent claim/venue downgrade; R4A/R4B multi-case benchmark (>=3 substantial cases/family, each with development/validation/holdout), split/holdout/access leakage audit; independent truth; B0/B1 and eligible B2 protocol; P/C/D unit protocol and pseudoreplication audit; C2 mutation suite with attack-class denominators; R6 holdout/family reporting; R7 or explicitly downgraded external-validity scope; R8 ledger. **Strongly Recommended:** second RTOS/source, index break-even, acquisition overhead, external capability matrix. **Nice-to-Have:** multiple boards/vendor adapters/Agent appendix. **Cut:** LLM contribution, unrelated modules, benchmark growth without a claim, proof-correctness narrative.

## 22. Evidence Ledger Design

| Claim ID | Claim | Required evidence | Experimental unit | Fault family / case / split | Capture / processing IDs | Nearest baseline | Independence / leak-control boundary | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | preservation + cost | labels, B0/B1/B2 decision, raw/statistics/holdout | D/C for preservation; P for cost | every family / holdout | required | B2-TBD from R2 | nested family->case->capture->trace->processing; `method_version`, `parameter_contract_hash`, split access/first-access/post-change log | pending |
| C2 | fail-closed | mutation/reason codes/valid controls/denominators | D/C mutation attack condition | every family / holdout variant | required | B1 controlled comparator | variant clustered by case/capture; `mutation_attack_class`, `variant_generation_hash` | pending |
| C3 | index break-even | build/storage/cold/warm/reuse | trace x query x P | approved scale / holdout | processing required | stream-sidecar, B2 if relevant | query/run nested in trace/case; frozen reuse-amortization model | conditional |

Every future entry links raw result, script, environment, figure/table, analysis, limitation and result status. No claim without a ledger row; no result without an artifact.

## 23. CCF-B Readiness Rubric

| Level | Hard condition |
| --- | --- |
| L0 Engineering-only | frozen implementation/repro records only |
| L1 Research question registered | R1A candidate and anti-claim |
| L2 Novelty preliminarily verified | R2 matrix and decision |
| L3 Final claims frozen and testable | R1B + formal/statistical/split contract |
| L4 Core multi-case evidence available | each family has development/validation/holdout evidence, independence/access audit and B0/B1/B2 decision |
| L5 External validity available | hardware or clearly labeled independent source |
| L6 Manuscript-ready | R8 ledger, limitations, reproducibility/review audit |
| L7 Submission-ready | all must-run and venue-fit gates |

`Submission-ready` never means `will be accepted`.

## 24. Claim Boundary

Only after relevant evidence: report preservation/cost for specified contract/case/scale; modeled mutation fail-closed behavior; stated index break-even; named capture provenance. With B2 `capability-only`/`not-comparable`, C1 is only a B0/B1 contract-bound observation or is deleted, and no prior-art ranking/superiority is permitted. Report-only: package size, host timing, replay state, capabilities, one-board observation, Agent explanation and synthetic fixture behavior. Cannot claim proof correctness, general RTOS, SOTA, diagnosis correctness without independent labels, hardware without capture, superiority without fair B2 comparison, Agent factual improvement or CCF-B acceptance. Internal ablation, prior-art superiority and novelty proof remain distinct.

## 25. Risk Register

| Risk | Likelihood / impact | Detection | Mitigation / fallback | Stop condition |
| --- | --- | --- | --- | --- |
| final claim frozen before prior art | medium/high | R1A/R2 audit | R1A provisional; only current-candidate R2 `PROCEED` enters R1B | R1B before PROCEED |
| prior art covers core mechanism | medium/high | R2 matrix | reframe/abandon | no material delta |
| only internal baseline | high/high | B2 registry | reproduce/capability downgrade | superiority claim without B2 |
| B2 reproduction fails | medium/high | license/deviation/contract record | retain capability matrix, apply decision table and lower claim/venue | hidden omission/substitution |
| closure loses evidence/result | medium/high | P1/R6 case results | redesign then cut C1 | persistent loss |
| index no break-even | high/medium | total-cost scaling | cut C3 | no stated threshold |
| pseudoreplication | medium/high | P/C/D raw-ID audit | clustered analysis, more cases | P repeats counted as D |
| insufficient cases/family or split coverage | medium/high | R4A/R4B freeze audit | lower scope, add cases | <3 cases or missing development/validation/holdout in any family |
| holdout leakage/overfitting | medium/high | split/change/first-access log | new holdout, preserve history | unlogged holdout access or post-access tuning |
| micro average hides failures | medium/medium | reporting audit | per-case/family/worst-case mandatory | macro/case output absent |
| non-independent truth, drops/license/hardware gap | medium/high | provenance/capture audit | redesign/exclude/downgrade source | analyzer-derived labels or unresolved provenance |
| scope/historical mutation | medium/high | allowlist/diff | isolate track | frozen path touched |

## 26. Resource and Feasibility Plan

Minimum publishable scope is one controlled RTOS platform or licensed source with independent labels, four families times at least three substantive cases **per family** with development/validation/holdout coverage, B0/B1/B2 decision, C2 mutations and conditional boundaries. Ideal scope adds a real board and second source, capture impact and adequate C/D variation. CPU/fast SSD are central; GPU is not required. Storage may be tens/hundreds GB for MVP and TB scale for full data. Literature/contract is roughly 1--2 person-weeks; R4A--R6 3--8 conditional weeks; hardware/debug 2--8+ conditional weeks and is the likely blocker. These are ranges, not commitments.

## 27. Final Decision Gates

**Gate A Provisional Claim:** R1A candidate is falsifiable enough for search. **Gate B Novelty:** R2 finds material delta; `REFRAME` loops to R1A/R2 and `ABANDON` stops. **Gate C Final Claim Freeze:** only current-candidate `PROCEED` allows R1B final claim/contract/cut list/venue family. **Gate D Benchmark-Ready Pilot:** R4A independent validation labels/splits exist, then P1--P3 justify scale-up only. **Gate E Benchmark Independence:** every family has substantive development/validation/holdout cases, independent labels and P/C/D/access boundaries. **Gate F Baseline:** B2 is quantitative eligible or audited non-comparability explicitly downgrades claim/venue. **Gate G External Validity:** hardware/second source completes or scope is lowered. **Gate H Evidence:** every claim has full ledger, parameter/access hashes and no prohibited B2 wording. **Gate I Submission:** every must-run plus R8/R9 audit passes.

## 28. Completion Audit

| ID | Severity | Problem | Required fix | Resolution |
| --- | --- | --- | --- | --- |
| A-01 | resolved | final claim previously preceded verified prior art | R1A/R2/R1B ordering and all dependent references | Reviewer A: closed |
| A-02 | resolved | baselines were internal only | B0/B1/B2 decision table and R2/R5/R6 gate | Reviewer A: closed |
| A-03 | resolved | statistical unit/pseudoreplication undefined | P/C/D protocol, raw IDs, analysis constraints | Reviewer B: closed |
| A-04 | resolved | four families lacked multi-case/holdout contract | R4A/R4B, >=3 cases/family, split/freeze/reporting | Reviewer B: closed |
| A-05 | minor | Skill immediate registration not confirmed | refresh before invocation | accepted risk |
| A-06 | minor | P7 provenance discrepancies historical | R0 only | accepted risk |
| A-07 | review | research logic review | R1A/R2/R1B and B2 branch audit | Reviewer A: blocking=0, major=0, minor=0, approved |
| A-08 | review | empirical/statistical review | R4A/R4B, P/C/D and holdout audit | Reviewer B: blocking=0, major=0, minor=0, conditionally approved |

Independent review now reports `blocking=0`, `major=0`, `minor=0`; accepted risks are A-05/A-06. The status is therefore **Planning Baseline Conditionally Approved**. It is not `Planning Baseline Complete` and does not authorize R0--R9 execution; a future independent approval is required for any status upgrade.
