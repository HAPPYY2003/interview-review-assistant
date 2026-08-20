from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import settings
from backend.app.database import ActiveAgentRunError, Database
from backend.app.schemas import (
    ConfirmQuestionsRequest,
    GrowthSnapshotDeleteBatch,
    GrowthSnapshotImportBatch,
    InterviewCreate,
    InterviewImport,
    MaterialText,
    QuestionPatch,
    ReviewRunCreate,
    TranscriptSegmentMergeRequest,
    TranscriptSegmentPatch,
    TranscriptSegmentSplitRequest,
)
from backend.app.services.audio import AudioInspectionError, inspect_audio
from backend.app.services.document_parser import DocumentParseError, DocumentParser
from backend.app.services.evidence import EvidenceReviewService
from backend.app.services.knowledge import KnowledgeBase
from backend.app.services.parse_workflow import ParseWorkflow
from backend.app.services.workflow import REPORT_SCHEMA_VERSION, ReviewWorkflow, public_artifact_receipt


settings.ensure_directories()
database = Database(settings.database_path)
knowledge = KnowledgeBase(settings.knowledge_dir)
review_service = EvidenceReviewService(knowledge)
document_parser = DocumentParser(settings.max_file_bytes)
workflow = ReviewWorkflow(database, review_service, settings)
parse_workflow = ParseWorkflow(database, settings)
background_tasks: set[asyncio.Task[Any]] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    stale_before = (datetime.now(timezone.utc) - timedelta(seconds=settings.agent_task_timeout + 30)).isoformat()
    database.fail_stale_runs(stale_before)
    yield
    for task in list(background_tasks):
        if not task.done():
            task.cancel()


app = FastAPI(title="Offer Radar Agent", version="0.3.0", lifespan=lifespan)
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


def get_parse_run_or_404(run_id: str) -> dict[str, Any]:
    try:
        return database.get_parse_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到解析任务") from exc


def resolve_material_path(stored_path: str) -> Path:
    path = Path(stored_path)
    return path.resolve() if path.is_absolute() else (settings.root_dir / path).resolve()


def serialize_material_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(settings.root_dir.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved)


def remove_agent_files_for_identifiers(root: Path, identifiers: list[str]) -> int:
    """Remove session/trace files that contain identifiers owned by one interview."""
    if not root.exists() or not identifiers:
        return 0
    resolved_root = root.resolve()
    removed = 0
    for candidate in list(resolved_root.rglob("*")):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved_root not in resolved.parents:
            continue
        matched = any(identifier in candidate.name for identifier in identifiers)
        if not matched:
            try:
                content = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            matched = any(identifier in content for identifier in identifiers)
        if matched:
            candidate.unlink(missing_ok=True)
            removed += 1
    return removed


def cleanup_deleted_interview_files(interview_id: str, cleanup: dict[str, list[str]]) -> None:
    """Remove private files after the database record has disappeared from the UI."""
    for stored_path in cleanup["storagePaths"]:
        candidate = resolve_material_path(stored_path)
        uploads = (settings.data_dir / "uploads").resolve()
        if uploads in candidate.parents:
            candidate.unlink(missing_ok=True)
    upload_dir = settings.data_dir / "uploads" / interview_id
    shutil.rmtree(upload_dir, ignore_errors=True)
    for parse_run_id in cleanup["parseRunIds"]:
        shutil.rmtree(settings.data_dir / "parse-runs" / parse_run_id, ignore_errors=True)
    remove_agent_files_for_identifiers(settings.data_dir / "sessions", cleanup["identifiers"])
    remove_agent_files_for_identifiers(settings.data_dir / "traces", cleanup["identifiers"])


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "appVersion": app.version,
        "reportSchemaVersion": REPORT_SCHEMA_VERSION,
        "runtime": "helloagents" if settings.real_agent_enabled else "fixture",
        "demoMode": settings.demo_mode,
        "knowledgeChunks": len(knowledge.documents),
        "webVerifyAvailable": settings.web_verify_enabled and bool(settings.tavily_api_key),
        "audioTranscriptionAvailable": bool(settings.deepgram_api_key) and not settings.demo_mode,
        "asrProvider": settings.asr_provider,
        "maxAudioBytes": settings.max_audio_bytes,
        "maxAudioSeconds": settings.max_audio_seconds,
    }


@app.post("/api/v1/interviews", status_code=201)
def create_interview(payload: InterviewCreate) -> dict[str, Any]:
    return database.create_interview(payload.model_dump(by_alias=False))


@app.get("/api/v1/interviews")
def list_interviews() -> list[dict[str, Any]]:
    return database.list_interviews()


@app.delete("/api/v1/interviews/{interview_id}")
def delete_interview(interview_id: str, background_tasks: BackgroundTasks) -> dict[str, bool]:
    # Deletion is intentionally idempotent so stale browser records and retried
    # requests can still clear any private artifacts left on disk.
    cleanup = database.delete_interview(interview_id)
    background_tasks.add_task(cleanup_deleted_interview_files, interview_id, cleanup)
    return {"deleted": True}


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
    cloud_consent: bool = Form(default=False),
) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="在线示例模式不接受私人材料上传")
    if material_type not in {"job_description", "resume", "transcript", "transcript_audio"}:
        raise HTTPException(status_code=422, detail="材料类型不正确")
    if material_type == "transcript_audio":
        if not cloud_consent:
            raise HTTPException(status_code=422, detail="上传音频前必须确认已获授权并同意发送到 Deepgram")
        suffix = Path(file.filename or "").suffix.lower()
        upload_dir = settings.data_dir / "uploads" / interview_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{uuid.uuid4()}{suffix}"
        size = 0
        try:
            with target.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_audio_bytes:
                        raise AudioInspectionError("音频超过 200MB 限制")
                    handle.write(chunk)
            inspection = inspect_audio(target, file.filename or "", settings)
            relative_path = serialize_material_path(target)
            return database.add_material(
                interview_id,
                material_type,
                "",
                file.filename,
                {"format": inspection.suffix.removeprefix("."), "cloudConsent": True},
                storage_path=relative_path,
                mime_type=inspection.mime_type,
                size_bytes=inspection.size_bytes,
                sha256=inspection.sha256,
                duration_seconds=inspection.duration_seconds,
                processing_status="UPLOADED",
            )
        except AudioInspectionError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            target.unlink(missing_ok=True)
            raise
    content = await file.read(settings.max_file_bytes + 1)
    try:
        parsed = document_parser.parse(file.filename or "", content)
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return database.add_material(interview_id, material_type, parsed.text, file.filename, parsed.metadata)


@app.get("/api/v1/materials/{material_id}/content")
def get_material_content(material_id: str) -> FileResponse:
    try:
        material = database.get_material(material_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到材料") from exc
    if material["material_type"] != "transcript_audio" or not material.get("storage_path"):
        raise HTTPException(status_code=404, detail="该材料没有可回放音频")
    candidate = resolve_material_path(material["storage_path"])
    uploads = (settings.data_dir / "uploads").resolve()
    if uploads not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(candidate, media_type=material.get("mime_type") or "application/octet-stream")


@app.post("/api/v1/interviews/{interview_id}/materials/text")
def add_material_text(interview_id: str, payload: MaterialText) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="材料文本不能为空")
    return database.add_material(interview_id, payload.material_type, payload.text.strip(), payload.filename, {"format": "text", "characters": len(payload.text)})


def _schedule_parse(run_id: str) -> None:
    task = asyncio.create_task(asyncio.to_thread(parse_workflow.execute, run_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@app.post("/api/v1/interviews/{interview_id}/parse", status_code=202)
async def parse_interview(interview_id: str) -> dict[str, Any]:
    interview = get_interview_or_404(interview_id)
    material = database.latest_material(interview_id, ["transcript_audio", "transcript"])
    if not material and interview["raw_transcript"].strip():
        material = database.add_material(
            interview_id,
            "transcript",
            interview["raw_transcript"],
            "pasted-transcript.txt",
            {"format": "text", "characters": len(interview["raw_transcript"])},
        )
    if not material:
        raise HTTPException(status_code=422, detail="面试文字稿或音频不能为空")
    if material["material_type"] == "transcript_audio" and not settings.deepgram_api_key:
        raise HTTPException(status_code=409, detail="音频解析需要在服务端配置 DEEPGRAM_API_KEY")
    provider = "deepgram" if material["material_type"] == "transcript_audio" else "text"
    run = database.create_parse_run(interview_id, material["id"], provider)
    _schedule_parse(run["id"])
    return {
        "parseRunId": run["id"],
        "interviewId": interview_id,
        "status": run["status"],
        "eventsUrl": f"/api/v1/parse-runs/{run['id']}/events",
    }


@app.get("/api/v1/parse-runs/{run_id}")
def get_parse_run(run_id: str) -> dict[str, Any]:
    return get_parse_run_or_404(run_id)


@app.get("/api/v1/parse-runs/{run_id}/events")
async def stream_parse_events(run_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
    get_parse_run_or_404(run_id)
    try:
        cursor = int(last_event_id or 0)
    except ValueError:
        cursor = 0

    async def generate():
        nonlocal cursor
        idle = 0
        while True:
            run = database.get_parse_run(run_id)
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


@app.get("/api/v1/interviews/{interview_id}/segments")
def get_interview_segments(interview_id: str, include_atoms: bool = Query(default=False, alias="includeAtoms")) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    audio = database.latest_material(interview_id, ["transcript_audio"])
    return {
        "segments": database.get_segments(interview_id),
        "atoms": database.get_atoms(interview_id) if include_atoms else [],
        "topics": database.get_question_topics(interview_id),
        "unresolvedCount": len(database.unresolved_segments(interview_id)),
        "audio": {
            "materialId": audio["id"],
            "filename": audio.get("filename", ""),
            "durationSeconds": audio.get("duration_seconds"),
            "url": f"/api/v1/materials/{audio['id']}/content",
        } if audio else None,
    }


@app.patch("/api/v1/interviews/{interview_id}/segments")
def patch_interview_segments(interview_id: str, payload: TranscriptSegmentPatch) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    segments = database.update_segments(interview_id, [item.model_dump(by_alias=True, exclude_none=True) for item in payload.segments])
    database.update_interview(interview_id, status="WAITING_CONFIRMATION", latest_run_id=None)
    return {"segments": segments, "unresolvedCount": len(database.unresolved_segments(interview_id)), "invalidatedPreviousReport": True}


@app.post("/api/v1/interviews/{interview_id}/segments/{segment_id}/split")
def split_interview_segment(interview_id: str, segment_id: str, payload: TranscriptSegmentSplitRequest) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    try:
        segments = database.split_segment(
            interview_id,
            segment_id,
            payload.after_atom_id,
            question_id=payload.turn_id,
            left_assignment=payload.left_assignment,
            right_assignment=payload.right_assignment,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="原文话轮不存在")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    database.update_interview(interview_id, status="WAITING_CONFIRMATION", latest_run_id=None)
    return {
        "segments": segments, "atoms": database.get_atoms(interview_id), "topics": database.get_question_topics(interview_id),
        "unresolvedCount": len(database.unresolved_segments(interview_id)), "invalidatedPreviousReport": True,
    }


@app.post("/api/v1/interviews/{interview_id}/segments/merge")
def merge_interview_segments(interview_id: str, payload: TranscriptSegmentMergeRequest) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    try:
        segments = database.merge_segments(interview_id, payload.segment_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="原文话轮不存在")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    database.update_interview(interview_id, status="WAITING_CONFIRMATION", latest_run_id=None)
    return {
        "segments": segments, "atoms": database.get_atoms(interview_id), "topics": database.get_question_topics(interview_id),
        "unresolvedCount": len(database.unresolved_segments(interview_id)), "invalidatedPreviousReport": True,
    }


@app.patch("/api/v1/interviews/{interview_id}/questions")
def patch_questions(interview_id: str, payload: QuestionPatch) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    segment_map = {item["id"]: item for item in database.get_segments(interview_id)}
    prepared = []
    for model in payload.questions:
        item = model.model_dump(by_alias=True)
        question_ids = item.get("questionSegmentIds", [])
        answer_ids = item.get("answerSegmentIds", [])
        overlap = set(question_ids) & set(answer_ids)
        if overlap:
            raise HTTPException(status_code=422, detail=f"同一话轮不能同时归属问题和回答：{next(iter(overlap))}")
        missing = [segment_id for segment_id in [*question_ids, *answer_ids] if segment_id not in segment_map]
        if missing:
            raise HTTPException(status_code=422, detail=f"题卡引用了不存在的话轮：{missing[0]}")
        item["extractedQuestion"] = (
            "\n".join(segment_map[segment_id]["rawText"] for segment_id in question_ids).strip()
            if question_ids else str(item.get("extractedQuestion") or item.get("interviewerQuestion") or "").strip()
        )
        item["extractedAnswer"] = (
            "\n".join(segment_map[segment_id]["rawText"] for segment_id in answer_ids).strip()
            if answer_ids else (
                "" if question_ids else str(item.get("extractedAnswer") or item.get("candidateAnswer") or "").strip()
            )
        )
        if not item.get("editedQuestion"):
            item["interviewerQuestion"] = item["extractedQuestion"]
        if not item.get("editedAnswer"):
            item["candidateAnswer"] = item["extractedAnswer"]
        if not str(item.get("interviewerQuestion", "")).strip():
            raise HTTPException(status_code=422, detail="题卡问题原文不能为空，请返回解析结果重新分配问题片段")
        resolved_codes = {"QUESTION_BOUNDARY_UNCERTAIN", "ANSWER_BOUNDARY_UNCERTAIN", "QA_PAIRING_AMBIGUOUS", "ANSWER_MISSING", "REFERENCE_VALIDATION_FAILED"}
        item["confirmationReasons"] = [reason for reason in item.get("confirmationReasons", []) if reason.get("code") not in resolved_codes]
        item["needsConfirmation"] = False if item.get("confirmed") else bool(item["confirmationReasons"] or not item.get("interviewerQuestion"))
        if item.get("editedQuestion") or item.get("editedAnswer") or question_ids or answer_ids:
            item["provenanceStatus"] = "edited"
        prepared.append(item)
    questions = database.replace_questions(interview_id, prepared)
    database.update_interview(interview_id, status="WAITING_CONFIRMATION", latest_run_id=None)
    return {"questions": questions, "invalidatedPreviousReport": True}


@app.post("/api/v1/interviews/{interview_id}/confirm")
def confirm_questions(interview_id: str, payload: ConfirmQuestionsRequest | None = Body(default=None)) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    questions = database.get_questions(interview_id)
    if not questions:
        raise HTTPException(status_code=409, detail="没有可确认的问题")
    unresolved_segments = database.unresolved_segments(interview_id)
    unresolved_questions = [item for item in questions if item.get("needsConfirmation")]
    acknowledge = bool(payload and payload.acknowledge_unresolved)
    if (unresolved_segments or unresolved_questions) and not acknowledge:
        raise HTTPException(status_code=409, detail="仍有低置信度或未归类片段，请处理或显式确认忽略")
    ignored_ids = list(payload.ignored_segment_ids) if payload else []
    if acknowledge:
        ignored_ids.extend(item["id"] for item in unresolved_segments)
    database.confirm_questions(interview_id, dict.fromkeys(ignored_ids))
    return {"status": "WAITING_CONFIRMATION", "confirmedCount": len(questions)}


def _schedule_run(run_id: str) -> None:
    task = asyncio.create_task(asyncio.to_thread(workflow.execute, run_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


def _schedule_fallback(run_id: str) -> None:
    task = asyncio.create_task(asyncio.to_thread(workflow.execute_fallback, run_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@app.post("/api/v1/interviews/{interview_id}/review-runs", status_code=202)
async def create_review_run(interview_id: str, payload: ReviewRunCreate | None = Body(default=None)) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    questions = database.get_questions(interview_id)
    if not questions:
        raise HTTPException(status_code=409, detail="没有可复盘的问题")
    if any(not str(item.get("interviewerQuestion", "")).strip() for item in questions):
        raise HTTPException(status_code=409, detail="存在问题原文为空的题卡，请返回人工校对页修复后再复盘")
    review_mode = payload.review_mode if payload else "full"
    unconfirmed = [item for item in questions if not item["confirmed"]]
    unresolved = database.unresolved_segments(interview_id) or [item for item in questions if item.get("needsConfirmation")]
    if review_mode == "full" and unconfirmed:
        raise HTTPException(status_code=409, detail="完整复盘必须先人工确认全部问题")
    if review_mode == "quick" and unconfirmed and not (payload and payload.acknowledge_unreviewed):
        raise HTTPException(status_code=409, detail="快速复盘需要确认使用未经人工校对的题卡")
    if unresolved and not (payload and payload.acknowledge_unresolved):
        raise HTTPException(status_code=409, detail="仍有低置信度或未归类片段，请显式确认后再复盘")
    enable_web = bool(payload.enable_web_verify) if payload else False
    enable_web = enable_web and settings.web_verify_enabled and bool(settings.tavily_api_key)
    if settings.agent_runtime == "helloagents" and not settings.real_agent_enabled:
        raise HTTPException(status_code=503, detail="真实 Agent 模式缺少可用模型配置；系统不会自动切换为 Fixture")
    agent_mode = "fixture" if settings.agent_runtime == "fixture" else "helloagents"
    try:
        run = database.create_run(interview_id, enable_web, review_mode, agent_mode=agent_mode, input_digest=workflow.input_digest(interview_id))
    except ActiveAgentRunError as exc:
        active_interview = database.get_interview(exc.interview_id) or {}
        label = " · ".join(part for part in (active_interview.get("company", ""), active_interview.get("position", "")) if part)
        detail = f"已有另一场真实 Agent 复盘正在运行{f'（{label}）' if label else ''}，请等待其完成后再开始。"
        raise HTTPException(status_code=409, detail=detail, headers={"X-Active-Run-ID": exc.run_id}) from exc
    if not run.get("reused"):
        _schedule_run(run["id"])
    return {"id": run["id"], "interviewId": interview_id, "status": run["status"], "reviewMode": run["review_mode"], "agentMode": run["agent_mode"], "reportSchemaVersion": REPORT_SCHEMA_VERSION, "reused": bool(run.get("reused")), "eventsUrl": f"/api/v1/runs/{run['id']}/events"}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = get_run_or_404(run_id)
    artifacts = database.get_stage_artifacts(run_id)
    run["artifacts"] = [public_artifact_receipt(item) for item in artifacts]
    accepted_topics = {item["topic_id"] for item in artifacts if item["phase"] == "evidence_review" and item["status"] == "ACCEPTED"}
    checkpoint = run.get("checkpoint", {})
    run["progress"] = {
        "completedTopics": len(accepted_topics),
        "auditRound": run.get("audit_round", 0),
        "revisionCount": run.get("revision_count", 0),
        "growthAuditRound": int(checkpoint.get("growthAuditRound") or 0),
        "growthRevisionCount": int(checkpoint.get("growthRevisionCount") or 0),
        "growthAuditAccepted": bool(checkpoint.get("growthAuditAccepted")),
        "checkpoint": checkpoint,
    }
    return run


@app.get("/api/v1/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    get_run_or_404(run_id)
    try:
        cursor = max(int(last_event_id or 0), max(0, int(after)))
    except ValueError:
        cursor = max(0, int(after))

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
    if run.get("input_digest") and run["input_digest"] != workflow.input_digest(run["interview_id"]):
        raise HTTPException(status_code=409, detail="题卡或材料已经修改，请创建新的复盘任务")
    if run.get("agent_mode") == "deterministic_fallback":
        raise HTTPException(status_code=409, detail="降级任务不能恢复为真实 Agent，请创建新的复盘任务")
    database.update_run(run_id, status="REVIEWING", phase="resuming", error="", failure_code="")
    database.append_event(run_id, "RUN_RESUMED", {"message": "从最近已接受的 Agent artifact 恢复"})
    _schedule_run(run_id)
    return {"id": run_id, "status": "REVIEWING"}


@app.post("/api/v1/runs/{run_id}/fallback", status_code=202)
async def fallback_run(run_id: str) -> dict[str, Any]:
    run = get_run_or_404(run_id)
    if run["status"] != "FAILED":
        raise HTTPException(status_code=409, detail="只有失败任务可以由用户主动生成降级报告")
    if run.get("input_digest") and run["input_digest"] != workflow.input_digest(run["interview_id"]):
        raise HTTPException(status_code=409, detail="题卡或材料已经修改，不能生成旧任务的降级报告")
    database.update_run(
        run_id,
        status="REVIEWING",
        phase="fallback_pending",
        agent_mode="deterministic_fallback",
        degraded=True,
        error="",
        failure_code="",
    )
    database.update_interview(run["interview_id"], status="REVIEWING")
    database.append_event(run_id, "FALLBACK_REQUESTED", {"message": "用户主动选择确定性降级报告"})
    _schedule_fallback(run_id)
    return {"id": run_id, "status": "REVIEWING", "agentMode": "deterministic_fallback", "degraded": True}


@app.get("/api/v1/interviews/{interview_id}/report")
def get_report(interview_id: str, run_id: str | None = Query(default=None, alias="runId")) -> dict[str, Any]:
    get_interview_or_404(interview_id)
    try:
        return workflow.report(interview_id, run_id=run_id)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/profile/trends")
def get_trends() -> dict[str, Any]:
    snapshots = database.get_growth_trends()
    return {"snapshots": snapshots, "count": len(snapshots)}


@app.get("/api/v1/profile/trends/candidates")
def get_trend_candidates() -> dict[str, Any]:
    candidates = database.get_growth_candidates()
    return {"candidates": candidates, "count": len(candidates)}


@app.post("/api/v1/profile/trends/import")
def import_trends(payload: GrowthSnapshotImportBatch) -> dict[str, Any]:
    return database.import_growth_snapshots(payload.interview_ids)


@app.post("/api/v1/profile/trends/delete-batch")
def delete_trends_batch(payload: GrowthSnapshotDeleteBatch) -> dict[str, int]:
    snapshot_ids = list(dict.fromkeys(payload.snapshot_ids))
    deleted_count = database.delete_growth_snapshots(snapshot_ids)
    return {"requestedCount": len(snapshot_ids), "deletedCount": deleted_count}


@app.delete("/api/v1/profile/trends/{snapshot_id}")
def delete_trend(snapshot_id: str) -> dict[str, bool]:
    if not database.delete_growth_snapshot(snapshot_id):
        raise HTTPException(status_code=404, detail="成长记录不存在")
    return {"deleted": True}


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
