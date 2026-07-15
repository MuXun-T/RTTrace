# P7.7 External Baseline Contract

Status: pre-registered, fail-closed comparison contract. This contract defines
what may be recorded; it does not establish that any external baseline is
available, installed, licensed, supported, executed, or comparable.

## Inventory Record

Each baseline record has a stable `baseline_id`, display name, candidate
category, source, version, license, availability, declared input support, and
declared capabilities. Every unverified value is `null` with a non-empty
reason. Candidate and comparison status are separate from each capability
status. Capability status is exactly `supported`, `unsupported`,
`not_applicable`, or `not_evaluated`; candidate status is `not_evaluated`; and
comparison status is exactly `quantitative`, `capability_only`,
`not_comparable`, `not_applicable`, or `not_evaluated`.

The only pre-registered candidate categories are RTOS trace viewer, Trace
Compass, Perfetto, RTOS vendor tool, custom full-trace baseline, and
no-evidence-package baseline. Each starts as an unverified inventory item with
candidate status `not_evaluated`. These names do not demonstrate installation,
license, input format support, suitability, or fair-comparison eligibility.

An unsupported capability is `not_applicable`, not an error and not a zero. A
baseline that cannot be evidenced or run remains `not_evaluated`; a baseline
that cannot meet the gates below is `not_comparable`. No absent value may be
serialized as numeric zero.

## Comparison Unit and Truth Boundary

The unit of comparison is a declared baseline run over one frozen raw trace,
one case/workload, one input scope, one independent truth boundary, one
environment identity, and one metric protocol. The truth boundary states only
which externally observable facts a comparison may address. It is not a
diagnosis label, root-cause label, replay/proof result, or correctness oracle
unless a separate independent, identical, pre-registered ground truth exists.

Raw baseline result bytes or lossless raw records, every sample, failed run,
exclusion decision, command-independent environment identity, metric unit, and
statistical protocol are retained in the independent P7.7 output. Summaries
must retain sample count, median, dispersion, and failed runs; they cannot
report only a best value. Results must not contain absolute paths, temporary
directories, PID, hostname, timestamp, username, or external secrets.

## Fairness Gates

A quantitative comparison is valid only when all seven gates are explicitly
`pass` for both sides. Any non-pass gate requires no quantitative fields and a
status/reason instead.

| Gate | Required evidence | Non-pass consequence |
| --- | --- | --- |
| `same_raw_trace_sha256` | exact same raw-trace SHA-256 | `not_comparable` |
| `same_case_workload` | exact case and workload identity | `not_comparable` |
| `same_input_scope` | identical input range and preprocessing boundary | `not_comparable` |
| `matched_truth_boundary` | independently documented identical truth boundary | `not_comparable` |
| `same_or_approved_equivalent_environment` | identical identity or approved-equivalence record | `not_comparable` |
| `same_metric_unit_statistics` | identical metric definition, unit, repeats, and statistic | `not_comparable` |
| `same_failure_exclusion_policy` | identical retained-failure and exclusion rule | `not_comparable` |

If a requested capability is outside a baseline's declared support, use
`not_applicable`; do not call the baseline incorrect. If a matching truth
boundary cannot be independently reconstructed, do not perform correctness,
accuracy, or any ranking comparison. Missing environment or metric evidence is
also non-comparable, not an approved equivalent environment.

## Quantitative Authorization

Only a record with all seven gates passing may use comparison status
`quantitative`. It may compare the pre-registered performance metric only in
the stated unit and protocol. Its raw samples, sample count, median,
dispersion, failures, exclusions, and environment identity must be retained.
The absence of a valid measurement produces no substitute score and no
cross-tool ordering.

A contract-valid report with zero quantitative records means solely that the
schema and contract were satisfied. It does not mean a candidate was tested,
unavailable, slower, worse, unsupported, or unsuitable.

## Exclusions and Claim Boundary

This P7.7 evidence layer cannot claim diagnosis or root-cause accuracy,
replay correctness, proof correctness or proof parity, general RTOS advantage,
anomaly-detection SOTA, overall tool superiority, human usability, remediation
safety, or publication acceptance probability. It cannot rank tools on any
correctness-like property without independent, identical, pre-registered ground
truth. It does not modify or reinterpret P6 or P7.1--P7.6 results.

No external baseline download, installation, license acceptance, probing, or
execution is authorized by this contract. Missing external prerequisites retain
capability-only/not-evaluated inventory evidence and stop quantitative work.
