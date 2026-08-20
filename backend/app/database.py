from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.app.services.evidence import QUESTION_TYPES, infer_topic_title, normalize_question_type


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActiveAgentRunError(RuntimeError):
    def __init__(self, run_id: str, interview_id: str):
        super().__init__("another real Agent review is already running")
        self.run_id = run_id
        self.interview_id = interview_id


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
            follow_up_impact TEXT NOT NULL DEFAULT '', confidence_score REAL NOT NULL DEFAULT 75,
            raw_confidence_score REAL NOT NULL DEFAULT 75, confidence_details_json TEXT NOT NULL DEFAULT '{}',
            confirmation_reasons_json TEXT NOT NULL DEFAULT '[]', parse_method TEXT NOT NULL DEFAULT 'legacy',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
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
            confidence_details_json TEXT NOT NULL DEFAULT '{}', confirmation_reasons_json TEXT NOT NULL DEFAULT '[]',
            parse_method TEXT NOT NULL DEFAULT 'legacy',
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE,
            FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS transcript_atoms (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, material_id TEXT, order_index INTEGER NOT NULL,
            raw_text TEXT NOT NULL, start_char INTEGER, end_char INTEGER, start_time REAL, end_time REAL,
            speaker_label TEXT NOT NULL DEFAULT '', speaker_role TEXT NOT NULL DEFAULT 'unknown',
            confidence REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE,
            FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS segment_atom_links (
            segment_id TEXT NOT NULL, atom_id TEXT NOT NULL, order_index INTEGER NOT NULL,
            PRIMARY KEY(segment_id, atom_id),
            FOREIGN KEY(segment_id) REFERENCES transcript_segments(id) ON DELETE CASCADE,
            FOREIGN KEY(atom_id) REFERENCES transcript_atoms(id) ON DELETE CASCADE
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
        CREATE TABLE IF NOT EXISTS growth_action_progress (
            run_id TEXT NOT NULL, action_id TEXT NOT NULL, interview_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', started_at TEXT, completed_at TEXT,
            user_note TEXT NOT NULL DEFAULT '', completion_evidence TEXT NOT NULL DEFAULT '',
            self_rating INTEGER, updated_at TEXT NOT NULL,
            PRIMARY KEY(run_id, action_id),
            FOREIGN KEY(run_id) REFERENCES review_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS practice_sessions (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, run_id TEXT NOT NULL,
            action_id TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'generating',
            brief_json TEXT NOT NULL DEFAULT '{}', draft_text TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(run_id, action_id, mode),
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES review_runs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS practice_attempts (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
            response_text TEXT NOT NULL, self_rating INTEGER,
            status TEXT NOT NULL DEFAULT 'reviewing', review_json TEXT NOT NULL DEFAULT '{}',
            error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(session_id, attempt_no),
            FOREIGN KEY(session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_practice_sessions_run_action
            ON practice_sessions(run_id, action_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_practice_attempts_session
            ON practice_attempts(session_id, attempt_no);
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
                "confidence_score": "REAL NOT NULL DEFAULT 75",
                "raw_confidence_score": "REAL NOT NULL DEFAULT 75",
                "confidence_details_json": "TEXT NOT NULL DEFAULT '{}'",
                "confirmation_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
                "parse_method": "TEXT NOT NULL DEFAULT 'legacy'",
            },
            "transcript_segments": {
                "speaker_confidence": "REAL",
                "confidence_details_json": "TEXT NOT NULL DEFAULT '{}'",
                "confirmation_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
                "parse_method": "TEXT NOT NULL DEFAULT 'legacy'",
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

    def delete_interview(self, interview_id: str) -> dict[str, list[str]]:
        with self._lock, self.connect() as connection:
            materials = connection.execute(
                "SELECT id, storage_path FROM materials WHERE interview_id=?",
                (interview_id,),
            ).fetchall()
            parse_runs = connection.execute(
                "SELECT id FROM parse_runs WHERE interview_id=?",
                (interview_id,),
            ).fetchall()
            review_runs = connection.execute(
                "SELECT id, hello_session_id FROM review_runs WHERE interview_id=?",
                (interview_id,),
            ).fetchall()
            questions = connection.execute(
                "SELECT id FROM question_cards WHERE interview_id=?",
                (interview_id,),
            ).fetchall()
            review_run_ids = [row["id"] for row in review_runs]
            artifact_sessions: list[str] = []
            evidence_ids: list[str] = []
            if review_run_ids:
                placeholders = ",".join("?" for _ in review_run_ids)
                artifact_sessions = [
                    row["session_id"]
                    for row in connection.execute(
                        f"SELECT session_id FROM review_stage_artifacts WHERE run_id IN ({placeholders}) AND session_id<>''",
                        review_run_ids,
                    ).fetchall()
                ]
                evidence_ids = [
                    row["id"]
                    for row in connection.execute(
                        f"SELECT id FROM evidence_refs WHERE run_id IN ({placeholders})",
                        review_run_ids,
                    ).fetchall()
                ]
            connection.execute("DELETE FROM interviews WHERE id=?", (interview_id,))
        identifiers = {
            interview_id,
            *(row["id"] for row in materials),
            *(row["id"] for row in parse_runs),
            *review_run_ids,
            *(row["id"] for row in questions),
            *(row["hello_session_id"] for row in review_runs if row["hello_session_id"]),
            *artifact_sessions,
            *evidence_ids,
        }
        return {
            "storagePaths": [row["storage_path"] for row in materials if row["storage_path"]],
            "parseRunIds": [row["id"] for row in parse_runs],
            "reviewRunIds": review_run_ids,
            "identifiers": sorted(identifiers),
        }

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
                confidence, speaker_confidence, needs_confirmation, excluded, confidence_details_json,
                confirmation_reasons_json, parse_method) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"], interview_id, material_id, item.get("ordinal", index), item.get("rawText", ""),
                    item.get("normalizedText", item.get("rawText", "")), item.get("speakerLabel", ""),
                    item.get("speakerRole", "unknown"), item.get("startTime"), item.get("endTime"),
                    item.get("startChar"), item.get("endChar"), float(item.get("confidence", 0)), item.get("speakerConfidence"),
                    int(item.get("needsConfirmation", False)), int(item.get("excluded", False)),
                    json.dumps(item.get("confidenceDetails", {}), ensure_ascii=False),
                    json.dumps(item.get("confirmationReasons", []), ensure_ascii=False), item.get("parseMethod", "legacy"),
                ),
            )
            for atom_order, atom_id in enumerate(item.get("atomIds", []), 1):
                connection.execute(
                    "INSERT OR IGNORE INTO segment_atom_links(segment_id,atom_id,order_index) VALUES(?,?,?)",
                    (item["id"], atom_id, atom_order),
                )

    @staticmethod
    def _replace_atoms(
        connection: sqlite3.Connection,
        interview_id: str,
        material_id: str | None,
        atoms: Iterable[dict[str, Any]],
    ) -> None:
        connection.execute("DELETE FROM transcript_atoms WHERE interview_id=?", (interview_id,))
        for index, item in enumerate(atoms, 1):
            connection.execute(
                """INSERT INTO transcript_atoms(id,interview_id,material_id,order_index,raw_text,start_char,end_char,
                start_time,end_time,speaker_label,speaker_role,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"], interview_id, material_id, item.get("ordinal", index), item.get("rawText", ""),
                    item.get("startChar"), item.get("endChar"), item.get("startTime"), item.get("endTime"),
                    item.get("speakerLabel", ""), item.get("speakerRole", "unknown"), float(item.get("confidence", 0)),
                ),
            )

    def get_atoms(self, interview_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transcript_atoms WHERE interview_id=? ORDER BY order_index", (interview_id,)
            ).fetchall()
        return [self._atom_dict(row) for row in rows]

    def get_segments(self, interview_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transcript_segments WHERE interview_id=? ORDER BY order_index", (interview_id,)
            ).fetchall()
            links = connection.execute(
                """SELECT l.* FROM segment_atom_links l JOIN transcript_segments s ON s.id=l.segment_id
                WHERE s.interview_id=? ORDER BY l.order_index""", (interview_id,),
            ).fetchall()
        by_segment: dict[str, list[str]] = {}
        for link in links:
            by_segment.setdefault(link["segment_id"], []).append(link["atom_id"])
        return [self._segment_dict(row, by_segment.get(row["id"], [])) for row in rows]

    def update_segments(self, interview_id: str, updates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock, self.connect() as connection:
            for item in updates:
                current = connection.execute(
                    "SELECT * FROM transcript_segments WHERE id=? AND interview_id=?", (item["id"], interview_id)
                ).fetchone()
                if not current:
                    continue
                reasons = json.loads(current["confirmation_reasons_json"] or "[]")
                role = item.get("speakerRole", current["speaker_role"])
                reasons = [reason for reason in reasons if reason.get("code") != "SPEAKER_ROLE_UNCERTAIN"]
                if role == "unknown":
                    reasons.append({"code": "SPEAKER_ROLE_UNCERTAIN", "label": "说话人身份不明确", "dimension": "speaker", "score": 55, "evidenceAtomIds": [], "impact": 45, "summary": ""})
                needs_confirmation = int(bool(reasons))
                connection.execute(
                    """UPDATE transcript_segments SET speaker_role=COALESCE(?,speaker_role),
                    needs_confirmation=?, excluded=COALESCE(?,excluded), confirmation_reasons_json=?,
                    parse_method=CASE WHEN ? IS NULL THEN parse_method ELSE 'edited' END
                    WHERE id=? AND interview_id=?""",
                    (
                        item.get("speakerRole"),
                        needs_confirmation,
                        int(item["excluded"]) if "excluded" in item else None,
                        json.dumps(reasons, ensure_ascii=False), item.get("speakerRole"),
                        item["id"], interview_id,
                    ),
                )
        return self.get_segments(interview_id)

    def split_segment(
        self,
        interview_id: str,
        segment_id: str,
        after_atom_id: str,
        *,
        question_id: str | None = None,
        left_assignment: str | None = None,
        right_assignment: str | None = None,
    ) -> list[dict[str, Any]]:
        assignments = (left_assignment, right_assignment)
        if any(value is not None for value in assignments) and (not question_id or any(value not in {"question", "answer", "none"} for value in assignments)):
            raise ValueError("指定拆分归属时必须同时提供题卡、左侧归属和右侧归属")
        with self._lock, self.connect() as connection:
            segment = connection.execute(
                "SELECT * FROM transcript_segments WHERE id=? AND interview_id=?", (segment_id, interview_id)
            ).fetchone()
            if not segment:
                raise KeyError(segment_id)
            atom_rows = connection.execute(
                """SELECT a.* FROM segment_atom_links l JOIN transcript_atoms a ON a.id=l.atom_id
                WHERE l.segment_id=? ORDER BY l.order_index""", (segment_id,),
            ).fetchall()
            atom_ids = [row["id"] for row in atom_rows]
            if after_atom_id not in atom_ids or atom_ids.index(after_atom_id) >= len(atom_ids) - 1:
                raise ValueError("拆分位置必须位于当前话轮的两个原子之间")
            split_index = atom_ids.index(after_atom_id) + 1
            left, right = atom_rows[:split_index], atom_rows[split_index:]
            new_id = str(uuid.uuid4())
            reasons = self._manual_segment_reasons(segment)
            needs_confirmation = int(bool(reasons))
            left_text = self._text_from_atom_rows(connection, segment["material_id"], left)
            right_text = self._text_from_atom_rows(connection, segment["material_id"], right)
            connection.execute(
                """UPDATE transcript_segments SET raw_text=?,normalized_text=?,end_time=?,end_char=?,
                needs_confirmation=?,confidence_details_json=?,confirmation_reasons_json=?,parse_method='edited'
                WHERE id=?""",
                (left_text, " ".join(left_text.split()), left[-1]["end_time"], left[-1]["end_char"], needs_confirmation,
                 json.dumps({"manualBoundaryEdit": True}, ensure_ascii=False), json.dumps(reasons, ensure_ascii=False), segment_id),
            )
            connection.execute(
                """INSERT INTO transcript_segments(id,interview_id,material_id,order_index,raw_text,normalized_text,
                speaker_label,speaker_role,start_time,end_time,start_char,end_char,confidence,speaker_confidence,
                needs_confirmation,excluded,confidence_details_json,confirmation_reasons_json,parse_method)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id, interview_id, segment["material_id"], segment["order_index"] + 1, right_text, " ".join(right_text.split()),
                 segment["speaker_label"], segment["speaker_role"], right[0]["start_time"], right[-1]["end_time"],
                 right[0]["start_char"], right[-1]["end_char"], segment["confidence"], segment["speaker_confidence"],
                 needs_confirmation, segment["excluded"], json.dumps({"manualBoundaryEdit": True}, ensure_ascii=False),
                 json.dumps(reasons, ensure_ascii=False), "edited"),
            )
            connection.execute("DELETE FROM segment_atom_links WHERE segment_id=?", (segment_id,))
            for order, row in enumerate(left, 1):
                connection.execute("INSERT INTO segment_atom_links VALUES(?,?,?)", (segment_id, row["id"], order))
            for order, row in enumerate(right, 1):
                connection.execute("INSERT INTO segment_atom_links VALUES(?,?,?)", (new_id, row["id"], order))
            links = connection.execute(
                "SELECT * FROM question_segment_links WHERE segment_id=?", (segment_id,)
            ).fetchall()
            if question_id and not any(link["question_id"] == question_id for link in links):
                raise ValueError("当前话轮不属于指定题卡")
            for link in links:
                connection.execute(
                    """UPDATE question_segment_links SET order_index=order_index+1
                    WHERE question_id=? AND link_role=? AND order_index>?""",
                    (link["question_id"], link["link_role"], link["order_index"]),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO question_segment_links VALUES(?,?,?,?)",
                    (link["question_id"], new_id, link["link_role"], link["order_index"] + 1),
                )
            connection.execute(
                "UPDATE transcript_segments SET order_index=order_index+1 WHERE interview_id=? AND id<>? AND order_index>?",
                (interview_id, new_id, segment["order_index"]),
            )
            self._renumber_segments(connection, interview_id)
            if question_id:
                connection.execute(
                    "DELETE FROM question_segment_links WHERE question_id=? AND segment_id IN (?,?)",
                    (question_id, segment_id, new_id),
                )
                for target_id, assignment in ((segment_id, left_assignment), (new_id, right_assignment)):
                    if assignment != "none":
                        connection.execute(
                            "INSERT INTO question_segment_links(question_id,segment_id,link_role,order_index) VALUES(?,?,?,?)",
                            (question_id, target_id, assignment, 0),
                        )
                self._renumber_question_segment_links(connection, question_id)
            self._refresh_question_texts(connection, interview_id)
        return self.get_segments(interview_id)

    def merge_segments(self, interview_id: str, segment_ids: Iterable[str]) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(segment_ids))
        if len(requested) < 2:
            raise ValueError("至少选择两个相邻话轮")
        with self._lock, self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM transcript_segments WHERE interview_id=? AND id IN ({','.join('?' for _ in requested)}) ORDER BY order_index",
                (interview_id, *requested),
            ).fetchall()
            if len(rows) != len(requested):
                raise KeyError("话轮不存在")
            ordinals = [row["order_index"] for row in rows]
            if ordinals != list(range(min(ordinals), max(ordinals) + 1)):
                raise ValueError("只能合并相邻话轮")
            keep_id = rows[0]["id"]
            selected = {row["id"] for row in rows}
            question_links = connection.execute(
                """SELECT l.* FROM question_segment_links l JOIN question_cards q ON q.id=l.question_id
                WHERE q.interview_id=? ORDER BY l.question_id,l.link_role,l.order_index""", (interview_id,),
            ).fetchall()
            link_signatures: dict[str, set[tuple[str, str]]] = {segment_id: set() for segment_id in selected}
            for link in question_links:
                if link["segment_id"] in selected:
                    link_signatures[link["segment_id"]].add((link["question_id"], link["link_role"]))
            signatures = [link_signatures[row["id"]] for row in rows]
            if any(signature != signatures[0] for signature in signatures[1:]):
                raise ValueError("只能合并同一题卡中同属问题或同属回答的相邻话轮")
            if len({row["speaker_role"] for row in rows}) > 1:
                raise ValueError("不同说话人的话轮不能合并")
            rebuilt: dict[tuple[str, str], list[str]] = {}
            for link in question_links:
                key = (link["question_id"], link["link_role"])
                target = rebuilt.setdefault(key, [])
                value = keep_id if link["segment_id"] in selected else link["segment_id"]
                if value not in target:
                    target.append(value)
            for question_id in {key[0] for key in rebuilt}:
                question_ids = set(rebuilt.get((question_id, "question"), []))
                answer_ids = set(rebuilt.get((question_id, "answer"), []))
                if question_ids & answer_ids:
                    raise ValueError("同一原文片段不能同时归属问题和回答")
            atom_rows = connection.execute(
                f"""SELECT a.* FROM segment_atom_links l JOIN transcript_atoms a ON a.id=l.atom_id
                WHERE l.segment_id IN ({','.join('?' for _ in requested)}) ORDER BY a.order_index""", tuple(requested),
            ).fetchall()
            text = self._text_from_atom_rows(connection, rows[0]["material_id"], atom_rows)
            roles = {row["speaker_role"] for row in rows}
            role = roles.pop() if len(roles) == 1 else "unknown"
            reasons = [] if role != "unknown" else [{
                "code": "SPEAKER_ROLE_UNCERTAIN", "label": "说话人身份不明确", "dimension": "speaker",
                "score": 55, "evidenceAtomIds": [row["id"] for row in atom_rows], "impact": 45, "summary": "合并的话轮原说话人不一致",
            }]
            connection.execute(
                """UPDATE transcript_segments SET raw_text=?,normalized_text=?,speaker_role=?,start_time=?,end_time=?,
                start_char=?,end_char=?,needs_confirmation=?,confidence_details_json=?,confirmation_reasons_json=?,parse_method='edited'
                WHERE id=?""",
                (text, " ".join(text.split()), role, atom_rows[0]["start_time"], atom_rows[-1]["end_time"],
                 atom_rows[0]["start_char"], atom_rows[-1]["end_char"], int(bool(reasons)),
                 json.dumps({"manualBoundaryEdit": True}, ensure_ascii=False), json.dumps(reasons, ensure_ascii=False), keep_id),
            )
            connection.execute("DELETE FROM segment_atom_links WHERE segment_id=?", (keep_id,))
            for order, atom in enumerate(atom_rows, 1):
                connection.execute("INSERT INTO segment_atom_links VALUES(?,?,?)", (keep_id, atom["id"], order))
            for row in rows[1:]:
                connection.execute("DELETE FROM transcript_segments WHERE id=?", (row["id"],))
            question_ids = [row[0] for row in connection.execute("SELECT id FROM question_cards WHERE interview_id=?", (interview_id,)).fetchall()]
            if question_ids:
                connection.execute(
                    f"DELETE FROM question_segment_links WHERE question_id IN ({','.join('?' for _ in question_ids)})", tuple(question_ids)
                )
            for (question_id, role_name), ids in rebuilt.items():
                for order, segment_id in enumerate(ids, 1):
                    connection.execute("INSERT INTO question_segment_links VALUES(?,?,?,?)", (question_id, segment_id, role_name, order))
            self._renumber_segments(connection, interview_id)
            self._refresh_question_texts(connection, interview_id)
        return self.get_segments(interview_id)

    @staticmethod
    def _text_from_atom_rows(connection: sqlite3.Connection, material_id: str | None, rows: list[sqlite3.Row]) -> str:
        if not rows:
            return ""
        start, end = rows[0]["start_char"], rows[-1]["end_char"]
        if material_id and start is not None and end is not None:
            material = connection.execute("SELECT text FROM materials WHERE id=?", (material_id,)).fetchone()
            if material and material["text"]:
                return str(material["text"])[int(start):int(end)].strip()
        parts = [str(row["raw_text"]).strip() for row in rows if str(row["raw_text"]).strip()]
        if parts and all(re.search(r"[\u3400-\u9fff]", part) for part in parts):
            return "".join(parts)
        return " ".join(parts)

    @staticmethod
    def _manual_segment_reasons(segment: sqlite3.Row) -> list[dict[str, Any]]:
        reasons = [
            item for item in json.loads(segment["confirmation_reasons_json"] or "[]")
            if item.get("code") not in {"QUESTION_BOUNDARY_UNCERTAIN", "ANSWER_BOUNDARY_UNCERTAIN", "CHUNK_OVERLAP_CONFLICT"}
        ]
        if segment["speaker_role"] == "unknown" and not any(item.get("code") == "SPEAKER_ROLE_UNCERTAIN" for item in reasons):
            reasons.append({"code": "SPEAKER_ROLE_UNCERTAIN", "label": "说话人身份不明确", "dimension": "speaker", "score": 55, "evidenceAtomIds": [], "impact": 45, "summary": ""})
        return reasons

    @staticmethod
    def _renumber_segments(connection: sqlite3.Connection, interview_id: str) -> None:
        rows = connection.execute("SELECT id FROM transcript_segments WHERE interview_id=? ORDER BY order_index,id", (interview_id,)).fetchall()
        for order, row in enumerate(rows, 1):
            connection.execute("UPDATE transcript_segments SET order_index=? WHERE id=?", (order, row["id"]))

    @staticmethod
    def _renumber_question_segment_links(connection: sqlite3.Connection, question_id: str) -> None:
        for role in ("question", "answer"):
            rows = connection.execute(
                """SELECT l.segment_id FROM question_segment_links l
                JOIN transcript_segments s ON s.id=l.segment_id
                WHERE l.question_id=? AND l.link_role=? ORDER BY s.order_index,s.id""",
                (question_id, role),
            ).fetchall()
            for order, row in enumerate(rows, 1):
                connection.execute(
                    "UPDATE question_segment_links SET order_index=? WHERE question_id=? AND segment_id=? AND link_role=?",
                    (order, question_id, row["segment_id"], role),
                )

    @staticmethod
    def _refresh_question_texts(connection: sqlite3.Connection, interview_id: str) -> None:
        questions = connection.execute("SELECT * FROM question_cards WHERE interview_id=?", (interview_id,)).fetchall()
        for question in questions:
            values: dict[str, str] = {}
            for role in ("question", "answer"):
                rows = connection.execute(
                    """SELECT s.raw_text FROM question_segment_links l JOIN transcript_segments s ON s.id=l.segment_id
                    WHERE l.question_id=? AND l.link_role=? ORDER BY l.order_index""", (question["id"], role),
                ).fetchall()
                values[role] = "\n".join(row["raw_text"] for row in rows).strip()
            effective_question = question["edited_question"] or values["question"]
            effective_answer = question["edited_answer"] or values["answer"]
            connection.execute(
                """UPDATE question_cards SET extracted_question=?,extracted_answer=?,question=?,answer=?,confirmed=0,
                provenance_status='edited',updated_at=? WHERE id=?""",
                (values["question"], values["answer"], effective_question, effective_answer, utc_now(), question["id"]),
            )

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
                needs_confirmation, provenance_status, follow_up_impact, confidence_score, raw_confidence_score,
                confidence_details_json, confirmation_reasons_json, parse_method, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    question_id, interview_id, item.get("order", 1), effective_question, effective_answer,
                    question_type, item.get("confidence", "medium"),
                    json.dumps(item.get("initialDiagnosis", []), ensure_ascii=False), int(item.get("confirmed", False)),
                    item.get("version", 1), topic_root_id, item.get("parentQuestionId"), item.get("turnType", "main"),
                    extracted_question, extracted_answer, edited_question, edited_answer,
                    item.get("topicTitle", ""), int(item.get("needsConfirmation", False)),
                    item.get("provenanceStatus", "edited" if edited_question or edited_answer else "source"),
                    item.get("followUpImpact", ""), float(item.get("confidenceScore", {"high": 90, "medium": 75, "low": 50}.get(item.get("confidence"), 75))),
                    float(item.get("rawConfidenceScore", item.get("confidenceScore", {"high": 90, "medium": 75, "low": 50}.get(item.get("confidence"), 75)))),
                    json.dumps(item.get("confidenceDetails", {}), ensure_ascii=False),
                    json.dumps(item.get("confirmationReasons", []), ensure_ascii=False), item.get("parseMethod", "legacy"), now, now,
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
        atoms: Iterable[dict[str, Any]],
        segments: Iterable[dict[str, Any]],
        questions: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with self._lock, self.connect() as connection:
            connection.execute("DELETE FROM transcript_segments WHERE interview_id=?", (interview_id,))
            self._replace_atoms(connection, interview_id, material_id, atoms)
            self._replace_segments(connection, interview_id, material_id, segments)
            self._replace_questions(connection, interview_id, questions)
        return self.get_questions(interview_id)

    def get_questions(self, interview_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM question_cards WHERE interview_id=? ORDER BY order_index", (interview_id,)).fetchall()
            links = connection.execute(
                """SELECT l.*,s.raw_text FROM question_segment_links l
                JOIN question_cards q ON q.id=l.question_id
                JOIN transcript_segments s ON s.id=l.segment_id
                WHERE q.interview_id=? ORDER BY l.order_index""", (interview_id,),
            ).fetchall()
        by_question: dict[str, dict[str, list[str]]] = {}
        linked_text: dict[str, dict[str, list[str]]] = {}
        for link in links:
            by_question.setdefault(link["question_id"], {"question": [], "answer": []})[link["link_role"]].append(link["segment_id"])
            linked_text.setdefault(link["question_id"], {"question": [], "answer": []})[link["link_role"]].append(link["raw_text"])
        questions = []
        for row in rows:
            item = self._question_dict(row, by_question.get(row["id"]))
            if row["id"] in by_question:
                extracted_question = "\n".join(linked_text[row["id"]]["question"]).strip()
                extracted_answer = "\n".join(linked_text[row["id"]]["answer"]).strip()
                item["extractedQuestion"] = extracted_question
                item["extractedAnswer"] = extracted_answer
                item["interviewerQuestion"] = item["editedQuestion"] or extracted_question
                item["candidateAnswer"] = item["editedAnswer"] or extracted_answer
            questions.append(item)
        return questions

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
        reused_run_id: str | None = None
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """SELECT id,interview_id,agent_mode FROM review_runs
                WHERE interview_id=? AND status IN ('REVIEWING','AUDITING')
                ORDER BY created_at DESC LIMIT 1""",
                (interview_id,),
            ).fetchone()
            if active:
                if str(active["agent_mode"]) == agent_mode:
                    reused_run_id = str(active["id"])
                else:
                    raise ActiveAgentRunError(str(active["id"]), str(active["interview_id"]))
            if not reused_run_id and agent_mode == "helloagents":
                global_active = connection.execute(
                    """SELECT id,interview_id FROM review_runs
                    WHERE agent_mode='helloagents' AND status IN ('REVIEWING','AUDITING')
                    ORDER BY created_at DESC LIMIT 1"""
                ).fetchone()
                if global_active:
                    raise ActiveAgentRunError(str(global_active["id"]), str(global_active["interview_id"]))
            if not reused_run_id:
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
        if reused_run_id:
            self.update_interview(interview_id, status="REVIEWING", latest_run_id=reused_run_id)
            run = self.get_run(reused_run_id)
            run["reused"] = True
            return run
        self.update_interview(interview_id, status="REVIEWING", latest_run_id=run_id)
        message = "快速复盘任务已创建" if review_mode == "quick" else "复盘任务已创建"
        self.append_event(run_id, "RUN_CREATED", {"phase": "queued", "reviewMode": review_mode, "message": message})
        run = self.get_run(run_id)
        run["reused"] = False
        return run

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

    def fail_stale_runs(self, stale_before: str) -> list[str]:
        failed: list[str] = []
        now = utc_now()
        with self._lock, self.connect() as connection:
            rows = connection.execute(
                """SELECT id,interview_id,events_json FROM review_runs
                WHERE status IN ('REVIEWING','AUDITING') AND updated_at<?""",
                (stale_before,),
            ).fetchall()
            for row in rows:
                events = json.loads(row["events_json"] or "[]")
                events.append({
                    "id": len(events) + 1,
                    "type": "RUN_FAILED",
                    "data": {
                        "status": "FAILED",
                        "code": "AGENT_PROCESS_INTERRUPTED",
                        "message": "服务重启前 Agent 长时间无心跳，可从最近检查点恢复。",
                    },
                    "createdAt": now,
                })
                connection.execute(
                    """UPDATE review_runs SET status='FAILED',phase='failed',error=?,failure_code=?,events_json=?,updated_at=?
                    WHERE id=?""",
                    (
                        "服务重启前 Agent 长时间无心跳，可从最近检查点恢复。",
                        "AGENT_PROCESS_INTERRUPTED",
                        json.dumps(events, ensure_ascii=False),
                        now,
                        row["id"],
                    ),
                )
                connection.execute(
                    "UPDATE interviews SET status='FAILED',updated_at=? WHERE id=? AND latest_run_id=?",
                    (now, row["interview_id"], row["id"]),
                )
                failed.append(row["id"])
        return failed

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

    def sync_growth_snapshot(self, interview_id: str, run_id: str, scores: dict[str, Any], action_items: list[dict[str, Any]]) -> int:
        weak_dimensions = sorted(
            (key for key in scores if key != "overall"),
            key=lambda key: float(scores.get(key) or 0),
        )[:2]
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                """UPDATE growth_snapshots
                SET run_id=?,scores_json=?,weak_dimensions_json=?,action_items_json=?
                WHERE interview_id=?""",
                (
                    run_id,
                    json.dumps(scores),
                    json.dumps(weak_dimensions, ensure_ascii=False),
                    json.dumps(action_items, ensure_ascii=False),
                    interview_id,
                ),
            )
        return cursor.rowcount

    def get_growth_action_progress(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM growth_action_progress WHERE run_id=?",
                (run_id,),
            ).fetchall()
        return {str(row["action_id"]): self._growth_action_progress_dict(row) for row in rows}

    def merge_growth_action_progress(
        self,
        run_id: str,
        action_items: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        progress = self.get_growth_action_progress(run_id)
        practice = self.get_practice_action_summaries(run_id)
        merged: list[dict[str, Any]] = []
        for index, source in enumerate(action_items):
            action = dict(source)
            action_id = str(action.get("id") or f"action-{index + 1}")
            saved = progress.get(action_id)
            status = str(
                (saved or {}).get("status")
                or ("completed" if action.get("completed") else "pending")
            )
            action.update({
                "id": action_id,
                "status": status,
                "completed": status == "completed",
                "startedAt": (saved or {}).get("startedAt"),
                "completedAt": (saved or {}).get("completedAt"),
                "userNote": (saved or {}).get("userNote", ""),
                "completionEvidence": (saved or {}).get("completionEvidence", ""),
                "selfRating": (saved or {}).get("selfRating"),
                "practiceCount": int((practice.get(action_id) or {}).get("practiceCount") or 0),
                "latestPracticeStatus": (practice.get(action_id) or {}).get("latestPracticeStatus", ""),
                "latestPracticeSessionId": (practice.get(action_id) or {}).get("latestPracticeSessionId", ""),
                "latestPracticeResult": (practice.get(action_id) or {}).get("latestPracticeResult", ""),
            })
            merged.append(action)
        return merged

    def update_growth_action_progress(
        self,
        *,
        run_id: str,
        action_id: str,
        interview_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM growth_action_progress WHERE run_id=? AND action_id=?",
                (run_id, action_id),
            ).fetchone()
            current = self._growth_action_progress_dict(row) if row else {
                "status": "pending", "startedAt": None, "completedAt": None,
                "userNote": "", "completionEvidence": "", "selfRating": None,
            }
            field_map = {
                "status": "status", "started_at": "startedAt", "completed_at": "completedAt",
                "user_note": "userNote", "completion_evidence": "completionEvidence",
                "self_rating": "selfRating",
            }
            for source, target in field_map.items():
                if source in updates:
                    value = updates[source]
                    if isinstance(value, datetime):
                        value = value.isoformat()
                    current[target] = value

            status = str(current.get("status") or "pending")
            if status == "completed":
                current["startedAt"] = current.get("startedAt") or now
                current["completedAt"] = current.get("completedAt") or now
            elif status == "in_progress":
                current["startedAt"] = current.get("startedAt") or now
                current["completedAt"] = None
            elif status == "pending":
                current["startedAt"] = None
                current["completedAt"] = None
            else:
                current["completedAt"] = None

            connection.execute(
                """INSERT INTO growth_action_progress(
                run_id,action_id,interview_id,status,started_at,completed_at,user_note,
                completion_evidence,self_rating,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,action_id) DO UPDATE SET
                interview_id=excluded.interview_id,status=excluded.status,
                started_at=excluded.started_at,completed_at=excluded.completed_at,
                user_note=excluded.user_note,completion_evidence=excluded.completion_evidence,
                self_rating=excluded.self_rating,updated_at=excluded.updated_at""",
                (
                    run_id, action_id, interview_id, status,
                    current.get("startedAt"), current.get("completedAt"),
                    current.get("userNote") or "", current.get("completionEvidence") or "",
                    current.get("selfRating"), now,
                ),
            )
            saved = connection.execute(
                "SELECT * FROM growth_action_progress WHERE run_id=? AND action_id=?",
                (run_id, action_id),
            ).fetchone()
        return self._growth_action_progress_dict(saved)

    def create_or_get_practice_session(
        self,
        *,
        interview_id: str,
        run_id: str,
        action_id: str,
        mode: str,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM practice_sessions WHERE run_id=? AND action_id=? AND mode=?",
                (run_id, action_id, mode),
            ).fetchone()
            created = row is None
            if created:
                session_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO practice_sessions(
                    id,interview_id,run_id,action_id,mode,status,brief_json,draft_text,
                    error_code,error_message,created_at,updated_at)
                    VALUES(?,?,?,?,?,'generating','{}','','','',?,?)""",
                    (session_id, interview_id, run_id, action_id, mode, now, now),
                )
            else:
                session_id = str(row["id"])
        return self.get_practice_session(session_id), created

    def get_practice_session(self, session_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM practice_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not row:
                raise KeyError(session_id)
            attempts = connection.execute(
                "SELECT * FROM practice_attempts WHERE session_id=? ORDER BY attempt_no",
                (session_id,),
            ).fetchall()
        result = self._practice_session_dict(row)
        result["attempts"] = [self._practice_attempt_dict(item) for item in attempts]
        return result

    def update_practice_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "status": "status", "brief": "brief_json", "draftText": "draft_text",
            "errorCode": "error_code", "errorMessage": "error_message",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, column in allowed.items():
            if key not in updates:
                continue
            value = updates[key]
            if key == "brief":
                value = json.dumps(value or {}, ensure_ascii=False)
            assignments.append(f"{column}=?")
            values.append(value)
        if not assignments:
            return self.get_practice_session(session_id)
        assignments.append("updated_at=?")
        values.append(utc_now())
        values.append(session_id)
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE practice_sessions SET {','.join(assignments)} WHERE id=?",
                tuple(values),
            )
            if not cursor.rowcount:
                raise KeyError(session_id)
        return self.get_practice_session(session_id)

    def create_practice_attempt(
        self,
        session_id: str,
        *,
        response_text: str,
        self_rating: int | None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self.connect() as connection:
            session = connection.execute(
                "SELECT id FROM practice_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not session:
                raise KeyError(session_id)
            row = connection.execute(
                "SELECT COALESCE(MAX(attempt_no),0)+1 FROM practice_attempts WHERE session_id=?",
                (session_id,),
            ).fetchone()
            attempt_no = int(row[0])
            attempt_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO practice_attempts(
                id,session_id,attempt_no,response_text,self_rating,status,review_json,
                error_code,error_message,created_at,updated_at)
                VALUES(?,?,?,?,?,'reviewing','{}','','',?,?)""",
                (attempt_id, session_id, attempt_no, response_text, self_rating, now, now),
            )
            connection.execute(
                """UPDATE practice_sessions SET status='reviewing',draft_text='',
                error_code='',error_message='',updated_at=? WHERE id=?""",
                (now, session_id),
            )
        return self.get_practice_attempt(attempt_id)

    def get_practice_attempt(self, attempt_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM practice_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
        if not row:
            raise KeyError(attempt_id)
        return self._practice_attempt_dict(row)

    def update_practice_attempt(self, attempt_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "status": "status", "review": "review_json",
            "errorCode": "error_code", "errorMessage": "error_message",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, column in allowed.items():
            if key not in updates:
                continue
            value = updates[key]
            if key == "review":
                value = json.dumps(value or {}, ensure_ascii=False)
            assignments.append(f"{column}=?")
            values.append(value)
        assignments.append("updated_at=?")
        values.append(utc_now())
        values.append(attempt_id)
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE practice_attempts SET {','.join(assignments)} WHERE id=?",
                tuple(values),
            )
            if not cursor.rowcount:
                raise KeyError(attempt_id)
        return self.get_practice_attempt(attempt_id)

    def get_practice_action_summaries(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self.connect() as connection:
            sessions = connection.execute(
                "SELECT * FROM practice_sessions WHERE run_id=? ORDER BY updated_at",
                (run_id,),
            ).fetchall()
            attempts = connection.execute(
                """SELECT a.* FROM practice_attempts a
                JOIN practice_sessions s ON s.id=a.session_id
                WHERE s.run_id=? ORDER BY a.created_at""",
                (run_id,),
            ).fetchall()
        by_session: dict[str, list[dict[str, Any]]] = {}
        for row in attempts:
            item = self._practice_attempt_dict(row)
            by_session.setdefault(item["sessionId"], []).append(item)
        summaries: dict[str, dict[str, Any]] = {}
        for row in sessions:
            session = self._practice_session_dict(row)
            action_id = session["actionId"]
            session_attempts = by_session.get(session["id"], [])
            current = summaries.setdefault(action_id, {
                "practiceCount": 0, "latestPracticeStatus": "",
                "latestPracticeSessionId": "", "latestPracticeResult": "",
            })
            current["practiceCount"] += len(session_attempts)
            current["latestPracticeStatus"] = session["status"]
            current["latestPracticeSessionId"] = session["id"]
            latest = session_attempts[-1] if session_attempts else None
            if latest and latest.get("status") == "reviewed":
                review = latest.get("review") or {}
                results = review.get("rubricResults") or []
                if review.get("completionRecommended"):
                    current["latestPracticeResult"] = "met"
                elif any(item.get("status") in {"met", "partially_met"} for item in results):
                    current["latestPracticeResult"] = "partially_met"
                else:
                    current["latestPracticeResult"] = "not_met"
            elif latest:
                current["latestPracticeResult"] = latest.get("status", "")
            elif session.get("draftText"):
                current["latestPracticeResult"] = "draft"
        return summaries

    def get_growth_trends(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT g.*, i.company, i.position, i.interview_date,
                COALESCE(r.updated_at,g.created_at) AS report_generated_at
                FROM growth_snapshots g JOIN interviews i ON i.id=g.interview_id
                LEFT JOIN review_runs r ON r.id=g.run_id
                ORDER BY g.created_at"""
            ).fetchall()
        return [
            {
                **dict(row),
                "scores": json.loads(row["scores_json"]),
                "weakDimensions": json.loads(row["weak_dimensions_json"]),
                "actionItems": self.merge_growth_action_progress(
                    row["run_id"], json.loads(row["action_items_json"]),
                ),
            }
            for row in rows
        ]

    def get_growth_candidates(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT i.id AS interview_id,i.company,i.position,i.round,i.interview_date,
                r.id AS run_id,r.metrics_json,r.updated_at AS completed_at,
                EXISTS(SELECT 1 FROM growth_snapshots g WHERE g.interview_id=i.id) AS already_added
                FROM interviews i JOIN review_runs r ON r.id=i.latest_run_id
                WHERE i.status='COMPLETED' AND r.status='COMPLETED'
                ORDER BY CASE WHEN i.interview_date='' THEN 1 ELSE 0 END,i.interview_date DESC,r.updated_at DESC"""
            ).fetchall()
        candidates = []
        for row in rows:
            metrics = json.loads(row["metrics_json"] or "{}")
            report = metrics.get("report") or {}
            scores = report.get("overallScores") or {}
            if not scores or float(scores.get("overall") or 0) <= 0:
                continue
            candidates.append({
                "interviewId": row["interview_id"], "runId": row["run_id"], "company": row["company"],
                "position": row["position"], "round": row["round"], "interviewDate": row["interview_date"],
                "completedAt": row["completed_at"], "scores": scores, "alreadyAdded": bool(row["already_added"]),
            })
        return candidates

    def import_growth_snapshots(self, interview_ids: Iterable[str]) -> dict[str, Any]:
        requested = list(dict.fromkeys(interview_ids))
        added_ids: list[str] = []
        existing_ids: list[str] = []
        unavailable_ids: list[str] = []
        with self._lock, self.connect() as connection:
            for interview_id in requested:
                existing = connection.execute(
                    "SELECT id FROM growth_snapshots WHERE interview_id=? LIMIT 1", (interview_id,)
                ).fetchone()
                if existing:
                    existing_ids.append(interview_id)
                    continue
                row = connection.execute(
                    """SELECT i.id AS interview_id,r.id AS run_id,r.metrics_json
                    FROM interviews i JOIN review_runs r ON r.id=i.latest_run_id
                    WHERE i.id=? AND i.status='COMPLETED' AND r.status='COMPLETED'""",
                    (interview_id,),
                ).fetchone()
                if not row:
                    unavailable_ids.append(interview_id)
                    continue
                metrics = json.loads(row["metrics_json"] or "{}")
                report = metrics.get("report") or {}
                scores = report.get("overallScores") or {}
                if not scores or float(scores.get("overall") or 0) <= 0:
                    unavailable_ids.append(interview_id)
                    continue
                weak = sorted(
                    (key for key in scores if key != "overall"),
                    key=lambda key: float(scores.get(key) or 0),
                )[:2]
                connection.execute(
                    "INSERT INTO growth_snapshots VALUES(?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()), interview_id, row["run_id"], json.dumps(scores),
                        json.dumps(weak, ensure_ascii=False),
                        json.dumps(report.get("actionItems") or [], ensure_ascii=False), utc_now(),
                    ),
                )
                added_ids.append(interview_id)
        return {
            "requestedCount": len(requested), "addedCount": len(added_ids),
            "alreadyExistsCount": len(existing_ids), "unavailableCount": len(unavailable_ids),
            "addedInterviewIds": added_ids, "alreadyExistsInterviewIds": existing_ids,
            "unavailableInterviewIds": unavailable_ids,
        }

    def delete_growth_snapshot(self, snapshot_id: str) -> bool:
        with self._lock, self.connect() as connection:
            cursor = connection.execute("DELETE FROM growth_snapshots WHERE id=?", (snapshot_id,))
        return cursor.rowcount > 0

    def delete_growth_snapshots(self, snapshot_ids: Iterable[str]) -> int:
        unique_ids = list(dict.fromkeys(snapshot_ids))
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM growth_snapshots WHERE id IN ({placeholders})",
                tuple(unique_ids),
            )
        return cursor.rowcount

    @staticmethod
    def _growth_action_progress_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "runId": row["run_id"], "actionId": row["action_id"],
            "interviewId": row["interview_id"], "status": row["status"],
            "startedAt": row["started_at"], "completedAt": row["completed_at"],
            "userNote": row["user_note"], "completionEvidence": row["completion_evidence"],
            "selfRating": row["self_rating"], "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _practice_session_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "interviewId": row["interview_id"],
            "runId": row["run_id"], "actionId": row["action_id"],
            "mode": row["mode"], "status": row["status"],
            "brief": json.loads(row["brief_json"] or "{}"),
            "draftText": row["draft_text"], "errorCode": row["error_code"],
            "errorMessage": row["error_message"], "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _practice_attempt_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "sessionId": row["session_id"],
            "attemptNo": int(row["attempt_no"]), "responseText": row["response_text"],
            "selfRating": row["self_rating"], "status": row["status"],
            "review": json.loads(row["review_json"] or "{}"),
            "errorCode": row["error_code"], "errorMessage": row["error_message"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

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
            "confidenceScore": float(row["confidence_score"]), "rawConfidenceScore": float(row["raw_confidence_score"]),
            "confidenceDetails": json.loads(row["confidence_details_json"] or "{}"),
            "confirmationReasons": json.loads(row["confirmation_reasons_json"] or "[]"),
            "parseMethod": row["parse_method"],
        }

    @staticmethod
    def _segment_dict(row: sqlite3.Row, atom_ids: list[str] | None = None) -> dict[str, Any]:
        return {
            "id": row["id"], "ordinal": row["order_index"], "rawText": row["raw_text"],
            "normalizedText": row["normalized_text"], "speakerLabel": row["speaker_label"],
            "speakerRole": row["speaker_role"], "startTime": row["start_time"], "endTime": row["end_time"],
            "startChar": row["start_char"], "endChar": row["end_char"], "confidence": row["confidence"],
            "speakerConfidence": row["speaker_confidence"],
            "needsConfirmation": bool(row["needs_confirmation"]), "excluded": bool(row["excluded"]),
            "atomIds": atom_ids or [], "confidenceDetails": json.loads(row["confidence_details_json"] or "{}"),
            "confirmationReasons": json.loads(row["confirmation_reasons_json"] or "[]"),
            "parseMethod": row["parse_method"],
        }

    @staticmethod
    def _atom_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "ordinal": row["order_index"], "rawText": row["raw_text"],
            "startChar": row["start_char"], "endChar": row["end_char"], "startTime": row["start_time"],
            "endTime": row["end_time"], "speakerLabel": row["speaker_label"], "speakerRole": row["speaker_role"],
            "confidence": float(row["confidence"]),
        }
