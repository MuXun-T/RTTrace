# R2-REFRAME B2 Candidate Registry

## Scope

This registry is an assessment only. No candidate was downloaded, built,
adapted, run, benchmarked, or compared. It does not authorize a B2 or preserve
a research claim after the R2 `ABANDON` direction.

Classification vocabulary is closed: `quantitative eligible`, `capability-only`,
and `not comparable`. Quantitative eligibility would require public runnable
code and verified license, faithful mapping of a frozen same input/truth/output
contract, mechanism and policy parameters, failure semantics, metrics and
environment, plus validation against an author artifact/example. No candidate
meets that gate in this documentation-only review.

| ID | Source/version and available code/license record | Contract comparison | Classification | Reason and allowed use |
| --- | --- | --- | --- | --- |
| B2-R1 | Torres-Arias et al., in-toto, USENIX Security 2019; [official paper](https://www.usenix.org/conference/usenixsecurity19/presentation/torres-arias); [official implementation](https://github.com/in-toto/in-toto), Apache-2.0 inspected. | Supply-chain layout/link policy versus the provisional RTOS evidence-package acceptance contract. Neither same trace input, same diagnosis truth boundary, nor same output/reason contract is established. | `capability-only` | It is a verified adverse mechanism and may be discussed in the novelty matrix only. No faithful adapter, quantitative ranking, accuracy or superiority statement is permitted. |
| B2-R2 | SLSA Provenance v1.0, [official specification](https://slsa.dev/spec/v1.0/provenance). The specification is public; this pass did not verify a specific runnable verifier/version/license. | Artifact provenance statement/policy versus provisional package contract. No same RTOS trace, validation-state/reason, diagnosis boundary, or evaluation contract exists. | `not comparable` | Standards conformance is not a runnable same-contract baseline. No adapter or implementation is authorized. |
| B2-R3 | BagIt RFC 8493, [official RFC](https://www.rfc-editor.org/rfc/rfc8493). The RFC lists multiple implementations; no selected implementation/version/license was verified. | Payload/tag manifest validity versus provisional cross-artifact identity/dependency/state policy. It lacks the same output and truth boundary. | `not comparable` | A format validator cannot be silently substituted with a weaker self-made baseline or used quantitatively. |
| B2-R4 | Rampin et al., ReproZip JOSS 2016, [official paper](https://doi.org/10.21105/joss.00107); [project repository](https://github.com/VIDA-NYU/reprozip), repository license not verified in this pass. | Research-package capture/reopen versus provisional acceptance contract; no same input, policy or output mapping. | `not comparable` | A reproduction packer is a capability neighbor only, and no tool execution is permitted. |

## Consequence

`quantitative eligible=0`, `capability-only=1`, `not comparable=3`. Even a
future quantitative-eligible B2 could not rescue the current registry: R2 has
cut PC-1, SC-1, and SC-2 for lack of a material research delta. No B2 work
may begin from this review.

## Module Audit: B2 Registry

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| B2 candidate comparability | Official in-toto paper/repository license, SLSA specification, RFC 8493, ReproZip JOSS/repository record | `r2_reframe_b2_candidate_registry.md` | No quantitative-eligible candidate; one capability-only and three not-comparable records | Assessment only; no download/build/run/adapter/benchmark or R1B | B2 is closed with the abandoned candidate; a future different R1A/R2 would reopen its own gate |
