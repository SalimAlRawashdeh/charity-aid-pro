from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Input ────────────────────────────────────────────────────────────────────

class GrantType(str, Enum):
    grant = "grant"
    trust = "trust"
    lottery = "lottery"
    corporate = "corporate"
    government = "government"


class Relationship(str, Enum):
    new = "new"
    previously_applied = "previously-applied"
    existing_funder = "existing-funder"
    re_eligible = "re-eligible"


class Duration(str, Enum):
    single_year = "single-year"
    multi_year = "multi-year"


class Status(str, Enum):
    identified = "identified"
    researching = "researching"
    applying = "applying"
    submitted = "submitted"
    awarded = "awarded"
    rejected = "rejected"


class OpportunityInput(BaseModel):
    id: str
    funderName: str
    programName: str
    amount: float
    amountMax: float | None = None
    type: GrantType
    deadline: str  # ISO date string or "unknown"
    location: str
    duration: Duration
    durationMonths: int = 12
    relationship: Relationship
    status: Status
    score: float = 0
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    eligibility: str = ""
    notes: str = ""
    website: str = ""
    contactName: str | None = None
    contactEmail: str | None = None
    source: str = ""
    extractionConfidence: float = 0.0


# ── Scoring sub-schemas ─────────────────────────────────────────────────────

class GatingCheck(BaseModel):
    pass_: bool = Field(alias="pass")

    model_config = {"populate_by_name": True}


class ExtractionConfidenceGate(GatingCheck):
    value: float


class EligibilityGate(GatingCheck):
    confidence: float
    reasoning: str


class GeographyGate(GatingCheck):
    location: str
    specificity: str | None = None


class ReapplicationGate(GatingCheck):
    relationship: str


class GatingResult(BaseModel):
    status: str  # "passed" | "failed" | "needs_review"
    extraction_confidence: ExtractionConfidenceGate
    eligibility: EligibilityGate
    geography: GeographyGate
    reapplication: ReapplicationGate


class StrategicFitScore(BaseModel):
    raw: float
    geography_modifier: float
    final: float
    reasoning: str


class FundingValueScore(BaseModel):
    score: int
    amount_used: float


class ReasonedScore(BaseModel):
    score: float
    reasoning: str


class ScoresResult(BaseModel):
    strategic_fit: StrategicFitScore
    funding_value: FundingValueScore
    probability: ReasonedScore
    effort: ReasonedScore
    strategic_value: ReasonedScore


class TimingResult(BaseModel):
    score: int | None
    days_to_deadline: int | None


# ── Output (input opportunity + scoring appended) ───────────────────────────

class ScoredOpportunity(OpportunityInput):
    gating: GatingResult
    scores: ScoresResult | None = None
    timing: TimingResult | None = None
    final_score: float | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    scored_at: datetime
