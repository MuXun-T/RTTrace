# Phase 1 H3 marker capability map: Alientek Elite V2

Status: `DEMONSTRATED_SELF_REVIEWED_PASS`. Pin identities remain
`phase1-pinmap-v2`; this map assigns H3 semantics without rewriting H2
history. Physical truth is the retained DSView waveform, with UART as an
independent transport cross-check.

| Capability | Observer marker | Target record | Exact observable condition |
| --- | --- | --- | --- |
| Epoch boundary | CH0 / PC2 | `EPOCH_BEGIN` and `EPOCH_END` | One gate-released Epoch pairs with UART sequence. |
| Calibration | CH1 / PC3 | TIM2 scheduled toggle | Independent timer alignment witness. |
| F1 target ready | CH2 / PC4 rising | `TRACE kind=10` | TIM4 unblocks the lower-priority target; CH2 remains high until its dispatch. |
| F1 target dispatch | CH2 / PC4 falling | `TRACE kind=11` | The target starts actual CPU work after the ready interval. |
| F1 higher-priority interference | CH3 / PC5 high | `TRACE kind=13/14` | Higher-priority interferer brackets its CPU workload. |
| F2 holder ownership | CH4 / PC6 high | `TRACE kind=20/21` | Mutex holder owns the registered mutex. |
| F2 waiter block | CH5 / PC7 high | `TRACE kind=22/23` | Waiter spans `xSemaphoreTake()` until ownership is acquired. |
| F3 IRQ activity | CH6 / PB5 pulse | `TRACE kind=30` every twentieth IRQ | TIM3 is enabled/disabled by the control task; Capture B resolves pulse width. |
| H3 scenario and controlled pressure | CH7 / PB0 high | `TRACE kind=1/2/3/4/90` | H3 case interval and pressure emission are enabled only after the gate. |

Capture A (`20260730T095442Z` H3 evidence manifest) witnesses all channels;
Capture B supplies the separate 100 MHz CH6 witness with a minimum high pulse
of 5.12 us (512 samples). A waveform may prove only the listed target-side
semantics. It does not establish fault truth, causality, manifestation, or a
future Phase 2 case contract.
