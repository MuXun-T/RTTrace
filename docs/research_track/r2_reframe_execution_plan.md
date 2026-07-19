# R2-REFRAME Formal Prior-Art Execution Plan

## Status And Boundary

This is the approved-plan execution record for the provisional R1A-REFRAME
registry, not a final claim freeze, implementation authorization, or research
result. The requested agent configuration was `model=gpt-5.6-terra` and
`reasoning_effort=high`. The collaboration interface exposes neither a
configuration setter nor runtime-attestation field; this records the request
and limitation only and does not attest the actual model.

Implementation Authorization: R2 Documentation Only

Prior-Art Search: Authorized

Baseline Implementation: Not Authorized

Experiment Execution: Not Started

Final Claim Freeze: Forbidden

Automatic Commit: Disabled

The review tests only PC-1, SC-1, and SC-2 in
`r1a_reframed_provisional_claim_registry.md`. Contract acceptance remains
separate from diagnosis, semantic, replay, downstream-consumer, proof, and
general-security correctness.

## Claim Questions And Taxonomy

| Candidate | Search question | Direct-substitution test |
| --- | --- | --- |
| PC-1 | Does a published system/specification bind multi-artifact identity, immutable content/digests, required dependencies and policy/state constraints, then reject invalid declared inputs? | A work is adverse if policy/configuration can express the declared modeled classes without a new RTOS-specific validation mechanism. |
| SC-1 | Do validators already produce deterministic, inspectable rejection/error outcomes for declared violations? | A general failure/result vocabulary is adverse unless RTOS evidence requires a demonstrated different mechanism. |
| SC-2 | Do combined manifest, hash, provenance, and dependency policies already cover cross-artifact mismatch classes beyond single-field checks? | Existing composable policy stacks are adverse to presenting the combination itself as a research mechanism. |

Required taxonomies are artifact/package integrity; identity-bound or
content-addressed artifacts; provenance validation; fail-closed/fail-safe
validation; stale or partial input rejection; manifest/dependency consistency;
research/reproducibility packages; digital-evidence bags/chain of custody;
tamper-evident and secure logging; reproducible workflow packages; in-toto,
SLSA and artifact attestation; replay-input validation; and RTOS trace-specific
constraints. Secure logging and chain-of-custody are checked for scope rather
than presumed equivalent: recording a change is not necessarily package
acceptance validation.

## Search Protocol

Exact queries include `"identity-bound evidence package"`, `"RTOS trace"
integrity provenance`, `"stale sidecar" validation`, `"partial package"
reject`, `"cross-artifact mismatch" validation`, `"fail closed" manifest`,
and `"replay input" validation`. Synonyms include artifact/bundle/bag/package,
identity/content-address/digest/hash/checksum, dependency/material/product,
provenance/attestation/custody, and reject/default-deny/non-success. Mechanism
queries include `in-toto layout materials products REQUIRE DISALLOW`, `SLSA
provenance subject digest materials`, `BagIt manifest tagmanifest valid bag`,
`tamper-evident secure audit log`, and `reproducible package validation`.
Problem queries combine multi-artifact, stale, incomplete, substituted,
mismatch, trace/log, evidence, and replay. Exclusion terms/criteria remove
single-file checksums with no package relation, generic logging that only
records events, implementation blogs, duplicate versions, and works with no
retrievable primary method/specification from the core evidence set.

Sources are official ACM/IEEE/USENIX/Springer/Elsevier proceedings where
available, DBLP/Crossref/OpenAlex/Semantic Scholar/Google Scholar/arXiv for
discovery and metadata, and official standard/project documentation. Every
retained core record is checked against an official paper PDF, RFC, or official
normative/project specification; discovery snippets never establish a method.
Backward chaining follows the sources cited by retained core papers/specs;
forward chaining queries Crossref/OpenAlex/Semantic Scholar or publisher
related-work pages where available. A failed API, unavailable full text, or
missing result is logged as a limitation, never as absence of prior art.

## Verification Rules

`verified` means title/authors/year/venue or issuing body and method were
checked in an official full paper, RFC, or normative specification. `partially
verified` means metadata is corroborated but the decisive method was not
available or not fully inspected. `unverified` is a discovery lead only.
`excluded` means duplicate, out of scope, non-primary commentary, or a
single-artifact/logging-only method that cannot bear a package-acceptance
conclusion. Metadata requires title, authors, year, venue, DOI or official URL;
code and license are separately checked from official repositories when they
exist. No partial or unverified record may support `PROCEED`.

Nearest-neighbor selection prioritizes a mechanism that jointly accepts or
rejects a declared set of artifacts by identity/digest and dependency policy,
then covers explicit required/forbidden artifacts and deterministic outcomes.
The matrix compares Problem, Representation, Mechanism, Threat/Safety, and
Evidence. "Not stated" means only that the inspected source did not state it.

## B2 And Decision Gates

B2 records use only `quantitative eligible`, `capability-only`, or `not
comparable`. Quantitative eligibility requires public runnable code with a
verified license and a faithful same-contract mapping of input, output,
mechanism, policy, failures, and metrics; none is assumed. No B2 is downloaded,
built, adapted, or executed in R2.

`PROCEED` requires fully verified closest neighbors, a material and falsifiable
problem/mechanism delta, a later test path, and an explicit B2 branch.
`REFRAME` requires a viable but changed claim, threat model, or mechanism and
returns to R1A. `ABANDON` is required when the core is directly covered and
the residual is an engineering composition or RTOS application migration.

Stop and notify the parent for direct generic PC-1 coverage, a necessary claim
change, conflicting/unavailable decisive primary evidence, an unfair B2,
need for code/experiments, or any unauthorized change. File sequence is this
plan, verified review, novelty matrix, B2 registry, then closeout. After each
file, run `git status --short`, `git diff --name-only`, and `git diff --check`.
