from io import BytesIO

import pytest
from docx import Document

from backend.app.services.document_parser import DocumentParseError, DocumentParser
from backend.app.services.knowledge import KnowledgeBase
from backend.app.services.text_utils import repair_mojibake


def test_text_and_docx_parsing(tmp_path):
    parser = DocumentParser(5 * 1024 * 1024)
    text = parser.parse("resume.txt", "产品经理，转化率提升 18%".encode("utf-8"))
    assert "18%" in text.text

    document = Document()
    document.add_paragraph("负责推荐系统实验")
    buffer = BytesIO()
    document.save(buffer)
    parsed = parser.parse("resume.docx", buffer.getvalue())
    assert "推荐系统实验" in parsed.text
    assert parsed.metadata["paragraphs"] == 1


def test_invalid_document_is_rejected():
    parser = DocumentParser(10)
    with pytest.raises(DocumentParseError, match="5MB"):
        parser.parse("large.txt", b"12345678901")
    with pytest.raises(DocumentParseError, match="仅支持"):
        parser.parse("resume.exe", b"text")


def test_local_knowledge_returns_citations(settings_factory):
    settings = settings_factory()
    knowledge = KnowledgeBase(settings.knowledge_dir)
    hits = knowledge.search("STAR 项目经历 关键行动 量化结果")
    assert hits
    assert hits[0].source.endswith(".md")
    assert 0 < hits[0].confidence <= 1


def test_mojibake_repair_and_timestamped_transcript(settings_factory):
    clean = "[00:01] 面试官：请介绍你负责的项目？\n[00:10] 候选人：我负责需求分析。"
    assert repair_mojibake("闈㈣瘯瀹橈細") == "面试官："

    settings = settings_factory()
    knowledge = KnowledgeBase(settings.knowledge_dir)
    from backend.app.services.evidence import EvidenceReviewService

    questions = EvidenceReviewService(knowledge).parse_transcript(clean)
    assert len(questions) == 1
    assert questions[0]["interviewerQuestion"] == "请介绍你负责的项目？"
