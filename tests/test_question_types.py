from backend.app.database import Database
from backend.app.services.evidence import (
    EvidenceReviewService,
    infer_question_type,
    infer_topic_title,
    normalize_question_type,
)
from backend.app.services.transcript_structure import ConfidenceAssessment, QuestionTurnSubmission


def test_question_type_inference_prefers_specific_intent():
    assert infer_question_type("请你用三分钟做一个自我介绍，并说明为什么适合这个岗位。") == "自我介绍"
    assert infer_question_type("先简单介绍一下你自己和个人背景。") == "自我介绍"
    assert infer_question_type("你在这个项目中为什么选择这套技术架构？") == "技术知识"
    assert infer_question_type("讲一次你推动团队解决分歧的经历。") == "行为面试"
    assert infer_question_type("为什么选择产品经理作为未来职业方向？") == "职业规划"
    assert infer_question_type("你有什么想问我们的吗？") == "反问环节"


def test_agent_question_type_labels_are_normalized_by_question_content():
    assert normalize_question_type("self_introduction", "请介绍一下你自己。") == "自我介绍"
    assert normalize_question_type("hypothetical", "实验没有显著提升时怎么判断下一步？") == "技术知识"
    assert normalize_question_type("behavioral", "请介绍一个你负责过的项目。") == "项目经历"
    assert normalize_question_type("probing", "你是如何解决跨团队分歧的？") == "行为面试"
    assert normalize_question_type("technical_knowledge", "请解释缓存策略。") == "技术知识"


def test_topic_title_describes_the_question_instead_of_repeating_its_type():
    assert infer_topic_title("请介绍一下你自己。", "自我介绍") == "个人背景与岗位契合"
    assert infer_topic_title("讲一次你推动研发和设计解决分歧的经历。", "行为面试") == "跨团队分歧处理"
    assert infer_topic_title("如果实验结果不显著，你会怎么判断下一步？", "技术知识") == "实验分析与决策"


def test_database_normalizes_new_and_legacy_question_types(settings_factory):
    settings = settings_factory()
    database = Database(settings.database_path)
    database.initialize()
    database.create_interview({"id": "question-type-test"})
    database.replace_questions(
        "question-type-test",
        [
            {
                "id": "technical-question",
                "interviewerQuestion": "请说明实验没有显著提升时怎么判断下一步？",
                "questionType": "hypothetical",
                "topicTitle": "技术知识",
            }
        ],
    )
    assert database.get_questions("question-type-test")[0]["questionType"] == "技术知识"

    with database.connect() as connection:
        connection.execute(
            "UPDATE question_cards SET question_type='hypothetical' WHERE id='technical-question'"
        )
    database.initialize()
    assert database.get_questions("question-type-test")[0]["questionType"] == "技术知识"


def test_self_introduction_is_supported_by_structuring_and_review(settings_factory):
    assessment = ConfidenceAssessment(score=90)
    turn = QuestionTurnSubmission(
        question_utterance_ids=["utterance-1"],
        answer_utterance_ids=["utterance-2"],
        question_type="自我介绍",
        topic_title="个人背景与岗位契合",
        question_boundary_assessment=assessment,
        answer_boundary_assessment=assessment,
        qa_pairing_assessment=assessment,
        question_type_assessment=assessment,
        topic_grouping_assessment=assessment,
    )
    assert turn.question_type == "自我介绍"

    settings = settings_factory()
    database = Database(settings.database_path)
    database.initialize()
    database.create_interview({"id": "self-introduction-test"})
    database.replace_questions(
        "self-introduction-test",
        [
            {
                "id": "self-introduction-question",
                "interviewerQuestion": "请用三分钟做一个自我介绍。",
                "questionType": "self_intro",
                "topicTitle": "自我介绍",
            }
        ],
    )
    question = database.get_questions("self-introduction-test")[0]
    assert question["questionType"] == "自我介绍"

    framework = EvidenceReviewService._recommended_framework(
        question["questionType"], "我有五年企业产品经验。", ["ev-1"], []
    )
    assert framework["type"] == "FIT_EVIDENCE_MOTIVATION"
    assert [section["key"] for section in framework["sections"]] == ["FIT", "EVIDENCE", "MOTIVATION"]


def test_database_repairs_a_conflicting_generated_topic_title(settings_factory):
    settings = settings_factory()
    database = Database(settings.database_path)
    database.initialize()
    database.create_interview({"id": "topic-title-test"})
    database.replace_questions(
        "topic-title-test",
        [
            {
                "id": "behavior-question",
                "interviewerQuestion": "讲一次你推动研发和设计解决分歧的经历。",
                "questionType": "行为面试",
                "topicTitle": "项目经历",
            }
        ],
    )

    database.initialize()

    topic = database.get_question_topics("topic-title-test")[0]
    assert topic["title"] == "跨团队分歧处理"
    assert topic["mainTurn"]["questionType"] == "行为面试"
