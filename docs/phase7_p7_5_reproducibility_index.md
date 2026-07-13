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
| `freertos_btf_1core` | `c75143ce8c2c2ec689289a6e52848cb5c4fca42ce343f6710571075478708b8d` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_btf_1core.json` | `3a780068900a56614c7ee48fb28f17a23c174582c8ce877d8ba4b4b36e6d4845` | 0 |
| `freertos_vcd_1core` | `17a8037ba36e12bd04aaa376fac253eac400585aebdee9e7ad7d751a2ebea257` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_vcd_1core.json` | `35573f907a1bb40d93f0c28f2a5ec4efec324cd1b666722c7481bbf74136bb84` | 0 |
| `freertos_btf_4cores` | `199aacdc0d1341d4fd799da10d1405e034065da13fa2bfbc865af1358e53fc24` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_btf_4cores.json` | `fe1264e642f56d21fb605077196dffe36cbec0128216c0878c28bf79a12a52d7` | 0 |
| `freertos_btf_50k` | `97ef68f12bd085ed66fb0fff50c65134c70cf91b0b433991e01f9ec1fd2f98e3` | `tests/python/fixtures/external_validation/layered_validation/reports/freertos_btf_50k.json` | `d10399ab2ebc4204d801af178d01d198e320731969f18aee3c22f6d341f8245b` | 0 |
| `zephyr` | `3f65a0553920237433e3c762548c4925fe2723441af6d7307318a6d8425cbe98` | `tests/python/fixtures/external_validation/layered_validation/reports/zephyr.json` | `5749bc2d9d262c40abd0b8e6d3218fa72d2c4a42d25af93bf392882a8623e1d6` | 2 |
| `zephelin` | `15d39789104f16103842f5ea0e7404af06c1f16138c5e547ff6ba7722f23d307` | `tests/python/fixtures/external_validation/layered_validation/reports/zephelin.json` | `4036ad2a2df1ac6776f39fda74e362c638df3839a3e44c83f7fdceb1aa662e3f` | 2 |

Focused integration regression:

```bash
PYTHONPATH=. pytest -q tests/python/test_external_layered_validation_integration.py
```

The canonical report is UTF-8, sorted compact JSON with one trailing LF.  It
contains no machine path, temporary path, time, hostname, username, or random
value.  `hardware_validation=false`; `proof_parity_eligible=false`;
`proof_parity=not_evaluated`; `proof_correctness=not_evaluated`; and P7.6 is
not started by this index.
