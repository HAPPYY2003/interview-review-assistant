from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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
            text TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS question_cards (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, order_index INTEGER NOT NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '', question_type TEXT NOT NULL DEFAULT '其他', confidence TEXT NOT NULL DEFAULT 'medium',
            initial_diagnosis_json TEXT NOT NULL DEFAULT '[]', confirmed INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS review_runs (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
            hello_session_id TEXT, error TEXT, metrics_json TEXT NOT NULL DEFAULT '{}', events_json TEXT NOT NULL DEFAULT '[]',
            enable_web_verify INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
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

    def update_interview(self, interview_id: str, **fields: Any) -> None:
        allowed = {"status", "latest_run_id", "job_description", "resume_text", "raw_transcript", "updated_at"}
        values = {key: value for key, value in fields.items() if key in allowed}
        values["updated_at"] = utc_now()
        assignment = ", ".join(f"{key}=?" for key in values)
        with self._lock, self.connect() as connection:
            connection.execute(f"UPDATE interviews SET {assignment} WHERE id=?", (*values.values(), interview_id))

    def add_material(self, interview_id: str, material_type: str, text: str, filename: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        material_id = str(uuid.uuid4())
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO materials VALUES(?,?,?,?,?,?,?)",
                (material_id, interview_id, material_type, filename, text, json.dumps(metadata or {}, ensure_ascii=False), utc_now()),
            )
        column = {"job_description": "job_description", "resume": "resume_text", "transcript": "raw_transcript"}[material_type]
        self.update_interview(interview_id, **{column: text})
        return {"id": material_id, "interviewId": interview_id, "materialType": material_type, "filename": filename, "textLength": len(text)}

    def replace_questions(self, interview_id: str, questions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now()
        with self._lock, self.connect() as connection:
            connection.execute("DELETE FROM question_cards WHERE interview_id=?", (interview_id,))
            for item in questions:
                connection.execute(
                    """INSERT INTO question_cards(id, interview_id, order_index, question, answer, question_type,
                    confidence, initial_diagnosis_json, confirmed, version, created_at, updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item["id"], interview_id, item.get("order", 1), item.get("interviewerQuestion", ""),
                        item.get("candidateAnswer", ""), item.get("questionType", "其他"), item.get("confidence", "medium"),
                        json.dumps(item.get("initialDiagnosis", []), ensure_ascii=False), int(item.get("confirmed", False)),
                        item.get("version", 1), now, now,
                    ),
                )
        return self.get_questions(interview_id)

    def get_questions(self, interview_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM question_cards WHERE interview_id=? ORDER BY order_index", (interview_id,)).fetchall()
        return [self._question_dict(row) for row in rows]

    def confirm_questions(self, interview_id: str) -> None:
        with self._lock, self.connect() as connection:
            connection.execute("UPDATE question_cards SET confirmed=1, updated_at=? WHERE interview_id=?", (utc_now(), interview_id))
        self.update_interview(interview_id, status="WAITING_CONFIRMATION")

    def create_run(self, interview_id: str, enable_web_verify: bool = False) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO review_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, interview_id, "REVIEWING", "queued", None, None, "{}", "[]", int(enable_web_verify), now, now),
            )
        self.update_interview(interview_id, status="REVIEWING", latest_run_id=run_id)
        self.append_event(run_id, "RUN_CREATED", {"phase": "queued", "message": "复盘任务已创建"})
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM review_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        result = dict(row)
        result["events"] = json.loads(result.pop("events_json") or "[]")
        result["metrics"] = json.loads(result.pop("metrics_json") or "{}")
        return result

    def update_run(self, run_id: str, *, status: str | None = None, phase: str | None = None, error: str | None = None, metrics: dict[str, Any] | None = None, hello_session_id: str | None = None) -> None:
        fields: dict[str, Any] = {"updated_at": utc_now()}
        if status is not None: fields["status"] = status
        if phase is not None: fields["phase"] = phase
        if error is not None: fields["error"] = error
        if metrics is not None: fields["metrics_json"] = json.dumps(metrics, ensure_ascii=False)
        if hello_session_id is not None: fields["hello_session_id"] = hello_session_id
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
    def _question_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "order": row["order_index"], "interviewerQuestion": row["question"],
            "candidateAnswer": row["answer"], "questionType": row["question_type"], "confidence": row["confidence"],
            "initialDiagnosis": json.loads(row["initial_diagnosis_json"] or "[]"), "confirmed": bool(row["confirmed"]), "version": row["version"],
        }

