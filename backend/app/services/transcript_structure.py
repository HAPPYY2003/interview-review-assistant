from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    import jieba
except ImportError:  # pragma: no cover - requirements include jieba
    jieba = None


QUESTION_TYPES = ("自我介绍", "项目经历", "技术知识", "行为面试", "业务理解", "职业规划", "反问环节", "其他")
SPEAKER_PATTERN = re.compile(
    r"^(?:(?P<time>\[[^\]]+\]|(?:\d{1,2}:){1,2}\d{2})\s*)?"
    r"(?P<label>面试官|采访者|interviewer|问|q|候选人|求职者|candidate|答|a)\s*[:：]\s*(?P<text>.*)$",
    re.I,
)
SENTENCE_END_PATTERN = re.compile(r"[。！？?!]")
QUESTION_CUE_PATTERN = re.compile(r"(?:请|能否|可以|为什么|怎么|如何|介绍|讲讲|谈谈|说说|哪些|什么|是否|如果|你会|你在)")
CANDIDATE_CUE_PATTERN = re.compile(r"^(?:我|我们|主要|首先|当时|后来|最后|因为|负责)")


class ConfirmationReasonCode(str, Enum):
    QUESTION_BOUNDARY_UNCERTAIN = "QUESTION_BOUNDARY_UNCERTAIN"
    ANSWER_BOUNDARY_UNCERTAIN = "ANSWER_BOUNDARY_UNCERTAIN"
    QA_PAIRING_AMBIGUOUS = "QA_PAIRING_AMBIGUOUS"
    SPEAKER_ROLE_UNCERTAIN = "SPEAKER_ROLE_UNCERTAIN"
    MAIN_FOLLOWUP_UNCERTAIN = "MAIN_FOLLOWUP_UNCERTAIN"
    FOLLOWUP_PARENT_UNCERTAIN = "FOLLOWUP_PARENT_UNCERTAIN"
    QUESTION_TYPE_UNCERTAIN = "QUESTION_TYPE_UNCERTAIN"
    TOPIC_GROUPING_UNCERTAIN = "TOPIC_GROUPING_UNCERTAIN"
    SOURCE_QUALITY_LOW = "SOURCE_QUALITY_LOW"
    ANSWER_MISSING = "ANSWER_MISSING"
    CHUNK_OVERLAP_CONFLICT = "CHUNK_OVERLAP_CONFLICT"
    REFERENCE_VALIDATION_FAILED = "REFERENCE_VALIDATION_FAILED"


REASON_LABELS = {
    ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN: "问题边界不明确",
    ConfirmationReasonCode.ANSWER_BOUNDARY_UNCERTAIN: "回答边界不明确",
    ConfirmationReasonCode.QA_PAIRING_AMBIGUOUS: "问答对应关系不明确",
    ConfirmationReasonCode.SPEAKER_ROLE_UNCERTAIN: "说话人身份不明确",
    ConfirmationReasonCode.MAIN_FOLLOWUP_UNCERTAIN: "主问题与追问关系不明确",
    ConfirmationReasonCode.FOLLOWUP_PARENT_UNCERTAIN: "追问归属不明确",
    ConfirmationReasonCode.QUESTION_TYPE_UNCERTAIN: "题型分类不确定",
    ConfirmationReasonCode.TOPIC_GROUPING_UNCERTAIN: "主题归并不确定",
    ConfirmationReasonCode.SOURCE_QUALITY_LOW: "原始文稿结构较弱",
    ConfirmationReasonCode.ANSWER_MISSING: "未识别到回答",
    ConfirmationReasonCode.CHUNK_OVERLAP_CONFLICT: "分块结果冲突",
    ConfirmationReasonCode.REFERENCE_VALIDATION_FAILED: "原文引用校验失败",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ConfidenceAssessment(StrictModel):
    score: int = Field(ge=0, le=100)
    reason_codes: list[ConfirmationReasonCode] = Field(default_factory=list)
    evidence_atom_ids: list[str] = Field(default_factory=list)
    summary: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_explanation(self) -> "ConfidenceAssessment":
        if self.score < 80 and (not self.reason_codes or not self.evidence_atom_ids):
            raise ValueError("低于 80 分必须提供原因代码和原子证据")
        if self.score >= 85 and self.reason_codes:
            raise ValueError("85 分及以上不能同时提交不确定原因")
        return self


class UtteranceSubmission(StrictModel):
    atom_ids: list[str] = Field(min_length=1)
    speaker_role: Literal["interviewer", "candidate", "system_noise", "unknown"]
    speaker_assessment: ConfidenceAssessment
    boundary_assessment: ConfidenceAssessment

    @model_validator(mode="after")
    def validate_reason_dimensions(self) -> "UtteranceSubmission":
        _require_reason_subset(self.speaker_assessment, {ConfirmationReasonCode.SPEAKER_ROLE_UNCERTAIN, ConfirmationReasonCode.SOURCE_QUALITY_LOW})
        _require_reason_subset(self.boundary_assessment, {ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN, ConfirmationReasonCode.ANSWER_BOUNDARY_UNCERTAIN, ConfirmationReasonCode.SOURCE_QUALITY_LOW})
        return self


class UtteranceBatch(StrictModel):
    utterances: list[UtteranceSubmission]


class QuestionTurnSubmission(StrictModel):
    question_utterance_ids: list[str] = Field(min_length=1)
    answer_utterance_ids: list[str] = Field(default_factory=list)
    turn_type: Literal["main", "follow_up"] = "main"
    parent_question_anchor: str | None = None
    question_type: Literal["自我介绍", "项目经历", "技术知识", "行为面试", "业务理解", "职业规划", "反问环节", "其他"]
    topic_title: str = Field(min_length=1, max_length=40)
    question_boundary_assessment: ConfidenceAssessment
    answer_boundary_assessment: ConfidenceAssessment
    qa_pairing_assessment: ConfidenceAssessment
    follow_up_assessment: ConfidenceAssessment | None = None
    question_type_assessment: ConfidenceAssessment
    topic_grouping_assessment: ConfidenceAssessment

    @model_validator(mode="after")
    def validate_follow_up(self) -> "QuestionTurnSubmission":
        if self.turn_type == "follow_up" and (not self.parent_question_anchor or not self.follow_up_assessment):
            raise ValueError("追问必须提交父问题锚点和追问归属评分")
        _require_reason_subset(self.question_boundary_assessment, {ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN, ConfirmationReasonCode.SOURCE_QUALITY_LOW})
        _require_reason_subset(self.answer_boundary_assessment, {ConfirmationReasonCode.ANSWER_BOUNDARY_UNCERTAIN, ConfirmationReasonCode.ANSWER_MISSING, ConfirmationReasonCode.SOURCE_QUALITY_LOW})
        _require_reason_subset(self.qa_pairing_assessment, {ConfirmationReasonCode.QA_PAIRING_AMBIGUOUS, ConfirmationReasonCode.ANSWER_MISSING})
        _require_reason_subset(self.question_type_assessment, {ConfirmationReasonCode.QUESTION_TYPE_UNCERTAIN})
        _require_reason_subset(self.topic_grouping_assessment, {ConfirmationReasonCode.TOPIC_GROUPING_UNCERTAIN})
        if self.follow_up_assessment:
            _require_reason_subset(self.follow_up_assessment, {ConfirmationReasonCode.MAIN_FOLLOWUP_UNCERTAIN, ConfirmationReasonCode.FOLLOWUP_PARENT_UNCERTAIN})
        return self


class QuestionTurnBatch(StrictModel):
    question_turns: list[QuestionTurnSubmission]


@dataclass(frozen=True)
class TranscriptProfile:
    profile_type: Literal["labeled_lines", "unlabeled_lines", "punctuated_stream", "raw_stream", "audio"]
    source_quality: int
    line_count: int
    speaker_label_coverage: float
    punctuation_density: float

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["profileType"] = data.pop("profile_type")
        data["sourceQuality"] = data.pop("source_quality")
        data["lineCount"] = data.pop("line_count")
        data["speakerLabelCoverage"] = data.pop("speaker_label_coverage")
        data["punctuationDensity"] = data.pop("punctuation_density")
        return data


def profile_transcript(text: str) -> TranscriptProfile:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    line_count = len(lines)
    labeled = sum(bool(SPEAKER_PATTERN.match(line)) for line in lines)
    coverage = labeled / line_count if line_count else 0.0
    punctuation_density = len(SENTENCE_END_PATTERN.findall(text or "")) / max(1, len(text or ""))
    if line_count >= 2 and coverage >= 0.6:
        profile_type, quality = "labeled_lines", 95
    elif line_count >= 2:
        profile_type, quality = "unlabeled_lines", 80
    elif punctuation_density >= 1 / 80:
        profile_type, quality = "punctuated_stream", 75
    else:
        profile_type, quality = "raw_stream", 50
    return TranscriptProfile(profile_type, quality, line_count, round(coverage, 3), round(punctuation_density, 4))


def atomize_text(text: str, profile: TranscriptProfile) -> list[dict[str, Any]]:
    source = text or ""
    spans: list[tuple[int, int, str, str, str]] = []
    if profile.profile_type in {"labeled_lines", "unlabeled_lines"}:
        cursor = 0
        for line in source.splitlines(keepends=True):
            content = line.rstrip("\r\n")
            stripped = content.strip()
            if not stripped:
                cursor += len(line)
                continue
            leading = len(content) - len(content.lstrip())
            line_start = cursor + leading
            match = SPEAKER_PATTERN.match(stripped)
            label = match.group("label") if match else ""
            body = match.group("text") if match else stripped
            body_offset = stripped.find(body)
            body_start = line_start + max(0, body_offset)
            role = _role_from_label(label)
            parts = _sentence_spans(body, body_start) if len(body) > 180 else [(body_start, body_start + len(body), body)]
            spans.extend((start, end, part, label, role) for start, end, part in parts if part.strip())
            cursor += len(line)
    elif profile.profile_type == "punctuated_stream":
        spans.extend((start, end, part, "", "unknown") for start, end, part in _sentence_spans(source, 0))
    else:
        if jieba:
            tokens = list(jieba.tokenize(source))
            spans.extend((start, end, word, "", "unknown") for word, start, end in tokens if word.strip())
        else:  # pragma: no cover
            spans.extend((match.start(), match.end(), match.group(0), "", "unknown") for match in re.finditer(r"\S", source))
    return [
        {
            "id": f"A{index:05d}",
            "ordinal": index,
            "rawText": raw,
            "startChar": start,
            "endChar": end,
            "startTime": None,
            "endTime": None,
            "speakerLabel": label,
            "speakerRole": role,
            "confidence": 0.99 if role != "unknown" else profile.source_quality / 100,
        }
        for index, (start, end, raw, label, role) in enumerate(spans, 1)
    ]


def atoms_from_audio_segments(
    segments: Iterable[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> tuple[TranscriptProfile, list[dict[str, Any]]]:
    items = list(segments)
    utterances = (payload or {}).get("results", {}).get("utterances", [])
    words = [word for utterance in utterances for word in (utterance.get("words") or [])]
    average = (
        sum(float(item.get("confidence", 0)) for item in words) / max(1, len(words))
        if words else sum(float(item.get("confidence", 0)) for item in items) / max(1, len(items))
    )
    low_speaker = any(item.get("speakerConfidence") is not None and float(item["speakerConfidence"]) < 0.6 for item in items)
    quality = min(70, round(average * 100)) if low_speaker else round(average * 100)
    profile = TranscriptProfile("audio", quality, len(items), 1.0, 0.0)
    role_by_label = {item.get("speakerLabel", ""): item.get("speakerRole", "unknown") for item in items}
    source_items = []
    if words:
        for utterance_index, utterance in enumerate(utterances, 1):
            utterance_speaker = utterance.get("speaker")
            for word in utterance.get("words") or []:
                speaker = word.get("speaker", utterance_speaker)
                label = f"speaker_{speaker}" if speaker is not None else ""
                source_items.append({
                    "rawText": word.get("punctuated_word") or word.get("word", ""),
                    "startTime": word.get("start"), "endTime": word.get("end"), "speakerLabel": label,
                    "speakerRole": role_by_label.get(label, "unknown"), "confidence": word.get("confidence", 0),
                    "utteranceIndex": utterance_index,
                })
    else:
        source_items = items
    atoms = []
    for index, item in enumerate(source_items, 1):
        atoms.append({
            "id": f"A{index:05d}", "ordinal": index, "rawText": item.get("rawText", ""),
            "startChar": None, "endChar": None, "startTime": item.get("startTime"), "endTime": item.get("endTime"),
            "speakerLabel": item.get("speakerLabel", ""), "speakerRole": item.get("speakerRole", "unknown"),
            "confidence": float(item.get("confidence", 0)),
            "groupId": item.get("utteranceIndex", index),
        })
    return profile, atoms


def fallback_utterances(atoms: list[dict[str, Any]], profile: TranscriptProfile, source: str = "") -> list[dict[str, Any]]:
    if not atoms:
        return []
    groups: list[list[dict[str, Any]]] = []
    if profile.profile_type != "raw_stream":
        groups = [[atom] for atom in atoms]
    else:
        current: list[dict[str, Any]] = []
        current_role = "interviewer" if QUESTION_CUE_PATTERN.search("".join(item["rawText"] for item in atoms[:8])) else "unknown"
        for atom in atoms:
            token = str(atom.get("rawText", ""))
            role = "candidate" if CANDIDATE_CUE_PATTERN.search(token) else "interviewer" if QUESTION_CUE_PATTERN.search(token) else current_role
            if current and role != current_role and len(current) >= 3:
                groups.append(current)
                current = []
            current.append(atom)
            current_role = role
        if current:
            groups.append(current)
        if len(groups) == 1:
            split_at = next((index for index, atom in enumerate(atoms[3:], 3) if CANDIDATE_CUE_PATTERN.search(str(atom["rawText"]))), None)
            if split_at:
                groups = [atoms[:split_at], atoms[split_at:]]

    utterances: list[dict[str, Any]] = []
    previous_role = "unknown"
    for index, group in enumerate(groups, 1):
        explicit = next((item.get("speakerRole") for item in group if item.get("speakerRole") != "unknown"), None)
        text = _text_from_atoms(group, source)
        if explicit:
            role = explicit
            speaker_score = 98
        elif QUESTION_CUE_PATTERN.search(text) or text.rstrip().endswith(("?", "？")):
            role, speaker_score = "interviewer", 74
        elif previous_role == "interviewer":
            role, speaker_score = "candidate", 72
        else:
            role, speaker_score = "unknown", 55
        boundary_score = 96 if profile.profile_type == "labeled_lines" else 82 if profile.profile_type != "raw_stream" else 55
        atom_ids = [item["id"] for item in group]
        reasons = [] if speaker_score >= 80 else [ConfirmationReasonCode.SPEAKER_ROLE_UNCERTAIN]
        boundary_reasons = [] if boundary_score >= 80 else [ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN]
        utterances.append({
            "id": f"U{index:04d}", "ordinal": index, "atomIds": atom_ids, "rawText": text,
            "normalizedText": " ".join(text.split()), "speakerLabel": group[0].get("speakerLabel", ""),
            "speakerRole": role, "startChar": group[0].get("startChar"), "endChar": group[-1].get("endChar"),
            "startTime": group[0].get("startTime"), "endTime": group[-1].get("endTime"),
            "confidence": min(speaker_score, boundary_score) / 100, "speakerConfidence": speaker_score / 100,
            "needsConfirmation": bool(reasons or boundary_reasons or profile.source_quality < 65), "excluded": role == "system_noise",
            "confidenceDetails": {
                "speaker": _assessment_dict(speaker_score, reasons, atom_ids),
                "boundary": _assessment_dict(boundary_score, boundary_reasons, atom_ids),
            },
            "confirmationReasons": _reason_entries(reasons + boundary_reasons, "utterance", min(speaker_score, boundary_score), atom_ids),
            "parseMethod": "deterministic",
        })
        previous_role = role
    return utterances


def utterances_from_submission(
    batch: UtteranceBatch,
    atoms: list[dict[str, Any]],
    source: str,
    profile: TranscriptProfile,
) -> list[dict[str, Any]]:
    atom_map = {item["id"]: item for item in atoms}
    used: set[str] = set()
    result = []
    for index, item in enumerate(batch.utterances, 1):
        if any(atom_id not in atom_map for atom_id in item.atom_ids):
            raise ValueError("UTTERANCE_UNKNOWN_ATOM")
        ordinals = [atom_map[atom_id]["ordinal"] for atom_id in item.atom_ids]
        if ordinals != list(range(min(ordinals), max(ordinals) + 1)) or used.intersection(item.atom_ids):
            raise ValueError("UTTERANCE_ATOMS_NOT_CONTIGUOUS")
        used.update(item.atom_ids)
        selected = [atom_map[atom_id] for atom_id in item.atom_ids]
        speaker = item.speaker_assessment
        boundary = item.boundary_assessment
        reasons = list(dict.fromkeys([*speaker.reason_codes, *boundary.reason_codes]))
        score = min(speaker.score, boundary.score)
        result.append({
            "id": f"U{index:04d}", "ordinal": index, "atomIds": list(item.atom_ids),
            "rawText": _text_from_atoms(selected, source), "normalizedText": " ".join(_text_from_atoms(selected, source).split()),
            "speakerLabel": selected[0].get("speakerLabel", ""), "speakerRole": item.speaker_role,
            "startChar": selected[0].get("startChar"), "endChar": selected[-1].get("endChar"),
            "startTime": selected[0].get("startTime"), "endTime": selected[-1].get("endTime"),
            "confidence": score / 100, "speakerConfidence": speaker.score / 100,
            "needsConfirmation": score < 75 or profile.source_quality < 65, "excluded": item.speaker_role == "system_noise",
            "confidenceDetails": {"speaker": speaker.model_dump(mode="json"), "boundary": boundary.model_dump(mode="json")},
            "confirmationReasons": _reason_entries(reasons, "utterance", score, list(item.atom_ids)),
            "parseMethod": "agent",
        })
    return result


def calculate_turn_confidence(
    turn_type: str,
    assessments: dict[str, ConfidenceAssessment],
    source_quality: int,
    *,
    hard_reasons: Iterable[ConfirmationReasonCode] = (),
) -> dict[str, Any]:
    weights = (
        {"speaker": 0.10, "questionBoundary": 0.15, "answerBoundary": 0.10, "qaPairing": 0.25, "followUp": 0.25, "questionType": 0.15}
        if turn_type == "follow_up"
        else {"speaker": 0.15, "questionBoundary": 0.20, "answerBoundary": 0.15, "qaPairing": 0.35, "questionType": 0.15}
    )
    semantic = sum(assessments[name].score * weight for name, weight in weights.items())
    raw_score = round(semantic * 0.8 + source_quality * 0.2)
    effective = raw_score
    reason_entries: list[dict[str, Any]] = []
    hard = list(dict.fromkeys(hard_reasons))
    for code in hard:
        reason_entries.append(_reason_entry(code, "validation", 0, [], 10_000))
    for name, weight in weights.items():
        assessment = assessments[name]
        for code in assessment.reason_codes:
            reason_entries.append(_reason_entry(code, name, assessment.score, assessment.evidence_atom_ids, round(weight * (100 - assessment.score), 2), assessment.summary))
    for name in ("topicGrouping",):
        assessment = assessments.get(name)
        if not assessment:
            continue
        for code in assessment.reason_codes:
            reason_entries.append(_reason_entry(code, name, assessment.score, assessment.evidence_atom_ids, round(0.1 * (100 - assessment.score), 2), assessment.summary))
    if source_quality < 65:
        reason_entries.append(_reason_entry(ConfirmationReasonCode.SOURCE_QUALITY_LOW, "sourceQuality", source_quality, [], round(0.2 * (100 - source_quality), 2)))

    core_names = ("speaker", "questionBoundary", "answerBoundary", "qaPairing")
    if hard or any(assessments[name].score < 60 for name in core_names):
        effective = min(effective, 64)
    warning = any(assessments[name].score < 75 for name in core_names)
    warning = warning or assessments["questionType"].score < 60 or source_quality < 65
    if turn_type == "follow_up":
        warning = warning or assessments["followUp"].score < 70
    if assessments.get("topicGrouping"):
        warning = warning or assessments["topicGrouping"].score < 70
    needs_confirmation = bool(hard or warning or reason_entries)
    if needs_confirmation:
        effective = min(effective, 84)
    level = "high" if effective >= 85 and not needs_confirmation else "medium" if effective >= 65 else "low"
    reason_entries = _deduplicate_and_rank_reasons(reason_entries)[:3]
    return {
        "confidence": level, "confidenceScore": effective, "rawConfidenceScore": raw_score,
        "needsConfirmation": needs_confirmation, "confirmationReasons": reason_entries,
        "confidenceDetails": {
            "sourceQuality": source_quality,
            "semanticScore": round(semantic),
            "rawScore": raw_score,
            "effectiveScore": effective,
            "dimensions": {name: value.model_dump(mode="json") for name, value in assessments.items()},
        },
    }


def chunk_atoms(atoms: list[dict[str, Any]], size: int = 160, overlap: int = 24) -> list[list[dict[str, Any]]]:
    return _chunk(atoms, size, overlap)


def chunk_utterances(utterances: list[dict[str, Any]], size: int = 40, overlap: int = 6) -> list[list[dict[str, Any]]]:
    return _chunk([item for item in utterances if not item.get("excluded")], size, overlap)


def _chunk(items: list[dict[str, Any]], size: int, overlap: int) -> list[list[dict[str, Any]]]:
    chunks, start = [], 0
    while start < len(items):
        end = min(len(items), start + size)
        chunks.append(items[start:end])
        if end >= len(items):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _sentence_spans(text: str, offset: int) -> list[tuple[int, int, str]]:
    result = []
    for match in re.finditer(r".+?(?:[。！？?!]+|$)", text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        if raw.strip():
            result.append((offset + match.start() + leading, offset + match.start() + trailing, raw.strip()))
    return result


def _role_from_label(label: str) -> str:
    value = (label or "").strip().lower()
    if value in {"面试官", "采访者", "interviewer", "问", "q"}:
        return "interviewer"
    if value in {"候选人", "求职者", "candidate", "答", "a"}:
        return "candidate"
    return "unknown"


def _text_from_atoms(atoms: list[dict[str, Any]], source: str) -> str:
    if not atoms:
        return ""
    start, end = atoms[0].get("startChar"), atoms[-1].get("endChar")
    if source and start is not None and end is not None:
        return source[int(start):int(end)].strip()
    parts = [str(item.get("rawText", "")).strip() for item in atoms if str(item.get("rawText", "")).strip()]
    if parts and all(re.search(r"[\u3400-\u9fff]", part) for part in parts):
        return "".join(parts)
    return " ".join(parts)


def _assessment_dict(score: int, reasons: list[ConfirmationReasonCode], atom_ids: list[str]) -> dict[str, Any]:
    return {"score": score, "reason_codes": [item.value for item in reasons], "evidence_atom_ids": atom_ids if reasons else [], "summary": ""}


def _reason_entries(codes: Iterable[ConfirmationReasonCode], dimension: str, score: int, atom_ids: list[str]) -> list[dict[str, Any]]:
    return [_reason_entry(code, dimension, score, atom_ids, 100 - score) for code in dict.fromkeys(codes)]


def _reason_entry(code: ConfirmationReasonCode, dimension: str, score: int, atom_ids: list[str], impact: float, summary: str = "") -> dict[str, Any]:
    return {
        "code": code.value, "label": REASON_LABELS[code], "dimension": dimension, "score": score,
        "evidenceAtomIds": list(atom_ids), "impact": impact, "summary": summary,
    }


def _deduplicate_and_rank_reasons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        existing = unique.get(item["code"])
        if not existing or float(item.get("impact", 0)) > float(existing.get("impact", 0)):
            unique[item["code"]] = item
    return sorted(unique.values(), key=lambda item: (-float(item.get("impact", 0)), item["code"]))


def _require_reason_subset(assessment: ConfidenceAssessment, allowed: set[ConfirmationReasonCode]) -> None:
    invalid = [code.value for code in assessment.reason_codes if code not in allowed]
    if invalid:
        raise ValueError(f"评分维度使用了不匹配的原因代码：{invalid[0]}")
