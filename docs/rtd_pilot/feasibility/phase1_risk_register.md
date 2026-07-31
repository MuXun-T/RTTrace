# Phase 1 risks and stop conditions

| Risk | Control | Status |
|---|---|---|
| Incorrect board-header assumption | no physical coordinates asserted; require photo/silkscreen audit | open physical blocker |
| Flash overwrites existing Keil LED image | use explicit-confirmation flash script; preserve a read-only backup before first flash if possible | pending |
| Observer conflated with parser truth | scripts only read raw GPIO/UART/metadata | controlled |
| UART changes timing | separate compile-time variants and perturbation protocol; H2 perturbation output is reproducible | controlled; no relative claim where BASE is not observable |
| IRQ marker is not independently controllable | separate TIM3 source and scheduled enable/disable control; H3 Capture B retains 57 CH6 pulses at 100 MHz | technical evidence complete; independent review pending |
| Variant baseline is rejected for intentional absent channels/UART | variant-specific validator profiles and nonflat-channel sets | controlled by 8/8 H2 validation and 38 tool tests |
| H3 is inferred from build evidence | exact H3 DSView hashes, marker map, UART validator, and gate receipt are retained | technical evidence complete; score and release review pending |
| Recorder presented as collector | PC7 is labelled minimal perturbation pulse only | controlled |
| Clock bound selected after result | H2 bounds are frozen from retained accepted sessions and remain revalidated | controlled for H2; no unreviewed H3 performance claim |
| Raw evidence loss | fresh files, hashes, no overwrite, retain failures | controlled by protocol |
