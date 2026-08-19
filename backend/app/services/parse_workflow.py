from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.app.agents.runtime import HelloAgentsRuntime
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.services.audio import DeepgramTranscriptionProvider, deepgram_segments, inspect_audio
from backend.app.services.evidence import QUESTION_TYPES, infer_topic_title, normalize_question_type
from backend.app.services.transcript import (
    build_question_cards,
    map_speaker_roles,
    validate_question_cards,
    validate_segments,
)
from backend.app.services.transcript_structure import (
    ConfidenceAssessment,
    ConfirmationReasonCode,
    QuestionTurnBatch,
    TranscriptProfile,
    UtteranceBatch,
    atomize_text,
    atoms_from_audio_segments,
    calculate_turn_confidence,
    chunk_atoms,
    chunk_utterances,
    fallback_utterances,
    profile_transcript,
    utterances_from_submission,
)
from backend.app.tools.parse_tools import build_parse_tools


ASSESSMENT_REASON_RULES = {
    "question_boundary_assessment": (
        {ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN, ConfirmationReasonCode.SOURCE_QUALITY_LOW},
        ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN,
    ),
    "answer_boundary_assessment": (
        {
            ConfirmationReasonCode.ANSWER_BOUNDARY_UNCERTAIN,
            ConfirmationReasonCode.ANSWER_MISSING,
            ConfirmationReasonCode.SOURCE_QUALITY_LOW,
        },
        ConfirmationReasonCode.ANSWER_BOUNDARY_UNCERTAIN,
    ),
    "qa_pairing_assessment": (
        {ConfirmationReasonCode.QA_PAIRING_AMBIGUOUS, ConfirmationReasonCode.ANSWER_MISSING},
        ConfirmationReasonCode.QA_PAIRING_AMBIGUOUS,
    ),
    "follow_up_assessment": (
        {ConfirmationReasonCode.MAIN_FOLLOWUP_UNCERTAIN, ConfirmationReasonCode.FOLLOWUP_PARENT_UNCERTAIN},
        ConfirmationReasonCode.MAIN_FOLLOWUP_UNCERTAIN,
    ),
    "question_type_assessment": (
        {ConfirmationReasonCode.QUESTION_TYPE_UNCERTAIN},
        ConfirmationReasonCode.QUESTION_TYPE_UNCERTAIN,
    ),
    "topic_grouping_assessment": (
        {ConfirmationReasonCode.TOPIC_GROUPING_UNCERTAIN},
        ConfirmationReasonCode.TOPIC_GROUPING_UNCERTAIN,
    ),
}


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
        self.raw_source = ""
        self.profile: TranscriptProfile | None = None
        self.atoms: list[dict[str, Any]] = []
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
            self.raw_source = text
            self.profile = profile_transcript(text)
            self.atoms = self._scope_atom_ids(atomize_text(text, self.profile))
            self.segments = self._scope_segment_ids(fallback_utterances(self.atoms, self.profile, text))
            self._write_json("profile.json", self.profile.as_dict())
            self._write_json("atoms.json", self.atoms)
            return {
                "artifactId": self.parse_run_id, "segmentCount": len(self.segments), "atomCount": len(self.atoms),
                "provider": "text", "profile": self.profile.as_dict(),
            }

        self.workflow.phase(self.parse_run_id, "TRANSCRIBING", "正在通过 Deepgram 生成时间戳和说话人片段")
        provider = DeepgramTranscriptionProvider(self.settings)
        payload, retries = provider.transcribe(self.workflow.material_path(self.material), self.inspection.get("mimeType", "application/octet-stream"))
        self._write_json("deepgram.json", payload)
        provider_segments = deepgram_segments(payload)
        map_speaker_roles(provider_segments)
        self.profile, self.atoms = atoms_from_audio_segments(provider_segments, payload)
        self.atoms = self._scope_atom_ids(self.atoms)
        self.segments = self._scope_segment_ids(self._audio_utterances(provider_segments))
        self._write_json("profile.json", self.profile.as_dict())
        self._write_json("atoms.json", self.atoms)
        self.db.update_parse_run(self.parse_run_id, retry_count=retries, artifact_id=self.parse_run_id)
        self.workflow.tool_event(self.parse_run_id, "DeepgramTranscription", {"segmentCount": len(self.segments), "retryCount": retries})
        return {
            "artifactId": self.parse_run_id, "segmentCount": len(self.segments), "atomCount": len(self.atoms),
            "provider": "deepgram", "retryCount": retries, "profile": self.profile.as_dict(),
        }

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
            {"segmentCount": len(self.segments), "atomCount": len(self.atoms), "issueCount": len(validation.issues), "blockingIssueCount": blocking, "averageConfidence": validation.average_confidence},
        )
        return {
            "artifactId": self.parse_run_id,
            "segmentCount": len(self.segments),
            "atomCount": len(self.atoms),
            "issueCount": len(validation.issues),
            "blockingIssueCount": blocking,
            "averageConfidence": validation.average_confidence,
        }

    def structure(self) -> dict[str, Any]:
        if not self.validated:
            self.validate()
        if any(item["severity"] == "blocking" for item in self.validation_issues):
            raise ValueError("转写片段存在阻塞问题，无法继续拆题")
        self.workflow.phase(self.parse_run_id, "STRUCTURING", "正在恢复话轮并识别主问题、回答和追问关系")
        cards: list[dict[str, Any]] = []
        agent_error = ""
        if self.settings.real_agent_enabled:
            try:
                agent_segments = self._build_agent_utterances()
                if agent_segments:
                    self.segments = agent_segments
                cards = self._build_agent_cards()
            except (ValueError, ValidationError, TypeError) as exc:
                agent_error = str(exc)
                self.workflow.tool_event(self.parse_run_id, "StructuredSubmissionRejected", {"message": agent_error[:180]})
        if not cards:
            # A malformed Agent payload is not evidence that the source references
            # are invalid. Let the deterministic parser derive confidence from the
            # actual transcript quality and boundaries.
            cards = self._fallback_cards()
        errors = validate_question_cards(cards, self.segments)
        if errors:
            cards = self._fallback_cards(force_confirmation=True)
            errors = validate_question_cards(cards, self.segments)
        self.questions = cards
        self._write_json("segments.json", self.segments)
        self._write_json("questions.json", cards)
        atom_chunks = len(chunk_atoms(self.atoms)) if self.profile and self.profile.profile_type == "raw_stream" else 1
        dialogue_chunks = len(chunk_utterances(self.segments))
        self.workflow.tool_event(self.parse_run_id, "TranscriptStructuring", {
            "atomChunkCount": atom_chunks, "dialogueChunkCount": dialogue_chunks, "questionCount": len(cards),
            "validationErrors": len(errors), "agentFallback": bool(agent_error),
        })
        return {
            "artifactId": self.parse_run_id, "atomChunkCount": atom_chunks, "dialogueChunkCount": dialogue_chunks,
            "questionCount": len(cards), "validationErrors": errors[:10], "agentFallback": bool(agent_error),
        }

    def submit(self) -> dict[str, Any]:
        if not self.questions:
            self.structure()
        self.workflow.phase(self.parse_run_id, "SUBMITTING", "正在回查片段引用并提交候选题卡")
        errors = validate_question_cards(self.questions, self.segments)
        self.accepted = bool(self.questions) and not errors
        data = {"accepted": self.accepted, "artifactId": self.parse_run_id, "questionCount": len(self.questions), "errors": errors[:10]}
        self.workflow.tool_event(self.parse_run_id, "SubmitQuestionCards", {"accepted": self.accepted, "questionCount": len(self.questions), "errorCount": len(errors)})
        return data

    def _build_agent_utterances(self) -> list[dict[str, Any]]:
        if not self.profile or not self.atoms:
            return []
        chunks = chunk_atoms(self.atoms) if self.profile.profile_type == "raw_stream" else [self.atoms]
        unique: dict[tuple[str, ...], dict[str, Any]] = {}
        atom_order = {item["id"]: item["ordinal"] for item in self.atoms}
        for chunk_index, chunk in enumerate(chunks, 1):
            core_start = chunk[min(24, len(chunk) - 1)]["id"] if self.profile.profile_type == "raw_stream" and chunk_index > 1 else chunk[0]["id"]
            first_payload = self.runtime.run_utterance_worker(chunk, "boundary_first", core_start)
            if not first_payload:
                continue
            selected_payload, unresolved = first_payload, False
            if self.profile.profile_type == "raw_stream":
                second_payload = self.runtime.run_utterance_worker(chunk, "speaker_first", core_start)
                if second_payload and self._utterance_signature(first_payload) != self._utterance_signature(second_payload):
                    audit = self.runtime.run_parse_auditor(first_payload, second_payload) or {}
                    selection = audit.get("selected")
                    selected_payload = second_payload if selection == "speaker_first" else first_payload
                    unresolved = selection not in {"boundary_first", "speaker_first"}
            batch = UtteranceBatch.model_validate(selected_payload)
            self._validate_assessment_atoms(batch.model_dump(mode="json"), {item["id"] for item in chunk})
            converted = utterances_from_submission(batch, chunk, self.raw_source, self.profile)
            for utterance in converted:
                if atom_order.get(utterance["atomIds"][0], 0) < atom_order.get(core_start, 0):
                    continue
                signature = tuple(utterance["atomIds"])
                if unresolved:
                    self._append_reason(utterance, ConfirmationReasonCode.CHUNK_OVERLAP_CONFLICT, "chunk", 0, signature)
                existing = unique.get(signature)
                if existing and (existing.get("speakerRole") != utterance.get("speakerRole") or existing.get("rawText") != utterance.get("rawText")):
                    self._append_reason(existing, ConfirmationReasonCode.CHUNK_OVERLAP_CONFLICT, "chunk", 0, signature)
                elif not existing:
                    unique[signature] = utterance
        result = sorted(unique.values(), key=lambda item: atom_order.get(item["atomIds"][0], 10**9))
        covered = {atom_id for item in result for atom_id in item.get("atomIds", [])}
        usable = {item["id"] for item in self.atoms if str(item.get("rawText", "")).strip()}
        if not result or len(covered) / max(1, len(usable)) < 0.8:
            raise ValueError("UTTERANCE_COVERAGE_TOO_LOW")
        for index, item in enumerate(result, 1):
            item["id"] = f"{self.material_id}:U{index:04d}"
            item["ordinal"] = index
        return result

    def _build_agent_cards(self) -> list[dict[str, Any]]:
        utterance_map = {item["id"]: item for item in self.segments}
        atom_ids = {item["id"] for item in self.atoms}
        submissions: dict[tuple[str, ...], Any] = {}
        conflicts: set[tuple[str, ...]] = set()
        for chunk in chunk_utterances(self.segments):
            payload = None
            last_error: Exception | None = None
            for _ in range(2):
                try:
                    payload = self.runtime.run_dialogue_worker(chunk)
                    if not payload:
                        raise ValueError("EMPTY_DIALOGUE_SUBMISSION")
                    payload = self._sanitize_dialogue_payload(payload, chunk)
                    batch = QuestionTurnBatch.model_validate(payload)
                    self._validate_assessment_atoms(batch.model_dump(mode="json"), atom_ids)
                    allowed = {item["id"] for item in chunk}
                    for turn in batch.question_turns:
                        if any(item not in allowed for item in [*turn.question_utterance_ids, *turn.answer_utterance_ids]):
                            raise ValueError("DIALOGUE_UNKNOWN_UTTERANCE")
                    for turn in batch.question_turns:
                        signature = tuple(turn.question_utterance_ids)
                        existing = submissions.get(signature)
                        if existing and existing.model_dump() != turn.model_dump():
                            conflicts.add(signature)
                        else:
                            submissions.setdefault(signature, turn)
                    last_error = None
                    break
                except (ValidationError, ValueError) as exc:
                    last_error = exc
            if last_error:
                raise last_error
        ordered = sorted(submissions.items(), key=lambda item: utterance_map[item[0][0]]["ordinal"])
        cards: list[dict[str, Any]] = []
        roots_by_anchor: dict[str, dict[str, Any]] = {}
        for signature, turn in ordered:
            q_segments = [utterance_map[item] for item in turn.question_utterance_ids]
            a_segments = [utterance_map[item] for item in turn.answer_utterance_ids]
            question_text = "\n".join(item["rawText"] for item in q_segments).strip()
            answer_text = "\n".join(item["rawText"] for item in a_segments).strip()
            speaker = self._speaker_assessment([*q_segments, *a_segments])
            assessments = {
                "speaker": speaker,
                "questionBoundary": turn.question_boundary_assessment,
                "answerBoundary": turn.answer_boundary_assessment,
                "qaPairing": turn.qa_pairing_assessment,
                "questionType": turn.question_type_assessment,
                "topicGrouping": turn.topic_grouping_assessment,
            }
            if turn.follow_up_assessment:
                assessments["followUp"] = turn.follow_up_assessment
            hard_reasons = []
            if not answer_text:
                hard_reasons.append(ConfirmationReasonCode.ANSWER_MISSING)
            if signature in conflicts or any(self._has_reason(item, ConfirmationReasonCode.CHUNK_OVERLAP_CONFLICT) for item in [*q_segments, *a_segments]):
                hard_reasons.append(ConfirmationReasonCode.CHUNK_OVERLAP_CONFLICT)
            parent = roots_by_anchor.get(turn.parent_question_anchor or "")
            is_follow_up = turn.turn_type == "follow_up" and bool(parent)
            if turn.turn_type == "follow_up" and not parent:
                hard_reasons.append(ConfirmationReasonCode.FOLLOWUP_PARENT_UNCERTAIN)
            confidence = calculate_turn_confidence(turn.turn_type, assessments, self.profile.source_quality, hard_reasons=hard_reasons)
            question_id = str(uuid.uuid4())
            topic_title = parent["title"] if is_follow_up else turn.topic_title or infer_topic_title(question_text, turn.question_type)
            card = {
                "id": question_id, "order": q_segments[0]["ordinal"], "interviewerQuestion": question_text,
                "candidateAnswer": answer_text, "questionType": normalize_question_type(turn.question_type, question_text),
                "initialDiagnosis": [], "confirmed": False, "version": 1,
                "topicRootId": parent["id"] if is_follow_up else question_id,
                "parentQuestionId": parent["id"] if is_follow_up else None,
                "turnType": "follow_up" if is_follow_up else "main", "extractedQuestion": question_text,
                "extractedAnswer": answer_text, "editedQuestion": "", "editedAnswer": "", "topicTitle": topic_title,
                "provenanceStatus": "conflict" if hard_reasons else "source", "followUpImpact": "",
                "questionSegmentIds": list(turn.question_utterance_ids), "answerSegmentIds": list(turn.answer_utterance_ids),
                "parseMethod": "agent", **confidence,
            }
            cards.append(card)
            if not is_follow_up:
                roots_by_anchor[turn.question_utterance_ids[0]] = {"id": question_id, "title": topic_title}
        return cards

    def _sanitize_dialogue_payload(self, payload: dict[str, Any], utterances: list[dict[str, Any]]) -> dict[str, Any]:
        """Normalize common LLM contract drift without changing source boundaries."""
        if not isinstance(payload, dict) or not isinstance(payload.get("question_turns"), list):
            return payload

        utterance_map = {item["id"]: item for item in utterances}
        allowed_atom_ids = {atom_id for item in utterances for atom_id in item.get("atomIds", [])}
        sanitized_turns = []
        for source_turn in payload["question_turns"]:
            if not isinstance(source_turn, dict):
                sanitized_turns.append(source_turn)
                continue
            turn = dict(source_turn)
            turn_type = str(turn.get("turn_type") or "main").strip().lower()
            turn["turn_type"] = "follow_up" if turn_type in {"follow_up", "followup", "follow-up"} else "main"

            question_ids = [item for item in turn.get("question_utterance_ids", []) if item in utterance_map]
            answer_ids = [item for item in turn.get("answer_utterance_ids", []) if item in utterance_map]
            question_atoms = [atom_id for item in question_ids for atom_id in utterance_map[item].get("atomIds", [])]
            answer_atoms = [atom_id for item in answer_ids for atom_id in utterance_map[item].get("atomIds", [])]
            question_text = "\n".join(utterance_map[item].get("rawText", "") for item in question_ids).strip()

            question_type = normalize_question_type(turn.get("question_type"), question_text)
            turn["question_type"] = question_type if question_type in QUESTION_TYPES else "其他"
            topic_title = str(turn.get("topic_title") or "").strip()
            if not topic_title or "\ufffd" in topic_title or len(topic_title) > 40:
                topic_title = infer_topic_title(question_text, turn["question_type"])
            turn["topic_title"] = topic_title[:40]

            evidence_defaults = {
                "question_boundary_assessment": question_atoms,
                "answer_boundary_assessment": answer_atoms or question_atoms,
                "qa_pairing_assessment": [*question_atoms, *answer_atoms],
                "follow_up_assessment": question_atoms,
                "question_type_assessment": question_atoms,
                "topic_grouping_assessment": [*question_atoms, *answer_atoms],
            }
            for field, (allowed_reasons, default_reason) in ASSESSMENT_REASON_RULES.items():
                assessment = turn.get(field)
                if field == "follow_up_assessment" and turn["turn_type"] == "main":
                    turn[field] = None
                    continue
                if not isinstance(assessment, dict):
                    if field != "follow_up_assessment":
                        continue
                    assessment = {"score": 60, "reason_codes": [], "evidence_atom_ids": [], "summary": ""}
                turn[field] = self._sanitize_assessment(
                    assessment,
                    allowed_reasons,
                    default_reason,
                    evidence_defaults[field],
                    allowed_atom_ids,
                )
            sanitized_turns.append(turn)
        return {**payload, "question_turns": sanitized_turns}

    @staticmethod
    def _sanitize_assessment(
        assessment: dict[str, Any],
        allowed_reasons: set[ConfirmationReasonCode],
        default_reason: ConfirmationReasonCode,
        fallback_atom_ids: list[str],
        allowed_atom_ids: set[str],
    ) -> dict[str, Any]:
        data = dict(assessment)
        try:
            score = max(0, min(100, round(float(data.get("score", 50)))))
        except (TypeError, ValueError):
            score = 50

        raw_reasons = data.get("reason_codes") if isinstance(data.get("reason_codes"), list) else []
        reasons: list[ConfirmationReasonCode] = []
        for raw_reason in raw_reasons:
            try:
                reason = ConfirmationReasonCode(raw_reason)
            except ValueError:
                continue
            if reason in allowed_reasons and reason not in reasons:
                reasons.append(reason)

        # reason_codes describe uncertainty only. Some compatible models emit
        # positive labels such as "only_one_interpretation" for high scores.
        if score >= 85:
            reasons = []
        elif not reasons and (score < 80 or raw_reasons):
            reasons = [default_reason]

        evidence = [item for item in data.get("evidence_atom_ids", []) if item in allowed_atom_ids]
        if reasons and not evidence:
            evidence = list(dict.fromkeys(item for item in fallback_atom_ids if item in allowed_atom_ids))
        summary = str(data.get("summary") or "").strip()
        if "\ufffd" in summary:
            summary = ""
        return {
            "score": score,
            "reason_codes": [item.value for item in reasons],
            "evidence_atom_ids": evidence,
            "summary": summary[:120],
        }

    def _fallback_cards(self, force_confirmation: bool = False) -> list[dict[str, Any]]:
        cards = build_question_cards(self.segments)
        segment_map = {item["id"]: item for item in self.segments}
        for card in cards:
            q_segments = [segment_map[item] for item in card.get("questionSegmentIds", []) if item in segment_map]
            a_segments = [segment_map[item] for item in card.get("answerSegmentIds", []) if item in segment_map]
            atom_refs = [atom_id for item in [*q_segments, *a_segments] for atom_id in item.get("atomIds", [])]
            speaker = self._speaker_assessment([*q_segments, *a_segments])
            boundary_score = min((round(float(item.get("confidence", 0)) * 100) for item in [*q_segments, *a_segments]), default=50)
            boundary_code = ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN if boundary_score < 80 else None
            answer_code = ConfirmationReasonCode.ANSWER_BOUNDARY_UNCERTAIN if boundary_score < 80 else None
            qa_score = 92 if a_segments and all(item.get("speakerRole") != "unknown" for item in [*q_segments, *a_segments]) else 68 if a_segments else 40
            assessments = {
                "speaker": speaker,
                "questionBoundary": self._assessment(boundary_score, boundary_code, atom_refs),
                "answerBoundary": self._assessment(boundary_score if a_segments else 40, answer_code if a_segments else ConfirmationReasonCode.ANSWER_MISSING, atom_refs),
                "qaPairing": self._assessment(qa_score, None if qa_score >= 80 else ConfirmationReasonCode.QA_PAIRING_AMBIGUOUS, atom_refs),
                "questionType": self._assessment(86, None, atom_refs),
                "topicGrouping": self._assessment(86, None, atom_refs),
            }
            if card.get("turnType") == "follow_up":
                clear_parent = bool(self.profile and self.profile.profile_type == "labeled_lines" and card.get("parentQuestionId"))
                assessments["followUp"] = self._assessment(
                    86 if clear_parent else 72,
                    None if clear_parent else ConfirmationReasonCode.FOLLOWUP_PARENT_UNCERTAIN,
                    atom_refs,
                )
            hard = []
            if not a_segments:
                hard.append(ConfirmationReasonCode.ANSWER_MISSING)
            if force_confirmation:
                hard.append(ConfirmationReasonCode.REFERENCE_VALIDATION_FAILED)
            confidence = calculate_turn_confidence(card.get("turnType", "main"), assessments, self.profile.source_quality if self.profile else 50, hard_reasons=hard)
            card.update(confidence)
            card["parseMethod"] = "fallback" if force_confirmation else "deterministic"
            card["provenanceStatus"] = "fallback" if force_confirmation else card.get("provenanceStatus", "source")
        return cards

    @staticmethod
    def _assessment(score: int, code: ConfirmationReasonCode | None, atom_ids: list[str]) -> ConfidenceAssessment:
        return ConfidenceAssessment(score=score, reason_codes=[code] if code else [], evidence_atom_ids=atom_ids if code else [], summary="")

    def _speaker_assessment(self, segments: list[dict[str, Any]]) -> ConfidenceAssessment:
        if not segments:
            return self._assessment(40, ConfirmationReasonCode.SPEAKER_ROLE_UNCERTAIN, [item["id"] for item in self.atoms[:1]])
        candidates = []
        for segment in segments:
            payload = (segment.get("confidenceDetails") or {}).get("speaker")
            if payload:
                try:
                    candidates.append(ConfidenceAssessment.model_validate(payload))
                    continue
                except ValidationError:
                    pass
            atom_ids = segment.get("atomIds", [])
            score = round(float(segment.get("speakerConfidence") or 0.55) * 100)
            code = ConfirmationReasonCode.SPEAKER_ROLE_UNCERTAIN if score < 80 or segment.get("speakerRole") == "unknown" else None
            candidates.append(self._assessment(score, code, atom_ids))
        return min(candidates, key=lambda item: item.score)

    @staticmethod
    def _validate_assessment_atoms(payload: Any, allowed: set[str]) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key == "evidence_atom_ids" and any(item not in allowed for item in value):
                    raise ValueError("ASSESSMENT_UNKNOWN_ATOM")
                ParsePipelineContext._validate_assessment_atoms(value, allowed)
        elif isinstance(payload, list):
            for item in payload:
                ParsePipelineContext._validate_assessment_atoms(item, allowed)

    @staticmethod
    def _utterance_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
        return tuple((tuple(item.get("atom_ids", [])), item.get("speaker_role")) for item in payload.get("utterances", []))

    @staticmethod
    def _has_reason(item: dict[str, Any], code: ConfirmationReasonCode) -> bool:
        return any(reason.get("code") == code.value for reason in item.get("confirmationReasons", []))

    @staticmethod
    def _append_reason(item: dict[str, Any], code: ConfirmationReasonCode, dimension: str, score: int, atom_ids: Any) -> None:
        reasons = item.setdefault("confirmationReasons", [])
        if not any(reason.get("code") == code.value for reason in reasons):
            reasons.append({"code": code.value, "label": "分块结果冲突", "dimension": dimension, "score": score, "evidenceAtomIds": list(atom_ids), "impact": 10000, "summary": ""})
        item["needsConfirmation"] = True

    def _write_json(self, filename: str, payload: Any) -> None:
        (self.artifact_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _scope_segment_ids(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for segment in segments:
            local_id = str(segment["id"])
            segment["id"] = f"{self.material_id}:{local_id}"
        return segments

    def _scope_atom_ids(self, atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for atom in atoms:
            atom["id"] = f"{self.material_id}:{atom['id']}"
        return atoms

    def _audio_utterances(self, provider_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_group: dict[int, list[str]] = {}
        for atom in self.atoms:
            by_group.setdefault(int(atom.get("groupId", atom.get("ordinal", 0))), []).append(atom["id"])
        result = []
        for index, source in enumerate(provider_segments, 1):
            item = dict(source)
            atom_ids = by_group.get(index, [])
            score = round(float(source.get("confidence", 0)) * 100)
            reasons = []
            if source.get("speakerRole") == "unknown":
                reasons.append({"code": "SPEAKER_ROLE_UNCERTAIN", "label": "说话人身份不明确", "dimension": "speaker", "score": round(float(source.get("speakerConfidence") or 0) * 100), "evidenceAtomIds": atom_ids, "impact": 45, "summary": ""})
            if score < 75:
                reasons.append({"code": "SOURCE_QUALITY_LOW", "label": "原始文稿结构较弱", "dimension": "sourceQuality", "score": score, "evidenceAtomIds": atom_ids, "impact": 100 - score, "summary": "音频转写片段置信度较低"})
            item.update({
                "atomIds": atom_ids, "confidenceDetails": {"sourceQuality": score},
                "confirmationReasons": reasons, "needsConfirmation": bool(reasons or source.get("needsConfirmation")),
                "parseMethod": "deepgram",
            })
            result.append(item)
        return result


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

            questions = self.db.commit_parse_result(interview["id"], material["id"], context.atoms, context.segments, context.questions)
            if context.is_audio:
                transcript = self._render_transcript(context.segments)
                self.db.update_interview(interview["id"], raw_transcript=transcript)
            unresolved = len(self.db.unresolved_segments(interview["id"])) + sum(item.get("needsConfirmation", False) for item in questions)
            metrics = {
                "durationSeconds": round(time.perf_counter() - started, 3),
                "profileType": context.profile.profile_type if context.profile else "unknown",
                "sourceQuality": context.profile.source_quality if context.profile else 0,
                "atomCount": len(context.atoms),
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
        stored_path = Path(material.get("storage_path", ""))
        candidate = stored_path.resolve() if stored_path.is_absolute() else (self.settings.root_dir / stored_path).resolve()
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
