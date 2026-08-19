from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
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
TOPIC_TEMPLATE_PLACEHOLDERS = {
    "综合诊断",
    "至少两个字的判断依据",
    "至少两个字的追问判断",
    "有证据的优点",
    "有证据的问题",
    "原回答路径概括",
    "步骤名称",
    "原回答内容",
    "逻辑缺口",
    "框架选择原因",
    "表达建议",
    "待补充：缺失事实",
    "只重组证据中的事实",
    "待补充的信息",
    "回答组织建议",
    "需要学习的知识",
    "岗位匹配判断",
}

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CHINESE_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
CHINESE_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}
CHINESE_NUMBER_PATTERN = r"[零〇一二两三四五六七八九十百千万亿点]+"
CHINESE_NUMBER_SUFFIXES = (
    "个百分点", "小时", "分钟", "工单", "客户", "任务", "模型",
    "个", "位", "名", "家", "条", "次", "年", "月", "日", "天", "周", "秒",
    "元", "人", "项", "层", "步", "类", "种", "轮", "分", "倍",
)


def _template_placeholder_paths(value: Any, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            paths.extend(_template_placeholder_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_template_placeholder_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        text = value.strip()
        if text in TOPIC_TEMPLATE_PLACEHOLDERS or re.search(r"__FILL_[A-Z0-9_]+__", text):
            paths.append(path)
    return paths


def _canonical_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f").rstrip("0").rstrip(".")


def _chinese_integer(token: str) -> int | None:
    if not token:
        return None
    if all(char in CHINESE_DIGITS for char in token):
        return int("".join(str(CHINESE_DIGITS[char]) for char in token))

    total = 0
    section = 0
    number = 0
    for char in token:
        if char in CHINESE_DIGITS:
            number = CHINESE_DIGITS[char]
        elif char in CHINESE_SMALL_UNITS:
            unit = CHINESE_SMALL_UNITS[char]
            section += (number or 1) * unit
            number = 0
        elif char in CHINESE_LARGE_UNITS:
            section += number
            total += (section or 1) * CHINESE_LARGE_UNITS[char]
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _chinese_decimal(token: str) -> Decimal | None:
    if not token:
        return None
    integer_text, separator, fractional_text = token.partition("点")
    integer = _chinese_integer(integer_text or "零")
    if integer is None:
        return None
    if not separator:
        return Decimal(integer)
    if not fractional_text or any(char not in CHINESE_DIGITS for char in fractional_text):
        return None
    digits = "".join(str(CHINESE_DIGITS[char]) for char in fractional_text)
    return Decimal(f"{integer}.{digits}")


def _numeric_claims(text: str) -> set[str]:
    """Return comparable numeric claims across Chinese and Arabic notation."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).replace("％", "%")
    claims: set[str] = set()

    percentage_pattern = re.compile(rf"百分之(?P<number>{CHINESE_NUMBER_PATTERN})")
    for match in percentage_pattern.finditer(normalized):
        value = _chinese_decimal(match.group("number"))
        if value is not None:
            claims.add(f"{_canonical_decimal(value)}%")
    without_chinese_percentages = percentage_pattern.sub(" ", normalized)

    for match in re.finditer(r"\d+(?:\.\d+)?%?", without_chinese_percentages):
        token = match.group(0)
        suffix = "%" if token.endswith("%") else ""
        number = token[:-1] if suffix else token
        try:
            claims.add(f"{_canonical_decimal(Decimal(number))}{suffix}")
        except InvalidOperation:
            continue

    for match in re.finditer(CHINESE_NUMBER_PATTERN, without_chinese_percentages):
        token = match.group(0)
        before = without_chinese_percentages[max(0, match.start() - 1):match.start()]
        after = without_chinese_percentages[match.end():match.end() + 5]
        has_numeric_unit = any(char in token for char in "十百千万亿点")
        has_counter = any(after.startswith(suffix) for suffix in CHINESE_NUMBER_SUFFIXES)
        is_ordinal = before == "第"
        is_plain_digit_sequence = len(token) > 1 and all(char in CHINESE_DIGITS for char in token)
        if token == "一" and before == "进" and after.startswith("步"):
            continue
        if not (has_numeric_unit or has_counter or is_ordinal or is_plain_digit_sequence):
            continue
        value = _chinese_decimal(token)
        if value is not None:
            claims.add(_canonical_decimal(value))
    return claims


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
    def __init__(self, catalog: Iterable[dict[str, Any]], registry: dict[str, dict[str, Any]], *, max_calls: int = 2):
        super().__init__(name="EvidenceLookup", description="检索原回答、岗位 JD 或简历，返回可用于结构化提交的证据 ID。", expandable=False)
        self.catalog = list(catalog)
        self.registry = registry
        self.max_calls = max(1, max_calls)
        self.call_count = 0

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="source_type", type="string", description="transcript、job_description 或 resume", required=True),
            ToolParameter(name="query", type="string", description="要查找的关键词或短语", required=True),
            ToolParameter(name="limit", type="integer", description="最多返回 1-3 条", required=False, default=3),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        self.call_count += 1
        if self.call_count > self.max_calls:
            return ToolResponse.partial(
                text="EvidenceLookup 检索预算已用完。请使用已经返回的证据 ID，并立即调用 SubmitTopicReview。",
                data={"matches": [], "budgetExhausted": True, "maxCalls": self.max_calls},
            )
        return self._lookup(parameters)

    def prefetch(self, parameters: dict[str, Any]) -> ToolResponse:
        """Register trusted source matches without consuming the agent lookup budget."""
        return self._lookup(parameters)

    def _lookup(self, parameters: dict[str, Any]) -> ToolResponse:
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
        if not matches:
            return status(text="没有找到可回查证据", data={"matches": []})
        visible_matches = [
            {
                "evidenceId": item["id"],
                "sourceType": item["sourceType"],
                "locator": item["locator"],
                "quote": item["quote"],
            }
            for item in matches
        ]
        return status(
            text=(
                f"找到 {len(matches)} 条可回查证据。提交复盘时 evidenceIds 只能使用下列 evidenceId：\n"
                f"{json.dumps(visible_matches, ensure_ascii=False)}"
            ),
            data={"matches": matches},
        )

    def reset_budget(self) -> None:
        self.call_count = 0


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
    result.update(evidence_id for item in payload.answer_logic.steps for evidence_id in item.evidence_ids)
    result.update(evidence_id for item in payload.answer_logic.gaps for evidence_id in item.evidence_ids)
    result.update(evidence_id for item in payload.interviewer_signals for evidence_id in item.evidence_ids)
    result.update(payload.recommended_answer.evidence_ids)
    result.update(evidence_id for item in payload.recommended_answer.framework.sections for evidence_id in item.evidence_ids)
    if payload.star_rewrite:
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
        raw = payload.model_dump(by_alias=True)
        placeholder_paths = _template_placeholder_paths(raw)
        if placeholder_paths:
            fields = ", ".join(placeholder_paths[:6])
            return self._reject(f"主题复盘仍包含提交模板占位内容：{fields}。必须根据当前题目和证据填写真实分析")
        if payload.topic_id != self.topic["id"]:
            return self._reject("topicId 与当前任务不一致")
        if payload.topic_version != int(self.topic.get("version") or 1):
            return self._reject("topicVersion 与当前题卡版本不一致，请读取最新题卡后重新提交")
        follow_up_ids = {item["id"] for item in self.topic.get("followUpTurns", [])}
        submitted_follow_ups = {item.question_id for item in payload.follow_up_assessments}
        if submitted_follow_ups != follow_up_ids:
            return self._reject("追问评估必须完整覆盖当前主题全部追问")
        submitted_signals = {item.turn_id for item in payload.interviewer_signals}
        missing_follow_up_signals = sorted(follow_up_ids - submitted_signals)
        if missing_follow_up_signals:
            return self._reject(f"每个追问都必须生成可回查的面试官信号：{', '.join(missing_follow_up_signals[:5])}")
        rationales = [" ".join(item.rationale.split()) for item in payload.dimensions]
        if len(set(rationales)) <= 2:
            return self._reject("五维评分不能大面积复用同一句判断依据，必须分别说明相关性、结构、证据、深度和岗位匹配")
        referenced = _referenced_ids(payload)
        missing = sorted(referenced - self.registry.keys())
        if missing:
            return self._reject(f"包含未通过 EvidenceLookup 获取的证据 ID：{', '.join(missing[:5])}")
        logic_ids = {
            evidence_id
            for item in (*payload.answer_logic.steps, *payload.answer_logic.gaps)
            for evidence_id in item.evidence_ids
        }
        if any(self.registry[item]["sourceType"] != "transcript" for item in logic_ids):
            return self._reject("回答逻辑只能引用面试原文证据")
        turn_questions = {
            str(turn["id"]): str(turn.get("interviewerQuestion") or "").strip()
            for turn in [self.topic, *self.topic.get("followUpTurns", [])]
        }
        for signal in payload.interviewer_signals:
            if signal.turn_id not in turn_questions:
                return self._reject(f"面试官信号包含未知话轮：{signal.turn_id}")
            signal_refs = [self.registry[item] for item in signal.evidence_ids]
            if any(item["sourceType"] != "transcript" for item in signal_refs):
                return self._reject("面试官信号只能引用面试原文")
            question_text = turn_questions[signal.turn_id]
            has_question_quote = any(
                question_text and (question_text in item["quote"] or item["quote"] in question_text)
                for item in signal_refs
            )
            if not has_question_quote:
                return self._reject(f"面试官信号没有引用对应问题原文：{signal.turn_id}")
        for assessment in payload.dimensions:
            scoring_refs = [self.registry[item] for item in assessment.evidence_ids if self.registry[item]["sourceType"] in SCORING_SOURCE_TYPES]
            if assessment.level in {"优秀", "良好"} and not scoring_refs:
                return self._reject(f"{assessment.dimension} 缺少原回答、JD 或简历证据，不能选择高等级")
            if assessment.dimension == "roleFit" and not self.has_jd and assessment.level in {"优秀", "良好"}:
                return self._reject("缺少 JD 时岗位匹配最高只能选择合格")
        answer_ids = set(payload.recommended_answer.evidence_ids)
        answer_ids.update(
            evidence_id for section in payload.recommended_answer.framework.sections for evidence_id in section.evidence_ids
        )
        answer_refs = [self.registry[item] for item in answer_ids]
        if any(item["sourceType"] not in {"transcript", "resume"} for item in answer_refs):
            return self._reject("推荐回答只能使用面试原文或简历中的候选人事实")
        for section in payload.recommended_answer.framework.sections:
            if not section.evidence_ids and "待补充" not in section.draft:
                return self._reject(f"回答框架段落“{section.label}”缺少证据时必须明确标记待补充")
        source_numbers = _numeric_claims(" ".join(item["quote"] for item in answer_refs))
        rewrite_text = " ".join([
            payload.recommended_answer.full_answer,
            *[item.draft for item in payload.recommended_answer.framework.sections],
        ])
        rewrite_numbers = _numeric_claims(rewrite_text)
        missing_numbers = sorted(rewrite_numbers - source_numbers)
        if missing_numbers:
            return self._reject(f"推荐回答包含证据中不存在的数字：{', '.join(missing_numbers[:8])}")
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
        priority = "high" if scores["overall"] < 5.5 else "medium" if scores["overall"] < 7.2 else "low"
        legacy_star = payload.star_rewrite.model_dump(by_alias=True) if payload.star_rewrite else self._legacy_star(payload)
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
            "strengthClaims": [item.model_dump(by_alias=True) for item in payload.strengths],
            "weaknessClaims": [item.model_dump(by_alias=True) for item in payload.weaknesses],
            "answerLogic": payload.answer_logic.model_dump(by_alias=True),
            "interviewerSignals": [item.model_dump(by_alias=True) for item in payload.interviewer_signals],
            "recommendedAnswer": payload.recommended_answer.model_dump(by_alias=True),
            "suggestedStructure": payload.suggested_structure,
            "knowledgeToPrepare": payload.knowledge_to_prepare,
            "roleFitDiagnosis": {
                "summary": payload.role_fit.summary,
                "evidenceIds": payload.role_fit.evidence_ids,
                "missingRequirements": payload.role_fit.missing_requirements,
                "uncertainty": payload.role_fit.uncertainty,
                "riskLevel": priority if self.has_jd else "unknown",
            },
            "starRewrite": legacy_star,
            "followUpTurns": self._follow_ups(payload),
            "uncertainties": payload.uncertainties,
            "revisionSummary": payload.revision_summary,
            "priority": {"level": priority, "reason": "由五维等级和证据完整性共同确定。"},
        }
        return ToolResponse.success(text="主题复盘已通过结构、版本和证据校验", data={"accepted": True, "topicId": payload.topic_id, "topicVersion": payload.topic_version, "scores": scores})

    @staticmethod
    def _legacy_star(payload: TopicReviewSubmission) -> dict[str, Any]:
        sections = {item.key.upper(): item.draft for item in payload.recommended_answer.framework.sections}
        return {
            "situation": sections.get("S", ""),
            "task": sections.get("T", ""),
            "action": sections.get("A", ""),
            "result": sections.get("R", ""),
            "fullAnswer": payload.recommended_answer.full_answer,
            "evidenceIds": payload.recommended_answer.evidence_ids,
            "missingInformation": payload.recommended_answer.missing_information,
        }

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
        if self.last_submission is not None:
            return ToolResponse.success(
                text="审计结果此前已经接收，本次重复提交已忽略。请立即结束审计任务。",
                data={
                    "accepted": True,
                    "locked": True,
                    "decision": self.last_submission["decision"],
                    "findingCount": len(self.last_submission["findings"]),
                },
            )
        try:
            payload = AuditSubmission.model_validate_json(str(parameters.get("audit_json", "{}")))
        except ValidationError as exc:
            return self._reject(f"审计提交未通过 Schema：{exc}")
        invalid_topics = sorted({item.topic_id for item in payload.findings} - self.topic_ids)
        invalid_evidence = sorted({evidence_id for item in payload.findings for evidence_id in item.evidence_ids} - self.evidence_ids)
        if invalid_topics:
            allowed = ", ".join(sorted(self.topic_ids))
            return self._reject(f"审计包含未知主题：{', '.join(invalid_topics)}。可用主题 ID 只有：{allowed}")
        if invalid_evidence:
            return self._reject(f"审计包含未知证据：{', '.join(invalid_evidence[:5])}")
        self.last_submission = payload.model_dump(by_alias=True)
        self.last_error = ""
        return ToolResponse.success(text=f"审计结果已接收：{payload.decision}", data={"accepted": True, "decision": payload.decision, "findingCount": len(payload.findings)})

    def _reject(self, message: str) -> ToolResponse:
        self.last_error = message
        return ToolResponse.partial(text=message, data={"accepted": False})


class SubmitPlanTool(Tool):
    def __init__(
        self,
        topic_ids: list[str] | set[str],
        evidence_ids: set[str],
        topic_evidence_ids: dict[str, list[str]] | None = None,
    ):
        super().__init__(name="SubmitPlan", description="提交整场评价、能力缺口和下一步行动计划。", expandable=False)
        self.topic_order = list(topic_ids)
        self.topic_ids = set(topic_ids)
        self.evidence_ids = evidence_ids
        self.topic_evidence_ids = topic_evidence_ids or {}
        self.last_submission: dict[str, Any] | None = None
        self.last_error = ""

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="plan_json",
                type="string",
                description=(
                    "完整成长计划 JSON 字符串。顶层必须包含 overallEvaluation、capabilityGaps、actionItems；"
                    "缺口 ID 使用 gap-1 格式；topicIds 和 evidenceIds 只能引用已审计报告中的真实 ID；"
                    "actionItems 必须包含 3 至 7 项，并用 order 从 1 开始连续编号；行动不绑定日期。"
                ),
                required=True,
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        try:
            if "plan_json" in parameters:
                payload = GrowthPlanSubmission.model_validate_json(str(parameters.get("plan_json", "{}")))
            else:
                payload = GrowthPlanSubmission.model_validate(self._flat_payload(parameters))
        except (ValidationError, ValueError) as exc:
            return self._reject(f"成长计划未通过 Schema：{exc}")
        if re.search(r"录用(?:概率|几率|可能性)|Offer\s*概率|胜算", payload.overall_evaluation.competitiveness, re.I):
            return self._reject("本场竞争力不能包含录用概率或胜算判断")
        evaluation_points = [*payload.overall_evaluation.strengths, *payload.overall_evaluation.risks]
        referenced_topics = {topic_id for item in evaluation_points for topic_id in item.topic_ids}
        referenced_topics.update(topic_id for gap in payload.capability_gaps for topic_id in gap.topic_ids)
        invalid_topics = sorted(referenced_topics - self.topic_ids)
        if invalid_topics:
            return self._reject(f"成长计划包含未知主题：{', '.join(invalid_topics)}")
        invalid_evidence = sorted({item for gap in payload.capability_gaps for item in gap.evidence_ids} - self.evidence_ids)
        if invalid_evidence:
            return self._reject(f"成长计划包含未知证据：{', '.join(invalid_evidence[:5])}")
        result = payload.model_dump(by_alias=True)
        result["actionItems"] = [
            {**item, "id": f"action-{item['order']}", "completed": False}
            for item in result["actionItems"]
        ]
        evaluation = result["overallEvaluation"]
        result["summary"] = evaluation["summary"]
        result["nextFocus"] = evaluation["nextFocus"]
        result["topRisks"] = [
            {"title": item["title"], "reason": item["impact"], "severity": item["priority"], "topicIds": item["topicIds"]}
            for item in result["capabilityGaps"][:3]
        ]
        self.last_submission = result
        self.last_error = ""
        action_count = len(result["actionItems"])
        return ToolResponse.success(text="下一步行动计划已通过结构校验", data={"accepted": True, "actionCount": action_count})

    @staticmethod
    def _flat_payload(parameters: dict[str, Any]) -> dict[str, Any]:
        actions = []
        for order in range(1, 8):
            raw = str(parameters.get(f"action_{order}") or parameters.get(f"day_{order}") or "").strip()
            if not raw:
                continue
            parts = [part.strip() for part in raw.split("|||")]
            if len(parts) == 8:
                title, description, action_type, gap_ids, dimension, priority, _legacy_deliverable, criterion = parts
            elif len(parts) == 7:
                title, description, action_type, gap_ids, dimension, priority, criterion = parts
            else:
                raise ValueError(f"action_{order} 必须包含标题、行动内容、类型、缺口、维度、优先级和完成标准")
            if not all((title, description, action_type, gap_ids, dimension, priority, criterion)):
                raise ValueError(f"action_{order} 的字段不能为空")
            actions.append({
                "order": len(actions) + 1,
                "title": title,
                "description": description,
                "type": action_type,
                "gapIds": [item.strip() for item in gap_ids.split(",") if item.strip()],
                "dimension": dimension,
                "priority": priority,
                "successCriterion": criterion,
            })
        if not 3 <= len(actions) <= 7:
            raise ValueError("下一步行动计划必须包含 3 至 7 项行动")

        def evaluation_points(name: str) -> list[dict[str, Any]]:
            result = []
            for line in str(parameters.get(name, "")).splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [part.strip() for part in line.split("|||", 1)]
                if len(parts) != 2 or not all(parts):
                    raise ValueError(f"{name} 每行必须包含内容和 topicId")
                result.append({"text": parts[0], "topicIds": [item.strip() for item in parts[1].split(",") if item.strip()]})
            return result

        gaps = []
        for line in str(parameters.get("gaps_text", "")).splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("|||", 9)]
            if len(parts) != 10 or not all(parts[:8]):
                raise ValueError("gaps_text 每行必须包含缺口 ID、类别、标题、说明、影响、优先级、topicId 和 evidenceId")
            gap_id, category, title, description, impact, priority, topic_ids, evidence_ids, learning, preparation = parts
            gaps.append({
                "id": gap_id, "category": category, "title": title, "description": description,
                "impact": impact, "priority": priority,
                "topicIds": [item.strip() for item in topic_ids.split(",") if item.strip()],
                "evidenceIds": [item.strip() for item in evidence_ids.split(",") if item.strip()],
                "learningItems": [item.strip() for item in learning.split(";") if item.strip()],
                "preparationItems": [item.strip() for item in preparation.split(";") if item.strip()],
            })

        return {
            "overallEvaluation": {
                "summary": str(parameters.get("summary", "")).strip(),
                "competitiveness": str(parameters.get("competitiveness", "")).strip(),
                "strengths": evaluation_points("strengths_text"),
                "risks": evaluation_points("risks_text"),
                "nextFocus": str(parameters.get("next_focus", "")).strip(),
            },
            "capabilityGaps": gaps,
            "actionItems": actions,
        }

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
    topic_ids = [item["id"] for item in draft]
    evidence_ids = {ref["id"] for item in draft for ref in item.get("evidenceRefs", []) if ref.get("id")}
    topic_evidence_ids = {
        item["id"]: [ref["id"] for ref in item.get("evidenceRefs", []) if ref.get("id")]
        for item in draft
    }
    submit = SubmitPlanTool(topic_ids, evidence_ids, topic_evidence_ids)
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
