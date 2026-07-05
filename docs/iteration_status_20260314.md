# 迭代状态纪要（2026-03-23，同步修订）

> 边界说明：本文件用于记录 `2026-03-23` 阶段快照，不作为当前状态真相源。当前项目状态以 `docs/final_validation_status_20260314.json` 为准；摘要结论以 `docs/final_acceptance_readiness_20260314.md` 为准。

## 本轮已完成

1. 采集端输出通道补强：
   - `collector/core/trace_collector.cpp` 现在支持基于 `output_open/output_write/output_close` hook 的文件、串口、网络三类输出路径。
   - `tests/cpp/test_collector.cpp` 新增了 hook 通道覆盖，验证 `TRACE_CHANNEL_FILE / SERIAL / NETWORK` 都能完成写出。
   - `collector/core/trace_collector.cpp` 已补齐 `flush_threshold_bytes` 与 `segment_duration_ns` 的正式行为语义；当前在 `auto_flush=1` 时，阈值配置会触发自动 flush，而分段时长也会驱动新的 segment rotation。
   - `tests/cpp/test_collector.cpp` 已新增专项回归，验证阈值自动 flush 与按时长分段旋转不再是死配置字段。

2. 桌面分析端在线链路补强：
   - `desktop/services.py` 与 `desktop/app/cli.py` 已补齐 `serial` 在线输入，当前支持 `file / socket / serial` 三类分析侧在线通道。
   - `tests/python/test_online_channel.py` 已补 `serial` 伪终端回归测试，验证离线解析与在线串口增量解析结果一致。

3. 桌面分析端能力补强：
   - `desktop/services.py` 为指标查询补充了按时间桶切分的 `bucket_series`，工作台可显示真实时间桶趋势而不再只有总量柱图。
   - 资源视图补充了 `hold_edges`、`wait_chains`，能直接看到等待链摘要。
   - `desktop/services.py` 已新增正式资源下钻接口，当前会把等待链、关键证据、相关状态段与推荐跳转窗口组织成结构化结果。
   - `desktop/app/gui.py` 的资源点击已不再停留在占位 note；当前会展示“资源 / 热点 / 关键等待链 / 相关事件 / 推荐跳转”的结构化摘要，并统一归一化 `resource_id` 输入。
   - 对比明细面已从原始 dict dump 收口成更清晰的证据摘要组织，能更直接看到 `baseline_view / candidate_view / delta_payload / related_events`，而不是只剩原始 JSON dump。
   - `viz_QueryTaskStates` 已开始区分 `lane_group=core` 与 `lane_group=task`。

4. 对齐与科研可信度修正：
   - `parser/align.py` 不再因为“看到校准事件”就误判 `align_calibrated=true`。
   - 当多核数据在所有活跃核上都具备校准锚点时，执行最小可验证的锚点对齐；否则退化为稳定排序并保留 `ALIGN_DEGRADED` 标记。

5. 离线滚动分片解析补强：
   - `parser/pipeline.py` 现在支持目录级 `.trace` 分片装载，会在多文件输入时自动跳过后续分片重复的全局头。
   - `tests/python/test_pipeline.py` 已新增目录分片解析与重复加载稳定性测试，验证文件边界不影响统一事件流结果。
   - 离线入口已从整文件 `read_bytes()` 收口为顺序流式读取；新增了“不依赖 `Path.read_bytes()`”“小块读取下目录分片仍正确跳过重复全局头”“非法 `read_size` 明确拒绝”的结构回归。

6. 指标深度补强：
   - `metric/core.py` 新增 `ready_wait_time` 与 `response_time` 指标。
   - 对比链路默认也会纳入这两个指标。

7. 格式与一致性验收证据补强：
   - `tests/python/test_pipeline.py` 已新增 `CRC_FAIL` 和 `DICT_MISMATCH` 回归测试。
   - `spec/schema_loader.py`、`spec/events.py`、`parser/codec.py`、`parser/pipeline.py` 已补结构化 `dictionary_info`，当前会显式记录请求来源、最终来源、版本状态、fallback 与 reason code，不再只剩 warning 文本。
   - 解析链路现在已能区分 `DICT_EXTERNAL_PATH_MISSING`、`DICT_EXTERNAL_JSON_INVALID`、`DICT_EXTERNAL_SCHEMA_INVALID`、`DICT_DEFAULT_ASSET_MISSING`、`DICT_DEFAULT_ASSET_INVALID`、`DICT_BUILTIN_FALLBACK` 与 `DICT_MISMATCH`。
   - `desktop/services.py` 导出包现会优先复制“解析时实际采用的字典”，并在 `meta.json.dictionary_status` 中固化来源、降级和版本状态；不再无差别复制仓库默认字典。
   - `tests/python/test_pipeline.py` 已补“外部字典对象输入 / 路径输入 / 版本不匹配 / 路径缺失 / JSON 非法 / schema 非法回退默认字典”回归，`tests/python/test_desktop.py` 已补“导出包伴随字典与实际采用字典一致”回归，当前兼容状态已整理到 `docs/dictionary_compatibility_matrix_20260314.md`。
   - 已新增重复解析稳定性测试，并把离线/在线一致性、复现一致性、对比口径一致性写入追踪矩阵。

8. 工程化与文档修正：
   - `pyproject.toml` 修复了 `readme` 路径和 `spec` 包数据路径错误。
   - `README.md` 已补充 `online-serial` CLI 用法。
   - `docs/traceability_matrix.json` 已扩展到当前 `28` 项需求映射 + `10` 项非功能映射。
   - `metric/service.py` 已从旧独立实现收敛为显式兼容层，当前统一转发到 `metric/core.py`。
   - `spec/models.py` 已显式标注为 legacy 兼容导出层，并补充 `canonical peer / legacy-only` 边界元数据；当前正式运行时模型入口已明确指向 `parser.models`，但 `Dataset / ExportJob / PackageResult / ReproSession` 等历史导出仍继续保留。

9. 非功能验收 smoke baseline 补强：
   - `desktop/services.py` 新增导出包规范化比较入口，可在去除 `export_time / snapshot_id / checksum` 等易变字段后比较导出内容一致性。
   - `tests/python/test_acceptance_baseline.py` 已补“重复导出一致性 / 对比口径一致性 / 复现重复一致性 / 短时 soak”四类非功能基线测试。
   - `tool/run_acceptance_baseline.py` 可离线生成 `docs/acceptance_baseline_20260314.json`，记录 parse / compare / export / repro 耗时、峰值内存与一致性结果。
   - 上述产物当前仅属于 smoke baseline，不代表已正式达到需求文档中的 `1GB / 24h / Windows-Linux` 验收门槛。

10. 科研指标与轻规则诊断补强：
   - `metric/core.py` 已新增 `response_jitter` 与 `irq_latency` 两类指标，补足“响应时间 / 抖动 / IRQ 延迟”这条主线中的关键缺口。
   - 已新增 `priority_inversion` 与 `irq_pressure` 两类轻规则告警及其诊断文本，能对潜在优先级反转窗口和异常 IRQ 挤压给出可举证结论。
   - `tests/python/test_pipeline.py` 已新增专项样例，验证新指标与新诊断能被稳定触发。
   - 本轮已新增 `deadline_miss` 指标、告警与诊断；当前仅在显式 `release_ts / deadline_ts / finish_ts` 语义存在时计算，并通过 `job_semantics / instance_semantics / deadline_semantics` 能力位约束精确结论范围。
   - 已新增“外部字典 + JSON payload”专项样例，验证实例语义、能力位与 `deadline_miss` 链路可被稳定触发。
   - `metric/core.py` 现已把 `deadline` 弱语义/不完整语义从 summary 计数推进到正式 `deadline_semantics_gap` 告警与诊断；当存在 incomplete/conflict 实例时，不再只埋在 `incomplete_count / conflict_count` 里，而会给出结构化降级证据。

11. 导出快照一致性证据补强：
   - `tests/python/test_desktop.py` 已新增“创建导出 job 后再修改界面上下文”的回归测试，验证最终导出包仍使用 job 创建时冻结的 `time_window / filter / selection / compare_scope`。
   - `desktop/services.py` 的 `BackgroundJobManager` 已补最小后台执行、状态查询与结果收口能力，`desktop/app/gui.py` 的导出已改为后台写包 + `QTimer` 轮询状态，不再在主线程直接执行 `export_WritePackage()`。
   - `tests/python/test_desktop.py` 与 `tests/python/test_desktop_runtime.py` 已新增异步导出回归，验证服务层状态流转与 Linux Qt offscreen GUI 导出链路可以收敛到完成。
   - 当前已证明“快照冻结 + 异步写包”最小闭环有效，但更细粒度进度、真正取消语义和更重负载下的交互体验仍未系统化验收。

12. 单数据集加载最小后台化：
   - `desktop/services.py` 已新增 `viz_LoadDatasetAsync()` 与 `viz_ResolveLoadDatasetJob()`，把“后台解析/主线程注册”拆成两段，避免后台线程直接写仓库状态。
   - `desktop/app/gui.py` 的单数据集加载已切到后台 load job + `QTimer` 轮询，不再在主线程直接同步调用 `viz_LoadDataset()`。
   - `tests/python/test_desktop.py` 与 `tests/python/test_desktop_runtime.py` 已新增专项回归，验证 load job 状态流转、重复 resolve 幂等，以及 Linux Qt offscreen 下单数据集加载后 UI 状态会自动收敛。
   - `parser/pipeline.py` 已新增 `prs_Prescan()`，可对 trace 文件或 trace 分片目录做全局头 + chunk header 预扫描，返回 `time_window / chunk_count / record_count / lod0_buckets` 级别的轻量摘要。
   - `desktop/services.py::viz_LoadDatasetAsync()` 现在会先返回 preview/readiness 合同，明确 `LOD0` 已具备首屏摘要预览，而 `LOD1/LOD2` 仍待完整解析完成。
   - `desktop/services.py` 现已对导出 package 输入补齐同口径 preview/readiness 合同；当前 package 路径也能在完整注册前返回 `time_window + lod0_buckets` 级别的预览，而不再是 staged-load skeleton 的空洞例外。
   - `parser/index.py` 与 `desktop/services.py::viz_QueryTimelineLOD()` 已把 `LOD0` 的默认主路径切到 `IndexBundle` 摘要聚合，并在复杂过滤场景下显式标记 `scan_fallback`，不再伪装成正式摘要主路径。
   - `desktop/services.py::BackgroundJobManager` 现已按 `input / parse_rebuild / query / export` 拆分执行 lane，不再是单一通用线程池；load job 的 `stage` 已从单一 `loading_full` 推进为 `preview_ready -> parse_rebuild -> query_ready`。
   - `parser/models.py` 已补 `LoadPreview / ReadinessState`，`prs_Prescan()`、package preview、load job、时间线/任务状态/事件表查询现在都能暴露统一 `stage / source / preview / lod_ready / view_ready / fallback_reason` 合同。
   - `parser/models.py`、`desktop/services.py` 与 `desktop/app/gui.py` 已把运行态 `TaskStateViewModel` 从旧 `segments / lane_map / evidence_refs` 过渡 DTO 收口为 `time_window / lane_order / rows / state_legend / summary / cursor_hint / trusted` 正式结构；当前 query-ready / materialized-bundle 路径下的任务状态表已消费行级 `rows`，不再直接依赖平铺 `TaskStateSeg` 列表。
   - `parser/models.py` 已新增正式 `TaskStatePreview` 并接入 `LoadPreview.task_state_preview`；`parser/pipeline.py::prs_Prescan()` 现在会在不构建完整 `RebuildBundle` 的前提下生成最小任务状态摘要，当前 trace 与 package preview 都能在 `preview_ready` 阶段返回 `LOD0 + TaskStatePreview`；对应 `GAP-02 / P0-02` 主目标已完成。
   - `desktop/app/gui.py` 的 footer 与任务状态面板已开始优先消费 `task_state_preview`，在完整 bundle 注册前即可显示任务数、lane 数与状态分布摘要；但这仍是 preview 摘要，不等价于正式 `viz_QueryTaskStates()` 查询。
   - `parser/models.py`、`spec/models.py` 与 `parser/index.py` 已把运行态 `EventPage` 从旧 `events / next_cursor / total` 收口为 `items / order_by / cursor_in / next_cursor / prev_cursor / has_more / total_hint / trusted` 正式结构；`desktop/services.py::viz_QueryEventTable()` 与 `desktop/app/gui.py` 现已统一消费 `items`，不再依赖旧 `events` 字段。
   - 事件表当前已具备正式 `NOT_READY` 与 source-backed 语义：当 `event_stream` 不可查询但 source 仍可回源时，`viz_QueryEventTable()` 与 `viz_QueryTimelineLOD(lod=2)` 会回退到 `trace_window_scan / package_index`；只有 bundle 与 source 都不可查询时才返回 `NOT_READY`。
   - 当前正常 bundle 路径也会显式标记 `fallback_reason=bundle_scan`，不再把“仍在完整 `event_stream` 上分页”的退化主路径伪装成正式大文件查询主路径；`event_table / timeline` 现已统一接入 `query_cache`，并具备 `cache_hit / cache_source / evicted` 观测字段；更高性能默认主路径优化与正式性能证据仍留给 `P0-07`。
   - `desktop/services.py` 已新增 `viz_QueryTimelineLODAsync()`、`viz_QueryTaskStatesAsync()` 与 `viz_QueryEventTableAsync()`，查询链路具备独立后台 job 能力；`GAP-03` 的 `TaskStateView / LOD2` 核心 staged 生命周期当前应视为已完成本轮收口，不再作为 `GAP-02` 的残余说明。

13. 外部验证执行路径固化：
   - 新增 `tool/compare_normalized_packages.py`，可对两份导出包做规范化比较，服务后续 Windows / Linux 一致性验收。
   - 新增 `docs/external_validation_checklist_20260314.md`，把跨平台、真实 GUI、性能与长稳的剩余外部验证工作收敛成明确步骤。
   - `tool/run_acceptance_baseline.py` 已补环境元数据与输入 `sha256` 指纹；`tool/compare_acceptance_baselines.py` 可对两份 baseline 报告做结构化比较。
   - 新增 `tool/prepare_external_validation_fixture.py`，可生成固定的 `baseline/candidate` 输入对和夹具 manifest，降低外部 Windows / Linux 验证的歧义。
   - 新增 `tool/desktop_validation_preflight.py` 与可落盘输出的 `tool/check_desktop_env.py --output`，外部验证人员可先判断“依赖未装”还是“运行时异常”，并保留 GUI 环境证据。
   - 新增 `tool/summarize_validation_status.py` 与 `docs/final_acceptance_readiness_20260314.md`，把当前内部验证与外部待执行项收口成单一交付摘要。
   - 本轮已在 Linux 本机补生成 `docs/acceptance_baseline_stage2_linux.json`、`docs/desktop_preflight_stage2_linux_venv.json` 与 `docs/desktop_env_stage2_linux.json`，进一步把“本机 smoke 证据”从口头描述转为可复核产物。
   - 当前最新本机证据已刷新到 `docs/acceptance_baseline_stage5_linux.json`、`docs/desktop_preflight_stage5_linux_venv.json` 与 `docs/desktop_env_stage5_linux.json`；但 `Windows/Linux` 一致性、正式性能门槛与长稳仍保持待外部执行状态。

14. `AnalysisContext` 临时态合同补强：
   - `desktop/services.py::BackgroundJobManager` 已新增最小 job-state callback，`WorkspaceController` 现会把 `queued/running` job id 同步写入 `AnalysisContext.pending_jobs`，并在任务完成后移除。
   - `ReplayService` 现会同步维护结构化 `playback_runtime`，覆盖 `init / play / pause / step / seek` 主链路。
   - `tests/python/test_desktop.py` 已新增后台 job 生命周期回归，并在 replay 主链路测试中补充 `playback_runtime` 断言。
   - `desktop/qt_compat.py` 现会在 Qt 环境下把 `ContextStore.changed` 绑定到 Qt signal，减少后台 job 状态回写引出的跨线程 GUI 更新问题。
   - `desktop/app/gui.py` 已补异步 load/export 状态文案优先级，避免 context refresh 把“摘要已就绪/导出完成”覆盖成低优先级摘要文本。
   - 当前 `pending_jobs / playback_runtime` 已从“字段存在”推进到“有真实维护链路”，但 `hover_target / transient_selection` 仍未形成正式运行态闭环。

15. package preview 元数据修正：
   - `desktop/services.py::_preview_package()` 现会优先对 package 内的 `event/events.trace` 执行 `prs_Prescan()`。
   - package preview 的 `chunk_count / record_count / core_ids / lod0_buckets / header` 现优先来自真实 packaged trace，而不是继续使用 manifest 的事件 count 冒充 chunk 数。
   - `tests/python/test_desktop.py` 已补直接对齐 packaged trace prescan 的回归。

16. CompareScope 维度合同补强：
   - `CompareScope.dimensions` 已开始真实驱动 compare 结果，而不再只是被 `cmp_SetScope()` 保存。
   - `DiffSummary` 现显式暴露 `dimensions / task_changes / core_changes / resource_changes / irq_changes`；
   - `DiffDetail` 现显式暴露 `dimension`；
   - `metric_Compare()` 已开始按 `metric / alert / hotspot / interval / task / core / resource / irq` 选择性生成正式 summary/detail 产物。

17. 时间线 `LOD1` 合同补齐：
   - `parser/models.py` 已新增 `SwitchPoint`；
   - `TimelinePayload` 已新增 `switch_points`；
   - `desktop/services.py::viz_QueryTimelineLOD()` 在 `lod=1` 时现会返回 `ExecSlice + SwitchPoint + IrqSpan`，并补 `summary.switch_count`。

18. 诊断 taxonomy 收口：
   - `Alert` 与 `Diagnosis` 已新增正式 `support_level`；
   - `metric/core.py` 已把 `exact / degraded / unsupported` 收口成统一推导逻辑；
   - `deadline_miss` 与 `deadline_semantics_gap` 现不再只靠文本表达语义支持等级。

19. 采集端正式实时路径收口：
   - `collector/core/trace_collector.cpp` 已改为预分配 per-core ring + `reserve/write/commit` 原地写入，record path 不再构造临时 `std::vector`。
   - auto flush 已切到后台 flush worker，record path 不再同步 drain + `channel.WriteChunk()`。
   - `ChannelMux` 已正式支持 `primary + fallback`、`retry_limit / retry_backoff_ms`、最终失败状态回传，以及 `TRACE_INTEGRITY_REASON_IO_BACKPRESSURE` 的统计与补写链路。
   - `tests/cpp/test_collector.cpp` 已补 `fallback / retry / io_backpressure` 专项回归，并复跑确认 `fallback async` 路径在当前基线下可稳定通过。
   - `trace_GetStats()` 已改为聚合原子快照；`trace_Destroy()` / `trace_Disable(keep_buffer=0)` 已补 producer quiesce。
   - `tests/cpp/test_collector.cpp` 已新增 `no_heap_alloc / async_flush / concurrent_stats / same_core_seq` 专项回归。
   - 当前 `FR-COL-02` 的实现性缺口已收口，剩余正式吞吐/长稳证据转入外部非功能验收。

20. segment chain 元信息消费链路收口：
   - `parser/pipeline.py` 现在会把 `segment_metas` 正式挂入 `RebuildBundle`，不再停留在 parser 临时结果。
   - `parser/codec.py` 已把 `SEGMENT_CHAIN_BREAK` 与 `SEGMENT_DICT_CONFLICT` 收口成正式 `UntrustedWindow`。
   - `desktop/services.py` 导出包现会把 `segment_chain` 写入 `meta.json / manifest.json`，并在 `repro_OpenPackage()` 前做一致性校验。
   - `tests/python/test_pipeline.py` 与 `tests/python/test_desktop.py` 已补 segment chain/export/repro 专项回归并通过。

21. collector perf / soak 基线补强：
   - 已新增 `tool/collector_bench_runner.cpp`、`tool/run_collector_perf_baseline.py` 与 `tool/run_collector_soak.py`。
   - 当前 Linux 本机已落盘 `docs/collector_perf_baseline_20260316_linux.json` 与 `docs/collector_soak_20260316_linux.json`。
   - `docs/final_acceptance_readiness_20260314.md` 与 `docs/external_validation_checklist_20260314.md` 已同步 collector 专项工具与报告路径。
   - collector 侧剩余非功能阻断项已收敛为 Windows 外部执行与更高门槛的正式验收，而不再是缺工具或缺报告格式。

22. desktop staged-load 性能证据补强：
   - `tool/run_acceptance_baseline.py` 已补 `--mode/--profile`，并在报告中新增 `desktop_perf` 节点，当前会记录 `load_preview_seconds`、`task_state_preview_seconds`、`event_table_first_page_seconds`、`lod2_first_window_seconds`、`peak_memory_mb` 与 query cache 预算/淘汰观测。
   - `tests/python/test_acceptance_baseline.py` 已补 `test_acceptance_baseline_captures_async_load_preview_metrics` 与 `test_acceptance_baseline_captures_peak_memory_and_cache_metrics`。
   - `tool/summarize_validation_status.py` 已支持聚合 `--desktop-perf` 报告，`tests/python/test_desktop_tooling.py` 已补 summary 聚合回归。
   - 当前 Linux 本机已落盘 `docs/acceptance_baseline_stage5_linux.json` 与 `docs/desktop_perf_baseline_20260316_linux.json`，并同步刷新 `docs/final_validation_status_20260314.json`。
   - `1GB` 首屏 `< 10s`、峰值内存 `< 4GB`、Windows/Linux 双平台和长稳 soak 仍明确保留为外部待执行项。

23. desktop runtime 首屏合同与文档口径收口：
   - `desktop/app/gui.py` 已修复 `preview_ready` 阶段 `task_state_table` 被 context refresh 清空的问题；当前 `TaskStatePreview` 会在 unresolved dataset 窗口里稳定保留，直到正式 `TaskStateView` 接管。
   - `tests/python/test_desktop.py` 已补 `test_context_change_does_not_clear_task_state_preview_during_active_load` 与 `test_task_state_preview_is_replaced_by_formal_rows_after_dataset_resolve`。
   - `tests/python/test_desktop_runtime.py` 已补 `test_offscreen_context_change_does_not_clear_task_state_preview_during_active_load`，并强化 `test_offscreen_async_load_updates_runtime_state`；后续 `WP-03-01` 又新增 source-backed `LOD2 / EventTable` runtime 专项回归，当前 `~/rttrace-desktop-venv/bin/python -m unittest tests.python.test_desktop_runtime` 共 `11` 个用例通过。
   - 已新增 `tool/run_desktop_runtime_report.py`，当前 Linux 本机已落盘 `docs/desktop_runtime_stage5_linux_venv.json`。
   - `tool/summarize_validation_status.py` 现已直接消费 `desktop_runtime` artifact，`docs/final_validation_status_20260314.json` 不再仅根据 preflight/env smoke 推断 runtime 状态。

24. Linux desktop perf formal report 收口：
   - `tool/run_acceptance_baseline.py` 现支持通用 `desktop_perf_acceptance` mode，并保留 `desktop_perf_acceptance_linux` 兼容入口；当前会在 `desktop_perf` 观测值之外，额外输出 `desktop_perf_acceptance` 阈值判定、输入范围与限制项。
   - `tests/python/test_acceptance_baseline.py` 已补通用 perf acceptance 回归，并覆盖 Windows 平台标签输出。
   - `tests/python/test_desktop_tooling.py` 已补 `test_linux_perf_acceptance_is_reflected_in_validation_summary`。
   - 当前 Linux 本机已落盘 `docs/desktop_perf_acceptance_20260316_linux.json`，`docs/final_validation_status_20260314.json` 已新增 `desktop_perf_linux_formal_ready / desktop_perf_linux_first_screen_pass / desktop_perf_linux_peak_memory_pass`。
   - 该 Linux formal report 已明确标注 `input_lt_1gb`、Windows 未覆盖和长稳未覆盖；外部待执行项现更清晰地收敛为 `1GB` 正式大输入、双平台一致性与长稳验证。

25. external status 结构化收口：
   - `tool/summarize_validation_status.py` 已支持导入 `--collector-soak`、Windows 侧 acceptance/runtime/perf/collector 产物，以及 `acceptance_compare / package_compare` 结果，并在 summary 中生成 `external_status`，不再只保留 `external_pending` 字符串列表。
   - `tests/python/test_desktop_tooling.py` 已补 Linux-only 汇总与 Linux+Windows 汇总回归。
   - 在 `2026-03-23` 该轮时点，`docs/final_validation_status_20260314.json` 曾明确写出：
      - `desktop_1gb_first_screen_lt_10s = pending_external`
      - `desktop_peak_memory_lt_4gb = pending_external`
      - `windows_linux_consistency = linux_evidence_only`
      - `long_duration_stability = linux_long_soak_evidence_ready_pending_windows`
   - 这使剩余外部执行项具备了“Linux 已有 evidence / 当前 scope / 剩余产物”三层结构，而不再只是挂账名词。

26. `WP-03-03` 默认主路径策略文档化收口：
   - `document/详细设计说明书_MVP_v1.0.md` 已明确 `LOD2 / EventTable` 默认主路径与切换条件：`bundle.event_stream` 可用时优先 `materialized_bundle`，不可用但 source 可回源时必须切 `trace_window_scan / package_index`，两者都不可用才允许 `NOT_READY`。
   - 同步固化 `fallback_reason` 边界：`materialized_bundle` 路径写 `bundle_scan`（必要时细化 `filter_requires_bundle_scan`），source-backed 路径保持 `None`。
   - `tmp/GAP-03` 已同步记录过 runtime artifact freshness 漂移；当前已重刷 `docs/desktop_runtime_stage5_linux_venv.json` -> `docs/final_validation_status_20260314.json` -> `docs/final_acceptance_readiness_20260314.md` 链路，runtime `11` 用例口径已对齐。

27. `1GB` formal perf 控制面硬化（第1阶段仓库内可修补项）：
   - `tool/run_acceptance_baseline.py` 已为 `desktop_perf_acceptance` 增加 `formal_input` 清单、`formal_input_verified`、`acceptance_scope=full|perf_only`，并把峰值内存从单一 `tracemalloc` 扩展为 `python_peak_alloc + os_peak` 双口径。
   - `tool/desktop_validation_preflight.py` 已扩展为可输出 `large_input_preflight`，支持对 baseline/candidate 输入、scratch 目录、可用磁盘空间、timeout 和本地磁盘提示做 fail-fast。
   - `tool/summarize_validation_status.py` 已收紧 `NFR-PERF-03/04/06` 的 formal 判定：仅文件大小不再足够，必须存在完整 `formal_input` manifest 且 `formal_input_verified=true` 才能进入正式闭环。
   - `tests/python/test_acceptance_baseline.py` 与 `tests/python/test_desktop_tooling.py` 已补 formal manifest、`perf_only` 跳过行为和 large-input preflight 相关回归。
   - 当前仓库已具备“拿到正式 `1GB` 输入即可产出可信 artifact”的工具前提；真正的 Linux `1GB` formal report 回填仍属于下一阶段。
28. public-RTOS `1GB` 预验证样本落库（第2阶段的前置）：
   - 已新增 `tool/build_public_rtos_large_input.py`，可把公开 Apache NuttX task trace 语义映射到当前正式 RTOS 事件模型，并构造 `>= 1GB` 预验证输入。
   - 仓库内现已落地：
     - `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_baseline.trace`
     - `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_candidate.trace`
     - `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_manifest.json`
     - `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_preflight.json`
   - 该样本当前只用于预验证与缺陷收口，不替代最终真实 external `>= 1GB` 输入。
29. `1GB` public 预验证当前观察（第2阶段仓库内收口已完成）：
   - checked-in 的 `example/public-rtos-1gb-prevalidation/public_rtos_1gb_fast_preflight.json` 已按当前代码口径重刷；当以 repo 共享目录作为 scratch 时，会明确返回 `scratch_dir_not_local_disk`，不再把共享目录误判为本地 SSD。
   - 本轮已使用 `docs/public_rtos_1gb_fast_desktop_perf_20260322.json` 刷新 `docs/final_validation_status_20260314.json`，在该时点真实口径为：
     - `desktop_1gb_first_screen_lt_10s = pending_external`
     - `desktop_peak_memory_lt_4gb = pending_external`
     - `NFR-PERF-06 = pending_external`
   - 当前还存在 `docs/public_rtos_1gb_dense_desktop_perf_blocker_20260322_phaseD.json`；dense 已从“外部终止且无产物”推进为“脚本内可定位的 `MemoryError` blocker”。
   - 这说明当前正式问题已经不再是“仓库里没有 `1GB` 样本或执行链路”，而是“是否还需要继续为 dense public 路径做进一步降峰值优化，以及何时切到真实 external formal close”。
30. 下一阶段边界（第3阶段 / formal close）：
   - `docs/1gb_input_gap_execution_plan_20260321.md` 已明确后续顺序：
     - 当前仓库内 `1GB` 缺口已按 `7.1` 判据修补到位
     - 若仍继续走 public 路径，则根据 dense blocker 决定是否推进 `hybrid load / metric window index / export 流式化`
     - 最后再替换为真实 external 输入执行 formal close
   - 因此在该时点不能把 `desktop_1gb_first_screen_lt_10s`、`desktop_peak_memory_lt_4gb`、`NFR-PERF-06` 写成已关闭。

## 当前判断

### 已基本具备闭环

1. 采集（文件 + hook 通道）-> 解析 -> 重建 -> 指标 -> 告警 -> 工作台 -> 对比 -> 导出 -> 复现。
2. 离线解析与在线增量解析（`file / socket / serial`）的一致性。
3. 离线单文件与目录分片日志的统一解析基线。
4. 基于统一事件流的回放、差异对比、导出包校验与上下文恢复。

### 部分满足，仍需继续迭代

1. 格式与契约覆盖：
   - 统一记录模型、未知事件兼容、`DICT_MISMATCH`、外部伴随字典显式装载入口、更细粒度的失效 reason code，以及导出包伴随字典对齐已经具备实现与专项验证；
   - 但真实多版本字典资产演进、跨工具交换包与更大范围的兼容矩阵仍未系统化覆盖。

2. 指标与诊断深度：
   - 已有 `cpu_utilization / blocked_time / ready_wait_time / response_time / response_jitter / context_switch_count / irq_busy_time / irq_latency`；
   - 轻规则诊断已覆盖 `long_block / long_irq / priority_inversion / irq_pressure / deadline_miss / untrusted_window`；
   - `deadline` 链路已经从“只有命中 miss 才发声”推进到“弱语义/不完整语义也会显式给出 `deadline_semantics_gap` 降级诊断”；
   - `Alert / Diagnosis` 现已具备正式 `support_level`，能区分 `exact / degraded / unsupported` taxonomy；
   - 但更完整的实例级任务语义重建与更广范围的规则覆盖仍未完成。

3. UI 专业性：
   - 当前工作台已具备可用的分析、对比、导出/复现、书签与回放链路；
   - 已补最小后台异步导出与状态反馈，并把资源等待链下钻、对比明细从原始 dump 推进到更结构化的证据摘要；
   - 但真正的工程级专业 UI 仍需要更强的多视图联动、更多图表表达以及更细粒度的任务进度/取消能力。

4. 验收证据：
   - 目前测试主要覆盖 Python 单元/集成样测与 C++ 采集器样测；
   - 现已补“导出一致性 / 复现一致性 / 对比口径一致性 / 短时 soak / 本机性能 smoke / desktop staged-load”基线；
   - 外部验证准备度已进一步补到“统一输入夹具 + baseline 报告元数据 + desktop perf 报告 + 报告比较脚本 + checklist”；
   - 但 `1GB` 首屏 `< 10s`、峰值内存 `< 4GB`、Windows/Linux 一致性、长稳与 GUI 真实运行环境验证仍不足。

5. 大文件加载架构：
   - 离线文件主路径已经改成顺序流式读取，不再先整文件驻留再二次切片；
   - GUI 单数据集路径已经补到“trace/package preview + 后台完整解析 + 主线程注册”的 staged skeleton，不再只有最小后台 load job；
   - `LOD0` 已开始真实依赖 prescan/index 摘要，而不是桶内重复扫描全量事件流；
   - `LOD1` 已补齐 `SwitchPoint` 正式载荷；
   - `TaskStateView` 的 query-ready 正式对象合同已完成：`rows[].segments / state_counts / lane_order / cursor_hint / trusted` 已落到运行态与兼容层模型，并由桌面查询与表格消费共同验证；
   - `desktop/services.py::viz_QueryTaskStates()` 现已切到独立 `task_state_window_index` 查询路径，不再直接对完整 `bundle.task_states` 做全量过滤；任务状态查询已具备独立缓存键：`time_window + lane_group + state_mask + filter`；`P0-05` 约束的是缓存键与命中语义，不再绑定 `DatasetRecord.task_state_query_cache` 作为唯一缓存承载，相关 desktop 定向回归也已同步按统一 `query_cache` 的 `task_states` namespace 校验；
   - 任务状态 formal query 当前已具备最小独立 readiness 语义：当 `bundle.task_states` 与窗口索引都不可用时，`viz_QueryTaskStates()` 会显式返回 `NOT_READY`；统一 `query_cache` 现已正式接管 `task_states / event_table / timeline` 三类查询，`cache_budget_mb`、LRU 回收、namespace stats 与 `cache_hit / cache_source / evicted` 观测面已落地，并已由 desktop/acceptance baseline 自动化回归锁定；更高层性能控制与正式门槛证据仍留给 `P0-07`；
   - 首屏 preview 现已从 `LOD0 only` 推进到 `LOD0 + TaskStatePreview`，GUI footer 与任务状态面板都能在 `preview_ready` 阶段消费这份任务状态摘要，而不必等待完整 bundle 注册；但这仍是 preview 合同，不等价于正式 `TaskStateView` 查询提前 ready。
   - 事件表的 query-ready 正式页对象合同已完成：`EventPage.items / order_by / cursor_in / next_cursor / prev_cursor / has_more / total_hint / trusted` 已在运行态、兼容层、索引层和 GUI 消费链路统一；
   - package preview 的 `chunk_count` 语义已修正，不再把事件数冒充 chunk 数；
   - `Input Worker / Parse-Rebuild Worker Pool / Query Worker Pool / Export Worker` 的执行分层与正式 readiness 合同现已落地；`WP-03-01/02/03` 后续收口点已转为 formal evidence、async query readiness 直接回归与 runtime artifact freshness，不再继续按 `TaskStateView / LOD2` staged 生命周期缺口归档。

6. `AnalysisContext` 临时态：
   - `pending_jobs` 与 `playback_runtime` 已开始由系统维护，不再只是模型占位；
   - 但 `hover_target / transient_selection` 仍缺少正式服务层或 GUI 链路，因此还不能宣称整套临时态合同已完成。

7. Compare 合同：
   - `dimensions` 已开始真实影响 compare 输出，不再只是弱字段；
   - 但更深层的热点/告警/关键区间下钻和更复杂的 compare UI 交互仍未达到设计上限。

## 仍未覆盖的重点需求类别

1. `FR-COL-02`：实现层已补齐预分配写路径、后台 flush worker 与原子 stats snapshot；当前剩余问题不再是 collector 架构缺失，而是正式吞吐、抖动与长稳证据仍未完成。
2. `FR-FMT-02`：已覆盖 `DICT_MISMATCH`、外部伴随字典装载、细粒度失效 reason code、结构化 `dictionary_info` 与导出包伴随字典对齐；但真实多版本演进矩阵与更大互操作验收仍未系统化完成。
3. `FR-MET-03`：轻规则诊断已补到优先级反转窗口、IRQ 挤压、`deadline_miss` 与 `deadline_semantics_gap`；但截止期分析仍依赖显式或弱语义输入，更完整的实例级规则覆盖仍未系统化验收。
4. `FR-CMP-*/OBJ-CompareScope`：
   - 当前 `dimensions` 已能驱动 `metric / alert / hotspot / interval / task / core / resource / irq` 的最小正式差异产物；
   - 但 compare 的深层 drilldown 能力和更强表达仍待继续增强。
5. `NFR-CONS-01 / NFR-PERF-* / NFR-STAB-01`：
   - 已有本机 smoke baseline，可记录 parse / export / repro 耗时、峰值内存和短时 soak 结果；
   - 但跨平台一致性、正式性能门槛与长稳证据仍未完成。

## 下一轮建议优先级

### P0

1. 把本机 smoke baseline 推进为更接近正式验收的证据，优先补 Windows/Linux 一致性样测与更大规模日志基线。
2. 在已补结构化字典状态、细粒度失效 reason code 和导出包伴随字典对齐的基础上，继续扩展真实多版本字典资产演进与更强兼容矩阵验收，继续夯实 `FR-FMT-02 / NFR-COMPAT-01`。
3. 继续把 GUI 运行时测试扩展到 Windows 真实 `PySide6 + pyqtgraph` 环境，并补双平台结果留档与更重负载下的真实桌面链路验证。

### P1

1. 在已有最小 `deadline_miss` 闭环的基础上，继续补更完整的实例级任务语义、弱语义降级样本和更广范围的规则覆盖。
2. 在已有资源等待链下钻和更清晰对比明细的基础上，继续增强更深层的证据联动、视图同步和专业表达。
3. 将 `NFR-PERF-01/02/05` 与更高强度稳定性测试继续纳入追踪矩阵，减少验收盲区。

### P2

1. 继续评估 `spec/models.py` 中 legacy-only exports 的真实外部使用需求，视兼容成本决定后续是否继续拆分、迁移或淘汰。
2. 继续提升 UI 表达与异步任务体验，使工作台更接近工程级分析工具。
