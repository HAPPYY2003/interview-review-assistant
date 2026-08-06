from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import settings
from backend.app.database import Database
from backend.app.schemas import InterviewCreate, InterviewImport, MaterialText, QuestionPatch, ReviewRunCreate
from backend.app.services.document_parser import DocumentParseError, DocumentParser
from backend.app.services.evidence import EvidenceReviewService
from backend.app.services.knowledge import KnowledgeBase
from backend.app.services.workflow import ReviewWorkflow


settings.ensure_directories()
database = Database(settings.database_path)
knowledge = KnowledgeBase(settings.knowledge_dir)
review_service = EvidenceReviewService(knowledge)
document_parser = DocumentParser(settings.max_file_bytes)
workflow = ReviewWorkflow(database, review_service, settings)
background_tasks: set[asyncio.Task[Any]] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    yield
    for task in list(background_tasks):
        if not task.done():
            task.cancel()


app = FastAPI(title="Offer Radar Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_interview_or_404(interview_id: str) -> dict[str, Any]:
    try:
        return database.get_interview(interview_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到面试记录") from exc


def get_run_or_404(run_id: str) -> dict[str, Any]:
    try:
        return database.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到复盘任务") from exc


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "runtime": "helloagents" if settings.real_agent_enabled else "fixture",
        "demoMode": settings.demo_mode,
        "knowledgeChunks": len(knowledge.documents),
        "webVerifyAvailable": settings.web_verify_enabled and bool(settings.tavily_api_key),
    }


@app.post("/api/v1/interviews", status_code=201)
def create_interview(payload: InterviewCreate) -> dict[str, Any]:
    return database.create_interview(payload.model_dump(by_alias=False))


@app.get("/api/v1/interviews")
def list_interviews() -> list[dict[str, Any]]:
    return database.list_interviews()


@app.post("/api/v1/interviews/import")
def import_interview(payload: InterviewImport) -> dict[str, Any]:
    interview = database.create_interview(payload.interview.model_dump(by_alias=False))
    questions = database.replace_questions(interview["id"], [item.model_dump(by_alias=True) for item in payload.questions])
    database.confirm_questions(interview["id"])
    return {"interview": database.get_interview(interview["id"]), "questions": questions}


@app.post("/api/v1/interviews/{interview_id}/materials")
async def upload_material(
    interview_id: str,
    material_type: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="在线示例模式不接受私人材料上传")
    if material_type not in {"job_description", "resume", "transcript"}:
        raise HTTPException(status_code=422, detail="材料类型不正确")
    content = await file.read(settings.max_file_bytes + 1)
    try:
        parsed = document_parser.parse(file.filename or "", content)
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return database.add_material(interview_id, material_type, parsed.text, file.filename, parsed.metadata)


@app.post("/api/v1/interviews/{interview_id}/materials/text")
def add_material_text(interview_id: str, payload: MaterialText) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="材料文本不能为空")
    return database.add_material(interview_id, payload.material_type, payload.text.strip(), payload.filename, {"format": "text", "characters": len(payload.text)})


@app.post("/api/v1/interviews/{interview_id}/parse")
def parse_interview(interview_id: str) -> dict[str, Any]:
    interview = get_interview_or_404(interview_id)
    if not interview["raw_transcript"].strip():
        raise HTTPException(status_code=422, detail="面试文字稿不能为空")
    database.update_interview(interview_id, status="PARSING")
    questions = review_service.parse_transcript(interview["raw_transcript"])
    if not questions:
        database.update_interview(interview_id, status="FAILED")
        raise HTTPException(status_code=422, detail="未识别到问题，请保留‘面试官：’和‘候选人：’标记")
    database.replace_questions(interview_id, questions)
    database.update_interview(interview_id, status="WAITING_CONFIRMATION")
    return {"questions": questions, "status": "WAITING_CONFIRMATION", "requiresHumanConfirmation": True}


@app.patch("/api/v1/interviews/{interview_id}/questions")
def patch_questions(interview_id: str, payload: QuestionPatch) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    questions = database.replace_questions(interview_id, [item.model_dump(by_alias=True) for item in payload.questions])
    database.update_interview(interview_id, status="WAITING_CONFIRMATION", latest_run_id=None)
    return {"questions": questions, "invalidatedPreviousReport": True}


@app.post("/api/v1/interviews/{interview_id}/confirm")
def confirm_questions(interview_id: str) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    questions = database.get_questions(interview_id)
    if not questions:
        raise HTTPException(status_code=409, detail="没有可确认的问题")
    database.confirm_questions(interview_id)
    return {"status": "WAITING_CONFIRMATION", "confirmedCount": len(questions)}


def _schedule_run(run_id: str) -> None:
    task = asyncio.create_task(asyncio.to_thread(workflow.execute, run_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@app.post("/api/v1/interviews/{interview_id}/review-runs", status_code=202)
async def create_review_run(interview_id: str, payload: ReviewRunCreate | None = Body(default=None)) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    questions = database.get_questions(interview_id)
    if not questions or not all(item["confirmed"] for item in questions):
        raise HTTPException(status_code=409, detail="必须先人工确认全部问题")
    enable_web = bool(payload.enable_web_verify) if payload else False
    enable_web = enable_web and settings.web_verify_enabled and bool(settings.tavily_api_key)
    run = database.create_run(interview_id, enable_web)
    _schedule_run(run["id"])
    return {"id": run["id"], "interviewId": interview_id, "status": run["status"], "eventsUrl": f"/api/v1/runs/{run['id']}/events"}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return get_run_or_404(run_id)


@app.get("/api/v1/runs/{run_id}/events")
async def stream_run_events(run_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
    get_run_or_404(run_id)
    try:
        cursor = int(last_event_id or 0)
    except ValueError:
        cursor = 0

    async def generate():
        nonlocal cursor
        idle = 0
        while True:
            run = database.get_run(run_id)
            pending = [event for event in run["events"] if event["id"] > cursor]
            for event in pending:
                cursor = event["id"]
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield f"id: {event['id']}\nevent: {event['type']}\ndata: {payload}\n\n"
            if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"} and not pending:
                break
            idle += 1
            if idle % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/v1/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str) -> dict[str, Any]:
    run = get_run_or_404(run_id)
    if run["status"] not in {"FAILED", "CANCELLED"}:
        raise HTTPException(status_code=409, detail="只有失败或取消的任务可以恢复")
    database.update_run(run_id, status="REVIEWING", phase="resuming", error="")
    database.append_event(run_id, "RUN_RESUMED", {"message": "从已确认题卡重新执行最近阶段"})
    _schedule_run(run_id)
    return {"id": run_id, "status": "REVIEWING"}


@app.get("/api/v1/interviews/{interview_id}/report")
def get_report(interview_id: str) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    try:
        return workflow.report(interview_id)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/profile/trends")
def get_trends() -> dict[str, Any]:
    snapshots = database.get_growth_trends()
    return {"snapshots": snapshots, "count": len(snapshots)}


# Compatibility API used by the original V2 frontend while the v1 workflow is adopted.
@app.post("/api/materials/analyze")
def legacy_materials(payload: dict[str, Any]) -> dict[str, Any]:
    result = review_service.analyze_materials(payload.get("jobDescription", ""), payload.get("resumeText", ""))
    return {**result, "meta": legacy_meta("materials")}


@app.post("/api/interviews/parse")
def legacy_parse(payload: dict[str, Any]) -> dict[str, Any]:
    questions = review_service.parse_transcript(payload.get("transcript", ""))
    if not questions:
        raise HTTPException(status_code=422, detail="未识别到问题")
    return {"questions": questions, "meta": legacy_meta("parse")}


@app.post("/api/questions/review")
def legacy_review(payload: dict[str, Any]) -> dict[str, Any]:
    interview_input = payload.get("interview", {})
    interview = {
        "job_description": interview_input.get("jobDescription", ""), "resume_text": interview_input.get("resumeText", ""),
        "raw_transcript": interview_input.get("rawTranscript", ""), "analysis_mode": interview_input.get("analysisMode", "full_context"),
    }
    batch = review_service.audit(interview, review_service.review(interview, payload.get("questions", [])))
    return {"reviews": batch["reviews"], "meta": legacy_meta("review")}


@app.post("/api/interviews/action-plan")
def legacy_action_plan(payload: dict[str, Any]) -> dict[str, Any]:
    questions = payload.get("questions", [])
    interview_input = payload.get("interview", {})
    interview = {
        "job_description": interview_input.get("jobDescription", ""), "resume_text": interview_input.get("resumeText", ""),
        "raw_transcript": interview_input.get("rawTranscript", ""), "analysis_mode": interview_input.get("analysisMode", "full_context"),
    }
    batch = review_service.audit(interview, review_service.review(interview, questions))
    return {"summary": batch["summary"], "topRisks": batch["topRisks"], "actionItems": batch["actionItems"], "meta": legacy_meta("action-plan")}


def legacy_meta(task: str) -> dict[str, Any]:
    return {"provider": "HelloAgents" if settings.real_agent_enabled else "Fixture", "model": settings.llm_model_id if settings.real_agent_enabled else "deterministic-evidence-v1", "promptVersion": f"{task}-v1", "generatedAt": datetime.now(timezone.utc).isoformat()}


if settings.frontend_dir.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host=settings.app_host, port=settings.app_port, reload=settings.app_env == "development")
