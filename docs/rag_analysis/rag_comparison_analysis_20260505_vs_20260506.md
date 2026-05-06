# RAG 对比配置评测分析报告

> 对比对象：
>
> - **20260505 优化版 RAG**：开启 Query Rewrite、动态路由、Multi-Query、BM25、Rerank、Parent 回填等优化能力。
> - **20260506 原始版 RAG**：`mode = original`，关闭 Query Rewrite、动态路由、Multi-Query、BM25、Rerank、Parent 回填。
>
> 评测规模：两次均为 **200 条 case**，`top_k = 5`，`low_quality_mrr_threshold = 0.5`。

---

## 1. 总体结论

这轮对比中，**优化版 RAG 没有在整体指标上超过原始版 RAG**。两者的 Recall 指标完全一致，说明优化链路没有带来更多有效召回；同时原始版在 MRR 和 NDCG 上略高，说明排序位置反而略优。

不过，优化版并非没有价值。它显著减少了 `low_quality_cases` 的数量，说明 BM25、Rerank、Parent 回填等组件对结果置信度和上下文质量有正向作用。当前主要问题不在整体召回能力，而在 **Query Rewrite 污染** 和 **特殊 query 路由不足**。

一句话总结：

> 当前优化版更像是“上下文增强 / 置信度增强版”，还不是“召回与排序显著提升版”。下一步应从整体调参转向失败样本驱动的专项优化。

---

## 2. 评测配置对比

| 项目 | 20260505 优化版 | 20260506 原始版 |
|---|---|---|
| case_count | 200 | 200 |
| top_k | 5 | 5 |
| low_quality_mrr_threshold | 0.5 | 0.5 |
| Query Rewrite | 开启 | 关闭 |
| Dynamic Route | 开启 | 关闭 |
| Multi-Query | 按路由开启 | 关闭 |
| BM25 | 开启 | 关闭 |
| Rerank | 开启 | 关闭 |
| Parent 回填 | 开启 | 关闭 |
| 问题类型识别 | 开启 | 全部 unknown |

原始版配置中明确关闭了所有优化项：

```json
{
  "mode": "original",
  "use_query_rewrite": false,
  "use_dynamic_route": false,
  "use_multi_query": false,
  "use_bm25": false,
  "use_rerank": false,
  "use_parent": false
}
```

---

## 3. 核心指标对比

| 指标 | 20260505 优化版 | 20260506 原始版 | 差异 | 判断 |
|---|---:|---:|---:|---|
| MRR | 0.9752 | 0.9762 | -0.0010 | 原始版略优 |
| Recall@1 | 0.965 | 0.965 | 0 | 持平 |
| Recall@3 | 0.985 | 0.985 | 0 | 持平 |
| Recall@5 | 0.990 | 0.990 | 0 | 持平 |
| NDCG@1 | 0.965 | 0.965 | 0 | 持平 |
| NDCG@3 | 0.9788 | 0.9813 | -0.0025 | 原始版略优 |
| NDCG@5 | 0.9847 | 0.9853 | -0.0006 | 原始版略优 |

### 3.1 指标解读

1. **Recall 完全持平**

   两版 Recall@1 / Recall@3 / Recall@5 完全一致，说明优化版没有额外找到更多正确结果。

2. **原始版排序略优**

   原始版 MRR 高 0.001，NDCG@3 高 0.0025，NDCG@5 高 0.0006。差距不大，但方向说明优化链路在少数样本上把正确结果排低了。

3. **整体质量都已经很高**

   两版 Recall@5 都达到 0.990，说明 200 条样本里约 198 条能在前 5 位找到相关结果。当前瓶颈不是基础召回能力，而是特殊 query 的精确处理和排序稳定性。

---

## 4. Low Quality Cases 对比

| 项目 | 20260505 优化版 | 20260506 原始版 | 变化 |
|---|---:|---:|---:|
| low_quality_cases 总数 | 6 | 31 | 优化版少 25 条 |
| no_hit | 2 | 2 | 持平 |
| late_hit | 2 | 1 | 优化版多 1 条 |
| weak_top1_score | 2 | 28 | 优化版少 26 条 |

### 4.1 关键观察

优化版的 `low_quality_cases` 数量明显下降，从 31 条降到 6 条。这个变化主要来自 `weak_top1_score` 的减少。

这说明优化版虽然没有提高 Recall，但确实改善了大量“命中了但分数偏低”的样本。换句话说，BM25 / Rerank / Parent 回填可能提升了命中结果的置信度和上下文可用性。

但需要注意：

- `no_hit` 没有减少；
- Recall 没有提升；
- MRR / NDCG 还略微下降。

因此，这个优化收益更偏“结果质量感知”或“生成上下文质量”，不是严格意义上的检索指标提升。

---

## 5. 优化版收益分析

### 5.1 低置信度样本显著减少

原始版中有大量样本属于 `weak_top1_score`，即第 1 位已经命中，但 top1 score 偏低。例如：

- `肋弓有第7至10对肋骨依次连接而成错误`
- `龙视安网络枪亮灯无信号`
- `亚洲四大神器是什么`
- `华为专业相机 戈壁`
- `三角梅枝干直径6公分是多少年`
- `勇敢的心赵舒城枪毙霍骁林是哪集`

优化版中这些样本大多不再出现在 low quality 列表里，说明增强链路提高了 top1 结果的综合分数。

### 5.2 路由和问题类型识别带来可解释性

优化版会给 query 打上问题类型，例如：

- `general`
- `why`
- `fact`
- `summary`
- `compare`
- `interview`

并根据问题类型选择不同路由策略，例如：

- `balanced_default`
- `focused_single_query`
- `mechanism_multi_query`
- `compare_multi_query`

这对后续定位问题有帮助。原始版全部为 `unknown`，缺乏可解释性。

### 5.3 Parent 回填提升生成上下文质量

优化版中部分样本出现了 `parent_enriched > 0`，说明检索命中 chunk 后会补充相邻上下文。这对 RAG 最终生成答案通常是有价值的，即使纯检索指标不一定明显提升。

---

## 6. 优化版主要问题

### 6.1 Query Rewrite 污染

这是当前最需要优先修复的问题。

#### 案例 1：`司鱼怎么样`

原始 query：

```text
司鱼怎么样
```

优化版改写：

```text
司鱼在T2Retrieval中文Benchmark中的表现如何
```

问题：

- 原 query 没有 `T2Retrieval`；
- 原 query 没有 `Benchmark`；
- 改写结果把系统评测语境注入到了用户意图中；
- 最终 top1 变成了不相关的 benchmark corpus 入口，导致 RR 从原始版的 1.0 下降到 0.5。

#### 案例 2：`不备忘录详细解答`

原始 query：

```text
不备忘录详细解答
```

优化版改写：

```text
T2Retrieval 中文 Benchmark 是否提供不依赖备忘录的详细解答
```

问题同样是引入了 `T2Retrieval` / `Benchmark` 等系统词，导致迟命中。

#### 案例 3：`小辣椒和什么最和适`

原始 query：

```text
小辣椒和什么最和适
```

优化版改写：

```text
小辣椒和什么食物最搭配
```

问题：

- `最和适` 应该保守修正为 `最合适`；
- 改写额外加入了“食物”这一领域假设；
- 如果语料中讨论的是种植搭配、调味搭配或其他场景，就可能产生召回偏移；
- 该样本从原始版 top1 命中变成优化版 late_hit_rank_5。

### 6.2 特殊 query 路由不足

两个版本都没有解决以下样本：

| Query | 问题类型 | 当前结果 |
|---|---|---|
| `4008888518是哪里的电话` | 数字实体 / 电话号码 | no_hit |
| `欲钱买春光灿烂打一肖` | 谜语 / 生肖 / 字面匹配 | no_hit |

这说明当前优化项没有覆盖这两类问题：

1. 长数字实体需要 exact / substring / BM25 加权，而不能主要依赖向量语义。
2. 谜语、生肖、歇后语类 query 更依赖字面 token，不能被语义改写稀释。

### 6.3 general 与 interview 是主要风险桶

优化版分桶结果中：

| 问题类型 | Case 数 | MRR | Recall@1 | NDCG@5 |
|---|---:|---:|---:|---:|
| general | 152 | 0.9706 | 0.9605 | 0.9824 |
| interview | 2 | 0.7500 | 0.5000 | 0.8155 |

`general` 样本最多，是整体指标下降的主要来源。`interview` 样本数很少，但表现偏弱，后续需要扩大样本再判断是否存在系统性问题。

---

## 7. 失败样本归因表

| Query | 原始版表现 | 优化版表现 | 主要原因 | 优先级 |
|---|---|---|---|---|
| 司鱼怎么样 | RR=1.0，weak_top1_score | RR=0.5，weak_top1_score | Rewrite 注入 Benchmark 语境 | P0 |
| 不备忘录详细解答 | RR=1.0，weak_top1_score | RR=0.3333，late_hit_rank_3 | Rewrite 注入 Benchmark 语境 | P0 |
| 小辣椒和什么最和适 | RR=1.0，weak_top1_score | RR=0.2，late_hit_rank_5 | typo 改写过度，加入“食物”假设 | P0/P2 |
| 4008888518是哪里的电话 | no_hit | no_hit | 数字实体检索失败 | P1 |
| 欲钱买春光灿烂打一肖 | no_hit | no_hit | 谜语 / 生肖类字面匹配失败 | P1 |
| 游戏主播七喵是男的还是女的 | weak_top1_score | weak_top1_score | 命中但置信度偏低 | P2 |

---

## 8. 优化建议

### P0：增加 Query Rewrite Guard

目标：禁止改写引入用户原 query 中不存在的系统词或评测词。

建议禁止词：

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
```

建议逻辑：

```python
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

预期收益：

- 修复 `司鱼怎么样`；
- 修复 `不备忘录详细解答`；
- 降低短 query 和模糊 query 的意图污染。

---

### P1：新增 numeric_entity 路由

目标：处理电话号码、编号、ID、长数字实体。

识别逻辑：

```python
import re

def is_numeric_entity_query(query: str) -> bool:
    return bool(re.search(r"\d{6,}", query))
```

推荐策略：

```python
{
    "strategy_name": "numeric_exact_boost",
    "use_multi_query": true,
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

### P1：新增 riddle_literal 路由

目标：处理谜语、生肖、歇后语、成语、字面匹配问题。

识别逻辑：

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

推荐策略：

```python
{
    "strategy_name": "literal_bm25_heavy",
    "use_multi_query": true,
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

### P2：短 query typo 改写保守化

目标：修复错别字，但不擅自增加领域假设。

反例：

```text
小辣椒和什么最和适
→ 小辣椒和什么食物最搭配
```

推荐改写：

```text
小辣椒和什么最合适
小辣椒和什么最搭配
小辣椒和什么一起合适
```

原则：

- 可以修错别字；
- 可以补全轻量同义词；
- 不要引入“食物”“种植”“药用”等没有证据的领域词；
- 短 query 必须保留原 query 作为一路召回。

---

### P2：拆分 low_quality_cases 类型

当前 `low_quality_cases` 混合了三类问题：

- `no_hit`：真正失败；
- `late_hit_rank_x`：命中靠后；
- `weak_top1_score`：命中但分数低。

建议日志结构拆分为：

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

分类逻辑：

```python
if reciprocal_rank == 0:
    failed_cases.append(case)
elif first_hit_rank and first_hit_rank > 1:
    late_hit_cases.append(case)
elif top1_score < 0.35:
    weak_confidence_cases.append(case)
```

这样后续调优可以更明确：

- `failed_cases` 优先看召回策略；
- `late_hit_cases` 优先看 rerank / rewrite；
- `weak_confidence_cases` 优先看 scoring / threshold / parent 上下文。

---

## 9. 当前不建议优先做的事情

### 9.1 不建议优先更换 embedding

当前两版 Recall@5 都是 0.990，说明绝大多数正确结果已经进入前 5。问题主要集中在特殊 query 类型和 rewrite 污染，而不是整体 embedding 能力不足。

### 9.2 不建议盲目加大 top_k

加大 top_k 可能会让更多噪声进入生成上下文。当前 Recall@5 已经很高，继续增大 top_k 的边际收益较低。

### 9.3 不建议继续无差别叠加 Multi-Query

Multi-Query 对 why / compare / summary 类问题可能有帮助，但对短 query、数字实体、谜语类 query 可能带来语义漂移。应该按问题类型精细控制，而不是全局开启。

---

## 10. 后续验证计划

### Step 1：先修 Query Rewrite Guard

修复后重新跑：

```text
case_count = 200
```

重点观察：

- `司鱼怎么样` 是否不再改写出 `T2Retrieval` / `Benchmark`；
- `不备忘录详细解答` 是否不再注入系统词；
- `小辣椒和什么最和适` 是否只做保守 typo 修正。

目标：

| 指标 | 目标 |
|---|---:|
| MRR | >= 0.9762 |
| NDCG@3 | >= 0.9813 |
| rewrite 污染样本 | 0 |

---

### Step 2：新增 numeric_entity 与 riddle_literal 路由

修复后重新跑：

```text
case_count = 200
```

重点观察：

- `4008888518是哪里的电话` 是否从 `no_hit` 变为 hit；
- `欲钱买春光灿烂打一肖` 是否从 `no_hit` 变为 hit。

目标：

| 指标 | 目标 |
|---|---:|
| no_hit 数量 | 2 → 0 或 1 |
| Recall@5 | > 0.990 |
| Recall@1 | >= 0.965 |

---

### Step 3：扩样到 500 条

最终建议跑：

```text
case_count = 500
```

重点看：

| 指标 | 目标 |
|---|---:|
| MRR | >= 0.976 |
| Recall@1 | >= 0.970 |
| Recall@3 | >= 0.990 |
| Recall@5 | >= 0.995 |
| no_hit 数量 | 明显下降 |
| rewrite 污染数量 | 0 |
| low_quality_cases | 按类别下降 |

---

## 11. 最终判断

本次对比说明：

1. **原始版整体检索指标略优**，优化版没有带来 Recall / MRR / NDCG 的整体提升。
2. **优化版显著减少 low_quality_cases**，说明增强链路对置信度和上下文质量有价值。
3. **Query Rewrite 是当前最大风险点**，尤其会向短 query 或模糊 query 注入 Benchmark / T2Retrieval 等系统词。
4. **数字实体和谜语类 query 是当前共同短板**，需要独立路由，而不是依赖通用语义检索。
5. 下一轮优化不应继续盲目叠加能力，而应围绕失败样本做专项修复。

最终建议：

> 保留优化版中的 BM25、Rerank、Parent 回填和动态路由框架，但立即收紧 Query Rewrite；同时补充 numeric_entity 与 riddle_literal 两类特殊路由。修复后再用相同 200 条样本回归，确认 MRR 至少回到原始版 0.9762 以上，再扩展到 500 条样本验证稳定性。
