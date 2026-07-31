# Phase 1 pin conflict audit v2

Evidence sources are the user-supplied Wildfire Fire V2 silkscreen photo record `6c656918c21198abee1760fdfaa11ccf_720.jpg` (SHA-256 `062cdf23ab2e1d4e37166a74792ab42d877bde77133fd0936d8dcddcfa7d884d`), the local STM32CubeF1 GPIO/USART1 device definitions, and the known board state. The photo establishes physical header exposure; CubeF1 establishes MCU GPIO/USART1 defaults but is not treated as a Fire V2 schematic. The user must visually reconfirm the indicated silk and UART jumper before the first power-up.

| Pin | Role | Photo/header evidence | Board load and conflict decision | Approved |
|---|---|---|---|---|
| PC2 | CH0 EPOCH | main GPIO header silk `PC2`, LCD-side row | GPIO input after reset; not an LCD/FSMC/SDRAM/SDIO/Ethernet pin in this fixed smoke configuration | yes |
| PC3 | CH1 calibration | main GPIO header silk `PC3`, RTC/SD-card-side row | GPIO input after reset; TIM2-update-ISR-scheduled software marker output, therefore jitter-measured rather than a hardware output-compare claim | yes |
| PC4-PC7 | CH2-CH5 | matching main-header silk, alternating rows | GPIO input after reset; no confirmed hardwired board load in the photo | yes |
| PB5 | CH6 IRQ | main-header silk `PB5` | red RGB LED is low-active. Logical active is physical high/LED off; no jumper required | yes, recorded load |
| PB0 | CH7 recorder | main-header silk `PB0` | green RGB LED is low-active. Logical active is physical high/LED off; no jumper required | yes, recorded load |
| PA9/PA10 | USART1 TX/RX | photo-visible UART jumper region plus main-header silk | CH340 route requires the UART jumper block to select USART1 PA9/PA10; no flow control | approved pending jumper confirmation |

PC0, PC1, PB10, SWD, reset, boot, crystals, power rails, and unaudited display/FSMC/SDRAM/SDIO/Ethernet signals are excluded. This is a smoke pin audit only; it does not declare a board-wide peripheral ownership model or a paper experiment.
