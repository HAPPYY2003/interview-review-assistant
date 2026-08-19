import json
import time
from types import SimpleNamespace

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
        self.max_steps_by_type = {}

    def generate_supervisor_plan(self, context):
        steps = ["随意分析"] if self.invalid_plan else ["evidence_review", "reflection_audit", "growth_plan"]
        return AgentRuntimeResult(text=json.dumps({"steps": steps}), metadata={"steps": steps, "duration_seconds": 0.01})

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        self.max_steps_by_type.setdefault(agent_type, []).append(max_steps)
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
                    {"dimension": name, "level": "合格", "rationale": f"{name} 维度的判断引用了候选人的原始回答。", "evidenceIds": [evidence_id]}
                    for name in ("relevance", "structure", "evidence", "depth", "roleFit")
                ],
                "strengths": [{"text": "具备可以回查的真实经历", "evidenceIds": [evidence_id]}],
                "weaknesses": [{"text": "仍需补充决策依据", "evidenceIds": [evidence_id]}],
                "answerLogic": {
                    "summary": "回答说明了经历，但决策过程不完整。",
                    "steps": [{"order": 1, "label": "原回答", "content": answer, "evidenceIds": [evidence_id]}],
                    "gaps": [{"text": "仍需补充决策依据", "evidenceIds": [evidence_id]}],
                },
                "interviewerSignals": [],
                "recommendedAnswer": {
                    "framework": {
                        "type": "STAR", "name": "STAR", "reason": "项目经历适合 STAR。",
                        "sections": [
                            {"key": "S", "label": "情境", "guidance": "说明背景。", "draft": answer, "evidenceIds": [evidence_id]},
                            {"key": "T", "label": "任务", "guidance": "说明任务。", "draft": "待补充：任务", "evidenceIds": []},
                        ],
                    },
                    "fullAnswer": answer, "evidenceIds": [evidence_id], "missingInformation": ["任务、行动和结果"],
                },
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
            evidence_id = draft[0]["evidenceRefs"][0]["id"]
            payload = {
                "overallEvaluation": {
                    "summary": "这是由 GrowthPlanner 提交的整场总结。",
                    "competitiveness": "具备基础竞争力，但不代表实际录用结果。",
                    "strengths": [{"text": "回答具备真实经历。", "topicIds": [topic_id]}],
                    "risks": [{"text": "决策依据不足。", "topicIds": [topic_id]}],
                    "nextFocus": "下一场重点表达关键决策。",
                },
                "capabilityGaps": [{
                    "id": "gap-1", "category": "soft_skill", "title": "决策表达",
                    "description": "需要更明确地解释取舍。", "impact": "影响分析深度。", "priority": "high",
                    "topicIds": [topic_id], "evidenceIds": [evidence_id],
                    "learningItems": ["结构化表达"], "preparationItems": ["补充取舍案例"],
                }],
                "actionItems": [
                    {"order": order, "title": f"行动 {order}", "description": "完成一次真实经历口述。", "type": "learning" if order % 2 else "preparation", "gapIds": ["gap-1"], "dimension": "structure", "priority": "high" if order == 1 else "medium", "successCriterion": "三分钟内完成结构化表达。"}
                    for order in range(1, 4)
                ],
            }
            by_name["SubmitPlan"].run({"plan_json": json.dumps(payload, ensure_ascii=False)})
        return AgentRuntimeResult(text="scripted", session_id=f"session-{agent_type}", metadata={"duration_seconds": 0.01, "tokens": 10})


class JsonOnlyEvidenceRuntime(ScriptedAgentRuntime):
    """Simulate a ReAct model that returns JSON but forgets the submit tool call."""

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        result = super().run_task_agent(agent_type, task, tools, max_steps=max_steps)
        if agent_type != "react":
            return result
        submit = next(tool for tool in tools if tool.name == "SubmitTopicReview")
        payload = submit.last_submission
        submit.last_submission = None
        submit.last_review = None
        return AgentRuntimeResult(
            text=f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```",
            session_id="session-json-only",
            metadata={"duration_seconds": 0.01, "tokens": 10},
        )


class FinalizerEvidenceRuntime(ScriptedAgentRuntime):
    """Simulate a ReAct model that ends without any structured submission."""

    def __init__(self):
        super().__init__()
        self.final_payload = None
        self.job_evidence_id = None
        self.finalizer_calls = 0

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        result = super().run_task_agent(agent_type, task, tools, max_steps=max_steps)
        if agent_type != "react":
            return result
        submit = next(tool for tool in tools if tool.name == "SubmitTopicReview")
        self.final_payload = submit.last_submission
        self.job_evidence_id = next(
            (item for item, ref in submit.registry.items() if ref["sourceType"] == "job_description"),
            None,
        )
        submit.last_submission = None
        submit.last_review = None
        return AgentRuntimeResult(
            text="证据分析完成。",
            session_id="session-missing-submit",
            metadata={"duration_seconds": 0.01, "tokens": 10},
        )

    def finalize_topic_review(self, _prompt):
        self.finalizer_calls += 1
        payload = json.loads(json.dumps(self.final_payload, ensure_ascii=False))
        if self.job_evidence_id:
            for item in [*payload["answerLogic"]["steps"], *payload["answerLogic"]["gaps"]]:
                item["evidenceIds"] = [self.job_evidence_id]
        return AgentRuntimeResult(
            text=json.dumps(payload, ensure_ascii=False),
            session_id="session-finalizer",
            metadata={"agent": "SimpleAgentFinalizer", "duration_seconds": 0.01, "tokens": 10},
        )


class PlaceholderEchoEvidenceRuntime(FinalizerEvidenceRuntime):
    """Simulate TaskTool echoing the old submit template after max-step exhaustion."""

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        result = super().run_task_agent(agent_type, task, tools, max_steps=max_steps)
        if agent_type != "react":
            return result
        echoed = json.loads(json.dumps(self.final_payload, ensure_ascii=False))
        echoed["diagnosis"] = "__FILL_DIAGNOSIS__"
        for dimension in echoed["dimensions"]:
            dimension["rationale"] = f"__FILL_{dimension['dimension'].upper()}_RATIONALE__"
        return AgentRuntimeResult(
            text=json.dumps(echoed, ensure_ascii=False),
            session_id="session-placeholder-echo",
            metadata={"duration_seconds": 0.01, "tokens": 10},
        )


class JsonOnlyGrowthRuntime(ScriptedAgentRuntime):
    """Simulate a PlanSolve model that returns the plan JSON without SubmitPlan."""

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        result = super().run_task_agent(agent_type, task, tools, max_steps=max_steps)
        if agent_type != "plan":
            return result
        submit = next(tool for tool in tools if tool.name == "SubmitPlan")
        payload = {
            key: submit.last_submission[key]
            for key in ("overallEvaluation", "capabilityGaps", "actionItems")
        }
        payload["actionItems"] = [
            {key: value for key, value in item.items() if key not in {"id", "completed"}}
            for item in payload["actionItems"]
        ]
        submit.last_submission = None
        return AgentRuntimeResult(
            text=json.dumps(payload, ensure_ascii=False),
            session_id="session-growth-json-only",
            metadata={"duration_seconds": 0.01, "tokens": 10},
        )


class FinalizerGrowthRuntime(ScriptedAgentRuntime):
    """Simulate the invalid IDs that previously made GrowthPlanner retry until timeout."""

    def __init__(self):
        super().__init__()
        self.final_payload = None
        self.finalizer_calls = 0

    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        result = super().run_task_agent(agent_type, task, tools, max_steps=max_steps)
        if agent_type != "plan":
            return result
        submit = next(tool for tool in tools if tool.name == "SubmitPlan")
        self.final_payload = json.loads(json.dumps({
            key: submit.last_submission[key]
            for key in ("overallEvaluation", "capabilityGaps", "actionItems")
        }, ensure_ascii=False))
        self.final_payload["actionItems"] = [
            {key: value for key, value in item.items() if key not in {"id", "completed"}}
            for item in self.final_payload["actionItems"]
        ]
        submit.last_submission = None
        return AgentRuntimeResult(
            text="成长计划已整理。",
            session_id="session-growth-missing-submit",
            metadata={"duration_seconds": 0.01, "tokens": 10},
        )

    def finalize_growth_plan(self, _prompt):
        self.finalizer_calls += 1
        payload = json.loads(json.dumps(self.final_payload, ensure_ascii=False))
        payload["capabilityGaps"][0]["id"] = "GAP-01"
        payload["capabilityGaps"][0]["topicIds"] = ["t01"]
        payload["capabilityGaps"][0]["evidenceIds"] = ["unknown-evidence"]
        for point in [
            *payload["overallEvaluation"]["strengths"],
            *payload["overallEvaluation"]["risks"],
        ]:
            point["topicIds"] = ["structure"]
        for action in payload["actionItems"]:
            action["gapIds"] = ["GAP-01"]
        return AgentRuntimeResult(
            text=json.dumps(payload, ensure_ascii=False),
            session_id="session-growth-finalizer",
            metadata={"agent": "SimpleAgentGrowthFinalizer", "duration_seconds": 0.01, "tokens": 10},
        )


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


class AcceptedAuditThenBlocksRuntime(ScriptedAgentRuntime):
    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        if agent_type == "reflection":
            submit = next(tool for tool in tools if tool.name == "SubmitAudit")
            submit.run({
                "audit_json": json.dumps({
                    "decision": "pass",
                    "findings": [],
                    "summary": "审计结果已经通过结构校验。",
                }, ensure_ascii=False),
            })
            time.sleep(1.5)
            return AgentRuntimeResult(text="late audit exit", metadata={"duration_seconds": 1.5})
        return super().run_task_agent(agent_type, task, tools, max_steps=max_steps)


class BlockingAuditRuntime(ScriptedAgentRuntime):
    def run_task_agent(self, agent_type, task, tools, *, max_steps=8):
        if agent_type == "reflection":
            time.sleep(1.5)
            return AgentRuntimeResult(text="late audit without submission", metadata={"duration_seconds": 1.5})
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
    runtime = ScriptedAgentRuntime(invalid_plan=True)
    workflow.agent_runtime = runtime
    monkeypatch.setattr(service, "review", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("真实模式不得调用本地规则报告")))
    run = database.create_run(interview["id"], agent_mode="helloagents", input_digest=workflow.input_digest(interview["id"]))

    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert any(event["type"] == "SUPERVISOR_PLAN_FALLBACK" for event in completed["events"])
    report = workflow.report(interview["id"])
    assert report["reportSchemaVersion"] == 2
    assert report["interview"]["summary"] == "这是由 GrowthPlanner 提交的整场总结。"
    assert report["interview"]["overallEvaluation"]["competitiveness"]
    assert report["questions"][0]["answerLogic"]["steps"]
    assert report["questions"][0]["recommendedAnswer"]["framework"]["type"]
    assert report["interview"]["latestAIMetadata"]["provider"] == "HelloAgents"
    assert report["questions"][0]["diagnosis"] == "这是由脚本化 EvidenceAnalyst 提交的诊断。"
    assert set(runtime.max_steps_by_type["react"]) == {5}
    phases = {item["phase"] for item in database.get_stage_artifacts(run["id"], accepted_only=True)}
    assert {"supervisor_plan", "evidence_review", "reflection_audit", "growth_plan"}.issubset(phases)


@pytest.mark.parametrize(
    ("runtime", "expected_event"),
    [
        (JsonOnlyEvidenceRuntime(), "MODEL_JSON_AUTO_SUBMITTED"),
        (FinalizerEvidenceRuntime(), "FINALIZER_FINISHED"),
        (PlaceholderEchoEvidenceRuntime(), "MODEL_JSON_AUTO_SUBMIT_SKIPPED"),
    ],
)
def test_evidence_submission_is_recovered_when_react_omits_tool_call(settings_factory, runtime, expected_event):
    database, workflow, interview, run = _agent_workflow(settings_factory, runtime)

    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert any(event["type"] == expected_event for event in completed["events"])
    if isinstance(runtime, FinalizerEvidenceRuntime):
        assert runtime.finalizer_calls == len(database.get_question_topics(interview["id"]))
        assert any(event["type"] == "MODEL_SUBMISSION_CONSTRAINED" for event in completed["events"])
        evidence_artifacts = [
            item for item in database.get_stage_artifacts(run["id"], accepted_only=True)
            if item["phase"] == "evidence_review"
        ]
        assert {item["agent_type"] for item in evidence_artifacts} == {"SimpleAgentFinalizer"}


@pytest.mark.parametrize(
    ("runtime", "expected_event"),
    [
        (JsonOnlyGrowthRuntime(), "MODEL_JSON_AUTO_SUBMITTED"),
        (FinalizerGrowthRuntime(), "FINALIZER_FINISHED"),
    ],
)
def test_growth_submission_recovers_without_repeated_plan_retries(settings_factory, runtime, expected_event):
    database, workflow, _, run = _agent_workflow(settings_factory, runtime)

    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED", completed.get("error")
    assert any(
        event["type"] == expected_event and event["data"].get("agent", "").startswith("GrowthPlanner")
        for event in completed["events"]
    )
    growth = database.accepted_artifact(run["id"], "growth_plan")["payload"]["plan"]
    assert growth["capabilityGaps"][0]["id"] == "gap-1"
    assert all(item["gapIds"] == ["gap-1"] for item in growth["actionItems"])
    if isinstance(runtime, FinalizerGrowthRuntime):
        assert runtime.finalizer_calls == 1
        artifact = database.accepted_artifact(run["id"], "growth_plan")
        assert artifact["agent_type"] == "SimpleAgentGrowthFinalizer"
        assert any(event["type"] == "MODEL_SUBMISSION_CONSTRAINED" for event in completed["events"])


def test_json_extraction_prefers_topic_submission_over_echoed_context():
    context = {"id": "topic-1", "interviewerQuestion": "请介绍项目。"}
    submission = {"topicId": "topic-1", "topicVersion": 1, "dimensions": []}

    extracted = ReviewWorkflow._extract_json_object(
        f"当前主题：{json.dumps(context, ensure_ascii=False)}\n最终结果：{json.dumps(submission, ensure_ascii=False)}"
    )

    assert extracted == submission


def test_topic_submission_contract_is_not_a_submit_ready_placeholder_json():
    contract = ReviewWorkflow._topic_submission_contract({"id": "topic-1", "version": 1, "followUpTurns": []})

    assert "__FILL_" not in contract
    assert ReviewWorkflow._extract_json_object(contract, {"topicId", "dimensions"}) is None


def test_topic_submission_constraint_repairs_flattened_framework_shape():
    evidence_id = "ev-answer"
    payload = {
        "topicId": "topic-1",
        "topicVersion": 1,
        "dimensions": [],
        "answerLogic": {"steps": [], "gaps": []},
        "recommendedAnswer": {
            "framework": "CUSTOM",
            "name": "诊断-生成-验证",
            "reason": "适合产品设计题。",
            "sections": [
                {"key": "diagnose", "label": "诊断", "guidance": "说明场景。", "draft": "说明业务场景。", "evidenceIds": [evidence_id]},
                {"key": "verify", "label": "验证", "guidance": "说明指标。", "draft": "说明验证指标。", "evidenceIds": [evidence_id]},
            ],
            "evidenceIds": [evidence_id],
            "missingInformation": [],
        },
        "roleFit": {"evidenceIds": [evidence_id]},
        "interviewerSignals": [],
        "followUpAssessments": [],
    }
    submit = SimpleNamespace(
        topic={"id": "topic-1", "version": 1, "candidateAnswer": "说明业务场景和验证指标。", "followUpTurns": []},
        registry={evidence_id: {"sourceType": "transcript", "quote": "说明业务场景和验证指标。"}},
    )

    constrained, repairs = ReviewWorkflow._constrain_topic_submission(payload, submit)

    recommended = constrained["recommendedAnswer"]
    assert recommended["framework"]["type"] == "CUSTOM"
    assert recommended["framework"]["name"] == "诊断-生成-验证"
    assert len(recommended["framework"]["sections"]) == 2
    assert recommended["fullAnswer"] == "说明业务场景。\n说明验证指标。"
    assert "recommendedAnswerShape" in repairs


def test_topic_submission_constraint_does_not_crash_on_wrong_nested_types():
    payload = {
        "topicId": "topic-1",
        "topicVersion": 1,
        "dimensions": ["invalid"],
        "answerLogic": "invalid",
        "recommendedAnswer": "invalid",
        "roleFit": "invalid",
        "interviewerSignals": ["invalid"],
        "followUpAssessments": ["invalid"],
    }
    submit = SimpleNamespace(
        topic={"id": "topic-1", "version": 1, "candidateAnswer": "回答原文", "followUpTurns": []},
        registry={},
    )

    constrained, _ = ReviewWorkflow._constrain_topic_submission(payload, submit)

    assert constrained["recommendedAnswer"] == "invalid"


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


def test_accepted_audit_is_recovered_when_agent_does_not_exit_before_timeout(settings_factory):
    runtime = AcceptedAuditThenBlocksRuntime()
    database, workflow, _, run = _agent_workflow(
        settings_factory,
        runtime,
        agent_task_timeout=1,
        agent_heartbeat_interval=0.1,
    )

    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["checkpoint"]["auditAccepted"] is True
    assert any(event["type"] == "AGENT_EXIT_TIMEOUT_RECOVERED" for event in completed["events"])
    assert not any(
        event["type"] == "AGENT_TIMEOUT" and event["data"].get("agent") == "QualityAuditor"
        for event in completed["events"]
    )
    audit = database.accepted_artifact(run["id"], "reflection_audit")
    assert audit["payload"]["audit"]["decision"] == "pass"


def test_resume_retries_interrupted_second_audit_round_without_artifact(settings_factory):
    blocking = BlockingAuditRuntime()
    database, workflow, _, run = _agent_workflow(
        settings_factory,
        blocking,
        agent_task_timeout=1,
        agent_heartbeat_interval=0.1,
    )

    workflow.execute(run["id"])
    failed = database.get_run(run["id"])
    assert failed["status"] == "FAILED"
    assert failed["phase"] == "reflection_audit"
    assert database.accepted_artifact(run["id"], "reflection_audit") is None

    database.update_run(
        run["id"],
        status="REVIEWING",
        phase="resuming",
        error="",
        failure_code="",
        audit_round=2,
    )
    workflow.agent_runtime = ScriptedAgentRuntime()
    workflow.execute(run["id"])

    completed = database.get_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert any(
        event["type"] == "AUDIT_ROUND_RETRY" and event["data"]["round"] == 2
        for event in completed["events"]
    )


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
