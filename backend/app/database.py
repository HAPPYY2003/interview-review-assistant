from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.app.services.evidence import QUESTION_TYPES, infer_topic_title, normalize_question_type


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3.Connection, then release the file handle."""

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = """
        CREATE TABLE IF NOT EXISTS interviews (
            id TEXT PRIMARY KEY, company TEXT NOT NULL DEFAULT '', position TEXT NOT NULL DEFAULT '',
            round TEXT NOT NULL DEFAULT '', interview_date TEXT, review_goal TEXT NOT NULL DEFAULT '',
            analysis_mode TEXT NOT NULL DEFAULT 'full_context', status TEXT NOT NULL DEFAULT 'DRAFT',
            job_description TEXT NOT NULL DEFAULT '', resume_text TEXT NOT NULL DEFAULT '',
            raw_transcript TEXT NOT NULL DEFAULT '', latest_run_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS materials (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, material_type TEXT NOT NULL, filename TEXT,
            text TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', storage_path TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '',
            duration_seconds REAL, processing_status TEXT NOT NULL DEFAULT 'READY', created_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS question_cards (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, order_index INTEGER NOT NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '', question_type TEXT NOT NULL DEFAULT '其他', confidence TEXT NOT NULL DEFAULT 'medium',
            initial_diagnosis_json TEXT NOT NULL DEFAULT '[]', confirmed INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1, topic_root_id TEXT, parent_question_id TEXT,
            turn_type TEXT NOT NULL DEFAULT 'main', extracted_question TEXT NOT NULL DEFAULT '',
            extracted_answer TEXT NOT NULL DEFAULT '', edited_question TEXT NOT NULL DEFAULT '',
            edited_answer TEXT NOT NULL DEFAULT '', topic_title TEXT NOT NULL DEFAULT '',
            needs_confirmation INTEGER NOT NULL DEFAULT 0, provenance_status TEXT NOT NULL DEFAULT 'source',
            follow_up_impact TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS parse_runs (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, material_id TEXT, status TEXT NOT NULL,
            phase TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '', retry_count INTEGER NOT NULL DEFAULT 0,
            artifact_id TEXT NOT NULL DEFAULT '', error TEXT, metrics_json TEXT NOT NULL DEFAULT '{}',
            events_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE,
            FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS transcript_segments (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, material_id TEXT, order_index INTEGER NOT NULL,
            raw_text TEXT NOT NULL, normalized_text TEXT NOT NULL DEFAULT '', speaker_label TEXT NOT NULL DEFAULT '',
            speaker_role TEXT NOT NULL DEFAULT 'unknown', start_time REAL, end_time REAL,
            start_char INTEGER, end_char INTEGER, confidence REAL NOT NULL DEFAULT 0,
            speaker_confidence REAL,
            needs_confirmation INTEGER NOT NULL DEFAULT 0, excluded INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE,
            FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS question_segment_links (
            question_id TEXT NOT NULL, segment_id TEXT NOT NULL, link_role TEXT NOT NULL,
            order_index INTEGER NOT NULL, PRIMARY KEY(question_id, segment_id, link_role),
            FOREIGN KEY(question_id) REFERENCES question_cards(id) ON DELETE CASCADE,
            FOREIGN KEY(segment_id) REFERENCES transcript_segments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS review_runs (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
            hello_session_id TEXT, error TEXT, metrics_json TEXT NOT NULL DEFAULT '{}', events_json TEXT NOT NULL DEFAULT '[]',
            enable_web_verify INTEGER NOT NULL DEFAULT 0, review_mode TEXT NOT NULL DEFAULT 'full',
            plan_json TEXT NOT NULL DEFAULT '{}', checkpoint_json TEXT NOT NULL DEFAULT '{}',
            input_digest TEXT NOT NULL DEFAULT '', agent_mode TEXT NOT NULL DEFAULT 'legacy',
            degraded INTEGER NOT NULL DEFAULT 0, failure_code TEXT NOT NULL DEFAULT '',
            audit_round INTEGER NOT NULL DEFAULT 0, revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS review_stage_artifacts (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, phase TEXT NOT NULL, topic_id TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
            agent_type TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
            duration_seconds REAL NOT NULL DEFAULT 0, token_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(run_id, phase, topic_id, version),
            FOREIGN KEY(run_id) REFERENCES review_runs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS question_reviews (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, question_id TEXT NOT NULL, review_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES review_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(question_id) REFERENCES question_cards(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS evidence_refs (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, question_id TEXT, source_type TEXT NOT NULL,
            source_id TEXT NOT NULL, quote TEXT NOT NULL, locator TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES review_runs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS growth_snapshots (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, run_id TEXT NOT NULL, scores_json TEXT NOT NULL,
            weak_dimensions_json TEXT NOT NULL, action_items_json TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES review_runs(id) ON DELETE CASCADE
        );
        """
        with self._lock, self.connect() as connection:
            connection.executescript(schema)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        additions = {
            "materials": {
                "storage_path": "TEXT NOT NULL DEFAULT ''",
                "mime_type": "TEXT NOT NULL DEFAULT ''",
                "size_bytes": "INTEGER NOT NULL DEFAULT 0",
                "sha256": "TEXT NOT NULL DEFAULT ''",
                "duration_seconds": "REAL",
                "processing_status": "TEXT NOT NULL DEFAULT 'READY'",
            },
            "question_cards": {
                "topic_root_id": "TEXT",
                "parent_question_id": "TEXT",
                "turn_type": "TEXT NOT NULL DEFAULT 'main'",
                "extracted_question": "TEXT NOT NULL DEFAULT ''",
                "extracted_answer": "TEXT NOT NULL DEFAULT ''",
                "edited_question": "TEXT NOT NULL DEFAULT ''",
                "edited_answer": "TEXT NOT NULL DEFAULT ''",
                "topic_title": "TEXT NOT NULL DEFAULT ''",
                "needs_confirmation": "INTEGER NOT NULL DEFAULT 0",
                "provenance_status": "TEXT NOT NULL DEFAULT 'legacy'",
                "follow_up_impact": "TEXT NOT NULL DEFAULT ''",
            },
            "transcript_segments": {
                "speaker_confidence": "REAL",
            },
            "review_runs": {
                "review_mode": "TEXT NOT NULL DEFAULT 'full'",
                "plan_json": "TEXT NOT NULL DEFAULT '{}'",
                "checkpoint_json": "TEXT NOT NULL DEFAULT '{}'",
                "input_digest": "TEXT NOT NULL DEFAULT ''",
                "agent_mode": "TEXT NOT NULL DEFAULT 'legacy'",
                "degraded": "INTEGER NOT NULL DEFAULT 0",
                "failure_code": "TEXT NOT NULL DEFAULT ''",
                "audit_round": "INTEGER NOT NULL DEFAULT 0",
                "revision_count": "INTEGER NOT NULL DEFAULT 0",
            },
        }
        for table, columns in additions.items():
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        connection.execute(
            """UPDATE question_cards SET topic_root_id=id,
            extracted_question=CASE WHEN extracted_question='' THEN question ELSE extracted_question END,
            extracted_answer=CASE WHEN extracted_answer='' THEN answer ELSE extracted_answer END
            WHERE topic_root_id IS NULL OR topic_root_id=''"""
        )
        rows = connection.execute(
            "SELECT id, question_type, question, topic_title, turn_type FROM question_cards"
        ).fetchall()
        for row in rows:
            normalized = normalize_question_type(
                row["question_type"], f"{row['topic_title']} {row['question']}"
            )
            if normalized != row["question_type"]:
                connection.execute(
                    "UPDATE question_cards SET question_type=? WHERE id=?",
                    (normalized, row["id"]),
                )
            topic_title = str(row["topic_title"] or "").strip()
            if row["turn_type"] == "main" and topic_title in QUESTION_TYPES and topic_title != normalized:
                connection.execute(
                    "UPDATE question_cards SET topic_title=? WHERE id=?",
                    (infer_topic_title(row["question"], normalized), row["id"]),
                )
        connection.execute(
            """UPDATE review_runs SET agent_mode='legacy'
            WHERE agent_mode='fixture' AND NOT EXISTS (
                SELECT 1 FROM review_stage_artifacts a WHERE a.run_id=review_runs.id
            )"""
        )

    def create_interview(self, payload: dict[str, Any]) -> dict[str, Any]:
        interview_id = payload.get("id") or str(uuid.uuid4())
        now = utc_now()
        values = {
            "id": interview_id,
            "company": payload.get("company", ""),
            "position": payload.get("position", ""),
            "round": payload.get("round", ""),
            "interview_date": str(payload.get("interview_date") or ""),
            "review_goal": payload.get("review_goal", ""),
            "analysis_mode": payload.get("analysis_mode", "full_context"),
            "job_description": payload.get("job_description", ""),
            "resume_text": payload.get("resume_text", ""),
            "raw_transcript": payload.get("raw_transcript", ""),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO interviews(id, company, position, round, interview_date, review_goal, analysis_mode,
                status, job_description, resume_text, raw_transcript, created_at, updated_at)
                VALUES(:id,:company,:position,:round,:interview_date,:review_goal,:analysis_mode,'DRAFT',
                :job_description,:resume_text,:raw_transcript,:created_at,:updated_at)
                ON CONFLICT(id) DO UPDATE SET company=excluded.company, position=excluded.position, round=excluded.round,
                interview_date=excluded.interview_date, review_goal=excluded.review_goal, analysis_mode=excluded.analysis_mode,
                job_description=excluded.job_description, resume_text=excluded.resume_text, raw_transcript=excluded.raw_transcript,
                updated_at=excluded.updated_at""",
                values,
            )
        return self.get_interview(interview_id)

    def get_interview(self, interview_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone()
        if not row:
            raise KeyError(interview_id)
        return dict(row)

    def list_interviews(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM interviews ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def delete_interview(self, interview_id: str) -> list[str]:
        with self._lock, self.connect() as connection:
            rows = connection.execute("SELECT storage_path FROM materials WHERE interview_id=?", (interview_id,)).fetchall()
            connection.execute("DELETE FROM interviews WHERE id=?", (interview_id,))
        return [row[0] for row in rows if row[0]]

    def get_parse_run_ids(self, interview_id: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT id FROM parse_runs WHERE interview_id=?", (interview_id,)).fetchall()
        return [row[0] for row in rows]

    def update_interview(self, interview_id: str, **fields: Any) -> None:
        allowed = {"status", "latest_run_id", "job_description", "resume_text", "raw_transcript", "updated_at"}
        values = {key: value for key, value in fields.items() if key in allowed}
        values["updated_at"] = utc_now()
        assignment = ", ".join(f"{key}=?" for key in values)
        with self._lock, self.connect() as connection:
            connection.execute(f"UPDATE interviews SET {assignment} WHERE id=?", (*values.values(), interview_id))

    def add_material(
        self,
        interview_id: str,
        material_type: str,
        text: str,
        filename: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        storage_path: str = "",
        mime_type: str = "",
        size_bytes: int = 0,
        sha256: str = "",
        duration_seconds: float | None = None,
        processing_status: str = "READY",
    ) -> dict[str, Any]:
        material_id = str(uuid.uuid4())
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO materials(id, interview_id, material_type, filename, text, metadata_json,
                storage_path, mime_type, size_bytes, sha256, duration_seconds, processing_status, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    material_id, interview_id, material_type, filename, text,
                    json.dumps(metadata or {}, ensure_ascii=False), storage_path, mime_type,
                    size_bytes, sha256, duration_seconds, processing_status, utc_now(),
                ),
            )
        column = {"job_description": "job_description", "resume": "resume_text", "transcript": "raw_transcript"}.get(material_type)
        if column and text:
            self.update_interview(interview_id, **{column: text})
        return self.get_material(material_id)

    def get_material(self, material_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        if not row:
            raise KeyError(material_id)
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        result["interviewId"] = result["interview_id"]
        result["materialType"] = result["material_type"]
        result["textLength"] = len(result.get("text", ""))
        return result

    def latest_material(self, interview_id: str, material_types: Iterable[str]) -> dict[str, Any] | None:
        types = list(material_types)
        placeholders = ",".join("?" for _ in types)
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT id FROM materials WHERE interview_id=? AND material_type IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
                (interview_id, *types),
            ).fetchone()
        return self.get_material(row["id"]) if row else None

    def create_parse_run(self, interview_id: str, material_id: str | None, provider: str) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO parse_runs(id, interview_id, material_id, status, phase, provider, retry_count,
                artifact_id, error, metrics_json, events_json, created_at, updated_at)
                VALUES(?,?,?,'QUEUED','queued',?,0,'',NULL,'{}','[]',?,?)""",
                (run_id, interview_id, material_id, provider, now, now),
            )
        self.update_interview(interview_id, status="PARSING")
        self.append_parse_event(run_id, "PARSE_CREATED", {"phase": "queued", "message": "解析任务已创建"})
        return self.get_parse_run(run_id)

    def get_parse_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM parse_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        result = dict(row)
        result["events"] = json.loads(result.pop("events_json") or "[]")
        result["metrics"] = json.loads(result.pop("metrics_json") or "{}")
        return result

    def update_parse_run(self, run_id: str, **updates: Any) -> None:
        allowed = {"status", "phase", "provider", "retry_count", "artifact_id", "error"}
        fields = {key: value for key, value in updates.items() if key in allowed and value is not None}
        if "metrics" in updates:
            fields["metrics_json"] = json.dumps(updates["metrics"], ensure_ascii=False)
        fields["updated_at"] = utc_now()
        assignment = ", ".join(f"{key}=?" for key in fields)
        with self._lock, self.connect() as connection:
            connection.execute(f"UPDATE parse_runs SET {assignment} WHERE id=?", (*fields.values(), run_id))

    def append_parse_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            row = connection.execute("SELECT events_json FROM parse_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            events = json.loads(row[0] or "[]")
            event = {"id": len(events) + 1, "type": event_type, "data": data, "createdAt": utc_now()}
            events.append(event)
            connection.execute(
                "UPDATE parse_runs SET events_json=?, updated_at=? WHERE id=?",
                (json.dumps(events, ensure_ascii=False), utc_now(), run_id),
            )
        return event

    def replace_segments(self, interview_id: str, material_id: str | None, segments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock, self.connect() as connection:
            self._replace_segments(connection, interview_id, material_id, segments)
        return self.get_segments(interview_id)

    @staticmethod
    def _replace_segments(
        connection: sqlite3.Connection,
        interview_id: str,
        material_id: str | None,
        segments: Iterable[dict[str, Any]],
    ) -> None:
        connection.execute("DELETE FROM transcript_segments WHERE interview_id=?", (interview_id,))
        for index, item in enumerate(segments, 1):
            connection.execute(
                """INSERT INTO transcript_segments(id, interview_id, material_id, order_index, raw_text,
                normalized_text, speaker_label, speaker_role, start_time, end_time, start_char, end_char,
                confidence, speaker_confidence, needs_confirmation, excluded) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"], interview_id, material_id, item.get("ordinal", index), item.get("rawText", ""),
                    item.get("normalizedText", item.get("rawText", "")), item.get("speakerLabel", ""),
                    item.get("speakerRole", "unknown"), item.get("startTime"), item.get("endTime"),
                    item.get("startChar"), item.get("endChar"), float(item.get("confidence", 0)), item.get("speakerConfidence"),
                    int(item.get("needsConfirmation", False)), int(item.get("excluded", False)),
                ),
            )

    def get_segments(self, interview_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transcript_segments WHERE interview_id=? ORDER BY order_index", (interview_id,)
            ).fetchall()
        return [self._segment_dict(row) for row in rows]

    def update_segments(self, interview_id: str, updates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock, self.connect() as connection:
            for item in updates:
                connection.execute(
                    """UPDATE transcript_segments SET speaker_role=COALESCE(?,speaker_role),
                    needs_confirmation=COALESCE(?,needs_confirmation), excluded=COALESCE(?,excluded)
                    WHERE id=? AND interview_id=?""",
                    (
                        item.get("speakerRole"),
                        int(item["needsConfirmation"]) if "needsConfirmation" in item else None,
                        int(item["excluded"]) if "excluded" in item else None,
                        item["id"], interview_id,
                    ),
                )
        return self.get_segments(interview_id)

    def replace_questions(self, interview_id: str, questions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        question_list = list(questions)
        with self._lock, self.connect() as connection:
            self._replace_questions(connection, interview_id, question_list)
        return self.get_questions(interview_id)

    @staticmethod
    def _replace_questions(connection: sqlite3.Connection, interview_id: str, questions: Iterable[dict[str, Any]]) -> None:
        question_list = list(questions)
        now = utc_now()
        connection.execute("DELETE FROM question_cards WHERE interview_id=?", (interview_id,))
        for item in question_list:
            question_id = item["id"]
            extracted_question = item.get("extractedQuestion") or item.get("interviewerQuestion", "")
            extracted_answer = item.get("extractedAnswer") or item.get("candidateAnswer", "")
            edited_question = item.get("editedQuestion", "")
            edited_answer = item.get("editedAnswer", "")
            effective_question = edited_question or extracted_question
            effective_answer = edited_answer or extracted_answer
            topic_root_id = item.get("topicRootId") or question_id
            question_type = normalize_question_type(
                item.get("questionType"), f"{item.get('topicTitle', '')} {effective_question}"
            )
            connection.execute(
                """INSERT INTO question_cards(id, interview_id, order_index, question, answer, question_type,
                confidence, initial_diagnosis_json, confirmed, version, topic_root_id, parent_question_id,
                turn_type, extracted_question, extracted_answer, edited_question, edited_answer, topic_title,
                needs_confirmation, provenance_status, follow_up_impact, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    question_id, interview_id, item.get("order", 1), effective_question, effective_answer,
                    question_type, item.get("confidence", "medium"),
                    json.dumps(item.get("initialDiagnosis", []), ensure_ascii=False), int(item.get("confirmed", False)),
                    item.get("version", 1), topic_root_id, item.get("parentQuestionId"), item.get("turnType", "main"),
                    extracted_question, extracted_answer, edited_question, edited_answer,
                    item.get("topicTitle", ""), int(item.get("needsConfirmation", False)),
                    item.get("provenanceStatus", "edited" if edited_question or edited_answer else "source"),
                    item.get("followUpImpact", ""), now, now,
                ),
            )
        for item in question_list:
            for role, key in (("question", "questionSegmentIds"), ("answer", "answerSegmentIds")):
                for order, segment_id in enumerate(item.get(key, []), 1):
                    connection.execute(
                        "INSERT OR IGNORE INTO question_segment_links VALUES(?,?,?,?)",
                        (item["id"], segment_id, role, order),
                    )

    def commit_parse_result(
        self,
        interview_id: str,
        material_id: str | None,
        segments: Iterable[dict[str, Any]],
        questions: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with self._lock, self.connect() as connection:
            self._replace_segments(connection, interview_id, material_id, segments)
            self._replace_questions(connection, interview_id, questions)
        return self.get_questions(interview_id)

    def get_questions(self, interview_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM question_cards WHERE interview_id=? ORDER BY order_index", (interview_id,)).fetchall()
            links = connection.execute(
                """SELECT l.* FROM question_segment_links l JOIN question_cards q ON q.id=l.question_id
                WHERE q.interview_id=? ORDER BY l.order_index""", (interview_id,),
            ).fetchall()
        by_question: dict[str, dict[str, list[str]]] = {}
        for link in links:
            by_question.setdefault(link["question_id"], {"question": [], "answer": []})[link["link_role"]].append(link["segment_id"])
        return [self._question_dict(row, by_question.get(row["id"])) for row in rows]

    def get_question_topics(self, interview_id: str) -> list[dict[str, Any]]:
        questions = self.get_questions(interview_id)
        roots = [item for item in questions if item["turnType"] == "main" or not item.get("parentQuestionId")]
        topics = []
        for root in roots:
            follow_ups = [item for item in questions if item["id"] != root["id"] and item.get("topicRootId") == root["id"]]
            follow_ups.sort(key=lambda item: item["order"])
            topics.append({"id": root["id"], "title": root.get("topicTitle") or root["interviewerQuestion"][:32], "mainTurn": root, "followUps": follow_ups})
        return topics

    def get_topic_questions(self, interview_id: str) -> list[dict[str, Any]]:
        result = []
        for topic in self.get_question_topics(interview_id):
            root = dict(topic["mainTurn"])
            follow_ups = topic["followUps"]
            parts = [root.get("candidateAnswer", "")]
            for item in follow_ups:
                parts.append(f"追问：{item['interviewerQuestion']}\n回答：{item['candidateAnswer']}")
            root["candidateAnswer"] = "\n".join(part for part in parts if part).strip()
            root["followUpTurns"] = follow_ups
            root["topicTitle"] = topic["title"]
            result.append(root)
        return result

    def confirm_questions(self, interview_id: str, ignored_segment_ids: Iterable[str] = ()) -> None:
        with self._lock, self.connect() as connection:
            for segment_id in ignored_segment_ids:
                connection.execute(
                    "UPDATE transcript_segments SET excluded=1, needs_confirmation=0 WHERE id=? AND interview_id=?",
                    (segment_id, interview_id),
                )
            connection.execute("UPDATE question_cards SET confirmed=1, updated_at=? WHERE interview_id=?", (utc_now(), interview_id))
        self.update_interview(interview_id, status="WAITING_CONFIRMATION")

    def unresolved_segments(self, interview_id: str) -> list[dict[str, Any]]:
        return [item for item in self.get_segments(interview_id) if item["needsConfirmation"] and not item["excluded"]]

    def create_run(
        self,
        interview_id: str,
        enable_web_verify: bool = False,
        review_mode: str = "full",
        *,
        agent_mode: str = "fixture",
        input_digest: str = "",
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO review_runs(id, interview_id, status, phase, hello_session_id, error,
                metrics_json, events_json, enable_web_verify, review_mode, plan_json, checkpoint_json,
                input_digest, agent_mode, degraded, failure_code, audit_round, revision_count, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, interview_id, "REVIEWING", "queued", None, None, "{}", "[]",
                    int(enable_web_verify), review_mode, "{}", "{}", input_digest, agent_mode,
                    0, "", 0, 0, now, now,
                ),
            )
        self.update_interview(interview_id, status="REVIEWING", latest_run_id=run_id)
        message = "快速复盘任务已创建" if review_mode == "quick" else "复盘任务已创建"
        self.append_event(run_id, "RUN_CREATED", {"phase": "queued", "reviewMode": review_mode, "message": message})
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM review_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        result = dict(row)
        result["events"] = json.loads(result.pop("events_json") or "[]")
        result["metrics"] = json.loads(result.pop("metrics_json") or "{}")
        result["plan"] = json.loads(result.pop("plan_json") or "{}")
        result["checkpoint"] = json.loads(result.pop("checkpoint_json") or "{}")
        result["degraded"] = bool(result.get("degraded"))
        return result

    def update_run(self, run_id: str, *, status: str | None = None, phase: str | None = None, error: str | None = None, metrics: dict[str, Any] | None = None, hello_session_id: str | None = None, plan: dict[str, Any] | None = None, checkpoint: dict[str, Any] | None = None, input_digest: str | None = None, agent_mode: str | None = None, degraded: bool | None = None, failure_code: str | None = None, audit_round: int | None = None, revision_count: int | None = None) -> None:
        fields: dict[str, Any] = {"updated_at": utc_now()}
        if status is not None: fields["status"] = status
        if phase is not None: fields["phase"] = phase
        if error is not None: fields["error"] = error
        if metrics is not None: fields["metrics_json"] = json.dumps(metrics, ensure_ascii=False)
        if hello_session_id is not None: fields["hello_session_id"] = hello_session_id
        if plan is not None: fields["plan_json"] = json.dumps(plan, ensure_ascii=False)
        if checkpoint is not None: fields["checkpoint_json"] = json.dumps(checkpoint, ensure_ascii=False)
        if input_digest is not None: fields["input_digest"] = input_digest
        if agent_mode is not None: fields["agent_mode"] = agent_mode
        if degraded is not None: fields["degraded"] = int(degraded)
        if failure_code is not None: fields["failure_code"] = failure_code
        if audit_round is not None: fields["audit_round"] = audit_round
        if revision_count is not None: fields["revision_count"] = revision_count
        assignment = ", ".join(f"{key}=?" for key in fields)
        with self._lock, self.connect() as connection:
            connection.execute(f"UPDATE review_runs SET {assignment} WHERE id=?", (*fields.values(), run_id))

    def append_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self.connect() as connection:
            row = connection.execute("SELECT events_json FROM review_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            events = json.loads(row[0] or "[]")
            event = {"id": len(events) + 1, "type": event_type, "data": data, "createdAt": utc_now()}
            events.append(event)
            connection.execute("UPDATE review_runs SET events_json=?, updated_at=? WHERE id=?", (json.dumps(events, ensure_ascii=False), utc_now(), run_id))
        return event

    def save_stage_artifact(
        self,
        run_id: str,
        phase: str,
        payload: dict[str, Any],
        *,
        topic_id: str = "",
        agent_type: str = "",
        model: str = "",
        session_id: str = "",
        duration_seconds: float = 0,
        token_count: int = 0,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM review_stage_artifacts WHERE run_id=? AND phase=? AND topic_id=?",
                (run_id, phase, topic_id),
            ).fetchone()
            version = int(row[0]) + 1
            connection.execute(
                "UPDATE review_stage_artifacts SET status='SUPERSEDED', updated_at=? WHERE run_id=? AND phase=? AND topic_id=? AND status='ACCEPTED'",
                (now, run_id, phase, topic_id),
            )
            artifact_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO review_stage_artifacts(id, run_id, phase, topic_id, version, status,
                payload_json, agent_type, model, session_id, duration_seconds, token_count, created_at, updated_at)
                VALUES(?,?,?,?,?,'ACCEPTED',?,?,?,?,?,?,?,?)""",
                (
                    artifact_id, run_id, phase, topic_id, version,
                    json.dumps(payload, ensure_ascii=False), agent_type, model, session_id,
                    duration_seconds, token_count, now, now,
                ),
            )
        return self.get_stage_artifact(artifact_id)

    def get_stage_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM review_stage_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError(artifact_id)
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
        return result

    def get_stage_artifacts(self, run_id: str, *, accepted_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM review_stage_artifacts WHERE run_id=?"
        parameters: list[Any] = [run_id]
        if accepted_only:
            query += " AND status='ACCEPTED'"
        query += " ORDER BY created_at, version"
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def accepted_artifact(self, run_id: str, phase: str, topic_id: str = "") -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT id FROM review_stage_artifacts
                WHERE run_id=? AND phase=? AND topic_id=? AND status='ACCEPTED'
                ORDER BY version DESC LIMIT 1""",
                (run_id, phase, topic_id),
            ).fetchone()
        return self.get_stage_artifact(row["id"]) if row else None

    def save_reviews(self, run_id: str, reviews: Iterable[dict[str, Any]]) -> None:
        now = utc_now()
        with self._lock, self.connect() as connection:
            connection.execute("DELETE FROM question_reviews WHERE run_id=?", (run_id,))
            for review in reviews:
                connection.execute(
                    "INSERT INTO question_reviews VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), run_id, review["id"], json.dumps(review, ensure_ascii=False), now, now),
                )

    def get_reviews(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT review_json FROM question_reviews WHERE run_id=?", (run_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_evidence(self, run_id: str, question_id: str, evidence: Iterable[dict[str, Any]]) -> None:
        with self._lock, self.connect() as connection:
            for item in evidence:
                connection.execute(
                    "INSERT OR REPLACE INTO evidence_refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["id"], run_id, question_id, item["sourceType"], item.get("sourceId", ""), item.get("quote", ""),
                        item.get("locator", ""), int(item.get("verified", False)), item.get("confidence", 0),
                        item.get("title", ""), item.get("url", ""), utc_now(),
                    ),
                )

    def save_growth_snapshot(self, interview_id: str, run_id: str, scores: dict[str, Any], weak_dimensions: list[str], action_items: list[dict[str, Any]]) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO growth_snapshots VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), interview_id, run_id, json.dumps(scores), json.dumps(weak_dimensions, ensure_ascii=False), json.dumps(action_items, ensure_ascii=False), utc_now()),
            )

    def get_growth_trends(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT g.*, i.company, i.position, i.interview_date FROM growth_snapshots g
                JOIN interviews i ON i.id=g.interview_id ORDER BY g.created_at"""
            ).fetchall()
        return [{**dict(row), "scores": json.loads(row["scores_json"]), "weakDimensions": json.loads(row["weak_dimensions_json"]), "actionItems": json.loads(row["action_items_json"])} for row in rows]

    @staticmethod
    def _question_dict(row: sqlite3.Row, links: dict[str, list[str]] | None = None) -> dict[str, Any]:
        links = links or {"question": [], "answer": []}
        return {
            "id": row["id"], "order": row["order_index"], "interviewerQuestion": row["question"],
            "candidateAnswer": row["answer"], "questionType": row["question_type"], "confidence": row["confidence"],
            "initialDiagnosis": json.loads(row["initial_diagnosis_json"] or "[]"), "confirmed": bool(row["confirmed"]), "version": row["version"],
            "topicRootId": row["topic_root_id"] or row["id"], "parentQuestionId": row["parent_question_id"],
            "turnType": row["turn_type"], "extractedQuestion": row["extracted_question"] or row["question"],
            "extractedAnswer": row["extracted_answer"] or row["answer"], "editedQuestion": row["edited_question"],
            "editedAnswer": row["edited_answer"], "topicTitle": row["topic_title"],
            "needsConfirmation": bool(row["needs_confirmation"]), "provenanceStatus": row["provenance_status"],
            "followUpImpact": row["follow_up_impact"], "questionSegmentIds": links.get("question", []),
            "answerSegmentIds": links.get("answer", []),
        }

    @staticmethod
    def _segment_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "ordinal": row["order_index"], "rawText": row["raw_text"],
            "normalizedText": row["normalized_text"], "speakerLabel": row["speaker_label"],
            "speakerRole": row["speaker_role"], "startTime": row["start_time"], "endTime": row["end_time"],
            "startChar": row["start_char"], "endChar": row["end_char"], "confidence": row["confidence"],
            "speakerConfidence": row["speaker_confidence"],
            "needsConfirmation": bool(row["needs_confirmation"]), "excluded": bool(row["excluded"]),
        }
