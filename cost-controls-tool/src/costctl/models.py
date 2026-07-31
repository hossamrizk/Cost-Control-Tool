"""Findings schema.

The field list is fixed by the assessment's required output structure. Two
fields go beyond it deliberately:

  finding_type  - satisfies BR-14 by separating a confirmed arithmetic error
                  from an item that merely requires explanation.
  ai            - the AI interpretation block, kept structurally separate from
                  the deterministic figures so that a reviewer can always see
                  which numbers were computed and which words were generated.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class Status(str, Enum):
    """BR-12: findings are born Draft."""
    DRAFT = "Draft"
    REVIEWED = "Reviewed"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    CLOSED = "Closed"


class FindingType(str, Enum):
    """BR-14: confirmed errors are not the same thing as open questions."""
    CONFIRMED_ERROR = "Confirmed error"
    REQUIRES_EXPLANATION = "Requires explanation"


class Category(str, Enum):
    CALCULATION_ERROR = "Calculation error"
    RECONCILIATION_BREAK = "Reconciliation break"
    UNFUNDED_COMMITMENT = "Unfunded commitment"
    FORECAST_MOVEMENT = "Forecast movement"
    FORECAST_INTEGRITY = "Forecast integrity"
    EXCLUDED_CHANGE_EXPOSURE = "Excluded change exposure"
    UNAPPROVED_CHANGE_IN_FORECAST = "Unapproved change in forecast"
    CHANGE_NOT_REFLECTED = "Change not reflected in budget"
    DUPLICATE_RECORD = "Duplicate record"
    CONTINGENCY_CONTROL = "Contingency control"
    DATA_QUALITY = "Data quality"


VALID_TRANSITIONS: dict[Status, set[Status]] = {
    Status.DRAFT: {Status.REVIEWED, Status.REJECTED},
    Status.REVIEWED: {Status.ACCEPTED, Status.REJECTED, Status.DRAFT},
    Status.ACCEPTED: {Status.CLOSED, Status.REVIEWED},
    Status.REJECTED: {Status.DRAFT, Status.CLOSED},
    Status.CLOSED: set(),
}


@dataclass
class AIInterpretation:
    """Words, not arithmetic. Produced by the LLM under schema constraint."""
    explanation: str = ""
    recommended_review: str = ""
    recommended_action: str = ""
    proposed_severity: Optional[str] = None
    proposed_confidence: Optional[int] = None
    severity_rationale: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    guardrail: str = "not_run"       # passed | blocked_numeric | error | not_run
    guardrail_detail: str = ""


@dataclass
class Finding:
    finding_id: str
    rule_id: str
    project: str
    cost_code: str
    package: str
    finding_category: Category
    finding_type: FindingType
    finding_description: str
    reported_value: Optional[Decimal]
    calculated_value: Optional[Decimal]
    difference: Optional[Decimal]
    potential_exposure: Decimal
    severity: Severity
    confidence: int
    source_file: str                  # BR-11
    source_reference: str             # BR-11
    recommended_review: str
    recommended_action: str
    status: Status = Status.DRAFT      # BR-12
    evidence: dict = field(default_factory=dict)
    ai: AIInterpretation = field(default_factory=AIInterpretation)

    def to_json(self) -> dict:
        def conv(v):
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, Enum):
                return v.value
            if dataclasses.is_dataclass(v):
                return {k: conv(x) for k, x in dataclasses.asdict(v).items()}
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v

        return {k: conv(v) for k, v in dataclasses.asdict(self).items()}
