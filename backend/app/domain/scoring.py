from __future__ import annotations

from statistics import mean
from typing import Mapping, Sequence


DIMENSIONS = ("relevance", "structure", "evidence", "depth", "roleFit")
WEIGHTS = {
    "relevance": 0.20,
    "structure": 0.15,
    "evidence": 0.25,
    "depth": 0.20,
    "roleFit": 0.20,
}


def clamp_score(value: object, fallback: float = 5.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return round(min(10.0, max(1.0, number)), 1)


def normalize_scores(scores: Mapping[str, object] | None) -> dict[str, float]:
    source = scores or {}
    normalized = {name: clamp_score(source.get(name)) for name in DIMENSIONS}
    normalized["overall"] = round(sum(normalized[name] * WEIGHTS[name] for name in DIMENSIONS), 1)
    return normalized


def aggregate_scores(items: Sequence[Mapping[str, object]]) -> dict[str, float]:
    valid = [normalize_scores(item) for item in items if item]
    if not valid:
        return {name: 0.0 for name in (*DIMENSIONS, "overall")}
    return {name: round(mean(item[name] for item in valid), 1) for name in (*DIMENSIONS, "overall")}

