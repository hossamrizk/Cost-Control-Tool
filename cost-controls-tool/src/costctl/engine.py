"""Pipeline orchestration.

Order matters and is fixed: ingest -> normalize -> deterministic rules ->
validate -> summarize -> (optional) AI interpretation. The AI step is last and
optional by design; removing it changes no figure in the output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import ENGINE_VERSION, RULESET_VERSION
from .ai import PROMPT_VERSION, Interpreter
from .audit import log_event
from .ingest import Table, load_dataset
from .model import CostModel, build_model
from .models import Finding, Status
from .rules import ALL_RULES, Thresholds, run_all
from .summary import ExecutiveSummary, build_summary, render_markdown

RULE_ORDER = {rule_id: index for index, (rule_id, _) in enumerate(ALL_RULES)}
SCHEMA_VERSION = "findings-1.0"


class ValidationError(RuntimeError):
    pass


@dataclass
class RunResult:
    run_id: str
    started_at: str
    model: CostModel
    findings: list[Finding]
    summary: ExecutiveSummary
    tables: dict[str, Table]
    thresholds: Thresholds
    ai_provider: str = "not_run"
    ai_model: str = ""
    warnings: list[str] = field(default_factory=list)

    def provenance(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "engine_version": ENGINE_VERSION,
            "ruleset_version": RULESET_VERSION,
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "thresholds": {
                "forecast_movement_absolute": str(self.thresholds.movement_abs),
                "forecast_movement_percent_of_budget": str(self.thresholds.movement_pct),
                "materiality_percent_of_project_budget": str(self.thresholds.materiality_pct),
                "rounding_tolerance": str(self.thresholds.tolerance),
            },
            "inputs": [
                {"name": name, "file": table.path.name, "sha256": table.sha256,
                 "rows": len(table)}
                for name, table in sorted(self.tables.items())
            ],
        }

    def to_document(self) -> dict:
        return {
            "provenance": self.provenance(),
            "executive_summary": _summary_json(self.summary),
            "findings": [f.to_json() for f in self.findings],
        }


def _summary_json(summary: ExecutiveSummary) -> dict:
    return {
        "project": summary.project,
        "period": summary.period,
        "current_budget": str(summary.total_budget),
        "commitments": str(summary.total_commitments),
        "actual_cost": str(summary.total_actual),
        "forecast_to_complete": str(summary.total_ftc),
        "reported_eac": str(summary.reported_eac),
        "calculated_eac": str(summary.calculated_eac),
        "reported_vac": str(summary.reported_vac),
        "calculated_vac": str(summary.calculated_vac),
        "excluded_change_exposure": str(summary.excluded_change_exposure),
        "adjusted_vac": str(summary.adjusted_vac),
        "adjusted_vac_percent_of_budget": str(summary.adjusted_vac_pct),
        "unapproved_change_in_forecast": str(summary.unapproved_change_in_forecast),
        "unfunded_commitments": str(summary.unfunded_commitments),
        "contingency": {
            "opening_balance": str(summary.contingency_opening),
            "approved_usage": str(summary.contingency_approved_usage),
            "pending_usage": str(summary.contingency_pending_usage),
            "remaining_approved_basis": str(summary.contingency_remaining_approved_basis),
            "reported_remaining": str(summary.contingency_reported_remaining),
        },
        "net_headroom": str(summary.net_headroom),
        "uncommitted_budget": str(summary.uncommitted_budget),
        "bridge": [{"label": s.label,
                    "movement": str(s.amount) if s.amount is not None else None,
                    "position": str(s.running), "basis": s.basis}
                   for s in summary.bridge],
        "watchlist": [{**e, "exposure": str(e["exposure"]),
                       "worst_severity": e["worst_severity"].value}
                      for e in summary.watchlist],
        "counts": summary.counts,
    }


def _assign_ids(findings: list[Finding]) -> list[Finding]:
    """Stable, deterministic identifiers: the same inputs always give F-001..F-0nn
    for the same findings, which is what makes the JSON diffable run to run."""
    def sort_key(f: Finding):
        try:
            code = int(f.cost_code)
        except ValueError:
            code = 10**9          # PROJECT-level findings sort last within a rule
        return (RULE_ORDER.get(f.rule_id, 99), code, f.source_reference)

    ordered = sorted(findings, key=sort_key)
    for index, finding in enumerate(ordered, start=1):
        finding.finding_id = f"F-{index:03d}"
    return ordered


def validate(findings: list[Finding]) -> None:
    """BR-11 and BR-12 are enforced here, not requested politely in a prompt."""
    for finding in findings:
        if not finding.source_file or not finding.source_reference:
            raise ValidationError(
                f"BR-11 violation: {finding.finding_id or finding.rule_id} has no source "
                f"file or source reference")
        if finding.status is not Status.DRAFT:
            raise ValidationError(
                f"BR-12 violation: {finding.finding_id} was created with status "
                f"{finding.status.value}; findings must be created as Draft")


def run(data_dir: str | Path = "data", *, thresholds: Thresholds | None = None,
        interpreter: Interpreter | None = None, log: bool = True,
        log_path=None) -> RunResult:
    started = datetime.now(timezone.utc)
    thresholds = thresholds or Thresholds()

    tables = load_dataset(data_dir)
    fingerprint = hashlib.sha256(
        "".join(t.sha256 for _, t in sorted(tables.items())).encode()).hexdigest()[:8]
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{fingerprint}"

    model = build_model(tables)
    findings = _assign_ids(run_all(model, thresholds))
    validate(findings)
    summary = build_summary(model, findings)

    result = RunResult(run_id=run_id, started_at=started.isoformat(timespec="seconds"),
                       model=model, findings=findings, summary=summary, tables=tables,
                       thresholds=thresholds)

    if interpreter is not None:
        result.ai_provider = interpreter.provider
        result.ai_model = interpreter.model
        for finding in findings:
            finding.ai = interpreter.interpret(finding)
        blocked = [f.finding_id for f in findings if f.ai.guardrail == "blocked_numeric"]
        errored = [f.finding_id for f in findings if f.ai.guardrail == "error"]
        if blocked:
            result.warnings.append(
                f"numeric guardrail blocked AI text on {len(blocked)} finding(s): "
                f"{', '.join(blocked)}")
        if errored:
            result.warnings.append(
                f"AI interpretation failed on {len(errored)} finding(s): "
                f"{', '.join(errored)}")

    if log:
        kwargs = {"run_id": run_id}
        if log_path is not None:
            kwargs["path"] = log_path
        log_event("analysis_run", {
            "inputs": [{"file": t.path.name, "sha256": t.sha256} for t in tables.values()],
            "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION,
            "findings": len(findings), "ai_provider": result.ai_provider,
            "warnings": result.warnings,
        }, **kwargs)
    return result


def write_outputs(result: RunResult, out_dir: str | Path = "out") -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    findings_path = out_dir / "findings.json"
    summary_path = out_dir / "executive_summary.md"
    findings_path.write_text(
        json.dumps(result.to_document(), indent=2), encoding="utf-8")
    summary_path.write_text(
        render_markdown(result.summary, result.findings), encoding="utf-8")
    return {"findings": findings_path, "summary": summary_path}
