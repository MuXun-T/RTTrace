# P7.5 Final Closeout

P7.5.8 starts from clean `main@8319be9`
(`8319be91b72fd6c615f205b3f20a80c1c2f153d9`). This is the final P7.5
implementation head; this closeout commit is the P7.5 freeze. P7.5 commits
are `6c46055`, `826216d`, `b5ea128`, `2b2ac38`, `80bd1e1`, `003c386`,
`96e9970`, `c81233f`, `ead3882`, and `8319be9`. No earlier commit was
amended, squashed, rebased, reset, or cleaned.

The frozen P7.5 closeout commit is
`e1a402be95a8bd8d5d16b409c1bab12c417f2ea9`
(`phase7: finalize layered validation and drift analysis`). This
documentation-only follow-up records that immutable freeze hash; it is not a
P7.5 implementation or freeze commit and does not start P7.6.

## Case Results

| Case | P7.4 replay state | P7.5 validation state | Canonical SHA-256 |
| --- | --- | --- | --- |
| `freertos_btf_1core` | `replay_pass` | `validation_pass` | `3a780068900a56614c7ee48fb28f17a23c174582c8ce877d8ba4b4b36e6d4845` |
| `freertos_vcd_1core` | `replay_fail` | `validation_pass` | `35573f907a1bb40d93f0c28f2a5ec4efec324cd1b666722c7481bbf74136bb84` |
| `freertos_btf_4cores` | `replay_pass` | `validation_pass` | `fe1264e642f56d21fb605077196dffe36cbec0128216c0878c28bf79a12a52d7` |
| `freertos_btf_50k` | `replay_fail` | `validation_pass` | `d10399ab2ebc4204d801af178d01d198e320731969f18aee3c22f6d341f8245b` |
| `zephyr` | `reference_only` | `reference_only` | `5749bc2d9d262c40abd0b8e6d3218fa72d2c4a42d25af93bf392882a8623e1d6` |
| `zephelin` | `reference_only` | `reference_only` | `4036ad2a2df1ac6776f39fda74e362c638df3839a3e44c83f7fdceb1aa662e3f` |

Canonical paths are
`tests/python/fixtures/external_validation/layered_validation/reports/<case>.json`.
Each case was generated twice outside the repository. Both outputs were
byte-identical to each other and to its canonical fixture; acquired-case CLI
exits were `0/0`, and reference-only case exits were the documented `2/2`.

## Regression Evidence

The authoritative full command was:

```bash
PYTHONPATH=. python3 -m pytest tests/python -q -ra
```

The final primary-agent run completed with `797 passed`, `1195 subtests
passed`, `224.68s`, and `exit code=0`. It reported `failures=0`, `skips=0`,
and `warnings=0`. Its stdout/stderr and explicit exit-code file were retained
outside the repository while it ran.

Focused primary-agent regressions all exited zero: P7.5 `33 passed, 118
subtests`; P7.4 `22, 3`; P7.3 `37, 46`; evidence binding `23, 78`; external
validation `115, 245`; and schema mirrors `28, 33`. The historic aggregate
handoff `110 passed, 242 subtests` is not reproducible from the current test
inventory: its exact current 27-file scope is `110 passed, 237 subtests`.
This is a stale count only, with no failure, skip, or warning; the full suite
is the acceptance result.

## Frozen Audit

P6.4 remains `fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`.
P7.3 `opened.json` remains
`dd835f9a8595cd46d61ab1c3e05c6afa6473e4d38402ebacef82ded804ddbef1`.
The four P7.2 raw trace hashes remain `571183cbafba85f0eeb9c74cf2350f02a4e628abe514409dd3c1b68286969f44`,
`7aedcf4e14bb0e34838613225ff50e6cb8d76ed62e12bcfe16aa6101b0f36997`,
`eb15beee65c62d53a3bbf9db5ebb36318156b720e4bd909272605dfeb1c6eed1`, and
`f032a6af43334abc5c5fbed6b145e711f0262e2b1dfa6d3a44a28c37b6308baa`.

All six P7.4 replay reports, four evidence bindings, four P7.4 expected
fixtures, and the P7.4 comparison profile retained their frozen SHA-256
values. P7.0-P7.4 frozen artifacts and prohibited paths were unchanged;
source/package/raw mutation counts were zero, proof writes were zero, and
there were no P7.6 path changes. The six frozen P7.4 truth-path counter sets
remain `LLM=0`, `Advisor=0`, `Feedback=0`, `Network=0`, `Shell=0`, and
`Subprocess=0`.

`hardware_validation=false`; `proof_parity_eligible=false`;
`proof_parity=not_evaluated`; `proof_correctness=not_evaluated`; and
`diagnosis_correctness=not_evaluated`. P7.6 is not started. Final independent
review reports `blocking=0`, `major=0`, and `minor=0`.
