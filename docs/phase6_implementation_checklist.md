# Phase 6 Implementation Checklist

Date: 2026-07-09

This checklist freezes the approved P6.0 and P6.1 execution window. P6.2 and later are future independent tasks and must not be implemented in this window.

## P6.0 - Baseline Audit And Scope Lock

- Execution status: completed in the current freeze scope.
- Goal: add documentation that records the Phase 6 baseline, claim boundary, phase scope, and risk register.
- Allowed new files: `docs/phase6_scope_note.md`, `docs/phase6_implementation_checklist.md`, `docs/phase6_risk_register.md`.
- Allowed modified files: none.
- Forbidden files: all code files, all schema files, `parser/evidence_models.py`, `desktop/evidence_export.py`, `collector/`, `tests/cpp/`.
- Targeted regression: `python3 -m pytest tests/python/test_runtime_optimization_advisor.py -q`; `python3 -m pytest tests/python/test_runtime_optimization_agents.py -q`.
- Main-agent review required: yes, before using the documents to justify P6.2+ work.
- Allowed to enter next phase from this window: only P6.1, because it was pre-approved in the same task.

## P6.1 - RTOS Diagnosis Case And Suite Schema

- Execution status: completed in the current freeze scope.
- Goal: add independent RTOS diagnosis metadata schemas, mirrored schema assets, a metadata-only stub suite, and focused tests.
- Allowed new files: `spec/schema/rtos_diagnosis_case.schema.json`, `spec/schema/rtos_diagnosis_suite.schema.json`, `spec/assets/schema/rtos_diagnosis_case.schema.json`, `spec/assets/schema/rtos_diagnosis_suite.schema.json`, `tests/python/test_rtos_diagnosis_schema.py`, `tests/python/fixtures/rtos_diagnosis/phase6_stub_suite.json`.
- Allowed modified files: none by default. Stop and request main-agent intervention if `spec/schema_loader.py`, `spec/schema_validator.py`, or `tests/python/test_schema_validator.py` appear necessary.
- Forbidden files: `parser/evidence_models.py`, `desktop/evidence_export.py`, `collector/`, `tests/cpp/`, existing benchmark schemas, existing proof digest schemas, existing result validity schemas.
- Targeted regression: `python3 -m pytest tests/python/test_rtos_diagnosis_schema.py -q`; `python3 -m pytest tests/python/test_runtime_optimization_advisor.py -q`; `python3 -m pytest tests/python/test_runtime_optimization_agents.py -q`.
- Main-agent review required: yes, before P6.2.
- Allowed to enter next phase from this window: no. P6.2 requires a separate task and approval.

## P6.2 - Synthetic RTOS Regression Case Fixture

- Execution status: future independent task, not in scope for this window.
- Goal: add at least eight known-root-cause synthetic RTOS fixture cases after schema and claim-boundary review.
- Allowed new files: future `tests/python/fixtures/rtos_diagnosis/phase6_suite.json`, small fixture files, and optional minimal fixture builder only if approved.
- Allowed modified files: future minimal test or sample-data files only if approved.
- Forbidden files: `collector/`, `parser/evidence_models.py`, `desktop/evidence_export.py`, `parser/runtime_optimization_gate.py`.
- Targeted regression: future `python3 -m pytest tests/python/test_rtos_diagnosis_fixtures.py -q`; future `python3 -m pytest tests/python/test_pipeline.py -q` only if pipeline is touched.
- Main-agent review required: yes.
- Allowed to enter next phase from this window: no.

## P6.3 - Evidence Package And Proof Replay Validation

- Execution status: future independent task, not in scope for this window.
- Goal: define and test evidence package replay equivalence without changing proof digest semantics.
- Allowed new files: future `parser/rtos_diagnosis_replay.py`, `tests/python/test_rtos_diagnosis_replay.py`, only if approved.
- Allowed modified files: future minimal replay/completeness files only if approved.
- Forbidden files: `parser/evidence_models.py`, `desktop/evidence_export.py` proof digest semantics, proof digest schemas.
- Targeted regression: future `python3 -m pytest tests/python/test_repro_evidence.py -q`; future `python3 -m pytest tests/python/test_rtos_diagnosis_replay.py -q`.
- Main-agent review required: yes.
- Allowed to enter next phase from this window: no.

## P6.4 - Diagnosis Metrics And Report Output

- Execution status: future independent task, not in scope for this window.
- Goal: add a Phase 6-specific diagnosis report schema and runner with explicit claim classes.
- Allowed new files: future `spec/schema/rtos_diagnosis_report.schema.json`, mirrored asset schema, runner, and focused tests only if approved.
- Allowed modified files: future `spec/schema_loader.py` or telemetry changes only if approved.
- Forbidden files: `parser/benchmark_matrix.py` existing contract, existing benchmark schemas, `parser/evidence_models.py`.
- Targeted regression: future `python3 -m pytest tests/python/test_rtos_diagnosis_benchmark.py -q`; schema validator tests only if schema infrastructure changes.
- Main-agent review required: yes.
- Allowed to enter next phase from this window: no.

## P6.5 - Advisor-Assisted Review Variants

- Execution status: future independent task, not in scope for this window.
- Goal: compare no-advisor, heuristic, retrieval-grounded, and LLM explanation-only review modes without changing truth paths.
- Allowed new files: future review wrapper and focused tests only if approved.
- Allowed modified files: future minimal advisor/retrieval files only if approved.
- Forbidden files: `parser/runtime_optimization_gate.py` accept/reject semantics, `parser/evidence_models.py`, `desktop/evidence_export.py` proof digest semantics, parse/align/rebuild/sidecar/index/proof truth paths.
- Targeted regression: future advisor/agent tests plus future `test_rtos_diagnosis_advisor_review.py`.
- Main-agent review required: yes.
- Allowed to enter next phase from this window: no.

## P6.6 - Human Feedback Schema And Minimal Review Fixture

- Execution status: future independent task, not in scope for this window.
- Goal: add minimal human feedback schema and fixture after real review requirements are approved.
- Allowed new files: future human feedback schema, mirrored asset schema, fixture, and focused tests only if approved.
- Allowed modified files: future report schema references only if approved.
- Forbidden files: `desktop/`, `collector/`, `parser/evidence_models.py`, UI or service framework entrypoints.
- Targeted regression: future `python3 -m pytest tests/python/test_human_feedback_schema.py -q`.
- Main-agent review required: yes.
- Allowed to enter next phase from this window: no.

## P6.7 - Final Phase 6 Regression And Closeout

- Execution status: future independent task, not in scope for this window.
- Goal: run Phase 6 targeted tests, existing advisor/agent tests, full Python regression, worktree audit, and closeout documentation.
- Allowed new files: future closeout summary and result artifacts only if approved.
- Allowed modified files: none by default.
- Forbidden files: `parser/evidence_models.py`, `collector/`, frozen Phase 3/4/5 closeout archives.
- Targeted regression: future Phase 6 tests, advisor/agent tests, and `python3 -m pytest tests/python -q`.
- Main-agent review required: yes.
- Allowed to enter next phase from this window: no.
