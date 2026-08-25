from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from backend.app.services.evidence import infer_probe_focus, infer_question_type, infer_topic_title
from backend.app.services.text_utils import (
    parse_transcript_line,
    repair_mojibake,
    subtitle_sequence_line_indexes,
    transcript_uses_short_timeline,
)
QUESTION_RE = re.compile(r"[?？]|^(?:请|你|为什么|怎么|如何|介绍|讲讲|谈谈|说说|哪些|什么|是否|能否)")
CLEAR_QUESTION_RE = re.compile(r"^(?:请|你|您|为什么|怎么|如何|介绍|讲讲|谈谈|说说|哪些|什么|是否|能否|可以|具体|最终|结果|如果|假设|那|再|先|最后)")
FOLLOW_UP_RE = re.compile(r"^(?:你刚才|刚才|具体|为什么|怎么证明|这个|这些|其中|最终|结果|关键|能再|请再|如果)")
NOISE_RE = re.compile(r"(?:听得到吗|麦克风|网络|稍等|录音开始|字幕|转写|谢谢参加|再见)")


@dataclass
class TranscriptValidation:
    segments: list[dict[str, Any]]
    issues: list[dict[str, str]]
    average_confidence: float

    @property
    def blocking(self) -> bool:
        return any(item["severity"] == "blocking" for item in self.issues)


def segment_text(transcript: str) -> list[dict[str, Any]]:
    source = repair_mojibake(transcript or "")
    allow_short_timestamp = transcript_uses_short_timeline(source)
    subtitle_indexes = subtitle_sequence_line_indexes(source)
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line_index, line in enumerate(source.splitlines(keepends=True)):
        content = line.rstrip("\r\n")
        if content.strip() and line_index not in subtitle_indexes:
            leading = len(content) - len(content.lstrip())
            spans.append((cursor + leading, cursor + len(content), content.strip()))
        cursor += len(line)
    if not spans and source.strip():
        start = len(source) - len(source.lstrip())
        spans = [(start, len(source.rstrip()), source.strip())]

    expanded: list[tuple[int, int, str]] = []
    for start, end, text in spans:
        if parse_transcript_line(text, allow_short_timestamp=allow_short_timestamp).speaker_label or len(text) <= 180:
            expanded.append((start, end, text))
            continue
        for match in re.finditer(r".+?(?:[。！？?!；;]+|$)", text):
            part = match.group(0).strip()
            if part:
                expanded.append((start + match.start(), start + match.end(), part))

    segments: list[dict[str, Any]] = []
    for start, end, original in expanded:
        parsed = parse_transcript_line(original, allow_short_timestamp=allow_short_timestamp)
        label = parsed.speaker_label
        text = parsed.text
        if not text:
            continue
        index = len(segments) + 1
        role = _role_from_label(label)
        text_start = start + parsed.text_offset
        confidence = 0.99 if role != "unknown" else 0.58
        segments.append(
            {
                "id": f"S{index:04d}",
                "ordinal": index,
                "rawText": text,
                "normalizedText": " ".join(text.split()),
                "speakerLabel": label,
                "speakerRole": role,
                "startTime": None,
                "endTime": None,
                "startChar": text_start,
                "endChar": text_start + len(text),
                "confidence": confidence,
                "speakerConfidence": 1.0 if role != "unknown" else None,
                "needsConfirmation": role == "unknown",
                "excluded": bool(NOISE_RE.search(text)),
            }
        )
    return segments


def map_speaker_roles(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        if segment.get("speakerLabel"):
            labels[segment["speakerLabel"]].append(segment)
    if not labels:
        _stabilize_unlabeled_qa_sequence(segments)
        return segments

    semantic_roles = {label: _role_from_label(label) for label in labels}
    unresolved_labels = [label for label, role in semantic_roles.items() if role == "unknown"]
    if not unresolved_labels:
        return segments

    scores: list[tuple[float, str]] = []
    for label, items in labels.items():
        if semantic_roles[label] != "unknown":
            continue
        questions = sum(bool(QUESTION_RE.search(item.get("normalizedText", ""))) for item in items)
        scores.append((questions / max(1, len(items)), label))
    scores.sort(reverse=True)
    if len(scores) < 2:
        return segments

    interviewer_labels = {label for ratio, label in scores if ratio >= 0.45}
    if not interviewer_labels:
        interviewer_labels.add(scores[0][1])
    candidate_labels = {label for _, label in scores if label not in interviewer_labels}
    if not candidate_labels and scores[0][0] - scores[-1][0] >= 0.2:
        candidate_labels.add(scores[-1][1])
        interviewer_labels.discard(scores[-1][1])

    ratios = {label: ratio for ratio, label in scores}
    interviewer_floor = min((ratios[label] for label in interviewer_labels), default=0.0)
    candidate_ceiling = max((ratios[label] for label in candidate_labels), default=1.0)

    for segment in segments:
        label = segment.get("speakerLabel", "")
        semantic_role = semantic_roles.get(label, "unknown")
        if semantic_role != "unknown":
            segment["speakerRole"] = semantic_role
            continue
        if label in interviewer_labels:
            margin = ratios[label] - candidate_ceiling
            _apply_inferred_speaker_role(
                segment,
                "interviewer",
                _role_inference_score(margin, len(labels[label])),
                "根据整场同一说话人编号的提问比例完成角色映射",
            )
        elif label in candidate_labels:
            margin = interviewer_floor - ratios[label]
            _apply_inferred_speaker_role(
                segment,
                "candidate",
                _role_inference_score(margin, len(labels[label])),
                "根据整场同一说话人编号的提问比例完成角色映射",
            )
        else:
            segment["speakerRole"] = "unknown"
            segment["needsConfirmation"] = True
    return segments


def validate_segments(segments: list[dict[str, Any]]) -> TranscriptValidation:
    issues: list[dict[str, str]] = []
    previous_end: float | None = None
    previous_text = ""
    confidences = []
    for segment in segments:
        text = segment.get("normalizedText", "").strip()
        if not text:
            segment["excluded"] = True
            issues.append({"severity": "warning", "code": "blank", "segmentId": segment["id"]})
            continue
        confidence = float(segment.get("confidence", 0))
        confidences.append(confidence)
        start, end = segment.get("startTime"), segment.get("endTime")
        if start is not None and end is not None:
            if end < start:
                issues.append({"severity": "blocking", "code": "invalid_timestamp", "segmentId": segment["id"]})
            if previous_end is not None and start < previous_end - 2:
                issues.append({"severity": "warning", "code": "timestamp_overlap", "segmentId": segment["id"]})
            previous_end = max(previous_end or 0, end)
        if text == previous_text:
            segment["excluded"] = True
            segment["needsConfirmation"] = True
            issues.append({"severity": "warning", "code": "adjacent_duplicate", "segmentId": segment["id"]})
        previous_text = text
        if confidence < 0.75:
            segment["needsConfirmation"] = True
    if not any(not item.get("excluded") for item in segments):
        issues.append({"severity": "blocking", "code": "no_usable_segments", "segmentId": ""})
    speakers = {item.get("speakerLabel") for item in segments if item.get("speakerLabel")}
    if len(speakers) > 8:
        issues.append({"severity": "warning", "code": "too_many_speakers", "segmentId": ""})
    average = sum(confidences) / len(confidences) if confidences else 0.0
    return TranscriptValidation(segments, issues, round(average, 3))


def chunk_segments(segments: list[dict[str, Any]], size: int = 40, overlap: int = 6) -> list[list[dict[str, Any]]]:
    usable = [item for item in segments if not item.get("excluded")]
    if not usable:
        return []
    chunks = []
    start = 0
    while start < len(usable):
        end = min(len(usable), start + size)
        if end < len(usable):
            candidates = [i for i in range(max(start + 1, end - 6), end + 1) if usable[i - 1].get("speakerRole") == "candidate"]
            if candidates:
                end = candidates[-1]
        chunks.append(usable[start:end])
        if end >= len(usable):
            break
        start = max(start + 1, end - overlap)
    return chunks


def build_question_cards(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    root: dict[str, Any] | None = None
    for segment in segments:
        if segment.get("excluded"):
            continue
        role = segment.get("speakerRole")
        text = segment.get("normalizedText", segment.get("rawText", "")).strip()
        if role == "system_noise" or not text:
            continue
        is_question = role == "interviewer" or (role == "unknown" and bool(QUESTION_RE.search(text)))
        if is_question:
            if current:
                questions.append(current)
            follow_up = root is not None and bool(FOLLOW_UP_RE.search(text))
            if not follow_up:
                root = None
            question_id = str(uuid.uuid4())
            suggested_type = infer_question_type(text)
            question_type = root["questionType"] if follow_up and root else suggested_type
            current = {
                "id": question_id,
                "order": len(questions) + 1,
                "interviewerQuestion": text,
                "candidateAnswer": "",
                "questionType": question_type,
                "confidence": confidence_label(
                    float(segment.get("confidence", 0)),
                    needs_confirmation=bool(segment.get("needsConfirmation")),
                ),
                "initialDiagnosis": [],
                "confirmed": False,
                "version": 1,
                "topicRootId": root["id"] if follow_up and root else question_id,
                "parentQuestionId": root["id"] if follow_up and root else None,
                "turnType": "follow_up" if follow_up else "main",
                "extractedQuestion": text,
                "extractedAnswer": "",
                "editedQuestion": "",
                "editedAnswer": "",
                "topicTitle": (root or {}).get("topicTitle", "") if follow_up else _topic_title(text),
                "needsConfirmation": bool(segment.get("needsConfirmation")),
                "provenanceStatus": "source",
                "followUpImpact": "",
                "questionSegmentIds": [segment["id"]],
                "answerSegmentIds": [],
                "probeFocus": infer_probe_focus(text) if follow_up else [],
                "probeFocusConfidence": 84 if follow_up else 100,
                "confidenceDetails": {"agentSuggestedQuestionType": suggested_type} if follow_up else {},
            }
            if not follow_up:
                root = current
        elif current:
            current["answerSegmentIds"].append(segment["id"])
            current["extractedAnswer"] += ("\n" if current["extractedAnswer"] else "") + text
            current["candidateAnswer"] = current["extractedAnswer"]
            current["needsConfirmation"] = current["needsConfirmation"] or role == "unknown" or bool(segment.get("needsConfirmation"))
            if current["needsConfirmation"] and current["confidence"] == "high":
                current["confidence"] = "medium"
    if current:
        questions.append(current)
    for index, item in enumerate(questions, 1):
        item["order"] = index
        if not item["candidateAnswer"]:
            item["needsConfirmation"] = True
            item["confidence"] = "low"
    return questions[:80]


def validate_question_cards(cards: list[dict[str, Any]], segments: Iterable[dict[str, Any]]) -> list[str]:
    segment_map = {item["id"]: item for item in segments}
    card_map = {item["id"]: item for item in cards}
    errors: list[str] = []
    for card in cards:
        question_ids = card.get("questionSegmentIds", [])
        answer_ids = card.get("answerSegmentIds", [])
        missing = [segment_id for segment_id in question_ids + answer_ids if segment_id not in segment_map]
        if missing:
            errors.append(f"{card['id']}:missing_segments")
            continue
        if set(question_ids) & set(answer_ids):
            errors.append(f"{card['id']}:segment_role_conflict")
        q_order = [segment_map[item]["ordinal"] for item in question_ids]
        a_order = [segment_map[item]["ordinal"] for item in answer_ids]
        if a_order and q_order and min(a_order) <= max(q_order):
            errors.append(f"{card['id']}:answer_before_question")
        parent = card.get("parentQuestionId")
        if parent and (parent not in card_map or parent == card["id"]):
            errors.append(f"{card['id']}:invalid_parent")
    return errors


def merge_worker_cards(results: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    for cards in results:
        for card in cards:
            signature = tuple(card.get("questionSegmentIds", []))
            existing = unique.get(signature)
            if not existing or _confidence_value(card.get("confidence")) > _confidence_value(existing.get("confidence")):
                unique[signature] = card
            elif existing != card:
                existing["needsConfirmation"] = True
    return sorted(unique.values(), key=lambda item: item.get("order", 0))


def _role_from_label(label: str) -> str:
    normalized = label.lower()
    if normalized in {"面试官", "采访者", "interviewer", "问", "q"}:
        return "interviewer"
    if normalized in {"候选人", "求职者", "candidate", "答", "a"}:
        return "candidate"
    if normalized in {"系统", "system"}:
        return "system_noise"
    return "unknown"


def _role_inference_score(margin: float, sample_count: int) -> int:
    if sample_count >= 2 and margin >= 0.4:
        return 92
    if margin >= 0.2:
        return 84
    return 76


def _stabilize_unlabeled_qa_sequence(segments: list[dict[str, Any]]) -> None:
    usable = [item for item in segments if not item.get("excluded") and str(item.get("normalizedText") or item.get("rawText") or "").strip()]
    if len(usable) < 4:
        return
    question_indexes = [
        index for index, item in enumerate(usable)
        if _is_clear_question(str(item.get("normalizedText") or item.get("rawText") or ""))
    ]
    if len(question_indexes) < 2 or question_indexes[0] > 1:
        return
    boundaries = [*question_indexes, len(usable)]
    if any(following - current < 2 for current, following in zip(boundaries, boundaries[1:])):
        return
    if (len(usable) - question_indexes[0]) / len(usable) < 0.8:
        return

    question_index_set = set(question_indexes)
    for index, segment in enumerate(usable):
        if index < question_indexes[0]:
            continue
        role = "interviewer" if index in question_index_set else "candidate"
        _apply_inferred_speaker_role(
            segment,
            role,
            88,
            "根据整场连续的提问与回答序列完成角色映射",
        )


def _is_clear_question(text: str) -> bool:
    value = text.strip()
    return value.endswith(("?", "？")) or bool(CLEAR_QUESTION_RE.search(value))


def _apply_inferred_speaker_role(segment: dict[str, Any], role: str, score: int, summary: str) -> None:
    segment["speakerRole"] = role
    is_audio = segment.get("startTime") is not None
    if not is_audio or segment.get("speakerConfidence") is None:
        segment["speakerConfidence"] = score / 100
    if score < 80:
        segment["needsConfirmation"] = True
        return

    details = segment.setdefault("confidenceDetails", {})
    details["speaker"] = {
        "score": score,
        "reason_codes": [],
        "evidence_atom_ids": [],
        "summary": summary,
    }
    reasons = [
        item for item in segment.get("confirmationReasons", [])
        if item.get("code") != "SPEAKER_ROLE_UNCERTAIN"
    ]
    segment["confirmationReasons"] = reasons
    if is_audio:
        segment["needsConfirmation"] = bool(reasons) or float(segment.get("confidence") or 0) < 0.75
    else:
        boundary = details.get("boundary") or {}
        boundary_score = float(boundary.get("score") or 100) / 100
        segment["confidence"] = min(score / 100, boundary_score)
        segment["needsConfirmation"] = bool(reasons) or segment["confidence"] < 0.75


def _topic_title(question: str) -> str:
    return infer_topic_title(question)


def confidence_label(value: float, *, needs_confirmation: bool = False) -> str:
    label = "high" if value >= 0.85 else "medium" if value >= 0.65 else "low"
    return "medium" if needs_confirmation and label == "high" else label


def _confidence_value(value: Any) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(value), 0)
