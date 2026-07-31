# 正点原子候选板的 UART 证据合约

状态：`DRAFT_NONFORMAL_ONLY`。本合约只定义候选板预检的替代证据，不宣称满足
Fire V2 的 `/dev/ttyUSB0` Capture 规则，也不修改其历史 validator。

旧合约需要板载 CH340 的主机 UART 日志，以便让 GPIO Observer 有一个独立的文本
和 epoch 序号交叉检查。新板在板载 CH340 路径未审计时，不能把零字节看作唯一的
继续条件。因此把观测拆成下列不可互相冒充的见证：

| 见证 | 所回答的问题 | 必须保留的证据 | 不能回答的问题 |
| --- | --- | --- | --- |
| `pa9_waveform_witness_v1` | MCU 是否在 PA9 处发出正确的 UART 字节？ | CH8 raw/CSV、DSView UART decoder export、ELF hash、CH0 同窗 gate 记录 | 板载 CH340 或主机是否收到字节 |
| `ch340_path_witness_v1` | 已确认 PA9 测试点到已确认 CH340 RX 测试点的物理路径是否传输该字节？ | CH8/CH9 同窗 raw/CSV、两路解码、断电路径审计 | USB 枚举和主机驱动是否接收 |
| `ch340_host_witness_v1` | 该板载 CH340 经 `/dev/ttyUSB0` 是否交付字节到主机？ | udev/VID:PID、持久只读 termios 配置、原始日志、同次候选 gate 与 `RTD1` 序号关联 | 任何缺字节的电气根因 |
| `pa9_independent_transport_witness_v1` | 不依赖板载 CH340 时，候选板是否能将 PA9 文本记录交付给独立主机接收器？ | DSLogic CH8 解码及独立 USB-TTL 原始日志；两者同次匹配 | 板载 CH340 是否正常 |

`pa9_independent_transport_witness_v1` 可在以后成为新板的**替代 UART 合约**，但
只能在下面所有条件通过后才可被冻结：DSLogic 已直接确认 PA9 的同次 115200 8N1
字节；USB-TTL 是独立、3.3 V 兼容的 RX-only 接收器，且只接 PA9、GND（不接其 TX、
VCC、RST 或 boot）；两个接收记录在同一已标识 gate release 中具有相同的 `RTD1`
记录和序号；USB-TTL 的设备身份、termios、线路连接照片、raw log 和 hash 均被保留；
候选 GPIO Observer 也已独立通过。它替代的是“独立 UART 文本见证”的语义，而不是
Fire V2 的 CH340 物理通道，因此正式 metadata 必须声明新合约名称、接收器身份与
电气连接，不得写成旧 CH340 规则通过。

若只使用 DSLogic UART decode，可形成 `pa9_waveform_witness_v1`，它足以定位 MCU
发射问题，但不足以替代主机传输见证。若只使用 USB-TTL 而未同时保留 CH8 解码，
也不足以排除接收器接线或错误源。两者均是新板预检，不是 Capture。2026-07-29 的
用户授权将这两类 PA9 直接观测从候选板冻结条件中移除；它们仍可用于后续故障定位，
不得反向修改 Fire V2 历史规则。

在正式 DSView 窗口开始后，不能 halt、read target、reset、断开 debugger 或重开
任何串口。替代合约的 USB-TTL 接收器也必须在窗口开始前打开并持续保持；预检阶段
的开关、重接和 SWD 调试必须显式保留为非正式记录。
