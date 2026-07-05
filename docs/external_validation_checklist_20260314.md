# 外部验证清单（2026-03-29，Final 收口同步版）

## 0. 专利范围注释（2026-04-15）

本清单是项目级 external 复核清单，不直接等价于专利 `10.4` formal close gate。  
专利线收口应按以下文档执行与裁决：

1. `docs/patent_10_4_formal_close_min_contract_20260415.md`
2. `docs/patent_final_validation_entry_20260415.md`
3. `docs/patent_final_validation_index_20260415.json`
4. `docs/patent_scope_bridge_20260415.md`
5. `docs/patent_external_blocker_register_20260415.md`

## 1. 目的

本文件用于在最终收口后冻结 external 复核与复测入口，统一约束以下内容：

1. 精确命令
2. 依赖版本
3. 产物命名规则
4. `summary` 回填方式

后续若触发复测批次，只认本文件与 `docs/final_validation_status_20260314.json` 的最新口径。

## 2. 当前权威状态

截至 `2026-03-29`，当前主状态已统一为：

1. Linux formal `>= 1GB` 已闭环
   - `desktop_1gb_first_screen_lt_10s = closed`
   - `desktop_peak_memory_lt_4gb = closed`
   - `NFR-PERF-06 = closed`
2. `docs/final_validation_status_20260314.json::external_pending = []`
3. 以下关键状态均已 `closed`：
   - `external_status.windows_linux_consistency`
   - `external_status.long_duration_stability`
   - `nfr_perf_status.NFR-PERF-01`
   - `nfr_perf_status.NFR-PERF-05`
4. `NFR-PERF-02` 当前按实现约束与回归证据视为已满足，不再列入 external backlog。
5. 不再接受以下旧口径：
   - 把 Linux `1GB` 三项写成 `pending_external`
   - 用裸 `pytest` 替代 `python3 -m pytest tests/python -q`
   - 在正式命令中使用 `/path/to/...` 或等价占位输出路径

## 3. 依赖版本冻结

### 3.1 运行环境

1. Windows `11`
2. Ubuntu `22.04`
3. Python `3.10+`

### 3.2 Python 依赖

1. `PySide6>=6.10,<6.11`
2. `pyqtgraph>=0.14,<0.15`
3. `numpy>=1.26`
4. `pytest>=8.0`

Windows 安装命令：

```bash
python -m pip install "PySide6>=6.10,<6.11" "pyqtgraph>=0.14,<0.15" "numpy>=1.26" "pytest>=8.0"
```

Linux 安装命令：

```bash
python3 -m pip install "PySide6>=6.10,<6.11" "pyqtgraph>=0.14,<0.15" "numpy>=1.26" "pytest>=8.0"
```

## 4. 路径与命名冻结

### 4.1 输入与 scratch 路径

1. Windows formal input：`D:\rttrace\formal_1gb_opaque.trace`
2. Windows scratch：`D:\rttrace\scratch`
3. Linux formal input：`/tmp/formal-google-cluster/formal_1gb_opaque.trace`
4. Linux scratch：`/tmp/formal-google-cluster/scratch`

若输入路径或 scratch 路径必须变更，必须先同步更新：

1. `docs/项目收口执行计划_20260326.md`
2. `docs/external_validation_checklist_20260314.md`

不得直接修改执行命令而不更新文档。

### 4.2 Canonical artifact 命名

1. `docs/desktop_preflight_windows_formal.json`
2. `docs/desktop_env_windows.json`
3. `docs/desktop_runtime_windows.json`
4. `docs/acceptance_baseline_windows_formal.json`
5. `docs/desktop_perf_acceptance_windows_formal.json`
6. `docs/package_windows_formal`
7. `docs/acceptance_baseline_20260325_linux_formal.json`
8. `docs/package_linux_formal`
9. `docs/acceptance_compare_windows_linux.json`
10. `docs/package_compare_windows_linux.json`
11. `docs/collector_soak_windows.json`
12. `docs/desktop_long_duration_soak_windows.json`
13. `docs/irrecoverable_error_observation_windows.json`
14. `docs/render_fps_report_linux_formal_gui.json`
15. `docs/render_fps_report_windows_formal_gui.json`
16. `docs/collector_perf_baseline_windows.json`
17. `docs/pytest_windows.json`

说明：

1. `docs/final_validation_status_20260314.json` 仍是唯一主 summary 文件。
2. 阶段性原始日志、截图、额外报告可放在外部工作区，但主 summary 只引用上述 canonical 文件名。

## 5. 执行前强制回归

Linux 本地必须先执行：

```bash
python3 -m pytest tests/python -q
g++ -std=c++17 -pthread -Icollector/include collector/core/trace_collector.cpp tests/cpp/test_collector.cpp -o build/trace_collector_tests && ./build/trace_collector_tests
```

Windows 外部执行前至少必须执行：

```bash
python -m pytest tests/python -q
```

Windows audited 留档入口：

```bash
python tool/run_pytest_report.py --output docs/pytest_windows.json
```

说明：

1. 该工具内部固定执行 `tests/python -q`，用于把 Windows 全量 `python -m pytest tests/python -q` 结果落为结构化 JSON。
2. `docs/desktop_runtime_windows.json` 仅为 runtime unittest 报告，不等价于 full pytest report。
3. `docs/pytest_windows.json` 用于 Windows 全量 pytest audited 留档；当前主 summary 不直接消费该文件，但审计包应保留。

## 6. 命令冻结

### 6.1 Final 主 summary 刷新命令

```bash
python3 tool/summarize_validation_status.py \
  --acceptance docs/acceptance_baseline_20260325_linux_formal.json \
  --desktop-preflight docs/desktop_preflight_20260325_linux_formal_1gb_opaque.json \
  --desktop-env docs/desktop_env_stage5_linux.json \
  --desktop-runtime docs/desktop_runtime_stage5_linux_venv.json \
  --desktop-perf docs/desktop_perf_acceptance_20260325_linux_formal_1gb_opaque.json \
  --collector-soak docs/collector_soak_20260316_linux.json \
  --collector-perf docs/collector_perf_baseline_20260316_linux.json \
  --windows-collector-perf docs/collector_perf_baseline_windows.json \
  --desktop-soak docs/desktop_long_duration_soak_linux.json \
  --irrecoverable-error docs/irrecoverable_error_observation_linux.json \
  --windows-desktop-soak docs/desktop_long_duration_soak_windows.json \
  --windows-irrecoverable-error docs/irrecoverable_error_observation_windows.json \
  --render-fps docs/render_fps_report_windows_formal_gui.json \
  --windows-acceptance docs/acceptance_baseline_windows_formal.json \
  --windows-desktop-runtime docs/desktop_runtime_windows.json \
  --windows-desktop-perf docs/desktop_perf_acceptance_windows_formal.json \
  --windows-collector-soak docs/collector_soak_windows.json \
  --acceptance-compare docs/acceptance_compare_windows_linux.json \
  --package-compare docs/package_compare_windows_linux.json \
  --output docs/final_validation_status_20260314.json
```

### 6.2 Phase 7 Windows 同口径 artifact

```bash
python tool/desktop_validation_preflight.py \
  --baseline-input D:\\rttrace\\formal_1gb_opaque.trace \
  --candidate-input D:\\rttrace\\formal_1gb_opaque.trace \
  --scratch-dir D:\\rttrace\\scratch \
  --load-timeout-s 600 \
  --output docs/desktop_preflight_windows_formal.json

python tool/check_desktop_env.py --output docs/desktop_env_windows.json

python tool/run_desktop_runtime_report.py \
  --output docs/desktop_runtime_windows.json

python tool/run_pytest_report.py \
  --output docs/pytest_windows.json

python tool/run_acceptance_baseline.py \
  --baseline-input D:\\rttrace\\formal_1gb_opaque.trace \
  --candidate-input D:\\rttrace\\formal_1gb_opaque.trace \
  --output docs/acceptance_baseline_windows_formal.json

python tool/run_acceptance_baseline.py \
  --mode desktop_perf_acceptance \
  --acceptance-scope perf_only \
  --profile medium \
  --soak-iterations 1 \
  --baseline-input D:\\rttrace\\formal_1gb_opaque.trace \
  --candidate-input D:\\rttrace\\formal_1gb_opaque.trace \
  --load-timeout-s 600 \
  --workdir D:\\rttrace\\scratch \
  --output docs/desktop_perf_acceptance_windows_formal.json

python -m desktop.app.cli export \
  --input D:\\rttrace\\formal_1gb_opaque.trace \
  --output-dir docs\\package_windows_formal
```

### 6.3 Phase 7 Linux compare 基线与 compare

```bash
python3 tool/run_acceptance_baseline.py \
  --baseline-input /tmp/formal-google-cluster/formal_1gb_opaque.trace \
  --candidate-input /tmp/formal-google-cluster/formal_1gb_opaque.trace \
  --output docs/acceptance_baseline_20260325_linux_formal.json

python3 -m desktop.app.cli export \
  --input /tmp/formal-google-cluster/formal_1gb_opaque.trace \
  --output-dir docs/package_linux_formal

python3 tool/compare_acceptance_baselines.py \
  --left docs/acceptance_baseline_windows_formal.json \
  --right docs/acceptance_baseline_20260325_linux_formal.json \
  --output docs/acceptance_compare_windows_linux.json

python3 tool/compare_normalized_packages.py \
  --left docs/package_windows_formal \
  --right docs/package_linux_formal \
  --output docs/package_compare_windows_linux.json
```

Phase 7 完成后的 summary 刷新命令：

```bash
python3 tool/summarize_validation_status.py \
  --acceptance docs/acceptance_baseline_20260325_linux_formal.json \
  --desktop-preflight docs/desktop_preflight_20260325_linux_formal_1gb_opaque.json \
  --desktop-env docs/desktop_env_stage5_linux.json \
  --desktop-runtime docs/desktop_runtime_stage5_linux_venv.json \
  --desktop-perf docs/desktop_perf_acceptance_20260325_linux_formal_1gb_opaque.json \
  --collector-soak docs/collector_soak_20260316_linux.json \
  --collector-perf docs/collector_perf_baseline_20260316_linux.json \
  --desktop-soak docs/desktop_long_duration_soak_linux.json \
  --irrecoverable-error docs/irrecoverable_error_observation_linux.json \
  --render-fps docs/render_fps_report_linux.json \
  --windows-acceptance docs/acceptance_baseline_windows_formal.json \
  --windows-desktop-runtime docs/desktop_runtime_windows.json \
  --windows-desktop-perf docs/desktop_perf_acceptance_windows_formal.json \
  --windows-collector-soak docs/collector_soak_windows.json \
  --windows-desktop-soak docs/desktop_long_duration_soak_windows.json \
  --windows-irrecoverable-error docs/irrecoverable_error_observation_windows.json \
  --acceptance-compare docs/acceptance_compare_windows_linux.json \
  --package-compare docs/package_compare_windows_linux.json \
  --output docs/final_validation_status_20260314.json
```

### 6.4 Phase 8A 长稳与不可恢复错误

Windows collector soak：

```bash
python tool/run_collector_soak.py --output docs/collector_soak_windows.json
```

Linux desktop long soak：

```bash
python3 tool/run_desktop_long_soak.py \
  --input /tmp/formal-google-cluster/formal_1gb_opaque.trace \
  --duration-s 86400 \
  --output docs/desktop_long_duration_soak_linux.json \
  --irrecoverable-output docs/irrecoverable_error_observation_linux.json
```

Windows desktop long soak：

```bash
python tool/run_desktop_long_soak.py \
  --input D:\\rttrace\\formal_1gb_opaque.trace \
  --duration-s 86400 \
  --output docs/desktop_long_duration_soak_windows.json \
  --irrecoverable-output docs/irrecoverable_error_observation_windows.json
```

说明：

1. 建议先以 `7200` 秒进行预跑，正式长稳留档以 `86400` 秒为准。
2. 只有 `24h` 级别 artifact 才能用于关闭 `NFR-STAB-01`。
3. `NFR-STAB-01` 的合同关闭判据是：Windows `desktop long soak >= 24h` 且 `irrecoverable_error_detected = false`。
4. `collector_soak_windows` 作为 supporting evidence 保留和记录，不作为 `NFR-STAB-01` 的合同关闭门槛。

### 6.5 Phase 8B formal GUI FPS

Linux onscreen 报告：

```bash
python3 tool/run_render_fps_report.py \
  --tile-count 100000 \
  --iterations 120 \
  --evidence-scope formal_gui_onscreen \
  --output docs/render_fps_report_linux_formal_gui.json
```

Windows onscreen 报告：

```bash
python tool/run_render_fps_report.py \
  --tile-count 100000 \
  --iterations 120 \
  --evidence-scope formal_gui_onscreen \
  --output docs/render_fps_report_windows_formal_gui.json
```

说明：

1. 新产物必须使用 `--evidence-scope formal_gui_onscreen`，且报告不能是 offscreen 执行（如 `offscreen` 或 `qpa:offscreen`）。
2. legacy 报告若仍带 `synthetic_workload`，只有在满足 `100k tiles`、`verdict.pass=true`、Qt 平台非 offscreen 且 `execution/notes` 可推断 onscreen 证据时，才允许进入 formal close。
3. legacy offscreen 报告一律不能关闭 `NFR-PERF-05`。

### 6.6 Phase 9 Windows collector perf

```bash
python tool/run_collector_perf_baseline.py --output docs/collector_perf_baseline_windows.json
```

Phase 9 完成后的 summary 刷新命令：

```bash
python3 tool/summarize_validation_status.py \
  --acceptance docs/acceptance_baseline_20260325_linux_formal.json \
  --desktop-preflight docs/desktop_preflight_20260325_linux_formal_1gb_opaque.json \
  --desktop-env docs/desktop_env_stage5_linux.json \
  --desktop-runtime docs/desktop_runtime_stage5_linux_venv.json \
  --desktop-perf docs/desktop_perf_acceptance_20260325_linux_formal_1gb_opaque.json \
  --collector-soak docs/collector_soak_20260316_linux.json \
  --collector-perf docs/collector_perf_baseline_20260316_linux.json \
  --windows-collector-perf docs/collector_perf_baseline_windows.json \
  --desktop-soak docs/desktop_long_duration_soak_linux.json \
  --irrecoverable-error docs/irrecoverable_error_observation_linux.json \
  --render-fps docs/render_fps_report_windows_formal_gui.json \
  --windows-acceptance docs/acceptance_baseline_windows_formal.json \
  --windows-desktop-runtime docs/desktop_runtime_windows.json \
  --windows-desktop-perf docs/desktop_perf_acceptance_windows_formal.json \
  --windows-collector-soak docs/collector_soak_windows.json \
  --windows-desktop-soak docs/desktop_long_duration_soak_windows.json \
  --windows-irrecoverable-error docs/irrecoverable_error_observation_windows.json \
  --acceptance-compare docs/acceptance_compare_windows_linux.json \
  --package-compare docs/package_compare_windows_linux.json \
  --output docs/final_validation_status_20260314.json
```

## 7. 当前通过判据

Final 收口后，本清单的通过判据固定为：

1. 不再存在“summary 已闭环但 checklist 仍写 pending_external”的冲突。
2. `external_pending = []`，且 `external_status + nfr_perf_status` 全部为 `closed`。
3. 后续 external 执行命令只使用本文件冻结的命令、版本与输出文件名。
4. compare 固定使用 `docs/package_windows_formal` 与 `docs/package_linux_formal`，不得再使用临时占位目录。
