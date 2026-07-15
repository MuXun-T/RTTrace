# Phase 7 P7.8 Reproducibility

Run from a clean checkout of the frozen Phase 7 head with `PYTHONPATH=.`:

```text
python3 -m pytest tests/python/test_phase7_closeout_models.py tests/python/test_phase7_closeout_runner.py tests/python/test_phase7_closeout_cli.py -q -ra
python3 tool/run_phase7_closeout_audit.py --manifest tests/python/fixtures/phase7_closeout/valid_manifest.json
python3 -m pytest tests/python -q -ra
```

The audit reads only declared frozen paths and uses raw-byte SHA-256 for
artifact checksums. Canonical manifest serialization is deterministic and
excludes time, absolute paths, `/tmp`, PID, hostname, username, UUID, and
random directory values. Run output and logs belong outside the repository.
The audit exits non-zero for missing or changed commits/artifacts, mirror
mismatch, path traversal, forbidden environment data, or any failed regression
gate. P7.6 host observations and P7.7 capability-only evidence remain report
only.
