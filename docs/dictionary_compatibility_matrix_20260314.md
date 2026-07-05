# 字典兼容矩阵（2026-03-14）

## 范围

本矩阵覆盖当前代码库中“事件字典装载 / `dict_ver` 校验 / fallback catalog 降级 / 导出包伴随字典”这条主链路的最新状态，用于说明：

1. 解析入口支持哪些字典来源。
2. 不同失败场景会落到哪些明确的 `reason_code`。
3. 解析阶段的字典状态如何传递到导出包 `meta.json` 与 `reference/dictionary.json`。
4. 哪些兼容情形已有回归，哪些仍未系统化覆盖。

## 当前实现边界

- 入口模块：
  - `spec/schema_loader.py`
  - `spec/events.py`
  - `parser/codec.py`
  - `parser/pipeline.py`
  - `desktop/services.py`
- 当前支持的输入形态：
  - 默认仓库字典
  - 显式传入 `dict`
  - 显式传入字典文件路径
- 当前已实现的关键行为：
  - 解析链路会生成结构化 `dictionary_info`，不再只剩 warning 文本。
  - 外部字典失败原因已拆分为更细粒度的 `reason_code`。
  - 导出包会优先复制“解析时实际采用的字典”，并在 `meta.json.dictionary_status` 中固化状态。
  - `dict_ver` 与 trace header 不匹配时继续保留 `DICT_MISMATCH`。

## 当前 reason code 语义

| Reason Code | 含义 | 典型触发场景 |
|---|---|---|
| `DICT_EXTERNAL_PATH_MISSING` | 外部路径型字典不存在 | `dictionary=/path/to/missing.json` |
| `DICT_EXTERNAL_JSON_INVALID` | 外部路径型字典 JSON 非法 | 字典文件可读但 JSON 破损 |
| `DICT_EXTERNAL_SCHEMA_INVALID` | 外部字典结构不合法 | 缺 `event_defs`、字段类型错误等 |
| `DICT_DEFAULT_ASSET_MISSING` | 仓库默认字典资产缺失 | `spec/dictionary/event_dictionary.json` 不可读 |
| `DICT_DEFAULT_ASSET_INVALID` | 仓库默认字典资产格式非法 | 默认字典 JSON 或 schema 非法 |
| `DICT_BUILTIN_FALLBACK` | 已退回 built-in catalog | 默认字典不可用后继续降级 |
| `DICT_MISMATCH` | 版本不匹配但继续解码 | `catalog.dict_ver != trace.header.dict_ver` |

## 兼容矩阵

| 场景 | 输入方式 | 当前行为 | Warning/标记 | 解析产物/导出状态 | 验证状态 |
|---|---|---|---|---|---|
| 默认字典正常可用 | `dictionary=None` | 使用仓库默认字典解码 | 无额外标记 | `dictionary_info.resolved_source=default` | 已有回归 |
| 默认字典缺失 | `dictionary=None`，默认字典不可读 | 回退 built-in catalog | `DICT_DEFAULT_ASSET_MISSING` + `DICT_BUILTIN_FALLBACK` | `resolved_source=builtin` | 已有回归 |
| 默认字典格式非法 | `dictionary=None`，默认字典 JSON/schema 非法 | 回退 built-in catalog | `DICT_DEFAULT_ASSET_INVALID` + `DICT_BUILTIN_FALLBACK` | `resolved_source=builtin` | 实现已支持，专项样例未单列 |
| 外部字典对象输入有效 | `dictionary=dict` | 使用外部字典解码 | 无额外标记 | `requested_source=external_dict`、`resolved_source=external` | 已有回归 |
| 外部字典路径输入有效 | `dictionary=Path/str` | 使用外部字典解码 | 无额外标记 | `requested_source=external_path`、`resolved_source=external` | 已有回归 |
| 外部字典 `dict_ver` 不匹配 | 外部字典有效，但版本与 trace header 不同 | 保持解码，标记版本风险 | `DICT_MISMATCH` | `version_mismatch=true` | 已有回归 |
| 外部字典路径缺失 | 外部路径不存在 | 回退默认字典 | `DICT_EXTERNAL_PATH_MISSING` | `resolved_source=default` | 已有回归 |
| 外部字典 JSON 非法 | 外部路径可读但 JSON 损坏 | 回退默认字典 | `DICT_EXTERNAL_JSON_INVALID` | `resolved_source=default` | 已有回归 |
| 外部字典结构不完整 | 外部字典缺关键字段 | 回退默认字典 | `DICT_EXTERNAL_SCHEMA_INVALID` | `resolved_source=default` | 已有回归 |
| 外部字典内容语义变更 | 同一 `event_id` 改名/改字段 | 以外部字典为准解释 | 仅在版本不匹配时额外告警 | 导出包复制实际采用字典 | 部分覆盖 |
| 导出包伴随字典对齐 | 解析后导出 package | 复制实际采用字典，不再强制复制仓库默认字典 | `meta.json.dictionary_status` | `reference/dictionary.json` 与解析使用字典一致 | 已有回归 |
| 导出包重新加载后保留状态 | `viz_LoadDataset(package)` / `repro_LoadAsDataset()` | 从包内 `meta.json` + `reference/dictionary.json` 恢复状态 | 无新增标记 | `dictionary_info` 可继续向后传递 | 实现已接通，未单列专项样例 |
| 多版本演进矩阵 | 多个真实 `dict_ver` 资产前后兼容组合 | 尚无系统矩阵 | 无 | 仅有 mismatch 语义 | 未覆盖 |

## 已落地测试

- `tests/python/test_pipeline.py`
  - `test_missing_dictionary_falls_back_to_builtin_catalog`
  - `test_external_dictionary_dict_input_is_used_for_event_catalog`
  - `test_external_dictionary_path_input_is_used_for_event_catalog`
  - `test_external_dictionary_version_mismatch_marks_untrusted_window`
  - `test_external_dictionary_missing_path_falls_back_to_default_catalog`
  - `test_external_dictionary_invalid_json_falls_back_to_default_catalog`
  - `test_external_dictionary_invalid_payload_falls_back_to_default_catalog`
- `tests/python/test_desktop.py`
  - `test_compare_export_repro_and_replay`
  - `test_export_package_uses_actual_loaded_dictionary`

## 当前仍未闭环的点

1. 还没有覆盖真正多版本字典资产的系统演进矩阵，例如“旧解析器读新字典 / 新解析器读旧字典 / 部分字段前向兼容”的组合验证。
2. 还没有把字典兼容矩阵扩展到跨平台互换包、外部工具交换包和长期演进资产的系统验收。
3. 当前 `dictionary_status` 已能表达来源、降级和 reason code，但还没有形成独立的兼容审计工具或批量报告。
4. 默认字典非法这一条虽然实现已支持，但还缺一个显式专项测试样例。

## 当前结论

当前项目已经从“外部伴随字典装载入口 + 基本 fallback”推进到“**失效原因可区分、解析状态可结构化、导出包伴随字典可对齐**”的阶段。它已经不再只是最小入口，但距离需求/设计文档中“真实多版本资产演进矩阵系统化、交换包互操作全面验证”的完整口径仍有一段距离。
