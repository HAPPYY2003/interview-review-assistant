from backend.app.database import Database
from backend.app.services.evidence import infer_question_type, infer_topic_title, normalize_question_type


def test_question_type_inference_prefers_specific_intent():
    assert infer_question_type("你在这个项目中为什么选择这套技术架构？") == "技术知识"
    assert infer_question_type("讲一次你推动团队解决分歧的经历。") == "行为面试"
    assert infer_question_type("为什么选择产品经理作为未来职业方向？") == "职业规划"
    assert infer_question_type("你有什么想问我们的吗？") == "反问环节"


def test_agent_question_type_labels_are_normalized_by_question_content():
    assert normalize_question_type("hypothetical", "实验没有显著提升时怎么判断下一步？") == "技术知识"
    assert normalize_question_type("behavioral", "请介绍一个你负责过的项目。") == "项目经历"
    assert normalize_question_type("probing", "你是如何解决跨团队分歧的？") == "行为面试"
    assert normalize_question_type("technical_knowledge", "请解释缓存策略。") == "技术知识"


def test_topic_title_describes_the_question_instead_of_repeating_its_type():
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
