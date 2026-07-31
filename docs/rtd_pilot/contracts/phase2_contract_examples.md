# Phase 2 Non-Instance Examples

These are field shapes only, not real Case or Capture data. Each outer
`example_only` field is documentation metadata, not a field added to the
strict schema object in `record`.

```json
{
  "example_only": true,
  "record_kind": "rtd_collector_config_snapshot",
  "record": {
    "schema_version": "rtd-phase2-v1.0",
    "config_snapshot_id": "snapshot:<sha256-prefix>",
    "source_metadata_digest": "<64-lowercase-hex>",
    "adapter_version": "rtd-phase2-config-adapter-v1"
  }
}
```

```json
{
  "example_only": true,
  "record_kind": "rtd_injection_ledger",
  "record": {
    "ledger_record_id": "ledger:<future-id>",
    "case_id": "case:<future-id>",
    "scenario_template_id": "scenario:<future-id>",
    "capture_id": "capture:<future-id>",
    "injection_status": "enabled",
    "injection_enable_epoch": {"start": "<future-boundary>"},
    "observer_adjudication_ref": "oar:<future-id>",
    "capture_validity_record_ref": "cvr:<future-id>"
  }
}
```

```json
{
  "example_only": true,
  "record_kind": "rtd_case_definition",
  "record": {
    "case_definition_id": "case-definition:<future-id>",
    "case_id": "case:<future-id>",
    "scenario_template_id": "scenario:<future-id>",
    "example_only": true
  }
}
```

The Ledger example intentionally contains no manifestation, Observer conclusion,
Capture-validity conclusion, invalid reason, raw-trace hash, verdict, or
diagnosis field. OAR alone holds `manifestation_status` and
`manifestation_interval`; CVR alone holds `capture_validity_status` and
`invalid_reason`.

`sequence_gaps`, `LOSS`, `OVERFLOW`, actual high watermark, truncation and
corruption belong only to a CIR. CCM records configuration capability only.
Historical records remain present through complete `prior_*_ref` chains; the
unique latest record is the chain leaf. Synthetic test records are created only
in temporary directories.
