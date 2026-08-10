import json
from pathlib import Path

from backend.app.config import ROOT_DIR
from backend.app.services.transcript import (
    build_question_cards,
    map_speaker_roles,
    segment_text,
    validate_question_cards,
    validate_segments,
)


def test_complete_sample_cases_remain_parseable():
    cases_dir = ROOT_DIR / "data" / "samples" / "interview_cases"
    case_dirs = sorted(path for path in cases_dir.iterdir() if path.is_dir())
    assert len(case_dirs) == 3

    for case_dir in case_dirs:
        profile = json.loads((case_dir / "profile.json").read_text(encoding="utf-8"))
        for key in ("company", "position", "round", "interviewDate", "reviewGoal"):
            assert profile[key]
        for filename in ("job_description.txt", "resume.txt", "transcript.txt"):
            assert (case_dir / filename).read_text(encoding="utf-8").strip()

        segments = segment_text((case_dir / "transcript.txt").read_text(encoding="utf-8"))
        map_speaker_roles(segments)
        validation = validate_segments(segments)
        cards = build_question_cards(segments)
        roots = [item for item in cards if item["turnType"] == "main"]
        follow_ups = [item for item in cards if item["turnType"] == "follow_up"]
        actual = {
            "segments": len(segments),
            "questions": len(cards),
            "topics": len(roots),
            "followUps": len(follow_ups),
            "needsConfirmation": sum(bool(item["needsConfirmation"]) for item in cards),
        }

        assert validation.blocking is False
        assert validate_question_cards(cards, segments) == []
        assert actual == profile["expectedParse"]
