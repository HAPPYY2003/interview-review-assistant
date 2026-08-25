from __future__ import annotations

import re
from dataclasses import dataclass


_MOJIBAKE_MARKERS = (
    "闈㈣瘯",
    "鍊欓€",
    "锛",
    "銆",
    "鈥",
    "绠",
    "浜",
)


_CLOCK_TOKEN = r"(?:\d{1,2}:){1,2}\d{2}(?:[.,]\d{1,3})?"
_BRACKETED_CLOCK = rf"(?:\[\s*{_CLOCK_TOKEN}\s*\]|【\s*{_CLOCK_TOKEN}\s*】|\(\s*{_CLOCK_TOKEN}\s*\)|（\s*{_CLOCK_TOKEN}\s*）)"
_TIMELINE_START_RE = re.compile(rf"^(?:(?P<bracketed>{_BRACKETED_CLOCK})|(?P<bare>{_CLOCK_TOKEN}))", re.I)
_TIMELINE_RANGE_RE = re.compile(
    rf"^\s*(?:-->|->|→|–|—|~|～|至|到)\s*(?:{_BRACKETED_CLOCK}|{_CLOCK_TOKEN})",
    re.I,
)
_SPEAKER_PREFIX_RE = re.compile(
    r"^(?P<label>面试官|采访者|interviewer|问|q|候选人|求职者|candidate|答|a|"
    r"系统|system|speaker[\s_-]*\d+|说话人\s*\d+|发言人\s*\d+)\s*[:：]\s*(?P<text>.*)$",
    re.I,
)


@dataclass(frozen=True)
class TranscriptLine:
    text: str
    speaker_label: str
    text_offset: int
    has_timeline: bool


def parse_transcript_line(line: str, *, allow_short_timestamp: bool = True) -> TranscriptLine:
    """Separate transcript metadata from spoken text while preserving its source offset."""
    value = line or ""
    leading = len(value) - len(value.lstrip())
    remaining = value[leading:]
    offset = leading
    timeline = _TIMELINE_START_RE.match(remaining)
    has_timeline = False
    if timeline:
        bare = timeline.group("bare") or ""
        short_bare = bool(bare) and bare.count(":") == 1
        end = timeline.end()
        range_match = _TIMELINE_RANGE_RE.match(remaining[end:])
        if range_match:
            end += range_match.end()
        next_character = remaining[end:end + 1]
        separated_bare = not bare or not next_character or next_character.isspace() or next_character in "|｜-–—·•"
        if (allow_short_timestamp or not short_bare) and separated_bare:
            has_timeline = True
            remaining = remaining[end:]
            offset += end
            separator = re.match(r"^[\s|｜\-–—·•]*", remaining)
            if separator:
                remaining = remaining[separator.end():]
                offset += separator.end()

    speaker = _SPEAKER_PREFIX_RE.match(remaining)
    label = speaker.group("label") if speaker else ""
    body = speaker.group("text") if speaker else remaining
    if speaker:
        body_offset = remaining.find(body)
        offset += max(0, body_offset)

    body_leading = len(body) - len(body.lstrip())
    body = body.strip()
    return TranscriptLine(
        text=body,
        speaker_label=label,
        text_offset=offset + body_leading,
        has_timeline=has_timeline,
    )


def transcript_uses_short_timeline(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    matches = sum(parse_transcript_line(line, allow_short_timestamp=True).has_timeline for line in lines)
    return matches >= 2 and matches / len(lines) >= 0.4


def subtitle_sequence_line_indexes(text: str) -> set[int]:
    lines = (text or "").splitlines()
    indexes: set[int] = set()
    for index, line in enumerate(lines):
        if not line.strip().isdigit():
            continue
        following = next((item.strip() for item in lines[index + 1:] if item.strip()), "")
        parsed = parse_transcript_line(following, allow_short_timestamp=True)
        if parsed.has_timeline and not parsed.text:
            indexes.add(index)
    return indexes


def repair_mojibake(text: str) -> str:
    """Repair the common UTF-8-as-GBK corruption without touching normal text."""
    if not text or not any(marker in text for marker in _MOJIBAKE_MARKERS):
        return text
    try:
        repaired = text.encode("gb18030").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

    original_score = sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)
    repaired_score = sum(repaired.count(marker) for marker in _MOJIBAKE_MARKERS)
    readable_gain = sum(repaired.count(word) for word in ("面试官", "候选人", "公司", "岗位", "问题"))
    return repaired if repaired_score < original_score and readable_gain > 0 else text
