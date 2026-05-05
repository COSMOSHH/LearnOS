# SQLite 切换 MySQL 迁移计划

> 当前环境：Windows + Docker  
> 目标数据库：MySQL 8  
> 当前数据库：SQLite `study_agent.sqlite3`  
> 当前向量库：ChromaDB，暂不迁移  
> 更新时间：2026-04-30

## 当前迁移进度

状态：第一版已完成。

已完成内容：

- 已接入 Docker MySQL：`127.0.0.1:3307`，数据库 `rag_db`，用户 `rag_user`。
- 已新增统一数据库连接层：`services/db.py`。
- 已支持 `DB_TYPE=mysql/sqlite` 双模式，默认连接 MySQL，可通过 `DB_TYPE=sqlite` 回退 SQLite。
- 已将核心 service 层数据库连接切换到统一 DB adapter。
- 已将 `tools/init_db.py` 改造为同时支持 SQLite 与 MySQL 建表。
- 已新增 `.env.example`，记录 MySQL 与 SQLite 回退配置。
- 已新增 `tools/migrate_sqlite_to_mysql.py`，支持 SQLite 到 MySQL 数据迁移、`--clear` 清空导入、`--dry-run` 预检查。
- 已将 `study_agent.sqlite3` 与 `chat_history.sqlite3` 中的历史数据迁移到 MySQL。
- 已完成 MySQL 最小 CRUD 烟测：新建会话、写入聊天历史、读取历史、删除会话。
- 已更新 `requirements.txt`，新增 `pymysql`。

本次迁移结果：

```text
study_sessions: 1
study_documents: 1
document_chunks: 8
document_summaries: 3
knowledge_points: 5
review_items: 7
quiz_sets: 1
quiz_questions: 3
quiz_attempts: 1
study_plans: 2
study_plan_items: 16
wrong_question_attempts: 0
event_logs: 5
answer_evaluations: 1
interview_sessions: 0
interview_turns: 0
agent_runs: 1
agent_run_steps: 4
rag_quality_samples: 0
chat_history: 1
thread_state: 0
```

已验证命令：

```bash
python tools/init_db.py
python tools/migrate_sqlite_to_mysql.py --clear
D:\app_tools\anaconda3\envs\langchain\python.exe tools\init_db.py
D:\app_tools\anaconda3\envs\langchain\python.exe tools\migrate_sqlite_to_mysql.py --dry-run
```

当前限制：

- ChromaDB 仍保持本地向量库，不在本次 MySQL 迁移范围内。
- 当前未引入 SQLAlchemy/Alembic，仍采用轻量 DB adapter。
- 当前测试环境缺少 `chromadb` 时，完整 `pytest` 会在收集阶段失败；这属于当前 shell Python 环境依赖问题，不是 MySQL 迁移逻辑错误。

## 迁移后问题修复记录

### MySQL datetime JSON 序列化问题

现象：

```text
[backend streaming error: Object of type datetime is not JSON serializable]
```

原因：

SQLite 返回的时间字段通常是字符串，而 PyMySQL 会把 `DATETIME/DATE` 字段返回为 Python `datetime/date` 对象。原有流式响应和部分接口直接调用 `json.dumps`，因此在 MySQL 环境下触发序列化错误。

修复：

- 在 `services/db.py` 的 MySQL cursor adapter 中统一处理查询结果。
- `fetchone/fetchall` 返回行数据时，将 `datetime` 转为 `YYYY-MM-DD HH:MM:SS` 风格字符串，将 `date` 转为 `YYYY-MM-DD`。
- 保持上层 service 行为接近原 SQLite 版本，避免每个接口单独处理时间字段。

验证：

```bash
D:\app_tools\anaconda3\envs\langchain\python.exe - <<'PY'
import json
from services.study_session_service import list_study_sessions
from services.observability_service import list_recent_runs, list_recent_events

json.dumps(list_study_sessions("default_user"), ensure_ascii=False)
json.dumps(list_recent_runs(limit=3), ensure_ascii=False)
json.dumps(list_recent_events(limit=3), ensure_ascii=False)
print("json-ok")
PY
```

## 迁移目标

将 LearnOS 当前基于 SQLite 的业务数据存储切换为 Docker 部署的 MySQL，同时保持现有学习链路稳定可用。

需要保留的核心功能包括：

- 学习会话
- 文档与网页资料入库
- 文档 chunk、摘要、知识点
- RAG 问答与聊天历史
- 测验生成、测验提交与评分
- 错题沉淀与错题重练
- 复习项与复习调度
- 学习计划
- 学习报告
- RAG 评测、低质 Query 样本与质量看板
- 会话删除与级联清理

ChromaDB 作为向量库暂时保持独立，不纳入本次 MySQL 迁移范围。

## 总体策略

本次迁移建议采用“先兼容运行，再迁移数据，再 Docker 化固化”的方式推进。

不建议第一版直接重构为 SQLAlchemy ORM。当前项目已经有较多 service 层函数，直接引入 ORM 会扩大改动面，也容易影响 RAG、测验、计划和质量看板等链路。

推荐方案：

- 使用 Docker Compose 启动 MySQL 8。
- 使用 `pymysql` 作为第一版 MySQL 驱动。
- 新增统一数据库连接层，逐步替换散落在 service 中的 `sqlite3.connect`。
- 保留 `DB_TYPE=sqlite/mysql` 双模式配置，方便回滚。
- 新增 SQLite 到 MySQL 的迁移脚本。
- 完成后再考虑是否升级为 SQLAlchemy 或 Alembic。

## 迁移检查清单

### 1. 盘点 SQLite 使用点

- [ ] 搜索所有 `sqlite3.connect`。
- [ ] 搜索所有 `DB_PATH`。
- [ ] 搜索所有直接建表 SQL。
- [ ] 搜索所有 SQLite 特有 SQL 语法。
- [ ] 盘点所有需要迁移的数据表。

重点文件大概率包括：

- `tools/init_db.py`
- `chat_history_service.py`
- `services/study_session_service.py`
- `services/document_service.py`
- `services/review_service.py`
- `services/quiz_service.py`
- `services/plan_service.py`
- `services/report_service.py`
- `services/rag_quality_service.py`

### 2. 新增 MySQL Docker 配置

- [ ] 新增 `docker-compose.yml`。
- [ ] 使用 MySQL 8 镜像。
- [ ] 设置数据库名、用户名、密码。
- [ ] 开启 `utf8mb4` 字符集。
- [ ] 配置本地端口映射。
- [ ] 配置 Docker volume 持久化。

建议配置：

```yaml
services:
  mysql:
    image: mysql:8.0
    container_name: learnos-mysql
    restart: unless-stopped
    environment:
      MYSQL_DATABASE: learnos
      MYSQL_USER: learnos
      MYSQL_PASSWORD: learnos_password
      MYSQL_ROOT_PASSWORD: root_password
      TZ: Asia/Shanghai
    ports:
      - "3306:3306"
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
    volumes:
      - learnos_mysql_data:/var/lib/mysql

volumes:
  learnos_mysql_data:
```

### 3. 新增环境变量配置

- [ ] 新增 `.env.example`。
- [ ] 增加数据库类型配置。
- [ ] 增加 MySQL 连接参数。
- [ ] 在 `config.py` 或统一 DB 层读取配置。

建议配置：

```env
DB_TYPE=mysql

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=learnos
MYSQL_USER=learnos
MYSQL_PASSWORD=learnos_password

SQLITE_DB_PATH=study_agent.sqlite3
```

### 4. 新增统一数据库连接层

- [ ] 新增 `services/db.py`。
- [ ] 封装 `get_connection()`。
- [ ] 根据 `DB_TYPE` 返回 SQLite 或 MySQL 连接。
- [ ] 封装参数占位符差异。
- [ ] 尽量保持 service 层调用方式稳定。

需要处理的差异：

| 项目 | SQLite | MySQL / PyMySQL |
| --- | --- | --- |
| 参数占位符 | `?` | `%s` |
| 自增主键 | `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT AUTO_INCREMENT PRIMARY KEY` |
| JSON 字段 | `TEXT` | `JSON` 或 `LONGTEXT` |
| 时间函数 | `CURRENT_TIMESTAMP` | `CURRENT_TIMESTAMP` |
| upsert | `INSERT OR REPLACE` / `ON CONFLICT` | `ON DUPLICATE KEY UPDATE` |
| last insert id | `cursor.lastrowid` | `cursor.lastrowid` |

### 5. 改造数据库初始化脚本

- [ ] 将 `tools/init_db.py` 改为根据 `DB_TYPE` 选择建表 SQL。
- [ ] 编写 MySQL 版本 DDL。
- [ ] 保留 SQLite 初始化能力作为回滚方案。
- [ ] 明确每张表的主键、自增、索引和外键关系。
- [ ] 对会话删除相关表增加级联清理策略。

MySQL DDL 注意点：

- `INTEGER PRIMARY KEY AUTOINCREMENT` 改为 `BIGINT AUTO_INCREMENT PRIMARY KEY`。
- 大文本字段使用 `LONGTEXT`。
- JSON 字段可以先使用 `LONGTEXT`，降低兼容风险。
- 所有中文内容表建议使用 `utf8mb4_unicode_ci`。
- 需要索引的字段包括 `user_id`、`session_id`、`document_id`、`knowledge_point_id`、`created_at`。

### 6. 改造 service 层 SQL

- [ ] 替换所有 `sqlite3.connect` 为统一 DB 连接。
- [ ] 替换 SQL 占位符。
- [ ] 检查所有插入、更新、删除、查询语句。
- [ ] 检查事务提交与连接关闭。
- [ ] 检查 JSON 字段序列化与反序列化。
- [ ] 检查时间字段行为。

重点 service：

- 学习会话服务
- 文档服务
- 聊天历史服务
- 复习服务
- 测验服务
- 学习计划服务
- 学习报告服务
- RAG 质量服务

### 7. 处理 SQLite 专属语法

- [ ] `INSERT OR REPLACE`
- [ ] `ON CONFLICT`
- [ ] `strftime`
- [ ] `datetime('now')`
- [ ] `LIMIT ? OFFSET ?`
- [ ] `LIKE` 大小写行为
- [ ] `json_extract` 或类似 SQLite JSON 函数
- [ ] `PRAGMA`

替换建议：

| SQLite 写法 | MySQL 替代 |
| --- | --- |
| `INSERT OR REPLACE` | `REPLACE INTO` 或 `ON DUPLICATE KEY UPDATE` |
| `ON CONFLICT` | `ON DUPLICATE KEY UPDATE` |
| `strftime(...)` | `DATE_FORMAT(...)` |
| `datetime('now')` | `NOW()` |
| `PRAGMA foreign_keys = ON` | MySQL 默认使用 InnoDB 外键 |

### 8. 编写 SQLite 到 MySQL 数据迁移脚本

- [ ] 新增 `tools/migrate_sqlite_to_mysql.py`。
- [ ] 从 SQLite 读取所有业务表数据。
- [ ] 按外键依赖顺序导入 MySQL。
- [ ] 保留原始 ID，避免历史关系断裂。
- [ ] 迁移 JSON 字段时保持字符串格式。
- [ ] 支持 dry-run。
- [ ] 支持导入前清空 MySQL 表。
- [ ] 输出每张表迁移数量。

建议迁移顺序：

1. 学习会话表
2. 文档表
3. 文档 chunk 表
4. 文档摘要表
5. 知识点表
6. 聊天历史表
7. 复习项表
8. 测验表
9. 测验题目表
10. 测验提交与评分表
11. 错题相关表
12. 学习计划表
13. 学习报告表
14. RAG 运行与评测表
15. RAG 低质样本表

实际顺序以当前 `tools/init_db.py` 中表结构为准。

### 9. 验证核心功能链路

- [ ] 启动 MySQL Docker 容器。
- [ ] 初始化 MySQL 表结构。
- [ ] 启动 FastAPI 后端。
- [ ] 启动 Streamlit 前端。
- [ ] 新建空白学习会话。
- [ ] 上传文件资料。
- [ ] 导入单页网页。
- [ ] 批量导入站内网页。
- [ ] 进行 RAG 问答。
- [ ] 查看来源引用。
- [ ] 生成测验。
- [ ] 提交测验并评分。
- [ ] 进入错题本。
- [ ] 进行错题重练。
- [ ] 查看复习项。
- [ ] 生成学习计划。
- [ ] 标记计划完成。
- [ ] 生成学习报告。
- [ ] 查看 RAG 质量看板。
- [ ] 删除学习会话并确认相关数据清理。

### 10. 更新测试

- [ ] 保留 SQLite 单元测试模式。
- [ ] 新增 MySQL 集成测试模式。
- [ ] 测试不应默认强依赖 Docker。
- [ ] 对核心 service 增加 MySQL 连接测试。
- [ ] 对接口层增加 MySQL 环境下的 smoke test。

建议测试策略：

- 普通单测：继续使用 SQLite 或临时库。
- 集成测试：手动设置 `DB_TYPE=mysql` 后执行。
- CI 后续再接 MySQL service。

### 11. 更新文档

- [ ] 更新 `README.md`。
- [ ] 更新 `requirements.txt`。
- [ ] 更新 `docs/architecture/数据库表结构设计.md`。
- [ ] 更新 `docs/reports/阶段性完成报告.md`。
- [ ] 新增 MySQL Docker 启动说明。
- [ ] 新增 SQLite 数据迁移说明。
- [ ] 标注如何回滚到 SQLite。

### 12. 保留回滚方案

- [ ] 保留 `study_agent.sqlite3` 备份。
- [ ] 保留 SQLite 初始化逻辑。
- [ ] 保留 `DB_TYPE=sqlite`。
- [ ] MySQL 切换失败时可以快速回滚。
- [ ] 迁移前导出数据库备份。

## 推荐实施顺序

1. 新增 `docker-compose.yml` 与 `.env.example`。
2. 新增统一数据库连接层 `services/db.py`。
3. 改造 `tools/init_db.py`，支持 MySQL 建表。
4. 改造 service 层 SQL。
5. 启动 MySQL 空库并验证初始化。
6. 编写 SQLite 到 MySQL 迁移脚本。
7. 执行数据迁移。
8. 跑核心功能链路验证。
9. 补充 MySQL 集成测试。
10. 更新 README 与数据库文档。

## 风险点

### 1. SQL 占位符差异

SQLite 使用 `?`，PyMySQL 使用 `%s`。如果遗漏替换，会导致运行时报 SQL 参数错误。

### 2. 自增主键差异

SQLite 的 `INTEGER PRIMARY KEY AUTOINCREMENT` 需要改成 MySQL 的 `BIGINT AUTO_INCREMENT PRIMARY KEY`。

### 3. JSON 字段兼容性

当前很多字段使用 JSON 字符串。如果直接改成 MySQL `JSON` 类型，需要确保写入内容一定合法。第一版建议先用 `LONGTEXT`，降低迁移风险。

### 4. 级联删除

SQLite 中可能依赖应用层删除逻辑。MySQL 如果引入外键，需要确认删除会话时是否会自动清理所有关联数据。

### 5. 字符集问题

学习资料和网页内容包含大量中文，MySQL 必须使用 `utf8mb4`。

### 6. 时间字段行为

SQLite 与 MySQL 的当前时间函数和时区处理不同，需要统一使用 `CURRENT_TIMESTAMP` 或应用层时间。

### 7. 测试污染

MySQL 测试如果直接连开发库，容易污染数据。集成测试建议使用单独数据库，如 `learnos_test`。

### 8. ChromaDB 与 MySQL 数据一致性

业务元数据进入 MySQL，但向量仍在 ChromaDB。删除文档、删除会话时，需要继续同步清理向量数据或至少避免检索到孤儿向量。

## 第一版验收标准

- [ ] Docker MySQL 可以一键启动。
- [ ] 后端可以通过 MySQL 正常启动。
- [ ] 所有业务表可以在 MySQL 中初始化。
- [ ] 旧 SQLite 数据可以迁移到 MySQL。
- [ ] 文件导入、网页导入、RAG 问答、测验、错题、复习、计划、报告、质量看板均可正常使用。
- [ ] 删除会话后，MySQL 关联数据不会残留明显脏数据。
- [ ] 项目仍保留切回 SQLite 的能力。

## 暂不纳入第一版的内容

- 不迁移 ChromaDB。
- 不引入 SQLAlchemy ORM。
- 不引入 Alembic 迁移体系。
- 不做多租户权限系统。
- 不做生产环境高可用 MySQL。
- 不做云数据库部署。

这些内容可以在 MySQL 第一版稳定后再逐步推进。
