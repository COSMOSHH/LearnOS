# RAG 主链路收口 Commit 文档

建议 commit message：

```text
收口 RAG 评测对比、Rewrite Guard 与低质样本归因
```

## 背景

2026-05-05 优化版 RAG 与 2026-05-06 原始 RAG 的 200 条 T2Retrieval 对比显示：

- Recall@1/3/5 持平，说明增强链路没有带来额外召回。
- 原始 RAG 的 MRR/NDCG 略高，说明增强链路在少数样本上存在排序扰动。
- 优化版 low_quality_cases 明显下降，但主要来自 weak_top1_score，不能直接等价为召回能力提升。
- 主要风险集中在 Query Rewrite 污染、数字实体检索和谜语/字面匹配类 query。

## 本次变更

1. Query Rewrite Guard
   - 禁止改写注入 `T2Retrieval`、`Benchmark`、`corpus`、`语料库`、`知识库`、`数据集`、`评测集` 等系统或评测语境词。
   - 数字实体 query 默认保持原 query，避免电话号码、编号、ID 被改写污染。
   - 谜语、歇后语、字面匹配类 query 默认保持原 query，避免语义扩写破坏字面匹配。
   - 短 query 过度扩写会回退到原 query，但允许带历史上下文的追问补全。

2. 特殊 Query 路由
   - 新增 `numeric_entity` 路由，使用 `numeric_exact_boost` 策略，提升 BM25 top-k。
   - 新增 `literal_riddle` 路由，使用 `literal_bm25_heavy` 策略，适配谜语、生肖、歇后语等问题。
   - Multi-Query 扩展对数字实体和谜语类 query 走保守扩展，保留原始字面表达。

3. Ablation 对比
   - RAG 评测请求新增 `run_ablation`。
   - 支持 original、latest、without Query Rewrite、without BM25、without Rerank、without Multi-Query、without Parent 回填等配置对比。
   - 前端 RAG 评测页新增 Ablation 开关，并展示每个配置的 MRR、Recall@1、Recall@5、NDCG@5、低质样本数和相对原始 RAG 的 delta。

4. 低质样本拆分
   - `low_quality_cases` 新增 `category` 和 `first_hit_rank`。
   - 新增 `low_quality_summary`：
     - `failed_cases`
     - `late_hit_cases`
     - `weak_confidence_cases`
   - 日志和前端展示 no-hit、late-hit、weak-confidence 三类计数。

5. 推进计划更新
   - 更新 `docs/plans/LearnOS最新推进计划与进度.md` 到 2026-05-06。
   - 将 RAG 对比配置、Ablation、Rewrite Guard、特殊路由、低质样本分类纳入第一阶段已完成项。
   - 将第一阶段状态调整为“主链路和评测闭环已完成，进入收口验证”。

## 主要文件

- `services/query_service.py`
- `services/rag_eval_service.py`
- `Zero_RAG/Server.py`
- `Zero_RAG/Client.py`
- `tests/test_services.py`
- `docs/plans/LearnOS最新推进计划与进度.md`
- `.gitignore`

## 验证

已执行：

```powershell
python -m py_compile services\query_service.py services\rag_eval_service.py Zero_RAG\Server.py Zero_RAG\Client.py tests\test_services.py
```

已执行：

```powershell
$env:DB_TYPE='sqlite'
D:\app_tools\anaconda3\envs\langchain\python.exe -m unittest `
  tests.test_services.ServiceTests.test_query_rewrite_heading_chunking_and_multi_query `
  tests.test_services.ServiceTests.test_rag_eval_metrics_and_low_quality_cases `
  tests.test_services.ServiceTests.test_rag_eval_original_config_disables_advanced_steps `
  tests.test_services.ServiceTests.test_rag_eval_supports_doc_ids_and_ndcg
```

结果：4 个相关单测通过。

已执行：

```powershell
$env:DB_TYPE='sqlite'
D:\app_tools\anaconda3\envs\langchain\python.exe -m unittest `
  tests.test_api.ApiTests.test_rag_eval_template_and_evaluate_endpoints
```

结果：1 个 RAG API 回归测试通过。

## 回归建议

下一次前端回归建议使用同一批 T2Retrieval 200 cases：

1. 运行 `最新RAG + 同时对比原始RAG`。
2. 勾选 `运行 Ablation 对比`。
3. 重点观察：
   - MRR 是否不低于原始 RAG 的 `0.9762`。
   - NDCG@5 是否不低于原始 RAG 的 `0.9853`。
   - Query Rewrite 污染样本是否归零。
   - `numeric_entity` 与 `literal_riddle` 的 no-hit 是否减少。

通过后再扩展到 500 cases，作为第一阶段 RAG 主链路收口验收。
