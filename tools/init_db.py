import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "study_agent.sqlite3"
ROOT_DIR = DB_PATH.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.db import connect_study_db, is_mysql


def _ensure_column(cursor, table_name: str, column_name: str, column_definition: str):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if column_name not in existing_columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def init_study_db() -> str:
    if is_mysql():
        return _init_mysql_study_db()
    return _init_sqlite_study_db()


def _init_sqlite_study_db() -> str:
    """Initialize the SQLite schema required for the study-agent MVP."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_name TEXT NOT NULL,
            topic TEXT,
            goal TEXT,
            status TEXT DEFAULT 'active',
            session_date DATE,
            tags_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS study_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            file_name TEXT,
            file_path TEXT,
            file_type TEXT,
            file_size INTEGER,
            content_hash TEXT,
            ingest_status TEXT DEFAULT 'pending',
            source_type TEXT DEFAULT 'upload',
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            token_count INTEGER,
            chroma_doc_id TEXT,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            summary_type TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            extra_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            document_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            category TEXT,
            importance INTEGER DEFAULT 3,
            difficulty INTEGER DEFAULT 3,
            source_chunk_id INTEGER,
            status TEXT DEFAULT 'active',
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id INTEGER,
            knowledge_point_id INTEGER,
            topic TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_type TEXT,
            review_status TEXT DEFAULT 'new',
            confidence_score INTEGER DEFAULT 3,
            error_count INTEGER DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            last_reviewed_at DATETIME,
            next_review_at DATETIME,
            priority_score REAL DEFAULT 0,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS quiz_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            question_count INTEGER DEFAULT 3,
            difficulty TEXT DEFAULT 'medium',
            source_type TEXT DEFAULT 'generated',
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_set_id INTEGER NOT NULL,
            question_index INTEGER NOT NULL,
            question_type TEXT,
            question_text TEXT NOT NULL,
            reference_answer TEXT,
            scoring_rubric TEXT,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_set_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            answers_json TEXT,
            result_json TEXT,
            total_score REAL DEFAULT 0,
            feedback_text TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS study_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            overview TEXT,
            source_type TEXT DEFAULT 'generated',
            status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS study_plan_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_text TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            is_completed INTEGER DEFAULT 0,
            priority_score REAL DEFAULT 0,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS wrong_question_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_item_id INTEGER NOT NULL,
            session_id INTEGER,
            user_id TEXT NOT NULL,
            question_type TEXT,
            answer_json TEXT,
            result_json TEXT,
            total_score REAL DEFAULT 0,
            status TEXT DEFAULT 'retrying',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS event_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            status TEXT DEFAULT 'success',
            session_id INTEGER,
            user_id TEXT,
            duration_ms INTEGER,
            message TEXT,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            user_id TEXT NOT NULL,
            source_type TEXT DEFAULT 'chat',
            query_text TEXT NOT NULL,
            answer_text TEXT NOT NULL,
            overall_score REAL DEFAULT 0,
            accuracy_score REAL DEFAULT 0,
            grounding_score REAL DEFAULT 0,
            completeness_score REAL DEFAULT 0,
            clarity_score REAL DEFAULT 0,
            feedback_text TEXT,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS interview_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            difficulty TEXT DEFAULT 'medium',
            total_rounds INTEGER DEFAULT 3,
            current_round INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            intro_text TEXT,
            summary_text TEXT,
            metadata_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS interview_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_session_id INTEGER NOT NULL,
            round_index INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            answer_text TEXT,
            ideal_answer TEXT,
            follow_up_question TEXT,
            feedback_text TEXT,
            score REAL DEFAULT 0,
            metadata_json TEXT,
            asked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            answered_at DATETIME
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            session_id INTEGER,
            user_id TEXT,
            status TEXT DEFAULT 'running',
            title TEXT,
            input_summary TEXT,
            output_summary TEXT,
            duration_ms INTEGER,
            metadata_json TEXT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_run_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            step_name TEXT NOT NULL,
            step_status TEXT DEFAULT 'success',
            duration_ms INTEGER,
            message TEXT,
            metadata_json TEXT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_quality_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            user_id TEXT,
            query_text TEXT NOT NULL,
            rewritten_query TEXT,
            question_type TEXT,
            reason TEXT,
            reciprocal_rank REAL DEFAULT 0,
            top1_json TEXT,
            metrics_json TEXT,
            source_run_id INTEGER,
            status TEXT DEFAULT 'open',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON study_sessions(user_id, session_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_session_id ON study_documents(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_session_id ON knowledge_points(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_review_user_next_review ON review_items(user_id, next_review_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_sets_session_id ON quiz_sets(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_questions_set_id ON quiz_questions(quiz_set_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_quiz_attempts_session_user ON quiz_attempts(session_id, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_study_plans_session_id ON study_plans(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_study_plan_items_plan_id ON study_plan_items(plan_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wrong_question_attempts_review_item ON wrong_question_attempts(review_item_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_logs_created_at ON event_logs(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_logs_session_id ON event_logs(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_answer_evaluations_session_id ON answer_evaluations(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interview_sessions_session_id ON interview_sessions(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interview_turns_session_id ON interview_turns(interview_session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_session_id ON agent_runs(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run_id ON agent_run_steps(run_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_quality_samples_session_id ON rag_quality_samples(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_quality_samples_type ON rag_quality_samples(question_type, reason)")

    _ensure_column(cursor, "study_documents", "source_type", "TEXT DEFAULT 'upload'")
    _ensure_column(cursor, "study_documents", "metadata_json", "TEXT")
    _ensure_column(cursor, "review_items", "mastery_level", "REAL DEFAULT 0")
    _ensure_column(cursor, "review_items", "last_score", "REAL DEFAULT 0")
    _ensure_column(cursor, "review_items", "best_score", "REAL DEFAULT 0")
    _ensure_column(cursor, "review_items", "retry_count", "INTEGER DEFAULT 0")
    _ensure_column(cursor, "study_plan_items", "priority_score", "REAL DEFAULT 0")

    conn.commit()
    conn.close()
    return str(DB_PATH)


def _init_mysql_study_db() -> str:
    """Initialize the MySQL schema used by LearnOS."""

    conn = connect_study_db(DB_PATH)
    cursor = conn.cursor()

    table_sqls = [
        """
        CREATE TABLE IF NOT EXISTS study_sessions (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(128) NOT NULL,
            session_name VARCHAR(255) NOT NULL,
            topic LONGTEXT,
            goal LONGTEXT,
            status VARCHAR(32) DEFAULT 'active',
            session_date DATE,
            tags_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS study_documents (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id BIGINT NOT NULL,
            title VARCHAR(512) NOT NULL,
            file_name LONGTEXT,
            file_path LONGTEXT,
            file_type VARCHAR(64),
            file_size BIGINT,
            content_hash VARCHAR(128),
            ingest_status VARCHAR(32) DEFAULT 'pending',
            source_type VARCHAR(64) DEFAULT 'upload',
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            document_id BIGINT NOT NULL,
            chunk_index INT NOT NULL,
            chunk_text LONGTEXT NOT NULL,
            token_count INT,
            chroma_doc_id VARCHAR(255),
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS document_summaries (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            document_id BIGINT NOT NULL,
            summary_type VARCHAR(64) NOT NULL,
            summary_text LONGTEXT NOT NULL,
            extra_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS knowledge_points (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id BIGINT NOT NULL,
            document_id BIGINT,
            title VARCHAR(512) NOT NULL,
            description LONGTEXT,
            category VARCHAR(128),
            importance INT DEFAULT 3,
            difficulty INT DEFAULT 3,
            source_chunk_id BIGINT,
            status VARCHAR(32) DEFAULT 'active',
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS review_items (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(128) NOT NULL,
            session_id BIGINT,
            knowledge_point_id BIGINT,
            topic LONGTEXT NOT NULL,
            summary LONGTEXT NOT NULL,
            source_type VARCHAR(64),
            review_status VARCHAR(32) DEFAULT 'new',
            confidence_score INT DEFAULT 3,
            error_count INT DEFAULT 0,
            review_count INT DEFAULT 0,
            last_reviewed_at DATETIME,
            next_review_at DATETIME,
            priority_score DOUBLE DEFAULT 0,
            mastery_level DOUBLE DEFAULT 0,
            last_score DOUBLE DEFAULT 0,
            best_score DOUBLE DEFAULT 0,
            retry_count INT DEFAULT 0,
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS quiz_sets (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id BIGINT NOT NULL,
            title VARCHAR(512) NOT NULL,
            question_count INT DEFAULT 3,
            difficulty VARCHAR(64) DEFAULT 'medium',
            source_type VARCHAR(64) DEFAULT 'generated',
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            quiz_set_id BIGINT NOT NULL,
            question_index INT NOT NULL,
            question_type VARCHAR(64),
            question_text LONGTEXT NOT NULL,
            reference_answer LONGTEXT,
            scoring_rubric LONGTEXT,
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            quiz_set_id BIGINT NOT NULL,
            session_id BIGINT NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            answers_json LONGTEXT,
            result_json LONGTEXT,
            total_score DOUBLE DEFAULT 0,
            feedback_text LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS study_plans (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id BIGINT NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            title VARCHAR(512) NOT NULL,
            overview LONGTEXT,
            source_type VARCHAR(64) DEFAULT 'generated',
            status VARCHAR(32) DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS study_plan_items (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            plan_id BIGINT NOT NULL,
            item_type VARCHAR(64) NOT NULL,
            item_text LONGTEXT NOT NULL,
            sort_order INT DEFAULT 0,
            is_completed TINYINT DEFAULT 0,
            priority_score DOUBLE DEFAULT 0,
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS wrong_question_attempts (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            review_item_id BIGINT NOT NULL,
            session_id BIGINT,
            user_id VARCHAR(128) NOT NULL,
            question_type VARCHAR(64),
            answer_json LONGTEXT,
            result_json LONGTEXT,
            total_score DOUBLE DEFAULT 0,
            status VARCHAR(32) DEFAULT 'retrying',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS event_logs (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(128) NOT NULL,
            status VARCHAR(32) DEFAULT 'success',
            session_id BIGINT,
            user_id VARCHAR(128),
            duration_ms BIGINT,
            message LONGTEXT,
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS answer_evaluations (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id BIGINT,
            user_id VARCHAR(128) NOT NULL,
            source_type VARCHAR(64) DEFAULT 'chat',
            query_text LONGTEXT NOT NULL,
            answer_text LONGTEXT NOT NULL,
            overall_score DOUBLE DEFAULT 0,
            accuracy_score DOUBLE DEFAULT 0,
            grounding_score DOUBLE DEFAULT 0,
            completeness_score DOUBLE DEFAULT 0,
            clarity_score DOUBLE DEFAULT 0,
            feedback_text LONGTEXT,
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS interview_sessions (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id BIGINT NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            title VARCHAR(512) NOT NULL,
            difficulty VARCHAR(64) DEFAULT 'medium',
            total_rounds INT DEFAULT 3,
            current_round INT DEFAULT 1,
            status VARCHAR(32) DEFAULT 'active',
            intro_text LONGTEXT,
            summary_text LONGTEXT,
            metadata_json LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS interview_turns (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            interview_session_id BIGINT NOT NULL,
            round_index INT NOT NULL,
            question_text LONGTEXT NOT NULL,
            answer_text LONGTEXT,
            ideal_answer LONGTEXT,
            follow_up_question LONGTEXT,
            feedback_text LONGTEXT,
            score DOUBLE DEFAULT 0,
            metadata_json LONGTEXT,
            asked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            answered_at DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            run_type VARCHAR(128) NOT NULL,
            session_id BIGINT,
            user_id VARCHAR(128),
            status VARCHAR(32) DEFAULT 'running',
            title VARCHAR(512),
            input_summary LONGTEXT,
            output_summary LONGTEXT,
            duration_ms BIGINT,
            metadata_json LONGTEXT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_run_steps (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            run_id BIGINT NOT NULL,
            step_name VARCHAR(128) NOT NULL,
            step_status VARCHAR(32) DEFAULT 'success',
            duration_ms BIGINT,
            message LONGTEXT,
            metadata_json LONGTEXT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS rag_quality_samples (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id BIGINT NOT NULL,
            user_id VARCHAR(128),
            query_text LONGTEXT NOT NULL,
            rewritten_query LONGTEXT,
            question_type VARCHAR(64),
            reason VARCHAR(64),
            reciprocal_rank DOUBLE DEFAULT 0,
            top1_json LONGTEXT,
            metrics_json LONGTEXT,
            source_run_id BIGINT,
            status VARCHAR(32) DEFAULT 'open',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]

    for sql in table_sqls:
        cursor.execute(sql)

    indexes = [
        ("idx_sessions_user_date", "study_sessions", "user_id, session_date"),
        ("idx_documents_session_id", "study_documents", "session_id"),
        ("idx_chunks_document_id", "document_chunks", "document_id"),
        ("idx_knowledge_session_id", "knowledge_points", "session_id"),
        ("idx_review_user_next_review", "review_items", "user_id, next_review_at"),
        ("idx_quiz_sets_session_id", "quiz_sets", "session_id"),
        ("idx_quiz_questions_set_id", "quiz_questions", "quiz_set_id"),
        ("idx_quiz_attempts_session_user", "quiz_attempts", "session_id, user_id"),
        ("idx_study_plans_session_id", "study_plans", "session_id"),
        ("idx_study_plan_items_plan_id", "study_plan_items", "plan_id"),
        ("idx_wrong_question_attempts_review_item", "wrong_question_attempts", "review_item_id"),
        ("idx_event_logs_created_at", "event_logs", "created_at"),
        ("idx_event_logs_session_id", "event_logs", "session_id"),
        ("idx_answer_evaluations_session_id", "answer_evaluations", "session_id"),
        ("idx_interview_sessions_session_id", "interview_sessions", "session_id"),
        ("idx_interview_turns_session_id", "interview_turns", "interview_session_id"),
        ("idx_agent_runs_session_id", "agent_runs", "session_id"),
        ("idx_agent_run_steps_run_id", "agent_run_steps", "run_id"),
        ("idx_rag_quality_samples_session_id", "rag_quality_samples", "session_id"),
        ("idx_rag_quality_samples_type", "rag_quality_samples", "question_type, reason"),
    ]
    for index_name, table_name, columns in indexes:
        cursor.execute(f"SHOW INDEX FROM {table_name} WHERE Key_name = ?", (index_name,))
        if cursor.fetchone() is None:
            cursor.execute(f"CREATE INDEX {index_name} ON {table_name}({columns})")

    conn.commit()
    conn.close()
    return "mysql://rag_user@127.0.0.1:3307/rag_db"


if __name__ == "__main__":
    print(init_study_db())
