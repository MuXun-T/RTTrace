# P7.6 Reproducibility

Run from the repository root and write output outside the repository:

```bash
PYTHONPATH=. python3 tool/run_external_benchmark.py \
  --output /tmp/p7.6-benchmark.json
```

The default matrix is four frozen FreeRTOS cases, two host modes, one warm-up,
and five measured samples. The output is canonical compact JSON with a stable
SHA-256 printed by the CLI. Re-canonicalizing identical raw samples must be
byte-identical; independent wall-clock measurements are not expected to be
byte-identical.

The CLI uses create-exclusive output and rejects repository paths. The raw
evidence is not a Phase 6 or P7.1-P7.5 artifact.
