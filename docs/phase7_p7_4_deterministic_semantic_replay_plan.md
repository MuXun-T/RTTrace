# Phase 7 P7.4 Deterministic Semantic Replay Plan

Status: Approved implementation plan

P7.4 starts from `main` commit `42b651f5e01f12846e13ac4fc10e9e650be192cb`. It adds a separate semantic replay contract; it does not change P7.1, P7.2, or P7.3 semantics.

## Frozen Inputs

| artifact | bytes | SHA-256 | result expectation |
| --- | ---: | --- | --- |
| `example.btf` | 101774 | `571183cbafba85f0eeb9c74cf2350f02a4e628abe514409dd3c1b68286969f44` | replayed |
| `example.vcd` | 246609 | `7aedcf4e14bb0e34838613225ff50e6cb8d76ed62e12bcfe16aa6101b0f36997` | replayed |
| `example-4cores.btf` | 1157633 | `eb15beee65c62d53a3bbf9db5ebb36318156b720e4bd909272605dfeb1c6eed1` | replayed |
| `example-50k.btf` | 2696092 | `f032a6af43334abc5c5fbed6b145e711f0262e2b1dfa6d3a44a28c37b6308baa` | syntactically parses all 50001 records; source-order validation then yields `replay_fail` for its first timestamp regression at physical line 2042 |

The inventory is `ad15a481e2dde8eea0ef2b6e3feecc083e30699532296f27d1095cacaedde54b`. Source is FreeRTOS-BTF-Trace commit `791410f5ebb05a9fdf77401228140c60275b5d27`, MIT, simulator-generated, and `hardware_validation=false`. Zephyr and Zephelin have no approved local trace and remain reference-only or not-evaluated. BTF and VCD are separate runs; no cross-format equivalence is asserted.

The P6.4 canonical hash is `fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`. The P7.3 canonical opened report hash is `dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1`.

## Ownership And Sequence

| phase | owner | write whitelist | consumer/reviewer |
| --- | --- | --- | --- |
| P7.4.0 | A/E, merged by primary agent | this file and `phase7_p7_4_replay_contract.md` | A-E |
| P7.4.1 | A | `parser/external_semantic_event_models.py`; three schema pairs; `test_external_semantic_event_models.py` | C/D/E |
| P7.4.2 | B | `parser/freertos_btf_parser.py`; `test_freertos_btf_parser.py`; `replay/btf/` compact fixtures | A/C/D |
| P7.4.3 | B | `parser/freertos_vcd_parser.py`, `parser/external_semantic_normalizer.py`; corresponding tests; `replay/vcd/` compact fixtures | A/C/D/E |
| P7.4.4 | C | `parser/external_package_replay_adapter.py`, `parser/external_semantic_replay.py`, `test_external_semantic_replay.py` | A/B/D/E |
| P7.4.5 | E fixtures, C comparator/test | `parser/external_replay_comparator.py`, comparator test, `replay/expected/`, `replay/comparison_profiles/` | A/D/E; test validates closed fixture/profile schema, identity recomputation, trace binding, self-blessing rejection, and post-freeze mutation |
| P7.4.6 | C | `parser/external_semantic_replay_report.py`, `tool/run_external_semantic_replay.py`, CLI test | A/D/E |
| P7.4.7 | D | `test_external_semantic_replay_security.py`, `replay/security/` compact fixtures | owners A-C fix production issues |
| P7.4.8 | E | canonical reports under `replay/reports/`, reproducibility index | A-D |
| P7.4.9 | E | claim boundary and closeout | A-D |

Only the listed phase files may change. All P7.2 raw inputs are read only and may only be copied into a test temporary directory. Forbidden paths include `collector/`, `desktop/evidence_export.py`, `parser/evidence_models.py`, all Phase 6, all P7.0-P7.3 artifacts and semantics, proof schemas/digests, P7.2 provenance/LICENSE/SOURCE/raw files, and the P7.3 canonical report.

Each phase re-audits HEAD and worktree, obtains fresh A-E task plans, limits edits to its row, runs targeted positive/negative/adversarial/security checks, has cross-owner review, fixes blocking/major findings, runs `git diff --check`, commits its named phase commit, and verifies a clean worktree. P7.4.4 and P7.4.7 use fail-fast mocks proving production makes no network, shell, subprocess, environment, LLM, advisor, or feedback call, and makes no source/package/raw/proof write.

## Format And Resource Freeze

BTF is UTF-8, four fixed headers (`#version 2.2.0`, creator, creation date, `#timeScale us`), followed by eight comma-separated fields: timestamp, subject, zero, kind, target, zero, action, detail. Supported kinds/actions are the observed `C/set_frequency`, `T/preempt|resume`, and `STI/trigger`; unsupported required combinations fail closed. The creation date never enters semantic output.

VCD supports the audited vendor form: `$version ... $end`, `$timeScale 1us $end`, one or nested bounded `$scope`, one-bit `$var wire`, `$upscope`, `$enddefinitions`, `$dumpvars`, decimal timestamps and scalar `0`/`1` changes. The vendor trace omits a `$dumpvars` closing `$end`; this exact form is supported only when `$dumpvars` is immediately followed by a timestamp or scalar change. Duplicate identifiers, unknown identifiers, x/z/vector values, malformed directives, pre-definition changes, truncation, and format confusion fail closed. Repeated display names are allowed only when identifiers differ.

Frozen limits are `MAX_TRACE_BYTES=4194304`, `MAX_EVENTS=65536`, `MAX_LINE_LENGTH=256`, `MAX_SIGNALS=128`, `MAX_TIMESTAMP=1000000`, `MAX_ATTRIBUTE_COUNT=8`, `MAX_IDENTIFIER_LENGTH=16`, `MAX_TASK_NAME_LENGTH=64`, and `MAX_SCOPE_DEPTH=8`. Boundary tests cover limit minus one, limit, and limit plus one. `MAX_EVENTS` counts emitted semantic-candidate records only: BTF data records and VCD scalar value changes, never headers/directives/timestamp-only VCD lines. The canonical unit is `timestamp_unit="us"`; these approved inputs require no numeric scaling. `source_record_index` is zero-based semantic input record order: BTF headers are excluded and VCD counts scalar changes only; errors report a separate one-based physical line number. Event ordering retains source order, requires nondecreasing timestamps, retains same-timestamp order, and uses source record index only as the stable tie-breaker. No parser reorders inputs or invents a multi-core happens-before relation.

## Interfaces

P7.4.1 supplies immutable, closed `SemanticEvent`, `ReplayState`, `ReplayReason`, `ComparisonProfile`, `Mismatch`, `ReplayInvariantResult`, and `ReplayReport` types plus matching schemas. B supplies immutable parsed records and normalized event tuples. C consumes only those events and a new P7.3 adapter context; the frozen P7.3 report remains unchanged with `replay_evaluated=false`. E supplies manually curated expected fixtures before comparison; production output is never an expected source. D only adds hostile tests/fixtures.

The P7.3 public seam is `read_directory_package()` followed by `validate_package()`. The adapter retains P7.3 facts and reasons unchanged, binds exactly one approved artifact, reads it with descriptor identity checks, and snapshots source, package, and the approved raw trace before and after. It does not modify P7.3.

## Commit Plan

1. `docs: approve phase7 deterministic semantic replay`
2. `phase7: add semantic event schemas and models`
3. `phase7: add deterministic FreeRTOS BTF parser`
4. `phase7: add VCD parser and event normalization`
5. `phase7: add deterministic semantic replay engine`
6. `phase7: add replay comparison profiles`
7. `phase7: add replay report and CLI`
8. `phase7: add semantic replay adversarial fixtures`
9. `phase7: complete semantic replay integration validation`
10. `phase7: finalize semantic replay and reproducibility`

P7.5 is not planned or started by this document.
