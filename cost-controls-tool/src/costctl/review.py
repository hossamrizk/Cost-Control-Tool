from dataclasses import dataclass
from datetime import datetime, timezone

from .audit import log_event
from .models import VALID_TRANSITIONS, Finding, Status


class InvalidTransition(ValueError):
    pass


@dataclass
class ReviewRecord:
    finding_id: str
    from_status: Status
    to_status: Status
    reviewer: str
    note: str
    timestamp: str


def can_transition(current: Status, target: Status) -> bool:
    return target in VALID_TRANSITIONS[current]


def set_status(finding: Finding, target: Status, *, reviewer: str, note: str = "",
               run_id: str = "", log_path=None) -> ReviewRecord:
    if not reviewer or not reviewer.strip():
        raise InvalidTransition("a reviewer identity is required to change status")
    if not can_transition(finding.status, target):
        raise InvalidTransition(
            f"{finding.status.value} -> {target.value} is not a permitted transition "
            f"(allowed: {', '.join(s.value for s in VALID_TRANSITIONS[finding.status]) or 'none'})")

    record = ReviewRecord(
        finding_id=finding.finding_id, from_status=finding.status, to_status=target,
        reviewer=reviewer.strip(), note=note.strip(),
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    finding.status = target

    payload = {"finding_id": record.finding_id, "from": record.from_status.value,
               "to": record.to_status.value, "reviewer": record.reviewer,
               "note": record.note}
    if log_path is None:
        log_event("status_change", payload, run_id=run_id, actor=record.reviewer)
    else:
        log_event("status_change", payload, run_id=run_id, actor=record.reviewer,
                  path=log_path)
    return record


def all_reviewed(findings: list[Finding]) -> bool:
    return all(f.status is not Status.DRAFT for f in findings)
