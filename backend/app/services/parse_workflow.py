from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from backend.app.agents.runtime import HelloAgentsRuntime
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.services.audio import DeepgramTranscriptionProvider, deepgram_segments, inspect_audio
from backend.app.services.evidence import infer_topic_title, normalize_question_type
from backend.app.services.transcript import (
    build_question_cards,
    chunk_segments,
    confidence_label,
    map_speaker_roles,
    merge_worker_cards,
    segment_text,
    validate_question_cards,
    validate_segments,
)
from backend.app.tools.parse_tools import build_parse_tools


class ParsePipelineContext:
    def __init__(self, workflow: "ParseWorkflow", run: dict[str, Any], material: dict[str, Any], interview: dict[str, Any]):
        self.workflow = workflow
        self.db = workflow.db
        self.settings = workflow.settings
        self.runtime = workflow.runtime
        self.parse_run_id = run["id"]
        self.material_id = material["id"]
        self.material = material
        self.interview = interview
        self.artifact_dir = self.settings.data_dir / "parse-runs" / self.parse_run_id
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.inspection: dict[str, Any] | None = None
        self.segments: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self.validation_issues: list[dict[str, str]] = []
        self.validated = False
        self.accepted = False

    @property
    def is_audio(self) -> bool:
        return self.material.get("material_type") == "transcript_audio"

    def inspect(self) -> dict[str, Any]:
        if self.inspection:
            return self.inspection
        self.workflow.phase(self.parse_run_id, "INSPECTING", "正在检查材料格式、大小和时长")
        if self.is_audio:
            path = self.workflow.material_path(self.material)
            result = inspect_audio(path, self.material.get("filename", ""), self.settings).as_dict()
        else:
            text = self.material.get("text") or self.interview.get("raw_transcript", "")
            if not text.strip():
                raise ValueError("面试文字稿为空")
            result = {"format": "text", "sizeBytes": len(text.encode("utf-8")), "characters": len(text)}
        self.inspection = result
        self.workflow.tool_event(self.parse_run_id, "InspectMaterial", {"format": result.get("suffix", result.get("format")), "sizeBytes": result["sizeBytes"]})
        return result

    def transcribe(self) -> dict[str, Any]:
        if self.segments:
            return {"artifactId": self.parse_run_id, "segmentCount": len(self.segments), "reused": True}
        self.inspect()
        if not self.is_audio:
            text = self.material.get("text") or self.interview.get("raw_transcript", "")
            self.segments = self._scope_segment_ids(segment_text(text))
            return {"artifactId": self.parse_run_id, "segmentCount": len(self.segments), "provider": "text"}

        self.workflow.phase(self.parse_run_id, "TRANSCRIBING", "正在通过 Deepgram 生成时间戳和说话人片段")
        provider = DeepgramTranscriptionProvider(self.settings)
        payload, retries = provider.transcribe(self.workflow.material_path(self.material), self.inspection.get("mimeType", "application/octet-stream"))
        self._write_json("deepgram.json", payload)
        self.segments = self._scope_segment_ids(deepgram_segments(payload))
        self.db.update_parse_run(self.parse_run_id, retry_count=retries, artifact_id=self.parse_run_id)
        self.workflow.tool_event(self.parse_run_id, "DeepgramTranscription", {"segmentCount": len(self.segments), "retryCount": retries})
        return {"artifactId": self.parse_run_id, "segmentCount": len(self.segments), "provider": "deepgram", "retryCount": retries}

    def validate(self) -> dict[str, Any]:
        if not self.segments:
            self.transcribe()
        self.workflow.phase(self.parse_run_id, "VALIDATING", "正在检查片段置信度、时间戳和重复内容")
        map_speaker_roles(self.segments)
        validation = validate_segments(self.segments)
        self.segments = validation.segments
        self.validation_issues = validation.issues
        self.validated = True
        self._write_json("segments.json", self.segments)
        blocking = sum(item["severity"] == "blocking" for item in validation.issues)
        self.workflow.tool_event(
            self.parse_run_id,
            "TranscriptValidation",
            {"segmentCount": len(self.segments), "issueCount": len(validation.issues), "blockingIssueCount": blocking, "averageConfidence": validation.average_confidence},
        )
        return {
            "artifactId": self.parse_run_id,
            "segmentCount": len(self.segments),
            "issueCount": len(validation.issues),
            "blockingIssueCount": blocking,
            "averageConfidence": validation.average_confidence,
        }

    def structure(self) -> dict[str, Any]:
        if not self.validated:
            self.validate()
        if any(item["severity"] == "blocking" for item in self.validation_issues):
            raise ValueError("转写片段存在阻塞问题，无法继续拆题")
        self.workflow.phase(self.parse_run_id, "STRUCTURING", "正在分块识别主问题、回答和追问关系")
        chunks = chunk_segments(self.segments)
        cards: list[dict[str, Any]] = []
        if self.settings.real_agent_enabled:
            worker_results = []
            role_overrides = []
            for chunk in chunks:
                result = self.runtime.run_parse_worker(chunk)
                if result:
                    role_overrides.extend(result.get("role_overrides", []))
                    worker_results.append(self._worker_cards(result.get("question_cards", []), chunk))
            self._apply_role_overrides(role_overrides)
            cards = merge_worker_cards(worker_results)
        if not cards:
            cards = build_question_cards(self.segments)
        errors = validate_question_cards(cards, self.segments)
        if errors and self.settings.real_agent_enabled:
            retry_results = []
            for chunk in chunks:
                result = self.runtime.run_parse_worker(chunk)
                if result:
                    retry_results.append(self._worker_cards(result.get("question_cards", []), chunk))
            retried = merge_worker_cards(retry_results)
            if retried and not validate_question_cards(retried, self.segments):
                cards = retried
                errors = []
        if errors:
            cards = build_question_cards(self.segments)
            for card in cards:
                card["needsConfirmation"] = True
        self.questions = cards
        self._write_json("questions.json", cards)
        self.workflow.tool_event(self.parse_run_id, "TranscriptStructuring", {"chunkCount": len(chunks), "questionCount": len(cards), "validationErrors": len(errors)})
        return {"artifactId": self.parse_run_id, "chunkCount": len(chunks), "questionCount": len(cards), "validationErrors": errors[:10]}

    def submit(self) -> dict[str, Any]:
        if not self.questions:
            self.structure()
        self.workflow.phase(self.parse_run_id, "SUBMITTING", "正在回查片段引用并提交候选题卡")
        errors = validate_question_cards(self.questions, self.segments)
        self.accepted = bool(self.questions) and not errors
        data = {"accepted": self.accepted, "artifactId": self.parse_run_id, "questionCount": len(self.questions), "errors": errors[:10]}
        self.workflow.tool_event(self.parse_run_id, "SubmitQuestionCards", {"accepted": self.accepted, "questionCount": len(self.questions), "errorCount": len(errors)})
        return data

    def _worker_cards(self, payloads: list[dict[str, Any]], chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
        segment_map = {item["id"]: item for item in chunk}
        cards: list[dict[str, Any]] = []
        roots_by_segment: dict[str, dict[str, str]] = {}
        for index, payload in enumerate(payloads, 1):
            q_ids = [item for item in payload.get("question_segment_ids", []) if item in segment_map]
            a_ids = [item for item in payload.get("answer_segment_ids", []) if item in segment_map]
            if not q_ids:
                continue
            question_id = str(uuid.uuid4())
            parent_segment = str(payload.get("parent_question_segment_id") or "")
            parent = roots_by_segment.get(parent_segment)
            parent_id = parent.get("id") if parent else None
            is_follow_up = bool(payload.get("is_follow_up")) and bool(parent_id)
            question_text = "\n".join(segment_map[item]["rawText"] for item in q_ids)
            answer_text = "\n".join(segment_map[item]["rawText"] for item in a_ids)
            confidence_value = payload.get("confidence", 0.7)
            needs_confirmation = bool(payload.get("needs_confirmation")) or not a_ids
            if confidence_value in {"high", "medium", "low"}:
                confidence = "medium" if needs_confirmation and confidence_value == "high" else confidence_value
            else:
                confidence = confidence_label(float(confidence_value), needs_confirmation=needs_confirmation)
            question_type = normalize_question_type(payload.get("question_type"), question_text)
            topic_title = roots_by_segment.get(parent_segment, {}).get("title") if is_follow_up else infer_topic_title(question_text, question_type)
            card = {
                "id": question_id,
                "order": segment_map[q_ids[0]]["ordinal"],
                "interviewerQuestion": question_text,
                "candidateAnswer": answer_text,
                "questionType": question_type,
                "confidence": confidence,
                "initialDiagnosis": [],
                "confirmed": False,
                "version": 1,
                "topicRootId": parent_id if is_follow_up else question_id,
                "parentQuestionId": parent_id if is_follow_up else None,
                "turnType": "follow_up" if is_follow_up else "main",
                "extractedQuestion": question_text,
                "extractedAnswer": answer_text,
                "editedQuestion": "",
                "editedAnswer": "",
                "topicTitle": topic_title,
                "needsConfirmation": needs_confirmation,
                "provenanceStatus": "source",
                "followUpImpact": "",
                "questionSegmentIds": q_ids,
                "answerSegmentIds": a_ids,
            }
            cards.append(card)
            if not is_follow_up:
                roots_by_segment[q_ids[0]] = {"id": question_id, "title": topic_title}
        return cards

    def _apply_role_overrides(self, overrides: list[dict[str, Any]]) -> None:
        allowed = {"interviewer", "candidate", "system_noise", "unknown"}
        by_id = {item["id"]: item for item in self.segments}
        for override in overrides:
            segment = by_id.get(str(override.get("segment_id", "")))
            role = str(override.get("speaker_role", ""))
            if segment and role in allowed:
                segment["speakerRole"] = role
                segment["needsConfirmation"] = role == "unknown"

    def _write_json(self, filename: str, payload: Any) -> None:
        (self.artifact_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _scope_segment_ids(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for segment in segments:
            local_id = str(segment["id"])
            segment["id"] = f"{self.material_id}:{local_id}"
        return segments


class ParseWorkflow:
    def __init__(self, database: Database, settings: Settings):
        self.db = database
        self.settings = settings
        self.runtime = HelloAgentsRuntime(settings)

    def execute(self, run_id: str) -> None:
        started = time.perf_counter()
        run = self.db.get_parse_run(run_id)
        interview = self.db.get_interview(run["interview_id"])
        if not run.get("material_id"):
            self._fail(run_id, interview["id"], "解析任务没有绑定材料")
            return
        try:
            material = self.db.get_material(run["material_id"])
            context = ParsePipelineContext(self, run, material, interview)
            tools, submit = build_parse_tools(context)
            if self.settings.real_agent_enabled:
                self.db.append_parse_event(run_id, "AGENT_STARTED", {"agent": "ReActAgent ParseAgent"})
                result = self.runtime.run_parse_agent(context.material_id, context.parse_run_id, tools)
                self.db.append_parse_event(run_id, "AGENT_FINISHED", {"agent": "ReActAgent ParseAgent", "resultCharacters": len(result.text)})
            if not submit.last_submission:
                for tool in tools:
                    parameter = {"material_id": context.material_id} if tool.name in {"InspectMaterial", "DeepgramTranscription"} else {"parse_run_id": context.parse_run_id}
                    tool.run(parameter)
            if not context.accepted:
                raise ValueError("候选题卡未通过最终校验")

            questions = self.db.commit_parse_result(interview["id"], material["id"], context.segments, context.questions)
            if context.is_audio:
                transcript = self._render_transcript(context.segments)
                self.db.update_interview(interview["id"], raw_transcript=transcript)
            unresolved = len(self.db.unresolved_segments(interview["id"])) + sum(item.get("needsConfirmation", False) for item in questions)
            metrics = {
                "durationSeconds": round(time.perf_counter() - started, 3),
                "segmentCount": len(context.segments),
                "questionCount": len(questions),
                "topicCount": len(self.db.get_question_topics(interview["id"])),
                "unresolvedCount": unresolved,
            }
            self.db.append_parse_event(run_id, "PARSE_FINISHED", {"status": "COMPLETED", **metrics})
            self.db.update_parse_run(run_id, status="COMPLETED", phase="completed", metrics=metrics, artifact_id=run_id, error="")
            self.db.update_interview(interview["id"], status="WAITING_CONFIRMATION")
        except Exception as exc:
            self._fail(run_id, interview["id"], str(exc))

    def phase(self, run_id: str, phase: str, message: str) -> None:
        self.db.update_parse_run(run_id, status=phase, phase=phase.lower())
        self.db.append_parse_event(run_id, "PARSE_PHASE_STARTED", {"phase": phase.lower(), "message": message})

    def tool_event(self, run_id: str, tool: str, data: dict[str, Any]) -> None:
        self.db.append_parse_event(run_id, "PARSE_TOOL_FINISHED", {"tool": tool, **data})

    def material_path(self, material: dict[str, Any]) -> Path:
        candidate = (self.settings.root_dir / material.get("storage_path", "")).resolve()
        uploads = (self.settings.data_dir / "uploads").resolve()
        if uploads not in candidate.parents:
            raise ValueError("材料路径不在受控上传目录")
        if not candidate.is_file():
            raise ValueError("音频文件不存在")
        return candidate

    def _fail(self, run_id: str, interview_id: str, message: str) -> None:
        safe_message = re.sub(r"(?:sk-|Token\s+)[A-Za-z0-9_.-]+", "***", message)[:500]
        self.db.append_parse_event(run_id, "PARSE_FAILED", {"status": "FAILED", "message": safe_message})
        self.db.update_parse_run(run_id, status="FAILED", phase="failed", error=safe_message)
        self.db.update_interview(interview_id, status="FAILED")

    @staticmethod
    def _render_transcript(segments: list[dict[str, Any]]) -> str:
        lines = []
        labels = {"interviewer": "面试官", "candidate": "候选人", "system_noise": "系统/噪声", "unknown": "未知说话人"}
        for item in segments:
            timestamp = ""
            if item.get("startTime") is not None:
                seconds = int(item["startTime"])
                timestamp = f"[{seconds // 60:02d}:{seconds % 60:02d}] "
            lines.append(f"{timestamp}{labels.get(item.get('speakerRole'), '未知说话人')}：{item.get('rawText', '')}")
        return "\n".join(lines)
