import time
import uuid
import io
import wave

from fastapi.testclient import TestClient

from backend.app.main import app, database, review_service, workflow


def _wav_bytes() -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(b"\x00\x00" * 800)
    return stream.getvalue()


def test_v1_api_end_to_end_and_sse_contract():
    interview_id = f"api-{uuid.uuid4()}"
    payload = {
        "id": interview_id,
        "company": "星河科技",
        "position": "产品经理",
        "round": "业务二面",
        "interviewDate": "2026-08-03",
        "reviewGoal": "检查证据完整性",
        "analysisMode": "full_context",
        "jobDescription": "负责数据分析、实验设计和跨团队推动。",
        "resumeText": "推动四轮实验，点击率提升 12.6%。",
        "rawTranscript": "面试官：请介绍一个项目。\n候选人：我推动四轮实验，点击率提升 12.6%。",
    }
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        created = client.post("/api/v1/interviews", json=payload)
        assert created.status_code == 201

        parsed = client.post(f"/api/v1/interviews/{interview_id}/parse")
        assert parsed.status_code == 202
        parse_run_id = parsed.json()["parseRunId"]
        parse_run = {}
        for _ in range(80):
            parse_run = client.get(f"/api/v1/parse-runs/{parse_run_id}").json()
            if parse_run["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert parse_run["status"] == "COMPLETED"
        parsed_result = client.get(f"/api/v1/interviews/{interview_id}/segments").json()
        questions = [topic["mainTurn"] for topic in parsed_result["topics"]]
        assert len(questions) == 1
        assert parsed_result["segments"][0]["startChar"] is not None

        parse_events = client.get(f"/api/v1/parse-runs/{parse_run_id}/events")
        assert "event: PARSE_FINISHED" in parse_events.text
        assert "THINKING" not in parse_events.text
        resumed_parse_events = client.get(
            f"/api/v1/parse-runs/{parse_run_id}/events",
            headers={"Last-Event-ID": "9"},
        )
        assert "id: 10" in resumed_parse_events.text
        assert "id: 1\n" not in resumed_parse_events.text

        patched = client.patch(f"/api/v1/interviews/{interview_id}/questions", json={"questions": questions})
        assert patched.status_code == 200
        assert patched.json()["invalidatedPreviousReport"] is True
        assert client.post(f"/api/v1/interviews/{interview_id}/confirm").status_code == 200

        started = client.post(f"/api/v1/interviews/{interview_id}/review-runs", json={"enableWebVerify": False})
        assert started.status_code == 202
        run_id = started.json()["id"]

        run = {}
        for _ in range(50):
            run = client.get(f"/api/v1/runs/{run_id}").json()
            if run["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert run["status"] == "COMPLETED"
        assert [event["type"] for event in run["events"]][-1] == "RUN_FINISHED"
        assert run["agent_mode"] == "fixture"
        assert run["progress"]["completedTopics"] == 1
        assert {item["phase"] for item in run["artifacts"]} == {
            "evidence_review", "reflection_audit", "growth_plan",
        }

        report = client.get(f"/api/v1/interviews/{interview_id}/report")
        assert report.status_code == 200
        assert report.json()["status"] == "COMPLETED"
        assert report.json()["questions"][0]["evidenceRefs"]
        assert report.json()["interview"]["latestAIMetadata"]["provider"] == "Fixture"
        assert report.json()["artifacts"]

        events = client.get(f"/api/v1/runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: RUN_FINISHED" in events.text
        assert "THINKING" not in events.text


def test_quick_review_preserves_unconfirmed_question_state():
    interview_id = f"quick-{uuid.uuid4()}"
    payload = {
        "id": interview_id,
        "company": "星河科技",
        "position": "产品经理",
        "jobDescription": "负责数据分析和实验设计。",
        "resumeText": "负责推荐策略实验。",
        "rawTranscript": "面试官：请介绍一个项目。\n候选人：我负责推荐策略实验，点击率提升 12%。",
    }
    with TestClient(app) as client:
        assert client.post("/api/v1/interviews", json=payload).status_code == 201
        parsed = client.post(f"/api/v1/interviews/{interview_id}/parse").json()
        for _ in range(80):
            parse_run = client.get(f"/api/v1/parse-runs/{parsed['parseRunId']}").json()
            if parse_run["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert parse_run["status"] == "COMPLETED"
        topics = client.get(f"/api/v1/interviews/{interview_id}/segments").json()["topics"]
        assert topics and topics[0]["mainTurn"]["confirmed"] is False

        blocked = client.post(
            f"/api/v1/interviews/{interview_id}/review-runs",
            json={"reviewMode": "quick"},
        )
        assert blocked.status_code == 409

        started = client.post(
            f"/api/v1/interviews/{interview_id}/review-runs",
            json={
                "reviewMode": "quick",
                "acknowledgeUnreviewed": True,
                "acknowledgeUnresolved": True,
            },
        )
        assert started.status_code == 202
        assert started.json()["reviewMode"] == "quick"
        run_id = started.json()["id"]
        for _ in range(50):
            run = client.get(f"/api/v1/runs/{run_id}").json()
            if run["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert run["status"] == "COMPLETED"
        assert run["review_mode"] == "quick"
        topics = client.get(f"/api/v1/interviews/{interview_id}/segments").json()["topics"]
        assert topics[0]["mainTurn"]["confirmed"] is False
        report = client.get(f"/api/v1/interviews/{interview_id}/report").json()
        assert report["interview"]["reviewMode"] == "quick"


def test_audio_upload_playback_and_confirmation_gate():
    interview_id = f"audio-{uuid.uuid4()}"
    with TestClient(app) as client:
        created = client.post("/api/v1/interviews", json={"id": interview_id, "company": "示例", "position": "产品"})
        assert created.status_code == 201

        denied = client.post(
            f"/api/v1/interviews/{interview_id}/materials",
            data={"material_type": "transcript_audio"},
            files={"file": ("sample.wav", _wav_bytes(), "audio/wav")},
        )
        assert denied.status_code == 422
        uploaded = client.post(
            f"/api/v1/interviews/{interview_id}/materials",
            data={"material_type": "transcript_audio", "cloud_consent": "true"},
            files={"file": ("sample.wav", _wav_bytes(), "audio/wav")},
        )
        assert uploaded.status_code == 200
        material = uploaded.json()
        playback = client.get(f"/api/v1/materials/{material['id']}/content")
        assert playback.status_code == 200
        assert playback.content.startswith(b"RIFF")
        assert client.post(f"/api/v1/interviews/{interview_id}/parse").status_code == 409

        text = client.post(
            f"/api/v1/interviews/{interview_id}/materials/text",
            json={"material_type": "transcript", "text": "请介绍项目？\n我负责实验设计并推动上线。"},
        )
        assert text.status_code == 200
        parse = client.post(f"/api/v1/interviews/{interview_id}/parse").json()
        for _ in range(80):
            run = client.get(f"/api/v1/parse-runs/{parse['parseRunId']}").json()
            if run["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert run["status"] == "COMPLETED"
        assert client.post(f"/api/v1/interviews/{interview_id}/confirm").status_code == 409
        assert client.post(
            f"/api/v1/interviews/{interview_id}/confirm",
            json={"acknowledgeUnresolved": True},
        ).status_code == 200

        trends = client.get("/api/v1/profile/trends")
        assert trends.status_code == 200
        assert trends.json()["count"] >= 1


def test_failed_agent_run_only_falls_back_after_explicit_request():
    interview_id = f"fallback-{uuid.uuid4()}"
    with TestClient(app) as client:
        interview = database.create_interview({
            "id": interview_id,
            "company": "星河科技",
            "position": "产品经理",
            "raw_transcript": "面试官：请介绍一个项目。\n候选人：我负责实验设计并推动上线。",
        })
        database.replace_questions(interview_id, review_service.parse_transcript(interview["raw_transcript"]))
        database.confirm_questions(interview_id)
        run = database.create_run(
            interview_id,
            agent_mode="helloagents",
            input_digest=workflow.input_digest(interview_id),
        )
        database.update_run(run["id"], status="FAILED", phase="failed", error="模型超时", failure_code="MODEL_TIMEOUT")
        database.update_interview(interview_id, status="FAILED")

        before = client.get(f"/api/v1/interviews/{interview_id}/report").json()
        assert before["status"] == "FAILED"
        requested = client.post(f"/api/v1/runs/{run['id']}/fallback")
        assert requested.status_code == 202

        completed = {}
        for _ in range(80):
            completed = client.get(f"/api/v1/runs/{run['id']}").json()
            if completed["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert completed["status"] == "COMPLETED"
        assert completed["agent_mode"] == "deterministic_fallback"
        assert completed["degraded"] is True
        assert any(event["type"] == "FALLBACK_REQUESTED" for event in completed["events"])

        report = client.get(f"/api/v1/interviews/{interview_id}/report").json()
        assert report["interview"]["latestAIMetadata"]["provider"] == "DeterministicFallback"
        assert report["interview"]["degraded"] is True
