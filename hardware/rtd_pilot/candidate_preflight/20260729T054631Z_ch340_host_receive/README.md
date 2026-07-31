# Candidate CH340 host receive preflight, 2026-07-29 (reset retry)

This is a second nonformal host receive attempt. The read-only receiver was
ready before an SWD target reset, resume, and a fresh gate write of
`0x20000004 = 0x00000001`. The receiver again used `/dev/ttyUSB0`, 115200
8N1, no flow control, and did not modify DTR or RTS.

The raw log contains zero bytes. The execution-location check found the
target in System Boot ROM, so this attempt is `inconclusive` for the host
layer; it does not establish an electrical root cause at PA9 or the CH340
path.
