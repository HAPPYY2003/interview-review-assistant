from __future__ import annotations

import io
import sqlite3
import wave
from pathlib import Path

import httpx
import pytest

from backend.app.services.audio import (
    AudioInspectionError,
    DeepgramTranscriptionProvider,
    TranscriptionError,
    deepgram_segments,
    inspect_audio,
)
from backend.app.services.transcript import (
    build_question_cards,
    chunk_segments,
    confidence_label,
    map_speaker_roles,
    merge_worker_cards,
    segment_text,
    validate_question_cards,
    validate_segments,
)


def test_confirmation_flag_caps_display_confidence_at_medium():
    assert confidence_label(0.85) == "high"
    assert confidence_label(0.85, needs_confirmation=True) == "medium"
from backend.app.database import Database
from backend.app.tools.parse_tools import build_parse_tools


def wav_bytes(seconds: float = 0.1, rate: int = 8_000) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return stream.getvalue()


def test_audio_inspection_uses_magic_bytes_hash_and_duration(tmp_path: Path, settings_factory):
    path = tmp_path / "sample.wav"
    path.write_bytes(wav_bytes(0.25))
    inspected = inspect_audio(path, "sample.wav", settings_factory())

    assert inspected.mime_type in {"audio/wav", "audio/x-wav"}
    assert inspected.duration_seconds == pytest.approx(0.25, abs=0.01)
    assert len(inspected.sha256) == 64

    with pytest.raises(AudioInspectionError, match="仅支持"):
        inspect_audio(path, "sample.exe", settings_factory())


def test_deepgram_segments_validation_and_speaker_mapping():
    payload = {
        "results": {
            "utterances": [
                {"speaker": 0, "start": 0.0, "end": 1.0, "transcript": "请介绍项目？", "words": [{"confidence": 0.96, "speaker_confidence": 0.93}]},
                {"speaker": 1, "start": 1.0, "end": 2.0, "transcript": "我负责实验设计。", "words": [{"confidence": 0.92}]},
                {"speaker": 2, "start": 2.0, "end": 3.0, "transcript": "具体结果是什么？", "words": [{"confidence": 0.9}]},
                {"speaker": 1, "start": 3.0, "end": 4.0, "transcript": "提升了 12%。", "words": [{"confidence": 0.62}]},
                {"speaker": 1, "start": 4.0, "end": 5.0, "transcript": "提升了 12%。", "words": [{"confidence": 0.62}]},
            ]
        }
    }
    segments = deepgram_segments(payload)
    map_speaker_roles(segments)
    result = validate_segments(segments)

    assert segments[0]["speakerRole"] == "interviewer"
    assert segments[0]["speakerConfidence"] == pytest.approx(0.93)
    assert segments[2]["speakerRole"] == "interviewer"
    assert segments[1]["speakerRole"] == "candidate"
    assert segments[3]["needsConfirmation"] is True
    assert segments[4]["excluded"] is True
    assert {issue["code"] for issue in result.issues} >= {"adjacent_duplicate"}


def test_chunk_overlap_merge_and_reference_validation():
    transcript = "\n".join(
        f"{'面试官' if index % 2 == 0 else '候选人'}：{'请说明方案？' if index % 2 == 0 else '这是回答。'}"
        for index in range(90)
    )
    segments = segment_text(transcript)
    chunks = chunk_segments(segments, size=40, overlap=6)
    assert len(chunks) >= 3
    assert set(item["id"] for item in chunks[0]) & set(item["id"] for item in chunks[1])

    cards = build_question_cards(segments[:4])
    assert validate_question_cards(cards, segments[:4]) == []
    broken = [{**cards[0], "answerSegmentIds": [cards[0]["questionSegmentIds"][0]]}]
    assert any("answer_before_question" in error for error in validate_question_cards(broken, segments[:4]))

    higher = {**cards[0], "confidence": "high"}
    lower = {**cards[0], "confidence": "low"}
    assert merge_worker_cards([[lower], [higher]])[0]["confidence"] == "high"


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("failed", request=httpx.Request("POST", "https://example.test"), response=httpx.Response(self.status_code))

    def json(self) -> dict:
        return self._payload


def test_deepgram_retries_once_for_429_and_not_for_auth(monkeypatch, tmp_path: Path, settings_factory):
    path = tmp_path / "audio.wav"
    path.write_bytes(wav_bytes())
    calls: list[int] = []
    statuses = [429, 200]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            calls.append(1)
            status = statuses.pop(0)
            return _FakeResponse(status, {"results": {"utterances": []}})

    monkeypatch.setattr("backend.app.services.audio.httpx.Client", FakeClient)
    provider = DeepgramTranscriptionProvider(settings_factory(deepgram_api_key="test-key"))
    _, retries = provider.transcribe(path, "audio/wav")
    assert retries == 1
    assert len(calls) == 2

    statuses[:] = [401, 200]
    calls.clear()
    with pytest.raises(TranscriptionError) as error:
        provider.transcribe(path, "audio/wav")
    assert error.value.status_code == 401
    assert len(calls) == 1


def test_parse_tools_reject_out_of_scope_ids():
    class Context:
        material_id = "material-1"
        parse_run_id = "run-1"

        def __init__(self):
            self.calls: list[str] = []

        def inspect(self):
            self.calls.append("inspect")
            return {}

        def transcribe(self):
            self.calls.append("transcribe")
            return {}

        def validate(self):
            self.calls.append("validate")
            return {}

        def structure(self):
            self.calls.append("structure")
            return {}

        def submit(self):
            self.calls.append("submit")
            return {"accepted": True}

    context = Context()
    tools, _ = build_parse_tools(context)
    inspect = next(tool for tool in tools if tool.name == "InspectMaterial")
    validate = next(tool for tool in tools if tool.name == "TranscriptValidation")
    inspect.run({"material_id": "another-material"})
    validate.run({"parse_run_id": "another-run"})
    assert context.calls == []
    assert [item.name for item in inspect.get_parameters()] == ["material_id"]
    assert [item.name for item in validate.get_parameters()] == ["parse_run_id"]


def test_legacy_question_cards_migrate_to_main_topics(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE interviews (
                id TEXT PRIMARY KEY, company TEXT NOT NULL DEFAULT '', position TEXT NOT NULL DEFAULT '',
                round TEXT NOT NULL DEFAULT '', interview_date TEXT, review_goal TEXT NOT NULL DEFAULT '',
                analysis_mode TEXT NOT NULL DEFAULT 'full_context', status TEXT NOT NULL DEFAULT 'DRAFT',
                job_description TEXT NOT NULL DEFAULT '', resume_text TEXT NOT NULL DEFAULT '',
                raw_transcript TEXT NOT NULL DEFAULT '', latest_run_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE question_cards (
                id TEXT PRIMARY KEY, interview_id TEXT NOT NULL, order_index INTEGER NOT NULL, question TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '', question_type TEXT NOT NULL DEFAULT '其他', confidence TEXT NOT NULL DEFAULT 'medium',
                initial_diagnosis_json TEXT NOT NULL DEFAULT '[]', confirmed INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO interviews(id, created_at, updated_at) VALUES('legacy-interview','now','now');
            INSERT INTO question_cards(id, interview_id, order_index, question, answer, created_at, updated_at)
            VALUES('legacy-question','legacy-interview',1,'原问题','原回答','now','now');
            """
        )
    database = Database(db_path)
    database.initialize()
    topic = database.get_question_topics("legacy-interview")[0]
    assert topic["mainTurn"]["turnType"] == "main"
    assert topic["mainTurn"]["topicRootId"] == "legacy-question"
    assert topic["mainTurn"]["extractedQuestion"] == "原问题"
    with database.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(transcript_segments)")}
    assert "speaker_confidence" in columns
