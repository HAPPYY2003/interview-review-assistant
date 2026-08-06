from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal["DRAFT", "PARSING", "WAITING_CONFIRMATION", "REVIEWING", "AUDITING", "COMPLETED", "FAILED", "CANCELLED"]


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


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


class QuestionPatch(APIModel):
    questions: list[QuestionCard]


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


class ReviewBatch(APIModel):
    reviews: list[dict[str, Any]]
    summary: str
    top_risks: list[dict[str, Any]] = Field(default_factory=list, alias="topRisks")
    action_items: list[dict[str, Any]] = Field(default_factory=list, alias="actionItems")
    audit_notes: list[str] = Field(default_factory=list, alias="auditNotes")


class ReviewRunCreate(APIModel):
    enable_web_verify: bool = Field(default=False, alias="enableWebVerify")


class RunEvent(APIModel):
    id: int
    type: str
    data: dict[str, Any]
    created_at: str = Field(alias="createdAt")

