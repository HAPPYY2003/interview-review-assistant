import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.agents.runtime import AgentRuntimeResult
from backend.app.schemas import PracticeBrief, PracticeReview
from backend.app.services.practice import PracticeService


def _brief_payload() -> dict:
    return {
        "mode": "oral_answer",
        "objective": "重新组织项目回答",
        "why": "原回答需要补充结构和事实边界。",
        "linkedGapIds": ["gap-1"],
        "linkedTopicIds": ["topic-1"],
        "allowedEvidenceIds": ["evidence-1"],
        "steps": ["先给结论", "补充行动", "说明结果"],
        "prompt": "请只使用确认事实重新回答当前问题。",
        "rubric": [
            {"id": "focus", "label": "回答聚焦", "criterion": "直接回应问题。"},
            {"id": "evidence", "label": "事实支持", "criterion": "关键结论有事实支持。"},
        ],
        "successCriterion": "完成一次结构清楚且可回查的回答。",
        "estimatedMinutes": 10,
    }


def test_practice_brief_rejects_invalid_mode_and_step_count():
    invalid_mode = {**_brief_payload(), "mode": "video_interview"}
    too_short = {**_brief_payload(), "steps": ["只做一步"]}

    with pytest.raises(ValidationError):
        PracticeBrief.model_validate(invalid_mode)
    with pytest.raises(ValidationError):
        PracticeBrief.model_validate(too_short)


def test_practice_service_rejects_unknown_context_references():
    context = {
        "gaps": [{"id": "gap-1"}],
        "topics": [{"id": "topic-1"}],
        "evidence": [{"id": "evidence-1"}],
    }
    candidate = {**_brief_payload(), "allowedEvidenceIds": ["evidence-unknown"]}

    with pytest.raises(ValueError, match="未知证据"):
        PracticeService._validate_brief(candidate, context, "oral_answer")


def test_practice_review_requires_exact_rubric_coverage():
    brief = _brief_payload()
    incomplete = {
        "summary": "回答已有基本结构。",
        "rubricResults": [
            {"rubricId": "focus", "status": "partially_met", "feedback": "结论仍可更直接。"},
            {"rubricId": "unknown", "status": "not_met", "feedback": "该标准不属于本次练习。"},
        ],
        "strengths": ["已经回应问题。"],
        "improvements": ["补充事实依据。"],
        "factualRisks": [],
        "nextAttemptFocus": "补充事实并再次复述。",
        "completionRecommended": False,
    }

    PracticeReview.model_validate(incomplete)
    with pytest.raises(ValueError, match="完整覆盖"):
        PracticeService._validate_review(incomplete, brief)


def test_invalid_primary_review_uses_structured_finalizer():
    brief = _brief_payload()
    valid_review = {
        "summary": "回答已经覆盖当前训练目标。",
        "rubricResults": [
            {"rubricId": "focus", "status": "met", "feedback": "能够直接回应问题。"},
            {"rubricId": "evidence", "status": "partially_met", "feedback": "还可补充验证方式。"},
        ],
        "strengths": ["回答聚焦。"],
        "improvements": ["补充验证过程。"],
        "factualRisks": [],
        "nextAttemptFocus": "下一次重点说明结果如何验证。",
        "completionRecommended": False,
    }

    class FakeDatabase:
        def __init__(self):
            self.attempt_updates = []
            self.session_updates = []

        def get_practice_session(self, _session_id):
            return {"id": "session-1", "runId": "run-1", "actionId": "action-1", "brief": brief}

        def get_practice_attempt(self, _attempt_id):
            return {"id": "attempt-1", "responseText": "我先回答核心问题，再说明真实行动和结果。"}

        def update_practice_attempt(self, _attempt_id, updates):
            self.attempt_updates.append(updates)

        def update_practice_session(self, _session_id, updates):
            self.session_updates.append(updates)

    class FakeRuntime:
        def __init__(self):
            self.finalizer_called = False

        def review_practice_response(self, _prompt):
            return AgentRuntimeResult(text="not-json")

        def finalize_practice_review(self, _prompt):
            self.finalizer_called = True
            return AgentRuntimeResult(text=json.dumps(valid_review, ensure_ascii=False))

    database = FakeDatabase()
    service = PracticeService.__new__(PracticeService)
    service.db = database
    service.settings = SimpleNamespace(real_agent_enabled=True, practice_review_timeout=60)
    service.runtime = FakeRuntime()
    service.action_context = lambda _run_id, _action_id: {
        "action": {}, "gaps": [], "topics": [], "evidence": [],
    }
    service._with_timeout = lambda callback, _timeout: callback()

    service.review_attempt("session-1", "attempt-1")

    assert service.runtime.finalizer_called is True
    assert database.attempt_updates[-1]["status"] == "reviewed"
    assert database.attempt_updates[-1]["review"]["rubricResults"] == valid_review["rubricResults"]
    assert database.session_updates[-1]["status"] == "reviewed"
