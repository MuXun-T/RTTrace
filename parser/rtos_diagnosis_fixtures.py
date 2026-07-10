from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GENERATED_FIXTURE_DIR = ROOT / "tests" / "python" / "fixtures" / "rtos_diagnosis" / "generated"
DEFAULT_OUTPUT_PATH = GENERATED_FIXTURE_DIR / "phase6_synthetic_suite.json"
SUITE_ID = "phase6_synthetic_suite"
SUITE_SCHEMA_VERSION = "rtos-diagnosis-suite-v1"
CASE_SCHEMA_VERSION = "rtos-diagnosis-case-v1"
GENERATED_AT = "2026-07-10T00:00:00+08:00"

CASE_KINDS = (
    "priority_inversion",
    "irq_latency_spike",
    "mutex_hold_inflation",
    "queue_wait_backlog",
    "task_starvation",
    "corrupt_segment",
    "stale_sidecar",
    "missing_calibration",
)

SIZE_POLICY: dict[str, int] = {
    "default_max_events_per_case": 8,
    "hard_max_events_per_case": 12,
    "max_trace_bytes": 1024,
    "max_text_bytes": 512,
    "max_case_file_count": 5,
    "max_suite_bytes": 24000,
}

REPLAY_PLACEHOLDER = {
    "replay_required": False,
    "expected_replay_pass": None,
    "equivalence_scope": "not_evaluated",
}

FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "proof_hash_input",
        "proof_digest_write_path",
        "raw_proof_digest_path",
        "llm_truth",
        "advisor_truth",
        "api_key",
        "token",
        "secret",
        "password",
    }
)

FORBIDDEN_CONTENT_FRAGMENTS = frozenset(
    {
        "/media/",
        "\\media\\",
        "proof_hash_input",
        "proof_digest_write_path",
        "raw_proof_digest_path",
        "proof_digest",
        "proof_payload",
        "ticket",
        ".sqlite3",
        "sqlite_index",
        "sqlite index",
        "api_key",
        "token",
        "secret",
        "password",
        "llm_truth",
        "advisor_truth",
    }
)

FORBIDDEN_PATH_FRAGMENTS = frozenset(
    {
        ".sqlite3",
        "proof_digest",
        "proof_payload",
        "ticket",
        "sqlite_index",
    }
)


@dataclass(frozen=True)
class CompanionFile:
    relative_path: str
    content: str


@dataclass(frozen=True)
class CaseTemplate:
    case_kind: str
    title: str
    description: str
    expected_root_cause_summary: str
    expected_affected_entity: dict[str, Any]
    expected_evidence_refs: tuple[dict[str, Any], ...]
    baseline_events: tuple[str, ...]
    candidate_events: tuple[str, ...]
    extra_files: tuple[CompanionFile, ...] = ()


@dataclass(frozen=True)
class GeneratedFixtureBundle:
    suite: dict[str, Any]
    companion_files: tuple[CompanionFile, ...]


CASE_TEMPLATES = (
    CaseTemplate(
        case_kind="priority_inversion",
        title="Priority inversion synthetic fixture",
        description="Small synthetic case where the candidate extends mutex blocking for a higher-priority task.",
        expected_root_cause_summary="Low-priority holder blocks a higher-priority task through a shared mutex for longer in the candidate fixture.",
        expected_affected_entity={
            "entity_kind": "task",
            "entity_id": "task_high_priority",
            "resource_id": "resource_shared_lock",
            "task_id": "task_high_priority",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_task", "ref_id": "task_high_priority", "required": True},
            {"ref_kind": "logical_resource", "ref_id": "resource_shared_lock", "required": True},
        ),
        baseline_events=(
            "ts=000 cpu=0 task=task_low_holder event=lock mutex=resource_shared_lock",
            "ts=002 cpu=0 task=task_high_priority event=blocked_on mutex=resource_shared_lock",
            "ts=005 cpu=0 task=task_low_holder event=unlock mutex=resource_shared_lock",
            "ts=006 cpu=0 task=task_high_priority event=resume reason=lock_released",
            "ts=007 cpu=0 task=task_high_priority event=diagnostic marker=baseline_hold_short",
        ),
        candidate_events=(
            "ts=000 cpu=0 task=task_low_holder event=lock mutex=resource_shared_lock",
            "ts=002 cpu=0 task=task_high_priority event=blocked_on mutex=resource_shared_lock",
            "ts=003 cpu=0 task=task_mid_runner event=preempt target=task_low_holder",
            "ts=009 cpu=0 task=task_low_holder event=unlock mutex=resource_shared_lock",
            "ts=010 cpu=0 task=task_high_priority event=resume reason=lock_released",
            "ts=011 cpu=0 task=task_high_priority event=diagnostic marker=candidate_hold_extended",
        ),
    ),
    CaseTemplate(
        case_kind="irq_latency_spike",
        title="IRQ latency spike synthetic fixture",
        description="Small synthetic case where candidate interrupt service starts later than the baseline envelope.",
        expected_root_cause_summary="Interrupt service response is delayed beyond the expected latency envelope in the candidate fixture.",
        expected_affected_entity={
            "entity_kind": "irq",
            "entity_id": "irq_timer_tick",
            "irq_id": "irq_timer_tick",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_irq", "ref_id": "irq_timer_tick", "required": True},
            {"ref_kind": "logical_trace_window", "ref_id": "window_irq_latency_spike", "required": True},
        ),
        baseline_events=(
            "ts=010 cpu=0 irq=irq_timer_tick event=raised",
            "ts=011 cpu=0 irq=irq_timer_tick event=service_begin latency_us=1",
            "ts=012 cpu=0 irq=irq_timer_tick event=service_end duration_us=2",
            "ts=013 cpu=0 irq=irq_timer_tick event=diagnostic marker=baseline_latency_nominal",
        ),
        candidate_events=(
            "ts=010 cpu=0 irq=irq_timer_tick event=raised",
            "ts=016 cpu=0 irq=irq_timer_tick event=service_begin latency_us=6",
            "ts=018 cpu=0 irq=irq_timer_tick event=service_end duration_us=2",
            "ts=019 cpu=0 irq=irq_timer_tick event=diagnostic marker=candidate_latency_spike",
        ),
    ),
    CaseTemplate(
        case_kind="mutex_hold_inflation",
        title="Mutex hold inflation synthetic fixture",
        description="Small synthetic case where candidate keeps the same mutex longer on a control path.",
        expected_root_cause_summary="A mutex is held longer in the candidate fixture than in the baseline fixture.",
        expected_affected_entity={
            "entity_kind": "mutex",
            "entity_id": "mutex_control_path",
            "resource_id": "mutex_control_path",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_mutex", "ref_id": "mutex_control_path", "required": True},
            {"ref_kind": "logical_task", "ref_id": "task_mutex_holder", "required": True},
        ),
        baseline_events=(
            "ts=020 cpu=0 task=task_mutex_holder event=lock mutex=mutex_control_path",
            "ts=023 cpu=0 task=task_mutex_holder event=work units=2",
            "ts=024 cpu=0 task=task_mutex_holder event=unlock mutex=mutex_control_path hold_ticks=4",
            "ts=025 cpu=0 task=task_mutex_holder event=diagnostic marker=baseline_mutex_window",
        ),
        candidate_events=(
            "ts=020 cpu=0 task=task_mutex_holder event=lock mutex=mutex_control_path",
            "ts=027 cpu=0 task=task_mutex_holder event=work units=5",
            "ts=031 cpu=0 task=task_mutex_holder event=unlock mutex=mutex_control_path hold_ticks=11",
            "ts=032 cpu=0 task=task_mutex_holder event=diagnostic marker=candidate_mutex_inflated",
        ),
    ),
    CaseTemplate(
        case_kind="queue_wait_backlog",
        title="Queue wait backlog synthetic fixture",
        description="Small synthetic case where candidate backlog grows between producer and consumer events.",
        expected_root_cause_summary="Queue consumers lag behind producers and increase queue backlog in the candidate fixture.",
        expected_affected_entity={
            "entity_kind": "queue",
            "entity_id": "queue_sensor_events",
            "resource_id": "queue_sensor_events",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_queue", "ref_id": "queue_sensor_events", "required": True},
            {"ref_kind": "logical_task", "ref_id": "task_queue_consumer", "required": True},
        ),
        baseline_events=(
            "ts=030 cpu=0 queue=queue_sensor_events event=enqueue depth=1",
            "ts=031 cpu=0 task=task_queue_consumer event=dequeue depth=0",
            "ts=032 cpu=0 queue=queue_sensor_events event=enqueue depth=1",
            "ts=033 cpu=0 task=task_queue_consumer event=dequeue depth=0",
            "ts=034 cpu=0 queue=queue_sensor_events event=diagnostic marker=baseline_depth_flat",
        ),
        candidate_events=(
            "ts=030 cpu=0 queue=queue_sensor_events event=enqueue depth=1",
            "ts=031 cpu=0 queue=queue_sensor_events event=enqueue depth=2",
            "ts=032 cpu=0 queue=queue_sensor_events event=enqueue depth=3",
            "ts=036 cpu=0 task=task_queue_consumer event=dequeue depth=2",
            "ts=037 cpu=0 task=task_queue_consumer event=dequeue depth=1",
            "ts=038 cpu=0 queue=queue_sensor_events event=diagnostic marker=candidate_backlog_persists",
        ),
    ),
    CaseTemplate(
        case_kind="task_starvation",
        title="Task starvation synthetic fixture",
        description="Small synthetic case where candidate leaves the same runnable task unscheduled for longer.",
        expected_root_cause_summary="A runnable task is not scheduled for the expected interval in the candidate fixture.",
        expected_affected_entity={
            "entity_kind": "task",
            "entity_id": "task_background_worker",
            "task_id": "task_background_worker",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_task", "ref_id": "task_background_worker", "required": True},
            {"ref_kind": "logical_trace_window", "ref_id": "window_task_starvation", "required": True},
        ),
        baseline_events=(
            "ts=040 cpu=0 task=task_background_worker event=ready",
            "ts=041 cpu=0 task=task_background_worker event=run slice=1",
            "ts=042 cpu=0 task=task_background_worker event=yield",
            "ts=043 cpu=0 task=task_background_worker event=diagnostic marker=baseline_schedulable",
        ),
        candidate_events=(
            "ts=040 cpu=0 task=task_background_worker event=ready",
            "ts=041 cpu=0 task=task_foreground event=run slice=1",
            "ts=048 cpu=0 task=task_foreground event=run slice=2",
            "ts=049 cpu=0 task=task_background_worker event=run slice=1 delayed_ticks=9",
            "ts=050 cpu=0 task=task_background_worker event=diagnostic marker=candidate_starved_interval",
        ),
    ),
    CaseTemplate(
        case_kind="corrupt_segment",
        title="Corrupt segment synthetic fixture",
        description="Small synthetic case where one candidate segment companion is marked malformed.",
        expected_root_cause_summary="A synthetic segment companion is malformed for the candidate fixture.",
        expected_affected_entity={
            "entity_kind": "trace_segment",
            "entity_id": "segment_0007",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_segment", "ref_id": "segment_0007", "required": True},
            {"ref_kind": "logical_case_note", "ref_id": "case_corrupt_segment_metadata", "required": True},
        ),
        baseline_events=(
            "ts=050 cpu=0 segment=segment_0007 event=read status=ok",
            "ts=051 cpu=0 segment=segment_0007 event=parse records=3",
            "ts=052 cpu=0 segment=segment_0007 event=diagnostic marker=baseline_segment_clean",
        ),
        candidate_events=(
            "ts=050 cpu=0 segment=segment_0007 event=read status=short_frame",
            "ts=051 cpu=0 segment=segment_0007 event=parse status=malformed",
            "ts=052 cpu=0 segment=segment_0007 event=diagnostic marker=candidate_segment_corrupt",
        ),
        extra_files=(
            CompanionFile(
                relative_path="corrupt_segment/segments/segment_0007.txt",
                content=(
                    "segment_id=segment_0007\n"
                    "status=corrupt\n"
                    "fault=truncated_event_field\n"
                ),
            ),
        ),
    ),
    CaseTemplate(
        case_kind="stale_sidecar",
        title="Stale sidecar synthetic fixture",
        description="Small synthetic case where candidate trace identity no longer matches a sidecar companion.",
        expected_root_cause_summary="Sidecar metadata no longer matches the candidate trace identity.",
        expected_affected_entity={
            "entity_kind": "sidecar",
            "entity_id": "sidecar_candidate_index",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_sidecar", "ref_id": "sidecar_candidate_index", "required": True},
            {"ref_kind": "logical_trace_window", "ref_id": "window_sidecar_check", "required": True},
        ),
        baseline_events=(
            "ts=060 cpu=0 trace=baseline_stale_sidecar event=index_sync status=matched",
            "ts=061 cpu=0 trace=baseline_stale_sidecar event=diagnostic marker=baseline_sidecar_fresh",
        ),
        candidate_events=(
            "ts=060 cpu=0 trace=candidate_stale_sidecar event=index_sync status=mismatch",
            "ts=061 cpu=0 trace=candidate_stale_sidecar event=diagnostic marker=candidate_sidecar_stale",
        ),
        extra_files=(
            CompanionFile(
                relative_path="stale_sidecar/sidecar/candidate_index.txt",
                content=(
                    "trace_id=baseline_stale_sidecar\n"
                    "observed_trace_id=candidate_stale_sidecar\n"
                    "status=stale_sidecar_mismatch\n"
                ),
            ),
        ),
    ),
    CaseTemplate(
        case_kind="missing_calibration",
        title="Missing calibration synthetic fixture",
        description="Small synthetic case where candidate timing conversion lacks the expected calibration note.",
        expected_root_cause_summary="Timing conversion depends on calibration metadata that is absent in the candidate fixture.",
        expected_affected_entity={
            "entity_kind": "calibration",
            "entity_id": "clock_calibration",
        },
        expected_evidence_refs=(
            {"ref_kind": "logical_calibration", "ref_id": "clock_calibration", "required": True},
            {"ref_kind": "logical_case_note", "ref_id": "case_missing_calibration_metadata", "required": True},
        ),
        baseline_events=(
            "ts=070 cpu=0 clock=core_clock event=convert ticks=100 scale=present",
            "ts=071 cpu=0 clock=core_clock event=diagnostic marker=baseline_calibration_present",
        ),
        candidate_events=(
            "ts=070 cpu=0 clock=core_clock event=convert ticks=100 scale=missing",
            "ts=071 cpu=0 clock=core_clock event=diagnostic marker=candidate_calibration_missing",
        ),
        extra_files=(
            CompanionFile(
                relative_path="missing_calibration/notes/calibration_status.txt",
                content=(
                    "baseline_calibration=clock_calibration_present\n"
                    "candidate_calibration=absent\n"
                    "status=missing_calibration_detected\n"
                ),
            ),
        ),
    ),
)


def companion_root_for_output(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.parent / f"{path.stem}_artifacts"


def build_fixture_bundle(max_events_per_case: int = SIZE_POLICY["default_max_events_per_case"]) -> GeneratedFixtureBundle:
    _validate_max_events_per_case(max_events_per_case)
    cases: list[dict[str, Any]] = []
    companion_files: list[CompanionFile] = []
    for template in CASE_TEMPLATES:
        cases.append(_build_case_payload(template))
        companion_files.extend(_build_companion_files(template, max_events_per_case))
    suite = _build_suite_payload(cases)
    bundle = GeneratedFixtureBundle(suite=suite, companion_files=tuple(companion_files))
    validate_fixture_bundle(bundle)
    return bundle


def write_generated_suite(
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    *,
    overwrite: bool = False,
    max_events_per_case: int = SIZE_POLICY["default_max_events_per_case"],
) -> dict[str, Any]:
    bundle = build_fixture_bundle(max_events_per_case=max_events_per_case)
    target = Path(output_path)
    companion_root = companion_root_for_output(target)
    if not overwrite:
        if target.exists():
            raise FileExistsError(f"suite output already exists: {target}")
        if companion_root.exists():
            raise FileExistsError(f"companion output already exists: {companion_root}")
    else:
        if target.exists():
            target.unlink()
        if companion_root.exists():
            shutil.rmtree(companion_root)

    target.parent.mkdir(parents=True, exist_ok=True)
    companion_root.mkdir(parents=True, exist_ok=True)

    for companion_file in bundle.companion_files:
        destination = companion_root / companion_file.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(companion_file.content, encoding="utf-8")

    suite_text = _suite_json_text(bundle.suite)
    target.write_text(suite_text, encoding="utf-8")

    return build_fixture_summary(bundle, output_path=target, max_events_per_case=max_events_per_case)


def build_fixture_summary(
    bundle: GeneratedFixtureBundle,
    *,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    max_events_per_case: int = SIZE_POLICY["default_max_events_per_case"],
) -> dict[str, Any]:
    companion_root = companion_root_for_output(output_path)
    case_file_counts = _companion_file_counts(bundle.companion_files)
    case_byte_totals = _case_companion_bytes(bundle.companion_files)
    return {
        "suite_id": bundle.suite["suite_id"],
        "suite_path": str(Path(output_path)),
        "companion_root": str(companion_root),
        "case_count": len(bundle.suite["cases"]),
        "case_kinds": [case["case_kind"] for case in bundle.suite["cases"]],
        "generated_file_count": len(bundle.companion_files) + 1,
        "case_file_counts": case_file_counts,
        "case_companion_bytes": case_byte_totals,
        "suite_size_bytes": len(_suite_json_text(bundle.suite).encode("utf-8")),
        "max_events_per_case": max_events_per_case,
        "size_policy": dict(SIZE_POLICY),
    }


def validate_fixture_bundle(bundle: GeneratedFixtureBundle) -> None:
    suite = bundle.suite
    case_kinds = [case["case_kind"] for case in suite["cases"]]
    if set(case_kinds) != set(CASE_KINDS):
        raise ValueError(f"fixture bundle must cover exactly {CASE_KINDS!r}")
    if len(case_kinds) != len(CASE_KINDS):
        raise ValueError("fixture bundle must contain exactly one case per case_kind")

    suite_json = _suite_json_text(suite)
    if len(suite_json.encode("utf-8")) > SIZE_POLICY["max_suite_bytes"]:
        raise ValueError("suite JSON exceeds size policy")
    _assert_no_forbidden_content(suite_json, label="suite_json")
    _assert_no_forbidden_fields(suite, label="suite")

    file_counts = _companion_file_counts(bundle.companion_files)
    for case_kind, file_count in file_counts.items():
        if file_count > SIZE_POLICY["max_case_file_count"]:
            raise ValueError(f"{case_kind} exceeds max_case_file_count")

    for companion_file in bundle.companion_files:
        _assert_safe_relative_path(companion_file.relative_path)
        _assert_no_forbidden_content(companion_file.content, label=companion_file.relative_path)
        size_limit = SIZE_POLICY["max_trace_bytes"] if companion_file.relative_path.endswith(".trace") else SIZE_POLICY["max_text_bytes"]
        if len(companion_file.content.encode("utf-8")) > size_limit:
            raise ValueError(f"{companion_file.relative_path} exceeds size policy")


def _validate_max_events_per_case(max_events_per_case: int) -> None:
    if not isinstance(max_events_per_case, int):
        raise TypeError("max_events_per_case must be an integer")
    if max_events_per_case < 1:
        raise ValueError("max_events_per_case must be >= 1")
    if max_events_per_case > SIZE_POLICY["hard_max_events_per_case"]:
        raise ValueError("max_events_per_case exceeds hard limit")


def _build_case_payload(template: CaseTemplate) -> dict[str, Any]:
    return {
        "schema_version": CASE_SCHEMA_VERSION,
        "case_id": f"phase6_synthetic_{template.case_kind}",
        "case_kind": template.case_kind,
        "title": template.title,
        "description": template.description,
        "baseline_trace": _trace_entry(template.case_kind, "baseline"),
        "candidate_trace": _trace_entry(template.case_kind, "candidate"),
        "expected_root_cause": {
            "root_cause_id": f"root_{template.case_kind}",
            "root_cause_kind": template.case_kind,
            "summary": template.expected_root_cause_summary,
        },
        "expected_affected_entity": dict(template.expected_affected_entity),
        "expected_evidence_refs": [dict(entry) for entry in template.expected_evidence_refs],
        "expected_closure_mode": "reference_only",
        "expected_replay": dict(REPLAY_PLACEHOLDER),
        "claim_class": "report_only",
        "tags": ["phase6", "synthetic", "fixture", "report_only"],
        "notes": [
            "Synthetic fixture case only; trace_ref remains logical.",
            "Companion files are small reproducible text artifacts.",
            "No replay verdict or benchmark metric is attached.",
        ],
    }


def _trace_entry(case_kind: str, variant: str) -> dict[str, Any]:
    return {
        "trace_id": f"{variant}_{case_kind}",
        "trace_ref": f"logical://rtos_diagnosis_synthetic/{case_kind}/{variant}",
        "trace_kind": "logical_reference",
        "notes": [
            "Suite metadata keeps logical references only.",
            "Companion synthetic trace is written beside generated output.",
        ],
    }


def _build_companion_files(template: CaseTemplate, max_events_per_case: int) -> tuple[CompanionFile, ...]:
    baseline_events = _trim_events(template.baseline_events, max_events_per_case)
    candidate_events = _trim_events(template.candidate_events, max_events_per_case)
    baseline_trace = CompanionFile(
        relative_path=f"{template.case_kind}/baseline.trace",
        content=_render_trace_file(template.case_kind, "baseline", baseline_events),
    )
    candidate_trace = CompanionFile(
        relative_path=f"{template.case_kind}/candidate.trace",
        content=_render_trace_file(template.case_kind, "candidate", candidate_events),
    )
    return (baseline_trace, candidate_trace, *template.extra_files)


def _trim_events(events: tuple[str, ...], max_events_per_case: int) -> tuple[str, ...]:
    if len(events) <= max_events_per_case:
        return events
    if max_events_per_case == 1:
        return (events[-1],)
    return (*events[: max_events_per_case - 1], events[-1])


def _render_trace_file(case_kind: str, variant: str, events: tuple[str, ...]) -> str:
    lines = [
        "# synthetic_rtos_diagnosis_trace",
        f"# case_kind={case_kind}",
        f"# variant={variant}",
        "# trace_ref_is_logical_only=true",
        *events,
    ]
    return "\n".join(lines) + "\n"


def _build_suite_payload(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SUITE_SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "generated_at": GENERATED_AT,
        "description": (
            "Small synthetic RTOS diagnosis fixture suite for approved P6.2 work. "
            "It keeps logical trace references in-suite and writes tiny companion files beside the generated output."
        ),
        "cases": cases,
        "claim_boundary": {
            "claimable": [],
            "report_only": [
                {
                    "boundary_id": "boundary_synthetic_fixture_report_only",
                    "topic": "synthetic_benchmark",
                    "claim_class": "report_only",
                    "statement": "Synthetic fixture coverage is report-only and does not establish real RTOS generality.",
                    "conditions": ["Cases remain synthetic and small."],
                },
                {
                    "boundary_id": "boundary_schema_validity_report_only",
                    "topic": "schema_validity",
                    "claim_class": "report_only",
                    "statement": "Schema-compatible generation only confirms fixture shape and boundary discipline.",
                    "conditions": ["Suite continues to validate against the existing P6.1 schema."],
                },
                {
                    "boundary_id": "boundary_phase6_scope_lock_report_only",
                    "topic": "phase6_scope_lock",
                    "claim_class": "report_only",
                    "statement": "P6.2 outputs stop at synthetic fixture material and do not include replay or benchmark runner semantics.",
                    "conditions": ["No evidence package, replay verdict, advisor comparison, or human feedback artifact is generated."],
                },
            ],
            "not_claimable": [
                {
                    "boundary_id": "boundary_top_k_root_cause_not_claimable",
                    "topic": "top_k_root_cause",
                    "claim_class": "not_claimable",
                    "statement": "Top-k root-cause performance is not claimable from fixture generation alone.",
                    "conditions": ["No diagnosis metrics runner exists in P6.2."],
                },
                {
                    "boundary_id": "boundary_human_helpfulness_not_claimable",
                    "topic": "human_helpfulness",
                    "claim_class": "not_claimable",
                    "statement": "Human helpfulness is outside the fixture-only scope.",
                    "conditions": ["P6.6 remains deferred."],
                },
                {
                    "boundary_id": "boundary_llm_explanation_not_claimable",
                    "topic": "llm_explanation",
                    "claim_class": "not_claimable",
                    "statement": "LLM explanation is not part of this fixture suite and cannot substitute for labels.",
                    "conditions": ["Truth paths remain deterministic and unchanged."],
                },
                {
                    "boundary_id": "boundary_p6_3_plus_not_claimable",
                    "topic": "p6_2_plus_approval",
                    "claim_class": "not_claimable",
                    "statement": "Later Phase 6 work remains deferred and is not approved by fixture generation.",
                    "conditions": ["Separate main-agent review is still required for P6.3 through P6.7."],
                },
            ],
        },
        "minimum_case_kinds": list(CASE_KINDS),
        "notes": [
            "Exactly eight synthetic cases cover the approved P6.2 case kinds.",
            "All cases remain report_only and keep expected_replay as a placeholder only.",
            "Companion files stay small and reproducible under the enforced size policy.",
        ],
    }


def _suite_json_text(suite: dict[str, Any]) -> str:
    return json.dumps(suite, ensure_ascii=False, indent=2) + "\n"


def _companion_file_counts(companion_files: tuple[CompanionFile, ...]) -> dict[str, int]:
    counts: dict[str, int] = {case_kind: 0 for case_kind in CASE_KINDS}
    for companion_file in companion_files:
        counts[_case_kind_from_relative_path(companion_file.relative_path)] += 1
    return counts


def _case_companion_bytes(companion_files: tuple[CompanionFile, ...]) -> dict[str, int]:
    totals: dict[str, int] = {case_kind: 0 for case_kind in CASE_KINDS}
    for companion_file in companion_files:
        totals[_case_kind_from_relative_path(companion_file.relative_path)] += len(companion_file.content.encode("utf-8"))
    return totals


def _case_kind_from_relative_path(relative_path: str) -> str:
    case_kind = relative_path.split("/", 1)[0]
    if case_kind not in CASE_KINDS:
        raise ValueError(f"unexpected case directory: {relative_path}")
    return case_kind


def _assert_safe_relative_path(relative_path: str) -> None:
    path = Path(relative_path)
    if path.is_absolute():
        raise ValueError(f"absolute path is forbidden: {relative_path}")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"parent traversal is forbidden: {relative_path}")
    normalized = relative_path.replace("\\", "/").lower()
    for fragment in FORBIDDEN_PATH_FRAGMENTS:
        if fragment in normalized:
            raise ValueError(f"forbidden path fragment {fragment!r} in {relative_path}")


def _assert_no_forbidden_content(value: str, *, label: str) -> None:
    lowered = value.lower()
    for fragment in FORBIDDEN_CONTENT_FRAGMENTS:
        if fragment in lowered:
            raise ValueError(f"forbidden content fragment {fragment!r} in {label}")


def _assert_no_forbidden_fields(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_FIELD_NAMES:
                raise ValueError(f"forbidden field name {key!r} in {label}")
            _assert_no_forbidden_fields(child, label=f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_forbidden_fields(child, label=f"{label}[{index}]")
