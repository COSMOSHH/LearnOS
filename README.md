# LearnOS

面向个人学习、资料问答、自动复习和面试准备的学习型 Agent 系统。

当前版本已经完成第一阶段的核心闭环：学习资料导入、自动生成学习会话、会话内 RAG 问答、复习提醒注入，以及中文工作台。近期新增了网页学习资料导入、学习会话删除、单机单用户固定会话视图等能力，已经可以稳定支撑本地演示。

## 当前能力

1. 支持导入 `pdf / docx / txt / md` 文件。
2. 支持导入单个网页链接，并抽取正文后进入同一套学习入库链路。
3. 支持“导入到当前会话”和“根据资料新建学习会话”两种模式。
4. 支持自动生成学习会话名称、学习主题、学习目标。
5. 支持资料切块、向量入库、会话内检索问答。
6. 支持自动生成摘要、关键词、知识点、面试要点。
7. 支持把知识点转成复习项，并在问答时注入复习提醒。
8. 支持查看历史学习会话，并删除当前学习会话。
9. 支持流式回答、来源展示、复习提醒展示。

## 适合演示的亮点

1. 从本地文件学习助手升级成“多源学习资料工作台”。
2. 同一条入库链路同时支持文件与网页资料，扩展成本低。
3. 会话级隔离的 RAG 检索，回答更聚焦当前学习主题。
4. 问答与复习项联动，体现“学习系统”而不只是“聊天系统”。
5. 保留多 Agent 架构基础，便于后续继续接测验、规划、评测模块。

## 项目结构

```text
LearnOS/
├── Zero_RAG/
│   ├── Client.py                # Streamlit 前端
│   ├── Server.py                # FastAPI 后端
│   ├── chat_history_service.py  # 聊天历史与线程状态
│   ├── llm_generator.py         # LLM 调用封装
│   └── RAG/
│       ├── document_loader.py   # 文件解析
│       ├── hybrid_retriever.py  # 检索融合
│       ├── text_splitter.py     # 文本切块
│       └── vector_store.py      # Chroma 封装
├── services/
│   ├── document_service.py      # 文档、摘要、知识点存储
│   ├── review_service.py        # 复习项与复习上下文
│   ├── study_session_service.py # 学习会话 CRUD
│   ├── summary_service.py       # 摘要与会话元信息生成
│   └── webpage_service.py       # 网页正文抽取
├── tools/
│   └── init_db.py               # SQLite 初始化
├── README.md
├── 阶段性完成报告.md
└── 产品功能清单与优先级.md
```

## 启动方式

### 1. 安装依赖

当前仓库还没有正式整理 `requirements.txt`，本地运行至少需要这些核心依赖：

```bash
pip install fastapi uvicorn streamlit requests pydantic chromadb PyPDF2 python-docx jieba rank-bm25 openai beautifulsoup4
```

### 2. 配置环境变量

至少需要：

```bash
DASHSCOPE_API_KEY=your_api_key
BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 3. 启动后端

```bash
python Zero_RAG/Server.py
```

默认地址：

```text
http://127.0.0.1:8000
```

### 4. 启动前端

```bash
streamlit run Zero_RAG/Client.py
```

## 数据存储说明

1. `study_agent.sqlite3`
   学习会话、文档、摘要、知识点、复习项。
2. `chat_history.sqlite3`
   聊天历史和线程状态。
3. `Zero_RAG/chroma_db`
   向量数据。
4. `uploaded_study_materials`
   上传资料的本地副本。

## 当前默认用户策略

当前前端为了避免“新开页面看不到旧会话”，默认固定为单机单用户模式。

位置：

1. [Zero_RAG/Client.py](Zero_RAG/Client.py) 中的 `DEFAULT_USER_ID`
2. [Zero_RAG/Client.py](Zero_RAG/Client.py) 中的 `ensure_user_id()`

如果要改回用户隔离：

1. 把 `DEFAULT_USER_ID` 改回动态生成策略。
2. 把 `ensure_user_id()` 改回“优先读取 query param，没有则生成 UUID”。
3. 后端接口不需要改，后端本来就是按 `user_id` 做学习会话隔离的。

## 当前交互说明

1. 学习会话在侧边栏选择。
2. 文件和网页导入都支持：
   `新建学习会话`
   `导入当前会话`
3. 提问框按回车提交。
4. 回答为流式输出。
5. “会话概览”位于“学习问答”上方，可折叠。
6. 支持删除当前会话。

## 当前已完成 / 未完成

### 已完成

1. 文件导入闭环。
2. 网页导入闭环。
3. 学习会话自动创建与管理。
4. 会话内 RAG 问答。
5. 复习提醒注入。
6. 会话删除能力。
7. 单机单用户稳定会话视图。

### 部分完成

1. 多 Agent 架构基础还在，但前台主链路仍以 `/chat` 为主。
2. 复习系统已经可用，但还没有完整的复习调度页面。
3. 测验、学习计划等工具文件存在，但还没有接进前端主工作流。

### 未完成

1. `requirements.txt` 及正式安装文档。
2. 测验模块前后端闭环。
3. 学习计划模块前后端闭环。
4. 每日学习报告与评测闭环。
5. 更完整的工程化测试与日志观测。

## 相关文档

1. [阶段性完成报告.md](阶段性完成报告.md)
2. [产品功能清单与优先级.md](产品功能清单与优先级.md)
3. [第一阶段实施任务拆解.md](第一阶段实施任务拆解.md)
4. [数据库表结构设计.md](数据库表结构设计.md)
5. [学习辅助Agent项目改造计划.md](学习辅助Agent项目改造计划.md)
