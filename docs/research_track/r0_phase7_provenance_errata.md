# R0 Phase 7 Non-Destructive Provenance Errata

## 1. Document Status

Status: R0 documentation-only addendum, dated 2026-07-17 (Asia/Shanghai).

Implementation Authorization: R0 Documentation Only

Experiment Execution: Not Started

Prior-Art Search: Not Started

Phase 7 Historical Mutation: Forbidden

Automatic Commit: Disabled

This errata is an additive explanation. It does not overwrite, delete, or
regenerate Phase 7 historical evidence.

## 2. Purpose and Non-Goals

Purpose: reconcile three provenance defects in the P7.8 frozen record: the
missing required `--output` argument, focused-test count scope, and recorded
HEAD versus final freeze commit.

Non-goals: modifying Phase 0-7 files, code, schemas, fixtures, tests, logs,
JUnit output, benchmark data, manifests, hashes, or historical conclusions;
running a new experiment, prior-art search, R1A, or any later R-track phase.

## 3. Frozen Source Inventory

| Source | Frozen role | Evidence used in R0 |
| --- | --- | --- |
| `docs/phase7_p7_8_reproducibility.md` | P7.8 future-use instructions | Records the incomplete audit command and the three-file textual focused command. |
| `docs/phase7_p7_8_closeout.md` | Historical closeout narrative | Records the full-result counts, `22 passed`, the audit hash, and `811d292` as start/final worktree HEAD. |
| `tests/python/fixtures/phase7_closeout/valid_manifest.json` | Frozen P7.8 manifest | Records `focused_command`, `focused_passed=22`, and `full_passed=843`. |
| `tool/run_phase7_closeout_audit.py` at `5c257ed` | Frozen CLI contract | Requires `--manifest` and `--output`; rejects repository-local output and opens the destination exclusively. |
| `tests/python/test_phase7_closeout_*.py` at `5c257ed` | Frozen test source | Supports a current collection-only scope check; it is not a historical execution log. |
| Git commits `811d292` and `5c257ed` | Commit lineage | Establish P7.7 freeze, P7.8 final freeze, ancestry, dates, and changed paths. |
| `docs/phase6_p6_7_closeout.md` | Protected predecessor context | Read-only context only; no Phase 6 finding is changed by this errata. |

All quoted frozen-file content was inspected at current `HEAD`
`5c257ed83a19086898a0173678c456410f55616d`. The master plan
`docs/ccfb_research_validation_master_plan.md` was present but untracked at
R0 start; it is not a frozen source and is not modified here.

## 4. E1 Missing `--output`

### Original recorded command

`docs/phase7_p7_8_reproducibility.md` and the manifest reproducibility list
record:

```text
python3 tool/run_phase7_closeout_audit.py --manifest tests/python/fixtures/phase7_closeout/valid_manifest.json
```

### Verified contract and corrected future-use command

The frozen CLI declares both `--manifest` and `--output` as required. It
requires the resolved output path to be outside the repository, creates its
parent directory if needed, and uses exclusive creation. A future destination
must therefore be external and absent before invocation.

```text
python3 tool/run_phase7_closeout_audit.py --manifest tests/python/fixtures/phase7_closeout/valid_manifest.json --output /tmp/p7_8_closeout_audit.json
```

This is a reconstructed command for future reproducibility, not a claim about
the command actually used for the historical audit. `/tmp/p7_8_closeout_audit.json`
is an example only; it must be outside the checkout and must not already exist.

### Evidence, confidence, and historical-result impact

Evidence source: frozen reproducibility document, frozen manifest,
`tool/run_phase7_closeout_audit.py` argument declarations and output guard,
and its `--help` output inspected during R0. The closeout records two
byte-identical audit outputs and their SHA-256, but no tracked historical argv,
output path, or run log establishes the actual invocation.

Confidence: high that the recorded future-use command is incomplete; high that
the corrected future-use command satisfies the frozen CLI contract; unresolved
for the historical actual command.

Historical-result impact: no historical pass/fail, hash, or claim is changed.
The exact historical command provenance remains unresolved.

Future operation: use the corrected command with a fresh external destination
and retain its argv/output provenance in a future, separately authorized run.

## 5. E2 Focused-Test Count Reconciliation

The values below are different measures or different command scopes. They must
not be substituted for one another. R0 performed collection-only checks with
cache writing disabled; these checks did not execute tests and do not recreate
or replace historical results.

| Document/Artifact | Original recorded number | Number meaning | Evidence source | Conflict | Correct explanation |
| --- | ---: | --- | --- | --- | --- |
| `valid_manifest.json` `regression.focused_command` | 22 passed | Declared focused pytest result | Command names only `test_phase7_closeout_models.py`; adjacent manifest field says `focused_passed=22` | Yes | The one-file command currently collects 17 pytest items, including 12 parameterized expansions. The historical value 22 cannot be attributed to that recorded one-file command. |
| `phase7_p7_8_reproducibility.md` first command | No count recorded | Three-file textual focused suite | Models, runner, and CLI module paths | Scope differs | These three files currently collect 19 pytest items. It omits `test_phase7_closeout_audit.py`; no historical pass count for this three-file command is retained. |
| `phase7_p7_8_closeout.md` | 22 passed | Historical statement called "P7.8 focused regression" | Closeout narrative | Ambiguous provenance, not overwritten | All four P7.8 test modules currently collect 22 pytest items: models 17, runner 1, CLI 1, audit 3. This is the only inspected scope matching 22, but the historical exact argv/pass log is not retained. |
| Current R0 collection-only check at `5c257ed` | 17 / 19 / 22 collected | Pytest collection counts for one / three / four P7.8 modules | `pytest --collect-only -q -p no:cacheprovider`; frozen test source | No historical result asserted | These are current source-derived collection counts, not historical pass counts. Parameterization expands the models module from 7 test definitions to 17 pytest items. |
| `valid_manifest.json` and `phase7_p7_8_closeout.md` | 843 passed | Full Python pytest passed count | Recorded full command is `PYTHONPATH=. python3 -m pytest tests/python -q -ra` | No numerical conflict with other measures | This is a historical passed count, not a recorded collected count. No retained collection log proves equality with collected tests or rules out deselection/xpass accounting. |
| `phase7_p7_8_closeout.md` | 1195 subtests passed | Separate subtest framework/reporting count for full regression | Closeout narrative only | Different statistic | It is neither pytest collected nor pytest passed. No retained raw log identifies its exact producer/scope. |
| `phase7_p7_8_closeout.md` | 2038 JUnit testcases | JUnit testcase-node count for full regression | Closeout narrative only | Different statistic | It is neither pytest collected nor pytest passed. No JUnit XML or retained producing command establishes the historical association. |
| `phase7_p7_7_closeout.md` | 821 passed / 1195 subtests / 2016 JUnit tests | Predecessor full-regression accounting | P7.7 closeout narrative | No conflict; useful delta context | Relative to P7.8's 843/1195/2038, the deltas are +22 pytest passed, +0 subtests, and +22 JUnit testcase nodes. This is compatible with, but does not prove, inclusion of the four-module P7.8 scope. |

Conclusion: the `22` focused value is a genuine command/count provenance
conflict in the manifest, while the 17, 19, and 22 source-derived collection
counts are explained by one-, three-, and four-module scope plus parameterized
expansion. The historical exact focused argv and pass output remain
`unresolved`. The 843/1195/2038 values are not mutually conflicting; they are
separate accounting layers, but their raw historical logs are not retained.
The P7.7-to-P7.8 delta is corroborating context only, not a replacement for a
missing P7.8 execution record.

Confidence: high for current command scope and collection arithmetic; medium
that historical `22 passed` intended the four-module P7.8 suite; unresolved
for its actual historical invocation and for raw subtest/JUnit provenance.

Historical-result impact: none. No count is replaced or recomputed as a
historical result.

Future operation: report command, collected, passed, skipped, deselected,
parameterization policy, subtest producer, JUnit producer/path, and testcase
count together in any separately authorized future run.

## 6. E3 Commit/HEAD Lineage

| Role | Commit | Meaning | Evidence |
| --- | --- | --- | --- |
| P7.7 freeze / P7.8 work start | `811d292566bad5466df79209e8d0c62fa3f5645a` | P7.7 external-baseline capability freeze; P7.8 closeout historically records this as its starting HEAD. | `git show --no-patch --format=fuller 811d292`; P7.8 closeout text. |
| P7.8 implementation end | `811d292566bad5466df79209e8d0c62fa3f5645a` | The historical P7.8 closeout records its final worktree HEAD as this commit. | `docs/phase7_p7_8_closeout.md`. Git alone does not prove execution timing. |
| P7.8 closeout writing worktree | `811d292566bad5466df79209e8d0c62fa3f5645a` | Same historical statement covers start and final HEAD while P7.8 content was still uncommitted. | `docs/phase7_p7_8_closeout.md`; interpreted only as a historical record. |
| Final P7.8 freeze | `5c257ed83a19086898a0173678c456410f55616d` | Commit named `phase7: freeze P7.8 closeout and final manifest`. | `git show --no-patch --format=fuller 5c257ed`; commit parent and merge-base are `811d292`; diff-tree adds all P7.8 files. |
| Current repository HEAD at R0 start and closeout | `5c257ed83a19086898a0173678c456410f55616d` | `main` HEAD; no R0 commit was created. | `git rev-parse HEAD`; final Git checks. |

`git merge-base 811d292 5c257ed` returns `811d292`, and `5c257ed` directly
parents `811d292`. Therefore `811d292` is not the final P7.8 freeze point.
The earlier value remains preserved as P7.8 worktree provenance, while
`5c257ed` is the final frozen P7.8 commit.

Confidence: high for Git lineage; high for what the historical closeout says;
no assertion is made that Git independently proves the historical execution
timeline.

Historical-result impact: none. The P7.8 closeout is not edited; its recorded
HEAD is contextualized rather than replaced.

Future operation: distinguish worktree start/end, document-write HEAD, and
freeze commit in separate fields.

## 7. Historical Record Preservation

The following records remain untouched: P6/P7 documents, P7.8 manifest,
source, schemas, fixtures, tests, artifact checksums, audit hash, benchmark
records, regression statements, and Git commits. R0 created no replacement
log, JUnit file, manifest, hash, or benchmark output.

## 8. Corrected Future Reproduction Guidance

From a clean checkout at the intended freeze commit, retain a separate
provenance record and use the reconstructed audit command in E1 with a fresh,
external output path. Treat the P7.8 full focused suite as four modules only
when explicitly named:

```text
PYTHONPATH=. python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py tests/python/test_phase7_closeout_audit.py -q -ra
```

This is future-use guidance based on frozen source scope. It is not asserted to
be the historical P7.8 argv. Future reports must preserve collection and
execution output, including any JUnit producer and path, outside the frozen
historical package.

## 9. Remaining Uncertainty

1. The actual historical audit argv and external output location are not
   retained.
2. The exact historical argv that produced `22 passed` is not retained.
3. The raw full-regression pytest, subtest, and JUnit outputs are not retained;
   JUnit producer/path and the 2038-node derivation are unresolved.
4. The P7.8 closeout's worktree timing is a historical document assertion,
   while Git independently establishes only the commit lineage and final freeze.

## 10. Claim Boundary

R0 supports provenance clarification only. It does not add proof correctness,
diagnosis accuracy, RTOS generality, real-hardware acquisition, baseline
ranking, benchmark, prior-art, usability, remediation, or publication claims.
No R1A or later research phase is authorized by this document.

## 11. Verification Checklist

- [x] Read Phase 6, P7.8, frozen manifest/source, and Git lineage without
      modifying them.
- [x] Verified CLI `--output` requirement and external-path guard.
- [x] Preserved original command text and labeled the corrected command as
      reconstructed future guidance.
- [x] Mapped pytest, subtest, and JUnit counts without conflation.
- [x] Performed collection-only scope checks without executing tests.
- [x] Ran the required `811d292`/`5c257ed` Git lineage checks.
- [x] Added this document without changing historical Phase 7 files.
