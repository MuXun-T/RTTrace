# Phase 1 正点原子备用板切换冻结

日期：2026-07-29。

用户已指定 正点原子精英 STM32F103ZE 为当前实际使用的备用板。Fire V2 因硬件复位
进入 System Boot ROM、以及其板载串口路径不能稳定支撑正式 UART 交叉验证，停止作为
活动采集板。此决定是 Phase 1 的硬件资源切换，不是平台选择、H2 通过、H3 结论或
Phase 2 入口。

正点原子板仅冻结为 `alientek-elite-stm32f103ze-candidate` 这一候选/备用板标签，
而不是正式 `board_id`。Fire V2 的 `phase1-pinmap-v2`、`fire-f103zet6-v2-1`、Capture
metadata、validator、已测时钟边界、GPIO/CH340 结论及所有成功或失败原始证据均仅
属于 Fire V2，不能迁移到新板。

后续只能遵循 `phase1_alientek_candidate_preflight.md` 和
`phase1_alientek_candidate_uart_contract.md` 的非正式预检：先完成新板丝印/跳帽/负载
审计、主机接收和 CH0--CH7 实体映射。PA9 发射与 PA9--CH340 路径仅作为候选板故障
定位诊断，不再是 2026-07-29 用户授权后的冻结条件。预检完成前，不创建新板 pin map、
board ID、正式 Capture metadata 或替代 validator，也不把任何新板 DSView 波形称为
Phase 1 通过或论文实验。

Fire V2 上最近的 2026-07-29 CH340 复核显示：在手工从 Boot ROM 跳转到 Flash 后，
RCC `CFGR=0x0010000a` 使 APB2 与固件固定的 8 MHz USART 计算不一致，115200 主机
读取出现乱码。该记录保留在外部 evidence，不能用于推断 正点原子板的 PA9、CH340、
时钟或跳帽状态。
