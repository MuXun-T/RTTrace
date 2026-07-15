# P7.7 Reproducibility

Run from the repository root. The input below is a read-only contract fixture;
the output must be outside the repository and is created exclusively.

```bash
PYTHONPATH=. python3 tool/run_external_baseline_comparison.py \
  --input tests/python/fixtures/external_validation/baseline/contract/valid_capability_only.json \
  --output /tmp/p7_7_external_baseline_comparison.json
```

The CLI returns zero only after it has parsed the supplied record through the
closed P7.7 model and written canonical compact ASCII JSON with one trailing
LF. It does not execute an external tool. An existing output path, a
repository output path, invalid input, or an invalid contract returns 70; CLI
usage returns 64. The output is create-exclusive and is never overwritten.

To check repeated canonical byte equality, use two new external output paths
with the same read-only input, then compare them:

```bash
PYTHONPATH=. python3 tool/run_external_baseline_comparison.py \
  --input tests/python/fixtures/external_validation/baseline/contract/valid_capability_only.json \
  --output /tmp/p7_7_external_baseline_comparison_a.json
PYTHONPATH=. python3 tool/run_external_baseline_comparison.py \
  --input tests/python/fixtures/external_validation/baseline/contract/valid_capability_only.json \
  --output /tmp/p7_7_external_baseline_comparison_b.json
cmp /tmp/p7_7_external_baseline_comparison_a.json /tmp/p7_7_external_baseline_comparison_b.json
```

Run the model, runner, and CLI checks plus schema-mirror checks with:

```bash
PYTHONPATH=. python3 -m pytest \
  tests/python/test_external_baseline_models.py \
  tests/python/test_external_baseline_runner.py \
  tests/python/test_external_baseline_cli.py -q -ra
cmp spec/schema/external_baseline_comparison.schema.json \
  spec/assets/schema/external_baseline_comparison.schema.json
```

The supplied inventory contains the six pre-registered candidate categories
only as `not_evaluated`, capability-only records. It has no external baseline
execution, no raw quantitative external result, and no quantitative comparison.
Repeated equality verifies serialization of identical explicit input only; it
does not verify a candidate, external tool, or fair measurement.
