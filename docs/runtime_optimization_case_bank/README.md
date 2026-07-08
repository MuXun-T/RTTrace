# Runtime Optimization Case Bank

This directory stores manual recommendation cases for Phase 3 retrieval-grounded advisor behavior.

- The case bank is grounding for advisor recommendations only. It is not trace truth, proof truth, or root-cause truth.
- Retrieved cases, `retrieved_case_refs`, `case_similarity_features`, and `evidence_context` must not enter `proof_digest`, `proof_hash`, or proof-hash input payloads.
- Retrieval results cannot bypass the deterministic validation gate. The advisor may recommend or abstain; execution still depends on deterministic gate acceptance.
- The advisor must fail closed when telemetry is missing, when retrieved cases conflict, or when an unsafe case is matched for a high-risk action.
- Stable `case_id` values are preserved for regression stability and historical references. If a legacy `case_id` name and the current case meaning diverge, treat `label`, `expected_decision`, `risk_level`, and `case_tags` as the semantic source of truth for this case bank.
- If evidence is incomplete, prefer `needs_more_data` labels or explicit placeholder-style source references such as `proposed_case`, `placeholder`, or `needs_measurement`. Do not turn retrieval cases into unsupported performance claims.
