# 正点原子精英 F103ZE 候选板：非正式电气与链路预检

状态：`CANDIDATE_PREFLIGHT_DESIGNED_HARDWARE_NOT_CONNECTED`。这是一套替代板的
准入程序，不是 Capture、H2 通过、H3 平台选择或 Phase 2 工作。旧 Fire V2 的成功、
失败、阈值、board ID、pin map 与 validator 结论均不适用于此板。

## 已知但未冻结的事实

候选标签为 `alientek-elite-stm32f103ze-candidate`，不是 `board_id`。已知探针为
CMSIS-DAP `0001A0000001`，目标 `stm32f103ze`；只读 attach 显示 Cortex-M3
Running、DBGMCU IDCODE `0x10036414`、Flash `512 KiB`，MCU UID words 为
`0x05d7ff34`, `0x334e5630`, `0x43057222`。DSLogic Plus 是 `2a0e:0034`，候选
CH340 是 `/dev/ttyUSB0` / `1a86:7523`。

外部证据根目录为
`/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/evidence/h2_20260728T082308Z_baremetal_gpio_uart_alignment_fix/alientek_elite_candidate_preflight/`。
已保留旧 Flash 只读备份 `preflash_new_board_20260728.bin`，SHA-256
`7519da4a6e67b7898e308c80b307d9deca673809480da2fb9e152c0fa5d78c33`。候选
`TASK_SMOKE` ELF SHA-256 是
`7610d46374472c9b42e1c6e4fecd99680a428433d4306c299c78cbf8896be518`；写后按
4912 bytes 读回的 hash 是
`e448746f2f7aad7f54cc228917ee30db7fd5e01e9927ebd114b024ddefdbfa32`。

该固件的运行时只读检查显示 `GPIOA CRH=0x888444b4`、PA9 是 USART1 复用推挽、
PA10 输入，且 `USART1 SR=0x000000c0`、`BRR=0x45`、`CR1=0x0000200c`。
一次持久 SWD `capture_armed=1` 写入释放过一个 EPOCH；随后 45 秒只读 termios
读取得到零字节 `uart_candidate_task01.log`。这个结果只描述该次 `/dev/ttyUSB0`
接收观察，不能推断 MCU、PA9、CH340 硬件或整板故障。

## 预检顺序

每次预检只产生一个新的外部目录和一个
`candidate_preflight_manifest.json`。用
`hardware/rtd_pilot/scripts/check_candidate_preflight.py` 在目录中检查它；该工具
不是正式 Capture validator。

1. **断电物理审计。** 拍摄正反面、所有与 PA9/PA10、USART1、CH340、跳帽和
   PC2--PC7/PB5/PB0 有关的丝印及连接位置。以该板手册/原理图和实际丝印分别记录
   每一条主张的来源、版本/页码或照片 hash。仅在断电时，对已被文档或丝印明确标为
   同一网络的可触及测试点做连续性测试；不可把 CH340 封装脚、猜测的过孔或 MCU
   引脚当测试点。记录每根 DSLogic 线、公共 GND、跳帽位置、板载 LED/外设/上拉的
   可能负载及未决项。缺少任何一项即不冻结映射。
2. **可选 MCU PA9 发射诊断层。** DSLogic CH8 以高阻输入接到确认的 PA9 MCU/排针侧，GND
   共地；CH0--CH7 保持原角色但仍只是候选角色。DSView 以 CH0 上升触发、至少
   20 MHz、记录实际阈值和前触发比例，保留 raw、CSV、UART-decoder export。CH8
   解码必须为 115200, 8N1, LSB-first，且在同一候选 ELF hash 和一次 gate release
   中读出固件预期的 `RTD1` 记录/序号。只有空闲高、起始低、位时间约 8.68 us 而
   没有可复核字节时，结果是 `inconclusive`，不是 PA9 发射通过。本层只用于定位
   诊断，2026-07-29 的用户授权已将其排除在候选板冻结条件之外。
3. **可选 PA9 到板载 CH340 路径诊断层。** 只在第 1 步已将可安全探测的 CH340 RX 一侧
   测试点确定后，CH8 保持 PA9 MCU 侧、CH9 接该测试点，使用同一个 gate release。
   记录两通道的 raw/CSV、两路解码和边沿延迟/极性。两端均有相同字节可证明该段
   路径；仅 CH8 有字节表示路径、跳帽或测试点识别仍未决；仅 CH9 有字节表示 CH8
   连接/映射未决。绝不凭 CH340 的零字节反推任一层。本层只用于定位诊断；在主机
   CH340 文本见证已通过时，其 `pending`、`fail` 或 `inconclusive` 状态不阻止候选
   板冻结审查。
4. **主机 CH340 接收层。** 单独、持久、只读地打开
   `/dev/ttyUSB0`，保留 udev/VID:PID、termios、DTR/RTS 初末状态、开始/结束时间和
   原始日志。一次可识别的候选固件 gate release 后，日志必须含可配对的预期 `RTD1`
   字节才算该层通过。零字节的层状态是 `fail` 或 `inconclusive`，不是整板故障。
   此步骤是预检，不能打开正式 DSView 窗口。
5. **GPIO Observer 真值层。** 用独立的实际丝印、照片/手册和 DSLogic 波形证明
   CH0--CH7 分别接到候选 PC2、PC3、PC4、PC5、PC6、PC7、PB5、PB0，并逐条审计
   板载负载。至少在候选固件的已知变体中观察与每个角色一致的非平坦波形；不能由
   某个未验证的旧板 header 编号推断。PB5/PB0 的极性与负载未证明前不得复用旧
   LED 结论。此层与 PA9/CH340 层独立，不能用 UART 成功替代。

任何步骤可重做，前次失败/未决证据必须保留和 hash。预检期间可以按照单次门控
流程进行必要的、留痕的 SWD 操作，但每一次都标为 `nonformal`。预检绝不开始正式
DSView window。

## 冻结门

只有下列项目均有独立、hash 绑定的结论，才可在另一项明确授权的工作中提出新的
`board_id`、pin map、Capture metadata 模板与专用 validator 规则：物理丝印/跳帽/
负载审计；主机 CH340 接收；CH0--CH7 实体映射及负载；DSLogic 设备、通道、阈值、
速率、触发配置。物理映射和 GPIO Observer 必须为 `pass`。UART 见证采用
`ch340_host_witness_v1`，要求主机接收为 `pass`，并保留同次可识别的 `RTD1` 记录与
gate 关联。PA9 发射及 PA9--CH340 路径诊断层不是冻结条件；其已有原始证据必须保留，
但状态可为 `pending`、`pass`、`fail` 或 `inconclusive`。预检 manifest 的
`freeze_requested` 必须始终为 `false`，所以它本身不能满足该门。

冻结后的任何正式窗口必须先启动 DSView 和所有确定的串口读取器；随后禁止 halt、
read target、reset、断开 debugger 或重开串口，直到窗口结束。这个限制不追溯地使
预检成为 Capture。
