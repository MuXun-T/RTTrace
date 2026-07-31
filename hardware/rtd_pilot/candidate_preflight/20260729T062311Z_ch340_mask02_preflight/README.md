# Candidate CH340 control profile, 2026-07-29

This is a nonformal serial-control preflight for
`alientek-elite-stm32f103ze-candidate`, not a Capture or a board freeze.

The CH340 was bound through
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`, resolving to
`/dev/ttyUSB0`. The reader used 115200 8N1, no flow control, read-only access,
and kept one file descriptor open throughout the test.

The observed-on-open mask was `0x00000006`. A same-fd reset under that
observe-only state entered System Boot ROM. In a separate retained attempt,
the reader set the candidate-tested output mask to `0x00000002`; a reset from
the same persistent UID-pinned SWD session then started Flash normally:
`PC=0x0800020c`, `USART1 BRR=0x00000045`, `CR1=0x0000200c`, scheduler running,
and four tasks created. The one gate write was consumed by firmware.

The raw log contains `RTD1 BOOT`, `RTD1 CAPTURE_ARMED`, and one matching
`RTD1 EPOCH_BEGIN`/`EPOCH_END` pair for sequence 1. This passes the candidate
CH340 host-receive layer. It does not prove PA9 waveform, the PA9--CH340
physical path, or CH0--CH7 mapping, and does not freeze any candidate board
metadata. Legacy pin-map text emitted by the already-installed ELF is retained
inside the raw log only and is not adopted by this candidate preflight.
