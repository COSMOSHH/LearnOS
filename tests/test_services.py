import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Zero_RAG import chat_history_service
from services import document_service, observability_service, plan_service, quiz_service, report_service, review_service, study_session_service, webpage_service
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

    def test_quiz_generation_grading_and_low_score_review_sink(self):
        session = {"session_name": "MySQL 锁", "topic": "锁机制", "goal": "理解原理"}
        knowledge_points = [
            {"title": "行锁", "description": "锁住索引项，减少并发冲突。"},
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

        result = quiz_service.grade_quiz_attempt(bundle["questions"], ["", "只写了一点"], llm_generator=None)
        self.assertIn("item_feedback", result)

        created_topics = review_service.create_review_items_from_quiz_feedback(
            user_id="u1",
            session_id=1,
            questions=bundle["questions"],
            result=result,
        )
        self.assertGreaterEqual(len(created_topics), 1)

        conn = sqlite3.connect(self.study_db)
        cursor = conn.cursor()
        cursor.execute("SELECT topic, source_type, priority_score FROM review_items")
        rows = cursor.fetchall()
        conn.close()

        self.assertTrue(any(row[1] == "quiz_feedback" for row in rows))
        self.assertTrue(all(row[2] >= 6 for row in rows))

    def test_review_queue_and_wrong_question_retry_update_status(self):
        session = study_session_service.create_study_session("u1", "错题重练测试", topic="锁", goal="掌握")
        quiz_set_id = quiz_service.create_quiz_set(session["id"], "测试测验", 2, "medium")
        quiz_service.save_quiz_questions(
            quiz_set_id,
            [
                {
                    "question_index": 1,
                    "question_type": "single_choice",
                    "question_text": "哪个概念更符合“锁住索引记录”？",
                    "reference_answer": "行锁",
                    "scoring_rubric": "选出最匹配的概念。",
                    "metadata": {"options": ["表锁", "行锁", "间隙锁", "意向锁"], "correct_answer": "行锁"},
                },
                {
                    "question_index": 2,
                    "question_type": "fill_blank",
                    "question_text": "填空：防止幻读常见会用到 ____。",
                    "reference_answer": "间隙锁",
                    "scoring_rubric": "填出关键术语。",
                    "metadata": {"blank_answers": ["间隙锁"]},
                },
            ],
        )
        stored = quiz_service.get_quiz_set_with_questions(quiz_set_id)
        result = quiz_service.grade_quiz_attempt(stored["questions"], ["表锁", ""], llm_generator=None)
        created_topics = review_service.create_review_items_from_quiz_feedback("u1", session["id"], stored["questions"], result)
        self.assertEqual(len(created_topics), 2)

        queue = review_service.list_review_queue("u1", session_id=session["id"], limit=5, due_only=False)
        self.assertGreaterEqual(len(queue), 2)

        wrong_items = review_service.get_quiz_feedback_items("u1", session_id=session["id"])
        retry_target = next(item for item in wrong_items if item["question_type"] == "single_choice")
        retry_result = review_service.retry_wrong_question(retry_target["id"], "u1", "行锁", llm_generator=None)
        self.assertEqual(retry_result["status"], "mastered")
        self.assertGreaterEqual(retry_result["result"]["score"], 5.0)

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
                "feedback_text": "需要补上幻读相关解释。",
            },
            llm_generator=None,
        )

        self.assertIn("学习报告", report["title"])
        self.assertTrue(any("最近一次测验总分为" in item for item in report["progress_snapshot"]))
        self.assertGreaterEqual(len(report["next_actions"]), 1)

    def test_delete_session_cascade_cleanup(self):
        session = study_session_service.create_study_session("u1", "测试会话", topic="测试", goal="测试")
        document_id = document_service.create_document(
            session_id=session["id"],
            title="测试文档",
            file_name="test.txt",
            file_path="/tmp/test.txt",
            file_type=".txt",
            file_size=10,
            content_hash="abc",
        )
        document_service.save_document_chunks(document_id, ["chunk1"], ["doc1"], {"session_id": session["id"]})
        document_service.save_document_summary(document_id, "short_summary", "测试摘要")
        knowledge_point_ids = document_service.save_knowledge_points(
            session["id"],
            document_id,
            [{"title": "知识点A", "description": "描述A", "importance": 3, "difficulty": 3}],
        )
        review_service.create_review_items_from_knowledge_points(
            user_id="u1",
            session_id=session["id"],
            knowledge_points=[{"title": "知识点A", "description": "描述A", "importance": 3, "difficulty": 3}],
            knowledge_point_ids=knowledge_point_ids,
        )

        quiz_set_id = quiz_service.create_quiz_set(session["id"], "测试测验", 1, "medium")
        quiz_service.save_quiz_questions(
            quiz_set_id,
            [
                {
                    "question_index": 1,
                    "question_type": "short_answer",
                    "question_text": "什么是锁？",
                    "reference_answer": "锁用于并发控制。",
                    "scoring_rubric": "定义、作用、例子。",
                }
            ],
        )
        quiz_service.save_quiz_attempt(
            quiz_set_id=quiz_set_id,
            session_id=session["id"],
            user_id="u1",
            answers=["锁用于并发控制。"],
            result={
                "total_score": 4,
                "overall_feedback": "不错",
                "item_feedback": [{"question_index": 1, "score": 4, "max_score": 5, "feedback": "较好", "suggestion": ""}],
            },
        )

        chat_history_service.save_chat_history("u1", "什么是锁？", "锁用于并发控制。", session_id=session["id"])
        state = chat_history_service.ThreadState(f"thread_u1_{session['id']}")
        chat_history_service.save_thread_state(state)

        deleted = study_session_service.delete_study_session(session["id"], "u1")
        chat_history_service.delete_session_history("u1", session["id"])
        self.assertTrue(deleted)

        study_conn = sqlite3.connect(self.study_db)
        study_cursor = study_conn.cursor()
        for table_name in [
            "study_sessions",
            "study_documents",
            "document_chunks",
            "document_summaries",
            "knowledge_points",
            "review_items",
            "quiz_sets",
            "quiz_questions",
            "quiz_attempts",
            "wrong_question_attempts",
            "event_logs",
        ]:
            study_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            self.assertEqual(study_cursor.fetchone()[0], 0, table_name)
        study_conn.close()

        chat_conn = sqlite3.connect(self.chat_db)
        chat_cursor = chat_conn.cursor()
        chat_cursor.execute("SELECT COUNT(*) FROM chat_history")
        self.assertEqual(chat_cursor.fetchone()[0], 0)
        chat_cursor.execute("SELECT COUNT(*) FROM thread_state")
        self.assertEqual(chat_cursor.fetchone()[0], 0)
        chat_conn.close()


if __name__ == "__main__":
    unittest.main()
