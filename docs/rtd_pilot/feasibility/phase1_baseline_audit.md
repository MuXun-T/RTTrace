# Phase 1 baseline, asset, and permission audit

This is a pre-entry inventory, not hardware execution evidence and not a paper experiment.

| Item | Observed evidence |
|---|---|
| Repository | root `/media/zzq/新加卷/patent/realization`; branch `main`; `START_HEAD=00b599fa62b5704a146e348df86cf56df0d0a94f` |
| Initial user worktree | 48 pre-existing untracked entries, recorded by the offline-environment audit; they were neither modified, stashed, reset, cleaned, committed, nor added |
| Host/tools | Ubuntu 22.04.2 x86_64; Arm GNU Toolchain 15.2.1; CMake 3.22.1; Ninja 1.13.0; pyOCD 0.45.1; offline environment result PASS |
| CMSIS-DAP | `lsusb`: `c251:f001`; `pyocd list`: Fire CMSIS-DAP UID `0001A0000001` |
| Target | prior verified target is `stm32f103ze`, Cortex-M3, 512 KiB Flash / 64 KiB RAM. This audit does not assert a new live target attach because `pyocd list` lists the probe but reports target `n/a` until the physical debug session is opened. |
| Serial | `/dev/ttyACM0` and `/dev/ttyUSB0`, group `dialout`; CH340 `1a86:7523` visible |
| Observer | DSLogic `2a0e:0034` visible; DSView reports `1.3.2` |
| Permissions/udev | `zzq` is in `dialout` and `plugdev`; local CMSIS-DAP and DreamSourceLab rule files exist |
| Existing board state | the Keil RGB LED program and its PB5/PB0/PB1 low-active LEDs are pre-entry hardware smoke only, never Linux-native development proof or paper data |

Before any overwrite, a read-only 512 KiB backup was retained at `/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/evidence/firmware_backup/pre_phase1_keil_firmware_20260725.bin`: `524288` bytes, SHA-256 `222444b4bd6c551822acd673b3f2325c3c86bb0c280ea96afad98479ee4ecca4`. It exists only to restore the user firmware; it is not publishable source, a Capture, or a research asset. No target mutation was performed by the Phase 1 preparation work documented here.
