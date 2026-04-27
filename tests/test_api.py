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

from services import (
    document_service,
    evaluation_service,
    interview_service,
    observability_service,
    plan_service,
    quiz_service,
    review_service,
    study_session_service,
)
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
        evaluation_service.DB_PATH = self.study_db
        interview_service.DB_PATH = self.study_db
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

    def test_interview_evaluation_and_agent_runs_endpoints(self):
        session = study_session_service.create_study_session("u1", "模拟面试接口测试", topic="锁", goal="练习表达")
        document_id = document_service.create_document(
            session_id=session["id"],
            title="面试资料",
            file_name="a.txt",
            file_path="a.txt",
            file_type=".txt",
            file_size=10,
            content_hash="interview-api",
        )
        document_service.save_knowledge_points(
            session["id"],
            document_id,
            [
                {"title": "行锁", "description": "锁住索引记录。", "importance": 3, "difficulty": 3},
                {"title": "间隙锁", "description": "锁住索引区间。", "importance": 4, "difficulty": 4},
            ],
        )
        document_service.save_document_summary(document_id, "short_summary", "本轮聚焦锁机制。")

        start_resp = self.client.post(
            f"/study_sessions/{session['id']}/interview_sessions",
            json={"user_id": "u1", "total_rounds": 2, "difficulty": "medium"},
        )
        self.assertEqual(start_resp.status_code, 200)
        start_payload = start_resp.json()
        interview_session = start_payload["interview_session"]
        self.assertIsNotNone(interview_session)

        answer_resp = self.client.post(
            f"/interview_sessions/{interview_session['id']}/answer",
            json={"user_id": "u1", "answer": "行锁是锁住索引记录的机制，用于减少并发冲突。"},
        )
        self.assertEqual(answer_resp.status_code, 200)
        self.assertIn("result", answer_resp.json())

        evaluation_resp = self.client.get(
            f"/study_sessions/{session['id']}/evaluations",
            params={"source_type": "interview", "limit": 10},
        )
        self.assertEqual(evaluation_resp.status_code, 200)
        self.assertGreaterEqual(evaluation_resp.json()["summary"]["count"], 1)

        runs_resp = self.client.get(
            f"/study_sessions/{session['id']}/agent_runs",
            params={"limit": 10},
        )
        self.assertEqual(runs_resp.status_code, 200)
        run_types = {item["run_type"] for item in runs_resp.json()["runs"]}
        self.assertTrue({"interview.start", "interview.answer"} & run_types)

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
        evaluation_service.save_answer_evaluation(
            session_id=session["id"],
            user_id="u1",
            query_text="q",
            answer_text="a",
            evaluation=evaluation_service.evaluate_answer("q", "a", llm_generator=None),
            source_type="chat",
        )
        interview_session_id = interview_service.create_interview_session(
            session_id=session["id"],
            user_id="u1",
            title="模拟面试",
            difficulty="medium",
            total_rounds=1,
            intro_text="请回答",
            questions=[{"round_index": 1, "question_text": "Q", "ideal_answer": "A", "focus": "核心概念"}],
        )
        interview_service.submit_interview_answer(interview_session_id, "u1", "A", llm_generator=None)
        run_id = observability_service.create_run(
            run_type="study_chat",
            session_id=session["id"],
            user_id="u1",
            title="学习问答",
            input_summary="q",
        )
        observability_service.add_run_step(run_id, "retrieve", duration_ms=10)
        observability_service.finish_run(run_id, output_summary="a", duration_ms=20)

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
            "answer_evaluations",
            "interview_sessions",
            "interview_turns",
            "agent_runs",
            "agent_run_steps",
            "event_logs",
        ]:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            self.assertEqual(cursor.fetchone()[0], 0, table_name)
        conn.close()

    def test_rag_eval_template_and_evaluate_endpoints(self):
        session = study_session_service.create_study_session("u1", "RAG评测测试", topic="锁", goal="评测")
        document_service.create_document(
            session_id=session["id"],
            title="MySQL 锁",
            file_name="lock.md",
            file_path="docs/lock.md",
            file_type=".md",
            file_size=10,
            content_hash="rag-eval",
        )

        template_resp = self.client.get(f"/study_sessions/{session['id']}/rag/eval_dataset_template")
        self.assertEqual(template_resp.status_code, 200)
        self.assertGreaterEqual(template_resp.json()["case_count"], 1)
        auto_cases_resp = self.client.get(
            f"/study_sessions/{session['id']}/rag/eval_cases",
            params={"limit": 5},
        )
        self.assertEqual(auto_cases_resp.status_code, 200)
        self.assertGreaterEqual(auto_cases_resp.json()["case_count"], 1)

        class FakeRetriever:
            def retrieve_with_debug(self, query, queries=None):
                return (
                    [
                        {
                            "document": "行锁用于锁住索引记录。",
                            "metadata": {
                                "source": "docs/lock.md",
                                "document_title": "MySQL 锁",
                                "section_title": "行锁",
                                "chunk_index": 0,
                            },
                            "score": 0.9,
                        }
                    ],
                    {"debug": "ok"},
                )

        with patch("Zero_RAG.Server._build_session_retriever", return_value=(FakeRetriever(), [{"chunk_text": "x", "metadata": {}}])):
            eval_resp = self.client.post(
                f"/study_sessions/{session['id']}/rag/evaluate",
                json={
                    "user_id": "u1",
                    "top_k": 3,
                    "low_quality_mrr_threshold": 0.5,
                    "cases": [
                        {
                            "query": "什么是行锁",
                            "relevant_sources": ["docs/lock.md"],
                            "relevant_titles": ["MySQL 锁"],
                            "relevant_keywords": ["行锁"],
                        }
                    ],
                },
            )

        self.assertEqual(eval_resp.status_code, 200)
        payload = eval_resp.json()
        self.assertEqual(payload["case_count"], 1)
        self.assertGreaterEqual(payload["metrics"]["mrr"], 1.0)


if __name__ == "__main__":
    unittest.main()
