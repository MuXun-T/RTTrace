# Phase 1 H3 Final Alignment Audit V3

## Result

`timer_alignment_score = 2`

`PHASE 1 CORRECTIVE READY TO FREEZE`

This is a Phase 1 H3 timer/alignment decision only. It does not create a
formal Case, run a fault experiment, change Phase 2 production code, or
authorize Phase 3.

## Repository State

- Branch: `main`
- START_HEAD: `c5ea276758d4aa372ae51a3ab16771d306065ee5`
- The pre-existing tracked changes are `spec/schema_loader.py` and
  `spec/schema_validator.py`; they were not touched by this audit.
- The worktree also contains pre-existing untracked Phase 2 and research
  material. This audit adds only Phase 1 H3 evidence/validation files listed
  below. `git diff --check` passes.

## Hardware Authorization And Configuration

The workspace operator explicitly authorized the final Session 3 recapture
after the failed UART hash-chain audit. DSView was manually armed by the
operator; this audit performed only offline evidence validation after capture.

- Board: Alientek ATK-DNF103 V2, STM32F103ZET6
- RTOS: FreeRTOS V10.3.1
- Firmware: audited `H3_COLLECTOR_SMOKE`, firmware SHA-256
  `e67583ad9fb4adf1d8c1c719b24718046eb0e9e87576d69c2992ea3b3390dfe3`
- ELF SHA-256: `574d91f7a3841ec4fde19355b8836ae5d61dd92cf2051d13e3ae294154c0895e`
- Pin semantics: `phase1-pinmap-v3-corrective`; PB0=`LCD_BL`, green LED=`PE5`
- Observer: DSView/libsigrok4DSL 0.2.0 export, CH0-CH7, 20 MHz, 5 s,
  CH0 rising, 1.6 V threshold, no external sampling clock
- Frozen calculation: `analyze_clock.py --period-s 0.001 --calibration-column CH1`
- V2 prospective limit: `alignment_error_bound <= 1.50 us`

The prospective V2 contract is
`hardware/rtd_pilot/contracts/phase1_h3_timer_bounds_alientek_elite_v2_v2.json`
with SHA-256 `245ebc1412a44b26b26019cd23e31e06d1fb3af3b647144278c1fd22704820ef`.
It takes effect at `2026-07-31T11:00:45Z`; all counted sessions retain the
matching pre-gate binding.

## Counted Sessions

| Session | Start (UTC) | Error bound | Result |
| --- | --- | ---: | --- |
| `h3-alignment-20260731T110847Z-session01-v2` | `2026-07-31T11:11:11.505Z` | `0.9000000000267475 us` | PASS |
| `h3-alignment-20260731T112454Z-session02-v2` | `2026-07-31T11:25:43.936Z` | `0.9500000000004297 us` | PASS |
| `h3-alignment-20260731T120337Z-session03-recapture01-v2` | `2026-07-31T12:07:14.827Z` | `1.0000000000001328 us` | PASS |

Each session has distinct reset, DSL, and UART hashes; one unambiguous
`seq=1` shared epoch; CH0-CH7 all non-flat; matched raw DSL/CSV/screenshot,
UART/gate, and calculation records. Two frozen-algorithm calculations are
byte-identical for each session, and the maximum epoch residual is zero.

The write-once integrity receipts are in each counted session directory. They
bind its manifest hash, the current verifier SHA-256
`144cab0bafd3fe6d1d09f64e8f7eebdac9c408253e1e81ca00a5811e55e2075b`, and
the full verification result.

## Retained Failures

`hardware/rtd_pilot/h3_alignment_sessions/h3-alignment-20260731T113323Z-session03-v2`
is retained and excluded. The current verifier rejects its malformed UART
SHA-256 and the gate/clock UART-hash disagreements. It was not renamed,
overwritten, or counted.

The original `1.10 us` failed observation remains in
`docs/rtd_pilot/feasibility/phase1_h3_offline_alignment_review.json` as
`absolute_error_max_s = 0.0000011000000000030083`; it remains historical,
not a V2 success.

## Independent Checks

The main audit revalidated all raw/derived hash chains, contract bindings,
reset receipts, channel semantics, timestamps, and computations. Both
read-only reviewers concluded `blocking=0, major=0, minor=1`; the minor is a
stale verifier-hash field in the recapture manifest, with the write-once
receipt as the authoritative current control binding. `gpt-5.6-terra high` was
requested for the review, but serving-model identity cannot be verified from
the available environment.

## Validation

- `python3 tool/rtd_phase1_verify_h3_corrective.py --scorecard ...v3.json`: PASS
- Phase 1 focused tests: `48 passed`
- Historical Phase 1 freeze verifier: PASS; it validates its existing receipt
  and is not used as a substitute for the V3 decision.
- Phase 2 focused contracts: `58 passed`
- External Case-evidence models: `4 passed, 16 subtests passed`
- Truth-boundary subset: `11 passed, 43 deselected`
- Phase 2 schema mirrors: 9/9 byte-identical
- Phase 2 serial sentinel: one `invalid trace magic` failure, matching the
  recorded inherited START_HEAD baseline; no Phase 2 contract/schema call path
  is involved and no assertion was changed.

## Gate Decisions

- Phase 1: `PHASE 1 CORRECTIVE READY TO FREEZE`
- Phase 2: `TECHNICALLY_READY` after focused regression only. No freeze,
  feature change, formal Case, or expanded authorization is asserted.
- Phase 3: `NOT AUTHORIZED`

No commit, push, or tag was created.

## Files Added Or Changed By This Audit

- `hardware/rtd_pilot/scripts/verify_h3_alignment_session.py`
- `hardware/rtd_pilot/tests/test_tools.py`
- the three counted sessions' `integrity_audit_receipt.json` files
- `docs/rtd_pilot/feasibility/phase1_h3_alignment_final_audit_v3_20260731.json`
- `docs/rtd_pilot/feasibility/phase1_h3_alignment_final_audit_v3_20260731.md`
- `docs/rtd_pilot/feasibility/phase1_h3_scorecard_v3.json`
- `tests/python/test_rtd_phase1_verify_h3_corrective.py`
