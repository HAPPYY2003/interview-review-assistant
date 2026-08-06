import time
import uuid

from fastapi.testclient import TestClient

from backend.app.main import app


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
        assert parsed.status_code == 200
        questions = parsed.json()["questions"]
        assert len(questions) == 1

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

        report = client.get(f"/api/v1/interviews/{interview_id}/report")
        assert report.status_code == 200
        assert report.json()["status"] == "COMPLETED"
        assert report.json()["questions"][0]["evidenceRefs"]

        events = client.get(f"/api/v1/runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: RUN_FINISHED" in events.text
        assert "THINKING" not in events.text

        trends = client.get("/api/v1/profile/trends")
        assert trends.status_code == 200
        assert trends.json()["count"] >= 1

