from datetime import datetime
from pydantic import BaseModel


class QuestionOut(BaseModel):
    id: str
    control_id: str
    text: str
    help_text: str | None = None
    question_type: str
    choices: list | None = None
    weight: float
    maturity_level: int | None = None
    expected_evidence_types: list | None = None
    is_required: bool
    order_index: int

    class Config:
        from_attributes = True


class ControlOut(BaseModel):
    id: str
    code: str
    name: str | None = None
    statement: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    weight: float
    criticality: str
    maturity_level: int | None = None
    cross_refs: list | None = None
    maturity_criteria: dict | None = None
    questions: list[QuestionOut] = []

    class Config:
        from_attributes = True


class CategoryOut(BaseModel):
    id: str
    code: str | None = None
    name: str | None = None
    criteria_statement: str | None = None
    description: str | None = None
    order_index: int
    controls: list[ControlOut] = []

    class Config:
        from_attributes = True


class DomainOut(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None
    order_index: int
    categories: list[CategoryOut] = []
    # Flat list of every control in the domain (back-compat; categories is the
    # canonical nesting framework → domain → category → control → question).
    controls: list[ControlOut] = []

    class Config:
        from_attributes = True


class FrameworkOut(BaseModel):
    id: str
    code: str
    name: str
    version: str
    description: str | None = None
    status: str
    total_domains: int
    total_categories: int = 0
    total_controls: int
    total_questions: int
    maturity_levels: dict | None = None
    created_at: datetime
    domains: list[DomainOut] = []

    class Config:
        from_attributes = True


class FrameworkSummary(BaseModel):
    id: str
    code: str
    name: str
    version: str
    status: str
    total_domains: int
    total_categories: int = 0
    total_controls: int
    total_questions: int
    created_at: datetime

    class Config:
        from_attributes = True
