# P7.5 Layered Validation Plan

Status: approved implementation plan.  P7.5 starts from
`main@49724c1456597cb1a9de5d0ad2c1e70328a7d549` with a clean worktree and
consumes the frozen per-case evidence bindings described by
`phase7_p7_5_layered_validation_contract.md`.

## Scope And File Boundary

The write whitelist is limited to new P7.5 files:

```
docs/phase7_p7_5_*.md
parser/external_layered_validation_models.py
parser/external_evidence_closure_validator.py
parser/external_validation_intake.py
parser/external_replay_equivalence_validator.py
parser/external_validation_drift.py
parser/external_proof_parity_gate.py
parser/external_layered_validation.py
parser/external_layered_validation_report.py
spec/schema/external_layered_validation_report.schema.json
spec/assets/schema/external_layered_validation_report.schema.json
spec/schema/external_validation_profile.schema.json
spec/assets/schema/external_validation_profile.schema.json
tool/run_external_layered_validation.py
tests/python/fixtures/external_validation/layered_validation/
tests/python/test_external_layered_validation*.py
tests/python/test_external_evidence_closure_validator.py
tests/python/test_external_validation_intake.py
tests/python/test_external_replay_equivalence_validator.py
tests/python/test_external_validation_drift.py
tests/python/test_external_proof_parity_gate.py
```

All P7.0--P7.4 artifacts, Phase 6/proof, raw inputs, expected fixtures,
profiles, `collector/`, `desktop/evidence_export.py`, and
`parser/evidence_models.py` are read-only dependencies.  The only accepted
cross-phase closure for the four acquired cases is the frozen binding fixture:
validate both its embedded `binding_identity` (the canonical identity input
digest) and its separate raw binding fixture SHA-256 (the complete stored
bytes digest).  Never use either as a stand-in for the other.

## Execution Table

| stage | writes and interface | immediate regression and success criterion |
| --- | --- | --- |
| P7.5.0 | this plan and contract only | `git diff --check`; frozen P7.3/P7.4/binding files untouched |
| P7.5.1 | schemas/mirrors and immutable closed model: states, ordered layers/reasons, identity/closure/equivalence/drift/proof/report records | model and schema tests; mirrors byte-equal; validation/replay states remain separate |
| P7.5.2 | read-only intake and closure validator consuming inventory, manifest, reopen report, binding, expected/profile, and replay report | valid chain; missing/hash/swap/stale/identity/symlink/TOCTOU failures; P7.3 package and binding regressions |
| P7.5.3 | replay-equivalence adapter calling only frozen P7.4 replay interface into temporary output | pass, both replay-fail, and reference-only reproduction; canonical bytes/hash and all required fields equal; P7.4 regression |
| P7.5.4 | deterministic replay-fact drift and proof-parity eligibility gate | drift ordering/mismatch tests; empty proof domain stays ineligible/not-evaluated |
| P7.5.5 | orchestration, canonical report builder, and explicit CLI | six cases; stable output/exit code; no absolute/temp/environment data; no production network/shell/subprocess |
| P7.5.6 | positive, negative, adversarial, and security fixture/tests | swaps, injected parity, report-only injection, source replacement, path/symlink/TOCTOU, mutation and invocation counters fail closed |
| P7.5.7 | six canonical P7.5 reports and reproducibility index | generate each report twice outside frozen inputs; byte/hash equality; focused, P7.3, P7.4, external, and schema regressions |
| P7.5.8 | claim boundary and closeout only | complete test suite, frozen hash/mutation audit, clean-worktree audit; P7.6 remains not started |

Each stage stops on a failed test, an out-of-whitelist write, unstable output,
or a change to frozen input.  The implementation agent reports the changed
files, commands, test counts/exit status, canonical-output status, and
blocking/major findings to the main agent, then waits for that stage's commit
and authorization before beginning the next stage.  The main agent alone
commits; no amend, squash, rebase, reset, clean, or P7.4 correction is
permitted.

## Required Case Results

| case | frozen P7.4 result | P7.5 target |
| --- | --- | --- |
| `freertos_btf_1core` | `replay_pass` | `validation_pass` after all acquired layers pass |
| `freertos_vcd_1core` | `replay_fail` / `UNSUPPORTED_REQUIRED_RECORD` | `validation_pass` if that exact failure reproduces |
| `freertos_btf_4cores` | `replay_pass` | `validation_pass` after all acquired layers pass |
| `freertos_btf_50k` | `replay_fail` / `TIMESTAMP_REGRESSION` | `validation_pass` if that exact failure reproduces |
| `zephyr_pipeline` | `reference_only` / `SOURCE_ACQUISITION_BLOCKED` | `reference_only`, limited source/reference checks only |
| `zephelin_optional` | `reference_only` / `SOURCE_EXTERNAL_REFERENCE_ONLY` | `reference_only`, limited source/reference checks only |

No acquired case can pass on closure/reproduction/equivalence/drift failure.
No reference-only case may run parser, replay, or comparison.  `proof_parity`
is ineligible and `not_evaluated` unless a complete frozen proof domain and
parity profile are found without adding semantics.

## Commit And Freeze Plan

1. `docs: approve phase7 layered validation and drift analysis`
2. `phase7: add layered validation schemas and models`
3. `phase7: add cross-phase identity and evidence closure validation`
4. `phase7: add deterministic replay result equivalence validation`
5. `phase7: add deterministic drift analysis and proof-parity gate`
6. `phase7: add layered validation report and CLI`
7. `phase7: add layered validation adversarial and security tests`
8. `phase7: complete layered validation integration and reproducibility`
9. `phase7: finalize layered validation and drift analysis`

Before each commit, audit the whitelist with `git status --short`,
`git diff --stat`, `git diff --name-only`, `git diff --check`, and
`git diff --cached --stat`.  After every commit, require a clean worktree.
The final freeze requires six canonical reports reproduced twice, all frozen
P6.4/P7.2/P7.3/P7.4/binding hashes unchanged, `blocking=0`, `major=0`, full
`tests/python` completion, and final review.  The final closeout records only
the allowed P7.5 claim boundary.
