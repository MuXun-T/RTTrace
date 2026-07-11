# Phase 7 P7.1 External Validation Contract Freeze

状态：Contract Freeze Candidate

子阶段：P7.1 External Validation Contract Freeze

实现状态：Not Started

Schema 实现：Not Started

Replay 实现：Not Started

数据采集：Not Started

测试实现：Not Started

自动提交：Disabled

## 1. 文档目的

本文件冻结 Phase 7 后续工作必须遵守的文档级 package、replay state、comparison、
identity、closure、mutation、error taxonomy 与 claim-boundary 合同。它是后续 schema
和实现的输入，不实现 package reopen、deterministic replay、schema、fixture、test、
数据采集或性能实验。

文档完成仅表示合同可接受内部审阅；不表示已有 external evidence package、已实现
reopen/replay、获得 `replay_pass`、真实硬件数据、proof parity 或论文外部实验。

## 2. 继承基线

| 项目 | 冻结事实 |
| --- | --- |
| 仓库 | `/media/zzq/新加卷/patent/realization` |
| 当前分支与 HEAD | `main`；`e8a52a00a2f00998cc3a2f3c30a6cf23746b028c` |
| P7.0 独立提交 | `e8a52a0 docs: add phase7 external validation preapproval` |
| Phase 6 最终提交 | `d3bcee0e40dbe7da933b067f1a6affb740fef26f` |
| P6.4 canonical SHA-256 | `fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e` |
| P6 replay baseline | `cases_total=8`，`replay_pass_count=0`，`replay_fail_count=0`，`reference_only_count=8`，`not_evaluated_count=0`，`proof_drift_count=0`，`all_replay_passed=false` |

`d3bcee0` 是当前 HEAD 的祖先。P6.4 hash、P6.3 状态和 Phase 6 regression baseline
只读；P7.1 不重跑或回写它们。未解决缺口仍包括 full evidence-package replay、
proof parity、raw-trace reconstruction、真实 RTOS/hardware/workload、性能、baseline、
经验正确性和真实参与者研究。当前日期/时间、绝对路径、随机 UUID 或环境噪声不得成为
未来 canonical artifact identity 输入。

## 3. 规范性术语

| 术语 | 定义 |
| --- | --- |
| External evidence package | 项目外部采集、生成、导出或提供，准备进入 Phase 7 验证流程的合同化证据包。 |
| Source trace | package 引用或包含的原始 trace 或 trace segment。 |
| Artifact | package 中具有独立 identity、checksum、type 和用途的文件或数据对象。 |
| Package manifest | 确定性声明 package 版本、artifact inventory、identity、checksum、provenance 与 replay contract 的文档。 |
| Replay | 按冻结合同重新打开 package，执行规定 deterministic pipeline stages 和 comparison rules。 |
| Comparison profile | 版本化比较合同，规定字段、等价关系、规范化、排序和禁止的容差。 |
| Evidence closure | 为 replay/验证结论所需的证据与依赖集合的完整性。 |
| Artifact identity | artifact 与 manifest 所声明身份是否一致。 |
| Source mutation | source trace、source metadata 或必要 artifact 的未授权变化。 |
| Proof drift | 在明确 comparison scope 内的 proof 相关字段或 hash-input 差异。 |
| Proof parity | 两个对象满足独立冻结的 proof-equivalence 合同。 |
| Proof correctness | proof 真实且正确地证明目标性质。 |

`proof drift = 0` 不自动等于 proof parity，更不自动等于 proof correctness。
LLM、advisor 和 feedback 均不参与 replay state、comparison truth、identity、closure
或 correctness 计算。

## 4. 合同分层原则

验证按以下独立层报告，前一层成立不证明后一层：

1. Package existence；2. Package readability；3. Package schema validity；
4. Package completeness；5. Checksum integrity；6. Artifact identity；
7. Provenance validity；8. Evidence closure；9. Pipeline executability；
10. Deterministic output availability；11. Semantic comparison；12. Replay result；
13. Proof drift；14. Proof parity；15. Proof correctness。

报告必须同时保存唯一 primary state 和零个或多个 reason codes，不能把层级失败
隐藏到单一状态。P7.1 只定义 proof drift measurement boundary；proof parity 与
proof correctness 需要独立批准和证据，不能由 replay result 推出。

## 5. Package 类型

| package type | 合同定义 |
| --- | --- |
| `self_contained` | 内含当前 replay profile 的全部 required artifacts。 |
| `referenced_external` | 部分 required artifacts 通过稳定 external reference 提供。 |
| `metadata_only` | 仅含 metadata/reference，不能执行完整 replay。 |
| `hybrid` | 部分 artifacts 内嵌，部分通过 external reference。 |

类型本身不决定 replay state。`metadata_only` 通常只能为 `reference_only`，除非
未来存在明确不需 trace 内容的 replay profile。外部引用失效必须产生 reason code，
不得静默忽略。

## 6. Artifact 字段分类

| 分类 | 规则 |
| --- | --- |
| `required` | 缺失即不满足完整 replay contract。 |
| `optional` | 缺失不影响当前 replay profile。 |
| `conditional_required` | 在指定 package type、platform、RTOS、profile 或 mode 下必须存在。 |
| `external_reference_only` | 仅保存稳定引用和 identity，不直接内嵌内容。 |
| `forbidden` | 任何 package 不得包含。 |

禁止至少包括 API key、access token、password、secret、未经批准的 PII、raw human
feedback、demographic data、writable proof path、executable shell command、tool
invocation request、LLM correctness self-rating、作为 identity 的 absolute local
machine path，以及未经授权再分发的真实 trace 内容。

## 7. Package Manifest 字段草案

这是未来 manifest 的字段计划，不是 JSON Schema。

| 组 | 字段 |
| --- | --- |
| Contract metadata | `contract_name`、`contract_version`、`manifest_version`、`package_kind`、`replay_profile_id`、`comparison_profile_id` |
| Source identity | `source_kind`、`source_trace_id`、`source_trace_checksum`、`source_trace_bytes`、`source_format`、`source_format_version` |
| Platform provenance | `board_family`、`board_revision`、`architecture`、`rtos_name`、`rtos_version`、`bsp_identity`、`firmware_image_hash`、`build_configuration_identity`、`recorder_tool_identity`、`recorder_configuration_identity`、`transport_identity`、`clock_configuration` |
| Artifact inventory | 每项含 `artifact_id`、`artifact_kind`、`relative_path` 或含 `reference_type`、`stable_identifier`、`expected_checksum`、`license`、`availability_expectation` 的 `external_reference`、`checksum`、`bytes`、required status、`content_version`、`media_type`、`provenance_reference`、`license_classification`、`privacy_classification` |
| Replay contract | `required_stages`、`required_artifacts`、`expected_output_references`、`comparison_profile`、`invariants`、`mutation_policy`、`unsupported_conditions` |
| Reproducibility metadata | `tool_version`、`schema_version`、`deterministic_configuration`、`environment_class`、`external_dependencies`、`platform_constraints` |

字段 requiredness 由 package type 和 replay profile 决定；不得假设所有 external data
都有真实硬件 provenance。未来 identity hash input 只能由这些稳定冻结字段构成；
hash builder 留待实现审批，且不修改现有 proof hash/digest。

## 8. 路径和引用规则

内部 artifact 必须使用 normalized package-relative path：禁止 absolute path、`..`
escape、符号链接 escape 和依平台变化的 identity。实现必须 deterministic normalize
path，Windows/Linux 表现不得改变 artifact identity。外部引用必须包含 reference type、
stable identifier、expected checksum、license 和 availability expectation；不可用、
license denied 或 checksum mismatch 必须显式报告。

## 9. Identity 合同

| identity | 冻结对象 |
| --- | --- |
| Package identity | manifest 与其冻结 identity fields 所标识的 package。 |
| Source identity | 原始 trace 或原始数据源。 |
| Artifact identity | 单个 artifact 的内容及其声明标识。 |
| Configuration identity | deterministic replay configuration。 |
| Comparison profile identity | 输出比较规则。 |

未来 identity 的冻结输入集合为：package identity 包含 contract metadata、source
identity、当前 replay profile 标记 required 的 Platform provenance fields、按
`artifact_id` 稳定排序的完整 artifact inventory、replay contract、reproducibility
metadata 和 package-root control files；source identity 包含 `source_kind`、`source_trace_id`、
`source_trace_checksum`、`source_trace_bytes`、`source_format` 与 `source_format_version`；
artifact identity 包含其 `artifact_id`、kind、relative path 或 external reference、raw-byte
checksum、bytes、content version 与唯一的 `provenance_reference` canonical identity；configuration identity
包含 profile 所需的 deterministic configuration、required stages 和 invariants；comparison
profile identity 包含第 20 节的全部 profile fields。所有集合排除 self-checksum、
当前时间、随机值、absolute path、runtime temp path 与未声明环境噪声。

Platform provenance 不属于 source identity 或 configuration identity；它作为 package
identity 的 profile-required input。`provenance_reference` 是 artifact provenance 的唯一
冻结输入，不得由实现以自由文本或额外环境属性扩展。

Package identity 相同不表示 external references 仍可访问；source checksum 相同不表示
package completeness；artifact identity 相同不表示 semantic replay；不同 comparison
profile 的结果不得直接声明等价。

## 10. Checksum 和完整性合同

项目的 `spec.io.checksum_file` 和 evidence export 均使用 raw bytes 的 SHA-256，
因此 Phase 7 默认继承 SHA-256：小写 64-hex digest、输入为原始 artifact bytes。
除非 future comparison profile 明确另行定义，checksum 不允许 line-ending
normalization 或 JSON canonicalization；JSON canonicalization 仅可用于独立 canonical
report identity，不能替代 artifact byte checksum。

checksum mismatch、external-reference checksum mismatch、zero-byte required artifact、
duplicate artifact ID、duplicate relative path 都是完整性错误。manifest 可由 package
identity 覆盖；self-checksum 不得形成循环输入，未来若需校验，使用 detached checksum
或明确排除 self field 后的 canonical bytes。

## 11. Completeness 合同

| 情况 | 执行前预声明的有限检查 | 已进入正式 replay |
| --- | --- | --- |
| required/conditional required 缺失、声明文件不存在、size/checksum/identity 不符、duplicate、unsupported required type | `reference_only` 不成立；未启动 run 时仅能 `not_evaluated` | `replay_fail` |
| optional 缺失 | warning，除非 profile 升级为 conditional required | warning，除非已升级为 required |
| extra/unlisted artifact | default sealed inventory：`replay_fail` | `replay_fail` |
| 文件存在但 manifest 未声明 | default sealed inventory：`replay_fail`；不得隐式采用 | `replay_fail` |

对进入正式 replay 的 package，required artifact、identity、checksum、closure、
invariant 或 deterministic-output mismatch 不能降级为 `reference_only`。
默认 sealed inventory 适用于 formal replay 和预声明 `reference_only`；任何 optional
artifact 也必须在 inventory 中声明。目录扫描不能把未声明文件作为输入或忽略对象。
package root 仅允许：(1) invocation 指定的唯一 manifest；(2) manifest 明确声明的至多
一个 detached manifest checksum file；(3) inventory 中声明的 artifacts。manifest 的
canonical bytes 与其 relative name 进入 package identity；detached checksum 只校验该
manifest canonical bytes，且其 relative name 和 raw bytes 进入 package identity，但它
不是 artifact inventory item。其他 root 或 nested file 均是 unlisted artifact 并导致
`ERR-ARTIFACT_UNDECLARED`。

## 12. Evidence Closure 合同

closure 定义包括 closure seed、required dependencies、sidecar/index dependencies、
dictionary/calibration dependencies、expected-output dependencies、frontier、unresolved
reference、closure completeness、closure profile 和 closure version。

metadata preservation、artifact retention、dependency closure、semantic closure 和 full
evidence closure 是不同层级。`evidence_retention_ratio=1.0` 不自动等于 full evidence
closure。P7.1 不实现 closure algorithm；未来 profile 必须声明其 required closure
level、seed、frontier policy 与 unresolved-reference handling。

## 13. Replay Primary State

每个 case 只有一个互斥 primary state：`not_evaluated`、`reference_only`、
`replay_pass` 或 `replay_fail`。`invalid`、`blocked`、`unsupported` 等仅是 reason
code，不新增无限主状态。primary state 与 reason codes 分开存储，并由 deterministic
code 产生；LLM、advisor、feedback 不得影响其计算。

## 14. 状态优先级

1. 未正式启动验证，或环境、批准数据、设备、受支持 profile 尚未就绪：`not_evaluated`。
2. 执行前已经声明为有限验证，且所有允许的 metadata/reference 检查成功、没有任何
   mismatch：`reference_only`。
3. 正式 replay 已开始，任一 contract/integrity/closure/stage/invariant/output 失败：
   `replay_fail`，即使后续阶段无法继续。
4. 正式 replay 的全部 `replay_pass` 必要条件成功：`replay_pass`。

`reference_only` 不能掩盖错误，也不能在正式 replay 失败后回退使用。`not_evaluated`
不适用于 evaluation attempted but failed。

一次 deterministic validation run 从 manifest/preflight 开始即为 evaluation attempted。
除非 run 完全未启动，preflight 或任一后续 stage 检出的 contract、integrity、closure、
path、external-reference 或 output error 都是 `replay_fail`。全局尚未批准 comparison
profile 可为未启动原因；已启动 run 中 package 缺失其 required comparison profile 则为
`replay_fail`。

## 15. Reason Code Taxonomy

现有仓库使用 `ERR-<DOMAIN>_<DETAIL>`，例如 `ERR-SIDECAR_INDEX_MISSING` 和
`ERR-PACKAGE_WRITE_FAILED`；P7.1 冻结同一风格。下面是含义草案，不实现代码。

| 类别 | reason codes |
| --- | --- |
| Contract | `ERR-CONTRACT_VERSION_UNSUPPORTED`、`ERR-MANIFEST_SCHEMA_INVALID`、`ERR-COMPARISON_PROFILE_MISSING`、`ERR-REPLAY_PROFILE_UNSUPPORTED` |
| Artifact | `ERR-REQUIRED_ARTIFACT_MISSING`、`ERR-ARTIFACT_CHECKSUM_MISMATCH`、`ERR-ARTIFACT_IDENTITY_MISMATCH`、`ERR-ARTIFACT_SIZE_MISMATCH`、`ERR-ARTIFACT_DUPLICATE`、`ERR-ARTIFACT_PATH_INVALID`、`ERR-ARTIFACT_UNDECLARED` |
| Source | `ERR-SOURCE_IDENTITY_MISMATCH`、`ERR-SOURCE_CHECKSUM_MISMATCH`、`ERR-SOURCE_MUTATION`、`ERR-SOURCE_FORMAT_UNSUPPORTED` |
| Closure | `ERR-CLOSURE_INCOMPLETE`、`ERR-DEPENDENCY_UNRESOLVED`、`ERR-DICTIONARY_MISSING`、`ERR-CALIBRATION_MISSING`、`ERR-SIDECAR_STALE` |
| Replay | `ERR-REQUIRED_STAGE_FAILED`、`ERR-REPLAY_INVARIANT_VIOLATION`、`ERR-DETERMINISTIC_OUTPUT_MISSING`、`ERR-OUTPUT_COMPARISON_MISMATCH` |
| External reference | `ERR-EXTERNAL_REFERENCE_UNAVAILABLE`、`ERR-EXTERNAL_REFERENCE_LICENSE_DENIED`、`ERR-EXTERNAL_REFERENCE_CHECKSUM_MISMATCH` |
| Security/privacy | `ERR-FORBIDDEN_FIELD_PRESENT`、`ERR-PATH_ESCAPE`、`ERR-SECRET_DETECTED`、`ERR-PII_DETECTED`、`ERR-UNAUTHORIZED_MUTATION` |

一个 case 可有多个 codes；codes 解释失败层级，不能取代 primary state。已启动 run
中本表全部 `ERR-*` 均为 blocking 并导致 `replay_fail`；warning 不使用 `ERR-*`。

## 16. `replay_pass` 最低必要条件

`replay_pass` 必须同时满足：

1. supported manifest contract、package type、replay profile 与 comparison profile；
2. valid manifest，全部 required 与 conditional required artifacts 存在；
3. checksum、package/source/artifact identity 与当前 profile 所需 provenance 有效；
4. evidence closure complete；
5. required deterministic stages 已执行，expected 与 actual deterministic outputs 可用；
6. frozen comparison 完成，所有 required invariants 及 equivalence relation 成立；
7. `source_mutation_count=0`、`unauthorized_mutation_count=0`、
   `advisor_mutation_count=0`、`feedback_mutation_count=0`；
8. deterministic code 产生完整 result report，且没有 blocking reason code。

CLI exit code=0、无异常、metadata 相同、部分 checksum、`proof_drift=0`、advisor/
reviewer opinion 或没有已记录 `replay_fail` 都不能替代以上条件。

## 17. `replay_fail` 合同

`replay_fail` 是正式验证结果，不表示整个系统无效，必须保留详细 reason codes 和
失败样本，论文不得隐藏它。其独立类别为：executable mismatch（pipeline 可执行但
expected/actual 不等价）、integrity failure（checksum/identity/source mutation）、
closure failure（必要证据或依赖不完整）、contract failure（version/profile/schema/
required field 不合规）和 invariant failure（违反 deterministic constraint）。

## 18. `reference_only` 合同

只适用于：metadata-only package；因合同/许可证不允许完整复制但引用和 identity
检查成功的 external artifact；没有完整 replay contract 的历史格式；数据许可证仅
允许有限检查；或 profile 明确预声明为 reference-only。必须在执行前声明，所有
允许检查成功，无 checksum/identity mismatch，无正式 replay 失败，且 report 列出
未执行检查。它始终有 `replay_pass=false`，也不得使 `all_replay_passed=true`。

## 19. `not_evaluated` 合同

仅适用于验证未运行、环境未准备、数据/授权未获批准、comparison profile 未批准、
所需设备暂不可用或工作排期未到。已尝试 evaluation 而失败必须按已进入的状态路径
报告为 `replay_fail`，不得写为 `not_evaluated`。

## 20. Comparison Profile 合同

每个 profile 必须含 `profile_id`、`profile_version`、compared artifact types/fields、
ignored fields、normalization rules、ordering rules、exact-match fields、numeric
tolerance fields 与 justification、platform-dependent fields、forbidden shortcuts、
expected output source 和 mismatch classification。

默认 exact deterministic comparison。数值 tolerance 仅限具领域依据的字段，必须在
观察结果前冻结；timestamp、temporary path 等被忽略的非语义字段必须预先列出。
proof fields 不得以“平台差异”随意忽略。缺失 profile 不能 `replay_pass`，不同
profile 的结果不得直接合并。

## 21. Expected Output 合同

允许来源：`frozen_reference`、`independent_oracle`、`baseline_run`、
`manually_curated_contract`、`external_ground_truth`。LLM output、human helpfulness
和 advisor recommendation 不能作为 expected truth。manually curated contract 必须
记录来源和审阅；baseline run 不自动是 ground truth；external ground truth 必须记录
license 与 provenance。expected output 变化必须新建版本，不能覆盖旧版本。

## 22. Mutation 合同

区分 source、package、artifact、expected-output、actual-output、advisor、feedback、
proof-field 与 comparison-profile mutation。replay 对输入只读，输出写独立目录；不得
覆盖 package/expected output/Phase 6 artifact。detection 必须 deterministic。
`mutation_count=0` 只说明合同范围内未检测到变化，不等于 correctness。

## 23. Deterministic Serialization 合同

未来 canonical report 必须稳定字段顺序和数组排序、UTF-8、固定 newline、固定
float format、repeated-run byte equality 与 schema mirror consistency；禁止当前时间、
随机字段、absolute path、non-deterministic temp directory 和环境噪声进入 canonical
hash。P7.1 不实现 serializer。

## 24. Exit Code 合同草案

未来 CLI 必须可区分：正常完成且 `replay_pass`；正常完成但 `reference_only`；正常
完成但 `replay_fail`；invalid invocation；invalid package；unsupported contract；
internal deterministic error。具体数值留待实现前冻结。exit code 不取代 structured
report，且 `replay_fail` 不得与程序崩溃同义。

## 25. 安全与隐私合同

package/replay 默认 no secret、no PII、no raw participant feedback、no arbitrary
executable content、no shell command、no tool invocation、no absolute path leak。必须
reject path traversal；拒绝 archive 内 symbolic link/hardlink 与解压后的 path escape；
不自动联网获取 external reference，只有已验证的本地 materialized artifact 才可参与
replay。后续实现须冻结 package size limit、decompression-bomb 防护、
license/redistribution/access-control classification。replay runner 默认不依赖 LLM 或网络。

## 26. 许可证与 Provenance 合同

每个真实 trace 必须声明 source owner、source URL/internal reference、license
identifier、redistribution/derivative/public/raw-trace/metadata/checksum-publication
permissions、retention/deletion/access restriction 与 consent/ethics applicability。
无 license 信息的真实 trace 不得自动进入公开 repository 或公开 fixture，也不得
声称外部可完整复现实验。

未来报告只有在 package provenance 满足对应 replay profile 的全部硬件采集要求时，
才可标注“真实 RTOS/硬件”；缺失时必须标注 non-hardware 或 unknown，不得由
`source_kind` 推断真实硬件。

## 27. 向后兼容原则

contract version 必须显式；不支持版本不得猜测解析或静默升级。migration 必须独立
设计，并单独定义迁移前后 identity。Phase 6 artifacts 不因 Phase 7 contract 自动
migrate；P6 的 `reference_only` 保持历史状态。

## 28. Phase 6 与 Phase 7 隔离

P7.1 只读依赖，禁止修改：`collector/`、`desktop/evidence_export.py`、
`parser/evidence_models.py`、`spec/schema/proof_digest.schema.json`、
`spec/assets/schema/proof_digest.schema.json`、`parser/rtos_diagnosis_*.py`、
`tool/*rtos_diagnosis*.py`、`tests/python/test_rtos_diagnosis_*.py`、
`tests/python/fixtures/rtos_diagnosis/generated/phase6_synthetic_suite.json` 及 artifacts、
`tests/python/fixtures/rtos_diagnosis/reports/phase6_diagnosis_report.json`、
`tests/python/fixtures/rtos_diagnosis/advisor_reviews/phase6_advisor_review.json`、
`tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_*.json`、
`tests/python/fixtures/rtos_diagnosis/closeout/phase6_closeout_manifest.json`、
`docs/phase6_*.md`、historical expected outputs 与 benchmark evidence。

Phase 7 只能引用 Phase 6，生成独立 replay report 和独立 canonical hash；不得回写
P6.3 结果，Phase 7 pass 也不改变其八个 `reference_only`。

## 29. P7.2--P7.8 对合同的依赖

| 阶段 | 必须遵守的 P7.1 输入 |
| --- | --- |
| P7.2 | provenance、license、identity 合同采集数据。 |
| P7.3 | package/path 合同实现 reopen。 |
| P7.4 | replay state、reason code、comparison profile 实现 replay。 |
| P7.5 | identity、closure、mutation、proof 分层验证。 |
| P7.6 | 不得修改 replay state 定义。 |
| P7.7 | 同一 truth boundary 和 comparison contract 的 external baseline。 |
| P7.8 | 记录 contract version、artifacts、tests、gaps 与 claim boundary。 |

## 30. 未来文件范围草案

只规划、不创建：

| 分类 | 候选路径 |
| --- | --- |
| `allowed_new` | `parser/external_validation_contract.py`、`parser/external_evidence_replay.py`、`tool/run_external_evidence_replay.py`、`tests/python/test_external_validation_contract.py`、`tests/python/test_external_evidence_replay.py`、`tests/python/fixtures/external_validation/`、`spec/schema/external_evidence_package.schema.json`、`spec/schema/external_replay_report.schema.json`、对应 `spec/assets/schema/` mirrors、`docs/phase7_*.md` |
| `allowed_modify` | P7.1 后默认空；任何已有文件须逐路径独立审批。 |
| `read_only_dependency` | Phase 6 replay/report/advisor/feedback modules、fixtures、reports、manifests、existing schemas/mirrors。 |
| `forbidden` | 第 28 节全部路径，以及 P7.0 文档。 |

这些候选不构成本轮或自动进入后续阶段的创建授权。

## 31. 后续测试合同

后续而非本轮的最小计划：contract tests（field class、version、path、identity、
checksum、comparison profile）；state tests（四个状态、priority、reason codes）；
negative fixtures（missing/corrupt/stale/duplicate/partial/unsupported/mismatch/mutation/
closure/output mismatch）；reproducibility（byte/hash equality、无 timestamp/absolute
path、schema mirrors）；regression（Phase 6 tests、P6.4 hash、P6 artifact mutation=0、
proof semantics drift=0）。

## 32. Claim Boundary

P7.1 完成后仅可支持：External Evidence Package 合同、replay state machine、
`replay_pass` 最低条件、reason taxonomy、comparison profile、P6/P7 isolation 与后续
实现输入规范均已文档级冻结。字段数、reason-code 类别、planned artifacts/tests 与
package types 仅 report-only。

仍不能支持 reopen/replay 已实现或执行、任何 replay pass、closure/proof parity/proof
correctness、真实 RTOS、性能、baseline、diagnosis/root-cause accuracy、通用 RTOS、
SOTA、human usability 或论文录用概率。

## 33. P7.1 风险与 Fallback

| 风险 | fallback |
| --- | --- |
| 合同过度复杂 | 冻结最小核心，扩展字段留给未来版本，不一次覆盖全部 RTOS。 |
| 状态重叠 | 保持四个 primary states，其余为 reason codes。 |
| comparison profile 不明确 | 默认 exact comparison；不能冻结的字段不用作 pass。 |
| proof parity 定义不足 | 只定义 proof drift boundary；parity 独立批准。 |
| 外部字段不完整 | 使用 conditional required；不降 pass 标准，按预声明类型为 reference-only 或正式失败为 replay-fail。 |
| 与现有 schema 冲突 | 只记录冲突，P7.1 不改 schema，P7.2 前单独审批。 |

## 34. P7.1 冻结条件

冻结前必须满足：P7.0 已独立提交；本轮仅一个新增文档；P6/P7.0 mutation=0；四个
states、priority、`replay_pass`、reference-only non-downgrade、comparison、identity/
closure 分层、reason codes、proof drift/parity/correctness、license/provenance、安全/
privacy、未来范围、test plan 与 claim boundary 都清晰；多 Agent 双轮审阅达到
`blocking=0`、`major=0`；`git diff --check` 通过；不自动提交。

本文件不授权 P7.2，不创建任何实现产物。
