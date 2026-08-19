from __future__ import annotations

import re
import uuid
from typing import Any

from backend.app.domain.scoring import aggregate_scores, normalize_scores
from backend.app.services.knowledge import KnowledgeBase
from backend.app.services.text_utils import repair_mojibake


QUESTION_TYPES = ("自我介绍", "项目经历", "技术知识", "行为面试", "业务理解", "职业规划", "反问环节", "其他")
QUESTION_TYPE_ALIASES = {
    "self_intro": "自我介绍",
    "self_introduction": "自我介绍",
    "personal_introduction": "自我介绍",
    "project": "项目经历",
    "project_experience": "项目经历",
    "experience": "项目经历",
    "technical": "技术知识",
    "technical_knowledge": "技术知识",
    "technical_question": "技术知识",
    "business": "业务理解",
    "business_understanding": "业务理解",
    "product_sense": "业务理解",
    "career": "职业规划",
    "career_planning": "职业规划",
    "reverse_question": "反问环节",
    "candidate_question": "反问环节",
    "other": "其他",
}

SELF_INTRO_PATTERN = re.compile(
    r"自我介绍|介绍(?:一下|下)?你自己|(?:简单|先)?介绍(?:一下|下)?自己|"
    r"(?:简单)?说(?:一下|下)?(?:你的)?(?:基本情况|个人背景)|"
    r"(?:做|作)(?:一下|下|个)?(?:个人|自我)介绍"
)


def split_segments(text: str, limit: int = 8) -> list[str]:
    segments = [re.sub(r"^[-*\d.、)）\s]+", "", item).strip() for item in re.split(r"[\r\n；;]+", text or "")]
    return list(dict.fromkeys(item for item in segments if len(item) >= 6))[:limit]


def infer_question_type(text: str) -> str:
    if SELF_INTRO_PATTERN.search(text): return "自我介绍"
    if re.search(r"有什么想问|还有什么问题|想了解我们|反问", text): return "反问环节"
    if re.search(r"冲突|失败|协作|分歧|压力|说服|推动.*分歧", text): return "行为面试"
    if re.search(r"指标|数据|算法|实验|测试|技术|架构|系统设计|性能|代码", text): return "技术知识"
    if re.search(r"职业规划|职业发展|离职|未来(?:三年|五年|职业)|为什么选择(?:我们|这家公司|该公司|这个岗位|该岗位|这个行业)", text): return "职业规划"
    if re.search(r"项目|经历|负责|挑战|成果", text): return "项目经历"
    if re.search(r"业务|用户|市场|产品|需求|商业", text): return "业务理解"
    return "其他"


def normalize_question_type(value: Any, text: str = "") -> str:
    raw = str(value or "").strip()
    if raw in QUESTION_TYPES:
        return raw
    key = re.sub(r"[\s-]+", "_", raw.lower())
    if key in QUESTION_TYPE_ALIASES:
        return QUESTION_TYPE_ALIASES[key]
    return infer_question_type(text)


def infer_topic_title(text: str, question_type: Any = "") -> str:
    kind = normalize_question_type(question_type, text)
    if kind == "自我介绍":
        return "个人背景与岗位契合"
    if kind == "行为面试":
        if re.search(r"分歧|冲突", text) and re.search(r"跨团队|研发|设计|协作|团队", text):
            return "跨团队分歧处理"
        if re.search(r"分歧|冲突", text):
            return "分歧与冲突处理"
        if re.search(r"失败|失误|挫折", text):
            return "失败复盘与改进"
        if re.search(r"压力|紧急|截止", text):
            return "压力与优先级管理"
        if re.search(r"说服|影响|推动|协作", text):
            return "沟通协作与推动"
        return "行为经历复盘"
    if kind == "技术知识":
        if re.search(r"实验|显著|样本量|分流", text):
            return "实验分析与决策"
        if re.search(r"数据|指标|漏斗", text):
            return "数据分析与指标"
        if re.search(r"架构|系统设计|性能", text):
            return "系统设计与架构"
        if re.search(r"算法|模型|代码", text):
            return "算法与技术原理"
        return "技术能力"
    if kind == "项目经历":
        if re.search(r"挑战|困难|复杂", text):
            return "挑战项目复盘"
        if re.search(r"成果|结果|提升|增长", text):
            return "项目成果与影响"
        return "项目职责与实践"
    if kind == "业务理解":
        if re.search(r"用户|需求", text):
            return "用户与需求洞察"
        if re.search(r"市场|竞品|商业", text):
            return "市场与商业判断"
        return "业务与产品理解"
    if kind == "职业规划":
        return "职业选择与规划"
    if kind == "反问环节":
        return "候选人反问"
    cleaned = re.sub(r"[？?。！!]", "", text).strip()
    return cleaned[:18] if cleaned else "其他问题"


class EvidenceReviewService:
    def __init__(self, knowledge: KnowledgeBase):
        self.knowledge = knowledge

    def analyze_materials(self, job_description: str, resume_text: str) -> dict[str, Any]:
        requirements = [
            {"id": f"req-{index}", "title": text[:28], "description": text, "priority": "core" if index <= 3 else "important", "category": self._category(text)}
            for index, text in enumerate(split_segments(job_description), 1)
        ]
        evidence = [
            {"id": f"resume-{index}", "title": text[:28], "description": text, "metrics": re.findall(r"\d+(?:\.\d+)?(?:%|万|亿|人|次|天|月|年)?", text), "tags": [self._category(text)]}
            for index, text in enumerate(split_segments(resume_text), 1)
        ]
        return {"jobRequirements": requirements, "resumeEvidence": evidence}

    def parse_transcript(self, transcript: str) -> list[dict[str, Any]]:
        transcript = repair_mojibake(transcript)
        lines = [line.strip() for line in (transcript or "").splitlines() if line.strip()]
        questions: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        timestamp = r"(?:(?:\[[^\]]+\]|(?:\d{1,2}:){1,2}\d{2})\s*)?"
        interviewer_pattern = re.compile(rf"^{timestamp}(?:面试官|采访者|interviewer|问|q)\s*[:：]\s*(.+)$", re.I)
        candidate_pattern = re.compile(rf"^{timestamp}(?:候选人|求职者|candidate|答|a)\s*[:：]\s*(.+)$", re.I)
        unlabeled_question_pattern = re.compile(r"^\[(?:录音约\s*)?[^\]]+\]\s*(.+)$", re.I)
        for line in lines:
            interviewer = interviewer_pattern.match(line)
            candidate = candidate_pattern.match(line)
            unlabeled = unlabeled_question_pattern.match(line) if not interviewer and not candidate else None
            if interviewer or (unlabeled and self._looks_like_question(unlabeled.group(1))):
                question = (interviewer or unlabeled).group(1).strip()
                if not self._looks_like_question(question):
                    continue
                if current and self._looks_like_follow_up(question):
                    current["followUpQuestions"].append(question)
                    continue
                if current:
                    questions.append(current)
                current = self._question(question, len(questions) + 1, "high")
            elif candidate and current:
                current["candidateAnswer"] += ("\n" if current["candidateAnswer"] else "") + candidate.group(1).strip()
            elif current:
                current["candidateAnswer"] += ("\n" if current["candidateAnswer"] else "") + line
        if current: questions.append(current)
        if questions:
            return questions[:40]

        sentences = [part.strip() for part in re.split(r"(?<=[？?。！!])|\r?\n", transcript or "") if part.strip()]
        current = None
        for sentence in sentences:
            if "?" in sentence or "？" in sentence or re.match(r"^(请|你|为什么|怎么|如何|介绍|讲一个|如果)", sentence):
                if current: questions.append(current)
                current = self._question(re.sub(r"^\S+[:：]\s*", "", sentence), len(questions) + 1, "low")
            elif current:
                current["candidateAnswer"] += sentence
        if current: questions.append(current)
        return questions[:40]

    def review(self, interview: dict[str, Any], questions: list[dict[str, Any]], enable_web_verify: bool = False) -> dict[str, Any]:
        materials = self.analyze_materials(interview.get("job_description", ""), interview.get("resume_text", ""))
        reviews = [self._review_question(interview, question, materials) for question in questions]
        overall = aggregate_scores([review["scores"] for review in reviews])
        ordered = sorted(reviews, key=lambda item: item["scores"]["overall"])
        risks = [{"questionId": item["id"], "title": item["interviewerQuestion"][:42], "reason": item["diagnosis"], "severity": item["priority"]["level"]} for item in ordered[:3]]
        actions = self._action_plan(ordered)
        summary = self._summary(overall, materials, enable_web_verify)
        gaps = self._capability_gaps(ordered)
        strongest = sorted(reviews, key=lambda item: item["scores"]["overall"], reverse=True)[:3]
        evaluation = {
            "summary": summary,
            "competitiveness": self._competitiveness(overall["overall"]),
            "strengths": [
                {"text": item["strengths"][0], "topicIds": [item["id"]]}
                for item in strongest if item.get("strengths")
            ],
            "risks": [
                {"text": item["weaknesses"][0], "topicIds": [item["id"]]}
                for item in ordered[:3] if item.get("weaknesses")
            ],
            "nextFocus": "优先补齐高优先级缺口，并按下一步行动计划改善关键回答。",
        }
        return {
            "reviews": reviews, "summary": summary, "overallScores": overall, "topRisks": risks,
            "overallEvaluation": evaluation, "capabilityGaps": gaps,
            "actionItems": actions, "nextFocus": evaluation["nextFocus"], "auditNotes": [],
        }

    def audit(self, interview: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
        notes: list[str] = []
        sources = {
            "transcript": interview.get("raw_transcript", ""),
            "job_description": interview.get("job_description", ""),
            "resume": interview.get("resume_text", ""),
        }
        for review in batch["reviews"]:
            valid_evidence = []
            for evidence in review.get("evidenceRefs", []):
                source = sources.get(evidence.get("sourceType", ""), "")
                evidence["verified"] = bool(evidence.get("quote") and evidence["quote"] in source)
                if evidence["verified"] or evidence.get("sourceType") == "knowledge":
                    valid_evidence.append(evidence)
                else:
                    notes.append(f"题目 {review['id']} 移除了一条无法回查的引用")
            review["evidenceRefs"] = valid_evidence
            valid_quotes = {item["quote"] for item in valid_evidence if item.get("quote")}
            for score_evidence in review.get("scoreEvidence", []):
                if score_evidence.get("quote") not in valid_quotes:
                    score_evidence["quote"] = ""
                    score_evidence["rationale"] += "（该维度缺少可直接回查的原文引用）"
            review["scores"] = normalize_scores(review.get("scores"))
        batch["overallScores"] = aggregate_scores([review["scores"] for review in batch["reviews"]])
        batch["auditNotes"] = notes or ["所有原文引用均已通过回查校验"]
        return batch

    def _review_question(self, interview: dict[str, Any], question: dict[str, Any], materials: dict[str, Any]) -> dict[str, Any]:
        answer = str(question.get("candidateAnswer", "")).strip()
        source_transcript = interview.get("raw_transcript", "")
        length_score = min(8.0, 3.5 + len(answer) / 90)
        has_metric = bool(re.search(r"\d", answer))
        has_structure = sum(bool(re.search(word, answer)) for word in ("首先", "其次", "最后", "背景", "目标", "结果")) >= 2
        requirements = materials["jobRequirements"]
        resume = materials["resumeEvidence"]
        role_match = self._best_match(answer + question.get("interviewerQuestion", ""), requirements)
        resume_match = self._best_match(answer, resume)
        scores = normalize_scores({
            "relevance": length_score + 0.8,
            "structure": length_score + (0.8 if has_structure else -0.4),
            "evidence": length_score + (1.0 if has_metric else -1.2),
            "depth": length_score + (0.4 if len(answer) >= 160 else -0.5),
            "roleFit": 5.0 if interview.get("analysis_mode") == "general" else length_score + (0.6 if role_match else -0.8),
        })
        root_answer = str(question.get("extractedAnswer") or question.get("candidateAnswer", "")).strip()
        quote = root_answer[: min(72, len(root_answer))]
        evidence_refs: list[dict[str, Any]] = []
        if quote:
            source_id = (question.get("answerSegmentIds") or [question["id"]])[0]
            evidence_refs.append(self._evidence("transcript", source_id, quote, source_transcript.find(quote), 1.0))
        follow_up_turns = []
        interviewer_signals = []
        for follow_up in question.get("followUpTurns", []):
            follow_up = dict(follow_up)
            follow_answer = str(follow_up.get("extractedAnswer") or follow_up.get("candidateAnswer", "")).strip()
            impact = self._follow_up_impact(root_answer, follow_answer)
            follow_up["followUpImpact"] = impact
            follow_up_turns.append(follow_up)
            follow_quote = follow_answer[: min(72, len(follow_answer))]
            if follow_quote:
                source_id = (follow_up.get("answerSegmentIds") or [follow_up["id"]])[0]
                ref = self._evidence("transcript", source_id, follow_quote, source_transcript.find(follow_quote), float(follow_up.get("confidence") != "low"))
                ref["title"] = f"追问影响：{impact}"
                evidence_refs.append(ref)
            question_quote = str(follow_up.get("interviewerQuestion") or "").strip()
            if question_quote:
                question_ref = self._evidence(
                    "transcript", (follow_up.get("questionSegmentIds") or [follow_up["id"]])[0],
                    question_quote, source_transcript.find(question_quote), float(follow_up.get("confidence") != "low"),
                )
                question_ref["title"] = "面试官追问信号"
                evidence_refs.append(question_ref)
                interviewer_signals.append({
                    "turnId": follow_up["id"], "type": self._signal_type(impact),
                    "interpretation": self._signal_interpretation(impact),
                    "confidence": "low" if not follow_up.get("questionSegmentIds") else "medium",
                    "evidenceIds": [question_ref["id"]],
                })
        if role_match:
            evidence_refs.append(self._evidence("job_description", role_match["id"], role_match["description"], interview.get("job_description", "").find(role_match["description"]), 0.95))
        if resume_match:
            evidence_refs.append(self._evidence("resume", resume_match["id"], resume_match["description"], interview.get("resume_text", "").find(resume_match["description"]), 0.95))
        knowledge_hits = self.knowledge.search(f"{question.get('questionType', '')} {question.get('interviewerQuestion', '')}")
        for hit in knowledge_hits[:2]:
            evidence_refs.append({"id": str(uuid.uuid4()), "sourceType": "knowledge", "sourceId": hit.id, "quote": hit.text[:180], "locator": hit.source, "verified": True, "confidence": hit.confidence, "title": hit.title, "url": ""})
        missing = []
        if not has_metric: missing.append("可验证的量化结果")
        if len(answer) < 140: missing.append("个人关键行动与决策过程")
        strengths = ["回答覆盖了问题主题"]
        if has_metric: strengths.append("提供了量化或可核验信息")
        if has_structure: strengths.append("表达具有清晰的先后结构")
        weaknesses = missing or ["可以进一步压缩背景并强化个人贡献"]
        diagnosis = "回答具备真实经历基础，但需要把个人判断、关键行动和结果因果关系说得更清楚。" if answer else "本题缺少有效回答，需要先补充真实经历，系统不会替你编造事实。"
        score_evidence = [
            {"dimension": "relevance", "score": scores["relevance"], "rationale": "回答是否直接覆盖问题核心。", "quote": quote, "evidenceIds": [evidence_refs[0]["id"]] if quote else []},
            {"dimension": "evidence", "score": scores["evidence"], "rationale": "检查是否包含可回查事实和量化结果。", "quote": quote if has_metric else "", "evidenceIds": [evidence_refs[0]["id"]] if quote and has_metric else []},
        ]
        risk_level = "high" if scores["overall"] < 5.5 else "medium" if scores["overall"] < 7.2 else "low"
        answer_evidence_ids = [item["id"] for item in evidence_refs if item["sourceType"] == "transcript" and item["quote"] and item["quote"] in root_answer][:1]
        claim_evidence_ids = answer_evidence_ids or [item["id"] for item in evidence_refs if item["sourceType"] == "transcript"][:1]
        answer_steps = [
            {"order": index, "label": "原回答片段", "content": item[:300], "evidenceIds": claim_evidence_ids}
            for index, item in enumerate(split_segments(answer, limit=4) or [answer or "未提供有效回答"], 1)
        ]
        framework = self._recommended_framework(question.get("questionType", ""), answer, claim_evidence_ids, missing)
        strength_claims = [{"text": item, "evidenceIds": claim_evidence_ids} for item in strengths if claim_evidence_ids]
        weakness_claims = [{"text": item, "evidenceIds": claim_evidence_ids} for item in weaknesses if claim_evidence_ids]
        recommended_answer = self._star_answer(answer, missing)
        return {
            **question,
            "followUpTurns": follow_up_turns,
            "scores": scores,
            "scoreEvidence": score_evidence,
            "evidenceRefs": evidence_refs,
            "diagnosis": diagnosis,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "strengthClaims": strength_claims,
            "weaknessClaims": weakness_claims,
            "answerLogic": {
                "summary": "本地规则按原回答顺序展示内容，不补写原文中不存在的逻辑。",
                "steps": answer_steps,
                "gaps": weakness_claims,
            },
            "interviewerSignals": interviewer_signals,
            "suggestedStructure": "先给结论，再按情境、任务、关键行动、结果和复盘展开。",
            "improvedAnswer": self._star_answer(answer, missing),
            "knowledgeToPrepare": ["项目核心指标口径", "个人决策与复盘案例"],
            "roleFitDiagnosis": {
                "matchedRequirementIds": [role_match["id"]] if role_match else [],
                "missingRequirementIds": [requirements[1]["id"]] if len(requirements) > 1 and not role_match else [],
                "resumeEvidenceIds": [resume_match["id"]] if resume_match else [],
                "riskLevel": risk_level if requirements else "unknown",
                "summary": "回答已找到岗位要求和简历证据的对应关系。" if role_match else "当前材料中没有找到足够强的岗位对应证据。",
            },
            "starRewrite": {
                "situation": answer or "[待补充：项目背景]",
                "task": "[待补充：你承担的明确目标]",
                "action": "[待补充：你的关键行动、判断和取舍]",
                "result": "原回答含有结果信息，请明确指标口径。" if has_metric else "[待补充：量化结果]",
                "fullAnswer": self._star_answer(answer, missing),
                "missingInformation": missing,
            },
            "recommendedAnswer": {
                "framework": framework,
                "fullAnswer": recommended_answer,
                "evidenceIds": claim_evidence_ids,
                "missingInformation": missing,
            },
            "priority": {"level": risk_level, "reason": "证据完整度与岗位映射共同决定本题复盘优先级。"},
        }

    @staticmethod
    def _follow_up_impact(root_answer: str, follow_answer: str) -> str:
        if not follow_answer or len(follow_answer) < 12:
            return "暴露回答不足"
        if re.search(r"(?:但是|并不是|相反|不一致|其实没有|前面说错)", follow_answer):
            return "存在前后矛盾"
        if re.search(r"\d|首先|其次|具体|例如|因为|指标|结果", follow_answer):
            return "补充有效证据"
        root_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", root_answer))
        follow_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", follow_answer))
        return "与主回答一致" if root_tokens & follow_tokens else "暴露回答不足"

    @staticmethod
    def _signal_type(impact: str) -> str:
        return {
            "补充有效证据": "request_detail", "暴露回答不足": "check_depth",
            "存在前后矛盾": "challenge_consistency", "与主回答一致": "unclear",
        }.get(impact, "unclear")

    @staticmethod
    def _signal_interpretation(impact: str) -> str:
        return {
            "补充有效证据": "追问要求候选人补充更具体的事实或结果。",
            "暴露回答不足": "追问后的回答仍不完整，可能需要补充关键过程。",
            "存在前后矛盾": "追问暴露了前后表述不一致，需要回查原始事实。",
            "与主回答一致": "追问延续了原回答，但没有形成新的明确判断。",
        }.get(impact, "当前材料不足以判断面试官意图。")

    @staticmethod
    def _recommended_framework(question_type: str, answer: str, evidence_ids: list[str], missing: list[str]) -> dict[str, Any]:
        kind = normalize_question_type(question_type)
        if kind in {"项目经历", "行为面试"}:
            framework_type, name = "STAR", "STAR"
            sections = [("S", "情境"), ("T", "任务"), ("A", "行动"), ("R", "结果")]
        elif kind == "业务理解":
            framework_type, name = "THREE_W", "3W"
            sections = [("WHY", "目标与原因"), ("WHAT", "方案内容"), ("HOW", "实施与验证")]
        elif kind in {"自我介绍", "职业规划"}:
            framework_type, name = "FIT_EVIDENCE_MOTIVATION", "结论—匹配点—证据—动机"
            sections = [("FIT", "匹配结论"), ("EVIDENCE", "经历证据"), ("MOTIVATION", "选择动机")]
        elif kind == "技术知识":
            framework_type, name = "PREP", "PREP"
            sections = [("P", "观点"), ("R", "理由"), ("E", "例证"), ("P2", "结论")]
        else:
            framework_type, name = "DIRECT", "直接结论—必要说明"
            sections = [("ANSWER", "直接回答"), ("DETAIL", "必要说明")]
        draft = answer[:600] if answer else "待补充：真实回答内容"
        return {
            "type": framework_type, "name": name,
            "reason": f"根据“{kind}”题型选择回答结构。",
            "sections": [
                {
                    "key": key, "label": label, "guidance": f"补充{label}部分并保持结论清晰。",
                    "draft": draft if index == 0 else f"待补充：{label}",
                    "evidenceIds": evidence_ids if index == 0 else [],
                }
                for index, (key, label) in enumerate(sections)
            ],
        }

    @staticmethod
    def _question(text: str, order: int, confidence: str) -> dict[str, Any]:
        return {"id": str(uuid.uuid4()), "order": order, "interviewerQuestion": text, "followUpQuestions": [], "candidateAnswer": "", "questionType": infer_question_type(text), "confidence": confidence, "initialDiagnosis": [], "confirmed": False, "version": 1}

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        if re.search(r"(?:不用作为正式问题|不用展开|我补充说明一下|稍微停一下|我看下时间|录音转写|只是确认|你有什么想问|候选人向面试官提问)", text):
            return False
        return bool(
            re.search(r"[?？]", text)
            or re.search(r"(?:请|介绍|讲讲|谈谈|说说|如何|怎么|为什么|哪些|什么|是否|能否|能不能|你会|你认为|你负责)", text)
        )

    @staticmethod
    def _looks_like_follow_up(text: str) -> bool:
        return bool(
            re.match(r"(?:如果.+发生冲突|如果业务方不同意|说具体一点|我打断一下|你刚才提到|刚才你提到|能再具体|请再具体)", text)
            or re.search(r"(?:不要只讲过程|怎么证明这不是偶然结果)", text)
        )

    @staticmethod
    def _evidence(source_type: str, source_id: str, quote: str, offset: int, confidence: float) -> dict[str, Any]:
        return {"id": str(uuid.uuid4()), "sourceType": source_type, "sourceId": source_id, "quote": quote, "locator": f"字符 {max(0, offset)}", "verified": offset >= 0, "confidence": confidence, "title": "", "url": ""}

    @staticmethod
    def _best_match(text: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", text.lower()))
        ranked = []
        for item in items:
            item_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", item.get("description", "").lower()))
            ranked.append((len(tokens & item_tokens), item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return ranked[0][1] if ranked and ranked[0][0] > 0 else None

    @staticmethod
    def _category(text: str) -> str:
        if re.search(r"数据|指标|分析|实验", text): return "数据分析"
        if re.search(r"用户|需求|产品", text): return "产品能力"
        if re.search(r"协作|沟通|推动", text): return "协作推动"
        return "经历证据"

    @staticmethod
    def _star_answer(answer: str, missing: list[str]) -> str:
        base = answer or "[待补充：真实经历和原始回答]"
        suffix = " ".join(f"[待补充：{item}]" for item in missing)
        return f"{base}\n建议按 STAR 重述：明确背景与目标，突出你的关键行动和取舍，最后说明结果与复盘。{suffix}".strip()

    @staticmethod
    def _action_plan(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [item["id"] for item in ordered]
        return [
            {"id": str(uuid.uuid4()), "order": 1, "title": "重写最低分问题的两分钟回答", "description": "补齐个人行动、取舍与量化结果，并计时复述。", "type": "preparation", "dimension": "structure", "priority": "high", "successCriterion": "在两分钟内讲清背景、个人行动、取舍和结果。", "practiceType": "answer_revision", "sourceQuestionIds": ids[:1], "completed": False},
            {"id": str(uuid.uuid4()), "order": 2, "title": "整理可复用的项目证据", "description": "核对指标口径、个人贡献和最终结果，并标记仍需补充的事实。", "type": "preparation", "dimension": "evidence", "priority": "medium", "successCriterion": "每条证据都能说明来源、个人贡献和可核查结果。", "practiceType": "evidence_collection", "sourceQuestionIds": ids[1:2], "completed": False},
            {"id": str(uuid.uuid4()), "order": 3, "title": "逐项映射岗位核心要求", "description": "为岗位核心要求选择真实案例，并练习说明匹配关系。", "type": "learning", "dimension": "roleFit", "priority": "medium", "successCriterion": "能用一分钟说明经历与岗位要求的对应关系。", "practiceType": "role_fit_preparation", "sourceQuestionIds": [], "completed": False},
        ]

    @staticmethod
    def _capability_gaps(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
        gaps = []
        for index, item in enumerate(ordered[:3], 1):
            evidence_ids = [
                evidence_id for score in item.get("scoreEvidence", [])
                for evidence_id in score.get("evidenceIds", [])
            ][:2]
            weakness = (item.get("weaknesses") or ["回答材料需要进一步补充"])[0]
            category = "case_material" if item.get("scores", {}).get("evidence", 0) < 6 else "soft_skill"
            gaps.append({
                "id": f"gap-{index}", "category": category,
                "title": weakness[:36], "description": weakness,
                "impact": item.get("diagnosis", "影响回答完整性和岗位匹配判断。"),
                "priority": "high" if item.get("scores", {}).get("overall", 0) < 6 else "medium",
                "topicIds": [item["id"]], "evidenceIds": list(dict.fromkeys(evidence_ids)),
                "learningItems": item.get("knowledgeToPrepare", [])[:2],
                "preparationItems": ["补充并重写该题的真实案例"],
            })
        return gaps or [{
            "id": "gap-1", "category": "case_material", "title": "回答案例准备",
            "description": "需要继续准备可回查的岗位相关案例。", "impact": "案例不足会限制回答的具体性。",
            "priority": "medium", "topicIds": [item["id"] for item in ordered[:1]], "evidenceIds": [],
            "learningItems": [], "preparationItems": ["整理一个完整项目案例"],
        }]

    @staticmethod
    def _competitiveness(score: float) -> str:
        if score >= 8.5:
            return "本场材料体现出较强岗位竞争力，但该判断不代表实际录用结果。"
        if score >= 7:
            return "本场材料体现出基础竞争力，仍需补强关键案例证据；该判断不代表实际录用结果。"
        if score >= 6:
            return "本场表现基本覆盖岗位要求，但差异化证据不足；该判断不代表实际录用结果。"
        return "本场材料暴露出较明显准备缺口，建议完成针对性训练后再评估；该判断不代表实际录用结果。"

    @staticmethod
    def _summary(scores: dict[str, float], materials: dict[str, Any], web: bool) -> str:
        weakest = min((name for name in scores if name != "overall"), key=lambda name: scores[name])
        weakest_label = {
            "relevance": "回答相关性", "structure": "表达结构", "evidence": "事实证据",
            "depth": "分析深度", "roleFit": "岗位匹配",
        }.get(weakest, weakest)
        web_note = "已按规则启用受限联网核验；联网信息不参与直接加分。" if web else "本次仅使用本地材料和知识库。"
        return f"本场综合得分 {scores['overall']:.1f}/10，当前最需要提升的维度是 {weakest_label}。系统已将结论绑定到原回答、JD 和简历证据。{web_note}"
