# P7.6 Closeout

P7.6 is an independent benchmark evidence layer. It retains raw samples and
failure runs, separates cold and same-process warm observations, and reports
median/MAD summaries. Acquisition overhead remains `not_evaluated` because the
approved hardware pairing prerequisites are absent.

The implementation started at
`4744300471faed8d14639fda24b8b17191b20da5` on `main`; no commit was created.
The final full regression command was
`PYTHONPATH=. python3 -m pytest tests/python -q -ra`, with `805 passed`,
`1195 subtests passed`, and exit zero. The corrective full regression reran
the same command with `806 passed`, `1195 subtests passed`, and exit zero.
The corrected focused P7.6 tests had `9 passed`.

The default external output held 48 raw samples: four cases, two modes, one
warm-up plus five measured samples per case/mode. It had 48 successful and
zero failed samples. Its canonical SHA-256 was
`c5a7c7b43cf0fb32905eb63ef3415a073d9363c21dc54843d6240822fcc46ef4`.
This value identifies that measured external output only; it is not a frozen
Phase 6 or P7.1-P7.5 identity. Parsing that same raw evidence twice and
serializing it canonically produced the same 81199 bytes and SHA-256. A new
timing run is not expected to retain elapsed/RSS values or this hash.

The final audit found byte-equal schema mirrors, no absolute-path/time/PID/
hostname fields in the output, and no tracked mutation from the start head.
P6.4 remained
`fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`;
P7.3 `opened.json` remained
`dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1`.

P7.6 has no real hardware, acquisition-overhead, OS-cache-reset, replay
correctness, proof correctness, diagnosis/root-cause accuracy, general RTOS,
SOTA, usability, remediation-safety, or publication-acceptance claim. RSS is
only a Linux host observation. P7.7 is not started by this closeout.

## Corrective Governance

`P76-PROC-01` remains a historical fact: the original strict implementation
Agent interrupted before completing its required item-by-item implementation
and reporting loop. The corrective replay is a retrospective independent
conformance review, not a reconstruction of that original process.

The replacement strict verification Agent independently completed and reported
Item 1. It found two implementation majors: an unapproved measured
`checksum_build_elapsed_s` metric and a schema accepting a non-measured zero
value. The primary Agent reviewed the report, removed the metric, and made the
byte-identical schema mirrors reject that value. The replacement Agent then
lost its session before its Item 1 re-review or Items 2--5. The primary Agent
completed technical checks and found a third implementation major: the CLI
allowed output inside the repository. It added repository-path rejection and a
focused test.

All three implementation majors are closed by focused tests. The replacement
Agent did not independently submit five separate reports or wait for five
primary-Agent reviews. On 2026-07-15 the project owner explicitly accepted
`P76-PROC-01` as an `accepted governance deviation`. This removes the open
governance major but does not reconstruct the missing reports or describe the
original process as completed.

The corrective P7.3 package scope passed `37 passed, 46 subtests passed`;
the P7.4 semantic scope passed `22 passed, 10 subtests passed`; and the P7.5
layered-validation scope passed `16 passed, 90 subtests passed`. P6.4 and
P7.3 hashes above, plus sampled P7.5 hashes
`3a780068900a56614c7ee48fb28f17a23c174582c8ce877d8ba4b4b36e6d4845` and
`35573f907a1bb40d93f0c28f2a5ec4efec324cd1b666722c7481bbf74136bb84`,
were rechecked unchanged. No existing tracked file is modified. The untracked
P7.6 inventory is:

```text
docs/phase7_p7_6_benchmark_contract.md
docs/phase7_p7_6_claim_boundary.md
docs/phase7_p7_6_closeout.md
docs/phase7_p7_6_governance_deviation.md
docs/phase7_p7_6_plan.md
docs/phase7_p7_6_reproducibility.md
parser/external_benchmark_models.py
parser/external_benchmark_runner.py
spec/assets/schema/external_benchmark_evidence.schema.json
spec/schema/external_benchmark_evidence.schema.json
tests/python/fixtures/external_validation/benchmark/contract/
tests/python/test_external_benchmark_cli.py
tests/python/test_external_benchmark_models.py
tests/python/test_external_benchmark_runner.py
tool/run_external_benchmark.py
```

## Governance Deviation Review

`docs/phase7_p7_6_governance_deviation.md` records the formal P76-PROC-01
review and owner acceptance. It preserves the interrupted multi-Agent history,
does not mark the original process complete, and does not start P7.7.

The 2026-07-15 revalidation passed P7.6 focused (`9 passed`), P7.3 (`37
passed, 46 subtests`), P7.4 (`22 passed, 10 subtests`), P7.5 (`16 passed, 90
subtests`), and full Python (`806 passed, 1195 subtests`). It rechecked
`py_compile`, `git diff --check`, schema mirrors, repeated canonicalization,
P6/P7.3/P7.5/raw hashes, no tracked mutation, and the report-only claim
boundary. The new external output's SHA-256 is intentionally run-specific and
is recorded only in the governance review evidence.
