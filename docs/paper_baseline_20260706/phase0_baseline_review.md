# Phase 0 Baseline Review

## Phase 0 Goal

- Freeze a paper-safe baseline under `docs/paper_baseline_20260706/` only.
- Reference existing archived evidence without modifying historical formal artifacts or existing `docs/result/*` and `docs/runtime_optimization_*` evidence files.
- Prove the current focused Python baseline is runnable before any later paper-oriented work.

## Created Files

- `docs/paper_baseline_20260706/evidence_index.json`
- `docs/paper_baseline_20260706/paper_claim_boundary.json`
- `docs/paper_baseline_20260706/paper_baseline_manifest.json`
- `docs/paper_baseline_20260706/phase0a_focused_pytest.log`
- `docs/paper_baseline_20260706/phase0b_focused_pytest.log`
- `docs/paper_baseline_20260706/phase0c_focused_pytest.log`
- `docs/paper_baseline_20260706/phase0d_focused_pytest.log`
- `docs/paper_baseline_20260706/phase0e_focused_pytest.log`
- `docs/paper_baseline_20260706/focused_pytest.log`
- `docs/paper_baseline_20260706/full_regression.log`
- `docs/paper_baseline_20260706/phase0_baseline_review.md`

## Evidence Source Discovery

- Required archived sources were found for the current freeze boundary:
  - `docs/runtime_optimization_current_status_20260704.md`
  - `docs/runtime_optimization_product_evidence_20260703/*`
  - `docs/runtime_optimization_closeout_20260701/*`
  - `docs/result/deepseek_advisor_smoke_20260505.md`
  - `docs/result/运行时优化Agent_P5_DeepSeek第五场景_20260507.md`
  - `docs/evidence_proof_archive_20260506/runtime_optimization_agent/p5_advisor_openai_structured/*`
  - `docs/evidence_proof_archive_20260416/formal_10_4/inputs/google_cluster_external_dense_1gb_formal_qualified.trace`
- Known archived note retained as reference only:
  - closeout mentions a missing historical `/home/zzq/patent/windows-3/...` checksum root on this machine, but the reviewed closeout artifacts themselves passed audit.

## Boundary Summary

- `claimable`: 7 entries.
  - qualified external dense 1GB anchor input with limited wording only
  - P3 cache-hit desktop open/load benefit
  - P4 click-to-export wait reduction
  - P5 acceptance met without 1GB speedup wording
  - current focused Python regression baseline pass
  - DeepSeek smoke proof parity pass with proof digest free of advisor/LLM fields
  - Windows/Linux mandatory evidence field consistency from archived parity evidence
- `not_claimable`: 12 entries.
  - archived frozen boundaries: P4 total elapsed reduction, P5 1GB product-path speedup, P7 true parallel rebuild speedup, ticket-fast-path general speedup, online LLM truth generation
  - user-forbidden future functionality: gate-first typed advisor, RuntimeCostGraph, retrieval-grounded advisor, offline/online split, sandbox eval, adversarial eval, RTOS diagnosis
- `proposed_only`: 6 entries.
  - Phase 1 through Phase 6 remain proposal-only and are not part of the frozen baseline

## Focused Pytest Status

- Command: `python3 -m pytest tests/python/test_runtime_optimization_advisor.py tests/python/test_runtime_optimization_agents.py -q`
- Phase 0A: pass, `99 passed in 7.33s`
- Phase 0B: pass, `99 passed in 7.36s`
- Phase 0C: pass, `99 passed in 7.30s`
- Phase 0D: pass, `99 passed in 7.33s`
- Phase 0E: pass, `99 passed in 7.32s`
- Canonical log path: `docs/paper_baseline_20260706/focused_pytest.log`
- Final Phase 0E refresh: completed; canonical log now reflects the last focused rerun

## Full Regression Status

- Main-agent final full regression command: `python3 -m pytest tests/python -q`
- Command selection check: `pyproject.toml`, `README.md`, `docs/`, and `tests/` exist; `Makefile`, `tox.ini`, `noxfile.py`, and `.github/workflows/` were not present. Existing validation docs use `python3 -m pytest tests/python -q` as the canonical Python regression baseline.
- Result: pass, `547 passed, 79 subtests passed in 112.47s`
- Exit code: `0`
- Log path: `docs/paper_baseline_20260706/full_regression.log`

## Historical Artifact Modification Check

- Historical artifacts modified: `false`
- Existing closeout, formal, result, schema and evidence archive files were hashed or referenced only.

## Risks And Phase 1 Preconditions

- P3 remains limited by `cold=1`, `warm=3`, and `hot_metadata_only` not being full UI open.
- P4 remains limited by `repeat=1` and non-claimable total elapsed regression.
- Any later Phase 1 work must keep proof facts clean, preserve current focused regression pass, and avoid rewriting archived evidence.
- Any later full-paper claim expansion must wait for main-agent full regression plus new phase-specific evidence.

## Main-Agent Review

- Full regression execution: completed by Main Agent.
- Final review conclusion: Phase 0 baseline freeze is complete with limits. The work only added `docs/paper_baseline_20260706/*`, did not modify historical formal artifacts, and did not implement Phase 1-6 functionality.
