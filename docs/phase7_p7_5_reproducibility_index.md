# P7.5 Layered Validation Reproducibility Index

P7.5 canonical reports are generated from the frozen P7.5 profile
`p7.5-layered-validation-v1`.  Each command writes outside the repository;
the two outputs for a case must be byte-identical before the canonical fixture
is accepted.

```bash
PYTHONPATH=. python3 tool/run_external_layered_validation.py --case CASE --output /outside-repository/first.json
PYTHONPATH=. python3 tool/run_external_layered_validation.py --case CASE --output /outside-repository/second.json
cmp /outside-repository/first.json /outside-repository/second.json
sha256sum /outside-repository/first.json /outside-repository/second.json
```

| case ID | P7.4 report SHA-256 | P7.5 canonical report | P7.5 SHA-256 | CLI exit |
| --- | --- | --- | --- | ---: |
| `freertos_btf_1core` | `c75143ce8c2c2ec689289a6e52848cb5c4fca42ce343f6710571075478708b8d` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_btf_1core.json` | `c307bfab9555055801fc85609865de4e9fdff136d18d7d7bad277c614a25e9bd` | 0 |
| `freertos_vcd_1core` | `17a8037ba36e12bd04aaa376fac253eac400585aebdee9e7ad7d751a2ebea257` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_vcd_1core.json` | `c37c3fa77cb15b80f98ba765917e7f13efc3ac9d7a6dbe54f5cb0e5fa036100c` | 0 |
| `freertos_btf_4cores` | `199aacdc0d1341d4fd799da10d1405e034065da13fa2bfbc865af1358e53fc24` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_btf_4cores.json` | `a492a7bcfd5b85d42ce1b5e82aa80c747d5f5ffc9e58306555f3a733657bbf5a` | 0 |
| `freertos_btf_50k` | `97ef68f12bd085ed66fb0fff50c65134c70cf91b0b433991e01f9ec1fd2f98e3` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_btf_50k.json` | `cf107a70473b0d483c0fc5acc14f3efd604687d55efc56b4f9a682abfdfeb60f` | 0 |
| `zephyr` | `3f65a0553920237433e3c762548c4925fe2723441af6d7307318a6d8425cbe98` | `tests/python/fixtures/external_validation/layered_validation/reports/zephyr.json` | `d0b83fb48fc821fd40a54eb7064f50f093a5dbfaad70cb0c2fa910853485c5c6` | 2 |
| `zephelin` | `15d39789104f16103842f5ea0e7404af06c1f16138c5e547ff6ba7722f23d307` | `tests/python/fixtures/external_validation/layered_validation/reports/zephelin.json` | `ef0ac521d6a24fdc4a86bdc8d1af54b12313df149c39130e7846956f6387769d` | 2 |

Focused integration regression:

```bash
PYTHONPATH=. pytest -q tests/python/test_external_layered_validation_integration.py
```

The canonical report is UTF-8, sorted compact JSON with one trailing LF.  It
contains no machine path, temporary path, time, hostname, username, or random
value.  `hardware_validation=false`; `proof_parity_eligible=false`;
`proof_parity=not_evaluated`; `proof_correctness=not_evaluated`; and P7.6 is
not started by this index.
