from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import socket
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parser import prs_Prescan

try:
    from desktop_validation_preflight import _linux_mount_info  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from tool.desktop_validation_preflight import _linux_mount_info

try:
    from run_acceptance_baseline import _trace_input_provenance  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from tool.run_acceptance_baseline import _trace_input_provenance

FORMAL_INPUT_BYTES = 1024 * 1024 * 1024
CONTRACT_VERSION = "patent_10_4_formal_close_min_contract_20260415"
DEFAULT_WINDOWS_INPUT_PATH = r"D:\rttrace\google_cluster_dense_1gb.trace"
DEFAULT_WINDOWS_SCRATCH_DIR = r"D:\rttrace\scratch"
DEFAULT_LOAD_TIMEOUT_S = 600
DEFAULT_SOAK_HOURS = 24
GROUP_DIRS = (
    "A_control_plane_first",
    "B_budget_pre_freeze",
    "C_degraded_audit",
    "parity",
    "soak",
    "preparation",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _linux_environment_summary(*, trace_path: Path, scratch_dir: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(scratch_dir)
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": "linux",
        "host": {
            "hostname": socket.gethostname(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "packages": {
            "PySide6": _package_version("PySide6"),
            "pyqtgraph": _package_version("pyqtgraph"),
            "pytest": _package_version("pytest"),
        },
        "input_storage": {
            "trace_path": str(trace_path),
            "trace_mount": _linux_mount_info(trace_path),
        },
        "scratch": {
            "path": str(scratch_dir),
            "mount": _linux_mount_info(scratch_dir),
            "free_space_bytes": int(usage.free),
            "total_space_bytes": int(usage.total),
        },
        "source_control": {
            "repo_revision": None,
            "revision_kind": "non_git_archive",
            "note": "Current workspace is not a git repository; use archive hash or directory snapshot for external audit.",
        },
    }


def _windows_environment_template(*, input_path: str, scratch_dir: str) -> dict[str, Any]:
    return {
        "captured_at": None,
        "platform": "windows",
        "status": "pending_external_capture",
        "required_fields": [
            "hostname",
            "windows_version",
            "cpu_model",
            "memory_gb",
            "python_version",
            "python_executable",
            "PySide6_version",
            "pyqtgraph_version",
            "execution_user",
            "timestamp",
        ],
        "recommended_paths": {
            "input_path": input_path,
            "scratch_dir": scratch_dir,
        },
        "capture_hint": "Run desktop_validation_preflight.py and check_desktop_env.py on the Windows execution host before formal proof execution.",
    }


def _input_alignment_report(source_report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source_report, dict):
        return {
            "aligned": False,
            "reason": "source_report_missing",
        }

    conversion = source_report.get("conversion")
    output = source_report.get("output")
    emitted_event_counts = conversion.get("emitted_event_counts") if isinstance(conversion, dict) else None
    required_event_names = {
        "TASK_READY",
        "TASK_DISPATCH",
        "CTX_SWITCH",
        "TASK_BLOCK",
        "TASK_WAKEUP",
        "TASK_EXIT",
    }
    emitted_names = set(emitted_event_counts) if isinstance(emitted_event_counts, dict) else set()
    opaque_filler_bytes = (
        int(conversion.get("opaque_filler_bytes", 0))
        if isinstance(conversion, dict) and conversion.get("opaque_filler_bytes") is not None
        else 0
    )
    return {
        "aligned": required_event_names.issubset(emitted_names) and opaque_filler_bytes == 0,
        "reason": None,
        "patent_alignment": [
            "External scheduler lifecycle records are mapped into TASK_READY/TASK_DISPATCH/CTX_SWITCH/TASK_BLOCK/TASK_WAKEUP/TASK_EXIT.",
            "The resulting trace exercises dependency-sidecar expansion over task lifecycle, dispatch, queue, wakeup, and exit relations.",
            "opaque_filler_bytes=0 ensures this input remains dense semantic trace content instead of source-byte padding.",
        ],
        "required_event_names": sorted(required_event_names),
        "emitted_event_names": sorted(emitted_names),
        "opaque_filler_bytes": opaque_filler_bytes,
        "stop_reason": output.get("stop_reason") if isinstance(output, dict) else None,
    }


def _input_qualification_report(
    *,
    trace_path: Path,
    source_report_path: Path | None,
    source_report: dict[str, Any] | None,
) -> dict[str, Any]:
    prescan = prs_Prescan(trace_path, include_task_state_preview=False)
    trace_size_bytes = int(trace_path.stat().st_size)
    trace_sha256 = _sha256(trace_path)
    input_provenance = _trace_input_provenance(trace_path)
    alignment = _input_alignment_report(source_report)
    size_ready = trace_size_bytes >= FORMAL_INPUT_BYTES
    prescan_ok = bool(prescan.ok)
    dense_semantic_ready = bool(alignment.get("aligned"))
    source_urls = []
    if isinstance(source_report, dict):
        source_urls = [
            item.get("url")
            for item in source_report.get("source_shards", [])
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]

    verdict = "pass" if size_ready and prescan_ok and input_provenance == "real_external" and dense_semantic_ready else "fail"
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "contract_version": CONTRACT_VERSION,
        "verdict": verdict,
        "input_class": "real_external_dense_1gb" if verdict == "pass" else "supporting_only",
        "trace_path": str(trace_path),
        "trace_sha256": trace_sha256,
        "trace_size_bytes": trace_size_bytes,
        "formal_1gb_verified": verdict == "pass",
        "input_provenance": input_provenance,
        "size_ready": size_ready,
        "prs_prescan": {
            "ok": prescan_ok,
            "code": prescan.code,
            "message": prescan.message,
            "warnings": list(prescan.warnings),
            "record_count": prescan.data.get("record_count") if prescan.ok and isinstance(prescan.data, dict) else None,
            "chunk_count": prescan.data.get("chunk_count") if prescan.ok and isinstance(prescan.data, dict) else None,
            "core_ids": prescan.data.get("core_ids") if prescan.ok and isinstance(prescan.data, dict) else None,
            "time_window": prescan.data.get("time_window") if prescan.ok and isinstance(prescan.data, dict) else None,
        },
        "source_report_path": str(source_report_path) if source_report_path is not None else None,
        "source_urls": source_urls,
        "dense_alignment": alignment,
        "reject_if_any": [
            "trace_size_bytes < 1073741824",
            "input_provenance != real_external",
            "prs_prescan.ok == false",
            "opaque_filler_bytes > 0",
            "required scheduler lifecycle event families missing",
        ],
    }


def _command_templates(
    *,
    repo_root: Path,
    archive_root: Path,
    trace_path: Path,
    linux_scratch_dir: Path,
    windows_input_path: str,
    windows_scratch_dir: str,
    soak_hours: int,
) -> str:
    realization_root = repo_root
    linux_preflight = archive_root / "preparation" / "linux_large_input_preflight.json"
    linux_env = archive_root / "preparation" / "linux_environment_summary.json"
    windows_env = archive_root / "preparation" / "windows_environment_capture_template.json"
    input_report = archive_root / "preparation" / "input_qualification_report.json"
    a_dir = archive_root / "A_control_plane_first"
    b_dir = archive_root / "B_budget_pre_freeze"
    c_dir = archive_root / "C_degraded_audit"
    parity_dir = archive_root / "parity"
    soak_dir = archive_root / "soak"
    formal_time_window_json = json.dumps([0.0, 0.0], separators=(",", ":"))
    return f"""# Patent 10.4 Formal Execution Preparation

## Frozen Inputs

- Linux trace: `{trace_path}`
- Windows target copy: `{windows_input_path}`
- Linux scratch: `{linux_scratch_dir}`
- Windows scratch: `{windows_scratch_dir}`
- Input qualification: `{input_report}`
- Linux environment summary: `{linux_env}`
- Windows environment template: `{windows_env}`

## Linux Preflight

```bash
cd {realization_root}
PYTHONPATH=. python3 tool/desktop_validation_preflight.py \\
  --baseline-input {trace_path} \\
  --candidate-input {trace_path} \\
  --scratch-dir {linux_scratch_dir} \\
  --load-timeout-s {DEFAULT_LOAD_TIMEOUT_S} \\
  --output {linux_preflight}

QT_QPA_PLATFORM=offscreen PYTHONPATH=. python3 tool/check_desktop_env.py \\
  --output {archive_root / "preparation" / "linux_gui_smoke.json"}
```

## Group A: Control Plane First

```bash
cd {realization_root}
PYTHONPATH=. python3 -m desktop.app.cli sidecar-build \\
  --input {trace_path} \\
  --output-dir {a_dir / "sidecar"} \\
  --rule-family ref_ref

PYTHONPATH=. python3 -m desktop.app.cli export-evidence \\
  --input {trace_path} \\
  --output-dir {a_dir / "packages" / "run_001"} \\
  --embodiment-mode mode_b \\
  --sidecar-source {a_dir / "sidecar" / "control" / "dependency_sidecar.jsonl"} \\
  --sidecar-manifest-source {a_dir / "sidecar" / "control" / "sidecar_manifest.json"} \\
  --time-window-json '{formal_time_window_json}' \\
  --seed-spec-json '{{"source_kind":"analysis_context","source_payload":{{}}}}' \\
  --rule-family ref_ref \\
  --budget-vector-json '{{"D_max":128,"C_events":1048576,"S_bytes":1073741824,"rho_max":64.0}}' \\
  --closure-policy-json '{{"allow_bounded":true,"allow_degraded":true,"frontier_ref_limit":4096}}'

PYTHONPATH=. python3 -m desktop.app.cli repro \\
  --package {a_dir / "packages" / "run_001"}

PYTHONPATH=. python3 -m desktop.app.cli evidence-query \\
  --package {a_dir / "packages" / "run_001"} \\
  --kind proof
```

## Group B: Budget Pre-freeze

```bash
cd {realization_root}
PYTHONPATH=. python3 -m desktop.app.cli export-evidence \\
  --input {trace_path} \\
  --output-dir {b_dir / "packages" / "D0"} \\
  --time-window-json '{formal_time_window_json}' \\
  --seed-spec-json '{{"source_kind":"analysis_context","source_payload":{{}}}}' \\
  --rule-family ref_ref \\
  --budget-vector-json '{{"D_max":0,"C_events":1048576,"S_bytes":1073741824,"rho_max":64.0}}' \\
  --closure-policy-json '{{"allow_bounded":true,"allow_degraded":true,"frontier_ref_limit":4096}}'
```

Run the remaining budget sweep variants using the matrix frozen in `realization/docs/专利10_4正式闭环与剩余验证执行文档_20260416.md`.

## Group C: Degraded Audit

```bash
cd {realization_root}
PYTHONPATH=. python3 {c_dir / "run_formal_c_linux.py"}
```

## Formal Parity (WP-05)

```bash
cd {realization_root}
PYTHONPATH=. python3 {parity_dir / "build_formal_parity_report.py"} --prepare-linux

# After Windows formal A/B/C summaries are available:
PYTHONPATH=. python3 {parity_dir / "build_formal_parity_report.py"} \\
  --compare \\
  --windows-manifest {parity_dir / "windows_artifact_manifest.json"} \\
  --output {parity_dir / "formal_parity_report.json"}
```

## Supporting Perf Snapshot And Soak

```bash
cd {realization_root}
PYTHONPATH=. python3 tool/run_acceptance_baseline.py \\
  --mode desktop_perf_acceptance \\
  --acceptance-scope perf_only \\
  --profile medium \\
  --soak-iterations 1 \\
  --baseline-input {trace_path} \\
  --candidate-input {trace_path} \\
  --load-timeout-s {DEFAULT_LOAD_TIMEOUT_S} \\
  --workdir {linux_scratch_dir} \\
  --output {archive_root / "parity" / "desktop_perf_acceptance_linux.json"}

PYTHONPATH=. python3 {soak_dir / "run_formal_wp06_linux.py"} \\
  --duration-s {soak_hours * 3600} \\
  --sample-interval-s 60
```

Windows formal execution should reuse the same SHA-qualified bytes and mirror the Linux field set. Freeze the actual Windows environment into the template JSON before running the proof groups.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prepare_patent_10_4_execution_prep")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--linux-scratch-dir", required=True)
    parser.add_argument("--source-report")
    parser.add_argument("--windows-input-path", default=DEFAULT_WINDOWS_INPUT_PATH)
    parser.add_argument("--windows-scratch-dir", default=DEFAULT_WINDOWS_SCRATCH_DIR)
    parser.add_argument("--soak-hours", type=int, default=DEFAULT_SOAK_HOURS)
    args = parser.parse_args(argv)

    trace_path = Path(args.trace).expanduser().resolve()
    if not trace_path.exists():
        raise SystemExit(f"--trace does not exist: {trace_path}")
    if trace_path.stat().st_size < FORMAL_INPUT_BYTES:
        raise SystemExit(f"--trace must be >= 1GB: {trace_path}")

    archive_root = Path(args.archive_root).expanduser().resolve()
    linux_scratch_dir = Path(args.linux_scratch_dir).expanduser().resolve()
    linux_scratch_dir.mkdir(parents=True, exist_ok=True)
    for group_dir in GROUP_DIRS:
        (archive_root / group_dir).mkdir(parents=True, exist_ok=True)

    source_report_path = Path(args.source_report).expanduser().resolve() if args.source_report else None
    source_report = _load_json(source_report_path)
    qualification = _input_qualification_report(
        trace_path=trace_path,
        source_report_path=source_report_path,
        source_report=source_report,
    )
    linux_env = _linux_environment_summary(trace_path=trace_path, scratch_dir=linux_scratch_dir)
    windows_template = _windows_environment_template(
        input_path=str(args.windows_input_path),
        scratch_dir=str(args.windows_scratch_dir),
    )
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "contract_version": CONTRACT_VERSION,
        "trace_path": str(trace_path),
        "trace_sha256": qualification["trace_sha256"],
        "trace_size_bytes": qualification["trace_size_bytes"],
        "archive_root": str(archive_root),
        "linux_scratch_dir": str(linux_scratch_dir),
        "windows_input_path": str(args.windows_input_path),
        "windows_scratch_dir": str(args.windows_scratch_dir),
        "soak_hours": int(args.soak_hours),
        "group_dirs": [str(archive_root / item) for item in GROUP_DIRS],
        "input_qualification_report": str(archive_root / "preparation" / "input_qualification_report.json"),
        "linux_environment_summary": str(archive_root / "preparation" / "linux_environment_summary.json"),
        "windows_environment_template": str(archive_root / "preparation" / "windows_environment_capture_template.json"),
        "command_templates": str(archive_root / "preparation" / "formal_command_templates.md"),
    }

    _write_json(archive_root / "preparation" / "input_qualification_report.json", qualification)
    _write_json(archive_root / "preparation" / "linux_environment_summary.json", linux_env)
    _write_json(archive_root / "preparation" / "windows_environment_capture_template.json", windows_template)
    _write_json(archive_root / "preparation" / "preparation_manifest.json", manifest)
    _write_text(
        archive_root / "preparation" / "formal_command_templates.md",
        _command_templates(
            repo_root=ROOT_DIR,
            archive_root=archive_root,
            trace_path=trace_path,
            linux_scratch_dir=linux_scratch_dir,
            windows_input_path=str(args.windows_input_path),
            windows_scratch_dir=str(args.windows_scratch_dir),
            soak_hours=int(args.soak_hours),
        ),
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
