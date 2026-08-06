import json

from backend.app.services.knowledge import KnowledgeBase
from backend.app.tools import build_agent_tools


def test_custom_tools_are_real_helloagents_tools(settings_factory):
    from hello_agents.tools.base import Tool

    settings = settings_factory()
    context = {
        "raw_transcript": "候选人：我推动实验后转化率提升 20%。",
        "job_description": "负责实验设计和数据分析。",
        "resume_text": "转化率提升 20%。",
    }
    tools, submit = build_agent_tools(KnowledgeBase(settings.knowledge_dir), context, settings)
    assert all(isinstance(tool, Tool) for tool in tools)

    evidence = next(tool for tool in tools if tool.name == "EvidenceLookup").run({"source_type": "transcript", "query": "20%"})
    assert evidence.status.value == "success"
    assert evidence.data["matches"][0]["verified"] is True

    score = next(tool for tool in tools if tool.name == "Score").run({"scores_json": json.dumps({"relevance": 8, "structure": 7, "evidence": 6, "depth": 7, "roleFit": 8})})
    assert score.data["overall"] == 7.2

    invalid = submit.run({"review_json": "{}"})
    assert invalid.status.value == "partial"


def test_supervisor_registers_task_and_domain_tools(settings_factory):
    from backend.app.agents.runtime import HelloAgentsRuntime
    from hello_agents import ToolRegistry

    settings = settings_factory(agent_runtime="helloagents", llm_api_key="test-key", llm_base_url="https://example.invalid/v1")
    runtime = HelloAgentsRuntime(settings)
    assert runtime.available is True
    registry = ToolRegistry()
    tools, _ = build_agent_tools(KnowledgeBase(settings.knowledge_dir), {}, settings)
    for tool in tools:
        registry.register_tool(tool)
    supervisor = runtime.create_supervisor(registry)
    names = set(registry.list_tools())
    assert {"KnowledgeSearch", "EvidenceLookup", "Score", "WebVerify", "SubmitReviewBatch", "Task"}.issubset(names)
    assert supervisor.__class__.__name__ == "PlanSolveAgent"
