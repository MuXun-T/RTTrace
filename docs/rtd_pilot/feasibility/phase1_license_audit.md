# Phase 1 license audit

Evidence was read locally, not inferred from memory.

| Component | Evidence | License | Phase 1 use |
|---|---|---|---|
| STM32CubeF1 | `/home/zzq/embedded/stm32f103_env/sdk/STM32CubeF1_LICENSE.md`, v1.8.7 | CMSIS Apache-2.0; device BSD-3-Clause; HAL BSD-3-Clause | CMSIS startup/device headers only |
| FreeRTOS kernel | `/home/zzq/embedded/sdk/STM32CubeF1/Middlewares/Third_Party/FreeRTOS`; local git `cdc07b553ee9a9676988e5089a1d484da5d37c9c`; `Source/History.txt` identifies V10.3.1; `LICENSE.md` SHA-256 `a426d8c9fac77c22e2a7ae9cccdae232eef49ba9609992719bc89735ae796054`; GCC/ARM_CM3 `port.c` SHA-256 `7473d722e0875be084392ab464a5b69385e0ebc6b0da4dde14f45581bbe8b600` | MIT | external workspace build only |
| uC/OS-III | `RTOS/uC-OS3-develop/LICENSE`, SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` | Apache-2.0 | backup candidate/hook audit |
| Arm GNU Toolchain | `/home/zzq/embedded/stm32f103_env/toolchain/SOURCE.txt` | local package record | build tool, no redistribution here |

No third-party source was copied into this repository. Firmware source and derived artifacts remain in the external workspace. Raw captures must not be added to git.
