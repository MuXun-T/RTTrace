# P7.2 External RTOS Trace Sources

This directory freezes provenance for licensed public and reproducibly generated external RTOS traces. It is acquisition metadata only. It does not reopen an evidence package, parse a trace, execute semantic replay, calculate a replay state, or establish proof parity.

The canonical inventory is `source_inventory.json`. Every source is fixed to a full Git commit, every copied file is checksummed, and all project paths are relative. The repository contains only four small FreeRTOS sample traces plus license copies; it does not contain a third-party repository or Git directory.

`zephyr_pipeline` is source-audited but has no generated trace because the required Zephyr build tooling was absent. `zephelin_optional` is source-audited only. Neither absence is replaced by a synthetic or unrelated artifact.
