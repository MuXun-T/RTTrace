# Public RTOS 1GB Prevalidation Sample

本目录保存当前仓库可直接复用的 `>= 1GB` public-RTOS 预验证样本。

## 内容

1. `public_rtos_1gb_fast_baseline.trace`
2. `public_rtos_1gb_fast_candidate.trace`
3. `nuttx_public_seed.systrace`
4. `public_rtos_1gb_fast_manifest.json`
5. `public_rtos_1gb_fast_preflight.json`
6. `public_rtos_1gb_dense_template.json`

## 当前校验值

1. `public_rtos_1gb_fast_baseline.trace`
   - size: `1073741880`
   - sha256: `f445be02657c6d24ac8d419323d634ee226b751b855ff4a7b6155bc5156fd245`
2. `public_rtos_1gb_fast_candidate.trace`
   - size: `1073741880`
   - sha256: `f445be02657c6d24ac8d419323d634ee226b751b855ff4a7b6155bc5156fd245`
3. `nuttx_public_seed.systrace`
   - sha256: `e2c4ed9ba56e47c5edcfbde225ded7b766848e8c2e953055d72fba51375967c0`

## 使用边界

1. 该样本来自公开 Apache NuttX task trace 语义，并已映射到当前仓库正式 RTOS 事件模型。
2. 该样本的 provenance 是 `public_rtos_seeded_padded`，只用于预验证、压链路、收缺陷。
3. 该样本不能替代最终真实 external `>= 1GB` 输入，也不能把 `formal_1gb_verified` 关成 `true`。

## 注意事项

1. `public_rtos_1gb_fast_manifest.json` 记录的是原始生成过程，因此其中仍会保留生成时的临时路径。
2. checked-in 的 `public_rtos_1gb_fast_manifest.json` 属于 `2026-03-21` 历史生成过程留档；它的字段集可能落后于当前 `tool/build_public_rtos_large_input.py --report` 输出，不应当被当作当前 builder 输出 schema 的权威来源。
3. 当前 public fast/dense 契约以 `docs/public_rtos_1gb_prevalidation_20260321.md` 和 `public_rtos_1gb_dense_template.json` 为准。
4. 目录中的 `public_rtos_1gb_fast_preflight.json` 已在仓库内路径上重新生成，可直接用于当前仓库引用。
5. `example/` 中的样本主要用于留档和交接；真正做耗时/内存验收时，应先把同一份字节内容复制到本地 SSD / `ext4` scratch，再执行 `desktop_perf_acceptance`。
6. 2026-03-21 已在本地 `ext4` 临时目录上尝试过一次 `1GB` public-RTOS `perf_only` 运行，但在 `3` 分 `20` 秒后仍未完成并被人工终止，因此当前结论仍是“可预验证、未正式闭环”。
7. dense 样本不直接入库；当前 repo 只保留 dense 生成模板，实际 dense `1GB` 文件应在本地 scratch 或外部执行机生成。
