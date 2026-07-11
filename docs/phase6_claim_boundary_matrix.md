# Phase 6 Claim Boundary Matrix

Date: 2026-07-10

Status values are limited to `supported_within_scope`, `report_only`,
`not_supported`, and `requires_external_validation`.  A status is not a claim
of publication readiness beyond the evidence level shown here.

| claim_id | claim_text | status | supporting_artifacts | limitations | prohibited_escalation | evidence_level |
|---|---|---|---|---|---|---|
| P6-C01 | Eight synthetic RTOS diagnosis case kinds are established. | supported_within_scope | P6.2 suite and fixture tests | Synthetic only. | Real RTOS generality or accuracy. | deterministic_contract |
| P6-C02 | Deterministic fixture generation is repeatable. | supported_within_scope | P6.2 builder, suite, fixture tests | Fixed fixtures only. | Hardware-trace reproducibility. | synthetic_pipeline |
| P6-C03 | Metadata-only reference replay preservation is implemented. | supported_within_scope | P6.3 replay module and tests | Not full evidence replay. | Replay pass or equivalence. | metadata_preservation |
| P6-C04 | Deterministic diagnosis metrics reporting is repeatable. | supported_within_scope | P6.4 report fixture, canonical hash, tests | Synthetic report only. | Diagnosis correctness. | deterministic_contract |
| P6-C05 | Advisor review overlays do not modify deterministic truth. | supported_within_scope | P6.5 review fixture and security tests | Review-only overlay. | Advisor correctness or causal benefit. | adversarial_test |
| P6-C06 | The synthetic feedback pipeline satisfies its privacy contract. | supported_within_scope | P6.6 summary and privacy tests | No real participants. | Human-study result. | adversarial_test |
| P6-C07 | Phase 6 regression gate passes when the recorded P6.7 full run passes. | supported_within_scope | P6.7 manifest and regression record | A regression run is not external validation. | System correctness or generality. | deterministic_contract |
| P6-C08 | Proof/export/collector frozen boundaries are unchanged by Phase 6. | supported_within_scope | Commit and diff audit | Does not prove parity. | Proof equivalence. | deterministic_contract |
| P6-C09 | Evidence metadata retention is `1.0` for the frozen cases. | report_only | P6.4 report | Metadata-only synthetic value. | Full evidence-package equivalence. | metadata_preservation |
| P6-C10 | `preservation_complete_count=8` is reported for frozen cases. | report_only | P6.4 report | Does not produce replay passes. | Complete reconstruction. | metadata_preservation |
| P6-C11 | Advisor review coverage is reported. | report_only | P6.5 review fixture | Coverage has no quality meaning. | Advisor accuracy improvement. | deterministic_contract |
| P6-C12 | Advisor limitation and citation coverage are reported. | report_only | P6.5 review fixture | Citation is not proof. | Evidence closure or proof fact. | deterministic_contract |
| P6-C13 | Synthetic Likert descriptive metrics are reported. | report_only | P6.6 feedback summary | Eight synthetic records only. | Usability or human effect. | synthetic_pipeline |
| P6-C14 | All replay cases passed. | not_supported | P6.4 report | `replay_pass_count=0`. | Any pass-rate claim. | deterministic_contract |
| P6-C15 | Full evidence-package equivalence exists. | not_supported | P6.3/P6.4 limitations | Reference-only metadata preservation. | Evidence-package replay equivalence. | deterministic_contract |
| P6-C16 | Proof parity is established. | not_supported | P6.3/P6.4 limitations | No proof-parity experiment. | Proof-correctness claim. | deterministic_contract |
| P6-C17 | Complete trace reconstruction is established. | not_supported | P6.3/P6.4 limitations | No raw-trace reconstruction. | Full-trace correctness. | deterministic_contract |
| P6-C18 | Diagnosis accuracy improved. | not_supported | Claim boundary and reports | No accuracy evaluation. | Accuracy/benchmark result. | deterministic_contract |
| P6-C19 | Root-cause correctness improved. | not_supported | Claim boundary and reports | No empirical correctness study. | Root-cause quality claim. | deterministic_contract |
| P6-C20 | Advisor correctness improved. | not_supported | P6.5 comparison boundary | No correctness comparison. | Advisor benefit claim. | adversarial_test |
| P6-C21 | Human usability improved. | not_supported | P6.6 summary | No real participants or effect analysis. | Usability-improvement claim. | synthetic_pipeline |
| P6-C22 | Results generalize to real RTOS systems. | not_supported | P6.2-P6.4 boundaries | No real workload/hardware evidence. | Real-system generality. | real_hardware_required |
| P6-C23 | The method is anomaly-detection SOTA. | not_supported | Phase 6 scope | No ranking or baseline comparison. | SOTA claim. | real_hardware_required |
| P6-C24 | Phase 6 establishes a causal effect. | not_supported | P6.5/P6.6 boundaries | No causal design or inference. | Causal claim. | real_participant_required |
| P6-C25 | Replay works on real hardware. | requires_external_validation | Gap register | Requires hardware traces and protocol. | Synthetic replay as hardware evidence. | real_hardware_required |
| P6-C26 | The method works on real RTOS workloads. | requires_external_validation | Gap register | Requires representative workloads. | Synthetic cases as workload evidence. | real_hardware_required |
| P6-C27 | Human feedback demonstrates benefit. | requires_external_validation | Gap register | Requires real participants and study governance. | Synthetic ratings as participant evidence. | real_participant_required |
| P6-C28 | Live LLM explanations have quality. | requires_external_validation | Gap register | Current mock is not a live model. | Mock behavior as live-model evaluation. | live_model_evaluation_required |
| P6-C29 | Results generalize across platforms. | requires_external_validation | Gap register | Requires multi-platform study. | Single synthetic suite as generality. | real_hardware_required |
| P6-C30 | The system has acceptable large-scale overhead. | requires_external_validation | Gap register | No scale/performance evaluation. | Fixture runtime as production overhead. | real_hardware_required |

The P6.4 report retains `replay_pass_count=0`, `reference_only_count=8`,
`claimable_count=0`, `report_only_count=8`, and `proof_drift_count=0`.
`reference_only` is never a replay pass.  P6.6 records zero real participant
records and all human/usability/correctness claim flags as `false`.
