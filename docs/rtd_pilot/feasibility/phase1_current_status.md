# Phase 1 current status

## Current status: 2026-07-30

`P1_STATUS=FROZEN` under project-owner `SELF_REVIEW` and `OWNER_APPROVAL`.
The statuses below are intentionally separate:

1. H2 remains an immutable historical submission: its eight UID-bound formal
   captures validate `8/8 VALID`, and its historical receipt retains
   `37 tests passed`.
2. H3 technical evidence is complete for the exact selected configuration:
   Capture A witnesses non-flat CH0--CH7, Capture B is the 100 MHz CH6 pulse
   witness, and the hash-bound collector UART validator is `VALID`.
3. P1 is `FROZEN`; all eleven H3 mandatory items are `PASS`, the platform is
   `SELECTED`, and data release uses `OWNER_CONTROLLED_RESEARCH_ACCESS`.
   The latest host-only hardware-tool regression is `38 tests passed`; it does
   not rewrite the H2 historical receipt.

No further hardware or DSView action was required for the freeze. The project
owner has authorized later Phase 2 work only; no Phase 2 implementation is
claimed by this status.

The final P1 binding is `phase1_final_freeze_decision.md` and the frozen
supplement manifest. The H2 submission manifest and receipt remain immutable
historical inputs. P1 has a selected platform, but this is not a commit, tag,
Phase 2 implementation, parser/diagnoser/CCM/Ledger result, fault-truth claim,
or paper experiment. Fire V2 remains formally retained but is not the active
capture board.

## Historical status: pre-freeze candidate state

`PHASE1_STATUS=ALIENTEK_CANDIDATE_PREFLIGHT_ACTIVE`。这不是 `COMPLETE`、H2
pass、H3 platform selection 或 Phase 2 入口。

活动硬件候选为正点原子精英 STM32F103ZE。它是与 Fire V2 独立的平台：旧 Fire V2
Capture 的成功/失败、UART 零字节、pin map、板载负载、时间阈值和 validator 规则均
仅保留为其自身的历史记录，不能迁移给该候选板。

已完成的是只读身份/寄存器核查、旧 Flash 备份、候选 `TASK_SMOKE` 写后读回 hash
核对，以及一次持久 SWD gate release 后的 CH340 零字节观察。2026-07-29 起，用户将
正点原子精英 STM32F103ZE 指定为当前实际使用的备用板；Fire V2 因启动/串口路径不
稳定而停止作为活动采集板，但其原始 evidence 和冻结合同保留不变。现阶段唯一许可的
后续工作是 `phase1_alientek_candidate_preflight.md` 中的非正式电气/链路预检。新板
`board_id`、pin map、metadata 和 Capture validator 仍未冻结。

2026-07-29 用户已明确修订候选板冻结门：PA9 直接发射和 PA9--CH340 直接路径仅保留为
非正式故障定位诊断，均不再是冻结条件。候选板仍必须完成物理映射/负载审计、通过
`ch340_host_witness_v1` 主机文本见证、以及 CH0--CH7 GPIO Observer 实际波形证据；
这一修订不适用于 Fire V2 的历史规则或证据语义。

不得实现 parser、diagnoser、CCM、Ledger、lineage 或 Agent；不得进入 Phase 2；不得
commit、push 或 tag。
