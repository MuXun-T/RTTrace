# Runtime Optimization Agent Execution Plan 2026-06-25

## 1. 目标与边界

本文档整理运行时优化 agent、冷启动优化、核心 rebuild/index/sidecar 优化、LLM 接入的工程实施计划。

核心目标：

- 降低 1GB 及更大 trace 的正式产品路径等待时间。
- 明确区分 formal 验收成本和真实产品运行成本。
- 让 agent/advisor 成为策略控制器，而不是把 LLM 放进解析、rebuild、sidecar/index 真值生成路径。
- 优先做低臃肿、高确定性的优化，再推进分段索引、后台预热和并行 rebuild。

约束：

- 不让 LLM 参与 trace decode、align、rebuild、sidecar edge、index 真值生成。
- 所有缓存和 fast path 必须通过 checksum、dictionary、schema、parser version、ticket/gate 校验。
- 所有性能收益必须用 product runtime benchmark 证明，不能用 formal A/B/C 总耗时代替。
- 每个阶段必须有 focused tests；formal A/B/C 只作为最终验收，不作为日常迭代手段。

## 2. 优先级总览

| 优先级 | 项目 | 预期收益 | 可行性 | 臃肿风险 | 建议结论 |
|---|---:|---:|---:|---:|---|
| P0 | 真实性能度量补齐 | 高 | 高 | 低 | 先做，否则无法可靠判断 speedup |
| P1 | rebuild/align/sidecar/index 低风险热点优化 | 中到高 | 高 | 低 | 第一批落地 |
| P2 | RuntimeLoadPlan 接入正式加载路径 | 高 | 中高 | 中 | agent 真正进入产品路径 |
| P3 | parser artifact 与 sidecar index 缓存复用 | 很高 | 中 | 中 | hot/warm start 最大收益 |
| P4 | 后台 sidecar/index 预热 | 高 | 中 | 中 | 降低用户等待 |
| P5 | 分段 sidecar/index 与 adaptive index | 很高 | 中低 | 中高 | 大日志长期方案 |
| P6 | LLM 接入 advisor/解释/离线优化 | 中 | 中 | 中 | 非真值路径可做 |
| P7 | 并行 rebuild / morsel rebuild | 高 | 低到中 | 高 | 最后做，先保证 parity |

## 3. P0 真实性能度量补齐

### 涉及文件

- `parser/pipeline.py`
- `parser/parser_process_agent.py`
- `tool/runtime_benchmark_runner.py`
- `tool/run_runtime_optimization_formal_matrix.py`
- `tests/python/test_pipeline.py`
- `tests/python/test_runtime_optimization_agents.py`

### 修改方式

- 在 `load_dataset_with_timings` 的输出中稳定暴露以下字段：
  - `parse_seconds`
  - `align_events_seconds`
  - `rebuild_seconds`
  - `idx_build_seconds`
  - `load_seconds`
  - `index_build_mode`
  - `materialize_event_stream`
  - `peak_rss_mb`，如果当前平台能采集
- 在 `ParserProcessAgent` 的 `parse_rebuild_result.json` 中同步写入上述字段。
- 在 `runtime_benchmark_runner.py` 中区分：
  - product runtime path
  - formal wall time
  - sidecar build time
  - sidecar index build/open time
  - ticket validate time
  - advisor overhead
- formal matrix 中已有的合成字段必须标记为 synthetic 或只用于 formal 证明，不作为 product speedup 依据。

### 预期效果

- 不直接提速。
- 后续每个优化项能算出真实 speedup、RSS 变化和 stage 占比。
- 避免继续出现“agent 是否提速证据不足”的问题。

### 验收标准

- `tests/python/test_pipeline.py` 覆盖 stage timing 字段。
- `tests/python/test_runtime_optimization_agents.py` 覆盖 `ParserProcessAgent` 输出字段。
- product benchmark report 中能直接看到 parse、align、rebuild、index、sidecar、advisor 分项。

## 4. P1 低风险核心热点优化

### 涉及文件

- `parser/rebuild.py`
- `parser/align.py`
- `parser/evidence_sidecar.py`
- `parser/index.py`
- `tests/python/test_rebuild.py`
- `tests/python/test_pipeline.py`
- `tests/python/test_evidence_sidecar.py`

### 修改方式

#### 4.1 `rebuild.py` open relation 索引优化

当前 `close_waits` 和 `close_holds` 会扫描 open relation 字典：

- task + obj 精确关闭时，应直接按 `(task_id, obj_id)` 查找。
- 仅按 task 关闭时，使用 `waits_by_task` / `holds_by_task`。
- 仅按 obj 关闭时，使用 `waits_by_obj` / `holds_by_obj`。
- 每次新增、关闭 open relation 时同步维护这些二级索引。

预期效果：

- 普通 trace 收益有限。
- 大量锁等待、资源等待场景下，避免从近似 `O(N * open_relations)` 退化。
- 不改变 rebuild 语义。

#### 4.2 `align.py` 跳过不必要排序

当前 `align_events` 至少会按 raw timestamp 排序；如果 offset 后顺序变化，还会再按 aligned sort key 排序。

建议：

- 增加 `_is_sorted_by_raw_key(events)`。
- 输入已排序时跳过第一次 sort。
- offset 后增加 `_is_sorted_by_event_sort_key(events)`，只有顺序变化时才做第二次 sort。

预期效果：

- 对已按采集顺序输出的 trace，减少一次 `O(N log N)`。
- 对多核校准且 offset 会改变全局顺序的情况仍保持正确排序。

#### 4.3 `evidence_sidecar.py` 避免重复全量排序

当前 `build_dependency_sidecar` 会 `sorted(list(bundle.event_stream), key=evd_StableEventSortKey)`。

建议：

- 如果 `bundle.event_stream` 已经稳定有序，直接复用。
- 如果调用方提供 `ref_index_rows`，优先使用它生成 ref 序列，减少对全量 `event_by_ref` 的依赖。
- 保持输出边排序和 edge hash 不变。

预期效果：

- 对大 trace 的 sidecar 构建减少一次全量排序和部分内存复制。
- 不改变 sidecar contract。

#### 4.4 `index.py` 保持 minimal/deferred 作为冷启动默认候选

当前已有 `full`、`minimal`、`deferred`。

建议：

- 不改变现有模式语义。
- 配合 P2 的 `RuntimeLoadPlan`，大 trace 冷启动默认不走 `full` UID index。
- full index 只在用户需要精确 UID 查询、导出 package 或后台预热时触发。

### 验收标准

- rebuild 输出与原实现一致。
- sidecar rows 集合与排序稳定。
- deferred/minimal index 既有测试继续通过。
- 新增大量 open wait/hold 的单测，验证关闭关系不丢失。

## 5. P2 RuntimeLoadPlan 接入正式加载路径

### 涉及文件

- `parser/runtime_advisor.py`
- `parser/runtime_optimization_gate.py`
- `parser/parser_process_agent.py`
- `desktop/services.py`
- `tests/python/test_runtime_optimization_advisor.py`
- `tests/python/test_desktop.py`
- `tests/python/test_runtime_optimization_agents.py`

### 修改方式

新增轻量 plan，不建议第一版引入复杂 agent 类。可以先用 dataclass 或严格 dict：

```text
RuntimeLoadPlan
- plan_version
- load_mode: full | cold_preview | warm_reuse | hot_reuse
- index_build_mode: full | minimal | deferred
- materialize_event_stream: bool
- try_parser_artifact_reuse: bool
- try_sidecar_index_reuse: bool
- background_sidecar_prebuild: bool
- reasons: list[str]
```

advisor 输入：

- `input_bytes`
- trace checksum
- dictionary checksum
- parser artifact 是否存在
- sidecar index ticket 是否存在
- 历史 runtime/RSS
- 当前调用场景，加载、查询、导出、formal

执行逻辑：

- `RuntimeOptimizationAdvisor` 生成建议。
- `runtime_optimization_gate` 校验建议是否合法。
- `desktop/services.py` 的 `_load_artifact_from_source` 在调用 `ParserProcessAgent` 前应用 plan。
- `ParserProcessAgent` 继续只负责执行，不负责自己做策略判断。

### 预期效果

- cold start：大 trace 可从 full index/full materialize 改为 minimal/deferred 首开。
- warm/hot start：为 P3 缓存复用铺路。
- agent/advisor 开始真正影响正式产品路径，而不是只出报告。

### 臃肿控制

- 不新增常驻 agent。
- 不引入在线 LLM。
- 不改变 GUI 主流程。
- 第一版只支持加载路径，不扩散到所有 export/query。

### 验收标准

- 大输入特征时 plan 选择 `minimal` 或 `deferred`。
- 小输入仍保持 `full` 或既有默认行为。
- 无 ticket 时不允许走 reuse。
- gate 失败自动回退 full safe path。

## 6. P3 parser artifact 与 sidecar index 缓存复用

### 涉及文件

- `parser/parser_process_agent.py`
- `parser/evidence_sidecar_index.py`
- `parser/sidecar_index_agent.py`
- `desktop/services.py`
- `tests/python/test_runtime_optimization_agents.py`
- `tests/python/test_desktop.py`

### 修改方式

parser artifact cache key：

```text
trace_checksum
dictionary_checksum
parser_version
index_build_mode
materialize_event_stream
schema_version
```

sidecar index cache：

- 复用现有 sidecar index ticket。
- 校验 sidecar path、checksum、file fingerprint、trace checksum、dictionary checksum、schema version。
- 校验失败时自动 rebuild，不允许半信任复用。

cache 目录：

- 使用 temp/cache 目录，例如系统 temp 下的 `rttrace-parser-agent/`。
- 不写入源码目录。
- 第一版不做复杂 LRU，只做 key 命中和显式失效。

### 预期效果

- 重复打开同一 1GB trace 时收益最大。
- hot start 可从重新 parse/rebuild/index 降到校验 ticket + 打开 artifact/index。

### 臃肿风险

- cache 生命周期和失效策略会增加复杂度。
- 需要严格测试 checksum mismatch、dictionary mismatch、parser version mismatch。

### 验收标准

- 第一次打开生成 cache/ticket。
- 第二次打开命中 cache。
- 修改 trace 后 cache 失效。
- 修改 dictionary 后 cache 失效。
- 旧 schema ticket 不可复用。

## 7. P4 后台 sidecar/index 预热

### 涉及文件

- `parser/sidecar_index_agent.py`
- `desktop/services.py`
- `desktop/evidence_export.py`
- `desktop/repository.py`
- `tests/python/test_desktop.py`
- `tests/python/test_runtime_optimization_agents.py`

### 修改方式

- dataset 加载成功后，如果 plan 指示 `background_sidecar_prebuild=True`，提交后台 job。
- 后台 job 只构建 sidecar/index，不阻塞当前加载和查询。
- job 状态写入 repository 或已有 job contract：
  - queued
  - running
  - completed
  - failed
  - cancelled
- evidence export 前优先检查后台产物是否完成，完成则复用，未完成则走现有路径。

### 预期效果

- 不减少总工作量。
- 降低用户等待，尤其是“打开 trace 后稍后导出 evidence package”的路径。
- 减少正式使用时把 sidecar/index 构建成本全部压到用户点击导出那一刻。

### 臃肿控制

- 第一版只允许单 dataset 一个后台 index job。
- 不做复杂调度队列。
- 失败不影响已加载 dataset。

### 验收标准

- 后台 job 失败时前台功能不失败。
- 后台完成后 export 能复用 index。
- 取消加载或关闭 dataset 后后台 job 可取消或不再写回 active record。

## 8. P5 分段 sidecar/index 与 adaptive index

### 涉及文件

- `parser/evidence_sidecar.py`
- `parser/evidence_sidecar_index.py`
- `parser/index.py`
- `parser/models.py`
- `desktop/package_writer.py`
- `desktop/evidence_export.py`
- `desktop/repro_evidence.py`
- `spec/schema/*.json`
- `tests/python/test_evidence_sidecar.py`
- `tests/python/test_desktop.py`

### 修改方式

新增 segment manifest：

```text
segment_id
time_begin
time_end
core_ids
event_count
sidecar_path
index_path
checksum
schema_version
```

sidecar/index 构建：

- 按 time/core/chunk 切分 sidecar。
- 每段生成独立 sidecar jsonl。
- 每段生成独立 sqlite index。
- 总 manifest 记录所有 segment。

查询：

- 先用 manifest 过滤 time/core。
- 只打开相关 segment index。
- 首次查询某范围时补建该范围索引，形成 adaptive index。

### 预期效果

- 大 trace 不再每次全量扫描/全量写 sidecar。
- 局部查询、局部导出、失败续跑收益明显。
- 对 1GB+ trace 是长期最有效方案之一。

### 臃肿风险

- 会影响 schema、package、repro、formal parity。
- 必须单独 PR，不应与 P1/P2 混做。

### 论文依据

- NoDB / SIGMOD 2012：按需访问 raw data，避免 upfront full load。
- Database Cracking / CIDR 2007：查询驱动逐步索引。
- Druid / SIGMOD 2014：segment + ingestion/query 分离。

## 9. P6 LLM 接入

### 涉及文件

- `parser/runtime_advisor.py`
- `parser/openai_advisor_client.py`
- `desktop/evidence_export.py`
- `tool/run_deepseek_advisor_smoke.py`
- `tests/python/test_runtime_optimization_advisor.py`

### 修改方式

LLM 只允许处理结构化摘要：

- stage timings
- RSS
- sidecar/index size
- ticket 状态
- failure reason
- benchmark delta

LLM 输出只允许是 advisory：

- 推荐 `minimal/deferred/full`
- 推荐是否后台预热
- 推荐优先构建哪个 time/core/task 范围
- 解释当前瓶颈

禁止：

- 生成 event。
- 生成 task state。
- 生成 sidecar edge。
- 生成 index truth。
- 在冷启动同步路径中等待远程 LLM。

### 预期效果

- 改善可解释性和研发定位效率。
- 对 runtime 直接提速有限。
- query intent 转索引优先级可能改善交互体验。

### 臃肿控制

- 默认关闭。
- 无 key、超时、返回非法结构时回退 heuristic。
- LLM 结果必须经过 schema validation。

### 验收标准

- 无 LLM key 时所有产品功能正常。
- LLM 超时时不影响加载、查询、导出。
- LLM 返回非法 JSON 时 fail closed。

## 10. P7 并行 rebuild / morsel rebuild

### 涉及文件

- `parser/rebuild.py`
- `parser/pipeline.py`
- `parser/models.py`
- `tests/python/test_rebuild.py`
- `tests/python/test_pipeline.py`

### 修改方式

先定义 shard boundary state：

- current task state
- running_by_core
- irq_stack
- open waits
- open holds
- resource graph partial edges

执行方式：

- 按 core/time/chunk 切 morsel。
- 每个 morsel 独立 rebuild 局部结果。
- merge 阶段合并边界状态。
- serial rebuild 作为 oracle，做 parity test。

### 预期效果

- 多核机器上理论收益高。
- 但准确性风险最高，尤其是跨 core、跨 chunk 的 task/resource/IRQ 关系。

### 实施建议

- P0-P5 稳定后再启动。
- 第一版只做实验分支。
- 不进入默认产品路径，直到 parity 覆盖足够。

## 11. 推荐 PR 切分

| PR | 内容 | 验证重点 |
|---|---|---|
| PR1 | P0 + P1 | stage telemetry、rebuild parity、sidecar parity |
| PR2 | P2 | RuntimeLoadPlan、desktop load path、gate fallback |
| PR3 | P3 | parser artifact cache、sidecar index ticket reuse |
| PR4 | P4 | 后台 prewarm、失败不影响前台 |
| PR5 | P5 | segment manifest、package/repro/schema/formal parity |
| PR6 | P6 | LLM advisory、schema validation、fallback |
| PR7 | P7 | 并行 rebuild 实验、serial parity |

## 12. 工程规范

- 每个 PR 必须有 focused tests。
- 不手改 `tmp/`、formal 历史产物、benchmark 历史产物。
- 不把 formal A/B/C 当日常性能测试。
- 新增 fast path 必须 fail closed。
- 新增 cache 必须有明确 invalidation。
- 新增 agent/advisor 输出必须可序列化、可 schema 校验、可审计。
- 所有 product runtime speedup 必须有 benchmark report 证明。
- 任何 LLM 结果都不能成为 trace 事实来源。

