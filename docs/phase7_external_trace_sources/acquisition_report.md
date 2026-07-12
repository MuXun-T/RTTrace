# P7.2 Acquisition Report

## Result

Three candidates completed source/license/provenance audit. `freertos_btf_trace` was acquired with four raw files. `zephyr_pipeline` is `acquisition_blocked` because `west` and a Zephyr SDK were not available; no installation was attempted. `zephelin_optional` is `external_reference_only`; generation was deferred because it is optional and would require its Renode workflow.

## FreeRTOS-BTF-Trace

Repository commit: `791410f5ebb05a9fdf77401228140c60275b5d27`.

The repository root license is MIT. No root NOTICE file was found. `tracedata/` contains repository sample outputs; four selected text files are each below 5 MiB and total 4,202,108 bytes. The upstream README identifies BTF as CSV-based and VCD as ASCII waveform format, and identifies its included RV64 instruction-set simulator as the generation environment. Its expected demo behavior is not treated as independent truth.

## Zephyr Pipeline

Stable tag `v4.4.1` was audited first and did not contain `samples/subsys/tracing/pipeline`. The exact audited source commit `b01be6b7b16eb12a8cd0275752d575aca489c430` contains the sample, its `native_sim` CTF configuration, and its semaphore priority-inversion variant. The repository root license is Apache-2.0 and no root NOTICE file was found.

No trace was generated: `west` was unavailable and no Zephyr SDK was found. CMake was present, but it is insufficient for the documented Zephyr workflow. This is recorded as `acquisition_blocked_missing_toolchain`, not as a successful capture or hardware result.

### LICENSE Whitespace Exception

At fixed commit `b01be6b7b16eb12a8cd0275752d575aca489c430`, the copied Zephyr `LICENSE` has `cmp=0`, identical SHA-256, and identical byte length to upstream. The upstream file itself ends with an extra blank line. It is retained unchanged for byte-level license provenance; it is not a license-quality issue or content normalization.

The one-time freeze audit exception applies only to `tests/python/fixtures/external_validation/sources/zephyr_pipeline/LICENSE`: (A) every other staged file must pass default `git diff --cached --check`; (B) this path is checked once with only `blank-at-eof` disabled; (C) it must also pass upstream `cmp`, SHA-256, and byte-length checks; and (D) no other whitespace error type is exempt. The exception does not apply to source code, documentation, JSON, traces, other licenses, or future files. It does not change claim boundary, `hardware_validation`, acquisition status, or replay state.

## Zephelin

Repository commit: `ca37e2efea312f39a8670078daa1a14a2361e358`.

The repository root license is Apache-2.0 and no root NOTICE file was found. Its README documents a Renode flow that emits CTF and converts it to TEF. No trace was generated and no Renode package was downloaded. This is an optional externally generated quasi-real source audit, not a hardware trace or an AI-performance experiment.

## Boundary

No package was reopened and no replay was attempted or adjudicated. No P6, P7.0, P7.1, proof, collector, parser, or evidence-export artifact was modified.
