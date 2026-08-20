from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RunStatus = Literal["DRAFT", "PARSING", "WAITING_CONFIRMATION", "REVIEWING", "AUDITING", "COMPLETED", "FAILED", "CANCELLED"]


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class StrictModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _normalize_string_list(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return value


def _normalize_joined_text(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "；".join(item.strip() for item in value if item.strip())
    return value


class InterviewCreate(APIModel):
    id: str | None = None
    company: str = ""
    position: str = ""
    round: str = ""
    interview_date: date | None = Field(default=None, alias="interviewDate")
    review_goal: str = Field(default="", alias="reviewGoal")
    analysis_mode: str = Field(default="full_context", alias="analysisMode")
    job_description: str = Field(default="", alias="jobDescription")
    resume_text: str = Field(default="", alias="resumeText")
    raw_transcript: str = Field(default="", alias="rawTranscript")


class MaterialText(APIModel):
    material_type: Literal["job_description", "resume", "transcript"]
    text: str
    filename: str | None = None


class QuestionCard(APIModel):
    id: str
    order: int = 1
    interviewer_question: str = Field(alias="interviewerQuestion")
    candidate_answer: str = Field(default="", alias="candidateAnswer")
    question_type: str = Field(default="其他", alias="questionType")
    confidence: Literal["high", "medium", "low"] = "medium"
    initial_diagnosis: list[str] = Field(default_factory=list, alias="initialDiagnosis")
    confirmed: bool = False
    version: int = 1
    topic_root_id: str | None = Field(default=None, alias="topicRootId")
    parent_question_id: str | None = Field(default=None, alias="parentQuestionId")
    turn_type: Literal["main", "follow_up"] = Field(default="main", alias="turnType")
    extracted_question: str = Field(default="", alias="extractedQuestion")
    extracted_answer: str = Field(default="", alias="extractedAnswer")
    edited_question: str = Field(default="", alias="editedQuestion")
    edited_answer: str = Field(default="", alias="editedAnswer")
    topic_title: str = Field(default="", alias="topicTitle")
    needs_confirmation: bool = Field(default=False, alias="needsConfirmation")
    provenance_status: Literal["source", "edited", "legacy", "conflict", "partial", "unverified", "fallback"] = Field(default="source", alias="provenanceStatus")
    follow_up_impact: str = Field(default="", alias="followUpImpact")
    question_segment_ids: list[str] = Field(default_factory=list, alias="questionSegmentIds")
    answer_segment_ids: list[str] = Field(default_factory=list, alias="answerSegmentIds")
    confidence_score: float = Field(default=75, ge=0, le=100, alias="confidenceScore")
    raw_confidence_score: float = Field(default=75, ge=0, le=100, alias="rawConfidenceScore")
    confidence_details: dict[str, Any] = Field(default_factory=dict, alias="confidenceDetails")
    confirmation_reasons: list[dict[str, Any]] = Field(default_factory=list, alias="confirmationReasons")
    parse_method: str = Field(default="legacy", alias="parseMethod")


class QuestionPatch(APIModel):
    questions: list[QuestionCard]


class ConfirmQuestionsRequest(APIModel):
    acknowledge_unresolved: bool = Field(default=False, alias="acknowledgeUnresolved")
    ignored_segment_ids: list[str] = Field(default_factory=list, alias="ignoredSegmentIds")


class TranscriptSegmentUpdate(APIModel):
    id: str
    speaker_role: Literal["interviewer", "candidate", "system_noise", "unknown"] | None = Field(default=None, alias="speakerRole")
    needs_confirmation: bool | None = Field(default=None, alias="needsConfirmation")
    excluded: bool | None = None


class TranscriptSegmentPatch(APIModel):
    segments: list[TranscriptSegmentUpdate]


class TranscriptSegmentSplitRequest(StrictModel):
    after_atom_id: str = Field(alias="afterAtomId")
    turn_id: str | None = Field(default=None, alias="turnId")
    left_assignment: Literal["question", "answer", "none"] | None = Field(default=None, alias="leftAssignment")
    right_assignment: Literal["question", "answer", "none"] | None = Field(default=None, alias="rightAssignment")


class TranscriptSegmentMergeRequest(StrictModel):
    segment_ids: list[str] = Field(min_length=2, alias="segmentIds")


class InterviewImport(APIModel):
    interview: InterviewCreate
    questions: list[QuestionCard]


class EvidenceRef(APIModel):
    id: str
    source_type: Literal["transcript", "job_description", "resume", "knowledge", "web"]
    source_id: str
    quote: str
    locator: str = ""
    verified: bool = False
    confidence: float = 0.0
    title: str = ""
    url: str = ""


class ScoreEvidence(APIModel):
    dimension: str
    score: float
    rationale: str
    quote: str = ""
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")


DimensionName = Literal["relevance", "structure", "evidence", "depth", "roleFit"]
ScoreLevel = Literal["优秀", "良好", "合格", "较弱", "缺失"]


class DimensionAssessment(StrictModel):
    dimension: DimensionName
    level: ScoreLevel
    rationale: str = Field(min_length=2, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")


class EvidenceClaim(StrictModel):
    text: str = Field(min_length=2, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")


class FollowUpAssessment(StrictModel):
    question_id: str = Field(alias="questionId")
    impact: Literal["补充有效证据", "暴露回答不足", "与主回答一致", "存在前后矛盾"]
    rationale: str = Field(min_length=2, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")


class RoleFitSubmission(StrictModel):
    summary: str = Field(min_length=2, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")
    missing_requirements: list[str] = Field(default_factory=list, alias="missingRequirements")
    uncertainty: str = ""

    @field_validator("missing_requirements", mode="before")
    @classmethod
    def normalize_missing_requirements(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class StarRewriteSubmission(StrictModel):
    situation: str
    task: str
    action: str
    result: str
    full_answer: str = Field(alias="fullAnswer")
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")
    missing_information: list[str] = Field(default_factory=list, alias="missingInformation")

    @field_validator("missing_information", mode="before")
    @classmethod
    def normalize_missing_information(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class AnswerLogicStep(StrictModel):
    order: int = Field(ge=1, le=12)
    label: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=2, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")


class AnswerLogicSubmission(StrictModel):
    summary: str = Field(min_length=2, max_length=600)
    steps: list[AnswerLogicStep] = Field(min_length=1, max_length=12)
    gaps: list[EvidenceClaim] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_step_order(self) -> "AnswerLogicSubmission":
        if [item.order for item in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("answerLogic.steps 必须从 1 开始连续编号")
        return self


class InterviewerSignalSubmission(StrictModel):
    turn_id: str = Field(alias="turnId")
    type: Literal[
        "request_detail", "verify_contribution", "verify_data", "check_depth",
        "challenge_consistency", "explicit_approval", "possible_topic_end", "unclear",
    ]
    interpretation: str = Field(min_length=2, max_length=500)
    confidence: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")


class FrameworkSectionSubmission(StrictModel):
    key: str = Field(min_length=1, max_length=30)
    label: str = Field(min_length=1, max_length=40)
    guidance: str = Field(min_length=2, max_length=400)
    draft: str = Field(min_length=2, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")


class AnswerFrameworkSubmission(StrictModel):
    type: Literal["STAR", "PREP", "THREE_W", "FIT_EVIDENCE_MOTIVATION", "DIRECT", "CUSTOM"]
    name: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=2, max_length=500)
    sections: list[FrameworkSectionSubmission] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def validate_custom_framework(self) -> "AnswerFrameworkSubmission":
        if self.type == "CUSTOM" and len(self.sections) < 2:
            raise ValueError("CUSTOM 框架至少需要两个结构段")
        return self


class RecommendedAnswerSubmission(StrictModel):
    framework: AnswerFrameworkSubmission
    full_answer: str = Field(min_length=2, max_length=4000, alias="fullAnswer")
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")
    missing_information: list[str] = Field(default_factory=list, max_length=10, alias="missingInformation")

    @field_validator("missing_information", mode="before")
    @classmethod
    def normalize_missing_information(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class TopicReviewSubmission(StrictModel):
    topic_id: str = Field(alias="topicId")
    topic_version: int = Field(ge=1, alias="topicVersion")
    diagnosis: str = Field(min_length=2, max_length=1200)
    dimensions: list[DimensionAssessment] = Field(min_length=5, max_length=5)
    strengths: list[EvidenceClaim] = Field(default_factory=list)
    weaknesses: list[EvidenceClaim] = Field(default_factory=list)
    answer_logic: AnswerLogicSubmission = Field(alias="answerLogic")
    interviewer_signals: list[InterviewerSignalSubmission] = Field(default_factory=list, max_length=12, alias="interviewerSignals")
    recommended_answer: RecommendedAnswerSubmission = Field(alias="recommendedAnswer")
    suggested_structure: str = Field(default="", alias="suggestedStructure", max_length=800)
    star_rewrite: StarRewriteSubmission | None = Field(default=None, alias="starRewrite")
    knowledge_to_prepare: list[str] = Field(default_factory=list, alias="knowledgeToPrepare")
    role_fit: RoleFitSubmission = Field(alias="roleFit")
    follow_up_assessments: list[FollowUpAssessment] = Field(default_factory=list, alias="followUpAssessments")
    uncertainties: list[str] = Field(default_factory=list)
    revision_summary: str = Field(default="", alias="revisionSummary", max_length=500)

    @field_validator("suggested_structure", mode="before")
    @classmethod
    def normalize_suggested_structure(cls, value: Any) -> Any:
        return _normalize_joined_text(value)

    @field_validator("knowledge_to_prepare", "uncertainties", mode="before")
    @classmethod
    def normalize_legacy_string_lists(cls, value: Any) -> Any:
        return _normalize_string_list(value)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "TopicReviewSubmission":
        required = {"relevance", "structure", "evidence", "depth", "roleFit"}
        actual = {item.dimension for item in self.dimensions}
        if actual != required:
            raise ValueError("dimensions 必须且只能包含五个评分维度")
        return self


class AuditFinding(StrictModel):
    topic_id: str = Field(alias="topicId")
    code: Literal[
        "invalid_reference", "unsupported_claim", "score_conflict", "missing_follow_up",
        "contradiction", "invented_rewrite", "incomplete_logic", "invalid_signal",
        "unsuitable_framework", "other",
    ]
    severity: Literal["critical", "warning"]
    field: str = ""
    message: str = Field(min_length=2, max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")


class AuditSubmission(StrictModel):
    decision: Literal["pass", "revise"]
    findings: list[AuditFinding] = Field(default_factory=list)
    summary: str = Field(min_length=2, max_length=800)

    @model_validator(mode="after")
    def validate_decision(self) -> "AuditSubmission":
        if self.decision == "revise" and not self.findings:
            raise ValueError("revise 必须至少包含一条审计发现")
        if self.decision == "pass" and any(item.severity == "critical" for item in self.findings):
            raise ValueError("存在 critical 问题时不能通过审计")
        return self


class RiskSubmission(StrictModel):
    title: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=2, max_length=500)
    severity: Literal["high", "medium"]
    topic_ids: list[str] = Field(min_length=1, alias="topicIds")


class EvaluationPointSubmission(StrictModel):
    text: str = Field(min_length=2, max_length=400)
    topic_ids: list[str] = Field(min_length=1, alias="topicIds")


class OverallEvaluationSubmission(StrictModel):
    summary: str = Field(min_length=2, max_length=1200)
    strengths: list[EvaluationPointSubmission] = Field(default_factory=list, max_length=3)
    risks: list[EvaluationPointSubmission] = Field(default_factory=list, max_length=3)
    next_focus: str = Field(min_length=2, max_length=500, alias="nextFocus")


class CapabilityGapSubmission(StrictModel):
    id: str = Field(pattern=r"^gap-[A-Za-z0-9_-]+$")
    category: Literal["hard_skill", "soft_skill", "domain_knowledge", "method_tool", "case_material"]
    title: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=2, max_length=600)
    impact: str = Field(min_length=2, max_length=500)
    priority: Literal["high", "medium"]
    topic_ids: list[str] = Field(min_length=1, alias="topicIds")
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")
    learning_items: list[str] = Field(default_factory=list, max_length=5, alias="learningItems")
    preparation_items: list[str] = Field(default_factory=list, max_length=5, alias="preparationItems")

    @model_validator(mode="after")
    def validate_actions(self) -> "CapabilityGapSubmission":
        if not self.learning_items and not self.preparation_items:
            raise ValueError("能力缺口至少需要一个学习项或准备项")
        return self


class ActionItemSubmission(StrictModel):
    order: int = Field(ge=1, le=7)
    title: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=2, max_length=600)
    type: Literal["learning", "preparation"]
    gap_ids: list[str] = Field(min_length=1, alias="gapIds")
    dimension: DimensionName
    priority: Literal["high", "medium"]
    success_criterion: str = Field(min_length=2, max_length=300, alias="successCriterion")

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_action_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "order" not in normalized and "day" in normalized:
            normalized["order"] = normalized["day"]
        normalized.pop("day", None)
        normalized.pop("deliverable", None)
        return normalized


class GrowthPlanSubmission(StrictModel):
    overall_evaluation: OverallEvaluationSubmission = Field(alias="overallEvaluation")
    capability_gaps: list[CapabilityGapSubmission] = Field(min_length=1, max_length=5, alias="capabilityGaps")
    action_items: list[ActionItemSubmission] = Field(min_length=3, max_length=7, alias="actionItems")

    @model_validator(mode="after")
    def validate_actions(self) -> "GrowthPlanSubmission":
        expected_order = set(range(1, len(self.action_items) + 1))
        if {item.order for item in self.action_items} != expected_order:
            raise ValueError("行动项 order 必须从 1 开始连续编号且不能重复")
        gap_ids = [item.id for item in self.capability_gaps]
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("capabilityGaps.id 不能重复")
        known_gaps = set(gap_ids)
        referenced = {gap_id for item in self.action_items for gap_id in item.gap_ids}
        if referenced - known_gaps:
            raise ValueError("actionItems 包含未知 gapId")
        high_priority = {item.id for item in self.capability_gaps if item.priority == "high"}
        if high_priority - referenced:
            raise ValueError("每个高优先级缺口至少需要一个行动项")
        return self


class GrowthActionProgressPatch(APIModel):
    run_id: str = Field(min_length=1, alias="runId")
    status: Literal["pending", "in_progress", "completed", "skipped"] | None = None
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    user_note: str | None = Field(default=None, max_length=2000, alias="userNote")
    completion_evidence: str | None = Field(default=None, max_length=4000, alias="completionEvidence")
    self_rating: int | None = Field(default=None, ge=1, le=5, alias="selfRating")

    @model_validator(mode="after")
    def validate_patch(self) -> "GrowthActionProgressPatch":
        editable = {
            "status", "started_at", "completed_at", "user_note",
            "completion_evidence", "self_rating",
        }
        if not (self.model_fields_set & editable):
            raise ValueError("至少需要提交一个行动进度字段")
        return self


class GrowthAuditFinding(StrictModel):
    target_type: Literal["overall_evaluation", "capability_gap", "action_item"] = Field(alias="targetType")
    target_id: str = Field(min_length=1, max_length=100, alias="targetId")
    code: Literal[
        "unsupported_overall_evaluation", "overall_score_conflict", "unsupported_gap",
        "gap_priority_mismatch", "action_gap_mismatch", "duplicate_action",
        "action_not_executable", "criterion_not_verifiable", "learning_preparation_mismatch",
        "invented_claim", "prohibited_probability", "invalid_reference",
        "high_priority_gap_uncovered", "other",
    ]
    severity: Literal["critical", "warning"]
    field: str = Field(default="", max_length=200)
    message: str = Field(min_length=2, max_length=600)
    topic_ids: list[str] = Field(default_factory=list, alias="topicIds")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")


class GrowthAuditSubmission(StrictModel):
    decision: Literal["pass", "revise"]
    summary: str = Field(min_length=2, max_length=800)
    findings: list[GrowthAuditFinding] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_decision(self) -> "GrowthAuditSubmission":
        if self.decision == "revise" and not self.findings:
            raise ValueError("revise 必须至少包含一条成长计划审计发现")
        if self.decision == "pass" and any(item.severity == "critical" for item in self.findings):
            raise ValueError("存在 critical 问题时不能通过成长计划终审")
        return self


class ReviewBatch(StrictModel):
    reviews: list[TopicReviewSubmission]
    summary: str
    top_risks: list[RiskSubmission] = Field(default_factory=list, alias="topRisks")
    action_items: list[ActionItemSubmission] = Field(default_factory=list, alias="actionItems")
    audit_notes: list[str] = Field(default_factory=list, alias="auditNotes")


class ReviewRunCreate(APIModel):
    enable_web_verify: bool = Field(default=False, alias="enableWebVerify")
    review_mode: Literal["full", "quick"] = Field(default="full", alias="reviewMode")
    acknowledge_unreviewed: bool = Field(default=False, alias="acknowledgeUnreviewed")
    acknowledge_unresolved: bool = Field(default=False, alias="acknowledgeUnresolved")


class GrowthSnapshotDeleteBatch(APIModel):
    snapshot_ids: list[str] = Field(min_length=1, max_length=1000, alias="snapshotIds")


class GrowthSnapshotImportBatch(APIModel):
    interview_ids: list[str] = Field(min_length=1, max_length=1000, alias="interviewIds")


class RunEvent(APIModel):
    id: int
    type: str
    data: dict[str, Any]
    created_at: str = Field(alias="createdAt")
