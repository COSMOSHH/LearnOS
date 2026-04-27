import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Zero_RAG import chat_history_service
from Zero_RAG.RAG.text_splitter import SemanticTextSplitter
from services import (
    context_service,
    document_service,
    evaluation_service,
    interview_service,
    observability_service,
    plan_service,
    query_service,
    rag_eval_service,
    quiz_service,
    report_service,
    review_service,
    study_session_service,
    webpage_service,
)
from tools import init_db


class FakeResponse:
    def __init__(self, url: str, html: str, encoding: str = "utf-8", status_code: int = 200):
        self.url = url
        self.content = html.encode(encoding)
        self.encoding = encoding
        self.apparent_encoding = encoding
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        study_fd, study_path = tempfile.mkstemp(prefix="test_study_agent_", suffix=".sqlite3", dir=".")
        chat_fd, chat_path = tempfile.mkstemp(prefix="test_chat_history_", suffix=".sqlite3", dir=".")
        os.close(study_fd)
        os.close(chat_fd)
        self.study_db = Path(study_path).resolve()
        self.chat_db = Path(chat_path).resolve()

        init_db.DB_PATH = self.study_db
        document_service.DB_PATH = self.study_db
        evaluation_service.DB_PATH = self.study_db
        interview_service.DB_PATH = self.study_db
        observability_service.DB_PATH = self.study_db
        plan_service.DB_PATH = self.study_db
        quiz_service.DB_PATH = self.study_db
        review_service.DB_PATH = self.study_db
        study_session_service.DB_PATH = self.study_db
        chat_history_service.DB_FILE = self.chat_db

        init_db.init_study_db()
        chat_history_service.init_db()

    def tearDown(self):
        for path in [self.study_db, self.chat_db]:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_fetch_webpage_content_extracts_article_text(self):
        html = """
        <html>
        <head><title>测试文章</title></head>
        <body>
          <nav>导航</nav>
          <article>
            <h1>MySQL 锁机制</h1>
            <p>这是正文第一段。</p>
            <p>这是正文第二段。</p>
          </article>
        </body>
        </html>
        """
        with patch.object(webpage_service.requests, "get", return_value=FakeResponse("https://example.com/a.html", html)):
            page = webpage_service.fetch_webpage_content("https://example.com/a.html")

        self.assertEqual(page["title"], "MySQL 锁机制")
        self.assertIn("这是正文第一段。", page["text"])
        self.assertNotIn("导航", page["text"])
        self.assertTrue(page.get("sections"))

    def test_fetch_webpage_batch_discovers_same_site_articles(self):
        directory_html = """
        <html><body>
          <a href="/mysql/a.html">文章A</a>
          <a href="/mysql/b.html">文章B</a>
          <a href="https://other.com/x.html">站外链接</a>
        </body></html>
        """
        article_a = """
        <html><body><article><h1>文章A</h1><p>内容A</p></article></body></html>
        """
        article_b = """
        <html><body><article><h1>文章B</h1><p>内容B</p></article></body></html>
        """

        def fake_get(url, headers=None, timeout=20):
            if url == "https://example.com/mysql/":
                return FakeResponse(url, directory_html)
            if url == "https://example.com/mysql/a.html":
                return FakeResponse(url, article_a)
            if url == "https://example.com/mysql/b.html":
                return FakeResponse(url, article_b)
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(webpage_service.requests, "get", side_effect=fake_get):
            batch = webpage_service.fetch_webpage_batch("https://example.com/mysql/", max_pages=2)

        self.assertEqual(len(batch["pages"]), 2)
        self.assertEqual({item["title"] for item in batch["pages"]}, {"文章A", "文章B"})
        self.assertTrue(all(page.get("sections") for page in batch["pages"]))

    def test_query_rewrite_heading_chunking_and_multi_query(self):
        rewritten = query_service.rewrite_query(
            "这个有什么区别？",
            history=[{"role": "user", "content": "redo log 和 undo log 是什么"}],
            session_context="MySQL 锁与事务",
            llm_generator=None,
        )
        self.assertIn("补充追问", rewritten["rewritten_query"])

        expanded = query_service.expand_query_to_multi_queries(
            original_query="redo log 和 undo log 有什么区别？为什么要同时存在？",
            rewritten_query="redo log 和 undo log 的区别和关系",
            session_context="MySQL 事务与日志",
            llm_generator=None,
        )
        self.assertGreaterEqual(len(expanded["queries"]), 2)

        splitter = SemanticTextSplitter(chunk_size=80, chunk_overlap=10)
        chunks = splitter.split_text_with_metadata(
            "# MySQL 锁\n## 行锁\n行锁用于锁住索引记录。\n## 间隙锁\n间隙锁用于防止幻读。",
            document_title="MySQL 锁",
        )
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(any("行锁" in item["heading_path"] for item in chunks))

    def test_context_compression_dedupes_and_truncates(self):
        results, debug_payload = context_service.build_generation_context(
            [
                {
                    "document": ("redo log 用于崩溃恢复，通过 WAL 先写日志后写数据页。") * 20,
                    "metadata": {"document_title": "redo log", "chunk_index": 0},
                    "score": 0.9,
                },
                {
                    "document": ("redo log 用于崩溃恢复，通过 WAL 先写日志后写数据页。") * 20,
                    "metadata": {"document_title": "redo log", "chunk_index": 1},
                    "score": 0.8,
                },
                {
                    "document": ("undo log 用于回滚和 MVCC，用来保存事务前数据版本。") * 20,
                    "metadata": {"document_title": "undo log", "chunk_index": 2},
                    "score": 0.7,
                },
            ],
            max_context_chars=500,
            per_chunk_max_chars=180,
        )

        self.assertLessEqual(len(results), 2)
        self.assertGreaterEqual(debug_payload["deduped"], 1)
        self.assertGreaterEqual(debug_payload["truncated"], 1)
        self.assertLessEqual(debug_payload["final_context_chars"], 500)

    def test_rag_eval_metrics_and_low_quality_cases(self):
        class FakeRetriever:
            def retrieve_with_debug(self, query, queries=None):
                if "行锁" in query:
                    return (
                        [
                            {
                                "document": "行锁用于锁住索引记录。",
                                "metadata": {"source": "doc://mysql-lock", "document_title": "MySQL 锁"},
                                "score": 0.95,
                            },
                            {
                                "document": "间隙锁用于锁区间。",
                                "metadata": {"source": "doc://gap-lock", "document_title": "间隙锁"},
                                "score": 0.75,
                            },
                        ],
                        {"debug": "ok"},
                    )
                return (
                    [
                        {
                            "document": "这是不相关内容。",
                            "metadata": {"source": "doc://unrelated", "document_title": "无关文档"},
                            "score": 0.3,
                        }
                    ],
                    {"debug": "ok"},
                )

        payload = rag_eval_service.evaluate_retrieval_cases(
            FakeRetriever(),
            [
                {
                    "query": "什么是行锁",
                    "relevant_sources": ["mysql-lock"],
                    "relevant_titles": ["MySQL 锁"],
                    "relevant_keywords": ["行锁"],
                },
                {
                    "query": "请解释哈希索引在这个文档里的定义",
                    "relevant_sources": ["hash-index"],
                    "relevant_titles": ["哈希索引"],
                    "relevant_keywords": ["哈希索引"],
                },
            ],
            rewrite_query=lambda query, **kwargs: {
                "original_query": query,
                "rewritten_query": query,
                "rewrite_reason": "test",
            },
            expand_query_to_multi_queries=lambda original_query, rewritten_query, **kwargs: {
                "strategy": "single_query",
                "queries": [rewritten_query or original_query],
            },
            session_context="MySQL",
            llm_generator=None,
            top_k=3,
            low_quality_mrr_threshold=0.5,
        )

        self.assertEqual(payload["case_count"], 2)
        self.assertAlmostEqual(payload["metrics"]["recall_at"]["1"], 0.5)
        self.assertAlmostEqual(payload["metrics"]["recall_at"]["3"], 0.5)
        self.assertAlmostEqual(payload["metrics"]["mrr"], 0.5)
        self.assertGreaterEqual(len(payload["low_quality_cases"]), 1)

    def test_build_session_eval_cases_matches_topic_keywords(self):
        session = {
            "session_name": "MySQL 锁机制",
            "topic": "事务与锁",
            "goal": "理解行锁、间隙锁和 next-key lock",
        }
        documents = [
            {"title": "MySQL 是怎么加锁的？", "file_name": "lock.md", "file_path": "docs/lock.md"},
            {"title": "MySQL 日志系统", "file_name": "log.md", "file_path": "docs/log.md"},
        ]
        knowledge_points = [
            {"title": "行锁", "description": "锁住索引记录"},
            {"title": "next-key lock", "description": "记录锁加间隙锁"},
        ]

        cases = rag_eval_service.build_session_eval_cases(
            session,
            documents,
            knowledge_points,
            limit=20,
            include_template_cases=True,
        )

        self.assertGreaterEqual(len(cases), 2)
        self.assertTrue(any(case.get("case_type") == "default" for case in cases))
        self.assertTrue(any("MySQL 是怎么加锁的？" in (case.get("query") or "") for case in cases))
        self.assertTrue(any("锁" in (case.get("query") or "") or "next-key" in (case.get("query") or "") for case in cases))

    def test_quiz_generation_grading_and_wrong_question_sink(self):
        session = {"session_name": "MySQL 锁", "topic": "锁机制", "goal": "理解原理"}
        knowledge_points = [
            {"title": "行锁", "description": "锁住索引记录，减少并发冲突。"},
            {"title": "间隙锁", "description": "锁住索引区间，防止幻读。"},
        ]
        summaries = [{"summary_type": "short_summary", "summary_text": "本次学习聚焦 MySQL 锁机制。"}]

        bundle = quiz_service.generate_quiz_bundle(
            session=session,
            knowledge_points=knowledge_points,
            summaries=summaries,
            llm_generator=None,
            question_count=2,
            difficulty="medium",
        )
        self.assertEqual(len(bundle["questions"]), 2)

        session_row = study_session_service.create_study_session("u1", "MySQL 锁练习", topic="锁", goal="掌握")
        result = quiz_service.grade_quiz_attempt(bundle["questions"], ["", "只写了一点"], llm_generator=None)
        created_topics = review_service.create_review_items_from_quiz_feedback(
            user_id="u1",
            session_id=session_row["id"],
            questions=bundle["questions"],
            result=result,
        )

        self.assertGreaterEqual(len(created_topics), 1)
        queue = review_service.list_review_queue("u1", session_id=session_row["id"], limit=5, due_only=False)
        self.assertGreaterEqual(len(queue), 1)

    def test_generate_session_report_uses_quiz_result(self):
        report = report_service.generate_session_report(
            session={"session_name": "MySQL 锁", "topic": "锁", "goal": "理解锁"},
            documents=[{"id": 1}, {"id": 2}],
            summaries=[{"summary_type": "short_summary", "summary_text": "已经学习了锁的基本机制。"}],
            knowledge_points=[{"title": "行锁"}, {"title": "间隙锁"}],
            review_items=[{"topic": "测验薄弱点：间隙锁"}],
            history=[{"query": "什么是间隙锁？", "response": "..."}, {"query": "为什么能防幻读？", "response": "..."}],
            latest_quiz_attempt={
                "total_score": 6,
                "result": {"max_total_score": 10},
                "feedback_text": "需要补上幻读相关理解。",
            },
            llm_generator=None,
        )

        self.assertIn("学习报告", report["title"])
        self.assertGreaterEqual(len(report["next_actions"]), 1)
        self.assertTrue(any("最近一次测验总分" in item for item in report["progress_snapshot"]))


if __name__ == "__main__":
    unittest.main()
