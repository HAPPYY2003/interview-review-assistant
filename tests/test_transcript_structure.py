from __future__ import annotations

import threading
import time

import pytest
from pydantic import ValidationError
from types import SimpleNamespace

from backend.app.database import Database
from backend.app.services.parse_workflow import ParsePipelineContext, ParseWorkflow
from backend.app.services.transcript_structure import (
    ConfidenceAssessment,
    ConfirmationReasonCode,
    atoms_from_audio_segments,
    atomize_text,
    calculate_turn_confidence,
    chunk_utterances,
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


def test_labeled_lines_merge_consecutive_same_speaker_into_turns():
    text = "\n".join([
        "面试官：请介绍项目。",
        "候选人：我负责需求分析。",
        "候选人：随后推动研发上线。",
        "候选人：最终完成了项目交付。",
        "面试官：你具体负责什么？",
        "候选人：我负责方案设计。",
        "候选人：也负责上线复盘。",
    ])
    profile = profile_transcript(text)
    atoms = atomize_text(text, profile)

    utterances = fallback_utterances(atoms, profile, text)

    assert [item["speakerRole"] for item in utterances] == [
        "interviewer", "candidate", "interviewer", "candidate",
    ]
    assert [len(item["atomIds"]) for item in utterances] == [1, 3, 1, 2]
    assert utterances[1]["rawText"] == "我负责需求分析。\n随后推动研发上线。\n最终完成了项目交付。"
    assert "候选人：" not in utterances[1]["rawText"]
    assert {atom_id for item in utterances for atom_id in item["atomIds"]} == {item["id"] for item in atoms}


def test_labeled_long_transcript_compacts_before_dialogue_chunking():
    lines = []
    for index in range(20):
        lines.append(f"面试官：问题 {index + 1}？")
        for part in range(5):
            lines.append(f"候选人：回答 {index + 1} 的第 {part + 1} 部分。")
    text = "\n".join(lines)
    profile = profile_transcript(text)
    atoms = atomize_text(text, profile)

    utterances = fallback_utterances(atoms, profile, text)
    chunks = chunk_utterances(utterances)

    assert len(atoms) == 120
    assert len(utterances) == 40
    assert len(chunks) == 1
    assert all(
        current["speakerRole"] != following["speakerRole"]
        for current, following in zip(utterances, utterances[1:])
    )


def test_dialogue_chunks_start_with_question_and_end_after_answer():
    utterances = [
        {
            "id": f"U{index + 1:04d}",
            "speakerRole": "interviewer" if index % 2 == 0 else "candidate",
        }
        for index in range(46)
    ]

    chunks = chunk_utterances(utterances, size=40, overlap=6)

    assert len(chunks) == 2
    assert all(chunk[0]["speakerRole"] == "interviewer" for chunk in chunks)
    assert all(chunk[-1]["speakerRole"] == "candidate" for chunk in chunks)


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


def test_labeled_transcript_reuses_local_utterances(settings_factory):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key")
    database = Database(settings.database_path)
    database.initialize()
    transcript = "面试官：请介绍项目。\n候选人：我负责推荐策略优化。"
    interview = database.create_interview({"id": "fast-labeled-parse", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")

    class FastRuntime:
        utterance_calls = 0

        def run_parse_agent(self, *_args, **_kwargs):
            return SimpleNamespace(text="scheduled")

        def run_utterance_worker(self, *_args, **_kwargs):
            self.utterance_calls += 1
            raise AssertionError("带说话人标签的文本不应再次执行话轮分段 Worker")

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

    runtime = FastRuntime()
    workflow = ParseWorkflow(database, settings)
    workflow.runtime = runtime
    workflow.execute(run["id"])

    completed = database.get_parse_run(run["id"])
    assert completed["status"] == "COMPLETED"
    assert runtime.utterance_calls == 0
    assert any(event["type"] == "PARSE_TOOL_FINISHED" and event["data"].get("tool") == "LocalUtterancesReused" for event in completed["events"])


def test_confident_raw_stream_uses_only_boundary_worker(settings_factory):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key")
    database = Database(settings.database_path)
    database.initialize()
    transcript = "请介绍你负责的项目我负责推荐系统优化并推动研发完成上线"
    interview = database.create_interview({"id": "fast-raw-parse", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")
    context = ParsePipelineContext(workflow := ParseWorkflow(database, settings), run, material, interview)
    context.transcribe()

    class ConfidentRuntime:
        strategies = []

        def run_utterance_worker(self, atoms, strategy, _core_start):
            self.strategies.append(strategy)
            return {"utterances": [{
                "atom_ids": [item["id"] for item in atoms],
                "speaker_role": "candidate",
                "speaker_assessment": {"score": 90, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                "boundary_assessment": {"score": 90, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
            }]}

        def run_parse_auditor(self, *_args):
            raise AssertionError("高置信度首轮结果不应启动第二 Worker 或解析审计")

    runtime = ConfidentRuntime()
    context.runtime = runtime

    assert context._build_agent_utterances()
    assert runtime.strategies == ["boundary_first"]


def test_dialogue_chunks_are_processed_with_bounded_parallelism(settings_factory):
    settings = settings_factory(
        agent_runtime="helloagents",
        llm_api_key="test-key",
        parse_worker_concurrency=3,
    )
    database = Database(settings.database_path)
    database.initialize()
    transcript = "面试官：问题。\n候选人：回答。"
    interview = database.create_interview({"id": "parallel-dialogue", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")
    context = ParsePipelineContext(ParseWorkflow(database, settings), run, material, interview)
    context.profile = profile_transcript(transcript)
    context.atoms = [{"id": f"A{index:04d}", "rawText": "原子"} for index in range(84)]
    context.segments = [
        {
            "id": f"U{index:04d}", "ordinal": index + 1,
            "rawText": "请说明项目。" if index % 2 == 0 else "我负责项目推进。",
            "speakerRole": "interviewer" if index % 2 == 0 else "candidate",
            "speakerConfidence": 0.96, "atomIds": [f"A{index:04d}"],
        }
        for index in range(84)
    ]

    class ParallelRuntime:
        def __init__(self):
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def run_dialogue_worker(self, utterances):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                question_index = next(index for index, item in enumerate(utterances[:-1]) if item["speakerRole"] == "interviewer")
                question = utterances[question_index]
                answer = utterances[question_index + 1]
                assessment = {"score": 95, "reason_codes": [], "evidence_atom_ids": [], "summary": ""}
                return {"question_turns": [{
                    "question_utterance_ids": [question["id"]],
                    "answer_utterance_ids": [answer["id"]],
                    "turn_type": "main", "parent_question_anchor": None,
                    "question_type": "项目经历", "topic_title": "项目经历",
                    "question_boundary_assessment": assessment,
                    "answer_boundary_assessment": assessment,
                    "qa_pairing_assessment": assessment,
                    "follow_up_assessment": None,
                    "question_type_assessment": assessment,
                    "topic_grouping_assessment": assessment,
                }]}
            finally:
                with self.lock:
                    self.active -= 1

    runtime = ParallelRuntime()
    context.runtime = runtime

    cards = context._build_agent_cards()

    assert cards
    assert runtime.max_active == 3


def test_equivalent_overlap_does_not_create_hard_confidence_conflict(settings_factory):
    settings = settings_factory(
        agent_runtime="helloagents",
        llm_api_key="test-key",
        parse_worker_concurrency=2,
    )
    database = Database(settings.database_path)
    database.initialize()
    transcript = "面试官：问题。\n候选人：回答。"
    interview = database.create_interview({"id": "equivalent-overlap", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")
    context = ParsePipelineContext(ParseWorkflow(database, settings), run, material, interview)
    context.profile = profile_transcript(transcript)
    context.atoms = [
        {"id": f"A{index + 1:04d}", "ordinal": index + 1, "rawText": "原子"}
        for index in range(46)
    ]
    context.segments = [
        {
            "id": f"U{index + 1:04d}",
            "ordinal": index + 1,
            "rawText": "请说明项目。" if index % 2 == 0 else "我负责项目推进。",
            "speakerRole": "interviewer" if index % 2 == 0 else "candidate",
            "speakerConfidence": 0.96,
            "atomIds": [f"A{index + 1:04d}"],
        }
        for index in range(46)
    ]

    class EquivalentOverlapRuntime:
        def run_dialogue_worker(self, utterances):
            score = 95 if utterances[0]["ordinal"] == 1 else 90
            assessment = {"score": score, "reason_codes": [], "evidence_atom_ids": [], "summary": ""}
            return {
                "question_turns": [
                    {
                        "question_utterance_ids": [utterances[index]["id"]],
                        "answer_utterance_ids": [utterances[index + 1]["id"]],
                        "turn_type": "main",
                        "parent_question_anchor": None,
                        "question_type": "项目经历",
                        "topic_title": "项目经历",
                        "question_boundary_assessment": assessment,
                        "answer_boundary_assessment": assessment,
                        "qa_pairing_assessment": assessment,
                        "follow_up_assessment": None,
                        "question_type_assessment": assessment,
                        "topic_grouping_assessment": assessment,
                    }
                    for index in range(0, len(utterances), 2)
                ]
            }

    context.runtime = EquivalentOverlapRuntime()

    cards = context._build_agent_cards()

    assert len(cards) == 23
    assert all(card["confidence"] == "high" for card in cards)
    assert all(
        reason["code"] != "CHUNK_OVERLAP_CONFLICT"
        for card in cards
        for reason in card["confirmationReasons"]
    )


def test_local_relation_repair_recovers_clear_follow_ups_from_conservative_agent(settings_factory):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key")
    database = Database(settings.database_path)
    database.initialize()
    transcript = "面试官：请介绍项目。\n候选人：我负责项目推进。"
    interview = database.create_interview({"id": "local-follow-up-repair", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")
    context = ParsePipelineContext(ParseWorkflow(database, settings), run, material, interview)
    context.profile = profile_transcript(transcript)

    questions = [
        ("介绍一下 FlowPilot 项目的目标和结果。", "项目经历", "FlowPilot 项目"),
        ("你刚才提到管理层最初想做自动回复，如何证明问题判断是对的？", "项目经历", "问题判断"),
        ("具体说说你如何确定 MVP 用户和功能优先级。", "项目经历", "MVP 范围"),
        ("请讲一个你在 AI 产品里犯过的严重错误。", "行为面试", "错误复盘"),
    ]
    context.atoms = []
    context.segments = []
    for index, (question, _question_type, _topic) in enumerate(questions):
        question_atom = f"A{index * 2 + 1:04d}"
        answer_atom = f"A{index * 2 + 2:04d}"
        context.atoms.extend([
            {"id": question_atom, "ordinal": index * 2 + 1, "rawText": question},
            {"id": answer_atom, "ordinal": index * 2 + 2, "rawText": "候选人回答"},
        ])
        context.segments.extend([
            {
                "id": f"U{index * 2 + 1:04d}", "ordinal": index * 2 + 1, "rawText": question,
                "speakerRole": "interviewer", "speakerConfidence": 0.96, "atomIds": [question_atom],
            },
            {
                "id": f"U{index * 2 + 2:04d}", "ordinal": index * 2 + 2, "rawText": "候选人回答",
                "speakerRole": "candidate", "speakerConfidence": 0.96, "atomIds": [answer_atom],
            },
        ])

    class ConservativeRuntime:
        def run_dialogue_worker(self, utterances):
            assessment = {"score": 95, "reason_codes": [], "evidence_atom_ids": [], "summary": ""}
            turns = []
            for index, (question, question_type, topic) in enumerate(questions):
                turns.append({
                    "question_utterance_ids": [f"U{index * 2 + 1:04d}"],
                    "answer_utterance_ids": [f"U{index * 2 + 2:04d}"],
                    "turn_type": "main",
                    "parent_question_anchor": None,
                    "question_type": question_type,
                    "topic_title": topic,
                    "question_boundary_assessment": assessment,
                    "answer_boundary_assessment": assessment,
                    "qa_pairing_assessment": assessment,
                    "follow_up_assessment": None,
                    "question_type_assessment": assessment,
                    "topic_grouping_assessment": assessment,
                })
            return {"question_turns": turns}

    context.runtime = ConservativeRuntime()
    cards = context._build_agent_cards()

    assert [card["turnType"] for card in cards] == ["main", "follow_up", "follow_up", "main"]
    assert cards[1]["parentQuestionId"] == cards[0]["id"]
    assert cards[2]["parentQuestionId"] == cards[0]["id"]
    assert cards[1]["topicRootId"] == cards[0]["id"]
    assert cards[2]["topicRootId"] == cards[0]["id"]
    assert cards[3]["topicRootId"] == cards[3]["id"]


def test_dialogue_contract_drift_is_sanitized_without_lowering_high_confidence(settings_factory):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key")
    database = Database(settings.database_path)
    database.initialize()
    transcript = "面试官：请介绍项目。\n候选人：我负责推荐策略优化。"
    interview = database.create_interview({"id": "agent-contract-drift", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")

    class ContractDriftRuntime:
        def run_parse_agent(self, *_args, **_kwargs):
            return SimpleNamespace(text="scheduled")

        def run_utterance_worker(self, atoms, _strategy, _core_start):
            return {
                "utterances": [{
                    "atom_ids": [atom["id"]],
                    "speaker_role": atom["speakerRole"],
                    "speaker_assessment": {"score": 96, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                    "boundary_assessment": {"score": 96, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                } for atom in atoms]
            }

        def run_dialogue_worker(self, utterances):
            positive_reason = ["only_one_interpretation"]
            return {
                "question_turns": [{
                    "question_utterance_ids": [utterances[0]["id"]],
                    "answer_utterance_ids": [utterances[1]["id"]],
                    "turn_type": "main_question", "parent_question_anchor": None,
                    "question_type": "���", "topic_title": "���",
                    "question_boundary_assessment": {"score": 95, "reason_codes": positive_reason, "evidence_atom_ids": [], "summary": "���"},
                    "answer_boundary_assessment": {"score": 95, "reason_codes": positive_reason, "evidence_atom_ids": [], "summary": "���"},
                    "qa_pairing_assessment": {"score": 95, "reason_codes": positive_reason, "evidence_atom_ids": [], "summary": "���"},
                    "follow_up_assessment": {"score": 95, "reason_codes": positive_reason, "evidence_atom_ids": [], "summary": "���"},
                    "question_type_assessment": {"score": 95, "reason_codes": positive_reason, "evidence_atom_ids": [], "summary": "���"},
                    "topic_grouping_assessment": {"score": 95, "reason_codes": positive_reason, "evidence_atom_ids": [], "summary": "���"},
                }]
            }

        def run_parse_auditor(self, *_args):
            return {"selected": "boundary_first", "summary": ""}

    workflow = ParseWorkflow(database, settings)
    workflow.runtime = ContractDriftRuntime()
    workflow.execute(run["id"])

    question = database.get_questions(interview["id"])[0]
    assert question["parseMethod"] == "agent"
    assert question["confidence"] == "high"
    assert question["needsConfirmation"] is False
    assert question["questionType"] == "项目经历"
    assert "�" not in question["topicTitle"]


def test_invalid_agent_payload_uses_source_based_fallback_confidence(settings_factory):
    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key")
    database = Database(settings.database_path)
    database.initialize()
    transcript = "面试官：请介绍项目。\n候选人：我负责推荐策略优化。"
    interview = database.create_interview({"id": "agent-fallback-confidence", "raw_transcript": transcript})
    material = database.add_material(interview["id"], "transcript", transcript)
    run = database.create_parse_run(interview["id"], material["id"], "text")

    class InvalidRuntime:
        def run_parse_agent(self, *_args, **_kwargs):
            return SimpleNamespace(text="scheduled")

        def run_utterance_worker(self, atoms, _strategy, _core_start):
            return {
                "utterances": [{
                    "atom_ids": [atom["id"]],
                    "speaker_role": atom["speakerRole"],
                    "speaker_assessment": {"score": 96, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                    "boundary_assessment": {"score": 96, "reason_codes": [], "evidence_atom_ids": [], "summary": ""},
                } for atom in atoms]
            }

        def run_dialogue_worker(self, _utterances):
            return {"question_turns": "invalid"}

        def run_parse_auditor(self, *_args):
            return {"selected": "boundary_first", "summary": ""}

    workflow = ParseWorkflow(database, settings)
    workflow.runtime = InvalidRuntime()
    workflow.execute(run["id"])

    question = database.get_questions(interview["id"])[0]
    assert question["parseMethod"] == "deterministic"
    assert question["confidence"] == "high"
    assert question["needsConfirmation"] is False
    assert all(item["code"] != "REFERENCE_VALIDATION_FAILED" for item in question["confirmationReasons"])
