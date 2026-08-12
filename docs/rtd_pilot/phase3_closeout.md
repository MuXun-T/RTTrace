# RTD-Pilot Phase 3 closeout

Status: `READY_TO_FREEZE_LOCALLY`

## Scope and start state

- Repository root: `/media/zzq/新加卷/patent/realization`
- Branch: `main`
- START_HEAD: `4f9de21135a0ed15374cc101da91354821b59f53`
- Start state contained the P3 implementation worktree diff plus pre-existing
  untracked `.aris/` and `docs/research_track/` material. Those untracked
  assets were neither removed nor included in this closeout or any commit.

The P3 implementation diff is confined to these approved implementation and
test paths:

- `desktop/services.py`
- `parser/codec.py`
- `parser/models.py`
- `parser/parser_process_agent.py`
- `parser/pipeline.py`
- `parser/rebuild.py`
- `parser/rtd_lineage.py`
- `spec/io.py`
- `spec/models.py`
- `tests/python/test_spec_models_compat.py`
- `tests/python/test_rtd_lineage.py`
- `tests/python/test_rtd_phase3_lineage_rebuild.py`
- `tests/python/test_rtd_phase3_production_context.py`

This closeout is added as an audit record only. No hardware operation, formal
Case/Capture, collector/backend work, fault injection, diagnoser, relevance,
verdict, Agent truth integration, or P4+ work occurred.

## Implemented lineage surface

P3 adds objective Evidence Lineage for task-state segments, execution slices,
resource hold/wait edges, IRQ spans, and ready-not-running intervals. A
lineage record retains only source/open/close/boundary event IDs, derivation
rule/version, observed horizons, capability/integrity references, objective
untrusted-window intersections, source integrity facts, and a structural
status. CCM and CIR are separate capture-context references; each retained
untrusted window is bound to CIR integrity information.

The implementation rejects unregistered or fabricated source IDs, retains all
intersecting untrusted windows, distinguishes boundary intersections, and does
not turn a missing open or close boundary into `COMPLETE`. `trusted` remains a
deprecated compatibility projection of authoritative lineage status, not a
replacement for it. Legacy bundles remain readable without being assigned
new lineage truth. Typed resource edges retain their legacy mapping projection.

`desktop/services.py` was reviewed separately. Its P3 changes are limited to
lineage registry serialization/deserialization validation, legacy bundle
compatibility, clipped-export lineage subset handling, required untrusted
window retention, and lineage/capture-reference presentation. It contains no
fault-specific relevance, `SUPPORTED`/`REFUTED`/`UNKNOWN`/`OOD` verdict,
diagnoser, truth judgment, Agent/LLM reasoning, or P4/P5/P6 function.

## Validation record

All commands below ran from the repository root with `/usr/bin/python3`.

| Gate | Result |
| --- | --- |
| P3 focused lineage, production-context, and serialization compatibility | `72 passed, 28 subtests passed` |
| Mandatory lineage/loss/boundary set | `15 passed, 9 deselected` |
| P1/P2 freeze verifier | `valid: true` |
| P1 standalone freeze verifier | `valid: true` |
| `git diff --check` | pass |
| Allowed/forbidden-path audit | pass; no hardware, collector, tool, P1/P2 contract, or P4+ path modified |
| Untracked/generated-artifact audit | pass; only approved P3 files plus preserved pre-existing `.aris/` and `docs/research_track/` material |

The 15 mandatory cases cover lost ready, lost dispatch/truncation, lost block,
lost wakeup/unlock, lost lock/IRQ enter, hidden unlock-lock gaps, lost IRQ
exit, disabled filter/sampling/mapping, task/exec/ready-not-running boundary
lineage, dispatch/competing execution, reused raw identity, IRQ nesting and
non-normal resource close, unmatched IRQ exit, multiple untrusted windows with
CCM/CIR binding, and typed-edge/registry round-trip without fabricated source
IDs.

Schema and serialization checks passed for the P3 lineage registry, typed
resource edges, CCM/CIR bindings, legacy reader behavior, and bundle
round-trip. The general bundle reader normalizes an omitted `alignment` into a
derived alignment summary; that behavior predates P3 and does not alter the
new lineage authority fields.

## Full Python regression and baseline disposition

`python3 -m pytest tests/python -q` completed with:

```text
1 failed, 935 passed, 1197 subtests passed
```

The sole failure is the historical sentinel:

```text
tests/python/test_online_channel.py::OnlineChannelTests::test_serial_channel_matches_offline_dataset
invalid trace magic
```

An isolated detached worktree at START_HEAD reproduced the same test failure
with the same `invalid trace magic` message. P3 did not modify the relevant
serial-channel production code. Therefore:

```text
new_regression_delta=0
```

This closeout does not claim that the inherited Python baseline failure was
fixed.

## Independent final review

An independent, read-only sub-agent was requested as `gpt-5.6-terra` with
`ultra` reasoning (higher than the requested `high`). The platform provides no
independently verifiable runtime model identity signature, which is recorded
here rather than inferred.

```text
blocking=0
major=0
minor=0
recommendation=create one local P3 freeze commit
```

The production-context remediation is complete. The ordinary
`ParserProcessAgent`/worker, pipeline, channel, workspace, package
reconstruction, and compare isolated paths accept and forward a validated
`CaptureLineageContext` to `rb_Rebuild`. The production-context regression
proves equivalent complete bindings where context is supplied, rejects
cross-capture and malformed context before worker dispatch, and preserves the
legacy fail-closed result where no context is supplied. This satisfies the P3
requirement that derived objects bind valid, capture-consistent CCM and CIR
references without fabricating lineage truth.

## Process record

Agent 2 completed the initial approved implementation item. Remaining approved
P3 integration was completed by the main Agent. No scope expansion, test
bypass, truth-boundary violation, or technical acceptance impact occurred.

The production call-site propagation remediation was independently rechecked.
No blocking or major issue remains.

## Freeze disposition and remaining risks

P1/P2 freeze integrity remains valid. No P1/P2 bound asset was changed, and
the P1/P2 receipt still records Phase 3 as not authorized at START_HEAD.

P3 is ready for exactly one local freeze commit. No push or tag is authorized,
and P4 remains unauthorized. The inherited serial regression sentinel remains
recorded above and is not a P3 regression.

```text
PHASE 3 READY TO FREEZE LOCALLY
P4 NOT AUTHORIZED
```
