# Phase 1 final freeze decision

```text
decision_id = phase1-final-freeze-alientek-elite-v2-20260730
phase = P1
freeze_status = FROZEN
freeze_date = 2026-07-30
branch = main
START_HEAD = c5ea276758d4aa372ae51a3ab16771d306065ee5
FINAL_HEAD = c5ea276758d4aa372ae51a3ab16771d306065ee5
worktree_status = DIRTY_UNTRACKED_PRESERVED_NO_CLEANUP
reviewer = SELF_REVIEW
approver = PROJECT_OWNER
approval_authority = OWNER_APPROVAL
approval_status = OWNER_APPROVED
review_status = SELF_REVIEW_COMPLETED
P2_authorization_status = OWNER_AUTHORIZED
```

## Repository state and Phase 1 changes

The branch is `main`; `START_HEAD` and `FINAL_HEAD` are identical because this
operation creates no commit, tag, push, or remote mutation. The worktree was
already dirty with unrelated untracked research material. Those paths were
preserved without cleanup, staging, or modification.

This freeze adds `phase1_data_release_decision.md`,
`phase1_data_release_inventory.json`, `phase1_h3_mandatory_scorecard.md`,
`phase1_h3_mandatory_scorecard.json`,
`phase1_platform_selection_decision.md`, this decision,
`phase2_owner_authorization.md`, `tool/rtd_phase1_verify_freeze.py`, the
owner-approval record, final receipt, and owner-freeze regression log. It
updates only current Phase 1 status/selection/release/closeout documents and
the Phase 1 supplement manifest/README. H2 manifest/receipt, H3 evidence
manifest, raw DSView files, UART evidence, and rejected evidence were not
modified.

## Binding decisions

| Binding | File | SHA-256 |
| --- | --- | --- |
| Frozen supplement manifest | `hardware/rtd_pilot/phase1_freeze_supplements/20260730T103123Z_alientek_elite_v2/phase1_final_freeze_supplement_manifest.json` | `357cc703cb8068e95c19b2b788867c2cb975512dc092d761708cc4f5548fb89a` |
| H3 scorecard | `phase1_h3_mandatory_scorecard.md` | `8866be2602b795f05ecde2b0a97ab04c48accfe0b77bfbcb569eb6001eb1be61` |
| H3 scorecard machine record | `phase1_h3_mandatory_scorecard.json` | `ff3e9151ece86991b4e766f7081b388836521e4780f6911ea9e8cbe8457c6c60` |
| Platform selection decision | `phase1_platform_selection_decision.md` | `babebec8524eebd477206057587e8969eb21ec893fd85b1e2860c48d62525b46` |
| Data-release decision | `phase1_data_release_decision.md` | `3022914ceecbbafc99ef7bad2db16ff8dccbfec1149d79082d683f84ff1c0b63` |
| Data-release inventory | `phase1_data_release_inventory.json` | `1cc331b15eea53d7e6b8b14038d59e7d26af0fc320caccc9003ea04824c669a9` |
| Owner self-review approval | `owner_self_review_approval.json` | `712b99ee96936d5f0869f8bee86105213bb50d6fb2bf144ba060c8cc33aab976` |

## Included evidence

The frozen supplement binds the unmodified H2 freeze manifest and historical
receipt, H3 evidence manifest, final H3 UART validator, gate receipt, H3
pre-capture metadata, H3 Capture A/B summaries and IRQ precision witness, H2
perturbation reproduction, and the host-only `38 tests passed` regression log.

The two raw DSView artifacts remain outside git and are frozen by SHA-256:

| Capture | Filename | SHA-256 |
| --- | --- | --- |
| A | `DSLogic PLus-la-260730-175831.dsl` | `c047680c3c9e912b1c28c4b0630e883bfa2858a474e86029821af1c8356b25b6` |
| B | `DSLogic PLus-la-260730-180245.dsl` | `183b319ddead9d5e04ab1cd51297bc9923cf92698e2f37b487278e3e62d99ed1` |

## Frozen platform and scope

The frozen platform identity is the selected Alientek ATK-DNF103 V2,
STM32F103ZET6, FreeRTOS V10.3.1, `H3_COLLECTOR_SMOKE` BIN SHA-256
`e67583ad9fb4adf1d8c1c719b24718046eb0e9e87576d69c2992ea3b3390dfe3`,
ELF SHA-256 `574d91f7a3841ec4fde19355b8836ae5d61dd92cf2051d13e3ae294154c0895e`,
and `phase1-pinmap-v2`. The frozen Observer identity is Capture A CH0--CH7 at
20 MHz and Capture B CH0/CH1/CH6 at 100 MHz, both with a 1.6 V threshold. The
frozen toolchain/build identity is Arm GNU Toolchain 15.2.1, CMake 3.22.1,
Ninja 1.13.0, and H3 build-config SHA-256
`3db978368eea1976acd14c665c902410a073aeda8e9e1b4b35ebfeecd0aec5f1`.

This decision freezes only RTD-Pilot Phase 1 hardware, RTOS, toolchain,
transport, Observer feasibility evidence, H3 scorecard, and platform selection.
Raw material stays owner-controlled and immutable; a redacted derivative must
not replace it. Replacing a board/MCU/RTOS/firmware/ELF/toolchain major version,
UART or collector configuration, Observer profile, or pin map requires review
of affected H3 items before reuse.

## Excluded claims

This freeze does not automatically publish raw DSView, UART, or ELF files; does
not create or implement Ledger, CCM, or CIR; does not create a Case,
CaptureEpisode, or CaseEpisodeBundle; does not produce `fault_manifested`, a
manifestation interval, or fault truth; does not implement a four-state
diagnoser; does not establish a paper experiment result; and does not prove
that Phase 2 technology is implemented.

The project owner has authorized later Phase 2 work only as defined in
`docs/rtd_pilot/contracts/phase2_owner_authorization.md`.
