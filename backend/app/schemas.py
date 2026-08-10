from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RunStatus = Literal["DRAFT", "PARSING", "WAITING_CONFIRMATION", "REVIEWING", "AUDITING", "COMPLETED", "FAILED", "CANCELLED"]


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class StrictModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


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
    provenance_status: Literal["source", "edited", "legacy"] = Field(default="source", alias="provenanceStatus")
    follow_up_impact: str = Field(default="", alias="followUpImpact")
    question_segment_ids: list[str] = Field(default_factory=list, alias="questionSegmentIds")
    answer_segment_ids: list[str] = Field(default_factory=list, alias="answerSegmentIds")


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


class StarRewriteSubmission(StrictModel):
    situation: str
    task: str
    action: str
    result: str
    full_answer: str = Field(alias="fullAnswer")
    evidence_ids: list[str] = Field(min_length=1, alias="evidenceIds")
    missing_information: list[str] = Field(default_factory=list, alias="missingInformation")


class TopicReviewSubmission(StrictModel):
    topic_id: str = Field(alias="topicId")
    topic_version: int = Field(ge=1, alias="topicVersion")
    diagnosis: str = Field(min_length=2, max_length=1200)
    dimensions: list[DimensionAssessment] = Field(min_length=5, max_length=5)
    strengths: list[EvidenceClaim] = Field(default_factory=list)
    weaknesses: list[EvidenceClaim] = Field(default_factory=list)
    suggested_structure: str = Field(default="", alias="suggestedStructure", max_length=800)
    star_rewrite: StarRewriteSubmission = Field(alias="starRewrite")
    knowledge_to_prepare: list[str] = Field(default_factory=list, alias="knowledgeToPrepare")
    role_fit: RoleFitSubmission = Field(alias="roleFit")
    follow_up_assessments: list[FollowUpAssessment] = Field(default_factory=list, alias="followUpAssessments")
    uncertainties: list[str] = Field(default_factory=list)
    revision_summary: str = Field(default="", alias="revisionSummary", max_length=500)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "TopicReviewSubmission":
        required = {"relevance", "structure", "evidence", "depth", "roleFit"}
        actual = {item.dimension for item in self.dimensions}
        if actual != required:
            raise ValueError("dimensions 必须且只能包含五个评分维度")
        return self


class AuditFinding(StrictModel):
    topic_id: str = Field(alias="topicId")
    code: Literal["invalid_reference", "unsupported_claim", "score_conflict", "missing_follow_up", "contradiction", "invented_rewrite", "other"]
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


class ActionItemSubmission(StrictModel):
    day: int = Field(ge=1, le=7)
    title: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=2, max_length=600)
    dimension: DimensionName
    priority: Literal["high", "medium"]
    success_criterion: str = Field(min_length=2, max_length=300, alias="successCriterion")


class GrowthPlanSubmission(StrictModel):
    summary: str = Field(min_length=2, max_length=1200)
    top_risks: list[RiskSubmission] = Field(default_factory=list, max_length=3, alias="topRisks")
    next_focus: str = Field(min_length=2, max_length=500, alias="nextFocus")
    action_items: list[ActionItemSubmission] = Field(min_length=7, max_length=7, alias="actionItems")

    @model_validator(mode="after")
    def validate_days(self) -> "GrowthPlanSubmission":
        if {item.day for item in self.action_items} != set(range(1, 8)):
            raise ValueError("七天计划必须完整包含第 1 到第 7 天")
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


class RunEvent(APIModel):
    id: int
    type: str
    data: dict[str, Any]
    created_at: str = Field(alias="createdAt")
