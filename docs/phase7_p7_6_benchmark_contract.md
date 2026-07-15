# P7.6 Benchmark Contract

P7.6 is an independent, report-only host benchmark. It reads the four frozen
FreeRTOS trace/package/replay cases and never writes replay truth or proof
inputs. `hardware_validation=false` is fixed.

Each sample uses `case_id:mode:iteration`, keeps the source trace SHA-256,
trace bytes, normalized event count, frozen package identity, workload and
environment identities, warm-up flag, status, failure reason, and all metric
records. A missing metric is `null` with status `unsupported`, `unavailable`,
or `not_evaluated`; zero is never a missing-value encoding.

`process_cold` means a newly materialized temporary package. `same_process_warm`
reuses one temporary package within the runner. Neither label claims an OS
cache reset; `cache_reset=false` is implicit in the contract.

The runner retains one warm-up and five measured samples per case and mode by
default. Summaries report measured count, success/failure count, median,
minimum, maximum, and MAD without dropping slow or failed runs. Raw samples are
canonicalized with sorted keys and sample IDs, compact ASCII JSON, and one LF.

Acquisition overhead is always:

```json
{"status":"not_evaluated","reason":"missing_board_or_source_identical_firmware"}
```

The required trace-disabled/enabled same-firmware board pairing is unavailable.
No simulator, public trace, or host timing is promoted to acquisition overhead.
