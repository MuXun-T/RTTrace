# Phase 1 pin map v2

Status: frozen software contract; physical wiring remains gated on the one-time visual confirmation below. The user-supplied Fire V2 top-view photo record is `/home/zzq/.config/QQ/nt_qq_998ab9147272dcdb256c0659a5a6c713/nt_data/Pic/2026-07/Thumb/6c656918c21198abee1760fdfaa11ccf_720.jpg`, SHA-256 `062cdf23ab2e1d4e37166a74792ab42d877bde77133fd0936d8dcddcfa7d884d`. With the Ethernet jack on the left and microSD at the right, it shows the long two-row main GPIO header between the RTC area and LCD: PC2/PC4/PC6 are the LCD-side row and PC3/PC5/PC7 the RTC/SD-card-side row. Use the printed silk, never a counted pin number.

| DSLogic | Role | GPIO | Header silk / row | Board load | Observer semantics |
|---|---|---|---|---|---|
| CH0 | EPOCH | PC2 | main header, LCD-side row, `PC2` | none confirmed | logical active = physical high |
| CH1 | CALIBRATION_TIMER | PC3 | main header, RTC/SD-card-side row, `PC3` | none confirmed | logical active = physical high |
| CH2 | TASK_A_RUNNING | PC4 | main header, LCD-side row, `PC4` | none confirmed | logical active = physical high |
| CH3 | TASK_B_RUNNING | PC5 | main header, RTC/SD-card-side row, `PC5` | none confirmed | logical active = physical high |
| CH4 | MUTEX_HOLD | PC6 | main header, LCD-side row, `PC6` | none confirmed | logical active = physical high |
| CH5 | MUTEX_WAIT | PC7 | main header, RTC/SD-card-side row, `PC7` | none confirmed | logical active = physical high |
| CH6 | IRQ_ACTIVE | PB5 | main header, RTC/SD-card-side row, silk `PB5` | red RGB LED, low-active | logical active = physical high = LED off |
| CH7 | RECORDER_OR_PRESSURE | PB0 | main header, LCD-side row, silk `PB0` | green RGB LED, low-active | logical active = physical high = LED off |

UART is the photo-visible on-board UART/CH340 route: USART1 TX `PA9`, RX `PA10`, `/dev/ttyUSB0`, 115200 8N1, no flow control. The UART jumper block must select the PA9/PA10/USART1 route before capture; do not substitute CMSIS-DAP `/dev/ttyACM0`.

The observer GPIO layer starts every marker inactive. PB5/PB0 inactive is physical low and therefore lights the attached LED: this is a documented board-load effect, not an inversion of observer logic. Capture metadata records both levels and the LED correlation. PC3 is a GPIO toggled by the TIM2 update ISR, not a timer output-compare pin; it is timer-scheduled, and its measured jitter is part of the required observer result.

`PC0`, `PC1`, `PB10`, USART3, `phase1-pinmap-v1`, PA13, PA14, NRST, BOOT0/BOOT1, crystal pins, power rails, and unaudited LCD/FSMC/SDRAM/SDIO/Ethernet pins are prohibited.
