# Candidate GPIO DSView preflight attempt, 2026-07-29

DSView was configured by the operator at 20 MHz, 1.6 V threshold, 5 s
window, and waiting for trigger. The candidate CH340 reader used the verified
stable by-id path, read-only 115200 8N1, and explicit modem mask `0x2`.

The same UID-pinned SWD session reset the target and wrote
`capture_armed=1` after only 200 ms. The reader received only the firmware
`RTD1 BOOT` record and no `CAPTURE_ARMED` or EPOCH record. The gate write was
too early and was overwritten during startup; this attempt is retained as
nonformal incomplete evidence and cannot establish GPIO mapping.
