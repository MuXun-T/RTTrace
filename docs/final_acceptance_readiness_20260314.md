# 最终验收准备摘要（2026-03-29，最终收口同步版）

## 专利范围注释（2026-04-15）

本文件是项目级（project-level）验收准备摘要，不是专利 `10.4` formal close 的最终裁决入口。  
若与专利线文档存在口径差异，专利闭环裁决以以下文档为准：

1. `docs/patent_final_validation_entry_20260415.md`
2. `docs/patent_final_validation_index_20260415.json`
3. `docs/patent_scope_bridge_20260415.md`

## 当前状态

项目当前已进入“控制面与权威文档口径统一收口”状态。按 `docs/final_validation_status_20260314.json` 的最新 machine-readable 结果：

1. `external_pending = []`
2. `external_status.windows_linux_consistency = closed`
3. `external_status.long_duration_stability = closed`
4. `nfr_perf_status.NFR-PERF-01 = closed`
5. `nfr_perf_status.NFR-PERF-05 = closed`
6. `nfr_perf_status.NFR-PERF-06 = closed`

当前主 summary 关键状态：

1. Linux / Windows 一致性 compare 均匹配：`acceptance_compare_match = true`、`package_compare_match = true`
2. Windows collector perf 三个场景均满足 `>= 1,000,000 events/s`
3. Windows `desktop long soak` 已达到 `24h`，且 `irrecoverable_error_detected = false`
4. Windows `pytest` audited artifact 已回传并保留在 canonical 路径 `docs/pytest_windows.json`

当前回归基线（`2026-03-29`）：

1. 命令：`python3 -m pytest tests/python -q`
2. 结果：`241 passed, 26 subtests passed`
3. 该数字是当次回归快照；后续以最新一次同命令实跑结果为准，不在其他文档重复固化旧数字。
全局闭环状态以 `docs/final_validation_status_20260314.json` 为准。

`NFR-STAB-01` 项目级正式关闭口径（已生效）：

1. 合同判据是 Windows `desktop long soak >= 24h` 且 `irrecoverable_error_detected = false`
2. `collector_soak_windows` 为 supporting evidence，保留审计价值，但不作为合同关闭门槛
3. 该口径与需求规格中 `NFR-STAB-01` 的 `24h` 门槛一致，不使用 `7x24`

## 当前可引用产物

1. `docs/final_validation_status_20260314.json`
2. `docs/traceability_matrix.json`
3. `docs/external_validation_checklist_20260314.md`
4. `docs/acceptance_baseline_20260325_linux_formal.json`
5. `docs/acceptance_baseline_windows_formal.json`
6. `docs/acceptance_compare_windows_linux.json`
7. `docs/package_compare_windows_linux.json`
8. `docs/desktop_preflight_20260325_linux_formal_1gb_opaque.json`
9. `docs/desktop_perf_acceptance_20260325_linux_formal_1gb_opaque.json`
10. `docs/desktop_perf_acceptance_windows_formal.json`
11. `docs/desktop_runtime_stage5_linux_venv.json`
12. `docs/desktop_runtime_windows.json`
13. `docs/collector_perf_baseline_20260316_linux.json`
14. `docs/collector_perf_baseline_windows.json`
15. `docs/collector_soak_20260316_linux.json`
16. `docs/collector_soak_windows.json`
17. `docs/desktop_long_duration_soak_linux.json`
18. `docs/desktop_long_duration_soak_windows.json`
19. `docs/irrecoverable_error_observation_linux.json`
20. `docs/irrecoverable_error_observation_windows.json`
21. `docs/render_fps_report_windows_formal_gui.json`
22. `docs/pytest_windows.json`

## 审阅结论

1. 当前控制面四份权威文档已按同一口径收敛：`final_validation_status` / `readiness` / `traceability` / `checklist`
2. 不再保留“Windows pytest 未回传 / Windows collector perf 未闭环 / GUI FPS 未闭环 / 长稳未闭环”的旧表述
3. 需求文档第 `7` 章对应 `NFR` 在主状态层已无活动 pending 项，当前 `external_pending = []`

## 后续建议

1. 后续若重新触发 external 批次，先按 checklist 冻结命令重跑，再刷新 summary
2. 若任一闭环项复测失败，必须把对应状态回退为非 `closed`，不得保留旧 `closed` 结论
3. `docs/pytest_windows.json` 继续作为 Windows 全量 `python -m pytest tests/python -q` 的 canonical audited artifact
