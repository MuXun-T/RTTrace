# RTOS Trace Analysis MVP

This repository implements the first executable baseline derived from the MVP
requirements, high-level design, and detailed design documents in this folder.

The codebase follows the document structure:

- `collector/`: C++17 collector library, host simulator, and collector checks
- `spec/`: frozen event catalog and JSON schemas
- `parser/`: decode, verify, align, rebuild, and index pipeline
- `metric/`: metrics, alerts, diagnosis, compare
- `desktop/`: CLI-facing service layer and minimal Qt-compatible shell
- `tests/`: Python, C++, and shared fixtures
- `docs/`: traceability and engineering notes

## Repository Scope

This repository tracks source code, tests, schemas, and the core design
documents needed to build and understand the project.

Large generated validation artifacts, evidence archives, and GB-scale sample
traces are intentionally excluded from version control. Rebuild them locally
with the scripts under `tool/` when they are needed.

For runtime data structures, `parser.models` is the canonical model entry.
`spec.models` remains available as a legacy compatibility export surface.
The current inventory, drift list, and migration rules are tracked in
`docs/spec_models_compatibility.md`.

## Status

This baseline is intentionally scoped for a repository without RTOS kernel
source. The collector side exposes the stable `trace_*` API and a host-side
simulator so that the analysis side can be exercised end-to-end. Future RTOS
integration only needs to replace the collector hook binding layer.

## Intended Workflow

1. Build or run the host simulator to generate a binary trace.
2. Parse and rebuild the trace into a `RebuildBundle`.
3. Run metrics, compare, export, replay, or repro flows through the CLI.
4. Optionally load the same services through the minimal desktop shell.

## Verification

The implementation is designed to run on `python3` and `g++`.
If `cmake` is unavailable, the collector can still be compiled with direct
`g++` commands. The CLI/service layer keeps standard-library fallback paths for
basic checks, but full desktop regression, GUI probes, and `pytest` audit
artifacts require the optional `.[desktop,dev]` dependency sets.

## Desktop Runtime

The desktop runtime now targets `PySide6 + pyqtgraph`.

Bootstrap a local environment with:

```bash
bash tool/bootstrap_desktop_env.sh
```

That bootstrap installs `.[desktop,dev]`, which is the supported baseline for
full desktop runtime smoke, GUI validation, and `tool/run_pytest_report.py`.

If the host cannot create a virtual environment and also lacks `pip`, install
`python3-pip` and `python3-venv` first, then rerun the script.

Run an offscreen runtime smoke check with:

```bash
python3 tool/desktop_validation_preflight.py --output docs/desktop_preflight_local.json
QT_QPA_PLATFORM=offscreen python3 tool/check_desktop_env.py --output docs/desktop_env_local.json
```

Launch the minimal GUI probe with:

```bash
source .venv/bin/activate
python rttrace_cli.py shell
```

Open the full workbench directly with dataset, compare pair, or repro package inputs:

```bash
source .venv/bin/activate
python -m desktop.app.gui --input /path/to/run.trace
python -m desktop.app.gui --baseline /path/to/base.trace --candidate /path/to/candidate.trace --tab compare
python -m desktop.app.gui --package /path/to/export-package --tab export
```

Load online channels through the CLI with:

```bash
python -m desktop.app.cli online-file --input /path/to/run.trace
python -m desktop.app.cli online-socket --host 127.0.0.1 --port 9000
python -m desktop.app.cli online-serial --device /dev/ttyUSB0 --baudrate 115200
python -m desktop.app.cli parse --input /path/to/segment_dir
```

## External Validation

Prepare a deterministic trace pair for Windows/Linux validation with:

```bash
python3 tool/prepare_external_validation_fixture.py --output-dir docs/validation-fixture
```

Generate acceptance baseline reports against that same input pair with:

```bash
python3 tool/run_acceptance_baseline.py --baseline-input docs/validation-fixture/baseline.trace --candidate-input docs/validation-fixture/candidate.trace --output docs/acceptance_baseline_windows.json
python3 tool/run_acceptance_baseline.py --baseline-input docs/validation-fixture/baseline.trace --candidate-input docs/validation-fixture/candidate.trace --output docs/acceptance_baseline_linux.json
```

Compare the two reports and the exported packages with:

```bash
python3 tool/compare_acceptance_baselines.py --left docs/acceptance_baseline_windows.json --right docs/acceptance_baseline_linux.json
python3 tool/compare_normalized_packages.py --left /path/to/windows-package --right /path/to/linux-package
```

Capture desktop runtime readiness on each host with:

```bash
python3 tool/desktop_validation_preflight.py --output docs/desktop_preflight_windows.json
QT_QPA_PLATFORM=offscreen python3 tool/check_desktop_env.py --output docs/desktop_env_windows.json
```
