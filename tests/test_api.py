import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
ZERO_RAG_DIR = ROOT_DIR / "Zero_RAG"
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))
if str(ZERO_RAG_DIR) not in sys.path:
    sys.path.append(str(ZERO_RAG_DIR))

try:
    from fastapi.testclient import TestClient
    from Zero_RAG import Server, chat_history_service

    FASTAPI_TESTS_AVAILABLE = True
    FASTAPI_TESTS_REASON = ""
except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
    TestClient = None
    Server = None
    chat_history_service = None
    FASTAPI_TESTS_AVAILABLE = False
    FASTAPI_TESTS_REASON = f"fastapi test dependencies are unavailable: {exc}"

from services import document_service, observability_service, plan_service, quiz_service, review_service, study_session_service
from tools import init_db


@unittest.skipUnless(FASTAPI_TESTS_AVAILABLE, FASTAPI_TESTS_REASON)
class ApiTests(unittest.TestCase):
    def setUp(self):
        study_fd, study_path = tempfile.mkstemp(prefix="test_api_study_", suffix=".sqlite3", dir=".")
        chat_fd, chat_path = tempfile.mkstemp(prefix="test_api_chat_", suffix=".sqlite3", dir=".")
        os.close(study_fd)
        os.close(chat_fd)
        self.study_db = Path(study_path).resolve()
        self.chat_db = Path(chat_path).resolve()
        self.upload_dir = Path(tempfile.mkdtemp(prefix="test_api_upload_", dir=".")).resolve()

        init_db.DB_PATH = self.study_db
        document_service.DB_PATH = self.study_db
        observability_service.DB_PATH = self.study_db
        plan_service.DB_PATH = self.study_db
        quiz_service.DB_PATH = self.study_db
        review_service.DB_PATH = self.study_db
        study_session_service.DB_PATH = self.study_db
        chat_history_service.DB_FILE = self.chat_db

        Server.UPLOAD_DIR = self.upload_dir
        Server.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        Server.llm_generator = None
        init_db.init_study_db()
        chat_history_service.init_db()

        self.patchers = [
            patch.object(Server.vector_store, "add_documents", return_value=None),
            patch.object(Server.vector_store, "delete_documents", return_value=None),
            patch("Zero_RAG.Server.summarize_text", return_value={
                "short_summary": "测试摘要",
                "keywords": ["测试", "网页"],
                "interview_takeaways": ["测试要点"],
                "knowledge_points": [
                    {"title": "知识点A", "description": "描述A", "importance": 3, "difficulty": 3, "metadata": {}}
                ],
            }),
            patch("Zero_RAG.Server.infer_session_metadata", return_value={
                "session_name": "批量网页测试会话",
                "topic": "网页测试",
                "goal": "验证批量导入",
                "tags": ["web"],
            }),
        ]
        for patcher in self.patchers:
            patcher.start()

        self.client = TestClient(Server.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for patcher in reversed(self.patchers):
            patcher.stop()
        for path in [self.study_db, self.chat_db]:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            for child in self.upload_dir.glob("**/*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            for child in sorted(self.upload_dir.glob("**/*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
            self.upload_dir.rmdir()
        except Exception:
            pass

    def test_plan_endpoints_support_persistence_and_completion(self):
        create_resp = self.client.post(
            "/study_sessions",
            json={"user_id": "u1", "session_name": "计划测试", "topic": "计划", "goal": "验证计划", "tags": []},
        )
        session_id = create_resp.json()["session"]["id"]

        generate_resp = self.client.post(f"/study_sessions/{session_id}/plan", json={"user_id": "u1"})
        self.assertEqual(generate_resp.status_code, 200)
        plan = generate_resp.json()["plan"]
        self.assertIsNotNone(plan)
        self.assertGreaterEqual(len(plan["action_steps"]), 1)

        first_item_id = plan["action_steps"][0]["id"]
        patch_resp = self.client.patch(f"/study_plans/items/{first_item_id}", json={"is_completed": True})
        self.assertEqual(patch_resp.status_code, 200)
        self.assertTrue(patch_resp.json()["item"]["is_completed"])

        load_resp = self.client.get(f"/study_sessions/{session_id}/plan", params={"only_incomplete": "true"})
        self.assertEqual(load_resp.status_code, 200)
        loaded_plan = load_resp.json()["plan"]
        self.assertTrue(all(not item["is_completed"] for group in ["today_focus", "priority_review", "next_questions", "action_steps"] for item in loaded_plan[group]))

    def test_quiz_attempt_endpoint_creates_feedback_and_review_items(self):
        session = study_session_service.create_study_session("u1", "测验测试", topic="锁", goal="测试")
        quiz_set_id = quiz_service.create_quiz_set(session["id"], "测试测验", 2, "medium")
        quiz_service.save_quiz_questions(
            quiz_set_id,
            [
                {
                    "question_index": 1,
                    "question_type": "short_answer",
                    "question_text": "什么是行锁？",
                    "reference_answer": "行锁锁住索引记录。",
                    "scoring_rubric": "定义、作用、例子。",
                },
                {
                    "question_index": 2,
                    "question_type": "short_answer",
                    "question_text": "什么是间隙锁？",
                    "reference_answer": "间隙锁锁住索引区间。",
                    "scoring_rubric": "定义、作用、例子。",
                },
            ],
        )

        resp = self.client.post(
            f"/study_sessions/{session['id']}/quiz_attempts",
            json={"user_id": "u1", "quiz_set_id": quiz_set_id, "answers": ["", "只答了一点"]},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("result", payload)
        self.assertGreaterEqual(payload["review_items_created"], 1)

        wrong_resp = self.client.get("/wrong_questions", params={"user_id": "u1", "session_id": session["id"], "max_score": 3})
        self.assertEqual(wrong_resp.status_code, 200)
        self.assertGreaterEqual(len(wrong_resp.json()["items"]), 1)

    def test_batch_webpage_endpoint_imports_multiple_pages(self):
        session = study_session_service.create_study_session("u1", "网页批量测试", topic="网页", goal="测试")
        batch_payload = {
            "source_url": "https://example.com/docs/",
            "site_name": "example.com",
            "pages": [
                {"title": "文章A", "text": "正文A", "source_url": "https://example.com/docs/a.html", "site_name": "example.com"},
                {"title": "文章B", "text": "正文B", "source_url": "https://example.com/docs/b.html", "site_name": "example.com"},
            ],
        }

        with patch("Zero_RAG.Server.fetch_webpage_batch", return_value=batch_payload):
            resp = self.client.post(
                f"/study_sessions/{session['id']}/webpages/batch",
                json={"user_id": "u1", "url": "https://example.com/docs/", "max_pages": 2},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["imported_count"], 2)

        conn = sqlite3.connect(self.study_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM study_documents WHERE session_id = ?", (session["id"],))
        self.assertEqual(cursor.fetchone()[0], 2)
        conn.close()

    def test_review_queue_wrong_retry_and_events_endpoints(self):
        session = study_session_service.create_study_session("u1", "复习调度测试", topic="锁", goal="巩固")
        quiz_set_id = quiz_service.create_quiz_set(session["id"], "测试测验", 1, "medium")
        quiz_service.save_quiz_questions(
            quiz_set_id,
            [
                {
                    "question_index": 1,
                    "question_type": "single_choice",
                    "question_text": "哪个概念最符合锁住索引记录？",
                    "reference_answer": "行锁",
                    "scoring_rubric": "选出最匹配概念。",
                    "metadata": {"options": ["表锁", "行锁", "间隙锁", "意向锁"], "correct_answer": "行锁"},
                }
            ],
        )
        submit_resp = self.client.post(
            f"/study_sessions/{session['id']}/quiz_attempts",
            json={"user_id": "u1", "quiz_set_id": quiz_set_id, "answers": ["表锁"]},
        )
        self.assertEqual(submit_resp.status_code, 200)

        queue_resp = self.client.get("/review_queue", params={"user_id": "u1", "session_id": session["id"], "limit": 5})
        self.assertEqual(queue_resp.status_code, 200)
        self.assertGreaterEqual(len(queue_resp.json()["items"]), 1)

        wrong_resp = self.client.get("/wrong_questions", params={"user_id": "u1", "session_id": session["id"]})
        item_id = wrong_resp.json()["items"][0]["id"]
        retry_resp = self.client.post(f"/wrong_questions/{item_id}/retry", json={"user_id": "u1", "answer": "行锁"})
        self.assertEqual(retry_resp.status_code, 200)
        self.assertEqual(retry_resp.json()["status"], "mastered")

        event_resp = self.client.get("/system/events", params={"session_id": session["id"], "limit": 10})
        self.assertEqual(event_resp.status_code, 200)
        self.assertGreaterEqual(len(event_resp.json()["events"]), 1)

    def test_delete_session_endpoint_cascades_related_data(self):
        session = study_session_service.create_study_session("u1", "删除测试", topic="删除", goal="验证")
        document_id = document_service.create_document(
            session_id=session["id"],
            title="测试文档",
            file_name="a.txt",
            file_path="a.txt",
            file_type=".txt",
            file_size=10,
            content_hash="abc",
        )
        document_service.save_document_chunks(document_id, ["chunk"], ["id1"], {"session_id": session["id"]})
        document_service.save_document_summary(document_id, "short_summary", "摘要")
        kp_ids = document_service.save_knowledge_points(
            session["id"],
            document_id,
            [{"title": "知识点", "description": "描述", "importance": 3, "difficulty": 3}],
        )
        review_service.create_review_items_from_knowledge_points(
            user_id="u1",
            session_id=session["id"],
            knowledge_points=[{"title": "知识点", "description": "描述", "importance": 3, "difficulty": 3}],
            knowledge_point_ids=kp_ids,
        )
        plan = plan_service.generate_learning_plan(session, [], [], [], None, llm_generator=None)
        plan_service.save_learning_plan(session["id"], "u1", plan)
        quiz_set_id = quiz_service.create_quiz_set(session["id"], "测验", 1, "medium")
        quiz_service.save_quiz_questions(
            quiz_set_id,
            [{"question_index": 1, "question_type": "short_answer", "question_text": "Q", "reference_answer": "A", "scoring_rubric": "R"}],
        )
        quiz_service.save_quiz_attempt(
            quiz_set_id,
            session["id"],
            "u1",
            ["A"],
            {"total_score": 4, "overall_feedback": "ok", "item_feedback": [{"question_index": 1, "score": 4, "max_score": 5, "feedback": "ok", "suggestion": ""}]},
        )
        chat_history_service.save_chat_history("u1", "q", "a", session_id=session["id"])

        resp = self.client.request("DELETE", f"/study_sessions/{session['id']}", json={"user_id": "u1"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])

        conn = sqlite3.connect(self.study_db)
        cursor = conn.cursor()
        for table_name in [
            "study_sessions",
            "study_documents",
            "document_chunks",
            "document_summaries",
            "knowledge_points",
            "review_items",
            "study_plans",
            "study_plan_items",
            "quiz_sets",
            "quiz_questions",
            "quiz_attempts",
            "wrong_question_attempts",
            "event_logs",
        ]:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            self.assertEqual(cursor.fetchone()[0], 0, table_name)
        conn.close()


if __name__ == "__main__":
    unittest.main()
