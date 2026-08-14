from __future__ import annotations

import pytest
from pydantic import ValidationError
from types import SimpleNamespace

from backend.app.database import Database
from backend.app.services.parse_workflow import ParseWorkflow
from backend.app.services.transcript_structure import (
    ConfidenceAssessment,
    ConfirmationReasonCode,
    atoms_from_audio_segments,
    atomize_text,
    calculate_turn_confidence,
    fallback_utterances,
    profile_transcript,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("面试官：请介绍项目。\n候选人：我负责推荐策略。", "labeled_lines"),
        ("请介绍项目？\n我负责推荐策略。", "unlabeled_lines"),
        ("请介绍项目？我负责推荐策略。最终提升了12%。", "punctuated_stream"),
        ("请介绍项目我负责推荐策略最终提升了百分之十二", "raw_stream"),
    ],
)
def test_profiles_all_supported_transcript_shapes(text: str, expected: str):
    profile = profile_transcript(text)
    atoms = atomize_text(text, profile)
    assert profile.profile_type == expected
    assert atoms
    assert all(text[item["startChar"]:item["endChar"]] == item["rawText"] for item in atoms)


def test_raw_stream_produces_low_quality_candidate_utterances():
    text = "请介绍项目我负责推荐策略优化首先分析漏斗最后点击率提升百分之十二"
    profile = profile_transcript(text)
    atoms = atomize_text(text, profile)
    utterances = fallback_utterances(atoms, profile, text)
    assert profile.source_quality == 50
    assert len(utterances) >= 2
    assert any(item["needsConfirmation"] for item in utterances)
    assert {atom_id for item in utterances for atom_id in item["atomIds"]} == {item["id"] for item in atoms}


def test_assessment_requires_reasons_and_evidence_for_low_score():
    with pytest.raises(ValidationError):
        ConfidenceAssessment(score=72)
    with pytest.raises(ValidationError):
        ConfidenceAssessment(
            score=90,
            reason_codes=[ConfirmationReasonCode.QUESTION_TYPE_UNCERTAIN],
            evidence_atom_ids=["A1"],
        )


def test_deepgram_words_become_timestamped_immutable_atoms():
    segments = [{
        "rawText": "请介绍项目", "speakerLabel": "speaker_0", "speakerRole": "interviewer",
        "startTime": 0.0, "endTime": 1.0, "confidence": 0.94, "speakerConfidence": 0.9,
    }]
    payload = {"results": {"utterances": [{
        "speaker": 0,
        "words": [
            {"word": "请", "punctuated_word": "请", "start": 0.0, "end": 0.2, "confidence": 0.96},
            {"word": "介绍", "punctuated_word": "介绍", "start": 0.2, "end": 0.6, "confidence": 0.94},
            {"word": "项目", "punctuated_word": "项目", "start": 0.6, "end": 1.0, "confidence": 0.92},
        ],
    }]}}
    profile, atoms = atoms_from_audio_segments(segments, payload)
    assert profile.profile_type == "audio"
    assert [item["rawText"] for item in atoms] == ["请", "介绍", "项目"]
    assert atoms[1]["startTime"] == 0.2
    assert all(item["speakerRole"] == "interviewer" for item in atoms)


def test_confidence_engine_attributes_low_score_to_specific_dimensions():
    def assessment(score: int, code: ConfirmationReasonCode | None = None):
        return ConfidenceAssessment(
            score=score,
            reason_codes=[code] if code else [],
            evidence_atom_ids=["A1"] if code else [],
        )

    result = calculate_turn_confidence(
        "main",
        {
            "speaker": assessment(90),
            "questionBoundary": assessment(30, ConfirmationReasonCode.QUESTION_BOUNDARY_UNCERTAIN),
            "answerBoundary": assessment(82),
            "qaPairing": assessment(61, ConfirmationReasonCode.QA_PAIRING_AMBIGUOUS),
            "questionType": assessment(86),
            "topicGrouping": assessment(86),
        },
        80,
    )
    assert result["confidence"] == "low"
    assert result["needsConfirmation"] is True
    assert [item["code"] for item in result["confirmationReasons"][:2]] == [
        "QUESTION_BOUNDARY_UNCERTAIN", "QA_PAIRING_AMBIGUOUS",
    ]


def test_atoms_persist_and_manual_split_merge_preserve_text(settings_factory):
    settings = settings_factory()
    database = Database(settings.database_path)
    database.initialize()
    text = "请介绍项目我负责推荐策略优化最后点击率提升百分之十二"
    interview = database.create_interview({"id": "structure-edit", "raw_transcript": text})
    material = database.add_material(interview["id"], "transcript", text)
    profile = profile_transcript(text)
    atoms = atomize_text(text, profile)
    for atom in atoms:
        atom["id"] = f"{material['id']}:{atom['id']}"
    utterances = fallback_utterances(atoms, profile, text)
    for index, segment in enumerate(utterances, 1):
        segment["id"] = f"segment-{index}"
    question_segment = utterances[0]
    answer_segment = utterances[1]
    card = {
        "id": "question-1", "order": 1, "interviewerQuestion": question_segment["rawText"],
        "candidateAnswer": answer_segment["rawText"], "questionType": "项目经历", "confidence": "low",
        "topicRootId": "question-1", "turnType": "main", "topicTitle": "项目经历",
        "needsConfirmation": True, "questionSegmentIds": [question_segment["id"]],
        "answerSegmentIds": [answer_segment["id"]],
    }
    database.commit_parse_result(interview["id"], material["id"], atoms, utterances, [card])
    original = database.get_segments(interview["id"])
    assert database.get_atoms(interview["id"])
    assert len(original[0]["atomIds"]) > 1

    split = database.split_segment(interview["id"], original[0]["id"], original[0]["atomIds"][0])
    assert len(split) == len(original) + 1
    merged = database.merge_segments(interview["id"], [split[0]["id"], split[1]["id"]])
    assert len(merged) == len(original)
    assert merged[0]["rawText"] == original[0]["rawText"]
    questions = database.get_questions(interview["id"])
    assert questions[0]["extractedQuestion"] == original[0]["rawText"]

    with pytest.raises(ValueError, match="同属问题或同属回答"):
        database.merge_segments(interview["id"], [merged[0]["id"], merged[1]["id"]])
    unchanged = database.get_questions(interview["id"])[0]
    assert set(unchanged["questionSegmentIds"]).isdisjoint(unchanged["answerSegmentIds"])

    reassigned = {
        **unchanged,
        "questionSegmentIds": [merged[0]["id"], merged[1]["id"]],
        "answerSegmentIds": [],
    }
    database.replace_questions(interview["id"], [reassigned])
    refreshed = database.get_questions(interview["id"])[0]
    assert refreshed["extractedAnswer"] == ""
    assert refreshed["candidateAnswer"] == ""

    corrupted = {**unchanged, "questionSegmentIds": [merged[0]["id"]], "answerSegmentIds": [merged[0]["id"]]}
    database.replace_questions(interview["id"], [corrupted])
    repaired_segments = database.split_segment(
        interview["id"],
        merged[0]["id"],
        merged[0]["atomIds"][0],
        question_id=unchanged["id"],
        left_assignment="question",
        right_assignment="answer",
    )
    repaired = database.get_questions(interview["id"])[0]
    assert len(repaired_segments) == len(merged) + 1
    assert len(repaired["questionSegmentIds"]) == 1
    assert len(repaired["answerSegmentIds"]) == 1
    assert set(repaired["questionSegmentIds"]).isdisjoint(repaired["answerSegmentIds"])


def test_two_stage_agent_contract_drives_cards_but_local_engine_sets_confidence(settings_factory):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key")
    database = Database(settings.database_path)
    database.initialize()
    transcript = "面试官：请介绍项目。\n候选人：我负责推荐策略优化。"
    interview = database.create_interview({"id": "agent-structure", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")

    class FakeRuntime:
        def run_parse_agent(self, *_args, **_kwargs):
            return SimpleNamespace(text="scheduled")

        def run_utterance_worker(self, atoms, _strategy, _core_start):
            return {
                "utterances": [
                    {
                        "atom_ids": [atom["id"]],
                        "speaker_role": atom["speakerRole"],
                        "speaker_assessment": {"score": 96, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                        "boundary_assessment": {"score": 96, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                    }
                    for atom in atoms
                ]
            }

        def run_dialogue_worker(self, utterances):
            return {
                "question_turns": [{
                    "question_utterance_ids": [utterances[0]["id"]],
                    "answer_utterance_ids": [utterances[1]["id"]],
                    "turn_type": "main", "parent_question_anchor": None,
                    "question_type": "项目经历", "topic_title": "项目经历",
                    "question_boundary_assessment": {"score": 95, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                    "answer_boundary_assessment": {"score": 95, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                    "qa_pairing_assessment": {"score": 95, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                    "follow_up_assessment": None,
                    "question_type_assessment": {"score": 92, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                    "topic_grouping_assessment": {"score": 92, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                }]
            }

        def run_parse_auditor(self, *_args):
            return {"selected": "boundary_first", "summary": ""}

    workflow = ParseWorkflow(database, settings)
    workflow.runtime = FakeRuntime()
    workflow.execute(run["id"])
    completed = database.get_parse_run(run["id"])
    questions = database.get_questions(interview["id"])
    assert completed["status"] == "COMPLETED"
    assert questions[0]["parseMethod"] == "agent"
    assert questions[0]["confidence"] == "high"
    assert questions[0]["needsConfirmation"] is False
    assert questions[0]["confirmationReasons"] == []
