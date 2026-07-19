# R1A-REFRAME Provisional Claim Registry

## Status

All content is candidate/provisional R1A-REFRAME registration. It is not a research result, prior-art outcome, implementation record, experiment, or R1B authorization.

Implementation Authorization: R1A-REFRAME Documentation Only

Prior-Art Search: Not Started for Reframed Registry

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Phase 7 Historical Mutation: Forbidden

Automatic Commit: Disabled

## Candidate Problem

**Fail-Closed Validation of Identity-Bound Evidence Packages for RTOS Trace Analysis**

**面向 RTOS Trace 分析的身份绑定证据包失效关闭验证**

Candidate problem: in a multi-artifact RTOS trace evidence pipeline, partial, stale, corrupt, or mismatched inputs can be silently accepted. The candidate question is whether a predeclared identity-bound package contract and layered validation can explicitly reject modeled invalid packages.

This is not a claim that former C1 budgeted dependency selection, on-demand slicing, or limited-history mechanisms are new; those are engineering background only. It is not a claim that former C2 dependency/package capture or reopen is a contribution. Former C3 index break-even is not a candidate contribution and is only an engineering/report-only observation.

## Candidate Problem-Method-Insight

| Element | Candidate/provisional registration |
| --- | --- |
| Problem | Partial, stale, corrupt, or mismatched multi-artifact inputs may silently pass into RTOS trace evidence handling. |
| Method | Predeclare source/artifact identity projections, immutable bytes or content references, checksums, required dependencies, state constraints, and modeled invalid-input classes; apply layered validation to produce a contract outcome. |
| Insight | The bounded question is silent acceptance of modeled invalid evidence packages under the declared contract, not completeness of package capture, diagnosis validity, security in general, or a slicing/indexing result. |

## Preliminary Truth Boundary and Output Contract

The candidate/provisional truth boundary includes only predeclared package contract bytes or content references, artifact IDs, source/artifact identity projections, checksums, required-dependency declarations, validation-state and reason taxonomy, and modeled invalid classes. Identity cannot be inferred from path or filename. A digest is an identity projection only if explicitly declared; it is not raw bytes, and neither raw-byte equality nor digest agreement establishes semantic correctness by itself.

The preliminary/provisional output contract has only `contract-accepted` or a deterministic auditable `non-success` with a reason code. It is deliberately separate from frozen Phase 7 package-open and replay states: it neither maps to, replaces, nor claims implementation of those historical states. A contract-accepted output records only declared validation success; it does not state diagnosis correctness, semantic correctness, replay correctness, or proof correctness.

Excluded boundaries include undeclared raw state, arbitrary malicious kernels, cryptographic breaking, full semantic correctness, unmodeled input classes, generic RTOS behavior, and any Agent/LLM role. Agent/LLM material cannot enter the truth, proof, acceptance, validation-state, or reason-code path.

## Candidate Claim Records

### PC-1: Primary Candidate

| Field | Candidate/provisional registration |
| --- | --- |
| Status | provisional primary candidate |
| Claim | Within a predeclared package contract and attack model, a combination of source/artifact identity, checksums, required-dependency validation, and state constraints can reduce or prevent modeled invalid evidence packages from being silently accepted. |
| Support condition | Valid controls and every declared modeled invalid package are separately recorded; modeled invalid packages receive contract non-success rather than silent contract acceptance. |
| Refuting/cut result | A declared modeled invalid package is silently contract-accepted; a necessary contract field is undeclared or ambiguous; the attack model cannot be bounded; or a new R2 finds a directly substituting mechanism that removes the material distinction. |
| Candidate measurable indicators | modeled-invalid silent-accept count/rate; valid-control non-success count/rate; per-attack-class coverage; stated-contract-field coverage; deterministic outcome agreement. |
| Required future evidence | A predeclared mutation/control corpus, valid/invalid denominators, contract version, reason-code ledger, and a complete new R2. |
| Forbidden interpretation | No all-attacker, all-input, universal-security, semantic-correctness, diagnosis-correctness, replay-correctness, or proof-correctness statement. |

### SC-1: Auditable Non-success Candidate

| Field | Candidate/provisional registration |
| --- | --- |
| Status | provisional supporting candidate |
| Claim | Layered validation can produce deterministic, auditable non-success states and reason codes for declared modeled violations. |
| Support condition | For repeated identical declared inputs, each modeled violation has one documented non-success status/reason outcome in the preliminary vocabulary. |
| Refuting/cut result | A modeled violation has no auditable non-success, repeated declared inputs yield incompatible status/reason outcomes, or the status taxonomy is insufficiently declared. |
| Candidate measurable indicators | status/reason repeat agreement; unmapped modeled-violation count; reason-code coverage by attack class; valid-control non-success count. |
| Boundary | This is a contract-output property only; it does not establish evidence meaning, diagnosis correctness, or the correctness of a downstream consumer. |

### SC-2: Combined-Contract Coverage Candidate

| Field | Candidate/provisional registration |
| --- | --- |
| Status | provisional supporting candidate |
| Claim | Compared with existence-only, checksum-only, or manifest-only weak validations, the combined declared contract can cover more modeled cross-artifact mismatch classes. |
| Support condition | The declared combined contract detects a strictly larger declared modeled mismatch-class set while valid controls remain separately reported. |
| Refuting/cut result | A weak validation covers the same declared mismatch-class set; the combined contract misses a declared class; the variants are not bounded B1 isolation; or comparison requires an external B2 conclusion. |
| Candidate measurable indicators | per-variant modeled mismatch-class coverage; per-variant silent-accept count/rate; valid-control non-success count/rate; declared reason-code coverage. |
| Boundary | These weak variants are B1 mechanism isolation only. They are neither B2 nor a literature-superiority comparison. |

## Anti-claims

The following anti-claims are mandatory boundaries for every candidate record:

1. Checksum agreement does not establish semantic correctness.
2. A contract-accepted package does not establish diagnosis correctness.
3. Fail-closed behavior does not mean there are no missed invalid conditions.
4. A mutation suite does not establish safety against arbitrary attacks.
5. Deterministic rejection does not establish proof correctness.
6. RTOS application differences do not automatically establish a material research distinction.
7. Internal ablation does not establish prior-art superiority.
8. Agent/LLM does not enter the truth or proof path.
9. Package/dependency capture and reopen are not candidate contributions in this registry.
10. Contract acceptance does not establish replay correctness or downstream consumer correctness.

## Preliminary Threat Model

| Class | Candidate/provisional scope |
| --- | --- |
| missing artifact | A declared required artifact is absent. |
| partial package | The required artifact/dependency set is incomplete. |
| stale sidecar/index/manifest | A sidecar, index, or manifest does not bind to the current declared identity projection. |
| source/package mismatch | A package does not bind to its declared source identity projection. |
| artifact substitution | A substituted artifact violates a required declared identity or checksum relation. |
| checksum mismatch | Declared checksum and declared bytes/content reference disagree. |
| identity mismatch | Source/artifact identity projections violate their predeclared relation. |
| required dependency missing | A declared required dependency is absent or fails its declared constraint. |
| TOCTOU or post-validation change | A pending candidate class for new R2 only; no coverage or mechanism is recorded here. |

Non-targets: arbitrary malicious-kernel behavior, cryptographic breaking, and full semantic correctness. The registry has no claim of attack-model completeness, universal rejection, or general safety.

## New R2 Taxonomy: Search Plan Only

No formal search is performed or concluded in this R1A-REFRAME registry. The new R2 must test whether cross-domain mechanisms can directly substitute for the declared package contract, rather than assuming RTOS context supplies a new mechanism.

| Category | Required R2 inspection and direct-substitution question |
| --- | --- |
| artifact/package integrity | Inspect integrity and package validation mechanisms. Can one directly satisfy this contract? |
| provenance validation | Inspect provenance validation. Can it directly bind required identity projections and dependencies? |
| identity-bound/content-addressed artifacts | Inspect content-addressed and identity-bound artifacts. Can they directly substitute for this identity relation? |
| fail-closed validation | Inspect rejection/default-deny validation. Can it directly provide the modeled rejection semantics? |
| stale/partial input rejection | Inspect stale metadata and incomplete-input rejection. Can it directly cover the declared classes? |
| research package validation | Inspect reproducibility package validation. Can it directly validate this contract? |
| digital evidence bag/chain of custody | Inspect forensic evidence bags and custody mechanisms. Can they directly substitute for the contract and output vocabulary? |
| tamper-evident logging | Inspect tamper-evident logging. Can it directly handle cross-artifact acceptance rather than only record changes? |
| secure trace/log integrity | Inspect secure trace/log integrity. Can it directly cover the multi-artifact threat model? |
| in-toto, SLSA, artifact attestation | Inspect attestation and supply-chain frameworks. Can they directly enforce dependency and state constraints? |
| workflow/reproducibility package consistency | Inspect workflow consistency. Can it directly reject stated stale or mismatched evidence inputs? |
| dependency graph consistency | Inspect dependency graph consistency. Can it directly validate required dependency relations? |

Each category requires exact-term, synonym, mechanism, problem, and citation-chain queries in the future R2. This is a taxonomy only, with no source, citation conclusion, B2 selection, or formal R2 outcome.

## Baseline and Prior-Work Boundary

| Class | Candidate/provisional use |
| --- | --- |
| B1 | Existence-only, checksum-only, and manifest-only weak validations isolate declared mechanisms. They do not establish a literature delta. |
| B2 | No quantitative-eligible B2 is currently available. A later B2 decision belongs to the complete new R2 and does not authorize implementation or comparison here. |
| Former C1 | Engineering background/audited component only; no candidate claim about budgeted dependency selection, on-demand slicing, or limited history. |
| Former C2 | Engineering background only; no candidate claim about dependency/package capture or reopen. |
| Former C3 | Removed from candidate contributions; report-only engineering observation. |

## Candidate Decision Rules

Retain a candidate/provisional record for new R2 only if its scope remains bounded, every support condition and refuting/cut result remains explicit, and cross-domain direct-substitution review remains pending. Cut or reframe a record when an invalid class is silently accepted, a required field is ambiguous, a claim exceeds the truth boundary, the attack model expands, or the new R2 removes the material distinction. New R2 is required before R1B; no R1B, experiment, benchmark, B2 execution, hardware work, or implementation is authorized by this registry.

## Requested Agent Configuration Record

The requested implementation-agent configuration is `model=gpt-5.6-terra` with `reasoning_effort=high`. The collaboration interface provides no model-setting or runtime-attestation field. This is a request/configuration limitation record only, not a runtime model attestation.
