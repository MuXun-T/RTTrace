# Phase 1 H3 Timer Bound V2 Review

## Decision

The operator explicitly authorized a prospective replacement of the H3 CH1
period-error acceptance baseline. The decision ID is
`phase1-h3-timer-bound-v2-20260731T110045Z`, and it is effective no earlier
than `2026-07-31T11:00:45Z`. The v1 bound remains immutable historical
evidence. The new v2 limit is `1.50 us`, applied only to H3 sessions started
after that decision and only under the exact H3 configuration in the v2
contract.

## Review of V1

The v1 `0.75 us` value was not arbitrary: it was the maximum of three
chronologically accepted Alientek calibration captures, with maxima of `0.55`,
`0.75`, and `0.60 us`. Its weakness is statistical scope. A maximum over three
different firmware variants was used as a future hard limit for the later
`H3_COLLECTOR_SMOKE` configuration without a stated forward margin or H3
configuration calibration cohort.

## Comparable H3 Cohort

Only complete H3 sessions on the ALIENTEK ATK-DNF103 V2 with the exact frozen
firmware/ELF/build, CH0--CH7 DSView profile, 20 MHz sampling, and unchanged
clock algorithm were admitted. Their independently recomputable CH1 maxima
are:

| Session | Maximum absolute CH1 error |
| --- | ---: |
| `h3-alignment-20260731T100657Z-session01-recapture01` | `0.75 us` |
| `h3-alignment-20260731T103111Z-session02-recapture02` | `1.25 us` |
| `h3-alignment-20260731T104432Z-session02-recapture03` | `0.90 us` |

The cohort maximum is `1.25 us`. At 20 MHz, one sample is `0.05 us`. V2 adds
five samples (`0.25 us`) as a fixed forward engineering margin and publishes
`1.50 us`. This is a transparent engineering acceptance baseline, not a
confidence interval or a claim about all future timing behavior.

`h3-alignment-20260731T095215Z-session02` is retained as a v1 `FAIL` with its
derived artifacts and manifest hash. Its manifest-referenced raw DSView CSV,
DSL, and screenshot are no longer present at their stated paths, so it is not
used as a v2 calibration source. Excluding it does not alter the `1.25 us`
complete-cohort maximum or the resulting `1.50 us` v2 limit.

## Source Binding And Prospective Boundary

The v2 contract binds the manifest, clock-analysis, normalized-observer, raw
DSView CSV/DSL/screenshot, UART, and gate-receipt SHA-256 values for every
admitted source session. A future v2 session must record its start time, the
v2 decision ID, the effective timestamp, and the SHA-256 of the exact v2
contract in a pre-gate binding record. Missing or mismatching values make that
future session a `FAIL`; it cannot be classified under v2 after the fact.

## Excluded Fire H2 Evidence

The supplied Fire V2 bare-metal `GPIO_UART` session is retained and was
recomputed. It has an absolute CH1 error of `4.66945 ms`, five epoch anchors,
and an alignment residual as high as `3.98885755 s`. It uses a different board,
firmware, and protocol, so it is neither a qualifying H3 calibration point nor
a basis for increasing the Alientek H3 threshold.

## Non-Retroactivity and Gates

The v1 contract and every v1 session manifest remain unchanged. In particular,
the previously failed `0.85 us`, `1.25 us`, and `0.90 us` sessions remain
`FAIL` under v1 and cannot be selected or relabelled as passes. V2 requires
three new, independently started, full-evidence H3 sessions and an independent
review before an H3 score change can be considered. It does not authorize
Phase 2 completion or Phase 3.
