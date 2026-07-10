# Phase 6 P6.5 Advisor Review Overlay

## Scope

P6.5 is a separate, read-only review overlay over a validated P6.4 report. It is not a diagnosis engine, root-cause source, replay verifier, proof producer, correctness judge, tool executor, or remediation agent. The P6.4 report stays the only source of deterministic Phase 6 metadata in this scope.

## Contract

`rtos-diagnosis-advisor-review-v1` is a strict JSON contract with an immutable source report ID and SHA-256 hash. The module validates the P6.4 report before and after construction, snapshots it through canonical P6.4 JSON, and builds output only from an allowlist of case ID, case kind, reference-only state, replay-pass flag, and bounded evidence retention metadata. It never reads root-cause, affected-entity, proof, raw trace, environment, network, or filesystem data.

The overlay has separate input-integrity, truth-invariance, safety, and review-summary fields. All truth and mutation counters are zero. Output claim class is `report_only`; citations are explicitly `not_proof=true` case references and never evidence closure or proof facts. Every completed or fallback case must carry at least one allowlisted citation.

## Variants

- `advisor_disabled`: eight disabled rows, no review content, and zero coverage.
- `deterministic_template`: deterministic per-case scope, limitation, and P6.4 case citation rows. It states that reference-only is not replay pass.
- `retrieval_grounded_review`: only exact, allowlisted P6.4 case IDs may be supplied as review references. Retrieved references are explanatory only and get `not_proof=true`; invalid or incomplete input falls back to the deterministic template.
- `mock_llm_explanation`: a fixed in-memory mock payload is parsed with a strict local schema. Its case binding and content are checked; malformed, extra, missing, unsafe, injected, or unsupported input is rejected and falls back to a deterministic template. It does not use a real LLM and records `llm_used=false`.

No live API, API key, environment lookup, network access, file access, tool invocation, command execution, or action execution exists in this module.
P6.6 human feedback and P6.7 closeout are not part of P6.5.

## Comparison Boundary

`build_variant_comparison` reports only schema validity, deterministic reproducibility, review coverage, limitation coverage, citation presence, abstention, fallback, and adversarial rejection. It deliberately has no diagnosis accuracy, root-cause accuracy, trace truth, proof correctness, or replay-success comparison.

## Failure Handling

Invalid retrieval or mock inputs are not emitted. They set schema-invalid and rejected-invalid counts, then return a deterministic fallback overlay without changing the source report. Prompt-injection patterns are counted as blocked. Unsupported output cannot be emitted because the public output schema has `additionalProperties=false` at every object level and the module validates the generated result before returning it.
