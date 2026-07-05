# Windows 外部环境执行计划（2026-03-27，`24h` 版）

## 1. 目的与范围

本文件用于指导在真实 Windows 外部环境中完成当前仍未闭环的 external 事项，并统一回答三件事：

1. 现在在 Windows 机器上还需要做什么
2. 需要传入什么东西
3. 具体按什么顺序、用什么命令执行

当前范围以现行权威状态为准：

1. `Phase 7` 已完成，`windows_linux_consistency` 已闭环，不再要求重跑 Windows/Linux compare。
2. 当前 Windows 外部环境仍需补齐的事项只有：
   - `NFR-PERF-05`
   - `NFR-PERF-01`
3. `NFR-STAB-01` 的正式门槛已收口为 `24h`，不再使用 `7x24`，且已按下述合同口径关闭。

`NFR-STAB-01` 项目级合同关闭口径（正式）：

1. Windows `desktop long soak >= 24h`。
2. Windows `irrecoverable_error_detected = false`。
3. `collector_soak_windows` 仅作为 supporting evidence 留档，不作为合同关闭门槛。

本文件与以下文档口径保持一致：

1. `docs/final_validation_status_20260314.json`
2. `docs/final_acceptance_readiness_20260314.md`
3. `docs/external_validation_checklist_20260314.md`
4. `docs/项目收口执行计划_20260326.md`

## 2. Windows 机器上需要准备和传入的东西

### 2.1 必须传入

1. 当前仓库工作副本一份，且路径中包含完整的 `tool/`、`desktop/`、`tests/`、`docs/` 目录。
2. formal 输入文件一份：
   - `D:\rttrace\formal_1gb_opaque.trace`
3. 本地 scratch 目录一份：
   - `D:\rttrace\scratch`
4. 可交互的 Windows 桌面会话：
   - `Windows 11`
   - 非 headless
   - 能以真实 GUI / onscreen 方式运行桌面程序

### 2.2 软件依赖

Windows 机器上需要：

1. `Python 3.10+`
2. `PySide6>=6.10,<6.11`
3. `pyqtgraph>=0.14,<0.15`
4. `numpy>=1.26`
5. `pytest>=8.0`

安装命令：

```powershell
python -m pip install "PySide6>=6.10,<6.11" "pyqtgraph>=0.14,<0.15" "numpy>=1.26" "pytest>=8.0"
```

### 2.3 不需要再额外传入的东西

当前不需要为了 Windows 外部执行再额外传入以下内容：

1. Linux compare 输入包
2. Linux package compare 目录
3. 已闭环的 `Phase 7` compare 临时产物

这些 compare 与 summary 回填动作在主工作区完成即可，不是本轮 Windows 外部机器的阻塞前置。

## 3. Windows 机器上现在要做什么

按当前状态，Windows 外部环境要完成的事情只有四类：

1. 跑一次真实 Windows 全量 `pytest`，并生成 audited JSON：
   - `docs/pytest_windows.json`
2. 跑 Windows 长稳相关产物：
   - `docs/collector_soak_windows.json`
   - `docs/desktop_long_duration_soak_windows.json`
   - `docs/irrecoverable_error_observation_windows.json`
3. 跑真实 GUI / onscreen FPS 报告：
   - `docs/render_fps_report_windows_formal_gui.json`
4. 跑 Windows collector perf：
   - `docs/collector_perf_baseline_windows.json`

如果你希望同一批次顺手刷新 Windows runtime 留档，也可以额外执行：

1. `python tool/run_desktop_runtime_report.py --output docs/desktop_runtime_windows.json`

但这不是当前剩余 external backlog 的主阻塞项。

## 4. 具体执行步骤

以下命令统一假定：

1. 在仓库根目录执行
2. 使用 Windows `PowerShell`
3. 不修改输出文件名
4. 不用裸 `pytest`
5. 不把输出落到占位路径或临时假路径

### 4.1 Step 0：环境准备

确认输入与 scratch 路径存在：

```powershell
Test-Path D:\rttrace\formal_1gb_opaque.trace
Test-Path D:\rttrace\scratch
```

安装依赖：

```powershell
python -m pip install "PySide6>=6.10,<6.11" "pyqtgraph>=0.14,<0.15" "numpy>=1.26" "pytest>=8.0"
```

如果 `D:\rttrace\scratch` 不存在，先创建：

```powershell
New-Item -ItemType Directory -Force D:\rttrace\scratch
```

### 4.2 Step 1：Windows 全量回归与 audited 留档

先跑真实 Windows 全量 `pytest`：

```powershell
python -m pytest tests/python -q
```

再落 audited JSON：

```powershell
python tool/run_pytest_report.py --output docs/pytest_windows.json
```

如需同步刷新 runtime unittest 留档，可额外执行：

```powershell
python tool/run_desktop_runtime_report.py --output docs/desktop_runtime_windows.json
```

本步产物：

1. `docs/pytest_windows.json`
2. 可选：`docs/desktop_runtime_windows.json`

### 4.3 Step 2：Windows collector soak

执行命令：

```powershell
python tool/run_collector_soak.py --output docs/collector_soak_windows.json
```

本步产物：

1. `docs/collector_soak_windows.json`

### 4.4 Step 3：Windows desktop long soak 与不可恢复错误观测

建议先做一次 `2h` 预跑，用于确认环境稳定；但正式留档只认 `24h` 产物。

`24h` 正式命令：

```powershell
python tool/run_desktop_long_soak.py --input D:\rttrace\formal_1gb_opaque.trace --duration-s 86400 --output docs/desktop_long_duration_soak_windows.json --irrecoverable-output docs/irrecoverable_error_observation_windows.json
```

本步产物：

1. `docs/desktop_long_duration_soak_windows.json`
2. `docs/irrecoverable_error_observation_windows.json`

判定要求：

1. 只有 `24h` artifact 才能用于关闭 `NFR-STAB-01`
2. 若出现 `failed`、崩溃、卡死或不可恢复错误观测，则不得宣称长稳闭环
3. 若 `2h` 预跑已经失败，应先排查环境或缺陷，不要直接进入 `24h`

### 4.5 Step 4：Windows formal GUI FPS

必须在真实 GUI / onscreen 会话中执行：

```powershell
python tool/run_render_fps_report.py --tile-count 100000 --iterations 120 --evidence-scope formal_gui_onscreen --output docs/render_fps_report_windows_formal_gui.json
```

本步产物：

1. `docs/render_fps_report_windows_formal_gui.json`

判定要求：

1. 新产物必须使用 `--evidence-scope formal_gui_onscreen`，且报告不得落入 offscreen 执行（如 `offscreen`/`qpa:offscreen`）。
2. legacy 报告若仍带 `synthetic_workload`，只有在满足 `100k tiles`、`verdict.pass=true`、Qt 平台非 offscreen 且有明确 onscreen 证据（`execution` 或 `notes` 可推断）时，才允许进入 formal close。
3. legacy offscreen 报告一律不能关闭 `NFR-PERF-05`。

### 4.6 Step 5：Windows collector perf

执行命令：

```powershell
python tool/run_collector_perf_baseline.py --output docs/collector_perf_baseline_windows.json
```

本步产物：

1. `docs/collector_perf_baseline_windows.json`

判定要求：

1. 所有 scenario 都应满足 `events_per_sec >= 1,000,000`
2. 若任何 scenario 不达标，则 `NFR-PERF-01` 不能关闭

## 5. 执行顺序建议

建议按下面顺序跑，不要打乱：

1. 环境准备
2. `python -m pytest tests/python -q`
3. `python tool/run_pytest_report.py --output docs/pytest_windows.json`
4. `python tool/run_collector_soak.py --output docs/collector_soak_windows.json`
5. `python tool/run_desktop_long_soak.py ... --duration-s 86400 ...`
6. `python tool/run_render_fps_report.py --tile-count 100000 --iterations 120 --evidence-scope formal_gui_onscreen --output docs/render_fps_report_windows_formal_gui.json`
7. `python tool/run_collector_perf_baseline.py --output docs/collector_perf_baseline_windows.json`

理由：

1. 先确认 Python 回归通过，再进行长稳和性能 external 批次。
2. `collector soak` 成本较低，先补最直接。
3. `desktop long soak` 耗时最长，应尽早启动。
4. GUI FPS 必须在真实桌面会话中做，适合在长稳执行窗口内或之后安排。
5. collector perf 是短任务，通常可在同批次末尾完成。

## 6. 需要回传给主工作区的东西

本轮 Windows 外部执行完成后，至少需要把以下产物回传到仓库同名路径：

1. `docs/pytest_windows.json`
2. `docs/collector_soak_windows.json`
3. `docs/desktop_long_duration_soak_windows.json`
4. `docs/irrecoverable_error_observation_windows.json`
5. `docs/render_fps_report_windows_formal_gui.json`
6. `docs/collector_perf_baseline_windows.json`

可选补充回传：

1. `docs/desktop_runtime_windows.json`
2. 失败截图
3. 控制台日志
4. 若 `24h` 中途失败，对应的崩溃时间点、现象和机器状态说明

## 7. 失败时怎么处理

若 Windows 外部执行中任何一步失败，处理原则如下：

1. 不要伪造或手改 JSON 产物。
2. 保留已生成的真实输出、日志和截图。
3. 失败在 `pytest`，先停在回归层，不继续做 formal 结论。
4. 失败在 `24h` soak，记录中断时间、报错和机器状态，作为整改输入回传。
5. GUI FPS 若仍是 offscreen/synthetic，不算正式闭环，只回传证据并标记 limitation。
6. collector perf 若不达标，直接按 `failed` 证据回传，不要重写验收门槛。

## 8. 回传后主工作区要做什么

Windows 机器只负责生成 Windows 侧真实产物。回传后，主工作区再做以下动作：

1. 把回传文件放回仓库 `docs/` 同名路径
2. 刷新 `docs/final_validation_status_20260314.json`
3. 刷新 `docs/final_acceptance_readiness_20260314.md`
4. 刷新 `docs/traceability_matrix.json`
5. 根据结果决定 `NFR-STAB-01`、`NFR-PERF-05`、`NFR-PERF-01` 是否进入 `closed`

## 9. 一句话结论

按当前项目状态，Windows 外部环境不需要再重跑 compare 主线；`NFR-STAB-01` 已按 Windows `24h` 正式口径闭环。若需复核，可按本文件 `4.4` 重跑长稳；当前外部主阻塞聚焦 `NFR-PERF-05` 与 `NFR-PERF-01` 的最终 closed 收口。
