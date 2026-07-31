# Phase 2 Regression Baseline Disposition

START_HEAD is `c5ea276758d4aa372ae51a3ab16771d306065ee5`. Evidence logs are retained
under `/tmp/rtd-pilot-baseline.U58PBf`; they were generated with the same
Python and Release/Ninja C++ environment for START_HEAD and the current tree.

| Surface | START_HEAD | Current tree | Disposition |
| --- | --- | --- | --- |
| `test_serial_channel_matches_offline_dataset` | 5/5 fail | 5/5 fail | `INHERITED_DETERMINISTIC_BASELINE_FAILURE` |
| `trace_collector_tests` async flush | 1/20 fail | 0/20 fail | `INHERITED_FLAKY_BASELINE_RISK` |

The Python test uses the same `multi_core` PTY input, `read_size=29`, and
`timeout_s=1.0` in all ten valid runs. Each fails with
`AssertionError: invalid trace magic` at `test_online_channel.py:119`. Its path
is `test_online_channel -> WorkspaceController.viz_LoadDatasetFromChannel ->
load_dataset_from_chunks -> parser`; no Phase 2 contract/schema implementation
is called. Five `/usr/local/bin/python` attempts without pytest are explicitly
excluded because no test ran.

The C++ repetition used GNU 11.4, `/usr/bin/c++`, CMake/Ninja, and Release
`-O3 -DNDEBUG`. START_HEAD run 6 alone failed `FAIL: fallback async flushed`;
the current tree has no failures or new pattern in twenty runs. Collector core,
collector test, and CMake files are byte-identical to START_HEAD. This is not a
claim that the C++ suite is universally stable.

`phase2_regression_delta = 0`; no test was ignored, no serial production code
was changed, and no collector assertion was weakened. Full-repository status is
not “all tests pass”: the completed current Python run reports `1 failed, 908
passed, 1195 subtests passed in 161.49s`, with this same inherited Python
failure as its sole failure. The C++ baseline risk is accepted only for this
Phase 2 delta assessment.
