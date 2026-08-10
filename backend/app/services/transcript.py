from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from backend.app.services.evidence import infer_question_type, infer_topic_title
from backend.app.services.text_utils import repair_mojibake


SPEAKER_RE = re.compile(
    r"^(?:(?P<time>\[[^\]]+\]|(?:\d{1,2}:){1,2}\d{2})\s*)?"
    r"(?P<label>面试官|采访者|interviewer|问|q|候选人|求职者|candidate|答|a)\s*[:：]\s*(?P<text>.*)$",
    re.I,
)
QUESTION_RE = re.compile(r"[?？]|^(?:请|你|为什么|怎么|如何|介绍|讲讲|谈谈|说说|哪些|什么|是否|能否)")
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
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line in source.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if content.strip():
            leading = len(content) - len(content.lstrip())
            spans.append((cursor + leading, cursor + len(content), content.strip()))
        cursor += len(line)
    if not spans and source.strip():
        start = len(source) - len(source.lstrip())
        spans = [(start, len(source.rstrip()), source.strip())]

    expanded: list[tuple[int, int, str]] = []
    for start, end, text in spans:
        if SPEAKER_RE.match(text) or len(text) <= 180:
            expanded.append((start, end, text))
            continue
        for match in re.finditer(r".+?(?:[。！？?!；;]+|$)", text):
            part = match.group(0).strip()
            if part:
                expanded.append((start + match.start(), start + match.end(), part))

    segments: list[dict[str, Any]] = []
    for index, (start, end, original) in enumerate(expanded, 1):
        match = SPEAKER_RE.match(original)
        label = match.group("label") if match else ""
        text = match.group("text").strip() if match else original
        role = _role_from_label(label)
        text_offset = original.find(text)
        text_start = start + max(0, text_offset)
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
    if not labels or all(any(item.get("speakerRole") != "unknown" for item in items) for items in labels.values()):
        return segments

    scores = []
    for label, items in labels.items():
        questions = sum(bool(QUESTION_RE.search(item.get("normalizedText", ""))) for item in items)
        scores.append((questions / max(1, len(items)), label))
    scores.sort(reverse=True)
    interviewer_labels = {label for ratio, label in scores if ratio >= 0.45}
    if not interviewer_labels and scores:
        interviewer_labels.add(scores[0][1])
    candidates = [label for _, label in reversed(scores) if label not in interviewer_labels]
    candidate_label = candidates[0] if candidates else None

    for segment in segments:
        label = segment.get("speakerLabel", "")
        if label in interviewer_labels:
            segment["speakerRole"] = "interviewer"
        elif label == candidate_label:
            segment["speakerRole"] = "candidate"
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
        text = segment.get("rawText", "").strip()
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
            current = {
                "id": question_id,
                "order": len(questions) + 1,
                "interviewerQuestion": text,
                "candidateAnswer": "",
                "questionType": infer_question_type(text),
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
    return "unknown"


def _topic_title(question: str) -> str:
    return infer_topic_title(question)


def confidence_label(value: float, *, needs_confirmation: bool = False) -> str:
    label = "high" if value >= 0.85 else "medium" if value >= 0.65 else "low"
    return "medium" if needs_confirmation and label == "high" else label


def _confidence_value(value: Any) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(value), 0)
