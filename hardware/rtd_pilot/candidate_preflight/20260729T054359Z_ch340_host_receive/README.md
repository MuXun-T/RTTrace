# Candidate CH340 host receive preflight, 2026-07-29

This is a nonformal `ch340_host_witness_v1` attempt for
`alientek-elite-stm32f103ze-candidate`. The receiver opened `/dev/ttyUSB0`
read-only with 115200 8N1, no flow control, and did not modify DTR or RTS.

The receiver was ready before one SWD gate write of
`0x20000004 = 0x00000001`. The raw log contains zero bytes. A later
execution-location check found the target in System Boot ROM, so this attempt
is `inconclusive` for the host layer; it is not evidence that PA9, the board
CH340 path, or the whole candidate board is faulty.
