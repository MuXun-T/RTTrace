# P7.4 Reproducibility Index

Run `python3 tool/run_external_semantic_replay.py --trace <approved trace> --format btf|vcd --output <outside-repository-output>`. Repeat the command and compare bytes and SHA-256. Focused validation is `python3 -m pytest tests/python/test_external_semantic_* tests/python/test_freertos_* -q`.

Canonical report hashes: `freertos_btf_1core.json` `71f4510cfd3b5f38970b599eb0352c3df35e5ec9c3b8c35fe2615fe07253e8f2`; `freertos_vcd_1core.json` `92f726044e1be01c46fd01dc67054ddccbe7d3e82288c096fcfea43e5fdb4b41`; `freertos_btf_4cores.json` `55ec16b0228b250b958731e85ed973570cfa1691e62b52b341e1febb64ac74e8`; `freertos_btf_50k.json` `e0249de7b526cb27d4558ff2160826123c529674b6b19cdee8b347b7d41d55ed`.
