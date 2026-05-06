# RAG Retrieval Evaluation 分析报告

## 1. 评测概况

本次日志类型为 `rag_retrieval_evaluation`，评测规模为 **200 条 case**，请求参数如下：

```json
{
  "top_k": 5,
  "low_quality_mrr_threshold": 0.5,
  "case_count": 200
}
```

本次结果不再是 10 / 50 条小样本时的满分，已经开始暴露真实边界问题，因此比前两次更有分析价值。

---

## 2. 总体指标

| 指标 | 结果 |
|---|---:|
| MRR | 0.9752 |
| Recall@1 | 0.965 |
| Recall@3 | 0.985 |
| Recall@5 | 0.990 |
| NDCG@1 | 0.965 |
| NDCG@3 | 0.9788 |
| NDCG@5 | 0.9847 |

### 解读

- `Recall@1 = 0.965`：约 **193 / 200** 条 query 在第 1 位命中相关结果。
- `Recall@3 = 0.985`：约 **197 / 200** 条 query 在前 3 位命中。
- `Recall@5 = 0.990`：约 **198 / 200** 条 query 在前 5 位命中。
- `MRR = 0.9752`：说明绝大多数样本排序靠前，失败或迟命中的样本数量较少。

### 结论

当前检索链路整体健康，已经具备较强的基础检索能力。下一步重点不应该是推倒重来，而是针对失败样本进行定向优化。

---

## 3. 分桶指标分析

| 问题类型 | Case 数 | MRR | Recall@1 | NDCG@5 |
|---|---:|---:|---:|---:|
| general | 152 | 0.9706 | 0.9605 | 0.9824 |
| why | 9 | 1.0000 | 1.0000 | 1.0000 |
| fact | 30 | 1.0000 | 1.0000 | 1.0000 |
| summary | 3 | 1.0000 | 1.0000 | 1.0000 |
| compare | 4 | 1.0000 | 1.0000 | 1.0000 |
| interview | 2 | 0.7500 | 0.5000 | 0.8155 |

### 关键观察

1. **general 是主要样本来源**
   - 152 / 200 条属于 general。
   - general 的 Recall@1 为 0.9605，是整体指标下降的主要来源。

2. **fact / why / summary / compare 当前表现很好**
   - 这些类型本次全部满分。
   - 但 summary 和 compare 样本数仍然偏少，不宜过度下结论。

3. **interview 类型最弱**
   - 仅 2 条样本，但 Recall@1 只有 0.5。
   - 暂时不能断言 interview 策略有系统性问题，但它是后续扩样重点。

---

## 4. Low Quality Cases 分析

本次 `low_quality_cases` 共 6 条：

| Query | Reason | Reciprocal Rank | 问题类型 |
|---|---|---:|---|
| 游戏主播七喵是男的还是女的 | weak_top1_score | 1.0 | 低置信度但命中 |
| 司鱼怎么样 | weak_top1_score | 0.5 | 改写污染 / 排序靠后 |
| 4008888518是哪里的电话 | no_hit | 0.0 | 数字实体检索失败 |
| 欲钱买春光灿烂打一肖 | no_hit | 0.0 | 谜语/生肖字面匹配失败 |
| 不备忘录详细解答 | late_hit_rank_3 | 0.3333 | 改写污染 / 迟命中 |
| 小辣椒和什么最和适 | late_hit_rank_5 | 0.2 | typo + 语义改写偏移 |

---

## 5. 问题归因

### 5.1 Query Rewrite 污染

典型案例：

```text
司鱼怎么样
→ 司鱼在T2Retrieval中文Benchmark中的表现如何
```

以及：

```text
不备忘录详细解答
→ T2Retrieval 中文 Benchmark 是否提供不依赖备忘录的详细解答
```

这类改写明显引入了用户原始问题中不存在的系统词，例如：

- T2Retrieval
- Benchmark
- 中文 Benchmark
- 数据集
- 语料库
- 知识库

这会严重污染检索意图。

#### 建议

增加 rewrite guard：

```python
FORBIDDEN_REWRITE_TERMS = [
    "T2Retrieval",
    "Benchmark",
    "benchmark",
    "中文Benchmark",
    "语料库",
    "知识库",
    "数据集",
]

def validate_rewrite(original: str, rewritten: str) -> str:
    original_lower = original.lower()
    rewritten_lower = rewritten.lower()

    original_has_forbidden = any(
        term.lower() in original_lower
        for term in FORBIDDEN_REWRITE_TERMS
    )

    rewritten_has_forbidden = any(
        term.lower() in rewritten_lower
        for term in FORBIDDEN_REWRITE_TERMS
    )

    if rewritten_has_forbidden and not original_has_forbidden:
        return original

    return rewritten
```

---

### 5.2 数字实体检索失败

失败案例：

```text
4008888518是哪里的电话
```

当前改写为：

```text
电话号码4008888518归属地查询
```

但最终没有命中。

数字实体类 query 的特点是：

- 语义向量不稳定；
- 数字必须精确保留；
- BM25 / exact match 更重要；
- 不应过度依赖语义扩写。

#### 建议

新增 `numeric_entity` 路由：

```python
import re

def is_numeric_entity_query(query: str) -> bool:
    return bool(re.search(r"\d{6,}", query))
```

对应策略：

```python
{
    "strategy_name": "numeric_exact_boost",
    "use_multi_query": True,
    "vector_top_k": 3,
    "bm25_top_k": 20,
    "final_top_k": 5,
    "parent_window": 1,
    "parent_max_chars": 900,
    "max_context_chars": 1800,
    "per_chunk_max_chars": 420
}
```

推荐 expanded queries：

```text
4008888518
4008888518 是哪里的电话
电话号码 4008888518
4008888518 归属地
```

---

### 5.3 谜语 / 生肖类问题失败

失败案例：

```text
欲钱买春光灿烂打一肖
```

改写后：

```text
谜语“欲钱买春光灿烂”打一生肖
```

这类问题更依赖字面匹配，关键 token 如：

- 欲钱买
- 春光灿烂
- 打一肖
- 打一生肖
- 谜语

不能被语义改写稀释。

#### 建议

新增 `riddle_literal` 路由：

```python
RIDDLE_PATTERNS = [
    "打一肖",
    "打一生肖",
    "谜语",
    "欲钱买",
    "歇后语",
    "成语",
]

def is_riddle_query(query: str) -> bool:
    return any(token in query for token in RIDDLE_PATTERNS)
```

对应策略：

```python
{
    "strategy_name": "literal_bm25_heavy",
    "use_multi_query": True,
    "vector_top_k": 3,
    "bm25_top_k": 20,
    "final_top_k": 5,
    "parent_window": 1,
    "parent_max_chars": 900,
    "max_context_chars": 1800,
    "per_chunk_max_chars": 420
}
```

推荐 expanded queries：

```text
欲钱买春光灿烂打一肖
欲钱买春光灿烂 打一生肖
春光灿烂 打一生肖
```

---

### 5.4 Typo 短 query 改写偏移

问题案例：

```text
小辣椒和什么最和适
```

明显应该修正为：

```text
小辣椒和什么最合适
```

但当前改写为：

```text
小辣椒和什么食物最搭配
```

问题在于它额外引入了“食物”这一领域假设。如果原语料讲的是种植搭配、配菜、调味或其他场景，就可能造成召回偏移。

#### 建议

对短 query typo 修正保持保守：

```text
小辣椒和什么最合适
小辣椒和什么最搭配
小辣椒和什么一起合适
```

不要在没有证据时加入具体领域词，例如“食物”“种植”“药用”等。

---

### 5.5 Low Quality 分类需要拆分

当前 `low_quality_cases` 同时包含：

- 真失败：`no_hit`
- 命中靠后：`late_hit_rank_3` / `late_hit_rank_5`
- 命中了但分数低：`weak_top1_score`

这三类问题的修复方式完全不同，不应该混在一个列表里。

#### 建议改成：

```json
{
  "failed_cases": [],
  "late_hit_cases": [],
  "weak_confidence_cases": []
}
```

分类逻辑：

```python
if reciprocal_rank == 0:
    failed_cases.append(case)
elif first_hit_rank and first_hit_rank > 1:
    late_hit_cases.append(case)
elif top1_score < 0.35:
    weak_confidence_cases.append(case)
```

---

## 6. 当前不建议优先优化的部分

### 6.1 不建议先换 embedding

目前 Recall@5 已经达到 0.990，说明绝大多数样本已经被召回。失败主要集中在特殊 query 类型和 query rewrite 污染，而不是整体 embedding 能力不足。

### 6.2 不建议先换 reranker

MRR = 0.9752，整体排序质量已经不错。迟命中样本需要分析，但当前还不足以证明 reranker 是主要瓶颈。

### 6.3 不建议盲目增大 top_k

增大 top_k 可能提升 Recall@5，但会带来更多噪声进入生成上下文。当前更应该做 query pattern 路由和 rerank 前候选质量优化。

---

## 7. 优化优先级

### P0：修复 rewrite guard

目标：禁止 rewrite 注入系统词。

预期收益：

- 修复 `司鱼怎么样`
- 修复 `不备忘录详细解答`
- 降低语义污染导致的迟命中和误召回

---

### P1：新增数字实体路由

目标：提升电话号码、编号、ID、长数字实体的精确召回能力。

适用 query：

```text
4008888518是哪里的电话
```

策略：

- 保留原始数字；
- 增加 exact / substring / BM25 权重；
- 降低 vector 依赖。

---

### P1：新增谜语 / 字面匹配路由

目标：处理谜语、生肖、歇后语、成语类 query。

适用 query：

```text
欲钱买春光灿烂打一肖
```

策略：

- 保留原句；
- 多 query 只做轻微变体；
- BM25 top_k 提高到 20。

---

### P2：短 query typo 保守改写

目标：修复错别字，但不擅自补充领域词。

适用 query：

```text
小辣椒和什么最和适
```

策略：

- 修错别字；
- 保留原 query；
- 生成 2~3 个轻量改写；
- 不加入无依据领域词。

---

### P2：拆分 low quality 日志结构

目标：让后续分析更清晰。

建议新增：

```json
{
  "failed_case_count": 0,
  "late_hit_case_count": 0,
  "weak_confidence_case_count": 0,
  "failed_cases": [],
  "late_hit_cases": [],
  "weak_confidence_cases": []
}
```

---

## 8. 后续验证计划

### Step 1：修复 rewrite guard

修复后先跑：

```text
case_count = 200
```

重点看：

- `司鱼怎么样`
- `不备忘录详细解答`

是否不再引入 Benchmark / T2Retrieval 等词。

---

### Step 2：新增 numeric_entity / riddle_literal 路由

再跑：

```text
case_count = 200
```

重点看：

- `4008888518是哪里的电话`
- `欲钱买春光灿烂打一肖`

是否从 `no_hit` 变为 hit。

---

### Step 3：跑 500 条

最终建议跑：

```text
case_count = 500
```

重点指标：

| 指标 | 目标 |
|---|---:|
| MRR | >= 0.975 |
| Recall@1 | >= 0.970 |
| Recall@3 | >= 0.990 |
| Recall@5 | >= 0.995 |
| no_hit 数量 | 明显下降 |
| rewrite 污染数量 | 0 |

---

## 9. 最终结论

这次 200 条评测说明：

1. 当前 RAG 检索链路整体质量很好；
2. 主体能力不是瓶颈，Recall@5 已达到 0.990；
3. 问题主要集中在 query rewrite 和特殊 query 路由；
4. 不建议优先更换 embedding 或 reranker；
5. 应优先修复 rewrite 污染、数字实体、谜语字面匹配和 typo 短 query；
6. 修复后应继续用 200 / 500 条样本验证。

一句话总结：

> 当前系统已经过了基础检索质量线，下一阶段应从“整体调参”转向“失败样本驱动的专项优化”。
