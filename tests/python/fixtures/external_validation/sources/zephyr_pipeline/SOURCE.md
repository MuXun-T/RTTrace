# Zephyr Pipeline Source

- Repository: `https://github.com/zephyrproject-rtos/zephyr`
- Fixed commit: `b01be6b7b16eb12a8cd0275752d575aca489c430`
- Tag: none. Stable tag `v4.4.1` was checked and did not contain this sample.
- Original path: `samples/subsys/tracing/pipeline`
- License: Apache-2.0; repository root `LICENSE` is copied unchanged and no root NOTICE was found.
- RTOS and format: Zephyr; CTF planned output.
- Generation environment: `native_sim` with `prj_native_ctf.conf`; `mps2/an385` is documented as an alternative simulation target.
- Workload: default mutex pipeline and `CONFIG_SAMPLE_BUS_SEM=y` priority-inversion variant.

The documented source workflow requires Zephyr tooling. In this environment `west` and the Zephyr SDK were unavailable, so no build command was run, no output was created, and no conversion occurred. The status is `acquisition_blocked_missing_toolchain`.

## LICENSE Whitespace Exception

The copied `LICENSE` is byte-identical to the file at fixed commit `b01be6b7b16eb12a8cd0275752d575aca489c430`: `cmp=0`, SHA-256 identical, and byte length identical. Upstream itself ends with an extra blank line, so this file is not modified or normalized for byte-level license provenance. Default `git diff --cached --check` reports `blank-at-eof` for this exact path.

The one-time audit exception applies only to `tests/python/fixtures/external_validation/sources/zephyr_pipeline/LICENSE`: all other staged files pass default strict checking; this path is checked once with only `blank-at-eof` disabled; it must also pass upstream `cmp`, SHA-256, and byte-length checks; and no other whitespace error type is exempt. The exception does not apply to source code, documentation, JSON, traces, other licenses, or future files. It is neither a license change nor a content normalization and does not change claim boundary, `hardware_validation`, acquisition status, or replay state.

This source may only support future reproducible simulator generation after a separately controlled toolchain setup. It is not a hardware capture, independent diagnosis truth, replay result, or replay pass.
