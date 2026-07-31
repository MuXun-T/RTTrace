# Phase 1 H3 controlled data-release route

Status: `SUPERSEDED_BY_OWNER_CONTROLLED_RESEARCH_ACCESS`. The operative
decision is `phase1_data_release_decision.md`; it is an owner-approved
controlled-access route, not an automatic public release.

| Data class | Retention location | External release state | Required review decision |
| --- | --- | --- | --- |
| DSView `.dsl`, CSV, screenshot | Owner-controlled external evidence store | No automatic publication | Owner supplies controlled access and creates a redacted derivative only when needed. |
| UART raw log, gate receipt, flash/build record | Owner-controlled Phase 1 evidence store | No automatic publication | Owner controls access and redacts identifying fields in any derivative. |
| Firmware source and hash-bound build metadata | External build workspace and repository metadata | Source notice and hash may be shared under their licenses | Preserve FreeRTOS MIT and STM32CubeF1 notices. |
| Derived H3 validation JSON and marker map | Repository documentation | Owner may share for thesis verification | Retain source hashes and the Phase 1 scope boundary. |

The existing Phase 1 license audit covers the component notices. The project
owner's data-release decision records audience, redaction, retention, and
third-party redistribution boundaries. Raw files remain retained even when
rejected and are never silently replaced.
