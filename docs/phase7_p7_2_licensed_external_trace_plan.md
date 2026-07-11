# P7.2 有许可证的外部 RTOS Trace 获取与来源冻结计划

状态：Execution Plan Amendment

所属阶段：Phase 7 / P7.2

实现状态：Not Started

真实硬件采集：Deferred

Replay 实现：Not Started

Replay 判定：Not Authorized

自动提交数据产物：Disabled

## 1. 修订目的与冻结边界

P7.2 的执行名称冻结为：

- P7.2：Licensed External RTOS Trace Acquisition and Provenance Freeze
- P7.2：有许可证的外部 RTOS Trace 获取与来源冻结

本文档只调整 P7.2 的数据来源和获取策略，不修改 P7.1 已冻结的 package、identity、closure、comparison、mutation 或 replay 状态合同。

P7.0（`e8a52a0`）、P7.1（`aea923b`）和所有 Phase 6 文件均为只读。特别是 P6.4 canonical SHA-256
`fe6bb93191fc72e9584339878c0418b1005f3631f0e213e92b8dcbeb2bd2811e`，以及
`replay_pass_count=0`、`replay_fail_count=0`、`reference_only_count=8`、
`all_replay_passed=false` 保持历史事实。本阶段不进行 package reopen、semantic replay、
replay state calculation、proof parity、diagnosis accuracy、benchmark 或 external baseline comparison；不得出现 `replay_pass`。

不修改 proof hash/digest 语义、collector、evidence export、parser、schema 或测试。LLM、advisor 和 feedback 不参与 provenance truth、ground truth 或任何 replay 判定。

## 2. 真实硬件路线延期

新增但不执行的路线：`P7.2-HW：Deferred Real-Hardware RTOS Trace Acquisition`。

当前没有开发板，不能自采真实硬件 trace。公开 trace、`native_sim`、QEMU、Renode 或仓库自带模拟器不得写成自采硬件数据。取得开发板后，P7.2-HW 必须另行审批，并负责板卡、BSP、固件 hash、trace recorder、传输通道和采集开销；在此之前不作 real-hardware validation、representative real-hardware dataset 或 general RTOS hardware validation 表述。

## 3. 数据分类与论文口径

每个来源和 artifact 必须归入一个分类：

| 分类 | 当前准入 | 说明 |
| --- | --- | --- |
| `public_pre_generated_trace` | 批准 | 许可证覆盖的公开已有 trace。 |
| `reproducibly_generated_simulator_trace` | 批准 | 固定 source/config 的 simulator 输出。 |
| `externally_generated_quasi_real_trace` | 批准 | 例如 Renode 的外部生成 trace。 |
| `verified_external_hardware_trace` | 暂不批准 | 仅有完整外部硬件 provenance 时可用。 |
| `self_acquired_hardware_trace` | 暂不批准 | 仅 P7.2-HW 获批并具备完整采集证据时可用。 |

除非有完整外部或自采硬件证据，每条数据必须记录 `hardware_validation=false`。当前论文允许口径仅为“licensed public and reproducibly generated external RTOS traces”（有许可证的公开及可复现生成的外部 RTOS Trace）。它不支持准确率、通用性、proof correctness 或硬件验证主张。

## 4. 准入和文件大小规则

来源必须公开可访问，含可核验的 `LICENSE`、适用 `NOTICE`（如存在）和 fixed tag/commit；trace 或生成过程、RTOS、格式、生成环境、hardware/simulator 分类必须明确。每个本地文件需要 SHA-256、字节数、项目相对路径、无 PII/secret/未经授权真实用户数据/来源不明二进制的检查。必须说明再分发权限和 ground truth 状态；README 的 demo expected behavior 不是独立 truth。每个选中文件必须经核验受仓库许可证覆盖；generated raw trace 只有在记录生成者、权利基础、许可证覆盖范围及适用 NOTICE 保留义务后才可本地复制，否则只能为 `external_reference_only`。

缺少许可证的来源为 `rejected`；许可证不确定的公开来源只能为 `external_reference_only`，不得复制原始数据。不得以浮动 URL 或 `main` 作为唯一 identity。单个 trace 不超过 5 MiB，全部新增 trace 不超过 20 MiB；超限者只记录为 `external_reference_only`。不使用 Git LFS，不裁剪 raw trace，不复制外部仓库或其 `.git` 目录。

所有外部 clone、构建、日志和生成物只在 `mktemp -d /tmp/rttrace-p7.2-sources.XXXXXX` 下。不得使用 sudo、系统级安装、API key 或项目内 Zephyr workspace。可使用临时 Python venv。

## 5. 来源矩阵

执行时重新核验许可证、仓库状态、exact commit、tag 与路径；下表仅是获批准入策略，不替代实际核验。

| source_id | 优先级 | 仓库和目标 | 预期许可证 | 分类和环境 | P7.2 目标 |
| --- | --- | --- | --- | --- | --- |
| `zephyr_pipeline` | Primary | `https://github.com/zephyrproject-rtos/zephyr`；`samples/subsys/tracing/pipeline` | Apache-2.0 | `reproducibly_generated_simulator_trace`；`native_sim`，备选 `mps2/an385 simulation`；`hardware_validation=false` | 固定稳定 tag/commit；若环境可用，生成 mutex 和 `CONFIG_SAMPLE_BUS_SEM=y` inversion trace；否则 provenance audit 加 `acquisition_blocked_missing_toolchain`。 |
| `freertos_btf_trace` | Primary / Practical Baseline | `https://github.com/kuopinghsu/FreeRTOS-BTF-Trace`；`tracedata/` | MIT | `public_pre_generated_trace`；RV64 simulator；`hardware_validation=false` | 优先选择实际存在且不超限的 `example.btf`、`example.vcd`、`example-4cores.btf`、`example-50k.btf`。 |
| `zephelin` | Optional Enhancement | `https://github.com/antmicro/zephelin` | Apache-2.0 | `externally_generated_quasi_real_trace`；Renode；`hardware_validation=false` | A/B 完成且范围可控时，仅审计 fixed commit、许可证和 CTF/TEF/Renode 生成说明即可。 |

Zephyr 不得使用浮动 `main`；稳定 tag 缺 sample 时须记录经审计的 exact commit。构建失败不可以已有 trace 冒充生成成功。FreeRTOS 的 BTF/VCD、SMP 和不同规模只说明格式/场景覆盖，不说明 diagnosis accuracy 或真实多核板卡实验。Zephelin 不扩展至 AI 性能论文主线，也不得将 Renode 写成硬件。

## 6. 获取步骤和失败处理

先处理 FreeRTOS-BTF-Trace：在临时目录 clone，记录 HEAD、LICENSE、`tracedata/` 候选、原始路径、文件类型、基本可读性、SHA-256 和 bytes；选择最小代表性集合，至少一个 BTF 与一个 VCD。仅在许可证覆盖选中文件且大小合规时，复制许可证和原始 artifact。

然后处理 Zephyr：选择包含 sample 的稳定 tag 或准确 commit，审计 LICENSE 和 sample 路径，检查 `west`、CMake、SDK、`native_sim` 前置条件。环境具备时按官方文档真实命令产生两种场景 trace，并记录 tag、commit、board/simulator、config、build command、真实输出格式、SHA-256、bytes 与 `hardware_validation=false`。缺工具链且安装成本/风险过高时记录 `acquisition_blocked_missing_toolchain`，不伪造输出。

最后才处理 Zephelin；实际生成不是本轮必要条件。任何下载、构建或生成失败必须原样记录，不能降级为成功或从其他来源替代。

## 7. 允许的项目内产物

P7.2 计划提交后的新增文件只可位于：

```text
docs/phase7_external_trace_sources/
tests/python/fixtures/external_validation/sources/
```

预期最小结构为 `README.md`、`source_inventory.json`、`acquisition_report.md`、`claim_boundary.md`，以及每个实际来源目录下的 `SOURCE.md`、`LICENSE`、适用时的 `NOTICE`、`checksums.sha256` 和仅实际取得的 `raw/` 文件。不得创建空伪 trace，且不得新增 parser、runner、schema 或自动化测试。

`source_inventory.json` 是确定性来源清单而非 schema：UTF-8、稳定字段顺序；顶层 `sources` 按 `source_id` 排序，每个来源的 `artifacts` 按 `artifact_id` 排序。每个候选来源必须有一个来源级记录，即使实际 artifact 为零；`artifacts` 可为空。不得包含当前时间、绝对路径、临时目录、随机 UUID、本机用户名或 secret。来源级记录至少包含：

```text
source_id, repository, repository_commit, repository_tag, source_path, license_spdx,
license_path, license_sha256, rtos_name, rtos_version, trace_format, data_class,
generation_mode, generation_environment, hardware_validation, workload_summary,
ground_truth_status, redistribution_status, acquisition_status, acquisition_reason,
external_reference, intended_use, claim_class, artifacts
```

每个 artifact 至少记录 `artifact_id`、`source_path`、`local_path`、`sha256` 和 `bytes`；无本地 artifact 的来源不伪造这些值。`data_class` 必须是第 3 节的受控分类。`repository_tag` 不存在时使用明确空值；`local_path` 必须项目相对。`acquisition_status` 仅可为 `acquired`、`generated`、`external_reference_only`、`acquisition_blocked`、`rejected` 或 `not_evaluated`；阻塞时使用 `acquisition_status=acquisition_blocked` 和稳定的 `acquisition_reason=missing_toolchain`。本阶段所有来源不记录 replay state，或仅记录 `not_evaluated`；不得出现 `replay_pass`、`replay_fail` 或 `all_replay_passed`。

每个 `SOURCE.md` 必须给出来源、fixed commit、tag、原始路径、许可证、适用 NOTICE、RTOS/格式、hardware/simulator、workload、获取/生成命令、转换情况、再分发、ground truth、可用/不可用范围、非硬件原因和不能形成 replay pass 的原因。所有项目内 canonical metadata、命令记录和报告均以变量或项目相对路径表达，不得含实际 `/tmp` 路径、其他绝对路径、当前时间、本机用户名或 token。

## 8. 验收、审阅与冻结条件

P7.2 最小验收：P7.2 计划已独立提交；三个候选均完成来源审计；至少一个来源取得；FreeRTOS 至少一个 BTF 和一个 VCD；每个本地文件有许可证、SHA-256、bytes、exact complete commit 和 hardware/simulator 分类；canonical metadata 无绝对路径或时间；P6.4 hash 不变；P6/P7.0/P7.1 mutation=0；无代码/schema/test 改动、无 replay 判定及硬件主张。Zephyr 生成若被工具链阻塞，保留 unresolved gap 而不阻止 P7.2 结束。

审阅分两轮，四名独立 reviewer 分别覆盖：(A) frozen boundary，(B) license/provenance，(C) representativeness/experiment boundary，(D) reproducibility/paper claim。每轮都报告 `blocking`、`major`、`minor`、`accepted_risk`、建议修改和许可结论。第一轮的全部 blocking/major 必须修复；第二轮必须 `blocking=0`、`major=0`。P7.2 数据审阅沿用 A 许可证、B provenance/identity、C 实验/claim、D 仓库/reproducibility 的分工。

计划冻结前只允许本文件变更，必须通过 `git diff --check`，审阅通过，并独立提交：

```text
docs: approve phase7 licensed external trace acquisition
```

提交后重新审计并确认 clean，才可进入 P7.2。P7.2 产物不可自动 `git add` 或 `git commit`；完成时工作区只能包含批准的数据、许可证和来源文档，且 staged 为空。仅当数据审阅不存在 blocking/major 时，允许进入 P7.2 独立冻结审计；不得进入 P7.3。

## 9. 验证清单

执行 P7.2 时审计 Git 状态、`git diff --check`、untracked/staged 内容；检查 source directories 无 symlink，artifact 无 path traversal，metadata 不含 absolute path、`/tmp/`、用户名、URL token、secret 或 PII；重新计算所有 checksums、bytes 和许可证 SHA-256，确认 complete 40-hex commit、唯一 source/artifact/local path，以及大小上限。最后重新计算 P6.4 canonical hash 并核对其仍为冻结值。

本计划本身不产生 external evidence package、replay report、canonical replay hash 或任何 replay 结论。
