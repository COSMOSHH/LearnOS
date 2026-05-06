# LearnOS 最新推进计划与进度

更新日期：2026-05-06

## 总体路线

当前项目的最佳演进路线不是先堆复杂多 Agent，而是按下面三阶段推进：

1. 第一阶段：把 RAG 主链路打磨到可评测、可观测、可解释。
2. 第二阶段：新增 Learning Coach Agent，让它基于 RAG、错题、复习记忆和学习计划做决策。
3. 第三阶段：加入 Reflection，让回答和计划可以自动评估、修正。

项目当前更准确的定位是：

> 以 RAG 为核心的学习助手产品，已经具备学习闭环和 Agent 化扩展骨架；下一步应优先强化 RAG 质量闭环，再在学习规划、复习调度、回答修正这些高价值决策点引入 Agent。

## 进度总览

| 阶段 | 目标 | 当前状态 | 建议优先级 |
| --- | --- | --- | --- |
| 第一阶段 | RAG 可评测、可观测、可解释 | 已完成主链路和评测闭环，进入第一阶段收口验证 | P0 |
| 第二阶段 | Learning Coach Agent 决策闭环 | 有计划、复习、测验服务，但 Agent 未接入主链路 | P1 |
| 第三阶段 | Reflection 自动评估与修正 | 有回答评测，但没有自动反思重写 | P2 |

## 2026-05-06 更新：RAG 主链路收口

本轮围绕 2026-05-05 优化版与 2026-05-06 原始版 RAG 对比结论，完成四项收口工作：

- [x] Query Rewrite Guard：禁止改写注入 `T2Retrieval`、`Benchmark`、`语料库`、`知识库` 等系统/评测语境词；数字实体、谜语/字面匹配类 query 默认保持原 query，避免意图漂移。
- [x] 特殊检索路由：新增 `numeric_entity` 与 `literal_riddle` 两类路由，采用 BM25-heavy / exact-friendly 策略处理电话、编号、谜语、歇后语等非通用语义检索问题。
- [x] Ablation 对比：RAG 评测支持一键运行 original、latest、去掉 Query Rewrite、去掉 BM25、去掉 Rerank、去掉 Multi-Query、去掉 Parent 回填等配置，前端展示各配置相对原始 RAG 的指标差异。
- [x] 低质样本拆分：将 `low_quality_cases` 拆分为 `failed_cases`、`late_hit_cases`、`weak_confidence_cases`，日志和前端可分别查看 no-hit、排序靠后、置信度偏低三类问题。

当前判断：第一阶段还不建议立刻切到 Agent 主线，下一步应先用同一批 T2Retrieval case 回归验证 Guard 和特殊路由是否把 MRR/NDCG 拉回到不低于原始 RAG，再扩到 500 cases 做稳定性验证。

## 第一阶段：RAG 主链路打磨

### 阶段目标

把当前 RAG 从“能回答”推进到“能评估、能解释、能定位问题、能持续优化”。

这一阶段是实习项目最稳的核心亮点，优先级最高。

### 当前已完成

- [x] 文件导入：支持 `pdf / docx / txt / md`。
- [x] 网页导入：支持单网页正文抽取。
- [x] 批量网页导入：支持静态目录页同站批量导入。
- [x] 文本切分：支持基础 chunk 切分。
- [x] 语义分块第一版：支持标题、代码块、表格、列表、段落识别。
- [x] Chunk 元数据：已绑定 `session_id / document_id / source / chunk_index / heading_path` 等信息。
- [x] 向量数据库：使用 ChromaDB。
- [x] Embedding：已接入向量化。
- [x] Vector Retrieval：已支持向量检索。
- [x] BM25 Retrieval：已支持稀疏召回。
- [x] Hybrid Retrieval：已形成向量检索 + BM25 的混合召回。
- [x] Rerank：已接入重排。
- [x] Query Rewrite：已支持查询改写。
- [x] Multi-Query：已支持多查询扩展。
- [x] 问题类型识别：已支持 fact、compare、why、summary、interview、plan、quiz 等类型。
- [x] 动态检索路由：可按问题类型调整 top-k、Multi-Query、Parent window、上下文预算。
- [x] Parent-Child Retrieval：已支持命中 chunk 附近上下文回填。
- [x] 上下文去重与压缩：已支持生成前上下文预算控制。
- [x] 流式回答：学习问答支持流式输出。
- [x] 来源展示：前端可展示来源、分片、分数。
- [x] RAG Debug：可查看 query rewrite、召回、rerank、parent 回填等调试信息。
- [x] Answer Evaluation：已支持回答质量评估。
- [x] Retrieval Evaluation：已支持 MRR、Recall@1。
- [x] 固定 Benchmark 准备：已支持将 BEIR SciFact 转换为 LearnOS 可导入语料与固定评测 cases。
- [x] 扩展检索指标：已支持 Recall@1/3/5、MRR、NDCG@1/3/5。
- [x] 低质 Query 沉淀：低质量样本可进入质量看板。
- [x] RAG 质量看板：已展示 MRR、Recall@1、路由分布、低质原因和样本。
- [x] Run / Step 观测：已记录学习问答、检索、生成、评估等步骤。
- [x] RAG 对比配置：已支持最新 RAG、原始 RAG、自定义开关和原始 baseline 对比。
- [x] RAG Ablation：已支持 Query Rewrite、BM25、Rerank、Multi-Query、Parent 回填等模块级消融对比。
- [x] Query Rewrite Guard：已对系统词注入、数字实体改写、谜语/字面匹配改写和短 query 过度扩写做保护。
- [x] 特殊 Query 路由：已新增 `numeric_entity`、`literal_riddle` 两类检索路由。
- [x] 低质 Query 分类：已拆分 no-hit、late-hit、weak-confidence 三类低质样本。

### 待推进清单

- [x] 固定 RAG benchmark 数据集。
  - 目标：把临时评测升级为稳定回归评测。
  - 输出：新增 `tools/prepare_scifact_benchmark.py`，可把 BEIR SciFact 转换为 `benchmarks/scifact/scifact_corpus.md` 和 `benchmarks/scifact/scifact_test_eval_cases.json`。

- [x] 扩展检索评估指标。
  - 目标：从 Recall@1 / MRR 扩展到 Recall@3、Recall@5、NDCG。
  - 输出：RAG 评估接口、运行 metadata 和质量看板已展示 NDCG@5。

- [ ] 加入 RAG 指标门禁。
  - 目标：核心评测集低于阈值时能暴露问题。
  - 输出：最小 CI 或本地命令，跑测试 + RAG benchmark。

- [x] 增加 query rewrite 前后对比。
  - 目标：解释改写是否真的提升召回。
  - 输出：通过 RAG 对比配置和 ablation 记录 original/latest/without-rewrite 的召回与排序指标差异。

- [x] 增加 rerank 前后排序对比。
  - 目标：解释 rerank 是否提升结果排序。
  - 输出：通过 ablation 记录 latest 与 without-rerank 的 MRR、Recall、NDCG 和低质样本差异；后续仍可增强到 debug 面板展示逐 query rerank 前后 top-k。

- [ ] 加入 embedding 缓存。
  - 目标：降低重复入库和重复 query 的 embedding 成本。
  - 输出：按 text hash 缓存 embedding。

- [ ] 加入 retrieval 缓存。
  - 目标：相同 session + query + route 下复用检索结果。
  - 输出：缓存命中率、过期策略、手动清理入口。

- [ ] 增加 token、耗时、成本统计。
  - 目标：让系统具备生产级可观测雏形。
  - 输出：run metadata 记录模型、token、耗时、估算成本。

- [ ] 增强来源引用粒度。
  - 目标：从 chunk 级引用推进到段落级或句级引用。
  - 输出：回答中可定位到更细的依据片段。

- [ ] 低质 query 状态流转。
  - 目标：低质样本不只是沉淀，还能管理修复状态。
  - 输出：`待修复 / 已修复 / 忽略` 状态和备注。

### 第一阶段验收标准

- [x] 有一组稳定 RAG benchmark。
- [x] 每次 RAG 优化后可以量化对比指标。
- [ ] 能解释一次回答的检索过程：原 query、改写 query、召回结果、rerank 排序、最终上下文、来源依据。
- [ ] 能定位低质回答原因：已完成检索侧 no-hit / late-hit / weak-confidence 分类；切分、上下文压缩、生成侧归因仍待补齐。
- [ ] 能在面试中讲清楚：为什么这个 RAG 不是简单向量问答。

### 第一阶段建议里程碑

| 里程碑 | 内容 | 优先级 |
| --- | --- | --- |
| M1 | 扩充 benchmark + Recall@3 / Recall@5 / NDCG | 已完成 |
| M2 | Query rewrite 与 rerank 前后对比 | 已完成第一版 ablation |
| M3 | 低质 query 状态管理 | 部分完成：已拆分原因，状态流转待做 |
| M4 | embedding / retrieval 缓存 | P1 |
| M5 | token、耗时、成本统计 | P1 |
| M6 | 更细粒度引用 | P2 |

### SciFact Benchmark 使用方式

已生成本地文件：

- `benchmarks/scifact/scifact_corpus.md`
- `benchmarks/scifact/scifact_test_eval_cases.json`
- `benchmarks/scifact/manifest.json`

生成命令：

```powershell
python tools\prepare_scifact_benchmark.py --dataset-dir datasets\scifact\scifact --output-dir benchmarks\scifact --split test
```

建议使用方式：

1. 在 LearnOS 中新建一个专门的 `SciFact Benchmark` 学习会话。
2. 导入 `benchmarks/scifact/scifact_corpus.md`。
3. 在前端 `RAG评测` 页点击 `加载SciFact评测集`，或使用 `/rag/benchmarks/scifact` 接口加载固定 cases。
4. 观察 MRR、Recall@1/3/5、NDCG@1/3/5 和低质 query。

## 第二阶段：Learning Coach Agent

### 阶段目标

新增一个真正服务学习闭环的垂直 Agent，而不是为了包装概念去堆多 Agent。

Learning Coach Agent 的核心职责：

> 基于 RAG 检索结果、历史问答、错题、复习记忆、测验表现和学习计划，判断用户今天应该学什么、先复习什么、要不要出题、下一步该问什么。

### 为什么选择 Learning Coach Agent

相比通用多 Agent，它更适合当前项目：

1. 和学习场景强绑定。
2. 能复用已有 RAG、错题、复习、计划、测验服务。
3. 决策结果容易展示。
4. 面试时更容易解释 Agent 的必要性。
5. 不会把项目复杂度拉得过高。

### 当前已有基础

- [x] 学习会话。
- [x] 文档摘要。
- [x] 知识点沉淀。
- [x] 历史问答。
- [x] 回答质量评估。
- [x] 测验生成和评分。
- [x] 错题本。
- [x] 错题重练。
- [x] 复习队列。
- [x] 学习计划生成。
- [x] 学习计划保存和完成状态更新。
- [x] run / step 观测。
- [x] `agent_engine.py` 已有 tool calling 与多 assistant 骨架。

### 待推进清单

- [ ] 定义 Learning Coach Agent 输入状态。
  - 输入应包含：当前会话、学习目标、资料摘要、知识点、最近问答、回答评估、错题、复习项、计划完成情况。

- [ ] 定义 Learning Coach Agent 输出协议。
  - 输出建议结构：
    - `today_focus`
    - `priority_review_items`
    - `recommended_questions`
    - `quiz_suggestion`
    - `plan_adjustments`
    - `reasoning_summary`

- [ ] 封装 Coach 可用工具。
  - 工具建议：
    - `get_session_learning_state`
    - `retrieve_learning_context`
    - `get_review_queue`
    - `get_wrong_questions`
    - `get_latest_plan`
    - `suggest_plan_adjustment`
    - `suggest_quiz`
    - `save_coach_recommendation`

- [ ] 新增 Coach 决策接口。
  - 建议接口：
    - `POST /study_sessions/{session_id}/coach/recommend`
    - `GET /study_sessions/{session_id}/coach/recommendations`

- [ ] 新增 Coach 运行记录。
  - run_type 建议：
    - `coach.recommend`
    - `coach.plan_adjust`
    - `coach.quiz_suggest`

- [ ] 前端新增 Coach 面板。
  - 展示内容：
    - 今天先学什么
    - 先复习什么
    - 建议追问什么
    - 是否建议自测
    - 为什么这样安排

- [ ] Coach 与学习计划联动。
  - 目标：Coach 可以基于错题和复习项调整计划优先级。

- [ ] Coach 与测验联动。
  - 目标：Coach 可以判断是否需要生成测验，以及测验主题。

- [ ] Coach 与 RAG 联动。
  - 目标：Coach 推荐追问时能基于资料检索结果，而不是只靠摘要。

### 第二阶段验收标准

- [ ] 用户打开一个学习会话后，可以看到 Coach 推荐的今日学习动作。
- [ ] 推荐结果能解释依据：来自错题、复习项、测验分数、历史问答还是资料内容。
- [ ] Coach 能调用至少 3 类真实业务工具。
- [ ] Coach 的决策结果可以持久化。
- [ ] Coach 的运行过程可在 run / step 中查看。
- [ ] Coach 不是简单 prompt 生成，而是基于结构化学习状态做决策。

### 第二阶段建议里程碑

| 里程碑 | 内容 | 优先级 |
| --- | --- | --- |
| M1 | 定义 learning_state 聚合接口 | P0 |
| M2 | 实现 Coach 推荐结构化输出 | P0 |
| M3 | 接入复习队列、错题、计划工具 | P0 |
| M4 | 前端 Coach 面板 | P1 |
| M5 | Coach 推荐持久化与 run/step 观测 | P1 |
| M6 | Coach 自动触发计划调整或测验建议 | P2 |

## 第三阶段：Reflection 自动评估与修正

### 阶段目标

把现有“回答后评估”升级为“评估后可自动修正”。

Reflection 的目标不是展示模型思考过程，而是形成可控的质量闭环：

> 生成回答或学习计划后，系统自动评估质量。如果低于阈值，则判断问题原因，并触发重新检索、重写回答或调整计划。

### 当前已有基础

- [x] 回答质量评估。
- [x] 面试回答评分。
- [x] RAG 检索评估。
- [x] 低质 query 沉淀。
- [x] run / step 观测。
- [x] 学习计划生成和保存。

### 待推进清单

- [ ] 定义回答 Reflection 评分维度。
  - 建议维度：
    - groundedness
    - completeness
    - clarity
    - source_coverage
    - hallucination_risk

- [ ] 定义回答修正策略。
  - 如果 groundedness 低：重新检索。
  - 如果 completeness 低：扩大 top-k 或 parent_window。
  - 如果 clarity 低：重写表达。
  - 如果 source_coverage 低：补充引用。
  - 如果 hallucination_risk 高：收缩回答，只保留有依据内容。

- [ ] 新增 Reflection run。
  - run_type 建议：
    - `reflection.answer`
    - `reflection.plan`

- [ ] 新增反思步骤记录。
  - step 建议：
    - `evaluate_initial_answer`
    - `diagnose_issue`
    - `revise_retrieval_strategy`
    - `regenerate_answer`
    - `evaluate_revised_answer`

- [ ] 实现回答低分自动重写。
  - MVP 规则：
    - 总分低于阈值时触发一次重写。
    - 最多重写一次，避免无限循环。
    - 保留原回答和修正版对比。

- [ ] 实现计划 Reflection。
  - 评估学习计划是否：
    - 目标明确
    - 顺序合理
    - 覆盖薄弱点
    - 时间可执行
    - 有复习和自测动作

- [ ] Reflection 结果沉淀。
  - 目标：把低质量原因写入质量样本或 run metadata。

### 第三阶段验收标准

- [ ] 回答生成后能自动评估。
- [ ] 低分回答能触发一次自动修正。
- [ ] 修正过程可观测。
- [ ] 能解释为什么修正：证据不足、覆盖不全、表达不清、引用不足等。
- [ ] 计划生成后可以评估并给出调整建议。
- [ ] Reflection 不影响正常响应稳定性。

### 第三阶段建议里程碑

| 里程碑 | 内容 | 优先级 |
| --- | --- | --- |
| M1 | 回答 Reflection 评分协议 | P0 |
| M2 | 低分回答自动重写一次 | P0 |
| M3 | Reflection run/step 观测 | P1 |
| M4 | 原回答与修正版对比展示 | P1 |
| M5 | 学习计划 Reflection | P2 |
| M6 | Reflection 结果反馈到 RAG 质量看板 | P2 |

## 推荐实施顺序

### 第 1 批：最值得马上做

- [ ] 扩充 RAG benchmark。
- [ ] 增加 Recall@3 / Recall@5 / NDCG。
- [ ] 增加 query rewrite 前后对比。
- [ ] 增加 rerank 前后排序对比。
- [ ] 定义 Learning Coach Agent 的 learning_state 聚合接口。

原因：这批任务能直接增强项目可信度，也为 Coach Agent 提供数据基础。

### 第 2 批：形成 Agent 亮点

- [ ] 实现 Coach 推荐结构化输出。
- [ ] 接入复习队列、错题、学习计划。
- [ ] 新增 Coach run/step 观测。
- [ ] 前端新增 Coach 面板。

原因：这批任务能让项目从 RAG 学习助手自然升级为 Agentic Learning Assistant。

### 第 3 批：形成自动修正闭环

- [ ] 定义回答 Reflection 评分协议。
- [ ] 低分回答自动重写一次。
- [ ] Reflection 结果写入 run metadata。
- [ ] 低质样本和 Reflection 原因联动。

原因：这批任务能体现 Agent 的自我评估和修正能力，但复杂度高于前两批。

## 面试讲法

可以这样介绍项目演进：

> 我没有一开始就堆多 Agent，而是先把 RAG 主链路做扎实。第一阶段重点是可评测、可观测、可解释，包括 Query Rewrite、Multi-Query、Hybrid Retrieval、Rerank、Parent 回填、RAG Debug、MRR/Recall 和质量看板。第二阶段我会加入 Learning Coach Agent，让它基于错题、复习记忆、历史问答和学习计划做学习决策。第三阶段再加入 Reflection，让回答和计划可以自动评估、自动修正。这样 Agent 不是概念包装，而是自然服务学习闭环。

## 最终目标

最终 LearnOS 应该从普通 RAG 学习问答系统，演进为：

> 一个可评测、可观测、可解释的 Agentic RAG 学习助手。它不仅能基于资料回答问题，还能根据学习状态主动安排复习、推荐追问、生成测验，并在回答或计划质量不足时自动反思和修正。
