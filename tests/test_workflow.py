import json
import time

import pytest

from backend.app.agents.runtime import AgentRuntimeResult
from backend.app.database import ActiveAgentRunError, Database
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


def test_complete_fixture_workflow_and_manual_growth_import(settings_factory):
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
    assert any(event["type"] == "SUPERVISOR_PLAN_ACCEPTED" for event in completed["events"])
    assert database.get_stage_artifacts(run["id"], accepted_only=True)

    report = workflow.report(interview["id"])
    assert report["status"] == "COMPLETED"
    assert len(report["questions"]) == 2
    assert report["interview"]["overallScores"]["overall"] > 0
    assert database.get_growth_trends() == []
    candidates = database.get_growth_candidates()
    candidate = next(item for item in candidates if item["interviewId"] == interview["id"])
    assert candidate["alreadyAdded"] is False

    imported = database.import_growth_snapshots([interview["id"]])
    assert imported["addedCount"] == 1
    assert imported["alreadyExistsCount"] == 0
    growth = database.get_growth_trends()[0]
    assert growth["interview_id"] == interview["id"]
    assert growth["report_generated_at"] == completed["updated_at"]

    duplicate = database.import_growth_snapshots([interview["id"]])
    assert duplicate["addedCount"] == 0
    assert duplicate["alreadyExistsCount"] == 1
    assert len(database.get_growth_trends()) == 1

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


def test_create_run_reuses_active_review_for_same_interview(settings_factory):
    settings = settings_factory()
    database, service, _ = build_workflow(settings)
    interview = database.create_interview({"id": "single-active-run", "raw_transcript": TRANSCRIPT})
    database.replace_questions(interview["id"], service.parse_transcript(TRANSCRIPT))
    database.confirm_questions(interview["id"])

    first = database.create_run(interview["id"])
    database.update_interview(interview["id"], latest_run_id=None)
    second = database.create_run(interview["id"])

    assert second["id"] == first["id"]
    assert second["reused"] is True
    assert database.get_interview(interview["id"])["latest_run_id"] == first["id"]
    with database.connect() as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM review_runs WHERE interview_id=? AND status IN ('REVIEWING','AUDITING')",
            (interview["id"],),
        ).fetchone()[0]
    assert active_count == 1


def test_only_one_real_agent_run_can_be_active_globally(settings_factory):
    settings = settings_factory()
    database, service, _ = build_workflow(settings)
    first_interview = database.create_interview({"id": "global-agent-first", "raw_transcript": TRANSCRIPT})
    second_interview = database.create_interview({"id": "global-agent-second", "raw_transcript": TRANSCRIPT})
    for interview in (first_interview, second_interview):
        database.replace_questions(interview["id"], service.parse_transcript(TRANSCRIPT))
        database.confirm_questions(interview["id"])

    first = database.create_run(first_interview["id"], agent_mode="helloagents")
    reused = database.create_run(first_interview["id"], agent_mode="helloagents")

    assert reused["id"] == first["id"]
    assert reused["reused"] is True
    with pytest.raises(ActiveAgentRunError) as error:
        database.create_run(second_interview["id"], agent_mode="helloagents")
    assert error.value.run_id == first["id"]
    assert error.value.interview_id == first_interview["id"]

    fixture = database.create_run(second_interview["id"], agent_mode="fixture")
    assert fixture["id"] != first["id"]


def test_topic_payload_excludes_parse_provenance_ids():
    topic = {
        "title": "项目复盘",
        "mainTurn": {
            "id": "topic-1",
            "version": 1,
            "interviewerQuestion": "请介绍项目。",
            "candidateAnswer": "我负责方案设计。",
            "questionType": "项目经历",
            "confirmed": True,
            "needsConfirmation": False,
            "questionSegmentIds": ["U0001"],
            "confidenceDetails": {"evidenceAtomIds": ["A0001"]},
        },
        "followUps": [{
            "id": "follow-up-1",
            "version": 1,
            "interviewerQuestion": "结果如何？",
            "candidateAnswer": "转化率提升。",
            "questionType": "项目经历",
            "confirmed": True,
            "needsConfirmation": False,
            "answerSegmentIds": ["U0002"],
        }],
    }

    payload = ReviewWorkflow._topic_for_agent(topic)

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "A0001" not in serialized
    assert "U0001" not in serialized
    assert "U0002" not in serialized
    assert payload["followUpTurns"][0]["id"] == "follow-up-1"


class ScriptedAgentRuntime:
    def __init__(self, invalid_plan: bool = False, *, fail_topic_id: str | None = None, critical_audits: int = 0):
        self.invalid_plan = invalid_plan
        self.fail_topic_id = fail_topic_id
        self.critical_audits = critical_audits
        self.audit_calls = 0
        self.topic_calls = {}

    def generate_supervisor_plan(self, context):
        steps = ["随意分析"] if self.invalid_plan else ["evidence_review", "reflection_audit", "growth_plan"]
        return AgentRuntimeResult(text=json.dumps({"steps": steps}), metadata={"steps": steps, "duration_seconds": 0.01})

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        by_name = {tool.name: tool for tool in tools}
        if agent_type == "react":
            submit = by_name["SubmitTopicReview"]
            topic = submit.topic
            self.topic_calls[topic["id"]] = self.topic_calls.get(topic["id"], 0) + 1
            if topic["id"] == self.fail_topic_id:
                return AgentRuntimeResult(text="scripted failure", metadata={"duration_seconds": 0.01})
            answer = topic.get("candidateAnswer") or topic.get("extractedAnswer") or "待补充"
            lookup = by_name["EvidenceLookup"]
            match = lookup.run({"source_type": "transcript", "query": answer[:8]}).data["matches"][0]
            evidence_id = match["id"]
            payload = {
                "topicId": topic["id"],
                "topicVersion": topic.get("version", 1),
                "diagnosis": "这是由脚本化 EvidenceAnalyst 提交的诊断。",
                "dimensions": [
                    {"dimension": name, "level": "合格", "rationale": "该判断引用了候选人的原始回答。", "evidenceIds": [evidence_id]}
                    for name in ("relevance", "structure", "evidence", "depth", "roleFit")
                ],
                "strengths": [{"text": "具备可以回查的真实经历", "evidenceIds": [evidence_id]}],
                "weaknesses": [{"text": "仍需补充决策依据", "evidenceIds": [evidence_id]}],
                "suggestedStructure": "使用 STAR 组织回答。",
                "starRewrite": {"situation": answer, "task": "待补充", "action": "待补充", "result": "待补充", "fullAnswer": answer, "evidenceIds": [evidence_id], "missingInformation": ["任务、行动和结果"]},
                "knowledgeToPrepare": ["STAR"],
                "roleFit": {"summary": "当前回答提供了基础岗位信号。", "evidenceIds": [evidence_id], "missingRequirements": [], "uncertainty": ""},
                "followUpAssessments": [
                    {"questionId": item["id"], "impact": "补充有效证据", "rationale": "追问补充了原回答。", "evidenceIds": [evidence_id]}
                    for item in topic.get("followUpTurns", [])
                ],
                "uncertainties": [],
                "revisionSummary": "根据审计意见更新" if "修订意见：[]" not in task else "",
            }
            submit.run({"review_json": json.dumps(payload, ensure_ascii=False)})
        elif agent_type == "reflection":
            self.audit_calls += 1
            if self.audit_calls <= self.critical_audits:
                draft = by_name["GetDraftReview"].payload
                finding = {"topicId": draft[0]["id"], "code": "unsupported_claim", "severity": "critical", "field": "diagnosis", "message": "诊断需要更明确地绑定原文。", "evidenceIds": [draft[0]["evidenceRefs"][0]["id"]]}
                payload = {"decision": "revise", "findings": [finding], "summary": "需要修订一个主题。"}
            else:
                payload = {"decision": "pass", "findings": [], "summary": "Reflection 已确认引用和评分一致。"}
            by_name["SubmitAudit"].run({"audit_json": json.dumps(payload, ensure_ascii=False)})
        elif agent_type == "plan":
            draft = by_name["GetAuditedReview"].payload
            topic_id = draft[0]["id"]
            payload = {
                "summary": "这是由 GrowthPlanner 提交的整场总结。",
                "topRisks": [{"title": "决策依据不足", "reason": "需要更明确地解释取舍。", "severity": "medium", "topicIds": [topic_id]}],
                "nextFocus": "下一场重点表达关键决策。",
                "actionItems": [
                    {"day": day, "title": f"第 {day} 天训练", "description": "完成一次真实经历口述。", "dimension": "structure", "priority": "high" if day <= 2 else "medium", "successCriterion": "三分钟内完成结构化表达。"}
                    for day in range(1, 8)
                ],
            }
            by_name["SubmitPlan"].run({"plan_json": json.dumps(payload, ensure_ascii=False)})
        return AgentRuntimeResult(text="scripted", session_id=f"session-{agent_type}", metadata={"duration_seconds": 0.01, "tokens": 10})


class BlockingAgentRuntime(ScriptedAgentRuntime):
    def __init__(self):
        super().__init__()
        self.block_topic_id = None

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        by_name = {tool.name: tool for tool in tools}
        if agent_type == "react" and by_name["SubmitTopicReview"].topic["id"] == self.block_topic_id:
            time.sleep(2)
            return AgentRuntimeResult(text="late result", metadata={"duration_seconds": 2})
        return super().run_task_agent(agent_type, task, tools, max_steps=max_steps)


class GrowthRuntimeFailure(ScriptedAgentRuntime):
    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        if agent_type == "plan":
            return AgentRuntimeResult(
                text="growth provider rejected the request",
                metadata={"duration_seconds": 0.01, "error": "forced tool choice is unsupported"},
                success=False,
            )
        return super().run_task_agent(agent_type, task, tools, max_steps=max_steps)


def test_real_mode_report_is_built_from_agent_artifacts(monkeypatch, settings_factory):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key")
    database, service, workflow = build_workflow(settings)
    interview = database.create_interview({
        "id": "agent-owned-report",
        "company": "星河科技",
        "position": "产品经理",
        "job_description": "负责实验设计和数据分析。",
        "resume_text": "推动实验并取得提升。",
        "raw_transcript": TRANSCRIPT,
    })
    database.replace_questions(interview["id"], service.parse_transcript(TRANSCRIPT))
    database.confirm_questions(interview["id"])
    workflow.agent_runtime = ScriptedAgentRuntime(invalid_plan=True)
    monkeypatch.setattr(service, "review", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("真实模式不得调用本地规则报告")))
    run = database.create_run(interview["id"], agent_mode="helloagents", input_digest=workflow.input_digest(interview["id"]))

    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert any(event["type"] == "SUPERVISOR_PLAN_FALLBACK" for event in completed["events"])
    report = workflow.report(interview["id"])
    assert report["interview"]["summary"] == "这是由 GrowthPlanner 提交的整场总结。"
    assert report["interview"]["latestAIMetadata"]["provider"] == "HelloAgents"
    assert report["questions"][0]["diagnosis"] == "这是由脚本化 EvidenceAnalyst 提交的诊断。"
    phases = {item["phase"] for item in database.get_stage_artifacts(run["id"], accepted_only=True)}
    assert {"supervisor_plan", "evidence_review", "reflection_audit", "growth_plan"}.issubset(phases)


def _agent_workflow(settings_factory, runtime, **setting_overrides):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key", **setting_overrides)
    database, service, workflow = build_workflow(settings)
    interview = database.create_interview({"id": "agent-recovery", "position": "产品经理", "job_description": "负责实验设计。", "raw_transcript": TRANSCRIPT})
    database.replace_questions(interview["id"], service.parse_transcript(TRANSCRIPT))
    database.confirm_questions(interview["id"])
    workflow.agent_runtime = runtime
    run = database.create_run(interview["id"], agent_mode="helloagents", input_digest=workflow.input_digest(interview["id"]))
    return database, workflow, interview, run


def test_topic_checkpoint_resume_skips_accepted_artifacts(settings_factory):
    failing = ScriptedAgentRuntime()
    database, workflow, interview, run = _agent_workflow(settings_factory, failing)
    topic_ids = [item["id"] for item in database.get_question_topics(interview["id"])]
    failing.fail_topic_id = topic_ids[1]

    workflow.execute(run["id"])

    assert database.get_run(run["id"])["status"] == "FAILED"
    assert database.accepted_artifact(run["id"], "evidence_review", topic_ids[0]) is not None
    succeeding = ScriptedAgentRuntime()
    workflow.agent_runtime = succeeding
    database.update_run(run["id"], status="REVIEWING", phase="resuming", error="", failure_code="")
    workflow.execute(run["id"])

    assert database.get_run(run["id"])["status"] == "COMPLETED"
    assert topic_ids[0] not in succeeding.topic_calls
    assert succeeding.topic_calls[topic_ids[1]] == 1


def test_agent_timeout_fails_with_progress_and_resumes_from_checkpoint(settings_factory):
    blocking = BlockingAgentRuntime()
    database, workflow, interview, run = _agent_workflow(
        settings_factory,
        blocking,
        agent_task_timeout=1,
        agent_heartbeat_interval=0.1,
    )
    topic_ids = [item["id"] for item in database.get_question_topics(interview["id"])]
    blocking.block_topic_id = topic_ids[1]

    workflow.execute(run["id"])

    failed = database.get_run(run["id"])
    event_types = [event["type"] for event in failed["events"]]
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "AGENT_TIMEOUT"
    assert database.accepted_artifact(run["id"], "evidence_review", topic_ids[0]) is not None
    assert "TOOL_STARTED" in event_types
    assert "TOOL_FINISHED" in event_types
    assert "AGENT_HEARTBEAT" in event_types
    assert "AGENT_TIMEOUT" in event_types

    succeeding = ScriptedAgentRuntime()
    workflow.agent_runtime = succeeding
    database.update_run(run["id"], status="REVIEWING", phase="resuming", error="", failure_code="")
    workflow.execute(run["id"])

    assert database.get_run(run["id"])["status"] == "COMPLETED"
    assert topic_ids[0] not in succeeding.topic_calls
    assert succeeding.topic_calls[topic_ids[1]] == 1


def test_growth_runtime_failure_keeps_accepted_audit_and_reports_real_stage(settings_factory):
    database, workflow, _, run = _agent_workflow(settings_factory, GrowthRuntimeFailure())

    workflow.execute(run["id"])

    failed = database.get_run(run["id"])
    assert failed["status"] == "FAILED"
    assert failed["phase"] == "growth_plan"
    assert failed["failure_code"] == "AGENT_RUNTIME_FAILED"
    assert failed["checkpoint"]["auditAccepted"] is True
    assert "forced tool choice is unsupported" in failed["error"]
    assert failed["events"][-1]["data"]["phase"] == "growth_plan"
    assert any(event["type"] == "AGENT_RUNTIME_FAILED" for event in failed["events"])


def test_stale_running_task_is_marked_recoverable_after_restart(settings_factory):
    settings = settings_factory()
    database, service, workflow = build_workflow(settings)
    interview = database.create_interview({"id": "stale-agent", "raw_transcript": TRANSCRIPT})
    database.replace_questions(interview["id"], service.parse_transcript(TRANSCRIPT))
    run = database.create_run(interview["id"], agent_mode="helloagents")

    failed_ids = database.fail_stale_runs("9999-01-01T00:00:00+00:00")

    failed = database.get_run(run["id"])
    assert failed_ids == [run["id"]]
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "AGENT_PROCESS_INTERRUPTED"
    assert failed["events"][-1]["type"] == "RUN_FAILED"


def test_reflection_revises_only_affected_topic_and_keeps_versions(settings_factory):
    runtime = ScriptedAgentRuntime(critical_audits=1)
    database, workflow, interview, run = _agent_workflow(settings_factory, runtime)
    topic_ids = [item["id"] for item in database.get_question_topics(interview["id"])]

    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["audit_round"] == 2
    assert completed["revision_count"] == 1
    topic_artifacts = [item for item in database.get_stage_artifacts(run["id"]) if item["phase"] == "evidence_review"]
    first_versions = [item for item in topic_artifacts if item["topic_id"] == topic_ids[0]]
    second_versions = [item for item in topic_artifacts if item["topic_id"] == topic_ids[1]]
    assert [item["status"] for item in first_versions] == ["SUPERSEDED", "ACCEPTED"]
    assert len(second_versions) == 1


def test_second_audit_failure_can_resume_from_last_critical_findings(settings_factory):
    runtime = ScriptedAgentRuntime(critical_audits=2)
    database, workflow, _, run = _agent_workflow(settings_factory, runtime)

    workflow.execute(run["id"])

    failed = database.get_run(run["id"])
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "AUDIT_CRITICAL"
    revisions_before_resume = failed["revision_count"]
    database.update_run(run["id"], status="REVIEWING", phase="resuming", error="", failure_code="")
    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["revision_count"] == revisions_before_resume + 1
    assert any(event["type"] == "AUDIT_RECOVERY_STARTED" for event in completed["events"])
