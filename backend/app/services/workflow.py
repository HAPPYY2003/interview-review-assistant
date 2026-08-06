from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

from backend.app.agents.runtime import HelloAgentsRuntime, write_fixture_session
from backend.app.config import Settings
from backend.app.database import Database, utc_now
from backend.app.services.evidence import EvidenceReviewService
from backend.app.tools import build_agent_tools


class ReviewWorkflow:
    def __init__(self, database: Database, review_service: EvidenceReviewService, settings: Settings):
        self.db = database
        self.review_service = review_service
        self.settings = settings
        self.agent_runtime = HelloAgentsRuntime(settings)

    def execute(self, run_id: str) -> None:
        started = time.perf_counter()
        run = self.db.get_run(run_id)
        interview = self.db.get_interview(run["interview_id"])
        questions = self.db.get_questions(interview["id"])
        trace_path = self.settings.data_dir / "traces" / f"trace-{run_id}.jsonl"
        try:
            self._phase(run_id, "evidence_review", "EvidenceAnalyst", "正在检索原回答、JD、简历与本地知识库")
            batch = self.review_service.review(interview, questions, bool(run["enable_web_verify"]))
            for review in batch["reviews"]:
                self.db.save_evidence(run_id, review["id"], review.get("evidenceRefs", []))
            self._event(run_id, trace_path, "TOOL_FINISHED", {"tool": "KnowledgeSearchTool", "hits": sum(len(item.get("evidenceRefs", [])) for item in batch["reviews"])})

            hello_session_id = None
            if self.settings.real_agent_enabled:
                self._event(run_id, trace_path, "AGENT_STARTED", {"agent": "PlanSolveAgent Supervisor", "mode": "helloagents"})
                agent_context = {"interview": self._safe_interview(interview), "questions": questions}
                tools, _ = build_agent_tools(self.review_service.knowledge, interview, self.settings)
                result = self.agent_runtime.run_supervisor(agent_context, tools=tools)
                hello_session_id = result.session_id
                self._event(run_id, trace_path, "AGENT_FINISHED", {"agent": "PlanSolveAgent Supervisor", "resultCharacters": len(result.text)})

            self._phase(run_id, "reflection_audit", "QualityAuditor", "正在回查引用并检查分数一致性")
            batch = self.review_service.audit(interview, batch)
            self._event(run_id, trace_path, "EVIDENCE_VALIDATED", {"valid": True, "notes": len(batch["auditNotes"])})

            self._phase(run_id, "growth_plan", "GrowthPlanner", "正在结合历史薄弱项生成七天训练计划")
            self.db.save_reviews(run_id, batch["reviews"])
            weak = sorted((key for key in batch["overallScores"] if key != "overall"), key=lambda key: batch["overallScores"][key])[:2]
            self.db.save_growth_snapshot(interview["id"], run_id, batch["overallScores"], weak, batch["actionItems"])
            report_meta = {"summary": batch["summary"], "overallScores": batch["overallScores"], "topRisks": batch["topRisks"], "actionItems": batch["actionItems"], "auditNotes": batch["auditNotes"]}
            elapsed = round(time.perf_counter() - started, 3)
            self.db.update_run(run_id, status="COMPLETED", phase="completed", metrics={"durationSeconds": elapsed, "questionCount": len(questions), "report": report_meta}, hello_session_id=hello_session_id or "")
            self.db.update_interview(interview["id"], status="COMPLETED")
            self._event(run_id, trace_path, "RUN_FINISHED", {"status": "COMPLETED", "durationSeconds": elapsed, "questionCount": len(questions)})
            if not hello_session_id:
                session_id = write_fixture_session(self.settings.data_dir / "sessions", run_id, self.db.get_run(run_id)["events"])
                self.db.update_run(run_id, hello_session_id=session_id)
        except Exception as exc:
            error = str(exc)
            self.db.update_run(run_id, status="FAILED", phase="failed", error=error)
            self.db.update_interview(interview["id"], status="FAILED")
            self._event(run_id, trace_path, "RUN_FAILED", {"status": "FAILED", "message": error})
            self._write_trace(trace_path, {"event": "error", "run_id": run_id, "payload": {"message": error, "traceback": traceback.format_exc(limit=3)}})

    def report(self, interview_id: str) -> dict[str, Any]:
        interview = self.db.get_interview(interview_id)
        run_id = interview.get("latest_run_id")
        if not run_id:
            raise KeyError("该面试还没有复盘任务")
        run = self.db.get_run(run_id)
        if run["status"] != "COMPLETED":
            return {"status": run["status"], "run": run}
        meta = run.get("metrics", {}).get("report", {})
        reviews = self.db.get_reviews(run_id)
        public_interview = {
            "id": interview["id"], "company": interview["company"], "position": interview["position"], "round": interview["round"],
            "interviewDate": interview["interview_date"], "reviewGoal": interview["review_goal"], "analysisMode": interview["analysis_mode"],
            "status": "completed", "summary": meta.get("summary", ""), "overallScores": meta.get("overallScores", {}),
            "topRisks": meta.get("topRisks", []), "auditNotes": meta.get("auditNotes", []),
            "latestAIMetadata": {"provider": "HelloAgents" if self.settings.real_agent_enabled else "Fixture", "model": self.settings.llm_model_id if self.settings.real_agent_enabled else "deterministic-evidence-v1", "promptVersion": "offer-radar-agent-v1", "generatedAt": run["updated_at"]},
        }
        return {"status": "COMPLETED", "interview": public_interview, "questions": reviews, "actions": meta.get("actionItems", []), "run": {key: run[key] for key in ("id", "status", "phase", "hello_session_id", "metrics")}}

    def _phase(self, run_id: str, phase: str, agent: str, message: str) -> None:
        self.db.update_run(run_id, status="AUDITING" if phase == "reflection_audit" else "REVIEWING", phase=phase)
        self.db.update_interview(self.db.get_run(run_id)["interview_id"], status="AUDITING" if phase == "reflection_audit" else "REVIEWING")
        trace_path = self.settings.data_dir / "traces" / f"trace-{run_id}.jsonl"
        self._event(run_id, trace_path, "PHASE_STARTED", {"phase": phase, "agent": agent, "message": message})

    def _event(self, run_id: str, trace_path: Path, event_type: str, data: dict[str, Any]) -> None:
        event = self.db.append_event(run_id, event_type, data)
        self._write_trace(trace_path, {"ts": event["createdAt"], "run_id": run_id, "event": event_type.lower(), "payload": data})

    @staticmethod
    def _write_trace(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        sanitized = json.dumps(payload, ensure_ascii=False).replace("sk-", "sk-***")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(sanitized + "\n")

    @staticmethod
    def _safe_interview(interview: dict[str, Any]) -> dict[str, Any]:
        return {key: interview.get(key) for key in ("company", "position", "round", "review_goal", "analysis_mode", "job_description", "resume_text", "raw_transcript")}
