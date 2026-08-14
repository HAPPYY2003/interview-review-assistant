import json

from backend.app.services.knowledge import KnowledgeBase
from backend.app.tools import build_agent_tools, build_audit_agent_tools, build_growth_agent_tools


def _topic_submission(topic_id: str, transcript_id: str, job_id: str, *, follow_ups: list[str] | None = None) -> dict:
    dimensions = [
        {"dimension": name, "level": "合格", "rationale": "回答具备基础信息并有原文支持。", "evidenceIds": [job_id if name == "roleFit" else transcript_id]}
        for name in ("relevance", "structure", "evidence", "depth", "roleFit")
    ]
    return {
        "topicId": topic_id,
        "topicVersion": 1,
        "diagnosis": "回答覆盖了问题，但需要补充决策依据和结果口径。",
        "dimensions": dimensions,
        "strengths": [{"text": "回答包含可回查经历", "evidenceIds": [transcript_id]}],
        "weaknesses": [{"text": "决策依据还不够完整", "evidenceIds": [transcript_id]}],
        "suggestedStructure": "按 STAR 顺序说明背景、任务、行动和结果。",
        "starRewrite": {
            "situation": "推动实验优化。",
            "task": "完成实验验证。",
            "action": "分析数据并推进实施。",
            "result": "转化率提升 20%。",
            "fullAnswer": "我推动实验优化，分析数据并推进实施，转化率提升 20%。",
            "evidenceIds": [transcript_id],
            "missingInformation": [],
        },
        "knowledgeToPrepare": ["实验设计"],
        "roleFit": {"summary": "经历与实验设计要求相关。", "evidenceIds": [job_id], "missingRequirements": [], "uncertainty": ""},
        "followUpAssessments": [
            {"questionId": item, "impact": "补充有效证据", "rationale": "追问补充了细节。", "evidenceIds": [transcript_id]}
            for item in (follow_ups or [])
        ],
        "uncertainties": [],
        "revisionSummary": "",
    }


def _growth_submission(topic_id: str) -> dict:
    return {
        "summary": "本场回答有真实证据基础，下一步应加强结构与决策解释。",
        "topRisks": [{"title": "决策依据不足", "reason": "部分回答缺少方案比较。", "severity": "medium", "topicIds": [topic_id]}],
        "nextFocus": "下一场重点说明个人判断与取舍。",
        "actionItems": [
            {"day": day, "title": f"第 {day} 天训练", "description": "使用真实经历完成一次结构化口述。", "dimension": "structure", "priority": "high" if day <= 2 else "medium", "successCriterion": "在三分钟内完整讲清 STAR。"}
            for day in range(1, 8)
        ],
    }


def test_custom_tools_validate_evidence_levels_audit_and_growth(settings_factory):
    from hello_agents.tools.base import Tool

    settings = settings_factory()
    context = {
        "raw_transcript": "候选人：我推动实验后转化率提升 20%。",
        "job_description": "负责实验设计和数据分析。",
        "resume_text": "转化率提升 20%。",
        "topic": {"id": "topic-1", "version": 1, "followUpTurns": []},
    }
    tools, submit = build_agent_tools(KnowledgeBase(settings.knowledge_dir), context, settings)
    assert all(isinstance(tool, Tool) for tool in tools)

    lookup = next(tool for tool in tools if tool.name == "EvidenceLookup")
    transcript_response = lookup.run({"source_type": "transcript", "query": "20%"})
    transcript = transcript_response.data["matches"][0]
    job_response = lookup.run({"source_type": "job_description", "query": "实验设计"})
    job = job_response.data["matches"][0]
    assert transcript["id"] in transcript_response.text
    assert job["id"] in job_response.text
    assert "evidenceId" in transcript_response.text
    score = next(tool for tool in tools if tool.name == "Score").run({"levels_json": json.dumps({name: "合格" for name in ("relevance", "structure", "evidence", "depth", "roleFit")}, ensure_ascii=False)})
    assert score.data["overall"] == 6.0

    accepted = submit.run({"review_json": json.dumps(_topic_submission("topic-1", transcript["id"], job["id"]), ensure_ascii=False)})
    assert accepted.status.value == "success"
    assert submit.last_review["scores"]["overall"] == 6.0
    stale = _topic_submission("topic-1", transcript["id"], job["id"])
    stale["topicVersion"] = 2
    assert submit.run({"review_json": json.dumps(stale, ensure_ascii=False)}).status.value == "partial"
    forged = _topic_submission("topic-1", "ev-forged", job["id"])
    assert submit.run({"review_json": json.dumps(forged, ensure_ascii=False)}).status.value == "partial"

    audit_tools, audit_submit = build_audit_agent_tools([submit.last_review], {transcript["id"]: transcript, job["id"]: job})
    assert {tool.name for tool in audit_tools} == {"GetDraftReview", "VerifyEvidence", "SubmitAudit"}
    audit_submit.run({"audit_json": json.dumps({"decision": "pass", "findings": [], "summary": "引用和评分一致。"}, ensure_ascii=False)})
    assert audit_submit.last_submission["decision"] == "pass"

    growth_tools, growth_submit = build_growth_agent_tools([submit.last_review], [], KnowledgeBase(settings.knowledge_dir))
    assert {tool.name for tool in growth_tools} == {"GetAuditedReview", "GetGrowthHistory", "KnowledgeSearch", "SubmitPlan"}
    growth_submit.run({"plan_json": json.dumps(_growth_submission("topic-1"), ensure_ascii=False)})
    assert len(growth_submit.last_submission["actionItems"]) == 7

    _, flat_submit = build_growth_agent_tools([submit.last_review], [], KnowledgeBase(settings.knowledge_dir))
    flat_response = flat_submit.run({
        "summary": "本场复盘需要提升表达结构。",
        "next_focus": "下一场重点说明决策依据。",
        "risks_text": "表达结构不足|||回答层次需要更清晰|||medium|||topic-1",
        **{
            f"day_{day}": f"第 {day} 天训练|||完成一次结构化口述|||structure|||{'high' if day <= 2 else 'medium'}|||三分钟内完整表达"
            for day in range(1, 8)
        },
    })
    assert flat_response.status.value == "success"
    assert len(flat_submit.last_submission["actionItems"]) == 7


def test_supervisor_registers_task_and_new_domain_tools(settings_factory):
    from backend.app.agents.runtime import HelloAgentsRuntime
    from hello_agents import ToolRegistry

    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key", llm_base_url="https://example.invalid/v1")
    runtime = HelloAgentsRuntime(settings)
    registry = ToolRegistry()
    tools, _ = build_agent_tools(KnowledgeBase(settings.knowledge_dir), {"topic": {"id": "topic", "followUpTurns": []}}, settings)
    for tool in tools:
        registry.register_tool(tool)
    supervisor = runtime.create_supervisor(registry)
    names = set(registry.list_tools())
    assert {"KnowledgeSearch", "EvidenceLookup", "Score", "WebVerify", "SubmitTopicReview", "Task"}.issubset(names)
    assert supervisor.__class__.__name__ == "PlanSolveAgent"


def test_growth_planner_uses_fixed_product_workflow():
    from backend.app.agents.runtime import FixedGrowthPlanner

    steps = FixedGrowthPlanner().plan("ignored")

    assert len(steps) == 3
    assert "GetAuditedReview" in steps[0]
    assert "GetGrowthHistory" in steps[1]
    assert "SubmitPlan" in steps[2]


def test_agent_runtime_reconfigures_windows_streams_to_utf8(monkeypatch, settings_factory):
    from backend.app.agents.runtime import HelloAgentsRuntime

    class FakeStream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdout = FakeStream()
    stderr = FakeStream()
    monkeypatch.setattr("backend.app.agents.runtime.sys.stdout", stdout)
    monkeypatch.setattr("backend.app.agents.runtime.sys.stderr", stderr)
    HelloAgentsRuntime(settings_factory()).configure_environment()
    assert stdout.calls == [{"encoding": "utf-8", "errors": "backslashreplace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "backslashreplace"}]


def test_utterance_worker_builds_payload_from_atoms(monkeypatch, settings_factory):
    from backend.app.agents.runtime import HelloAgentsRuntime

    captured = {}

    class FakeSimpleAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, prompt):
            captured["prompt"] = prompt
            return '{"utterances": []}'

    runtime = HelloAgentsRuntime(settings_factory())
    runtime.available = True
    runtime.SimpleAgent = FakeSimpleAgent
    runtime.HelloAgentsLLM = lambda: object()
    monkeypatch.setattr(runtime, "configure_environment", lambda: None)
    monkeypatch.setattr(runtime, "_config", lambda: {})

    result = runtime.run_utterance_worker(
        [{"id": "atom-1", "rawText": "请介绍一下项目。", "speakerRole": "interviewer"}],
        "boundary_first",
        "atom-1",
    )

    assert result == {"utterances": []}
    assert '"atom_id": "atom-1"' in captured["prompt"]
