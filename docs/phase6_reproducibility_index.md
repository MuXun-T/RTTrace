# Phase 6 Reproducibility Index

Date: 2026-07-10

Run these commands from the repository root.  They use only frozen synthetic
fixtures and local code; none requires a network, environment secret, real
participant data, real hardware, or a live LLM.  Command output files are
placed under `/tmp` and are not repository artifacts.

## Deterministic Phase 6 Commands

```bash
python3 tool/build_rtos_diagnosis_cases.py --output /tmp/phase6_synthetic_suite.json --overwrite
python3 tool/run_rtos_diagnosis_replay.py --suite /tmp/phase6_synthetic_suite.json --output /tmp/phase6_replay.json --overwrite
python3 tool/build_rtos_diagnosis_report.py --suite /tmp/phase6_synthetic_suite.json --output /tmp/phase6_report.json --overwrite --report-id phase6-p6-4-fixture
mkdir -p /tmp/phase6_advisor_reviews
python3 tool/run_rtos_diagnosis_advisor_review.py --report /tmp/phase6_report.json --variant advisor_disabled --output /tmp/phase6_advisor_reviews/advisor_disabled.json --overwrite --review-id phase6-p6-5-advisor-review
python3 tool/run_rtos_diagnosis_advisor_review.py --report /tmp/phase6_report.json --variant deterministic_template --output /tmp/phase6_advisor_reviews/deterministic_template.json --overwrite --review-id phase6-p6-5-advisor-review
python3 tool/run_rtos_diagnosis_advisor_review.py --report /tmp/phase6_report.json --variant retrieval_grounded_review --output /tmp/phase6_advisor_reviews/retrieval_grounded_review.json --overwrite --review-id phase6-p6-5-advisor-review
python3 tool/run_rtos_diagnosis_advisor_review.py --report /tmp/phase6_report.json --variant mock_llm_explanation --mock-response tests/python/fixtures/rtos_diagnosis/advisor_reviews/mock_responses.json --output /tmp/phase6_advisor_reviews/mock_llm_explanation.json --overwrite --review-id phase6-p6-5-advisor-review
python3 tool/build_rtos_diagnosis_human_feedback_summary.py --feedback tests/python/fixtures/rtos_diagnosis/human_feedback/phase6_synthetic_feedback.json --source-report /tmp/phase6_report.json --source-review-dir /tmp/phase6_advisor_reviews --synthetic-only --output /tmp/phase6_human_feedback_summary.json --overwrite --summary-id phase6-p6-6-synthetic
python3 tool/build_phase6_closeout_manifest.py --output /tmp/phase6_closeout_manifest.json --summary --overwrite --manifest-id phase6-p6-7-closeout --test-passed <passed> --subtests-passed <subtests>
```

The closeout builder must produce the same canonical manifest for the same
explicit inputs.  It must not add a clock-derived identifier or timestamp;
`manifest-id` is explicit.  Its output must use repository-relative artifact
paths and must retain the canonical P6.4 report hash
`fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`.

## Validation Commands

```bash
python3 -m pytest tests/python/test_phase6_closeout.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_schema.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_fixtures.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_replay.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_report.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_advisor_review.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_advisor_review_security.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_human_feedback.py -q
python3 -m pytest tests/python/test_rtos_diagnosis_human_feedback_privacy.py -q
python3 -m pytest tests/python/test_runtime_optimization_advisor.py -q
python3 -m pytest tests/python/test_runtime_optimization_agents.py -q
python3 -m pytest tests/python/test_deepseek_advisor_smoke.py -q
python3 -m pytest tests/python/test_schema_validator.py -q
python3 -m pytest tests/python/test_repro_evidence.py -q
python3 -m pytest tests/python/test_clipped_trace_completeness.py -q
python3 -m pytest tests/python -q
```

The committed P6.7 manifest records the final full-regression counts, not a
hard-coded count in the builder.  Before final freeze, compare that record
with the actual final `tests/python` result and keep `failures=0`, `skips=0`,
and `warnings=0` only when the command output establishes those facts.

## Audit Inputs And Expected Boundaries

- Frozen commits, required artifact paths, schema mirror checks, P6.4 hash,
  and all closeout invariants are checked by the focused closeout test.
- The committed P6.4 report establishes `replay_pass_count=0`,
  `reference_only_count=8`, `all_replay_passed=false`, `claimable_count=0`,
  `report_only_count=8`, and `proof_drift_count=0`.
- The committed P6.6 summary establishes eight synthetic feedback records,
  zero real participant records, `contains_real_participant_data=false`, and
  `pipeline_validation_only=true`.
- The manifest must exclude raw feedback records, participant-level data,
  proof-hash inputs, proof-digest write paths, live-LLM results, correctness
  rankings, secrets/tokens, absolute paths, and path traversal.

See `docs/phase6_claim_boundary_matrix.md` for the interpretation boundary and
`docs/phase6_p6_7_closeout.md` for unresolved external validation gaps.
