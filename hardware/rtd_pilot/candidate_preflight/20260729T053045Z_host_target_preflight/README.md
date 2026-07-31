# Candidate host/target preflight, 2026-07-29

This directory records a nonformal, read-only admission check for
`alientek-elite-stm32f103ze-candidate`. It is not a Capture, a frozen board
definition, a pin map, or a Phase 1 pass.

The check observed the UID-pinned probe, target catalogue entry, a running
Cortex-M3 through attach-mode SWD, DSLogic Plus USB enumeration, and CH340
USB/udev enumeration. It intentionally did not launch DSView, open the CH340
device, reset or halt the target, or perform flash operations.

The preserved candidate pre-flash backup hash was rechecked. Its external
path is evidence-only and is not a source for a board ID, pin mapping,
Capture metadata, timing limit, LED polarity, or validator rule.

All findings requiring physical observation remain pending. In particular,
USB enumeration cannot establish PA9 transmission, PA9--CH340 continuity,
host serial receipt, or any CH0--CH7 physical assignment.
