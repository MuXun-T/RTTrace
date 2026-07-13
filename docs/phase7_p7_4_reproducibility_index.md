# P7.4 Reproducibility Index

Run each fixed case twice with `PYTHONPATH=. python3 tool/run_external_semantic_replay.py --case <case> --output <outside-repository-output>`, then compare bytes and SHA-256. The CLI exits 0 for replay pass, 2 for reference-only, and 4 for replay fail.

| case | state | replay/comparison attempted | primary reason | canonical SHA-256 |
| --- | --- | --- | --- | --- |
| `freertos_btf_1core` | `replay_pass` | true/true | null | `c75143ce8c2c2ec689289a6e52848cb5c4fca42ce343f6710571075478708b8d` |
| `freertos_vcd_1core` | `replay_fail` | true/false | `UNSUPPORTED_REQUIRED_RECORD` | `17a8037ba36e12bd04aaa376fac253eac400585aebdee9e7ad7d751a2ebea257` |
| `freertos_btf_4cores` | `replay_pass` | true/true | null | `199aacdc0d1341d4fd799da10d1405e034065da13fa2bfbc865af1358e53fc24` |
| `freertos_btf_50k` | `replay_fail` | true/true | `TIMESTAMP_REGRESSION` | `97ef68f12bd085ed66fb0fff50c65134c70cf91b0b433991e01f9ec1fd2f98e3` |
| `zephyr` | `reference_only` | false/false | `SOURCE_ACQUISITION_BLOCKED` | `3f65a0553920237433e3c762548c4925fe2723441af6d7307318a6d8425cbe98` |
| `zephelin` | `reference_only` | false/false | `SOURCE_EXTERNAL_REFERENCE_ONLY` | `15d39789104f16103842f5ea0e7404af06c1f16138c5e547ff6ba7722f23d307` |

The four FreeRTOS reports bind the raw trace SHA-256 and verified P7.2 source-row identity; they also carry the separately verified P7.3 opened package identity. This does not claim a trace-package binding. They use `p7.4-freertos-semantic-exact-v1`/`v1`. Successful BTF reports carry expected/actual identities. The VCD parser-failure report carries the validated expected identity `16b64e6376f2c81b42098064dbfb51816bef1143741d5edfe1845f0bc54f3a69` and no actual identity. Reference reports intentionally have `package_open_result=not_attempted` and package identity null because their state is derived from frozen P7.2 acquisition facts, not a P7.3 package-open result.

The two-run verification must produce byte equality and identical hashes for all six cases. Reference-only state comes from verified frozen P7.2 source-acquisition facts; it is not a P7.3 `opened_reference_only` package result. Canonical files are under `tests/python/fixtures/external_validation/replay/reports/`.
