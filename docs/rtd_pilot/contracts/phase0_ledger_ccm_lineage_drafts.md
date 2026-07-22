# RTD-Pilot Phase 0 Ledger, CCM, CIR, and Lineage Concept Drafts

This document freezes concepts only. It creates no Schema, validator, record,
hash, runtime object, Injection Ledger, OAR, CVR, CCM, CIR, Evidence Lineage,
diagnostic audit, verdict, or evaluation result. Every future object is
append-only or versioned: correction creates a new version with its prescribed
`prior_*_ref`; no authoritative fact is silently overwritten.

## Object-level permission matrix

"Read-only consumer" describes a component's permission for a particular
upstream object. It must not be used to describe the whole component as
read-only for every object.

| Component / role | Permitted writes | Permitted reads | Explicitly forbidden writes |
| --- | --- | --- | --- |
| Collector/config exporter | raw Trace artifact; raw collection configuration; collector counter report | build/config | Ledger; Observer truth; CIR canonical record; lineage; diagnosis |
| Parser/decoder | `decoder_integrity_report`; parsed events | raw Trace; dictionary; CCM | canonical CCM/CIR; Ledger; Observer truth; lineage; diagnosis relevance; verdict |
| Canonical manifest assembler | versioned CCM; versioned CIR | configuration facts; collector counters; decoder integrity; Observer boundary/integrity reports | manifestation truth; lineage; diagnosis relevance; verdict |
| Deterministic rebuild | derived task/resource/IRQ objects; Evidence Lineage | parsed events; CCM; CIR; untrusted windows | Ledger; Observer truth; diagnosis relevance; diagnosis verdict |
| Deterministic diagnoser | diagnostic relevance audit; relevance audit; `SUPPORTED`/`REFUTED`/`UNKNOWN`/`OOD` verdict | derived objects; lineage; CCM; CIR; fixed rules; candidate binding | Ledger; Observer adjudication; original lineage; CCM/CIR |
| Independent Observer service | raw Observer artifacts; Observer measurement report | independent physical signals | diagnosis; lineage; CCM configuration facts |
| Observer adjudicator | Observer Adjudication Record (OAR) | Observer artifacts; frozen predicate | Ledger configuration; diagnosis output |
| Capture quality adjudicator | Capture Validity Record (CVR) | raw artifact status; Observer status; CIR evidence | manifestation conclusion; diagnosis |
| Evaluator | evaluation result; score records | sealed truth; frozen diagnosis; split; scorer | Ledger; Observer; CCM/CIR; lineage; diagnosis |
| Agent | optional candidate recommendation or natural-language report, only after separate authorization | explicitly allowlisted read-only material | Ledger; OAR; CVR; CCM; CIR; lineage; relevance; verdict; evaluation result |
| Report/export | immutable presentation/export artifact | all frozen outputs | all source objects and verdict |

No component may write truth, write a diagnosis result, and evaluate its own
correctness. Evaluator output is scored only against sealed OAR/Case truth and
frozen diagnosis output.

## Future-object authority register

Each future object has exactly one authoritative writer and the following
frozen writer contract.

| Object | Authoritative writer | Source artifacts | Append-only or versioned | Read-only consumers | Forbidden writers |
| --- | --- | --- | --- | --- | --- |
| Injection Ledger | isolated capture administration service | approved capture administration inputs and configuration identity | append-only/versioned Ledger | assembler; rebuild; diagnoser; report/export | parser/decoder; assembler; rebuild; diagnoser; Observer; adjudicators; evaluator; Agent |
| raw collection configuration | collector/config exporter | build/config facts and collector settings | immutable/append-only | assembler; CVR adjudicator; report/export | capture administration; parser/decoder; rebuild; diagnoser; Observer; adjudicators; evaluator; Agent |
| raw Trace artifact | collector/config exporter | collector recorder output and capture configuration identity | immutable/append-only | parser/decoder; assembler as integrity input; CVR adjudicator; report/export | capture administration; parser/decoder; assembler; rebuild; diagnoser; Observer; adjudicators; evaluator; Agent |
| collector counter report | collector/config exporter | collector counters and collector configuration | append-only | assembler; CVR adjudicator; report/export | parser/decoder; rebuild; diagnoser; evaluator; Agent |
| parsed events | parser/decoder | raw Trace and dictionary | append-only | rebuild; assembler as integrity input; report/export | capture administration; rebuild; diagnoser; evaluator; Agent |
| `decoder_integrity_report` | parser/decoder | raw Trace, dictionary, decode outcome | append-only | assembler; CVR adjudicator; report/export | capture administration; rebuild; diagnoser; evaluator; Agent |
| CCM | canonical manifest assembler | configuration facts and collector configuration | versioned | parser/decoder; rebuild; diagnoser; CVR adjudicator; report/export | collector exporter; parser/decoder; rebuild; diagnoser; Observer; evaluator; Agent |
| CIR | canonical manifest assembler | collector counters; decoder integrity; Observer boundary/integrity reports; raw Trace status | versioned | rebuild; diagnoser; CVR adjudicator; report/export | collector exporter; parser/decoder; rebuild; diagnoser; Observer; evaluator; Agent |
| derived objects | deterministic rebuild | parsed events; CCM; CIR; untrusted windows | append-only/versioned | diagnoser; report/export | capture administration; assembler; Observer; adjudicators; evaluator; Agent |
| Evidence Lineage | deterministic rebuild | derived objects; parsed events; CCM; CIR; all objective untrusted-window intersections | append-only/versioned | diagnoser; report/export | capture administration; assembler; Observer; adjudicators; diagnoser; evaluator; Agent |
| raw Observer artifacts / measurement report | independent Observer service | independent physical signals | append-only | Observer adjudicator; assembler as integrity input; CVR adjudicator; report/export | capture administration; parser/decoder; rebuild; diagnoser; evaluator; Agent |
| OAR | independent Observer adjudicator | Observer artifacts and frozen manifestation predicate | append-only/versioned | capture administration by reference; evaluator; report/export | capture administration; parser/decoder; assembler; rebuild; diagnoser; CVR adjudicator; Agent |
| CVR | independent capture quality adjudicator | raw Trace status; OAR observer status; CCM; CIR | append-only/versioned | capture administration by reference; report/export | capture administration; parser/decoder; assembler; rebuild; diagnoser; Observer adjudicator; evaluator; Agent |
| diagnostic relevance audit | deterministic diagnoser | derived objects; lineage; CCM; CIR; fixed rules; candidate binding | append-only/versioned | evaluator; report/export | capture administration; assembler; rebuild; Observer; adjudicators; evaluator; Agent |
| Diagnosis Verdict | deterministic diagnoser | fixed rules; candidate binding; diagnostic relevance audit | append-only/versioned | evaluator; report/export | capture administration; assembler; rebuild; Observer; adjudicators; evaluator; Agent |
| Evaluation Result | evaluator | sealed OAR/Case truth; frozen diagnosis; split manifest; scorer | append-only/versioned | report/export | capture administration; assembler; rebuild; diagnoser; Observer; adjudicators; Agent |
| presentation/export artifact | report/export | frozen outputs | immutable | authorized readers | every source-object writer; Agent |

## Injection Ledger

The Injection Ledger proves configured injection and identity only. Its
authoritative writer is the **isolated capture administration service**. It
contains exactly the following conceptual fields:

```text
ledger_record_id
case_id
scenario_template_id
fault_family
fault_variant
control_type
board_id
session_id
capture_id
firmware_hash
ELF_hash
config_hash
source_commit
RTOS_name
RTOS_version
toolchain
compiler_flags
workload_seed
injection_definition
injection_parameters
injection_status
injection_enable_epoch
collector_config_hash
operator
timestamp
ledger_version
prior_ledger_ref
observer_adjudication_ref
capture_validity_record_ref
```

`injection_status` is one of `not_applicable`, `configured`, `enabled`,
`enable_marker_missing`, or `failed`. `injection_enable_epoch` is required when
`injection_status == enabled`; for a healthy control it is `not_applicable` or
null and it must never be a zero value, empty string, or placeholder timestamp.

The Ledger must not directly save or independently write
`manifestation_status`, `manifestation_interval`, `observer_status`,
`capture_validity_status`, or `invalid_reason`. It links to OAR and CVR by
reference only; copying their conclusion fields into Ledger is prohibited.

## Observer Adjudication Record (OAR)

OAR is the sole authoritative manifestation-truth record. Its authoritative
writer is the **independent Observer adjudicator**, which is distinct from both
capture administration and the Observer artifact service.

```text
observer_adjudication_id
case_id
capture_id
observer_status
observer_artifact_hash
manifestation_status
manifestation_interval
manifestation_predicate_id
manifestation_predicate_version
observer_adjudication_version
alignment_error_bound
adjudicator
adjudication_timestamp
prior_adjudication_ref
```

`observer_status` is `complete`, `partial`, `corrupt`, `missing`, or
`unalignable`; `manifestation_status` is `manifested`, `not_manifested`, or
`not_assessable`. For `manifested`, `manifestation_interval` is required. For
`not_manifested`, it is empty. For `not_assessable`, it must not be generated.
When no Observer artifact exists, `observer_artifact_hash` is empty/null and a
placeholder hash is forbidden.

## Capture Validity Record (CVR)

CVR is the sole authoritative technical-Capture-validity record. Its
authoritative writer is the **independent capture quality adjudicator**.

```text
capture_validity_record_id
case_id
capture_id
capture_validity_status
invalid_reason
raw_trace_status
raw_trace_hash
observer_status
CCM_ref
CIR_ref
quality_rule_id
quality_rule_version
quality_adjudicator
adjudication_timestamp
prior_validity_ref
```

`capture_validity_status` is `valid`, `invalid`, or `pending_adjudication`;
`invalid_reason` is required for `invalid` and uses a controlled vocabulary.
`raw_trace_status` is `complete`, `partial`, `corrupt`, or `missing`. A raw
Trace hash is required when an artifact exists; missing artifacts have a null
hash, never a placeholder. CVR decides whether a Capture meets frozen technical
validity conditions. It does not decide whether a fault manifested or whether a
diagnoser is correct.

The frozen reference graph is:

```text
Injection Ledger -> observer_adjudication_ref -> OAR -> Observer artifacts
                 -> capture_validity_record_ref -> CVR -> raw Trace / CCM / CIR / Observer status
```

## Capture Capability Manifest (CCM)

CCM is a versioned static/configuration capability object. It answers: **what
could this Capture configuration theoretically observe?** The canonical
manifest assembler is its authoritative writer.

```text
capability_manifest_id, capture_id, event_types_enabled, task_filter,
resource_filter, irq_filter, core_filter, sampling_enabled, sampling_rate,
sampling_phase, buffer_capacity, buffer_high_watermark_policy,
timestamp_source, clock_resolution, payload_fields, dictionary_version,
mapping_version, collector_version, RTOS_version, firmware_hash,
collector_config_hash, manifest_version, prior_manifest_ref
```

CCM does not contain observed overflow, sequence continuity, observed
watermark, truncation, corruption, or diagnosis relevance. Those observations
belong only to CIR, the collector counter report, or the decoder integrity
report.

## Capture Integrity Record (CIR)

CIR is a versioned per-Capture integrity object. It answers: **was actual
acquisition complete, and what degradation occurred?** Its authoritative writer
is the canonical manifest assembler, which derives it only from the authority
register's immutable inputs.

```text
integrity_record_id, capture_id, sequence_continuity, sequence_gap_records,
overflow_count, buffer_high_watermark_observed, natural_overflow, truncated,
trace_start_boundary, trace_end_boundary, payload_corruption, decoder_failure,
observer_record_status, alignment_error_bound, alignment_status,
raw_trace_status, raw_trace_hash, decoder_integrity_report_ref,
collector_counter_report_ref, observer_boundary_report_ref, integrity_version,
prior_integrity_ref
```

Every derived object and diagnosis audit binds both
`capture_capability_manifest_ref` and `capture_integrity_record_ref`; binding
only CCM is invalid. CIR does not establish manifestation truth or diagnostic
relevance.

## Evidence Lineage and diagnosis objects

The deterministic rebuild writes objective derivation provenance only. It must
preserve all objective intersections and may not read fault family or verdict.

```text
source_event_ids, open_event_id, close_event_id, boundary_event_ids,
derivation_rule_id, derivation_version, interval_start, interval_end,
all_intersecting_untrusted_window_ids,
boundary_intersecting_untrusted_window_ids, source_integrity_issue_ids,
capture_capability_manifest_ref, capture_integrity_record_ref, lineage_status
```

The deterministic diagnoser separately writes the **diagnostic relevance audit**
and the Diagnosis Verdict:

```text
relevant_untrusted_window_ids, irrelevant_untrusted_window_ids,
unresolved_window_ids, relevance_rule_id, relevance_rule_version,
relevance_reason
```

Relevance is fault-rule, entity-binding, and candidate-interval specific.
Changing relevance rules never rewrites Evidence Lineage. The verdict vocabulary
is exactly `SUPPORTED`, `REFUTED`, `UNKNOWN`, and `OOD`; no confidence,
probability, Agent score, package validity, or proof digest is a verdict state.

The existing decoder's sequence/LOSS/OVERFLOW observations are evidence inputs,
not CCM fields ([codec](../../../parser/codec.py)). The total plan describes the
missing source/boundary lineage required by this conceptual contract
([implementation plan](../../research_track/rtos_reliable_diagnosis_pilot/rtos_reliable_diagnosis_implementation_plan.md)).
