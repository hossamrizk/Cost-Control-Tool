from dataclasses import dataclass, field
from decimal import Decimal

from .model import CostModel
from .models import Finding, FindingType, Severity
from .money import ZERO, fmt


@dataclass
class BridgeStep:
    label: str
    amount: Decimal | None
    running: Decimal
    basis: str


@dataclass
class ExecutiveSummary:
    project: str
    period: str
    total_budget: Decimal
    total_commitments: Decimal
    total_actual: Decimal
    total_ftc: Decimal
    reported_eac: Decimal
    calculated_eac: Decimal
    reported_vac: Decimal
    calculated_vac: Decimal
    excluded_change_exposure: Decimal
    adjusted_vac: Decimal
    unapproved_change_in_forecast: Decimal
    unfunded_commitments: Decimal
    contingency_opening: Decimal
    contingency_approved_usage: Decimal
    contingency_pending_usage: Decimal
    contingency_remaining_approved_basis: Decimal
    contingency_reported_remaining: Decimal
    net_headroom: Decimal
    uncommitted_budget: Decimal
    bridge: list[BridgeStep] = field(default_factory=list)
    watchlist: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def adjusted_vac_pct(self) -> Decimal:
        if self.total_budget == 0:
            return ZERO
        return (self.adjusted_vac / self.total_budget * 100).quantize(Decimal("0.01"))


def build_summary(model: CostModel, findings: list[Finding]) -> ExecutiveSummary:
    reported_vac = model.total_reported_vac
    calculated_vac = model.total_calculated_vac
    correction = calculated_vac - reported_vac
    excluded = model.excluded_exposure()
    adjusted = calculated_vac - excluded

    bridge = [
        BridgeStep("Reported VAC", None, reported_vac,
                   "Sum of reported VAC across package lines"),
        BridgeStep("Correction to definitional EAC (BR-02/BR-03)", correction,
                   calculated_vac,
                   "Recomputed as Current Budget less (Actual Cost + Forecast to Complete)"),
        BridgeStep("Less excluded change exposure (BR-06)", -excluded, adjusted,
                   "Pending and potential change marked 'Not Included', after "
                   "deduplication under BR-07"),
    ]

    unfunded = sum((l.commitments - l.current_budget for l in model.current.values()
                    if l.commitments > l.current_budget), ZERO)
    headroom = (model.contingency_remaining_approved_basis - excluded
                - model.contingency_pending_usage)

    watchlist = []
    order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    by_package: dict[str, dict] = {}
    for finding in findings:
        key = finding.cost_code
        entry = by_package.setdefault(key, {
            "cost_code": key, "package": finding.package, "findings": 0,
            "exposure": ZERO, "worst_severity": Severity.LOW, "rules": []})
        entry["findings"] += 1
        entry["exposure"] += finding.potential_exposure
        if order[finding.severity] < order[entry["worst_severity"]]:
            entry["worst_severity"] = finding.severity
        if finding.rule_id not in entry["rules"]:
            entry["rules"].append(finding.rule_id)
    watchlist = sorted(by_package.values(),
                       key=lambda e: (order[e["worst_severity"]], -e["exposure"]))

    counts = {
        "total": len(findings),
        "confirmed_errors": sum(1 for f in findings
                                if f.finding_type is FindingType.CONFIRMED_ERROR),
        "requires_explanation": sum(1 for f in findings
                                    if f.finding_type is FindingType.REQUIRES_EXPLANATION),
        "by_severity": {s.value: sum(1 for f in findings if f.severity is s)
                        for s in Severity},
        "by_status": {},
    }
    for finding in findings:
        counts["by_status"][finding.status.value] = \
            counts["by_status"].get(finding.status.value, 0) + 1

    return ExecutiveSummary(
        project=model.project, period=model.current_period,
        total_budget=model.total_budget, total_commitments=model.total_commitments,
        total_actual=sum((l.actual_cost for l in model.current.values()), ZERO),
        total_ftc=sum((l.forecast_to_complete for l in model.current.values()), ZERO),
        reported_eac=model.total_reported_eac, calculated_eac=model.total_calculated_eac,
        reported_vac=reported_vac, calculated_vac=calculated_vac,
        excluded_change_exposure=excluded, adjusted_vac=adjusted,
        unapproved_change_in_forecast=model.unapproved_in_forecast(),
        unfunded_commitments=unfunded,
        contingency_opening=model.contingency_opening,
        contingency_approved_usage=model.contingency_approved_usage,
        contingency_pending_usage=model.contingency_pending_usage,
        contingency_remaining_approved_basis=model.contingency_remaining_approved_basis,
        contingency_reported_remaining=model.contingency_reported_remaining,
        net_headroom=headroom,
        uncommitted_budget=model.total_budget - model.total_commitments,
        bridge=bridge, watchlist=watchlist, counts=counts)


def render_markdown(summary: ExecutiveSummary, findings: list[Finding]) -> str:
    lines = [
        f"# Executive Cost Summary — {summary.project}",
        f"**Reporting period:** {summary.period}  ",
        f"**Status:** all findings below are Draft until reviewed (BR-12)",
        "",
        "## Position",
        "",
        "| Measure | Amount |",
        "| --- | --- |",
        f"| Current Budget | {fmt(summary.total_budget)} |",
        f"| Commitments | {fmt(summary.total_commitments)} |",
        f"| Actual Cost | {fmt(summary.total_actual)} |",
        f"| Forecast to Complete | {fmt(summary.total_ftc)} |",
        f"| Reported EAC | {fmt(summary.reported_eac)} |",
        f"| **Calculated EAC** (Actual + FTC) | **{fmt(summary.calculated_eac)}** |",
        f"| Reported VAC | {fmt(summary.reported_vac)} |",
        f"| **Calculated VAC** (Budget - EAC) | **{fmt(summary.calculated_vac)}** |",
        "",
        "## Variance bridge",
        "",
        "| Step | Movement | Position |",
        "| --- | --- | --- |",
    ]
    for step in summary.bridge:
        movement = fmt(step.amount, signed=True) if step.amount is not None else "—"
        lines.append(f"| {step.label} | {movement} | {fmt(step.running)} |")
    lines += [
        "",
        f"Adjusted variance at completion is **{fmt(summary.adjusted_vac)}**, "
        f"{summary.adjusted_vac_pct}% of Current Budget.",
        "",
        "## Contingency",
        "",
        "| Measure | Amount |",
        "| --- | --- |",
        f"| Opening balance | {fmt(summary.contingency_opening)} |",
        f"| Approved usage | {fmt(summary.contingency_approved_usage)} |",
        f"| Remaining, approved basis | {fmt(summary.contingency_remaining_approved_basis)} |",
        f"| Pending draws (not yet approved) | {fmt(summary.contingency_pending_usage)} |",
        f"| Balance as reported in register | {fmt(summary.contingency_reported_remaining)} |",
        "",
        f"Net headroom after excluded change exposure and pending draws: "
        f"**{fmt(summary.net_headroom)}**.",
        "",
        "## Other exposures",
        "",
        f"- Unapproved change already embedded in the forecast: "
        f"{fmt(summary.unapproved_change_in_forecast)}",
        f"- Unfunded commitments above budget: {fmt(summary.unfunded_commitments)}",
        f"- Uncommitted budget: {fmt(summary.uncommitted_budget)}",
        "",
        "## Packages requiring management attention",
        "",
        "| Cost code | Package | Worst severity | Findings | Exposure | Rules |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in summary.watchlist:
        lines.append(
            f"| {entry['cost_code']} | {entry['package']} | "
            f"{entry['worst_severity'].value} | {entry['findings']} | "
            f"{fmt(entry['exposure'])} | {', '.join(entry['rules'])} |")
    lines += [
        "",
        "## Findings profile",
        "",
        f"- Total findings: {summary.counts['total']}",
        f"- Confirmed errors: {summary.counts['confirmed_errors']}",
        f"- Requiring explanation: {summary.counts['requires_explanation']}",
        f"- By severity: " + ", ".join(
            f"{k} {v}" for k, v in summary.counts["by_severity"].items() if v),
        "",
    ]
    return "\n".join(lines)
