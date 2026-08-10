from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.domain.scoring import DIMENSIONS, scores_from_levels
from backend.app.schemas import AuditSubmission, GrowthPlanSubmission, TopicReviewSubmission
from backend.app.services.knowledge import KnowledgeBase

try:
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


SCORING_SOURCE_TYPES = {"transcript", "job_description", "resume"}


def _evidence_id(source_type: str, source_id: str, quote: str, locator: str) -> str:
    digest = hashlib.sha256(f"{source_type}|{source_id}|{locator}|{quote}".encode("utf-8")).hexdigest()[:18]
    return f"ev-{digest}"


def _entry(source_type: str, source_id: str, quote: str, locator: str, *, confidence: float = 1.0, title: str = "", url: str = "") -> dict[str, Any]:
    return {
        "id": _evidence_id(source_type, source_id, quote, locator),
        "sourceType": source_type,
        "sourceId": source_id,
        "quote": quote,
        "locator": locator,
        "verified": True,
        "confidence": confidence,
        "title": title,
        "url": url,
    }


def _material_entries(source_type: str, text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    cursor = 0
    for index, part in enumerate(re.split(r"[\r\n；;]+", text or ""), 1):
        quote = part.strip()
        if not quote:
            continue
        start = text.find(quote, cursor)
        start = max(0, start)
        end = start + len(quote)
        cursor = end
        entries.append(_entry(source_type, f"{source_type}-{index}", quote, f"字符 {start}-{end}"))
    return entries


def build_evidence_catalog(context: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    segments = list(context.get("segments") or [])
    for index, segment in enumerate(segments, 1):
        quote = str(segment.get("rawText", "")).strip()
        if not quote or segment.get("excluded"):
            continue
        if segment.get("startTime") is not None:
            locator = f"{float(segment['startTime']):.2f}s-{float(segment.get('endTime') or segment['startTime']):.2f}s"
        else:
            locator = f"字符 {int(segment.get('startChar') or 0)}-{int(segment.get('endChar') or 0)}"
        entries.append(_entry("transcript", str(segment.get("id") or f"segment-{index}"), quote, locator, confidence=float(segment.get("confidence") or 0.0)))
    if not entries:
        entries.extend(_material_entries("transcript", str(context.get("raw_transcript", ""))))
    entries.extend(_material_entries("job_description", str(context.get("job_description", ""))))
    entries.extend(_material_entries("resume", str(context.get("resume_text", ""))))
    return entries


class KnowledgeSearchTool(Tool):
    def __init__(self, knowledge: KnowledgeBase):
        super().__init__(name="KnowledgeSearch", description="检索本地评分准则和回答框架；知识片段只能解释规则，不能直接提高评分。", expandable=False)
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
    def __init__(self, catalog: Iterable[dict[str, Any]], registry: dict[str, dict[str, Any]]):
        super().__init__(name="EvidenceLookup", description="检索原回答、岗位 JD 或简历，返回可用于结构化提交的证据 ID。", expandable=False)
        self.catalog = list(catalog)
        self.registry = registry

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="source_type", type="string", description="transcript、job_description 或 resume", required=True),
            ToolParameter(name="query", type="string", description="要查找的关键词或短语", required=True),
            ToolParameter(name="limit", type="integer", description="最多返回 1-3 条", required=False, default=3),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        source_type = str(parameters.get("source_type", ""))
        query = str(parameters.get("query", "")).strip()
        limit = min(3, max(1, int(parameters.get("limit", 3))))
        if source_type not in SCORING_SOURCE_TYPES or not query:
            return ToolResponse.partial(text="来源类型不合法或查询为空", data={"matches": []})
        lowered = query.lower()
        tokens = [token.lower() for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9.%]+", query)]
        ranked: list[tuple[int, dict[str, Any]]] = []
        for item in self.catalog:
            if item["sourceType"] != source_type:
                continue
            quote = item["quote"].lower()
            score = 100 if lowered in quote else sum(1 for token in tokens if token in quote)
            if score:
                ranked.append((score, item))
        matches = [item for _, item in sorted(ranked, key=lambda row: row[0], reverse=True)[:limit]]
        for item in matches:
            self.registry[item["id"]] = item
        status = ToolResponse.success if matches else ToolResponse.partial
        return status(text=f"找到 {len(matches)} 条可回查证据", data={"matches": matches})


class ScoreTool(Tool):
    def __init__(self):
        super().__init__(name="Score", description="将五维等级映射为固定分值，并按 20/15/25/20/20 计算综合分。", expandable=False)

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="levels_json", type="string", description="五个维度到优秀、良好、合格、较弱、缺失的 JSON 映射", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            levels = json.loads(str(parameters.get("levels_json", "{}")))
            scores = scores_from_levels(levels)
        except (json.JSONDecodeError, ValueError) as exc:
            return ToolResponse.partial(text=f"评分等级不合法：{exc}", data={"accepted": False})
        return ToolResponse.success(text=f"确定性综合分为 {scores['overall']:.1f}/10", data=scores)


class WebVerifyTool(Tool):
    def __init__(self, settings: Settings, registry: dict[str, dict[str, Any]]):
        super().__init__(name="WebVerify", description="仅在本地知识不足或事实具有时效性时核验网页；网页结果不能作为加分证据。", expandable=False)
        self.settings = settings
        self.registry = registry

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="query", type="string", description="需要事实核验的查询", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        if not self.settings.web_verify_enabled or not self.settings.tavily_api_key:
            return ToolResponse.partial(text="联网核验未启用，继续使用本地知识并标记不确定性", data={"results": [], "uncertain": True})
        try:
            from tavily import TavilyClient
            response = TavilyClient(api_key=self.settings.tavily_api_key).search(str(parameters.get("query", "")), max_results=3, include_answer=False, include_raw_content=False)
            results = []
            for index, item in enumerate(response.get("results", [])[:3], 1):
                ref = _entry("web", item.get("url", f"web-{index}"), item.get("content", ""), item.get("url", ""), confidence=float(item.get("score", 0)), title=item.get("title", ""), url=item.get("url", ""))
                self.registry[ref["id"]] = ref
                results.append(ref)
            return ToolResponse.success(text=f"联网核验返回 {len(results)} 个来源；这些来源不参与直接加分", data={"results": results, "uncertain": not results})
        except Exception as exc:
            return ToolResponse.partial(text=f"联网核验不可用：{exc}", data={"results": [], "uncertain": True})


def _referenced_ids(payload: TopicReviewSubmission) -> set[str]:
    result = {evidence_id for item in payload.dimensions for evidence_id in item.evidence_ids}
    result.update(evidence_id for item in (*payload.strengths, *payload.weaknesses) for evidence_id in item.evidence_ids)
    result.update(payload.star_rewrite.evidence_ids)
    result.update(payload.role_fit.evidence_ids)
    result.update(evidence_id for item in payload.follow_up_assessments for evidence_id in item.evidence_ids)
    return result


class SubmitTopicReviewTool(Tool):
    def __init__(self, topic: dict[str, Any], registry: dict[str, dict[str, Any]], *, has_jd: bool):
        super().__init__(name="SubmitTopicReview", description="提交当前主题的严格结构化证据诊断；只有通过 Schema 和证据校验的结果才会被接受。", expandable=False)
        self.topic = topic
        self.registry = registry
        self.has_jd = has_jd
        self.last_submission: dict[str, Any] | None = None
        self.last_review: dict[str, Any] | None = None
        self.last_error = ""

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="review_json", type="string", description="符合 TopicReviewSubmission Schema 的 JSON 字符串", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            payload = TopicReviewSubmission.model_validate_json(str(parameters.get("review_json", "{}")))
        except ValidationError as exc:
            return self._reject(f"结构化主题复盘未通过 Schema：{exc}")
        if payload.topic_id != self.topic["id"]:
            return self._reject("topicId 与当前任务不一致")
        if payload.topic_version != int(self.topic.get("version") or 1):
            return self._reject("topicVersion 与当前题卡版本不一致，请读取最新题卡后重新提交")
        follow_up_ids = {item["id"] for item in self.topic.get("followUpTurns", [])}
        submitted_follow_ups = {item.question_id for item in payload.follow_up_assessments}
        if submitted_follow_ups != follow_up_ids:
            return self._reject("追问评估必须完整覆盖当前主题全部追问")
        referenced = _referenced_ids(payload)
        missing = sorted(referenced - self.registry.keys())
        if missing:
            return self._reject(f"包含未通过 EvidenceLookup 获取的证据 ID：{', '.join(missing[:5])}")
        for assessment in payload.dimensions:
            scoring_refs = [self.registry[item] for item in assessment.evidence_ids if self.registry[item]["sourceType"] in SCORING_SOURCE_TYPES]
            if assessment.level in {"优秀", "良好"} and not scoring_refs:
                return self._reject(f"{assessment.dimension} 缺少原回答、JD 或简历证据，不能选择高等级")
            if assessment.dimension == "roleFit" and not self.has_jd and assessment.level in {"优秀", "良好"}:
                return self._reject("缺少 JD 时岗位匹配最高只能选择合格")
        source_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", " ".join(self.registry[item]["quote"] for item in payload.star_rewrite.evidence_ids)))
        rewrite_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", payload.star_rewrite.full_answer))
        if rewrite_numbers - source_numbers:
            return self._reject("STAR 改写包含证据中不存在的数字")
        levels = {item.dimension: item.level for item in payload.dimensions}
        scores = scores_from_levels(levels)
        evidence_refs = [self.registry[item] for item in sorted(referenced)]
        score_evidence = []
        for assessment in payload.dimensions:
            first = self.registry[assessment.evidence_ids[0]]
            score_evidence.append({
                "dimension": assessment.dimension,
                "level": assessment.level,
                "score": scores[assessment.dimension],
                "rationale": assessment.rationale,
                "quote": first["quote"],
                "evidenceIds": assessment.evidence_ids,
            })
        raw = payload.model_dump(by_alias=True)
        priority = "high" if scores["overall"] < 5.5 else "medium" if scores["overall"] < 7.2 else "low"
        self.last_submission = raw
        self.last_review = {
            **self.topic,
            "diagnosis": payload.diagnosis,
            "scores": scores,
            "scoreLevels": levels,
            "scoreEvidence": score_evidence,
            "evidenceRefs": evidence_refs,
            "strengths": [item.text for item in payload.strengths],
            "weaknesses": [item.text for item in payload.weaknesses],
            "suggestedStructure": payload.suggested_structure,
            "knowledgeToPrepare": payload.knowledge_to_prepare,
            "roleFitDiagnosis": {
                "summary": payload.role_fit.summary,
                "evidenceIds": payload.role_fit.evidence_ids,
                "missingRequirements": payload.role_fit.missing_requirements,
                "uncertainty": payload.role_fit.uncertainty,
                "riskLevel": priority if self.has_jd else "unknown",
            },
            "starRewrite": payload.star_rewrite.model_dump(by_alias=True),
            "followUpTurns": self._follow_ups(payload),
            "uncertainties": payload.uncertainties,
            "revisionSummary": payload.revision_summary,
            "priority": {"level": priority, "reason": "由五维等级和证据完整性共同确定。"},
        }
        return ToolResponse.success(text="主题复盘已通过结构、版本和证据校验", data={"accepted": True, "topicId": payload.topic_id, "topicVersion": payload.topic_version, "scores": scores})

    def _follow_ups(self, payload: TopicReviewSubmission) -> list[dict[str, Any]]:
        by_id = {item.question_id: item for item in payload.follow_up_assessments}
        result = []
        for turn in self.topic.get("followUpTurns", []):
            assessment = by_id[turn["id"]]
            result.append({**turn, "followUpImpact": assessment.impact, "followUpRationale": assessment.rationale, "evidenceIds": assessment.evidence_ids})
        return result

    def _reject(self, message: str) -> ToolResponse:
        self.last_error = message
        return ToolResponse.partial(text=message, data={"accepted": False})


class JsonSnapshotTool(Tool):
    def __init__(self, name: str, description: str, payload: Any):
        super().__init__(name=name, description=description, expandable=False)
        self.payload = payload

    def get_parameters(self) -> list[ToolParameter]:
        return []

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        return ToolResponse.success(text="已返回只读结构化数据", data={"payload": self.payload})


class VerifyEvidenceTool(Tool):
    def __init__(self, registry: dict[str, dict[str, Any]]):
        super().__init__(name="VerifyEvidence", description="回查一个或多个证据 ID 是否来自系统登记的原始来源。", expandable=False)
        self.registry = registry

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="evidence_ids_json", type="string", description="证据 ID 数组 JSON", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            ids = json.loads(str(parameters.get("evidence_ids_json", "[]")))
        except json.JSONDecodeError:
            return ToolResponse.partial(text="证据 ID 数组不是合法 JSON", data={"valid": False})
        missing = [item for item in ids if item not in self.registry]
        return ToolResponse.success(text="证据回查完成", data={"valid": not missing, "missing": missing, "matches": [self.registry[item] for item in ids if item in self.registry]})


class SubmitAuditTool(Tool):
    def __init__(self, topic_ids: set[str], evidence_ids: set[str]):
        super().__init__(name="SubmitAudit", description="提交 Reflection 审计决定和结构化发现。", expandable=False)
        self.topic_ids = topic_ids
        self.evidence_ids = evidence_ids
        self.last_submission: dict[str, Any] | None = None
        self.last_error = ""

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="audit_json", type="string", description="符合 AuditSubmission Schema 的 JSON 字符串", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            payload = AuditSubmission.model_validate_json(str(parameters.get("audit_json", "{}")))
        except ValidationError as exc:
            return self._reject(f"审计提交未通过 Schema：{exc}")
        invalid_topics = sorted({item.topic_id for item in payload.findings} - self.topic_ids)
        invalid_evidence = sorted({evidence_id for item in payload.findings for evidence_id in item.evidence_ids} - self.evidence_ids)
        if invalid_topics:
            return self._reject(f"审计包含未知主题：{', '.join(invalid_topics)}")
        if invalid_evidence:
            return self._reject(f"审计包含未知证据：{', '.join(invalid_evidence[:5])}")
        self.last_submission = payload.model_dump(by_alias=True)
        return ToolResponse.success(text=f"审计结果已接收：{payload.decision}", data={"accepted": True, "decision": payload.decision, "findingCount": len(payload.findings)})

    def _reject(self, message: str) -> ToolResponse:
        self.last_error = message
        return ToolResponse.partial(text=message, data={"accepted": False})


class SubmitPlanTool(Tool):
    def __init__(self, topic_ids: set[str]):
        super().__init__(name="SubmitPlan", description="提交整场总结、风险和完整七天训练计划。", expandable=False)
        self.topic_ids = topic_ids
        self.last_submission: dict[str, Any] | None = None
        self.last_error = ""

    def get_parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="plan_json", type="string", description="符合 GrowthPlanSubmission Schema 的 JSON 字符串", required=True)]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            payload = GrowthPlanSubmission.model_validate_json(str(parameters.get("plan_json", "{}")))
        except ValidationError as exc:
            return self._reject(f"成长计划未通过 Schema：{exc}")
        invalid_topics = sorted({topic_id for risk in payload.top_risks for topic_id in risk.topic_ids} - self.topic_ids)
        if invalid_topics:
            return self._reject(f"成长计划包含未知主题：{', '.join(invalid_topics)}")
        result = payload.model_dump(by_alias=True)
        result["actionItems"] = [
            {**item, "id": f"day-{item['day']}", "completed": False}
            for item in result["actionItems"]
        ]
        self.last_submission = result
        return ToolResponse.success(text="七天成长计划已通过结构校验", data={"accepted": True, "actionCount": 7})

    def _reject(self, message: str) -> ToolResponse:
        self.last_error = message
        return ToolResponse.partial(text=message, data={"accepted": False})


def build_evidence_agent_tools(knowledge: KnowledgeBase, context: dict[str, Any], settings: Settings, topic: dict[str, Any]) -> tuple[list[Tool], SubmitTopicReviewTool, dict[str, dict[str, Any]]]:
    registry: dict[str, dict[str, Any]] = {}
    catalog = build_evidence_catalog(context)
    submit = SubmitTopicReviewTool(topic, registry, has_jd=bool(str(context.get("job_description", "")).strip()))
    return [EvidenceLookupTool(catalog, registry), KnowledgeSearchTool(knowledge), ScoreTool(), WebVerifyTool(settings, registry), submit], submit, registry


def build_audit_agent_tools(draft: list[dict[str, Any]], registry: dict[str, dict[str, Any]]) -> tuple[list[Tool], SubmitAuditTool]:
    topic_ids = {item["id"] for item in draft}
    submit = SubmitAuditTool(topic_ids, set(registry))
    return [JsonSnapshotTool("GetDraftReview", "读取待审计的完整结构化复盘草稿。", draft), VerifyEvidenceTool(registry), submit], submit


def build_growth_agent_tools(draft: list[dict[str, Any]], history: list[dict[str, Any]], knowledge: KnowledgeBase) -> tuple[list[Tool], SubmitPlanTool]:
    topic_ids = {item["id"] for item in draft}
    submit = SubmitPlanTool(topic_ids)
    return [
        JsonSnapshotTool("GetAuditedReview", "读取已通过 Reflection 审计的复盘结果。", draft),
        JsonSnapshotTool("GetGrowthHistory", "读取同岗位优先的脱敏成长趋势。", history),
        KnowledgeSearchTool(knowledge),
        submit,
    ], submit


def build_agent_tools(knowledge: KnowledgeBase, context: dict[str, Any], settings: Settings) -> tuple[list[Tool], SubmitTopicReviewTool]:
    topic = context.get("topic") or {"id": "topic", "followUpTurns": []}
    tools, submit, _ = build_evidence_agent_tools(knowledge, context, settings, topic)
    return tools, submit
