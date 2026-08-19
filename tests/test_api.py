import time
import uuid
import io
import wave

from fastapi.testclient import TestClient

from backend.app.main import app, database, review_service, settings, workflow


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
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["reportSchemaVersion"] == 2
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
        assert started.json()["reportSchemaVersion"] == 2
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
        assert report.json()["reportSchemaVersion"] == 2
        assert report.json()["questions"][0]["evidenceRefs"]
        assert report.json()["questions"][0]["answerLogic"]["steps"]
        assert report.json()["questions"][0]["recommendedAnswer"]["framework"]["type"]
        assert report.json()["interview"]["overallEvaluation"]["score"] == report.json()["interview"]["overallScores"]["overall"]
        assert report.json()["interview"]["capabilityGaps"]
        assert len(report.json()["actions"]) == 3
        assert all(item["gapIds"] and item["successCriterion"] for item in report.json()["actions"])
        assert all("deliverable" not in item for item in report.json()["actions"])
        assert report.json()["interview"]["latestAIMetadata"]["provider"] == "Fixture"
        assert report.json()["artifacts"]

        candidates = client.get("/api/v1/profile/trends/candidates")
        assert candidates.status_code == 200
        candidate = next(
            item for item in candidates.json()["candidates"]
            if item["interviewId"] == interview_id
        )
        assert candidate["alreadyAdded"] is False
        assert candidate["scores"]["overall"] > 0

        imported = client.post(
            "/api/v1/profile/trends/import",
            json={"interviewIds": [interview_id]},
        )
        assert imported.status_code == 200
        assert imported.json()["addedCount"] == 1

        duplicate = client.post(
            "/api/v1/profile/trends/import",
            json={"interviewIds": [interview_id]},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["alreadyExistsCount"] == 1
        matching_snapshots = [
            item for item in client.get("/api/v1/profile/trends").json()["snapshots"]
            if item["interview_id"] == interview_id
        ]
        assert len(matching_snapshots) == 1
        generated_at = report.json()["interview"]["latestAIMetadata"]["generatedAt"]
        assert matching_snapshots[0]["report_generated_at"] == candidate["completedAt"]
        assert matching_snapshots[0]["report_generated_at"] == generated_at
        updated_candidate = next(
            item for item in client.get("/api/v1/profile/trends/candidates").json()["candidates"]
            if item["interviewId"] == interview_id
        )
        assert updated_candidate["alreadyAdded"] is True

        events = client.get(f"/api/v1/runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: RUN_FINISHED" in events.text
        assert "THINKING" not in events.text
        last_event_id = run["events"][-1]["id"]
        resumed_events = client.get(f"/api/v1/runs/{run_id}/events?after={last_event_id - 1}")
        assert f"id: {last_event_id}\n" in resumed_events.text
        assert f"id: {last_event_id - 1}\n" not in resumed_events.text


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


def test_reassigning_the_only_answer_segment_clears_stale_extracted_answer():
    interview_id = f"reassign-{uuid.uuid4()}"
    transcript = "面试官：请介绍项目。\n候选人：我负责推荐策略优化。"
    with TestClient(app) as client:
        assert client.post("/api/v1/interviews", json={"id": interview_id, "rawTranscript": transcript}).status_code == 201
        parsed = client.post(f"/api/v1/interviews/{interview_id}/parse").json()
        for _ in range(80):
            run = client.get(f"/api/v1/parse-runs/{parsed['parseRunId']}").json()
            if run["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert run["status"] == "COMPLETED"
        turn = client.get(f"/api/v1/interviews/{interview_id}/segments").json()["topics"][0]["mainTurn"]
        turn["questionSegmentIds"] = [*turn["questionSegmentIds"], *turn["answerSegmentIds"]]
        turn["answerSegmentIds"] = []

        patched = client.patch(f"/api/v1/interviews/{interview_id}/questions", json={"questions": [turn]})

        assert patched.status_code == 200
        updated = patched.json()["questions"][0]
        assert updated["extractedAnswer"] == ""
        assert updated["candidateAnswer"] == ""


def test_question_patch_preserves_legacy_text_and_rejects_empty_question():
    interview_id = f"legacy-question-{uuid.uuid4()}"
    with TestClient(app) as client:
        assert client.post("/api/v1/interviews", json={"id": interview_id}).status_code == 201
        legacy = {
            "id": f"legacy-topic-{uuid.uuid4()}",
            "interviewerQuestion": "请介绍一个项目。",
            "candidateAnswer": "我负责推荐策略优化。",
            "questionType": "项目经历",
        }

        patched = client.patch(
            f"/api/v1/interviews/{interview_id}/questions",
            json={"questions": [legacy]},
        )

        assert patched.status_code == 200
        assert patched.json()["questions"][0]["interviewerQuestion"] == "请介绍一个项目。"
        legacy["interviewerQuestion"] = ""
        legacy["candidateAnswer"] = ""
        rejected = client.patch(
            f"/api/v1/interviews/{interview_id}/questions",
            json={"questions": [legacy]},
        )
        assert rejected.status_code == 422
        assert "问题原文不能为空" in rejected.json()["detail"]


def test_parse_api_supports_all_transcript_shapes_and_returns_atoms():
    samples = {
        "labeled_lines": "面试官：请介绍项目。\n候选人：我负责推荐策略优化。",
        "unlabeled_lines": "请介绍项目？\n我负责推荐策略优化。",
        "punctuated_stream": "请介绍项目？我负责推荐策略优化。最终点击率提升12%。",
        "raw_stream": "请介绍项目我负责推荐策略优化最后点击率提升百分之十二",
    }
    with TestClient(app) as client:
        for expected_profile, transcript in samples.items():
            interview_id = f"shape-{expected_profile}-{uuid.uuid4()}"
            assert client.post("/api/v1/interviews", json={"id": interview_id, "rawTranscript": transcript}).status_code == 201
            parsed = client.post(f"/api/v1/interviews/{interview_id}/parse").json()
            run = {}
            for _ in range(80):
                run = client.get(f"/api/v1/parse-runs/{parsed['parseRunId']}").json()
                if run["status"] in {"COMPLETED", "FAILED"}:
                    break
                time.sleep(0.05)
            assert run["status"] == "COMPLETED"
            assert run["metrics"]["profileType"] == expected_profile
            result = client.get(f"/api/v1/interviews/{interview_id}/segments?includeAtoms=true").json()
            assert result["atoms"]
            assert result["topics"]
            if expected_profile == "raw_stream":
                turn = result["topics"][0]["mainTurn"]
                assert turn["needsConfirmation"] is True
                assert any(item["code"] == "SOURCE_QUALITY_LOW" for item in turn["confirmationReasons"])


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
        assert report["reportSchemaVersion"] == 2
        assert report["interview"]["capabilityGaps"]
        assert len(report["actions"]) == 3
        assert report["interview"]["latestAIMetadata"]["provider"] == "DeterministicFallback"
        assert report["interview"]["degraded"] is True


def test_report_can_target_completed_run_when_a_newer_run_failed():
    interview_id = f"report-run-{uuid.uuid4()}"
    with TestClient(app) as client:
        interview = database.create_interview({
            "id": interview_id,
            "company": "星河科技",
            "position": "产品经理",
            "raw_transcript": "面试官：请介绍一个项目。\n候选人：我负责实验设计并推动上线。",
        })
        database.replace_questions(interview_id, review_service.parse_transcript(interview["raw_transcript"]))
        database.confirm_questions(interview_id)
        completed_run = database.create_run(
            interview_id,
            agent_mode="fixture",
            input_digest=workflow.input_digest(interview_id),
        )
        workflow.execute(completed_run["id"])
        newer_run = database.create_run(
            interview_id,
            agent_mode="fixture",
            input_digest=workflow.input_digest(interview_id),
        )
        database.update_run(newer_run["id"], status="FAILED", phase="growth_plan", error="测试失败", failure_code="TEST_FAILURE")

        latest_report = client.get(f"/api/v1/interviews/{interview_id}/report")
        targeted_report = client.get(
            f"/api/v1/interviews/{interview_id}/report",
            params={"runId": completed_run["id"]},
        )

        assert latest_report.json()["status"] == "FAILED"
        assert targeted_report.status_code == 200
        assert targeted_report.json()["status"] == "COMPLETED"
        assert targeted_report.json()["run"]["id"] == completed_run["id"]


def test_delete_interview_removes_database_rows_and_private_artifacts():
    interview_id = f"delete-all-{uuid.uuid4()}"
    with TestClient(app) as client:
        database.create_interview({
            "id": interview_id,
            "company": "星河科技",
            "position": "产品经理",
            "raw_transcript": "面试官：请介绍项目。\n候选人：我负责推荐策略。",
        })
        upload_dir = settings.data_dir / "uploads" / interview_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_file = upload_dir / "interview.txt"
        upload_file.write_text("private transcript", encoding="utf-8")
        material = database.add_material(
            interview_id,
            "transcript",
            "private transcript",
            "interview.txt",
            storage_path=str(upload_file),
        )
        parse_run = database.create_parse_run(interview_id, material["id"], "text")
        review_run = database.create_run(interview_id, agent_mode="fixture")
        database.replace_questions(interview_id, [{
            "id": f"question-{uuid.uuid4()}",
            "order": 1,
            "interviewerQuestion": "请介绍项目。",
            "candidateAnswer": "我负责推荐策略。",
            "questionType": "项目经历",
        }])
        database.save_growth_snapshot(interview_id, review_run["id"], {"overall": 7.0}, [], [])

        parse_artifact = settings.data_dir / "parse-runs" / parse_run["id"]
        parse_artifact.mkdir(parents=True, exist_ok=True)
        (parse_artifact / "questions.json").write_text("private", encoding="utf-8")
        trace_file = settings.data_dir / "traces" / "trace-delete-test.jsonl"
        trace_file.write_text(f'{{"interview_id":"{interview_id}"}}', encoding="utf-8")
        session_file = settings.data_dir / "sessions" / "session-delete-test.json"
        session_file.write_text(f'{{"run_id":"{review_run["id"]}"}}', encoding="utf-8")

        deleted = client.delete(f"/api/v1/interviews/{interview_id}")

        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        assert not upload_dir.exists()
        assert not parse_artifact.exists()
        assert not trace_file.exists()
        assert not session_file.exists()
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM interviews WHERE id=?", (interview_id,)).fetchone()[0] == 0
            for table in ("materials", "question_cards", "parse_runs", "review_runs", "growth_snapshots"):
                assert connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE interview_id=?",
                    (interview_id,),
                ).fetchone()[0] == 0


def test_growth_snapshot_delete_keeps_interview_record():
    interview_id = f"trend-delete-{uuid.uuid4()}"
    with TestClient(app) as client:
        database.create_interview({
            "id": interview_id,
            "company": "星河科技",
            "position": "产品经理",
            "interview_date": "2026-08-03",
        })
        run = database.create_run(interview_id, agent_mode="fixture")
        database.save_growth_snapshot(
            interview_id,
            run["id"],
            {"overall": 7.2, "relevance": 7.5, "structure": 6.0, "evidence": 7.5, "depth": 7.5, "roleFit": 7.5},
            ["structure"],
            [],
        )
        snapshot = next(
            item for item in client.get("/api/v1/profile/trends").json()["snapshots"]
            if item["interview_id"] == interview_id
        )
        assert snapshot["created_at"]

        deleted = client.delete(f"/api/v1/profile/trends/{snapshot['id']}")

        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        assert database.get_interview(interview_id) is not None
        assert all(
            item["id"] != snapshot["id"]
            for item in client.get("/api/v1/profile/trends").json()["snapshots"]
        )
        assert client.delete(f"/api/v1/profile/trends/{snapshot['id']}").status_code == 404

        for overall in (6.8, 8.0):
            database.save_growth_snapshot(
                interview_id,
                run["id"],
                {"overall": overall, "relevance": 7.5, "structure": 6.0, "evidence": 7.5, "depth": 7.5, "roleFit": 7.5},
                ["structure"],
                [],
            )
        snapshot_ids = [
            item["id"]
            for item in client.get("/api/v1/profile/trends").json()["snapshots"]
            if item["interview_id"] == interview_id
        ]
        batch_deleted = client.post(
            "/api/v1/profile/trends/delete-batch",
            json={"snapshotIds": snapshot_ids},
        )

        assert batch_deleted.status_code == 200
        assert batch_deleted.json() == {"requestedCount": 2, "deletedCount": 2}
        assert database.get_interview(interview_id) is not None
        assert not any(
            item["interview_id"] == interview_id
            for item in client.get("/api/v1/profile/trends").json()["snapshots"]
        )

        database.save_growth_snapshot(
            interview_id,
            run["id"],
            {"overall": 7.6, "relevance": 7.5, "structure": 7.5, "evidence": 7.5, "depth": 7.5, "roleFit": 7.5},
            [],
            [],
        )
        assert client.delete(f"/api/v1/interviews/{interview_id}").status_code == 200
        with database.connect() as connection:
            remaining_snapshots = connection.execute(
                "SELECT COUNT(*) FROM growth_snapshots WHERE interview_id=?",
                (interview_id,),
            ).fetchone()[0]
        assert remaining_snapshots == 0


def test_growth_snapshot_tracks_latest_review_score():
    interview_id = f"trend-sync-{uuid.uuid4()}"
    database.create_interview({"id": interview_id, "company": "星河科技", "position": "产品经理"})
    first_run = database.create_run(interview_id, agent_mode="fixture")
    database.update_run(first_run["id"], status="COMPLETED", phase="completed")
    database.save_growth_snapshot(
        interview_id,
        first_run["id"],
        {"overall": 5.2, "relevance": 6.0, "structure": 4.0, "evidence": 5.0, "depth": 5.0, "roleFit": 6.0},
        ["structure", "evidence"],
        [],
    )

    latest_run = database.create_run(interview_id, agent_mode="fixture")
    latest_scores = {
        "overall": 7.4,
        "relevance": 7.5,
        "structure": 7.0,
        "evidence": 7.5,
        "depth": 7.5,
        "roleFit": 7.5,
    }

    assert database.sync_growth_snapshot(interview_id, latest_run["id"], latest_scores, []) == 1
    snapshot = next(item for item in database.get_growth_trends() if item["interview_id"] == interview_id)
    assert snapshot["run_id"] == latest_run["id"]
    assert snapshot["scores"] == latest_scores
    assert snapshot["weakDimensions"] == ["structure", "relevance"]
