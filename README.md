# LearnOS

一个面向个人学习、文档问答、自动复习和面试准备的多 Agent 学习系统。

项目由原有旅行场景多 Agent 项目改造而来，当前已经完成第一阶段的大部分核心链路：上传资料、自动创建学习会话、中文摘要/知识点提取、会话内 RAG 问答、自动复习注入、中文前端工作台。

## 项目定位

这个项目的目标不是做一个普通聊天机器人，而是做一个：

1. 能接收当天学习资料的学习型 Agent。
2. 能围绕资料持续问答的 RAG 系统。
3. 能自动带出历史知识点复习的记忆型 Agent。
4. 能作为 Agent 应用开发岗位求职作品展示工程能力的项目。

## 当前能力

当前版本已经支持：

1. 创建学习会话。
2. 根据上传资料自动生成会话名称、学习主题和学习目标。
3. 上传 `pdf/docx/txt/md` 学习资料。
4. 自动完成文档解析、切分、向量入库。
5. 自动生成中文摘要、关键词、知识点和面试要点。
6. 将知识点转成复习项。
7. 在当前学习会话内基于资料进行问答。
8. 回答时展示来源片段。
9. 自动注入相关复习内容。
10. 使用中文 Streamlit 界面完成基本学习工作流。

## 项目结构

```text
LearnOS/
├─ Zero_RAG/
│  ├─ Server.py                  # FastAPI 后端入口
│  ├─ Client.py                  # Streamlit 中文前端
│  ├─ agent_engine.py            # 多 Agent 路由与调度
│  ├─ base_data_model.py         # Agent 工具 schema
│  ├─ chat_history_service.py    # 聊天历史与线程状态
│  ├─ llm_generator.py           # LLM 调用封装
│  └─ RAG/
│     ├─ document_loader.py      # 文档解析
│     ├─ text_splitter.py        # 文本切分
│     ├─ vector_store.py         # Chroma 封装
│     ├─ hybrid_retriever.py     # 混合检索
│     └─ config.py               # RAG 配置
├─ services/
│  ├─ study_session_service.py   # 学习会话数据服务
│  ├─ document_service.py        # 文档与摘要数据服务
│  ├─ review_service.py          # 复习项数据服务
│  └─ summary_service.py         # 中文摘要与会话信息推断
├─ tools/
│  ├─ init_db.py                 # SQLite 初始化
│  ├─ learning_ingest_tools.py   # 学习资料处理工具
│  ├─ review_tools.py            # 复习工具
│  ├─ quiz_tools.py              # 测验工具
│  ├─ planner_tools.py           # 计划工具
│  └─ retriever_vector.py        # 本地学习文档检索工具
├─ 产品功能清单与优先级.md
├─ 数据库表结构设计.md
├─ 多Agent架构图.md
├─ 第一阶段实施任务拆解.md
└─ 学习辅助Agent项目改造计划.md
```

## 技术栈

1. Python
2. FastAPI
3. Streamlit
4. SQLite
5. ChromaDB
6. BM25 + 向量检索 + Rerank
7. OpenAI 兼容接口 / DashScope 兼容调用

## 核心流程

### 1. 上传资料

1. 用户上传 `pdf/docx/txt/md` 文件。
2. 后端解析文档文本。
3. 文本切分后写入 Chroma。
4. 系统生成中文摘要、关键词、知识点和面试要点。
5. 系统自动根据资料生成：
   - 会话名称
   - 学习主题
   - 学习目标

### 2. 学习问答

1. 用户围绕当前学习会话提问。
2. 系统只在当前会话内做 RAG 检索。
3. 回答时附带来源片段。
4. 系统从 `review_items` 中挑选相关旧知识，生成轻量复习提醒。

### 3. 多 Agent 协作

当前多 Agent 结构包括：

1. `primary_assistant`
   负责主控、理解意图和组织回答。
2. `learning_ingest_assistant`
   负责资料准备、知识点提取。
3. `review_assistant`
   负责复习提醒和复习项构建。
4. `quiz_assistant`
   负责测验生成和自测评分。
5. `planner_assistant`
   负责学习计划拆解。

## 启动方式

### 1. 安装依赖

当前仓库还没有完整依赖清单，至少需要这些核心依赖：

```bash
pip install fastapi uvicorn streamlit requests pydantic chromadb PyPDF2 python-docx jieba rank-bm25 openai
```

如果你使用 DashScope / 阿里兼容接口，还需要准备相应 SDK 与环境变量。

### 2. 配置环境变量

至少需要配置：

```bash
DASHSCOPE_API_KEY=your_api_key
BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

如果后续启用联网搜索，还需要：

```bash
TAVILY_API_KEY=your_api_key
```

### 3. 启动后端

在项目根目录执行：

```bash
python Zero_RAG/Server.py
```

默认监听：

```text
http://127.0.0.1:8000
```

### 4. 启动前端

在项目根目录执行：

```bash
streamlit run Zero_RAG/Client.py
```

## 当前数据说明

项目当前使用两个本地 SQLite 文件：

1. `study_agent.sqlite3`
   学习会话、文档、摘要、知识点、复习项。
2. `chat_history.sqlite3`
   聊天历史和线程状态。

向量数据默认保存在：

```text
Zero_RAG/chroma_db
```

## 当前已完成功能

### 已完成

1. 旅行场景工具、schema、prompt 已替换为学习场景。
2. 前端中文界面已经完成。
3. 学习会话支持刷新后继续查看。
4. 上传资料后自动创建会话信息，不再需要手填。
5. 文档解析、切分、入库已接通。
6. 会话内 RAG 问答已接通。
7. 自动复习注入已接通。
8. 中文摘要与知识点生成已接入。

### 部分完成

1. 多 Agent 工具层已经改成学习场景，但前端主问答路径当前主要走 `/chat`，不是完全依赖 `/agent_chat`。
2. 测验和计划工具文件已经有基础实现，但还没有完整接进前端工作流。

### 未完成

1. 完整依赖清单和环境安装说明还未沉淀为正式 `requirements.txt`。
2. 测验模式、错题本、学习计划页面还没正式做完。
3. 学习报告、评测闭环、执行日志、知识图谱还未实现。
4. 已经导入过的旧英文摘要数据不会自动回填成中文，需要重新导入生成。

## 适合展示的亮点

这个项目当前比较适合在简历或面试里强调：

1. 从旧多 Agent 项目迁移到学习场景的能力。
2. RAG + 结构化记忆 + 自动复习的组合设计。
3. 会话内隔离检索和复习上下文注入。
4. Agent 架构、数据库设计、产品规划、代码落地都在同一项目里体现。

## 后续建议

优先建议继续做这几件事：

1. 补 `requirements.txt`
2. 接通测验模式
3. 接通学习计划页面
4. 加一份演示脚本
5. 补项目截图和最终项目说明文档

## 相关文档

1. [学习辅助Agent项目改造计划.md](学习辅助Agent项目改造计划.md)
2. [产品功能清单与优先级.md](产品功能清单与优先级.md)
3. [数据库表结构设计.md](数据库表结构设计.md)
4. [多Agent架构图.md](多Agent架构图.md)
5. [第一阶段实施任务拆解.md](第一阶段实施任务拆解.md)
