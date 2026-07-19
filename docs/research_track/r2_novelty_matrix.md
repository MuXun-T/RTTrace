# R2 Novelty Matrix

## Status

`No` below means the inspected source does not state the property. It does not
mean no such work exists and it does not establish novelty.

## Five-Layer Matrix

| Work | Problem | Representation | Mechanism | Safety | Evidence | R2 delta reading |
| --- | --- | --- | --- | --- | --- | --- |
| R1A provisional | Bounded RTOS diagnostic evidence package | Raw/package events, sidecar, vector budget, frozen frontier, identity | Budget-before-read closure under frozen order/rules, validated reopen | Required mismatch/nonavailability is non-success | None yet; independent labels, invalid controls, and cost ledger are future requirements | Pending, not a result |
| P1 Agrawal/Horgan | Debugging | Execution/dependence slice | Dynamic slicing | Not inspected | Not inspected | Broad C1 overlap |
| P2 Nagarajan et al. | Failed-run debugging under retained-history limit | In-memory dynamic dependence graph, circular buffer | Online dependence capture, selective storage, on-demand slice | No package validation in inspected abstract | Abstract reports trace/buffer/time/slowdown observations | Closest C1; stated R1A frontier/ledger difference is not shown material |
| P3 Perera et al. | Concurrent dynamic slicing | Pi-calculus configurations/executions | Causal backward/forward slicing, Galois connection | Formal causal consistency, not package validation | Agda formalisation and example | Causal/replay overlap; artifact contract difference unpositioned |
| P4 Lee et al. | Post-silicon debug | On-chip signal sequences | Dynamic sequence slicing | Not inspected | Not inspected | Embedded selective-slicing overlap |
| P5 Rampin et al. | Reproducible experiments | Files/binaries/dependencies/configuration package | System-call-traced pack/unpack | No inspected reason-coded rejection | Tool/documentation/examples | C2 package/reopen overlap; narrow integrity distinction remains unverified |
| P6 Chirigati et al. | ReproZip reproduction | ReproZip package | ReproZip capture/replay | Not inspected | Not inspected | P5 venue corroboration |
| P7 Guo | Portable experiment packages | System-call-interposed package | CDE package construction | Not inspected | Not inspected | C2 package lead |

## Required Mechanism Questions

| Question | C1 sources | C2 sources | C3 sources | Answer |
| --- | --- | --- | --- | --- |
| Budgeted dependency selection? | P2 yes: fixed buffer/selective dependency storage; P1/P3/P4 support slicing line | none | none | Existing C1 literature prevents a generic budgeted-selection claim; exact vector budget/frontier is unresolved. |
| Explicit frontier/degraded state? | None in inspected sources | None | None | No direct match verified; absence is not a material-delta proof. |
| Source/package identity binding? | none | P5 packages dependencies, but inspected text does not specify R1A binding rule | none | No verified same trace-package rule. |
| Fail closed on required mismatch? | none | none in inspected P5 | none | No direct coverage found; C2 remains engineering contract pending new search. |
| Diagnosis/evidence preservation tested? | P2 efficiency/capture abstract; P3 formal slice relation | P5 reproduction support | none | R1A still lacks independent-label evidence; no comparison claim permitted. |
| Index build and net cost counted? | none | none | no same-contract paper verified | Missing coverage cannot retain C3 as a research candidate. |

## Candidate Disposition

| Candidate | Closest work | Prior-art impact | Disposition |
| --- | --- | --- | --- |
| C1 | P2, with P3/P4 adverse support | Broad bounded dependency selection is occupied; materiality of the stated additions is not established. | Do not carry unchanged; a narrower candidate requires new R1A and R2. |
| C2 | P5, with P6/P7 package support | Package/dependency capture/reopen is occupied; the narrow fail-closed rule has inadequate search/evidence. | Do not carry unchanged; new R1A plus targeted R2 if pursued. |
| C3 | Generic trace/index leads, no same-contract full method | Index net-cost accounting is an engineering question without a verified delta. | Cut from current candidate set. |

## Conclusion And Module Audit

The matrix supports `REFRAME`, not `PROCEED`: C1 has close adverse work, C2
requires a changed and newly searched problem, C3 has no verified delta. This
does not predict the outcome of a new registry.

| item | sources_checked | files_changed | verification_result | boundary_status | open_issue |
| --- | --- | --- | --- | --- | --- |
| Five-layer and mechanism matrix | P1-P7 core records; frozen R1A contract | `r2_novelty_matrix.md` | Affirmative statements limited to inspected sources; unresolved cells retained | Documentation-only; no claim freeze/R1B | New R1A and complete new R2 required for any revised C1/C2 |
