# R2-REFRAME Verified Prior-Art Review

## Scope, Method, And Limits

This formal review concerns only the provisional identity-bound,
fail-closed evidence-package registry (PC-1, SC-1, SC-2). It does not find
novelty, establish a security guarantee, authorize a baseline, or change any
frozen implementation contract. "Direct substitution" below means that a
predeclared configuration of existing work can implement the claimed generic
contract elements; it does not assert that it is a drop-in implementation of
the frozen project.

Discovery searched ACM, IEEE, USENIX, Springer/Elsevier landing pages, DBLP,
Crossref, OpenAlex, Semantic Scholar, Google Scholar, arXiv, and official
standards/project documentation using the planned exact, synonym, mechanism,
problem, and exclusion queries. Retention used official full papers, RFCs, or
official normative specifications. Crossref and OpenAlex were used for
metadata/discovery only. This environment lacked `jq`; OpenAlex result parsing
therefore did not yield an additional retained source. Some publisher pages or
APIs were inaccessible. Those failures are limitations, not negative evidence.

Backward chaining: ReproZip's inspected JOSS PDF identifies the SIGMOD
ReproZip paper; in-toto's official paper/specification connects supply-chain
integrity to prior update/provenance work; BagIt and W3C PROV list normative
references. Forward-chain attempts used Crossref/OpenAlex/Semantic Scholar
discovery and official project pages; no forward result was treated as method
evidence without a primary source. The retained direct-neighbor conclusion does
not rely on unavailable forward citations.

## Core Records

| ID / status | Bibliographic record and sources | Problem, representation, and mechanism | Threat/safety and evidence | Code / license | Relevance |
| --- | --- | --- | --- | --- | --- |
| V1 `verified` | Santiago Torres-Arias, Hammad Afzali, Trishank Karthik Kuppusamy, Reza Curtmola, Justin Cappos, *in-toto: Providing farm-to-table guarantees for bits and bytes*, 2019, 28th USENIX Security Symposium; [official paper and venue](https://www.usenix.org/conference/usenixsecurity19/presentation/torres-arias). Metadata and full method: official USENIX PDF, inspected. | Software-supply-chain integrity; signed layout and link metadata containing material/product artifact hashes; authorized functionaries, thresholds, and artifact rules link steps. The official specification defines `MATCH`, `REQUIRE`, `DISALLOW`, `CREATE`, `DELETE`, and `MODIFY`; verification errors on unmet rules. | Threat is unauthorized/tampered supply-chain artifacts. Paper evaluates 30 compromise scenarios and deployment examples; this is not RTOS diagnosis or an invalid-package false-accept experiment. | [in-toto/in-toto](https://github.com/in-toto/in-toto); Apache-2.0 inspected in official repository. | PC-1: adverse direct generic mechanism for identity/digest/dependency policy and required/forbidden artifact rejection. SC-1: verification errors are inspectable outcomes, though no match to the registry's reason taxonomy is claimed. SC-2: directly undermines presenting the combined fields alone as a material mechanism. |
| V2 `verified` | J. Kunze, J. Littman, E. Madden, J. Scancella, C. Adams, *The BagIt File Packaging Format (V1.0)*, 2018, RFC 8493; [official RFC](https://www.rfc-editor.org/rfc/rfc8493). Metadata and full normative text inspected. | Reliable digital-content storage/transfer; hierarchical bag with payload, `manifest-algorithm.txt`, optional `tagmanifest-algorithm.txt`, metadata, and optional fetch file. Section 3 defines complete/valid bags and validation of required payload entries and manifests. | It provides integrity against corruption but explicitly says it is not designed against active attacks. RFC examples/specification are evidence, not RTOS testing. | Implementations are listed by RFC but no single canonical code/license was verified. | PC-1: adverse to partial-package and checksum/manifest fields. SC-1: supports valid/invalid package outcomes, not the proposed reason codes. SC-2: shows manifest plus tag-manifest composition is established. It alone does not express all source/dependency policy. |
| V3 `verified` | SLSA community, *SLSA Provenance, v1.0*, 2023, SLSA specification; [official specification](https://slsa.dev/spec/v1.0/provenance). Metadata/issuing body and normative method inspected from official specification. | Verifying where/when/how an artifact was produced; an in-toto statement binds `subject` artifact names and digests to `buildDefinition`, `resolvedDependencies`/materials, builder and run details. Consumers apply a policy to provenance. | Supply-chain provenance/threat context. The specification documents schema and verification intent, not a controlled RTOS package experiment. | Public specification repository was identified, but this pass did not independently verify a repository license. | PC-1: a configurable provenance-policy mechanism binds subject digest and dependencies; it is a direct generic substitute for those contract fields. SC-1: policy verification can yield acceptance/failure but not the registry's exact reason vocabulary. SC-2: adverse to the claimed field combination. |
| V4 `verified` | Remi Rampin, Fernando Chirigati, Dennis Shasha, Juliana Freire, Vicky Steeves, *ReproZip: The Reproducibility Packer*, 2016, Journal of Open Source Software 1(8):107, [10.21105/joss.00107](https://doi.org/10.21105/joss.00107). Metadata and official JOSS PDF inspected. | Reproducible research package; system-call tracing captures binaries, files, dependencies, configuration and execution information into `.rpz`; ReproUnzip provides unpacking modes. | Evidence is documentation/examples and cited case-study material, not fail-closed cross-artifact validation or RTOS trace tests. | [VIDA-NYU/reprozip](https://github.com/VIDA-NYU/reprozip); license was not verified in this pass because the requested raw repository license URL returned 404. The JOSS article is CC-BY-4.0. | PC-1: established package/dependency capture/reopen neighbor, but not itself a direct substitute for all declared validation policy. SC-1/SC-2: supporting adverse context only. |
| V5 `verified` | W3C Provenance Working Group, *PROV-DM: The PROV Data Model*, 2013, W3C Recommendation; [official recommendation](https://www.w3.org/TR/prov-dm/). Issuing body and full normative recommendation inspected. | Interoperable provenance representation of entities, activities, agents, generation, usage, derivation and bundles; companion constraints validate provenance records. | Provenance representation/constraint model, not cryptographic package integrity or a package acceptance experiment. | Standard, no canonical implementation/license recorded. | PC-1: broad representation neighbor for source/artifact relations; not a stand-alone substitute for cryptographic validation. SC-2: establishes relation/constraint composition as prior art. |
| P1 `partially verified` | Bruce Schneier, John Kelsey, *Secure audit logs to support computer forensics*, 1999, ACM Transactions on Information and System Security 2(2), [10.1145/317087.317089](https://doi.org/10.1145/317087.317089). Metadata checked with Crossref; authoritative full method was not retrieved. | Secure/tamper-evident audit logging; no full-method statement used here. | Not used for a package-acceptance or RTOS claim. | No code/license verified. | Taxonomy coverage only. A tamper-evident log records history; without full method it cannot be asserted to accept/reject cross-artifact packages. |
| P2 `partially verified` | Justin Samuel, Nick Mathewson, Justin Cappos, Roger Dingledine, *Survivable key compromise in software update systems*, 2010, 17th ACM Conference on Computer and Communications Security, [10.1145/1866307.1866315](https://doi.org/10.1145/1866307.1866315). Crossref metadata and official project lead were located, but the requested paper PDF endpoint returned HTML and no authoritative full method was inspected. | Update metadata/dependency freshness is relevant by title/scope only. | No mechanism or experiment relied upon. | Project source/license not verified in this pass. | Taxonomy/citation-chain lead only; excluded from the direct-coverage conclusion. |

## Cross-Domain Questions

| Required question | Verified answer | Limit and effect |
| --- | --- | --- |
| Can in-toto/SLSA/attestation directly replace the generic contract? | Yes for the core generic fields: V1 verifies artifact hashes across materials/products, required/forbidden artifacts and threshold/policy rules; V3 binds artifact subject digests and resolved dependencies to a provenance policy. | They do not automatically import project-specific state/reason names. That naming difference is not a material mechanism absent a demonstrated new validation algorithm or constraint class. |
| Does digital-evidence chain-of-custody cover identity-bound packages? | BagIt V2 covers hierarchical package manifests and complete/valid-package checks; PROV V5 represents provenance/bundles. | Neither is claimed here to supply the whole security/threat model. Their existence prevents treating package identity/manifest binding as a new problem by itself. |
| Do secure/tamper-evident logs cover trace/package binding? | P1 is only partial and is not used affirmatively. | Logging records can be related but are not presumed equivalent to acceptance validation; no absence claim follows. |
| Is fail-closed validation already a mature model? | V1 uses rule verification that returns error for unconsumed/disallowed/missing artifacts; V2 defines complete/valid packages. | The review does not claim universal safety. It finds the proposed default-deny contract pattern established. |
| Are stale sidecars, partial packages, and cross-artifact mismatches a new problem? | V1's inter-step materials/products and V2's manifests directly address generic alteration, missing and package-integrity classes; V3 binds subjects/dependencies in provenance. | A stale sidecar may require a project policy mapping, but that is configuration of existing binding/policy mechanisms on this record. |
| Are layered reason-coded rejections a material delta? | No verified source here uses the exact R1A vocabulary. | Exact labels and deterministic sorting are output-format choices. No evidence establishes a new material mechanism; SC-1 cannot carry the primary contribution. |
| Is the present claim merely checksum/manifest/state-machine composition? | On the verified record, yes: its elements are independently and jointly represented by established package, attestation and policy systems. | No evidence of an RTOS-specific constraint that generic policy cannot express was found; lack of a found paper is not an absence proof. |
| Does RTOS trace create an unaddressed special constraint? | The frozen project has fixed simulator-trace and replay boundaries, but no R1A mechanism demonstrates a constraint inexpressible as an artifact/dependency/policy rule. | RTOS is an input context only in this review; it cannot support PC-1 materiality. |

## Screening Ledger And Bias Controls

Retained ledger: `verified=5`, `partially verified=2`, `unverified=0`,
`excluded=6` (duplicate ReproZip venue record; former slicing/index results
outside the reframed claim; single-file checksum/content-addressing leads;
generic logging-only leads; discovery/blog results; RTOS trace-search results
without a package-validation method). Counts describe screened records, not all
literature.

Adverse evidence was intentionally retained from software supply-chain,
archival/digital-package, reproducibility, provenance, and secure-log domains.
No conclusion is based only on an abstract, search snippet, or an unverified
claim of absence. The direct-substitution conclusion rests on V1--V3 official
methods. It does not claim that any source proves semantic correctness,
diagnosis correctness, replay correctness, proof correctness, universal
security, or that an unimplemented R1A package accepts/rejects any input.

## Module Audit: Verified Review

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| Formal cross-domain prior-art review | Official USENIX in-toto paper/spec; RFC 8493; SLSA v1.0 specification; JOSS ReproZip PDF; W3C PROV-DM; Crossref and official/discovery pages for partial leads | `r2_reframe_verified_prior_art_review.md` | Five full-method/spec verified records; direct generic coverage established by V1--V3; two partial leads are not dispositive | Documentation only; no code, B2, experiment, or frozen-file modification | Exact RTOS-specific mechanism is not registered; no fair B2 follows from literature review |
