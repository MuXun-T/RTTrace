# P7.7 Plan: External Baseline Evidence

Status: approved implementation plan; no external baseline has been evaluated.

P7.7 produces an independent capability/comparison evidence layer only. It
does not change P6 or P7.1--P7.6 truth, artifacts, hashes, expected output,
proof semantics, or claim scope. It does not begin P7.8.

## Preconditions and Scope

Inputs are the frozen P7.3--P7.6 cases and their read-only identities, an
operator-supplied baseline inventory record, and, only for a measurement,
operator-supplied raw results. An external tool may not be downloaded,
installed, probed, or executed by this implementation. Source, version,
license, input support, and availability are unknown until independently
recorded as evidence. A missing field is `null` plus a non-empty reason; zero
never encodes missing or unavailable data.

The seven required comparison gates are: identical raw-trace SHA-256;
identical case/workload; identical input scope; independently matched truth
boundary; identical or approved-equivalent environment; identical metric,
unit, and statistical protocol; and identical failure/exclusion treatment.
Quantitative comparison is allowed only when every gate passes. Otherwise the
record is capability-only, not-comparable, not-applicable, or not-evaluated.

## File Boundary

`allowed_new`:

```text
docs/phase7_p7_7_plan.md
docs/phase7_p7_7_baseline_contract.md
docs/phase7_p7_7_reproducibility.md
docs/phase7_p7_7_claim_boundary.md
docs/phase7_p7_7_closeout.md
parser/external_baseline_models.py
parser/external_baseline_runner.py
tool/run_external_baseline_comparison.py
spec/schema/external_baseline_comparison.schema.json
spec/assets/schema/external_baseline_comparison.schema.json
tests/python/fixtures/external_validation/baseline/contract/README.md
tests/python/fixtures/external_validation/baseline/contract/*.json
tests/python/test_external_baseline_models.py
tests/python/test_external_baseline_runner.py
tests/python/test_external_baseline_cli.py
```

`allowed_modify`: none.

`read_only`: P7.1--P7.6 documents, implementations, tests, schemas, fixtures,
canonical artifacts and hashes; Phase 6 artifacts and reproducibility index.

`forbidden`: every existing file, including `collector/`,
`desktop/evidence_export.py`, `parser/evidence_models.py`, all proof
digest/hash implementations and schemas, `parser/rtos_diagnosis_*.py`,
`tool/*rtos_diagnosis*.py`, `tests/python/test_rtos_diagnosis_*.py`,
`spec/**/rtos_diagnosis_*.schema.json`, all P6 and P7.1--P7.6 frozen
artifacts, hashes, and expected outputs.

## Approved Items

| Item | Inputs and output | Tests and acceptance | Stop condition / fallback |
| --- | --- | --- | --- |
| P7.7.1 | This plan and the baseline contract. Candidate inventory categories are unverified `not_evaluated`. | Document audit for seven gates, null+reason semantics, raw retention, forbidden claims, and no external execution. | Stop on a required frozen-file change; retain only contract documents. |
| P7.7.2 | Contract plus JSON fixtures. Output: closed model and byte-identical schema mirrors. | Model rejection tests cover supported, unsupported, not-applicable, not-comparable, truth/environment/metric mismatch, zero misuse, canonical repeat, and information leakage. | Stop if schema/model needs a frozen surface; do not weaken validation. |
| P7.7.3 | Validated inventory/measurement input. Output: fail-closed runner and CLI writing outside the repository. | Runner/CLI tests cover exit code, create-exclusive path, raw retention, unfair-comparison rejection, and deterministic serialization. | No adapter runs an external baseline. Missing evidence remains `not_evaluated`. |
| P7.7.4 | Runner output. Output: independent canonical comparison report. | Repeated canonical bytes, schema mirror, raw result completeness, and no absolute path, temporary directory, PID, hostname, timestamp, or username. | No quantitative summary unless every gate passes; otherwise emit status and reasons only. |
| P7.7.5 | Verified test results and no-mutation audit. Output: reproducibility, claim-boundary, and closeout documents. | P7.7 focused tests, P7.3--P7.6 adjacent regression, full Python regression, `py_compile`, `git diff --check`, frozen-hash audit. | Stop and report any failed regression, mutation, missing evidence, license uncertainty, or unfair comparison. |

## Candidate Inventory and Comparison Eligibility

The candidate categories below are inventory labels, not assertions about a
specific product: RTOS trace viewer; Trace Compass; Perfetto; RTOS vendor
tool; custom full-trace baseline; and no-evidence-package baseline. At plan
time every category has candidate status `not_evaluated`, availability unknown,
version unknown, license unknown, and no verified input or capability support.

All candidates can appear in a capability matrix only when their fields are
explicitly evidenced. No candidate is eligible for quantitative comparison at
this time. A candidate that does not support a requested capability is
`not_applicable`, not a failure. A candidate lacking evidence, installation,
license clarity, input support, matching trace/case/input scope/truth boundary,
approved equivalent environment, metric protocol, or failure/exclusion policy
is `not_evaluated` or `not_comparable` with its reason retained.

## Risks, Exclusions, and Freeze

External availability, licensing, true input support, and independently matched
truth boundaries are explicit prerequisites, not assumptions. If any is
missing, P7.7 fails closed to capability-only inventory evidence and publishes
no quantitative result. A baseline's absence, unsupported capability, or
incomparable truth boundary must never become a zero score, error, accuracy
ranking, or inferred disadvantage.

P7.7 may describe evidence-backed inventory, declared capability, comparison
gates, exclusion reasons, and limited performance measurements satisfying all
gates. It must not claim diagnosis or root-cause accuracy, replay correctness,
proof correctness/parity, general RTOS benefit, anomaly-detection SOTA, overall
tool superiority, usability, remediation safety, or publication likelihood.

Freeze requires all new files only, byte-identical schema mirrors, deterministic
serialization, raw-result retention, no information leakage, all targeted and
adjacent tests passing, full Python regression passing, and unchanged frozen
P6/P7.1--P7.6 identities. A zero-result report means only that the contract
validly recorded no quantitative comparison; it does not validate an external
tool or establish a negative result.
