# Phase 2 Freeze Inventory

Status: `PREPARED_NOT_READY_TO_FREEZE`. Phase 2 freezes offline contracts and
synthetic examples only; it creates no real F1/F2/F3 Case, Capture, control, or
hardware claim.

Candidate freeze assets:

- `spec/rtd_pilot_contracts.py`
- `spec/schema_validator.py`
- both mirrored copies of the nine `rtd_*.schema.json` schemas
- `tests/python/test_rtd_pilot_contracts.py`
- `tests/python/test_rtd_pilot_contract_cli.py`
- `docs/rtd_pilot/contracts/phase2_contract_reference.md`
- `docs/rtd_pilot/contracts/phase2_contract_examples.md`
- `docs/rtd_pilot/contracts/phase2_regression_baseline_disposition.json`
- `docs/rtd_pilot/contracts/phase2_regression_baseline_disposition.md`
- `docs/rtd_pilot/contracts/phase2_final_independent_review.md`

The freeze dependency is the Phase 1 H3 corrective gate. It is currently
blocked by the independently reviewed timer/alignment result; therefore this
inventory is preparatory and must not be presented as a Phase 2 freeze.

Forbidden from this inventory: parser/rebuild or diagnoser changes, collector
production changes, lineage, Phase 3 assets, formal hardware Cases, raw
evidence replacement, and synthetic examples counted as real Cases.
