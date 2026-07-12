# Phase 7 P7.3 External Evidence Package Reopen

状态：Approved Implementation Plan Candidate

子阶段：P7.3 External Evidence Package Reopen

实现状态：Not Started

Semantic Replay：Not Authorized

Replay State Calculation：Not Authorized

自动提交实现产物：按子阶段冻结

## 1. 目的与边界

P7.3 只实现外部 evidence package 的确定性、离线、只读打开与基础完整性验证。它读取
manifest，验证目录路径、artifact bytes、SHA-256、声明 identity、package identity、
completeness 与 external-reference declaration，并生成独立的 package-open report。

P7.3 不解析 BTF、VCD、CTF 或 TEF 语义；不重建 trace；不执行 semantic replay、输出比较、
proof parity 或 correctness 判断；不评估 diagnosis、root cause、Advisor、LLM 或 human
feedback；不访问网络、不执行 shell 或 package 内容，且不进行硬件验证。Package opened
不等于任何语义成功，checksum/identity 有效不等于 diagnosis 或 proof 正确。

P7.4 才能在单独批准后处理 deterministic semantic replay。P7.3 固定所有 open report
的 `replay_evaluated=false`，且不定义或产生 P7.1 replay primary state。

## 2. P7.2 冻结输入

P7.3 只读 `docs/phase7_external_trace_sources/source_inventory.json` 及 sources fixture。
FreeRTOS-BTF-Trace 固定 commit `791410f5ebb05a9fdf77401228140c60275b5d27`、MIT、
RV64 simulator、`hardware_validation=false`，并有四个本地 raw trace。Zephyr 固定 commit
`b01be6b7b16eb12a8cd0275752d575aca489c430`、Apache-2.0、`acquisition_blocked`/
`missing_toolchain`；Zephelin 固定 commit `ca37e2efea312f39a8670078daa1a14a2361e358`、
Apache-2.0、`external_reference_only`。后二者没有本地 trace，所有来源均非硬件验证。

P6、P7.0、P7.1、P7.2 plan/provenance、proof semantics、collector、evidence export 和
P7.2 raw trace/LICENSE/SOURCE 文件均为只读。

## 3. Supported Contract

v1 固定：`contract_name=rttrace_external_evidence_package`、
`contract_version=p7.3-package-open-v1`、
`manifest_version=external-evidence-package-v1`，固定 manifest 文件名
`package_manifest.json`。仅支持 directory package、SHA-256、local artifact bytes、
metadata-only package、referenced-external declaration 和 offline validation。

支持的 package kind 是 `self_contained`、`metadata_only`、`referenced_external`。识别
`hybrid` 但返回 `unsupported`；有效 archive/hybrid contract 不能被误报为 invalid。v1
不支持 zip/tar/compressed archive、下载、动态代码、shell、tool invocation、自动 external
artifact retrieval 或任何语义 trace parsing。

### 3.1 Manifest v1

顶层字段为 `contract_name`、`contract_version`、`manifest_version`、`package_kind`、
`package_identity`、`source`、`artifacts`、`external_references`、`configuration`、
`configuration_identity`、`package_open_profile_id`、`checksum_algorithm`、
`hardware_validation` 和 `limitations`。

`source` 包含 `source_id`、`source_kind`、`repository`、`repository_commit`、
`source_path`、`source_artifact_id`、`source_checksum`、`source_bytes`、`rtos_name`、`trace_format`、
`source_format_version`、`generation_mode`、`hardware_validation`、`license_spdx` 和 `source_identity`。artifact 包含
`artifact_id`、`artifact_kind`、`relative_path`、`required`、`bytes`、`sha256`、
`media_type`、`source_artifact_id`、`content_version`、`provenance_reference` 与 `artifact_identity`。external reference 包含
`reference_id`、`reference_kind`、`repository`、`repository_commit`、`source_path`、
`expected_sha256`、`license_spdx`、`availability`、`required`、`source_id` 与 `reference_identity`。`configuration`
is a closed object with `validation_mode=offline` and `package_profile=p7.3-directory-v1`.
`checksum_algorithm` is required: `sha256` is the only supported value; another valid string is
`unsupported` with `ERR_PACKAGE_CHECKSUM_ALGORITHM_UNSUPPORTED`, while a missing/non-string value is
invalid. A syntactically valid algorithm is a non-empty lowercase ASCII token matching `[a-z0-9_-]+`.
`package_open_profile_id` is exactly `p7.3-package-open-only-v1`; it is a P7.3 open-scope marker, not a
P7.1 comparison-profile identity, and report/model never label it a semantic comparison identity.

The source mapping is exact. `source_kind` equals the P7.2 record's `data_class`; `repository`,
`repository_commit`, `rtos_name`, `trace_format`, `generation_mode`, `hardware_validation`, and
`license_spdx` equal their same-named P7.2 fields. `source_format_version` is exactly
`p7.2-source-inventory-v1`; it records the frozen provenance vocabulary, not parsed trace semantics.
For self-contained, `source_artifact_id` equals the
primary required artifact's `source_artifact_id`, `source_path` equals that P7.2 artifact's `source_path`, and
`source_checksum`/`source_bytes` equal its `sha256`/`bytes`. For metadata-only/referenced-external,
`source_artifact_id`, `source_checksum`, and `source_bytes` are all JSON null, and `source_path` equals
the P7.2 source record's `source_path`. No other null/non-null pairing is valid. The P7.1-compatible
aliases are `source_trace_id=source_artifact_id`, `source_trace_checksum=source_checksum`,
`source_trace_bytes=source_bytes`, `source_format=trace_format`, and
`source_format_version=source_format_version`; their null behavior is the same. `source_identity` is
SHA-256 of exactly these six P7.1-compatible alias fields and validator recomputes it from the stated
P7.2 projection.

所有对象拒绝未声明字段。递归拒绝以下 key：`api_key`、`access_token`、`password`、
`secret`、`shell_command`、`tool_invocation`、`writable_proof_path`、`raw_human_feedback`、
`demographics`、`absolute_local_path`、`llm_correctness`、`advisor_correctness`，以及 P7.3
boundary 禁止的 `replay_pass`、`replay_fail`、`all_replay_passed`、`diagnosis_accuracy`、
`root_cause_accuracy`、`proof_correctness` semantic-result key。实现还检测 key 中的
`secret` 和 `pii`，但不记录值。

Requiredness is machine-enforced: every kind needs the fixed contract fields, a source record bound to
a P7.2 `source_id`, `configuration_identity`, `package_open_profile_id`, `hardware_validation`, and
limitations. `self_contained` needs at least one required primary local artifact. The source object's
`source_artifact_id` names that primary artifact and must match immutable P7.2 inventory bytes/checksum;
every additional declared artifact is optional, may be absent, and when present must also bind a unique
P7.2 artifact from the same source and pass bytes/checksum/identity validation. `metadata_only` has no local artifacts and at least one external
reference. `referenced_external` has no local artifacts and at least one external reference. A local
artifact plus an external reference is `hybrid` and unsupported. For source records without a local
P7.2 trace, source bytes/checksum may be null but source provenance identity is still mandatory.
Both top-level and source `hardware_validation` are fixed `false`, must equal each other and the bound
P7.2 inventory record, and `true` or any mismatch is invalid with `ERR_SOURCE_IDENTITY_MISMATCH`.
Each `reference_id` is unique. Each reference `source_id` binds to a P7.2 source and exactly matches its repository, commit and SPDX
license. `reference_kind=provenance_license` must use `source_path=LICENSE` and that source's frozen
license SHA-256. `reference_kind=trace_content` must use an acquired P7.2 artifact's source path and
SHA-256. `reference_identity` is SHA-256 of the closed reference record excluding only itself. P7.3
checks only these declarations and never fetches either. This gives Zephyr/Zephelin a valid
license-provenance reference without inventing an unavailable trace checksum.

### 3.2 Identity

identity 均为 `sha256(canonical JSON UTF-8 bytes)`，canonical JSON 使用 `sort_keys=true`、
`,`/`:` separators、ASCII escaping 和末尾单一换行。package identity 输入排除其自身、
当前时间、本机/temporary path、运行环境、open result、reason codes 和 CLI output。

package input is one exact projection: fixed manifest relative name; contract metadata; package kind;
the source object exactly as listed above; canonical configuration; configuration identity; comparison
profile ID; artifact identity records sorted by `artifact_id`; external-reference records sorted by
`reference_id`; hardware validation; and sorted limitations. An artifact record is exactly
`artifact_id`, `artifact_kind`, `relative_path`, `required`, `bytes`, `sha256`, `media_type`,
`source_artifact_id`, `content_version`, `provenance_reference`, `artifact_identity`. A reference record is exactly `reference_id`,
`reference_kind`, `source_id`, `repository`, `repository_commit`, `source_path`, `expected_sha256`,
`license_spdx`, `availability`, `required`, `reference_identity`. It does not include a second raw
manifest-object projection.
`package_identity` itself is excluded, preventing self-reference. Artifact identity input is the same
artifact record with `artifact_identity` removed; `content_version` is exactly `p7.2-source-inventory-v1`
and `provenance_reference` exactly `<source_id>:<source_artifact_id>`; configuration identity is SHA-256 of exactly
`{"validation_mode":"offline","package_profile":"p7.3-directory-v1"}`. The comparison ID is
exactly `p7.3-package-open-only-v1`; another non-empty value is invalid with
`ERR_COMPARISON_PROFILE_IDENTITY_MISMATCH`, while a missing value uses
`ERR_COMPARISON_PROFILE_IDENTITY_MISSING`. Package identity is exactly
`sha256(canonical_json({"manifest_name":"package_manifest.json","manifest": M}))`, where `M` is the
parsed top-level manifest with only `package_identity` removed, every object limited to its closed schema
keys, `artifacts` sorted by `artifact_id`, `external_references` sorted by `reference_id`, and
`limitations` sorted lexically with JSON null retained. No raw input bytes, aliases, filesystem data,
report field or runtime data is included. This is the only package identity projection and its canonical
manifest serialization plus fixed relative name satisfies the P7.1 identity input boundary. This identity
is independent of the existing proof digest and never changes proof hash input.

All SHA-256 values are lowercase 64-hex digests of raw bytes. The external `expected_sha256` uses the
same format. A source ID binds only to the P7.2 inventory record; source mutation validation compares
the four frozen FreeRTOS raw files against that record and never infers a network or hardware fact.

### 3.3 Completeness 与 external references

inventory 是 sealed：除 manifest 和声明 artifact 外的普通文件均为 undeclared artifact。
`self_contained` 的 required local artifacts 必须存在且通过 bytes/checksum/identity；optional
artifact 可缺失但若存在仍必须验证。`metadata_only` 和 `referenced_external` 可在有限的
manifest/reference checks 成功后为 `opened_reference_only`。required reference 标为
`unavailable` 或 `license_blocked` 时为 `blocked`；缺 identity/checksum 等 declaration
缺陷为 invalid。P7.3 从不访问该 reference。

`availability` is one of `declared`、`unavailable`、`license_blocked`; it is a declaration checked
offline, not a network reachability or acquisition result. Zephyr's P7.2 acquisition-blocked fact does
not itself make a correctly declared non-required reference package blocked.

## 4. Open Result 与 Reason Taxonomy

独立 `PackageOpenResult` 仅有：`not_attempted`、`opened`、`opened_reference_only`、
`blocked`、`invalid`、`unsupported`。所有状态互斥。

* `opened`: supported self-contained package 的所有有限 contract/integrity checks 通过。
* `opened_reference_only`: 执行前声明为 metadata-only 或 referenced-external 且全部有限
  checks 通过。
* `blocked`: manifest 合法但 required external reference 当前不可用或被许可阻止。
* `invalid`: malformed contract、security/path/integrity/completeness/mutation failure。
* `unsupported`: manifest 合法但 version、kind、algorithm 或 feature 不受 v1 支持。

Reason codes 固定采用 `ERR_PACKAGE_*` 或 `ERR_EXTERNAL_REFERENCE_*`：

* Manifest: `ERR_PACKAGE_MANIFEST_MISSING`、`ERR_PACKAGE_MANIFEST_UNREADABLE`、
  `ERR_PACKAGE_MANIFEST_INVALID`、`ERR_PACKAGE_CONTRACT_VERSION_UNSUPPORTED`、
  `ERR_PACKAGE_KIND_UNSUPPORTED`、`ERR_PACKAGE_CHECKSUM_ALGORITHM_UNSUPPORTED`。
* Path: `ERR_PACKAGE_ABSOLUTE_PATH`、`ERR_PACKAGE_PATH_TRAVERSAL`、
  `ERR_PACKAGE_SYMLINK_FORBIDDEN`、`ERR_PACKAGE_PATH_DUPLICATE`、
  `ERR_PACKAGE_PATH_NORMALIZATION`。
* Artifact: `ERR_PACKAGE_REQUIRED_ARTIFACT_MISSING`、`ERR_PACKAGE_ARTIFACT_SIZE_MISMATCH`、
  `ERR_PACKAGE_ARTIFACT_CHECKSUM_MISMATCH`、`ERR_PACKAGE_ARTIFACT_ID_DUPLICATE`、
  `ERR_PACKAGE_ARTIFACT_KIND_UNSUPPORTED`、`ERR_PACKAGE_UNDECLARED_ARTIFACT`、
  `ERR_PACKAGE_ZERO_BYTE_REQUIRED_ARTIFACT`、`ERR_PACKAGE_ARTIFACT_IDENTITY_MISMATCH`。
* Identity: `ERR_PACKAGE_IDENTITY_MISMATCH`、`ERR_SOURCE_IDENTITY_MISMATCH`、
  `ERR_CONFIGURATION_IDENTITY_MISMATCH`、`ERR_COMPARISON_PROFILE_IDENTITY_MISSING`、
  `ERR_COMPARISON_PROFILE_IDENTITY_MISMATCH`、`ERR_PACKAGE_SOURCE_MUTATION`、
  `ERR_PACKAGE_MUTATION`。
* External reference: `ERR_EXTERNAL_REFERENCE_UNAVAILABLE`、
  `ERR_EXTERNAL_REFERENCE_IDENTITY_MISSING`、`ERR_EXTERNAL_REFERENCE_CHECKSUM_MISSING`、
  `ERR_EXTERNAL_REFERENCE_LICENSE_BLOCKED`、`ERR_EXTERNAL_REFERENCE_ID_DUPLICATE`、
  `ERR_EXTERNAL_REFERENCE_IDENTITY_MISMATCH`。
* Security/limits: `ERR_PACKAGE_FORBIDDEN_FIELD`、`ERR_PACKAGE_SECRET_DETECTED`、
  `ERR_PACKAGE_PII_DETECTED`、`ERR_PACKAGE_ARCHIVE_UNSUPPORTED`、`ERR_PACKAGE_SIZE_LIMIT`、
  `ERR_PACKAGE_ARTIFACT_COUNT_LIMIT`、`ERR_PACKAGE_MANIFEST_SIZE_LIMIT`、
  `ERR_PACKAGE_SPECIAL_FILE`、`ERR_PACKAGE_HARDLINK_FORBIDDEN`。

Validation emits sorted unique codes. Invalid dominates blocked and successful states; unsupported
applies only to an otherwise valid manifest with an unimplemented valid feature. The deterministic
classification order is: every supplied regular-file input returns unsupported; a missing, unreadable or
other non-directory/non-regular root, or any malformed/security/integrity/completeness/mutation fault,
returns invalid; only then may a valid
unsupported version/kind/checksum feature return unsupported; only then may a valid required unavailable
reference return blocked; otherwise a valid reference-only kind returns opened_reference_only and a
valid self-contained kind returns opened. `not_attempted` is a request/default state only and is never
returned after an open invocation. No integrity/security failure may be downgraded to reference-only.

This is an independent `PackageOpenReason` vocabulary. It is not a P7.1 replay reason taxonomy and has
no mapping to any P7.1 replay state.

## 5. Path and Security Policy

Internal paths are normalized POSIX relative paths. Empty, absolute, Windows drive/UNC, backslash,
NUL, `.` and `..` forms, overlong paths, normalization changes and duplicate normalized paths are
rejected. The input root resolver starts from a retained trusted `/` descriptor for absolute paths or a
retained trusted current-working-directory descriptor for relative paths, rejects empty/`.`/`..` segments,
and opens every root-path ancestor with parent `dir_fd|O_DIRECTORY|O_NOFOLLOW` while retaining and
`fstat`-checking each descriptor identity. A pre-read `lstat` tree walk then accepts only directories and regular files, rejects socket,
FIFO, device and all other special files, and requires `st_nlink == 1` for every regular file. The root is
opened with `O_DIRECTORY|O_NOFOLLOW`, then `fstat`-matched against its initial `lstat`; every internal
directory is opened from its parent `dir_fd` with `O_DIRECTORY|O_NOFOLLOW`, and every file with its parent
`dir_fd` and `O_NOFOLLOW`. Its `fstat` must match the preflight `st_dev`, `st_ino`, `st_mode`, `st_size`
and `st_nlink` before its bytes are read. Thus no input read follows a pathname after root opening. The
root, manifest, every directory, every declared artifact and every undeclared entry must not be symlinks;
any symlink is rejected. The complete tree is snapshotted before and after validation by the same
descriptor-anchored traversal to detect TOCTOU mutation. Every supplied regular-file package input
returns `unsupported` with `ERR_PACKAGE_ARCHIVE_UNSUPPORTED`, without reading, parsing or trusting its
extension; only a directory enters the v1 reader. A directory with an archive-looking name uses the
normal directory contract. The reader does not import artifact code, read environment variables,
invoke subprocess/shell, make network calls or write package/source/proof input. The CLI accepts absolute
or cwd-relative output paths, resolves their parent by the same trusted descriptor protocol, rejects
empty/`.`/`..`/NUL components and symlinks, requires the final name to be one basename, records each
ancestor descriptor identity, rejects an ancestor equal to the package-root descriptor, rejects any
report-target symlink, rejects an ancestor equal to the retained repository-root descriptor, and creates
only with `O_CREAT|O_EXCL|O_NOFOLLOW` using the opened parent `dir_fd`. Thus CLI output is outside both
the package and repository roots, so it cannot create files in P7.2 source, Phase 6, proof, collector or
other frozen repository paths.

Frozen limits are `MAX_MANIFEST_BYTES=1 MiB`、`MAX_ARTIFACT_COUNT=256`、
`MAX_DECLARED_PACKAGE_BYTES=64 MiB`、`MAX_PATH_LENGTH=512` 和 `MAX_JSON_DEPTH=32`.
The walk also freezes `MAX_PACKAGE_FILE_COUNT=257` (manifest plus at most 256 artifact files),
`MAX_PACKAGE_ENTRY_COUNT=512` (all files and directories), and `MAX_ACTUAL_PACKAGE_BYTES=65 MiB`;
limits are enforced before opening another entry. Each descriptor read is bounded by the preflight size
plus one byte within the remaining global budget, then rechecked by `fstat` against its captured object
identity and size, so a growing file is rejected before unbounded consumption.

The pre-validation snapshot records sorted relative file paths, `st_dev`, `st_ino`, `st_mode`, `st_size`,
`st_nlink`, byte counts and streaming SHA-256 for the manifest and complete package tree; the post-
validation descriptor-anchored snapshot is compared to it and any change makes
`package_mutation_count>0` and invalid. At the same two points, P7.3 snapshots every P7.2
checksum-bearing local input: the inventory itself (fixed SHA-256
`ad15a481e2dde8eea0ef2b6e3feecc083e30699532296f27d1095cacaedde54b`), the four FreeRTOS raw files,
and all three copied source `LICENSE` files. License path is exactly
`tests/python/fixtures/external_validation/sources/<source_id>/<license_path>` from the inventory. All
paths are resolved from the retained repository-root descriptor with every intermediate component opened
by `dir_fd|O_DIRECTORY|O_NOFOLLOW` and retained pre/post identity checks; final files are no-follow
regular-file descriptors. Each digest must match the fixed inventory or its artifact/license digest and
its pre/post descriptor identity must match; any difference makes
`source_mutation_count>0` and invalid. Reports expose counts only, never source or local absolute paths.

## 6. Deterministic Report and CLI

The report schema contains version/contract/package identity/kind/result/reason codes; manifest/path
flags; required/optional/checksum/size counts; external-reference counts; completeness; source/package
mutation counts; forbidden/path counts; hardware validation; fixed `replay_evaluated=false`; and sorted
limitations. The report emits only validated `hardware_validation=false`. It contains no proof, accuracy,
diagnosis or semantic result field.

Serialization is canonical UTF-8 JSON with a final newline, stable keys and sorted arrays. It has no
timestamp, absolute path, temporary location, username or environment data. Repeated identical runs
must produce byte- and SHA-256-equal reports.

CLI syntax is `python3 tool/run_external_package_reopen.py --package DIR --output REPORT --offline`
with optional `--expected-contract-version`. `--offline` is default and the only supported mode. Exit
codes are 0 opened, 2 opened_reference_only, 3 invalid, 4 blocked, 5 unsupported, 64 invalid invocation,
70 internal deterministic error. A normal invalid package creates a structured report without traceback.

## 7. Fixtures, Tests, and Reproduction

Tests construct temporary self-contained packages from P7.2 FreeRTOS files and never duplicate their
large raw bytes in Git. Positive cases cover all four FreeRTOS artifacts, Zephyr metadata-only and
Zephelin referenced-external. Negative/security cases cover malformed and oversized manifests, paths,
symlinks, checksum/size/identity/mutation failures, forbidden nested fields, sealed inventory, limits,
references, archive/hybrid, output-inside-package and executable/environment fields. Mocks fail on
network, subprocess, shell and environment access. Security coverage asserts nested and directory
symlink rejection, archive preflight classification, no-follow reads, snapshot mutation detection,
exclusive output creation and output-parent symlink escape rejection.
Model and validator tests include top-level/source hardware `true` and mismatch adversarial cases.
Security tests also cover special files, hard links, actual undeclared byte/file-count limits and
descriptor-based output-parent replacement resistance. They also cover input-ancestor replacement and
same-content inode replacement and grow-during-read replacement during the validation window, plus
regular files with archive-looking or no suffix. CLI tests cover output targets below P7.2 source and
Phase 6/proof roots and require refusal before creation.

Focused tests run the four new external-package test files; P7.3.6 also executes canonical valid,
reference-only, invalid, unsupported and blocked CLI runs twice where applicable, verifies P7.2 SHA-256
inventory and the unchanged P6.4 hash, then runs `python3 -m pytest tests/python -q`.

## 8. Approved File Whitelist and Commits

Only these new files are permitted: four P7.3 documents; three parser modules; one CLI; two schema
mirrors for each schema; four named tests; and files below
`tests/python/fixtures/external_validation/packages/`. No existing file may be changed. The subphase
commits are P7.3.0 plan, P7.3.1 schemas/models, P7.3.2 reader, P7.3.3 validator, P7.3.4 report/CLI,
P7.3.5 fixtures/security, P7.3.6 integration/reproduction, and P7.3.7 closeout. Each is audited,
independently reviewed, tested, committed, and returned to a clean worktree before the next begins.

## 9. Risk, Fallback, and Claim Boundary

Risks are path escape, mutable input, malformed/oversized manifests, unlicensed/unavailable references,
and accidental expansion into semantic work. The fallback is fail closed as invalid/blocked/unsupported
with deterministic reason codes; it is never a successful open. Scope remains directory-only until a
future separately approved archive design exists.

P7.3 can support only deterministic package-open facts and report-only counts. It cannot support
semantic replay, trace reconstruction, semantic closure, proof parity/correctness, diagnosis/root-cause
correctness, general RTOS claims, real hardware, performance/scalability/baseline claims, human
usability, live LLM quality or publication outcome.
