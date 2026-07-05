# 磁盘清理报告 2026-07-04

## 目标

清理本仓库中的临时结果、过时 Windows 交付副本、重复压缩包和可再生构建产物，同时保留当前仍被正式文档引用或被当前状态文档指定为锚点的结果归档。

## 审查依据

- 当前运行时优化锚点以 `realization/docs/runtime_optimization_current_status_20260704.md` 为准：
  - closeout 根目录为 `realization/docs/runtime_optimization_closeout_20260701`
  - 补充产品证据根目录为 `realization/docs/runtime_optimization_product_evidence_20260703`
- Windows 正式 parity 结果当前以 `windows-3` 为准，见：
  - `windows-3/wp05_windows_artifact_audit_20260429.md`
  - `realization/docs/result/patent_improvement_metrics_20260429.md`
  - `realization/docs/patent_final_validation_entry_20260415.md`
- `Windows/soak/windows_wp06_soak_scope_note.json` 仍被正式验证入口文档引用，因此保留。
- `realization/.gitignore` 已明确将 `tmp/`、`build/`、`*.trace`、`.env*` 视为非长期归档对象。

## 保留的正式结果锚点

- `windows-3/formal_10_4_wp05_pass`
- `realization/docs/evidence_proof_archive_20260418`
- `realization/docs/evidence_proof_archive_20260421`
- `realization/docs/evidence_proof_archive_20260506`
- `realization/docs/runtime_optimization_closeout_20260701`
- `realization/docs/runtime_optimization_product_evidence_20260703`
- `Windows/soak/windows_wp06_soak_scope_note.json`

## 删除清单

| 路径 | 删除前大小 | 结果作用 | 判定 | 删除理由 | 保留替代 |
| --- | ---: | --- | --- | --- | --- |
| `realization/tmp` | 279G | 临时跑数目录，包含 formal matrix scratch、最小证据试跑、GAP 草稿、候选状态文件等 | 删除 | 明确临时产物，且当前 closeout/正式归档已另存到 `realization/docs/*` | `realization/docs/runtime_optimization_closeout_20260701`、`realization/docs/runtime_optimization_product_evidence_20260703` |
| `Windows/A_control_plane_first` | 13G | 早期 Windows A 组 staging 结果 | 删除 | 已被 `windows-3` 的最终审计版覆盖 | `windows-3/formal_10_4_wp05_pass/A_control_plane_first` |
| `Windows/B_budget_pre_freeze` | 525M | 早期 Windows B 组 staging 结果 | 删除 | 已被 `windows-3` 的最终审计版覆盖 | `windows-3/formal_10_4_wp05_pass/B_budget_pre_freeze` |
| `Windows/C_degraded_audit` | 932K | 早期 Windows C 组 staging 结果 | 删除 | 已被 `windows-3` 的最终审计版覆盖 | `windows-3/formal_10_4_wp05_pass/C_degraded_audit` |
| `Windows/preparation` | 20K | 早期 Windows preparation 报告 | 删除 | 已过时，且正式材料以 `windows-3`/正式入口文档为准 | `windows-3/formal_10_4_wp05_pass/preparation` |
| `Windows/windows_artifact_manifest.json` | 24K | 早期 Windows 交付 manifest | 删除 | 已被最终 parity 审计根覆盖 | `windows-3/formal_10_4_wp05_pass/windows_artifact_manifest.json` |
| `Windows/windows_final_readiness_report.json` | 4K | 早期 Windows readiness 报告 | 删除 | 已被最终 parity 审计根覆盖 | `windows-3/formal_10_4_wp05_pass/windows_final_readiness_report.json` |
| `Windows/WP08_repo_regression_windows` | 12K | Windows 回归日志副本 | 删除 | 小体积但已过时，且不再是当前正式锚点 | 正式结论已写入 `windows-3` 审计文档和 `realization/docs/result/*` |
| `Windows-2/A_control_plane_first` | 14G | 2026-04-27 阶段的 Windows A 组中间交付树 | 删除 | 被 `windows-3` 最终审计根取代 | `windows-3/formal_10_4_wp05_pass/A_control_plane_first` |
| `Windows-2/B_budget_pre_freeze` | 860M | 2026-04-27 阶段的 Windows B 组中间交付树 | 删除 | 被 `windows-3` 最终审计根取代 | `windows-3/formal_10_4_wp05_pass/B_budget_pre_freeze` |
| `Windows-2/C_degraded_audit` | 1.5M | 2026-04-27 阶段的 Windows C 组中间交付树 | 删除 | 被 `windows-3` 最终审计根取代 | `windows-3/formal_10_4_wp05_pass/C_degraded_audit` |
| `Windows-2/preparation` | 44K | 2026-04-27 阶段的 Windows preparation 中间报告 | 删除 | 被后续最终审计根覆盖 | `windows-3/formal_10_4_wp05_pass/preparation` |
| `Windows-2/windows_formal_10_4_core_delivery_20260427.tar.gz` | 24K | 旧版核心文档交付包 | 删除 | 旧交付压缩包，已被 `windows-3` 审计版和其 tarball 取代 | `windows-3/formal_10_4_wp05_pass` |
| `Windows-2/windows_formal_10_4_packages_delivery_20260427.tar.gz` | 14M | 旧版 packages 交付包 | 删除 | 旧交付压缩包，已过时 | `windows-3/formal_10_4_wp05_pass` |
| `Windows-2/windows_formal_10_4_sidecar_delivery_20260427.tar.gz` | 2.3G | 旧版 sidecar 交付包 | 删除 | 与当前最终审计根职责重复，且版本较早 | `windows-3/formal_10_4_wp05_pass` |
| `Windows-2/evidence_proof_archive_20260427/formal_10_4_wp05_pass` | 15G | 2026-04-27 的 WP05 pass 抽取结果 | 删除 | 2026-04-29 的 `windows-3` 为后续审计确认后的正式根 | `windows-3/formal_10_4_wp05_pass` |
| `Windows-2/evidence_proof_archive_20260427/formal_10_4_wp05_pass.tar.gz` | 2.4G | 上述旧版 WP05 pass 的压缩包 | 删除 | 旧版压缩包，且已存在更新版 `windows-3` tarball | `windows-3/formal_10_4_wp05_pass.tar.gz` |
| `soak.zip` | 2.3G | `Windows` 早期结果树的压缩副本 | 删除 | 与已删的 `Windows/*` staging 树重复；唯一仍需保留的 soak 说明已保存在单独 JSON 中 | `Windows/soak/windows_wp06_soak_scope_note.json` |
| `realization/build` | 4.0M | 本地构建输出 | 删除 | 可再生构建产物 | 源码仍在 `realization/` |
| `realization/build-local-audit` | 3.1M | 本地 audit 构建输出 | 删除 | 可再生产物 | 源码仍在 `realization/` |
| `realization/build-rttrace-dev` | 3.2M | 本地 dev 构建输出 | 删除 | 可再生产物 | 源码仍在 `realization/` |
| `realization.zip` | 3.2G | `realization/` 目录整包备份 | 删除 | 与现存工作树重复的归档包 | 当前工作树 `realization/` |
| `realization-v1.zip` | 3.2G | `realization/` 的另一版整包备份 | 删除 | 与现存工作树重复的归档包 | 当前工作树 `realization/` |

## 预计释放空间

- 精确统计：`358324635459` bytes
- 约等于：`358.3 GB`（十进制）
- 约等于：`333.7 GiB`（二进制）

## 说明

- 本次不删除 `windows-3/formal_10_4_wp05_pass.tar.gz`，因为当前正式验证入口和审计文档仍直接引用该交付包。
- 本次不删除 `realization/docs/evidence_proof_archive_20260506` 与 `realization/docs/runtime_optimization_closeout_20260701`，因为它们仍是当前运行时优化与专利结果文档的正式锚点。
