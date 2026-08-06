from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.domain.scoring import normalize_scores
from backend.app.schemas import ReviewBatch
from backend.app.services.knowledge import KnowledgeBase

try:  # HelloAgents is installed in the project environment, but tests can import without it.
    from hello_agents.tools.base import Tool, ToolParameter
    from hello_agents.tools.response import ToolResponse
except ImportError:  # pragma: no cover
    class Tool:
        def __init__(self, name: str, description: str, expandable: bool = False):
            self.name, self.description, self.expandable = name, description, expandable

    class ToolParameter:
        def __init__(self, **kwargs: Any):
            self.__dict__.update(kwargs)

    class ToolResponse:
        @staticmethod
        def success(text: str, data: Any = None, stats: Any = None):
            return {"status": "success", "text": text, "data": data, "stats": stats}

        @staticmethod
        def partial(text: str, data: Any = None, stats: Any = None):
            return {"status": "partial", "text": text, "data": data, "stats": stats}


class KnowledgeSearchTool(Tool):
    def __init__(self, knowledge: KnowledgeBase):
        super().__init__(name="KnowledgeSearch", description="检索本地面试评分准则和回答框架，返回可引用的知识片段。", expandable=False)
        self.knowledge = knowledge

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="query", type="string", description="要检索的问题、题型或能力维度", required=True),
            ToolParameter(name="limit", type="integer", description="最多返回条数，范围 1-3", required=False, default=3),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        query = str(parameters.get("query", "")).strip()
        limit = min(3, max(1, int(parameters.get("limit", 3))))
        hits = [hit.__dict__ for hit in self.knowledge.search(query, limit)]
        confidence = max((hit["confidence"] for hit in hits), default=0.0)
        return ToolResponse.success(text=f"本地知识库返回 {len(hits)} 条结果，最高置信度 {confidence:.2f}", data={"hits": hits, "confidence": confidence})


class EvidenceLookupTool(Tool):
    def __init__(self, context: dict[str, Any]):
        super().__init__(name="EvidenceLookup", description="只读检索原回答、岗位 JD 或简历中的可回查证据。", expandable=False)
        self.sources = {
            "transcript": str(context.get("raw_transcript", "")),
            "job_description": str(context.get("job_description", "")),
            "resume": str(context.get("resume_text", "")),
        }

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="source_type", type="string", description="transcript、job_description 或 resume", required=True),
            ToolParameter(name="query", type="string", description="要查找的关键词或短语", required=True),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        source_type = str(parameters.get("source_type", ""))
        query = str(parameters.get("query", "")).strip()
        source = self.sources.get(source_type, "")
        if not source or not query:
            return ToolResponse.partial(text="没有找到可检索的来源或查询为空", data={"matches": []})
        index = source.lower().find(query.lower())
        if index < 0:
            tokens = [token for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", query) if token]
            index = next((source.lower().find(token.lower()) for token in tokens if source.lower().find(token.lower()) >= 0), -1)
        if index < 0:
            return ToolResponse.partial(text="原始材料中没有找到匹配证据", data={"matches": []})
        start, end = max(0, index - 45), min(len(source), index + max(90, len(query) + 45))
        match = {"sourceType": source_type, "quote": source[start:end], "locator": f"字符 {start}-{end}", "verified": True}
        return ToolResponse.success(text="找到 1 条可回查原文", data={"matches": [match]})


class ScoreTool(Tool):
    def __init__(self):
        super().__init__(name="Score", description="按固定权重计算五维分数，模型不能自行修改权重。", expandable=False)

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="scores_json", type="string", description="包含 relevance、structure、evidence、depth、roleFit 的 JSON", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            raw = json.loads(str(parameters.get("scores_json", "{}")))
        except json.JSONDecodeError:
            return ToolResponse.partial(text="分数字段不是合法 JSON", data={})
        scores = normalize_scores(raw)
        return ToolResponse.success(text=f"确定性综合分为 {scores['overall']:.1f}/10", data=scores)


class WebVerifyTool(Tool):
    def __init__(self, settings: Settings):
        super().__init__(name="WebVerify", description="仅在本地知识不足或事实具有时效性时检索网页；结果不得直接提高评分。", expandable=False)
        self.settings = settings

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="query", type="string", description="需要事实核验的查询", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        if not self.settings.web_verify_enabled or not self.settings.tavily_api_key:
            return ToolResponse.partial(text="联网核验未启用，继续使用本地知识并标记不确定性", data={"results": [], "uncertain": True})
        try:
            from tavily import TavilyClient
            response = TavilyClient(api_key=self.settings.tavily_api_key).search(
                str(parameters.get("query", "")), max_results=3, include_answer=False, include_raw_content=False
            )
            results = [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("content", ""), "score": item.get("score", 0)} for item in response.get("results", [])[:3]]
            return ToolResponse.success(text=f"联网核验返回 {len(results)} 个带链接来源；这些来源不参与直接加分", data={"results": results, "uncertain": len(results) == 0})
        except Exception as exc:
            return ToolResponse.partial(text=f"联网核验不可用：{exc}；已降级到本地知识", data={"results": [], "uncertain": True})


class SubmitReviewBatchTool(Tool):
    def __init__(self):
        super().__init__(name="SubmitReviewBatch", description="提交结构化复盘批次；提交前执行 Pydantic Schema 校验。", expandable=False)
        self.last_submission: dict[str, Any] | None = None

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="review_json", type="string", description="符合 ReviewBatch Schema 的 JSON 字符串", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            payload = json.loads(str(parameters.get("review_json", "{}")))
            validated = ReviewBatch.model_validate(payload).model_dump(by_alias=True)
        except (json.JSONDecodeError, ValidationError) as exc:
            return ToolResponse.partial(text=f"结构化复盘未通过校验：{exc}", data={"accepted": False})
        self.last_submission = validated
        return ToolResponse.success(text=f"已接收 {len(validated['reviews'])} 道题的结构化复盘", data={"accepted": True, "reviewCount": len(validated["reviews"])})


def build_agent_tools(knowledge: KnowledgeBase, context: dict[str, Any], settings: Settings) -> tuple[list[Tool], SubmitReviewBatchTool]:
    submit = SubmitReviewBatchTool()
    return [KnowledgeSearchTool(knowledge), EvidenceLookupTool(context), ScoreTool(), WebVerifyTool(settings), submit], submit

