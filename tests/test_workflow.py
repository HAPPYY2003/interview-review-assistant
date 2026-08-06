from backend.app.database import Database
from backend.app.services.evidence import EvidenceReviewService
from backend.app.services.knowledge import KnowledgeBase
from backend.app.services.workflow import ReviewWorkflow


TRANSCRIPT = """面试官：请介绍一个最有挑战的项目。
候选人：我负责推荐策略优化，推动四轮实验，点击率提升 12.6%。
面试官：你的关键决策是什么？
候选人：我提出按生命周期分层，并推动算法和研发共同落地。"""


def build_workflow(settings):
    database = Database(settings.database_path)
    database.initialize()
    review_service = EvidenceReviewService(KnowledgeBase(settings.knowledge_dir))
    return database, review_service, ReviewWorkflow(database, review_service, settings)


def test_complete_fixture_workflow_and_growth_memory(settings_factory):
    settings = settings_factory()
    database, service, workflow = build_workflow(settings)
    interview = database.create_interview({
        "id": "interview-1",
        "company": "星河科技",
        "position": "产品经理",
        "analysis_mode": "full_context",
        "job_description": "负责数据分析、实验设计和跨团队推动。",
        "resume_text": "推动四轮实验，点击率提升 12.6%。",
        "raw_transcript": TRANSCRIPT,
    })
    questions = service.parse_transcript(TRANSCRIPT)
    assert len(questions) == 2
    database.replace_questions(interview["id"], questions)
    database.confirm_questions(interview["id"])
    run = database.create_run(interview["id"])
    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["hello_session_id"].startswith("fixture-")
    assert any(event["type"] == "EVIDENCE_VALIDATED" for event in completed["events"])

    report = workflow.report(interview["id"])
    assert report["status"] == "COMPLETED"
    assert len(report["questions"]) == 2
    assert report["interview"]["overallScores"]["overall"] > 0
    assert database.get_growth_trends()

    transcript_refs = [ref for question in report["questions"] for ref in question["evidenceRefs"] if ref["sourceType"] == "transcript"]
    assert transcript_refs
    assert all(ref["verified"] for ref in transcript_refs)
    assert all(ref["quote"] in TRANSCRIPT for ref in transcript_refs)


def test_question_edit_invalidates_previous_run(settings_factory):
    settings = settings_factory()
    database, service, _ = build_workflow(settings)
    interview = database.create_interview({"id": "interview-2", "raw_transcript": TRANSCRIPT})
    questions = service.parse_transcript(TRANSCRIPT)
    database.replace_questions(interview["id"], questions)
    database.confirm_questions(interview["id"])
    run = database.create_run(interview["id"])
    assert database.get_interview(interview["id"])["latest_run_id"] == run["id"]
    questions[0]["interviewerQuestion"] = "请重新介绍这个项目。"
    database.replace_questions(interview["id"], questions)
    database.update_interview(interview["id"], status="WAITING_CONFIRMATION", latest_run_id=None)
    updated = database.get_interview(interview["id"])
    assert updated["status"] == "WAITING_CONFIRMATION"
    assert updated["latest_run_id"] is None

