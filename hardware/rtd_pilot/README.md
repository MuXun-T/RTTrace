# RTD-Pilot Phase 1 hardware tools

These scripts establish independent GPIO/DSLogic and UART evidence only. They never call the parser, metric, diagnoser, Agent, CCM, Ledger, or lineage code.

`pinmaps/phase1_board_contracts.json` selects the frozen Phase 1 profile by `board_id`. It retains the Fire V2 contract and adds the separately authorized ALIENTEK ATK-DNF103 V2 unit. Physical engagement remains blocked until the selected profile's header-silk and CH340-jumper checklist is completed. Run `python3 -m unittest hardware/rtd_pilot/tests/test_tools.py` before use. `validate_capture.py` is fail-closed and retains no data itself; raw captures must remain outside the repository and metadata must refer to their hashes.

`candidate_preflight/alientek_elite_f103ze_preflight_template.json` and
`scripts/check_candidate_preflight.py` remain historical nonformal electrical/link preflight
tools. They reject formal Capture fields and do not mutate either frozen board contract.
Use `observer/metadata_alientek_elite_v2_template.json` only for a fresh formal capture of
the frozen ALIENTEK unit; its MCU UID and pinmap hash are required.
