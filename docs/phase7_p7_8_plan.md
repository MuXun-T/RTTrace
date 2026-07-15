# Phase 7 P7.8 Closeout Plan

P7.8 is a closeout-only evidence layer. It reads frozen P6 and P7.1-P7.7
commits, artifacts, schemas, expected outputs, hashes, regression records,
accepted risks, and the P7.6 governance deviation. It does not reopen packages,
run replay, acquire traces, benchmark, compare baselines, or change any prior
semantic contract.

## Bounded outputs

1. `phase7_closeout_models.py` and mirrored
   `phase7_closeout_manifest.schema.json` define a closed manifest. Canonical
   bytes are UTF-8 JSON with ASCII escaping, sorted keys, compact separators,
   finite values, and one trailing LF. Only normalized repository-relative
   paths are accepted. Timestamps, absolute paths, temporary paths, PIDs,
   hostnames, usernames, random values, and machine noise are forbidden.
2. `run_phase7_closeout_audit.py` validates the manifest, verifies frozen
   commits/artifact checksums/schema mirrors, and emits deterministic audit
   output. Missing or mismatched evidence fails closed.
3. The closeout, reproducibility, and claim-boundary documents preserve every
   historical minor, accepted risk, and accepted governance deviation.

## File boundary

Only the explicitly approved P7.8 files may be added. P6 and P7.1-P7.7 code,
fixtures, schemas, canonical artifacts, hashes, collector/export/proof/schema
implementations, diagnosis code, and historical documents are read-only. No
Phase 8 work is authorized.

## Acceptance

The manifest must validate against both byte-identical schema mirrors, repeat
with byte-identical canonical serialization and SHA-256, contain complete
frozen inventory and identity fields, and pass focused, adjacent, and full
Python regression. Any missing artifact, checksum mismatch, schema mismatch,
path escape, environment leak, mutation, or reproducibility failure is a
blocking closeout failure; no assertion is weakened to recover.
