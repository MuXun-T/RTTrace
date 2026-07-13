# P7.3/P7.4 Evidence Binding Reproducibility

Build each binding twice outside the repository with the existing CLI:

```bash
PYTHONPATH=. python3 tool/build_external_case_evidence_bindings.py build --case CASE --output /outside-repository/first.json
PYTHONPATH=. python3 tool/build_external_case_evidence_bindings.py build --case CASE --output /outside-repository/second.json
cmp /outside-repository/first.json /outside-repository/second.json
sha256sum /outside-repository/first.json /outside-repository/second.json
```

`CASE` is one of `freertos_btf_1core`, `freertos_vcd_1core`,
`freertos_btf_4cores`, or `freertos_btf_50k`.  The builder reads P7.2 raw
inputs, P7.3 manifests and CLI-reproduced reports, and frozen P7.4 reports;
it does not run semantic replay.  The canonical fixture is copied only after
the two external outputs have identical bytes and SHA-256 values.

| case | canonical binding | binding identity | fixture SHA-256 | bytes |
| --- | --- | --- | --- | ---: |
| `freertos_btf_1core` | `evidence_bindings/freertos_btf_1core.json` | `f80803eea21de187d93baa871b27df0e99a9e6432334830644be5c2f71eb2cbe` | `1d9942dffbb8dde1cb89a65370095904b7c5929b187144a591d5561d247ede73` | 1839 |
| `freertos_vcd_1core` | `evidence_bindings/freertos_vcd_1core.json` | `47a8d8cca264d2b7740b33577d755d5c46b84e0af8ac09dabb12f27c07235805` | `85fc1795b2bed8a3e739cffb181e58fea557d2776fd1198587f8f7c0916b35e7` | 1839 |
| `freertos_btf_4cores` | `evidence_bindings/freertos_btf_4cores.json` | `673bf36b77b87779a57e0d3a3a7414b313a9cbf302c3208b508e58518556a1cf` | `f1efcca8c9b86fd373771bb20154767481ffd5112549a399f232af7d183161ea` | 1853 |
| `freertos_btf_50k` | `evidence_bindings/freertos_btf_50k.json` | `201d7c7dca1ba19597a7f3295c3f1145e4fe6c276369769ed9f98224c63bfedc` | `bdf840f27f64d7a226b9141d67c9dcf74f052630482baab7209d7b06913b89b4` | 1832 |

Records use canonical UTF-8 JSON with sorted compact keys and one trailing LF.
They contain repository-relative logical paths only, no temporary/absolute
path, current time, random value, host, or environment value.  Build and
verify make no network, shell, subprocess, LLM, Advisor, Feedback, semantic
replay, frozen source/raw/P7.3/P7.4 report/proof write, or
hardware-validation call; P7.3 reopening writes only its temporary package
and external temporary report.
`hardware_validation=false`; P7.5 and P7.6 are not started.
