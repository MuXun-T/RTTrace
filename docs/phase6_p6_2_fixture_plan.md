# Phase 6 P6.2 Fixture Plan

Date: 2026-07-10

## Scope

This document defines the approved `P6.2` synthetic RTOS regression fixture slice only. It covers eight small known-root-cause synthetic cases, their companion fixture material, size limits, and the reporting boundary for generated suite metadata. It does not approve or implement `P6.3` replay equivalence, `P6.4` diagnosis metrics, `P6.5` advisor comparison, `P6.6` human feedback, or `P6.7` closeout packaging.

## Case Matrix

| case_kind | Synthetic baseline/candidate delta | Expected affected entity | Companion material |
|---|---|---|---|
| `priority_inversion` | Candidate extends the blocking interval on a shared mutex held by a low-priority task. | High-priority task and shared resource. | Two small `.trace` files. |
| `irq_latency_spike` | Candidate delays interrupt service handling beyond the baseline envelope. | Timer IRQ. | Two small `.trace` files. |
| `mutex_hold_inflation` | Candidate keeps a mutex longer than baseline on the same control path. | Mutex / resource holder. | Two small `.trace` files. |
| `queue_wait_backlog` | Candidate accumulates a queue backlog and delayed dequeue points. | Queue and consumer task. | Two small `.trace` files. |
| `task_starvation` | Candidate leaves a runnable task unscheduled for a longer interval. | Starved task. | Two small `.trace` files. |
| `corrupt_segment` | Candidate marks one synthetic segment as malformed while baseline stays consistent. | Trace segment. | Two `.trace` files plus a tiny `segments/` companion directory. |
| `stale_sidecar` | Candidate trace identity no longer matches a small synthetic sidecar record. | Sidecar metadata. | Two `.trace` files plus a tiny sidecar text file. |
| `missing_calibration` | Candidate omits the timing-calibration companion that baseline conceptually has. | Calibration metadata. | Two `.trace` files plus a tiny calibration note. |

## Output Shape

- The suite JSON remains schema-compatible with `rtos_diagnosis_suite.schema.json`.
- Every case keeps `baseline_trace.trace_ref` and `candidate_trace.trace_ref` as logical references such as `logical://...`; suite content must not embed `/media/` or any absolute host path.
- Generated companion files may live under `tests/python/fixtures/rtos_diagnosis/generated/`, but they remain small synthetic artifacts only.
- Each case carries:
  - baseline trace metadata
  - candidate trace metadata
  - `expected_root_cause`
  - `expected_affected_entity`
  - `expected_evidence_refs`
  - `expected_closure_mode`
  - `expected_replay`
  - `claim_class`

## Size Policy

The fixture builder must encode and enforce these limits, and tests must verify them:

- Default `max_events_per_case`: `8`
- Hard ceiling for `--max-events-per-case`: `12`
- Max bytes per generated `.trace` file: `1024`
- Max bytes per generated sidecar/segment/note text file: `512`
- Max generated companion files per case directory: `5`
- Max bytes for the generated suite JSON: `24000`
- Generated fixtures must stay CI-friendly and reproducible; no large datasets, no random growth, no hardware dependency

## Claim Boundary

- Every generated case stays `claim_class: "report_only"` by default.
- `expected_replay` stays placeholder-only:
  - `{"replay_required": false, "expected_replay_pass": null, "equivalence_scope": "not_evaluated"}`
- Synthetic fixture coverage may be reported as fixture availability only.
- Synthetic fixture coverage must not be stated as real RTOS generality, diagnosis accuracy, top-k performance, proof validity, or replay equivalence.
- No generated file may contain proof-digest payloads, proof tickets, sqlite artifacts, secret/token material, or benchmark-result semantics.

## P6.3 Defer Boundary

`P6.2` stops at synthetic fixture generation. The following remain explicitly deferred:

- Evidence package creation
- Replay equivalence or replay pass/fail semantics
- Proof digest or proof payload generation
- Ticket or sqlite index generation
- Diagnosis benchmark runner outputs
- Advisor explanation comparison
- Human feedback artifacts

The builder and tests must treat any of the above as out-of-scope and reject them if they appear in generated paths or content.
