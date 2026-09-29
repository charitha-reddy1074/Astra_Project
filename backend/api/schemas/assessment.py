from datetime import datetime
from pydantic import BaseModel


# ── Core assessment schemas ───────────────────────────────────────────────────

class GenerationConfig(BaseModel):
    """How the AI should generate the questionnaire for an assessment.

    Chosen in the builder's "Generation Settings" section. `strategy` is the
    only lever that changes whether the (slow) LLM runs:
      - framework_only     : use the framework's imported questions only (no LLM)
      - framework_plus_ai  : keep framework questions AND append maturity-graded
                             diagnostic questions (LLM)
      - ai_rewrite         : replace framework questions with diagnostic ones (LLM)
    """
    strategy: str | None = None
    depth: str = "standard"                    # standard | detailed
    evidence_policy: str = "high_and_critical" # always | high_and_critical | optional
    custom_instructions: str | None = None
    recommendation_mode: str = "hybrid"        # framework | ai | hybrid


class AssessmentCreate(BaseModel):
    name: str
    description: str | None = None
    framework_ids: list[str]
    selected_domains: list[str] = []
    organization: str | None = None
    assigned_to: str | None = None
    # Direct market association (1:1 model — one assessment, one market)
    market_id: str | None = None
    market_label: str | None = None
    generation_config: GenerationConfig | None = None


class GenerateQuestionnaireIn(BaseModel):
    """Optional override sent with a generate-questionnaire call. When omitted,
    the assessment's stored generation_config (set at creation) is used."""
    generation_config: GenerationConfig | None = None


class AssessmentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class AssessmentAssign(BaseModel):
    assigned_to: str | None = None


class ResponseCreate(BaseModel):
    question_id: str
    control_id: str | None = None
    response_value: str | None = None
    notes: str | None = None


class ResponseOut(BaseModel):
    id: str
    assessment_id: str
    question_id: str
    control_id: str | None = None
    response_value: str | None = None
    notes: str | None = None
    score: float | None = None
    answered_at: datetime

    class Config:
        from_attributes = True


class EvidenceOut(BaseModel):
    id: str
    response_id: str
    assessment_id: str
    file_name: str
    file_type: str | None = None
    file_size: int | None = None
    description: str | None = None
    uploaded_at: datetime

    class Config:
        from_attributes = True


class FindingOut(BaseModel):
    id: str
    assessment_id: str
    control_id: str | None = None
    control_code: str | None = None
    framework_code: str | None = None
    domain_code: str | None = None
    title: str
    gap_description: str | None = None
    recommendation: str | None = None
    severity: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ScoreOut(BaseModel):
    id: str
    assessment_id: str
    framework_code: str | None = None
    domain_code: str | None = None
    control_code: str | None = None
    level: str
    score: float
    max_score: float
    percentage: float
    maturity_level: int
    answered_questions: int
    total_questions: int

    class Config:
        from_attributes = True


class QuestionnaireOut(BaseModel):
    id: str
    assessment_id: str
    question_ids: list
    total_questions: int
    answered_count: int
    generated_at: datetime

    class Config:
        from_attributes = True


class AssessmentSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    framework_ids: list
    status: str
    organization: str | None = None
    overall_score: float | None = None
    maturity_level: int | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    market_id: str | None = None
    market_label: str | None = None

    class Config:
        from_attributes = True


class AssessmentOut(AssessmentSummary):
    responses: list[ResponseOut] = []
    findings: list[FindingOut] = []
    scores: list[ScoreOut] = []
