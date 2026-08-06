from backend.app.domain.scoring import aggregate_scores, normalize_scores


def test_weighted_score_is_deterministic():
    result = normalize_scores({"relevance": 8, "structure": 7, "evidence": 6, "depth": 5, "roleFit": 9})
    assert result["overall"] == 7.0
    assert result["evidence"] == 6.0


def test_score_clamps_and_aggregates():
    first = normalize_scores({"relevance": 99, "structure": 0})
    second = normalize_scores({"relevance": 6, "structure": 8})
    combined = aggregate_scores([first, second])
    assert first["relevance"] == 10.0
    assert first["structure"] == 1.0
    assert combined["relevance"] == 8.0
