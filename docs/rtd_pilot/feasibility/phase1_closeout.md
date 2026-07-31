# Phase 1 closeout: technical evidence complete, final freeze pending

`P1_STATUS=FROZEN`. The H2 historical submission remains immutable, the exact
H3 evidence is retained and hash-bound, all eleven H3 mandatory items are
`PASS`, the exact platform is `SELECTED`, and data release is approved as
`OWNER_CONTROLLED_RESEARCH_ACCESS`. Later Phase 2 work is owner-authorized but
has not been implemented by this closeout.

## Retained historical blocker record

The following pre-H2/H3 blocker investigation remains historical evidence; it
must not be treated as the current P1 status. The bare-metal GPIO/UART
calibration series has three valid real captures, freezing the observed maxima:
drift `3695.3956547738053 ppm`, alignment residual
`0.014807050000000377 s`, and CH1 period absolute error `0.00000425 s`.

The RTOS Task smoke remains blocked. In external evidence directory `h2_20260728T082308Z_baremetal_gpio_uart_alignment_fix/rtos_task_smoke_swd_gate/formal_task03_single_gate`, `dslogic_task03_single_gate.dsl` and `dslogic_task03_single_gate.csv` are retained, but `uart_task03.log`, `uart_task03_attempt2.log`, and `uart_task03_formal.log` are all empty. It is invalid evidence because a UART-capable variant requires nonempty, matching UART epoch records. It must not be presented as a successful Task capture.

The board is unstable under hardware reset and when opening CH340: those actions have repeatedly entered STM32 System Boot ROM. TASK05--TASK09 identified the relevant modem-line behavior: clearing DTR holds the target in reset, while the candidate same-fd Flash-reset sequence (RTS low, DTR low for 50 ms, DTR high) reaches normal Flash/FreeRTOS execution. Yet the exact firmware writes its BOOT string to a correctly configured USART1/PA9 and CH340 still receives no byte. After full power-off and reseating PA9-RXD/PA10-TXD, TASK10--TASK12 also excluded clock mismatch, tty access direction, and immediate RTS restoration. The remaining blocker is physical PA9 electrical observation: add an unused DSLogic CH8 probe temporarily to PA9 without altering fixed CH0--CH7 wiring, determine whether USART1 TX reaches the header, and repair the route indicated by that result. A nonempty UART `BOOT` record is still required before DSView is armed. Once the DSView window begins, target halt/read/reset, debugger disconnect, and UART reopen are prohibited. The earlier target-halt/read/continue waveform remains retained and invalid.

TASK18 completed that first electrical observation: CH8 recorded PA9 transitions at 115200-compatible timing during a gated EPOCH, proving the MCU endpoint is transmitting. It did not make the UART cross-check valid because CH340 remained empty. After a subsequent jumper replacement, TASK20 repeated the persistent read-only CH340 preflight with a fresh non-halting UID-pinned SWD attach and an accepted single gate write; its UART file remained zero bytes. The unresolved boundary is now the PA9-RXD jumper/output side versus the CH340 receiver: a nonformal simultaneous CH8 MCU-side and CH9 RXD/CH340-side probe is required before another UART preflight. Neither an electrical CH8/CH9 observation nor a GPIO-only DSLogic waveform can replace the mandated `/dev/ttyUSB0` UART records.

An earlier host snapshot lacked DSLogic enumeration; DSLogic Plus `2a0e:0034` subsequently enumerated and DSView was configured for TASK04. That removed only the analyzer-availability gate, not the UART/GPIO cross-check blocker: TASK04 did not start DSView sampling because its required `BOOT` record was absent.

At the time of this historical record, no mutex, IRQ, combined, recorder
perturbation, metadata/validator completion, full regression, or independent
final review could proceed until that Task UART/GPIO cross-check blocker was
resolved. The current restrictions remain: no Phase 2, parser, diagnoser, CCM,
Ledger, lineage, Agent, fault injection, Case creation, or independent-fault
truth workflow is authorized.
