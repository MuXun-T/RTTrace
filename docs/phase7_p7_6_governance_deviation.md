# P7.6 Governance Deviation Review and Freeze Authorization Audit

Date: 2026-07-15

## Decision

`P76-PROC-01` does not affect the current engineering correctness of P7.6.
It is a process-governance deviation. On 2026-07-15 the project owner explicitly
accepted it as an `accepted governance deviation`. This approval does not state
or imply that the original process completed.

## Original Requirement and Actual Process

The additional P7.6 governance requirement required two distinct sub-Agents,
the specified model, separation of planning and implementation roles, and an
implementation Agent that completed five bounded items in order. For every
item it required implementation, testing, a separate report, and primary-Agent
review before moving to the next item.

The planning Agent completed the plan. The original implementation Agent
interrupted before producing modifications. The primary Agent implemented
P7.6. A replacement verification Agent independently completed and reported
Item 1, finding two real majors: the unapproved
`checksum_build_elapsed_s` metric and schema acceptance of a non-measured zero
value. The primary Agent reviewed and fixed both. The replacement Agent then
interrupted before its Item 1 re-review and before Items 2--5. The primary
Agent performed the Item 2--5 technical audits, found that the CLI allowed a
repository-internal evidence path, and fixed it with a focused test.

Five replacement-Agent reports were therefore not produced, and the required
per-item wait/review sequence did not occur. The requested model choice cannot
be fixed or proven through the available orchestration interface. These facts
are retained; this audit neither recreates reports nor describes the original
process as completed.

## Contract Classification

The Phase 7 preapproval and P7.6 plan/benchmark contract define benchmark
correctness through the closed sample model, runner behavior, canonical
serialization, repeat matrix, evidence isolation, tests, no-mutation checks,
and claim boundary. They do not make a named Agent, a model selection, or five
Agent reports an input to benchmark values, evidence identity, replay, or
validation semantics.

The five-report sequence is consequently a later project-governance control,
implemented through an orchestration interface. It is not an original frozen
repository correctness contract. Its failure shows that the prescribed review
process was not fully executed; it does not itself make a measurement,
canonical evidence, or frozen artifact indeterminate.

## Root Cause

The evidence supports Agent-session interruption, not developer disregard of
the process. Two independent long-running Agent sessions interrupted. The
orchestration interface does not expose a way to pin or attest a sub-Agent's
model selection. Treating uninterrupted tool behavior and model attestation as
an irreplaceable freeze condition therefore creates a governance dependency
that the available tooling cannot reliably satisfy.

Retrying another Agent would not restore the historical per-item process or
provide missing contemporaneous reports. It would add workflow noise and
create an avoidable risk of changes to an otherwise verified implementation.

## Impact Analysis

| Area | Impact | Evidence |
| --- | --- | --- |
| Code correctness | Not affected after correction | Three implementation majors were fixed; focused tests pass. |
| Benchmark correctness | Not affected | Contract, runner, repeat matrix, and report-only boundary were rechecked. |
| Canonical evidence | Not affected | One new external 48-sample output round-tripped byte-for-byte. |
| Schema and CLI | Not affected | Mirrors are byte-equal; unavailable values fail closed; repository output is rejected. |
| P7.3--P7.5 compatibility | Not affected | Frozen adjacent regression scopes and hashes pass unchanged. |
| Complete regression and no-mutation | Not affected | Full suite passes; HEAD and tracked-file mutation checks are clean. |
| Claim boundary | Not affected | No hardware, acquisition-overhead, OS-cache-reset, or broader performance claim was introduced. |
| Audit independence | Reduced | The primary Agent implemented and performed most final technical audit work. |
| Process traceability | Reduced | Five independent implementation reports and model attestation do not exist. |

The deviation does not make benchmark numbers untrustworthy, canonical evidence
uncertain, replay/validation semantics drift, or frozen artifacts modified. It
does leave process independence below the originally requested level.

## Revalidation Record

The audit began and ended at
`4744300471faed8d14639fda24b8b17191b20da5`. The following commands exited
zero on 2026-07-15: the P7.6 focused suite (`9 passed`); the P7.3 package
scope (`37 passed, 46 subtests`); the P7.4 semantic scope (`22 passed, 10
subtests`); the P7.5 layered-validation scope (`16 passed, 90 subtests`); and
`PYTHONPATH=. python3 -m pytest tests/python -q -ra` (`806 passed, 1195
subtests`). `py_compile`, schema mirror byte comparison, and `git diff --check`
also passed.

P6.4 canonical identity remained
`fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`;
P7.3 `opened.json` remained
`dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1`.
The sampled P7.5 report hashes remained
`3a780068900a56614c7ee48fb28f17a23c174582c8ce877d8ba4b4b36e6d4845` and
`35573f907a1bb40d93f0c28f2a5ec4efec324cd1b666722c7481bbf74136bb84`.
The four raw trace hashes remained `571183cbafba85f0eeb9c74cf2350f02a4e628abe514409dd3c1b68286969f44`,
`7aedcf4e14bb0e34838613225ff50e6cb8d76ed62e12bcfe16aa6101b0f36997`,
`eb15beee65c62d53a3bbf9db5ebb36318156b720e4bd909272605dfeb1c6eed1`, and
`f032a6af43334abc5c5fbed6b145e711f0262e2b1dfa6d3a44a28c37b6308baa`.

## Compensating Controls Executed

- Independent planning review and independent replacement-Agent Item 1 review.
- Replacement-Agent discovery of two real majors, followed by minimal primary-Agent fixes.
- Focused P7.6 regression: `9 passed`.
- P7.3 package regression: `37 passed, 46 subtests passed`.
- P7.4 semantic regression: `22 passed, 10 subtests passed`.
- P7.5 layered-validation regression: `16 passed, 90 subtests passed`.
- Full Python regression: `806 passed, 1195 subtests passed`.
- `py_compile`, `git diff --check`, byte-equal schema mirrors, and canonical repeated serialization.
- Rechecked P6.4 canonical identity, P7.3 `opened.json`, sampled P7.5 reports, and all four raw trace hashes.
- No tracked source/package/frozen-artifact mutation; benchmark CLI rejects repository output.
- New external evidence scan found no absolute path, `/tmp`, PID, hostname, timestamp, or username leak.
- That external output had 48 samples, `81203` bytes, and SHA-256
  `930e12b4a5e2445b1c75cfa5adbe3039ba0595cef014c42c51b9947f35e6d0e7`;
  it is run-specific report-only evidence, not a frozen identity.
- Closeout retains the interruption and all three historical implementation majors.

## Remaining Risk and Non-Claims

There are no five complete replacement-Agent reports, no proof that every
implementation step used the specified model, and no process independence
equivalent to the original design. Acceptance must not be read as evidence that
the original multi-Agent sequence completed, that all five items were completed
by an independent implementation Agent, or that the specified model was
technically verified.

The engineering limitations also remain: no real hardware, no acquisition
overhead evaluation, no OS cache reset, and RSS/performance observations only
for this Linux host and the four frozen FreeRTOS traces.

## Acceptance Rationale and Authorization

The owner approved acceptance because the deviation did not change code, data,
or benchmark results; every discovered implementation major was repaired and
tested; full regression and no-mutation checks provide direct engineering
evidence; and another Agent cannot reliably reconstruct the missing historical
process. Refusing acceptance would make an unstable tooling requirement
permanently block a verified engineering result.

Only the project owner or user may approve this deviation. Codex did not
self-approve it: the project owner explicitly approved it on 2026-07-15. Its
status is `accepted governance deviation`, never `original process completed`.

## Current Gate and Counts

Current status, after owner approval:

- `blocking = 0`
- `open technical major = 0`
- `open governance major = 0`
- `accepted governance deviation = 1` (`P76-PROC-01`)
- `minor = 0`
- `accepted technical risks = 4`

The four accepted technical risks are the documented absence of real hardware,
acquisition-overhead measurement, OS cache reset, and generalization beyond the
current Linux host/four frozen traces.

The owner acceptance permits the independent P7.6 freeze commit after its
final regression, no-mutation, and staged-diff gates pass. P7.7 remains out of
scope and may not start in this review.
