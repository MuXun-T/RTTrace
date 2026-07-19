# R2-REFRAME Five-Layer Novelty Matrix

## Reading Rule

This matrix compares the provisional R1A records to verified methods. A `not
stated` cell means the inspected source did not state that item; it never means
the item is absent from all prior art. The matrix does not establish novelty,
correctness, security, or later empirical results.

| Work | Problem | Representation | Mechanism | Threat / safety boundary | Evidence | Impact on PC-1 / SC-1 / SC-2 |
| --- | --- | --- | --- | --- | --- | --- |
| R1A provisional registry | Silent acceptance of partial, stale, corrupt or mismatched multi-artifact RTOS-trace packages | Declared artifact/source identity projections, bytes/content references, checksums, dependencies, state/reason fields | Layered identity/checksum/dependency/state validation yielding `contract-accepted` or non-success | Only modeled input conditions; excludes semantics, diagnosis, replay, proof and arbitrary attackers | None; mutation corpus is future-only | Candidate only, not a result |
| V1 in-toto | Supply-chain artifact integrity across steps | Signed layout and link metadata with artifact hashes, materials, products, authorized functionaries | Rule/policy verification: matching, required/forbidden artifacts, creation/deletion/modification and thresholds | Unauthorized/tampered artifacts; rule errors, not a claim of arbitrary security | Official paper's 30 compromise scenarios and deployment examples | PC-1 core representation and policy/rejection mechanism are directly covered at a generic level. SC-1 exact labels differ only as presentation. SC-2's combined-field idea is covered. |
| V2 BagIt | Reliable storage/transfer of arbitrary digital content | Hierarchical payload/tag files, payload and tag manifests, checksums | Complete/valid-bag validation against required manifests and entries | Integrity/corruption, explicitly not active-attack security | Normative RFC and format examples | PC-1 partial/manifest/checksum portion is occupied. SC-1 valid/invalid outcome pattern is established. SC-2 shows manifest composition is established. |
| V3 SLSA Provenance | Verify artifact production provenance | In-toto statement with subjects/digests, build definition, materials/resolved dependencies, builder/run data | Consumer policy evaluates provenance statement and dependencies | Supply-chain provenance, not trace diagnosis | Normative schema/specification | PC-1 source/artifact identity and dependency binding are directly represented. SC-2 combined attestation/policy is adverse. |
| V4 ReproZip | Package computational experiments with dependencies for reproduction | `.rpz` containing binaries, files, dependencies, configuration and execution information | System-call-traced pack and multiple unpack modes | Reproducibility packaging, not declared invalid-package validation | JOSS paper, documentation/examples | Neighbor for package/dependency concern, but not decisive direct coverage. |
| V5 PROV-DM | Exchange provenance information across heterogeneous systems | Entities, activities, agents, derivations, bundles and constraints | Provenance data-model/constraint representation | Records trust/provenance relations, not cryptographic validation by itself | W3C Recommendation | Broad representation neighbor; reinforces that source/artifact relation modeling is established, but not direct PC-1 substitute alone. |
| P1 secure audit logging (partial) | Preserve audit history for forensics | Log records | Full method not inspected | Tamper-evident logging scope unverified here | Metadata only | Neither positive nor negative PC-1 conclusion; it prevents an unsupported statement that logging is absent. |

## Claim-Level Decision

| Candidate | Nearest work and relation | Material problem/mechanism delta after verification | Disposition |
| --- | --- | --- | --- |
| PC-1 primary | V1 in-toto is nearest: declared hash-bound artifacts, dependencies and required/forbidden policy with rejection. V3 SLSA independently binds subject digest and dependencies to consumer policy; V2 packages/checks manifests. | None demonstrated. The residual is selecting/projecting RTOS trace artifacts, assigning local state/reason names, and composing established fields. The registry provides no evidence that a generic policy cannot express the modeled classes. | Cut. The current primary candidate is an engineering/application composition, not a retained research claim. |
| SC-1 auditable non-success | V1/V2 have verification failure/validity outcomes. | Deterministic local reason-code vocabulary/order is an output-contract detail; no material validation mechanism is registered. | Cut as independent research candidate. It may remain an engineering acceptance-contract property only. |
| SC-2 combined-contract coverage | V1 combines hash-bound artifact relations with policy; V2 combines payload/tag manifests; V3 combines subject and dependency attestation. | The proposed comparison against existence/checksum/manifest-only validators is an internal B1 isolation, not a cross-domain mechanism delta. | Cut as research candidate. It may remain a bounded engineering test if separately authorized later. |

## Explicit Findings

1. Generic attestation is not a literal drop-in implementation for frozen RTOS
   package/replay code, but V1/V3 can express the primary generic fields by
   configuration. That is sufficient to remove a material PC-1 distinction as
   registered.
2. Digital-forensics custody and secure logging were searched rather than
   ignored. The verified package/provenance results already decide the generic
   claim; partial logging/custody leads are not stretched into either coverage
   or absence.
3. Fail-closed/default-deny validation exists as V1 rule failure and V2
   valid-bag checking. This does not establish a universal security property.
4. Stale sidecar, partial package and cross-artifact mismatch are important
   engineering classes, but the registry gives no demonstrated new formal
   representation, validator, or threat constraint beyond existing artifact
   policy/provenance/package mechanisms.
5. Package acceptance remains unrelated to diagnosis, semantic, replay,
   downstream-consumer, and proof correctness. No future mutation result may
   erase that boundary.

## Matrix Conclusion

The formal review supports `ABANDON`, not `PROCEED` or `REFRAME`. A reframe
would need a newly registered, falsifiable, RTOS-specific mechanism or threat
constraint that is shown not to be an ordinary policy configuration; none is
available in the present registry. This result is conservative: it does not
assert that no possible future problem exists, only that this candidate cannot
advance as a material research contribution.

## Module Audit: Matrix

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| Five-layer and claim matrix | V1-V5 verified records and P1 partial record in `r2_reframe_verified_prior_art_review.md`; frozen provisional registry | `r2_reframe_novelty_matrix.md` | Direct generic coverage removes PC-1 material delta; SC-1/SC-2 are output/B1 composition only; conservative result `ABANDON` | Documentation only; no claim freeze, implementation, B2, experiment or R1B | A future, materially different R1A problem would require a new registry and R2; it is not proposed here |
