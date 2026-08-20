import json

from backend.app.schemas import TopicReviewSubmission
from backend.app.services.knowledge import KnowledgeBase
from backend.app.tools import build_agent_tools, build_audit_agent_tools, build_growth_agent_tools, build_growth_audit_agent_tools
from backend.app.tools.agent_tools import JsonSnapshotTool, ScoreTool, SubmitTopicReviewTool


def _topic_submission(topic_id: str, transcript_id: str, job_id: str, *, follow_ups: list[str] | None = None) -> dict:
    rationale_by_dimension = {
        "relevance": "回答直接回应了问题核心并有原文支持。",
        "structure": "回答具备清晰的先后结构并有原文支持。",
        "evidence": "回答包含可以回查的经历和结果证据。",
        "depth": "回答解释了行动过程但决策依据仍可补充。",
        "roleFit": "回答经历与岗位要求存在直接关联。",
    }
    dimensions = [
        {"dimension": name, "level": "合格", "rationale": rationale_by_dimension[name], "evidenceIds": [job_id if name == "roleFit" else transcript_id]}
        for name in ("relevance", "structure", "evidence", "depth", "roleFit")
    ]
    return {
        "topicId": topic_id,
        "topicVersion": 1,
        "diagnosis": "回答覆盖了问题，但需要补充决策依据和结果口径。",
        "dimensions": dimensions,
        "strengths": [{"text": "回答包含可回查经历", "evidenceIds": [transcript_id]}],
        "weaknesses": [{"text": "决策依据还不够完整", "evidenceIds": [transcript_id]}],
        "answerLogic": {
            "summary": "回答先说明行动，再给出结果。",
            "steps": [{"order": 1, "label": "行动与结果", "content": "推动实验并取得提升。", "evidenceIds": [transcript_id]}],
            "gaps": [{"text": "决策依据还不够完整", "evidenceIds": [transcript_id]}],
        },
        "interviewerSignals": [],
        "recommendedAnswer": {
            "framework": {
                "type": "STAR", "name": "STAR", "reason": "项目经历适合按 STAR 展开。",
                "sections": [
                    {"key": "S", "label": "情境", "guidance": "交代背景。", "draft": "推动实验优化。", "evidenceIds": [transcript_id]},
                    {"key": "T", "label": "任务", "guidance": "说明任务。", "draft": "待补充：明确任务", "evidenceIds": []},
                    {"key": "A", "label": "行动", "guidance": "说明行动。", "draft": "分析数据并推进实施。", "evidenceIds": [transcript_id]},
                    {"key": "R", "label": "结果", "guidance": "说明结果。", "draft": "转化率提升 20%。", "evidenceIds": [transcript_id]},
                ],
            },
            "fullAnswer": "我推动实验优化，分析数据并推进实施，转化率提升 20%。",
            "evidenceIds": [transcript_id], "missingInformation": ["明确任务"],
        },
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


def test_json_snapshot_returns_large_payload_only_once():
    tool = JsonSnapshotTool("GetContext", "读取上下文。", {"items": [1, 2, 3]})

    first = tool.run({})
    repeated = tool.run({})

    assert first.data["payload"] == {"items": [1, 2, 3]}
    assert repeated.data == {"alreadyRead": True, "readCount": 2}
    assert tool.read_count == 2


def _growth_submission(topic_id: str, evidence_id: str) -> dict:
    return {
        "overallEvaluation": {
            "summary": "本场回答有真实证据基础，下一步应加强结构与决策解释。",
            "competitiveness": "具备基础竞争力，但不代表实际录用结果。",
            "strengths": [{"text": "回答包含真实经历。", "topicIds": [topic_id]}],
            "risks": [{"text": "决策依据不足。", "topicIds": [topic_id]}],
            "nextFocus": "下一场重点说明个人判断与取舍。",
        },
        "capabilityGaps": [{
            "id": "gap-1", "category": "soft_skill", "title": "决策表达",
            "description": "部分回答缺少方案比较。", "impact": "影响分析深度判断。", "priority": "high",
            "topicIds": [topic_id], "evidenceIds": [evidence_id],
            "learningItems": ["方案比较方法"], "preparationItems": ["补充项目取舍案例"],
        }],
        "actionItems": [
            {"order": order, "title": f"行动 {order}", "description": "使用真实经历完成一次结构化口述。", "type": "learning" if order % 2 else "preparation", "gapIds": ["gap-1"], "dimension": "structure", "priority": "high" if order == 1 else "medium", "successCriterion": "在三分钟内完整讲清 STAR。"}
            for order in range(1, 4)
        ],
    }


def test_topic_submission_normalizes_legacy_text_field_shapes():
    payload = _topic_submission("topic-1", "ev-transcript", "ev-job")
    payload["suggestedStructure"] = ["先说明背景和任务", "再说明行动和结果"]
    payload["recommendedAnswer"]["missingInformation"] = "补充样本量"
    payload["starRewrite"]["missingInformation"] = "补充实验周期"
    payload["knowledgeToPrepare"] = "实验设计"
    payload["uncertainties"] = "结果尚未独立验证"
    payload["roleFit"]["missingRequirements"] = "补充岗位场景"

    submission = TopicReviewSubmission.model_validate(payload)

    assert submission.suggested_structure == "先说明背景和任务；再说明行动和结果"
    assert submission.recommended_answer.missing_information == ["补充样本量"]
    assert submission.star_rewrite is not None
    assert submission.star_rewrite.missing_information == ["补充实验周期"]
    assert submission.knowledge_to_prepare == ["实验设计"]
    assert submission.uncertainties == ["结果尚未独立验证"]
    assert submission.role_fit.missing_requirements == ["补充岗位场景"]


def test_custom_tools_validate_evidence_levels_audit_and_growth(settings_factory):
    from hello_agents.tools.base import Tool

    settings = settings_factory()
    context = {
        "raw_transcript": "面试官：请介绍项目。\n候选人：我推动实验后转化率提升 20%。",
        "job_description": "负责实验设计和数据分析。",
        "resume_text": "转化率提升 20%。",
        "topic": {"id": "topic-1", "version": 1, "interviewerQuestion": "请介绍项目。", "followUpTurns": []},
    }
    tools, submit = build_agent_tools(KnowledgeBase(settings.knowledge_dir), context, settings)
    assert all(isinstance(tool, Tool) for tool in tools)

    lookup = next(tool for tool in tools if tool.name == "EvidenceLookup")
    prefetched = lookup.prefetch({"source_type": "transcript", "query": "20%"})
    assert prefetched.status.value == "success"
    assert lookup.call_count == 0
    transcript_response = lookup.run({"source_type": "transcript", "query": "20%"})
    transcript = transcript_response.data["matches"][0]
    job_response = lookup.run({"source_type": "job_description", "query": "实验设计"})
    job = job_response.data["matches"][0]
    assert transcript["id"] in transcript_response.text
    assert job["id"] in job_response.text
    assert "evidenceId" in transcript_response.text
    exhausted = lookup.run({"source_type": "transcript", "query": "项目"})
    assert exhausted.status.value == "partial"
    assert exhausted.data["budgetExhausted"] is True
    assert all(tool.name != "Score" for tool in tools)
    score = ScoreTool().run({"levels_json": json.dumps({name: "合格" for name in ("relevance", "structure", "evidence", "depth", "roleFit")}, ensure_ascii=False)})
    assert score.data["overall"] == 6.0

    accepted = submit.run({"review_json": json.dumps(_topic_submission("topic-1", transcript["id"], job["id"]), ensure_ascii=False)})
    assert accepted.status.value == "success"
    assert submit.last_review["scores"]["overall"] == 6.0
    duplicate = submit.run({"review_json": "{}"})
    assert duplicate.status.value == "success"
    assert duplicate.data["locked"] is True

    def validate_once(payload):
        validator = SubmitTopicReviewTool(submit.topic, submit.registry, has_jd=True)
        response = validator.run({"review_json": json.dumps(payload, ensure_ascii=False)})
        return response, validator

    placeholder = _topic_submission("topic-1", transcript["id"], job["id"])
    placeholder["diagnosis"] = "综合诊断"
    response, validator = validate_once(placeholder)
    assert response.status.value == "partial"
    assert "占位" in validator.last_error
    repeated_rationale = _topic_submission("topic-1", transcript["id"], job["id"])
    for dimension in repeated_rationale["dimensions"]:
        dimension["rationale"] = "回答具备基础信息并有原文支持。"
    response, validator = validate_once(repeated_rationale)
    assert response.status.value == "partial"
    assert "五维评分" in validator.last_error
    incomplete_framework = _topic_submission("topic-1", transcript["id"], job["id"])
    incomplete_framework["recommendedAnswer"]["framework"]["sections"] = incomplete_framework["recommendedAnswer"]["framework"]["sections"][:1]
    assert validate_once(incomplete_framework)[0].status.value == "partial"
    submit.topic["followUpTurns"] = [{"id": "follow-1", "interviewerQuestion": "请补充结果。", "candidateAnswer": "结果提升 20%。"}]
    missing_signal = _topic_submission("topic-1", transcript["id"], job["id"], follow_ups=["follow-1"])
    response, validator = validate_once(missing_signal)
    assert response.status.value == "partial"
    assert "面试官信号" in validator.last_error
    submit.topic["followUpTurns"] = []
    stale = _topic_submission("topic-1", transcript["id"], job["id"])
    stale["topicVersion"] = 2
    assert validate_once(stale)[0].status.value == "partial"
    forged = _topic_submission("topic-1", "ev-forged", job["id"])
    assert validate_once(forged)[0].status.value == "partial"
    invalid_logic = _topic_submission("topic-1", transcript["id"], job["id"])
    invalid_logic["answerLogic"]["steps"][0]["evidenceIds"] = [job["id"]]
    assert validate_once(invalid_logic)[0].status.value == "partial"
    submit.topic["followUpTurns"] = [{"id": "follow-1", "interviewerQuestion": "请补充结果。", "candidateAnswer": "结果提升 20%。"}]
    invalid_signal = _topic_submission("topic-1", transcript["id"], job["id"], follow_ups=["follow-1"])
    invalid_signal["interviewerSignals"] = [{
        "turnId": "topic-1", "type": "request_detail", "interpretation": "要求补充细节。",
        "confidence": "high", "evidenceIds": [transcript["id"]],
    }]
    assert validate_once(invalid_signal)[0].status.value == "partial"
    submit.topic["followUpTurns"] = []
    invalid_framework = _topic_submission("topic-1", transcript["id"], job["id"])
    invalid_framework["recommendedAnswer"]["framework"]["type"] = "CHAIN"
    assert validate_once(invalid_framework)[0].status.value == "partial"
    invented_number = _topic_submission("topic-1", transcript["id"], job["id"])
    invented_number["recommendedAnswer"]["fullAnswer"] = "我推动实验后转化率提升 30%。"
    assert validate_once(invented_number)[0].status.value == "partial"

    audit_tools, audit_submit = build_audit_agent_tools([submit.last_review], {transcript["id"]: transcript, job["id"]: job})
    assert {tool.name for tool in audit_tools} == {"GetDraftReview", "VerifyEvidence", "SubmitAudit"}
    audit_submit.run({"audit_json": json.dumps({"decision": "pass", "findings": [], "summary": "引用和评分一致。"}, ensure_ascii=False)})
    assert audit_submit.last_submission["decision"] == "pass"
    duplicate = audit_submit.run({
        "audit_json": json.dumps({
            "decision": "revise",
            "findings": [{
                "topicId": "invented-topic",
                "code": "other",
                "severity": "critical",
                "message": "这次重复提交不应覆盖已接受的结果。",
                "evidenceIds": [],
            }],
            "summary": "重复提交。",
        }, ensure_ascii=False),
    })
    assert duplicate.status.value == "success"
    assert duplicate.data["locked"] is True
    assert audit_submit.last_submission["decision"] == "pass"
    assert audit_submit.last_error == ""

    _, invalid_audit_submit = build_audit_agent_tools([submit.last_review], {transcript["id"]: transcript, job["id"]: job})
    invalid = invalid_audit_submit.run({
        "audit_json": json.dumps({
            "decision": "revise",
            "findings": [{
                "topicId": "q1",
                "code": "score_conflict",
                "severity": "critical",
                "message": "主题 ID 使用错误。",
                "evidenceIds": [],
            }],
            "summary": "需要修订。",
        }, ensure_ascii=False),
    })
    assert invalid.status.value == "partial"
    assert "可用主题 ID 只有" in invalid_audit_submit.last_error
    assert submit.last_review["id"] in invalid_audit_submit.last_error

    growth_tools, growth_submit = build_growth_agent_tools([submit.last_review], [], KnowledgeBase(settings.knowledge_dir))
    assert {tool.name for tool in growth_tools} == {"GetAuditedReview", "GetGrowthHistory", "KnowledgeSearch", "SubmitPlan"}
    assert [item.name for item in growth_submit.get_parameters()] == ["plan_json"]
    growth_submit.run({"plan_json": json.dumps(_growth_submission("topic-1", transcript["id"]), ensure_ascii=False)})
    assert len(growth_submit.last_submission["actionItems"]) == 3
    assert [item["order"] for item in growth_submit.last_submission["actionItems"]] == [1, 2, 3]
    assert all("deliverable" not in item for item in growth_submit.last_submission["actionItems"])
    duplicate_plan = growth_submit.run({"plan_json": "{}"})
    assert duplicate_plan.status.value == "success"
    assert duplicate_plan.data["locked"] is True

    def validate_plan_once(payload):
        _, validator = build_growth_agent_tools([submit.last_review], [], KnowledgeBase(settings.knowledge_dir))
        response = validator.run({"plan_json": json.dumps(payload, ensure_ascii=False)})
        return response, validator

    legacy_plan = _growth_submission("topic-1", transcript["id"])
    for item in legacy_plan["actionItems"]:
        item["day"] = item.pop("order")
        item["deliverable"] = "旧版具体产出"
    response, legacy_submit = validate_plan_once(legacy_plan)
    assert response.status.value == "success"
    assert [item["order"] for item in legacy_submit.last_submission["actionItems"]] == [1, 2, 3]
    assert all("day" not in item and "deliverable" not in item for item in legacy_submit.last_submission["actionItems"])
    duplicate_gaps = _growth_submission("topic-1", transcript["id"])
    duplicate_gaps["capabilityGaps"].append(dict(duplicate_gaps["capabilityGaps"][0]))
    assert validate_plan_once(duplicate_gaps)[0].status.value == "partial"
    invalid_gap_reference = _growth_submission("topic-1", transcript["id"])
    invalid_gap_reference["actionItems"][0]["gapIds"] = ["gap-missing"]
    assert validate_plan_once(invalid_gap_reference)[0].status.value == "partial"
    too_short = _growth_submission("topic-1", transcript["id"])
    too_short["actionItems"] = too_short["actionItems"][:2]
    assert validate_plan_once(too_short)[0].status.value == "partial"
    nonconsecutive = _growth_submission("topic-1", transcript["id"])
    nonconsecutive["actionItems"][2]["order"] = 4
    assert validate_plan_once(nonconsecutive)[0].status.value == "partial"
    probability_claim = _growth_submission("topic-1", transcript["id"])
    probability_claim["overallEvaluation"]["competitiveness"] = "本场录用概率为 80%。"
    assert validate_plan_once(probability_claim)[0].status.value == "partial"

    _, audit_plan_submit = build_growth_agent_tools([submit.last_review], [], KnowledgeBase(settings.knowledge_dir))
    audit_plan_submit.run({"plan_json": json.dumps(_growth_submission("topic-1", transcript["id"]), ensure_ascii=False)})
    plan_artifact = {"id": "growth-artifact-1", "version": 1, "payload": {"plan": audit_plan_submit.last_submission}}
    audit_context = {
        "overallScores": {"overall": 6.0},
        "topics": [{"topicId": "topic-1", "title": "项目经历", "evidenceIds": [transcript["id"]]}],
        "evidenceCatalog": [{"evidenceId": transcript["id"], "sourceType": "transcript"}],
    }
    growth_audit_tools, growth_audit_submit = build_growth_audit_agent_tools(
        plan_artifact, audit_context, {transcript["id"]: transcript, job["id"]: job},
    )
    assert {tool.name for tool in growth_audit_tools} == {"GetGrowthPlan", "GetGrowthAuditContext", "VerifyEvidence", "SubmitGrowthAudit"}
    unread_growth_audit = growth_audit_submit.run({
        "audit_json": json.dumps({"decision": "pass", "summary": "不应跳过上下文读取。", "findings": []}, ensure_ascii=False),
    })
    assert unread_growth_audit.status.value == "partial"
    assert "GetGrowthPlan" in growth_audit_submit.last_error
    assert "GetGrowthAuditContext" in growth_audit_submit.last_error
    for tool in growth_audit_tools:
        if tool.name in {"GetGrowthPlan", "GetGrowthAuditContext"}:
            tool.run({})
    accepted_growth_audit = growth_audit_submit.run({
        "audit_json": json.dumps({"decision": "pass", "summary": "成长计划可追溯且可执行。", "findings": []}, ensure_ascii=False),
    })
    assert accepted_growth_audit.status.value == "success"
    duplicate_growth_audit = growth_audit_submit.run({
        "audit_json": json.dumps({"decision": "revise", "summary": "重复提交。", "findings": [{
            "targetType": "capability_gap", "targetId": "missing-gap", "code": "unsupported_gap",
            "severity": "critical", "message": "不应覆盖首次结果。", "topicIds": [], "evidenceIds": [],
        }]}, ensure_ascii=False),
    })
    assert duplicate_growth_audit.status.value == "success"
    assert duplicate_growth_audit.data["locked"] is True

    invalid_growth_audit_tools, invalid_growth_audit = build_growth_audit_agent_tools(
        plan_artifact, audit_context, {transcript["id"]: transcript},
    )
    for tool in invalid_growth_audit_tools:
        if tool.name in {"GetGrowthPlan", "GetGrowthAuditContext"}:
            tool.run({})
    invalid_target = invalid_growth_audit.run({
        "audit_json": json.dumps({"decision": "revise", "summary": "需要修订。", "findings": [{
            "targetType": "capability_gap", "targetId": "missing-gap", "code": "unsupported_gap",
            "severity": "critical", "message": "缺口不存在。", "topicIds": ["topic-1"], "evidenceIds": [transcript["id"]],
        }]}, ensure_ascii=False),
    })
    assert invalid_target.status.value == "partial"
    assert "未知缺口" in invalid_growth_audit.last_error

    invalid_growth_audit_tools, invalid_growth_audit = build_growth_audit_agent_tools(
        plan_artifact, audit_context, {transcript["id"]: transcript},
    )
    for tool in invalid_growth_audit_tools:
        if tool.name in {"GetGrowthPlan", "GetGrowthAuditContext"}:
            tool.run({})
    empty_revision = invalid_growth_audit.run({
        "audit_json": json.dumps({"decision": "revise", "summary": "需要修订。", "findings": []}, ensure_ascii=False),
    })
    assert empty_revision.status.value == "partial"
    pass_with_critical = invalid_growth_audit.run({
        "audit_json": json.dumps({"decision": "pass", "summary": "错误通过。", "findings": [{
            "targetType": "overall_evaluation", "targetId": "overall-evaluation", "code": "overall_score_conflict",
            "severity": "critical", "message": "综合评价与分数冲突。", "topicIds": ["topic-1"], "evidenceIds": [],
        }]}, ensure_ascii=False),
    })
    assert pass_with_critical.status.value == "partial"

    _, flat_submit = build_growth_agent_tools([submit.last_review], [], KnowledgeBase(settings.knowledge_dir))
    flat_response = flat_submit.run({
        "summary": "本场复盘需要提升表达结构。",
        "competitiveness": "具备基础竞争力，但不代表实际录用结果。",
        "next_focus": "下一场重点说明决策依据。",
        "strengths_text": "回答包含真实经历|||topic-1",
        "risks_text": "表达结构不足|||topic-1",
        "gaps_text": f"gap-1|||soft_skill|||表达结构|||回答层次需要更清晰|||影响分析深度|||high|||topic-1|||{transcript['id']}|||结构化表达方法|||补充回答提纲",
        **{
            f"action_{order}": f"行动 {order}|||完成一次结构化口述|||{'learning' if order % 2 else 'preparation'}|||gap-1|||structure|||{'high' if order == 1 else 'medium'}|||三分钟内完整表达"
            for order in range(1, 4)
        },
    })
    assert flat_response.status.value == "success"
    assert len(flat_submit.last_submission["actionItems"]) == 3


def test_recommended_answer_accepts_chinese_number_normalization_and_rejects_new_numbers(settings_factory):
    settings = settings_factory()
    answer = (
        "项目每月处理四千八百个复杂工单，平均处理时间三十八分钟，"
        "任务通过率达到百分之六十一，单次成本为零点三六元。"
    )
    context = {
        "raw_transcript": f"面试官：请介绍项目结果。\n候选人：{answer}",
        "job_description": "负责企业产品的数据分析和效果评估。",
        "resume_text": "",
        "topic": {
            "id": "topic-cn-number",
            "version": 1,
            "interviewerQuestion": "请介绍项目结果。",
            "candidateAnswer": answer,
            "followUpTurns": [],
        },
    }
    tools, submit = build_agent_tools(KnowledgeBase(settings.knowledge_dir), context, settings)
    lookup = next(tool for tool in tools if tool.name == "EvidenceLookup")
    transcript = lookup.run({"source_type": "transcript", "query": "四千八百", "limit": 3}).data["matches"][0]
    job = lookup.run({"source_type": "job_description", "query": "数据分析", "limit": 3}).data["matches"][0]
    payload = _topic_submission("topic-cn-number", transcript["id"], job["id"])
    normalized_answer = "项目每月处理 4800 个复杂工单，平均处理时间 38 分钟，任务通过率达到 61％，单次成本为 0.36 元。"
    payload["recommendedAnswer"]["framework"]["sections"] = [
        {"key": "ANSWER", "label": "直接回答", "guidance": "说明结果。", "draft": normalized_answer, "evidenceIds": [transcript["id"]]},
        {"key": "DETAIL", "label": "补充信息", "guidance": "补充缺失背景。", "draft": "待补充：项目背景", "evidenceIds": []},
    ]
    payload["recommendedAnswer"]["fullAnswer"] = normalized_answer
    payload["recommendedAnswer"]["evidenceIds"] = [transcript["id"]]
    payload.pop("starRewrite", None)

    accepted = submit.run({"review_json": json.dumps(payload, ensure_ascii=False)})

    assert accepted.status.value == "success"

    payload["recommendedAnswer"]["fullAnswer"] = (
        "第一部分说明背景，第二部分说明行动，并从五个方面组织，区分三类状态。" + normalized_answer
    )
    outlined_submit = SubmitTopicReviewTool(context["topic"], submit.registry, has_jd=True)
    outlined = outlined_submit.run({"review_json": json.dumps(payload, ensure_ascii=False)})
    assert outlined.status.value == "success"

    payload["recommendedAnswer"]["fullAnswer"] += " 另一个指标达到 62%。"
    rejected_submit = SubmitTopicReviewTool(context["topic"], submit.registry, has_jd=True)
    rejected = rejected_submit.run({"review_json": json.dumps(payload, ensure_ascii=False)})

    assert rejected.status.value == "partial"
    assert "62%" in rejected_submit.last_error


def test_topic_submission_normalizes_timestamp_signal_to_followup_uuid():
    answer_id = "ev-answer"
    question_id = "ev-follow-question"
    job_id = "ev-job"
    registry = {
        answer_id: {"id": answer_id, "sourceType": "transcript", "quote": "我推动实验，转化率提升 20%。", "locator": "字符 10-30"},
        question_id: {"id": question_id, "sourceType": "transcript", "quote": "请补充说明最终结果。", "locator": "06:23"},
        job_id: {"id": job_id, "sourceType": "job_description", "quote": "负责实验设计和数据分析。", "locator": "字符 0-12"},
    }
    topic = {
        "id": "topic-1", "version": 1, "interviewerQuestion": "请介绍项目。",
        "followUpTurns": [{"id": "follow-1", "interviewerQuestion": "请补充说明最终结果。", "candidateAnswer": "转化率提升 20%。"}],
    }
    submit = SubmitTopicReviewTool(topic, registry, has_jd=True)
    payload = _topic_submission("topic-1", answer_id, job_id, follow_ups=["follow-1"])
    payload["interviewerSignals"] = [{
        "turnId": "06:23", "type": "verify_data", "interpretation": "追问要求核查结果数据。",
        "confidence": "high", "evidenceIds": [question_id],
    }]

    response = submit.run({"review_json": json.dumps(payload, ensure_ascii=False)})

    assert response.status.value == "success"
    assert submit.last_submission["interviewerSignals"][0]["turnId"] == "follow-1"


def test_topic_submission_drops_interviewer_signals_without_followups():
    answer_id = "ev-answer"
    question_id = "ev-main-question"
    job_id = "ev-job"
    registry = {
        answer_id: {"id": answer_id, "sourceType": "transcript", "quote": "我推动实验，转化率提升 20%。", "locator": "字符 10-30"},
        question_id: {"id": question_id, "sourceType": "transcript", "quote": "请介绍项目。", "locator": "00:00"},
        job_id: {"id": job_id, "sourceType": "job_description", "quote": "负责实验设计和数据分析。", "locator": "字符 0-12"},
    }
    submit = SubmitTopicReviewTool(
        {"id": "topic-1", "version": 1, "interviewerQuestion": "请介绍项目。", "followUpTurns": []},
        registry,
        has_jd=True,
    )
    payload = _topic_submission("topic-1", answer_id, job_id)
    payload["interviewerSignals"] = [{
        "turnId": "00:00", "type": "request_detail", "interpretation": "主问题要求介绍项目。",
        "confidence": "medium", "evidenceIds": [question_id],
    }]

    response = submit.run({"review_json": json.dumps(payload, ensure_ascii=False)})

    assert response.status.value == "success"
    assert submit.last_submission["interviewerSignals"] == []


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
    assert {"KnowledgeSearch", "EvidenceLookup", "WebVerify", "SubmitTopicReview", "Task"}.issubset(names)
    assert "Score" not in names
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
