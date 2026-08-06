from __future__ import annotations


_MOJIBAKE_MARKERS = (
    "闈㈣瘯",
    "鍊欓€",
    "锛",
    "銆",
    "鈥",
    "绠",
    "浜",
)


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
