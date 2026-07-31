"""Deterministic rule engine (BR-01).

Every finding in this module is produced by arithmetic on source data. No
language model is invoked here, and none of these figures are ever recomputed
downstream. Rules prefixed BR- come from the assessment's business-rule table;
rules prefixed DR- are derived checks that the stated rules imply but do not
name explicitly, and each carries a rationale in its description.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable

from .model import CostModel, PackageLine
from .models import Category, Finding, FindingType, Severity, Status
from .money import ZERO, fmt, fmt_m

RULE_PRIORITY = {
    "BR-02": Severity.CRITICAL, "BR-03": Severity.CRITICAL, "BR-04": Severity.HIGH,
    "BR-05": Severity.CRITICAL, "BR-06": Severity.HIGH,     "BR-07": Severity.CRITICAL,
    "BR-08": Severity.HIGH,     "BR-09": Severity.HIGH,     "BR-10": Severity.CRITICAL,
    "BR-13": Severity.MEDIUM,
    "DR-01": Severity.HIGH, "DR-02": Severity.HIGH, "DR-03": Severity.MEDIUM,
    "DR-04": Severity.MEDIUM,
}


@dataclass(frozen=True)
class Thresholds:
    """All thresholds are explicit and configurable; none are hidden in code."""
    movement_abs: Decimal = Decimal("2000000")
    movement_pct: Decimal = Decimal("0.03")
    # BR-04 reads "above $2,000,000 or 3% of budget". Implemented as OR, so the
    # effective trigger is the lower of the two. See README assumptions.
    movement_or_semantics: bool = True
    materiality_pct: Decimal = Decimal("0.01")   # of project budget
    tolerance: Decimal = Decimal("1")            # rounding tolerance in dollars


def _severity(rule_id: str, exposure: Decimal, project_budget: Decimal,
              thresholds: Thresholds) -> Severity:
    base = RULE_PRIORITY.get(rule_id, Severity.MEDIUM)
    if project_budget > 0 and abs(exposure) >= project_budget * thresholds.materiality_pct:
        return Severity.CRITICAL
    return base


def _confidence(finding_type: FindingType) -> int:
    """Deterministic findings are arithmetically certain; open questions are not."""
    return 100 if finding_type is FindingType.CONFIRMED_ERROR else 80


def _finding(model: CostModel, thresholds: Thresholds, *, rule_id: str, cost_code: str,
             category: Category, finding_type: FindingType, description: str,
             reported: Decimal | None, calculated: Decimal | None,
             difference: Decimal | None, exposure: Decimal,
             source_file: str, source_reference: str,
             review: str, action: str, severity_basis: Decimal | None = None,
             evidence: dict | None = None) -> Finding:
    """severity_basis is the amount materiality is judged against. It is stated
    explicitly per rule because it is not always the reported exposure: an
    unapproved change already inside the forecast adds no exposure, but a
    $6,000,000 unapproved commitment to scope is still a critical control issue.
    """
    return Finding(
        finding_id="",                     # assigned by the engine, deterministically
        rule_id=rule_id,
        project=model.project,
        cost_code=cost_code,
        package=model.codes.name(cost_code) if cost_code not in ("PROJECT", "") else cost_code,
        finding_category=category,
        finding_type=finding_type,
        finding_description=description,
        reported_value=reported,
        calculated_value=calculated,
        difference=difference,
        potential_exposure=exposure,
        severity=_severity(rule_id,
                           severity_basis if severity_basis is not None else exposure,
                           model.total_budget, thresholds),
        confidence=_confidence(finding_type),
        source_file=source_file,
        source_reference=source_reference,
        recommended_review=review,
        recommended_action=action,
        status=Status.DRAFT,
        evidence=evidence or {},
    )


# --------------------------------------------------------------------------- #
# BR-02 / BR-03: EAC and VAC must follow the definitions, not the report.
# --------------------------------------------------------------------------- #
def br02_eac_integrity(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for line in model.current.values():
        diff = line.reported_eac - line.calculated_eac
        if abs(diff) <= t.tolerance:
            continue
        out.append(_finding(
            model, t, rule_id="BR-02", cost_code=line.cost_code,
            category=Category.CALCULATION_ERROR, finding_type=FindingType.CONFIRMED_ERROR,
            description=(
                f"Reported EAC of {fmt(line.reported_eac)} does not equal Actual Cost "
                f"{fmt(line.actual_cost)} plus Forecast to Complete "
                f"{fmt(line.forecast_to_complete)} = {fmt(line.calculated_eac)}. "
                f"The report overstates EAC by {fmt(abs(diff))}."
                if diff > 0 else
                f"Reported EAC of {fmt(line.reported_eac)} does not equal Actual Cost "
                f"{fmt(line.actual_cost)} plus Forecast to Complete "
                f"{fmt(line.forecast_to_complete)} = {fmt(line.calculated_eac)}. "
                f"The report understates EAC by {fmt(abs(diff))}."),
            reported=line.reported_eac, calculated=line.calculated_eac, difference=diff,
            exposure=ZERO,
            source_file=line.source_file, source_reference=line.source_reference,
            review="Trace the reported EAC to the forecast workbook and identify the "
                   "cell or adjustment that does not derive from Actual + FTC.",
            action="Correct the reported EAC in the cost report and reissue the affected "
                   "package line before the figures are used for reporting.",
            severity_basis=abs(diff),
            evidence={"actual_cost": str(line.actual_cost),
                      "forecast_to_complete": str(line.forecast_to_complete),
                      "commentary": line.commentary}))
    return out


def br03_vac_integrity(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for line in model.current.values():
        diff = line.reported_vac - line.calculated_vac
        if abs(diff) <= t.tolerance:
            continue
        eac_broken = abs(line.reported_eac - line.calculated_eac) > t.tolerance
        out.append(_finding(
            model, t, rule_id="BR-03", cost_code=line.cost_code,
            category=Category.CALCULATION_ERROR, finding_type=FindingType.CONFIRMED_ERROR,
            description=(
                f"Reported VAC of {fmt(line.reported_vac)} does not equal Current Budget "
                f"{fmt(line.current_budget)} minus calculated EAC "
                f"{fmt(line.calculated_eac)} = {fmt(line.calculated_vac)}."
                + (" This is a consequence of the reported EAC error on the same line "
                   "(BR-02), not an independent VAC error." if eac_broken else "")),
            reported=line.reported_vac, calculated=line.calculated_vac, difference=diff,
            exposure=ZERO,
            source_file=line.source_file, source_reference=line.source_reference,
            review="Confirm whether the VAC error is solely a consequence of the EAC "
                   "error or whether the budget figure is also misstated.",
            action="Recompute VAC as Current Budget minus corrected EAC.",
            severity_basis=abs(diff),
            evidence={"consequential_of": "BR-02" if eac_broken else None,
                      "current_budget": str(line.current_budget)}))
    return out


# --------------------------------------------------------------------------- #
# BR-04: material forecast movement requires an explanation.
# --------------------------------------------------------------------------- #
def br04_forecast_movement(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for code, line in model.current.items():
        prev = model.previous.get(code)
        if prev is None:
            continue
        movement = model.movement(code)
        pct_threshold = line.current_budget * t.movement_pct
        triggers = []
        if abs(movement) > t.movement_abs:
            triggers.append(f"exceeds {fmt(t.movement_abs)}")
        if abs(movement) > pct_threshold:
            triggers.append(f"exceeds 3% of budget ({fmt(pct_threshold)})")
        if not triggers:
            continue

        explained = model.approved_included_changes(code)
        unapproved = model.unapproved_in_forecast(code)
        residual = movement - explained - unapproved
        out.append(_finding(
            model, t, rule_id="BR-04", cost_code=code,
            category=Category.FORECAST_MOVEMENT,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"EAC moved {fmt(movement, signed=True)} from {model.previous_period} "
                f"({fmt(prev.calculated_eac)}) to {model.current_period} "
                f"({fmt(line.calculated_eac)}); {' and '.join(triggers)}. "
                f"Approved change accounts for {fmt(explained)}, unapproved change already "
                f"embedded in the forecast accounts for {fmt(unapproved)}, leaving "
                f"{fmt(residual)} unattributed. Management commentary reads: "
                f"\"{line.commentary}\"."),
            reported=prev.calculated_eac, calculated=line.calculated_eac,
            difference=movement, exposure=abs(residual) if residual > 0 else ZERO,
            source_file=line.source_file,
            source_reference=f"{line.source_reference}; prior period "
                             f"{prev.source_file} {prev.source_reference}",
            review="Obtain a quantified reconciliation of the movement from the package "
                   "cost engineer, and confirm the commentary is consistent with it.",
            action="Require a written movement explanation with the unattributed amount "
                   "broken down by driver before the forecast is accepted.",
            severity_basis=abs(movement),
            evidence={"movement": str(movement), "threshold_abs": str(t.movement_abs),
                      "threshold_pct_value": str(pct_threshold),
                      "explained_by_approved_change": str(explained),
                      "unapproved_change_in_forecast": str(unapproved),
                      "unattributed": str(residual),
                      "commentary": line.commentary,
                      "exposure_bucket": "unattributed_movement"}))
    return out


# --------------------------------------------------------------------------- #
# BR-05: commitments above budget are unfunded.
# --------------------------------------------------------------------------- #
def br05_unfunded_commitment(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for code, line in model.current.items():
        over = line.commitments - line.current_budget
        if over <= t.tolerance:
            continue
        refs = [c for c in model.commitments if c.cost_code == code]
        out.append(_finding(
            model, t, rule_id="BR-05", cost_code=code,
            category=Category.UNFUNDED_COMMITMENT, finding_type=FindingType.CONFIRMED_ERROR,
            description=(
                f"Commitments of {fmt(line.commitments)} exceed the Current Budget of "
                f"{fmt(line.current_budget)} by {fmt(over)}. The package is committed "
                f"beyond its authorized funding."),
            reported=line.commitments, calculated=line.current_budget, difference=over,
            exposure=over,
            source_file=line.source_file,
            source_reference=f"{line.source_reference}; "
                             + "; ".join(f"{c.source_file} {c.source_reference}" for c in refs),
            review="Confirm the executed contract value and check whether an approved "
                   "change or budget transfer was authorized but not posted to the budget.",
            action="Raise a budget transfer from contingency or reduce committed scope; "
                   "do not release further commitments against this package until funded.",
            severity_basis=over,
            evidence={"commitment_ids": [c.commitment_id for c in refs],
                      "exposure_bucket": "unfunded_commitment"}))
    return out


# --------------------------------------------------------------------------- #
# BR-06: excluded pending / potential change is additional exposure.
# --------------------------------------------------------------------------- #
def br06_excluded_change(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for change in model.changes:                     # deduplicated register only
        if change.is_included or change.is_approved:
            continue
        line = model.current.get(change.cost_code)
        eac = line.calculated_eac if line else ZERO
        out.append(_finding(
            model, t, rule_id="BR-06", cost_code=change.cost_code,
            category=Category.EXCLUDED_CHANGE_EXPOSURE,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"{change.change_id} ({change.description}) is {change.status} with a "
                f"forecast treatment of '{change.forecast_treatment}', so its "
                f"{fmt(change.amount)} is not reflected in the package EAC of {fmt(eac)}. "
                f"It must be reported as additional exposure."),
            reported=eac, calculated=eac + change.amount, difference=change.amount,
            exposure=change.amount,
            source_file=change.source_file, source_reference=change.source_reference,
            review="Confirm the current status and likelihood of the change, and whether "
                   "the excluded amount is already covered by package contingency.",
            action="Report the amount as exposure in the executive summary and set a "
                   "decision date for approval or rejection.",
            severity_basis=change.amount,
            evidence={"change_id": change.change_id, "change_status": change.status,
                      "forecast_treatment": change.forecast_treatment,
                      "date_identified": change.date_identified,
                      "exposure_bucket": "excluded_change"}))
    return out


# --------------------------------------------------------------------------- #
# BR-07: duplicates must be found before aggregation.
# --------------------------------------------------------------------------- #
def br07_duplicate_changes(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for dup in model.duplicate_changes:
        original = next((c for c in model.changes if c.dedup_key == dup.dedup_key), None)
        original_ref = f"{original.source_file} {original.source_reference}" if original else "n/a"
        out.append(_finding(
            model, t, rule_id="BR-07", cost_code=dup.cost_code,
            category=Category.DUPLICATE_RECORD, finding_type=FindingType.CONFIRMED_ERROR,
            description=(
                f"Change {dup.change_id} appears more than once in the change register "
                f"with identical cost code, amount ({fmt(dup.amount)}), status and "
                f"treatment. The duplicate is excluded from all aggregation; had it been "
                f"included, exposure would be overstated by {fmt(dup.amount)}."),
            reported=dup.amount * 2, calculated=dup.amount, difference=dup.amount,
            exposure=ZERO,   # deliberately zero: this is an overstatement risk, not exposure
            source_file=dup.source_file,
            source_reference=f"{dup.source_reference} (duplicate of {original_ref})",
            review="Confirm with the change controller that this is a data-entry "
                   "duplicate and not two genuinely separate changes sharing an ID.",
            action="Remove the duplicate row from the change register and add a unique "
                   "constraint on change ID at the point of entry.",
            evidence={"change_id": dup.change_id,
                      "overstatement_if_aggregated": str(dup.amount),
                      "exposure_bucket": "none_duplicate"}))
    return out


# --------------------------------------------------------------------------- #
# BR-08: contingency control.
# --------------------------------------------------------------------------- #
def br08_contingency_control(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for txn in model.contingency:
        if txn.is_draw and txn.is_approved and not txn.approval_reference.strip():
            out.append(_finding(
                model, t, rule_id="BR-08", cost_code="12000",
                category=Category.CONTINGENCY_CONTROL,
                finding_type=FindingType.CONFIRMED_ERROR,
                description=(
                    f"Contingency draw {txn.transaction_id} ({txn.category}) of "
                    f"{fmt(txn.usage)} is recorded as Approved but carries no approval "
                    f"reference. Every other approved draw in the register cites one."),
                reported=txn.usage, calculated=None, difference=None, exposure=txn.usage,
                source_file=txn.source_file, source_reference=txn.source_reference,
                review="Locate the approval instrument for this draw and confirm the "
                       "authorizing signature and date.",
                action="Attach the approval reference or reverse the draw and restore the "
                       "contingency balance.",
                evidence={"transaction_id": txn.transaction_id, "status": txn.status,
                          "exposure_bucket": "uncontrolled_contingency"}))

    # Basis of the reported running balance.
    approved_basis = model.contingency_remaining_approved_basis
    reported = model.contingency_reported_remaining
    diff = reported - approved_basis
    if abs(diff) > t.tolerance:
        pending = model.contingency_pending_usage
        last = model.contingency[-1]
        out.append(_finding(
            model, t, rule_id="BR-08", cost_code="12000",
            category=Category.CONTINGENCY_CONTROL,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"The register reports a remaining contingency balance of {fmt(reported)}, "
                f"but the definition (opening balance minus approved usage) gives "
                f"{fmt(approved_basis)}. The running balance has been reduced by "
                f"{fmt(pending)} of draws that are still Pending, so the reported figure "
                f"mixes approved and unapproved usage."),
            reported=reported, calculated=approved_basis, difference=diff, exposure=ZERO,
            source_file=last.source_file, source_reference=last.source_reference,
            review="Agree the reporting basis with the project controls lead: approved "
                   "usage only, with pending draws disclosed separately as commitments "
                   "against the reserve.",
            action="Restate the register to show approved balance, pending draws and "
                   "resulting forecast balance as three separate lines.",
            evidence={"opening_balance": str(model.contingency_opening),
                      "approved_usage": str(model.contingency_approved_usage),
                      "pending_usage": str(pending),
                      "exposure_bucket": "none_disclosure"}))
    return out


# --------------------------------------------------------------------------- #
# BR-09: negative forecast to complete.
# --------------------------------------------------------------------------- #
def br09_negative_ftc(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    for code, line in model.current.items():
        if line.forecast_to_complete >= ZERO:
            continue
        out.append(_finding(
            model, t, rule_id="BR-09", cost_code=code,
            category=Category.FORECAST_INTEGRITY,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"Forecast to Complete is {fmt(line.forecast_to_complete)}, a negative "
                f"value. A negative FTC implies the package expects to recover cost "
                f"already incurred, which usually indicates an over-accrual, a credit "
                f"posted to the wrong period, or a forecast plug."),
            reported=line.forecast_to_complete, calculated=ZERO,
            difference=line.forecast_to_complete, exposure=abs(line.forecast_to_complete),
            source_file=line.source_file, source_reference=line.source_reference,
            review="Reconcile actual cost to invoices and accruals for this package and "
                   "confirm whether a credit or reversal is expected.",
            action="Restate FTC to zero or to a supported positive value and disclose any "
                   "expected credit separately.",
            severity_basis=abs(line.forecast_to_complete),
            evidence={"actual_cost": str(line.actual_cost),
                      "commitments": str(line.commitments),
                      "commentary": line.commentary,
                      "exposure_bucket": "forecast_integrity"}))
    return out


# --------------------------------------------------------------------------- #
# BR-10: project totals must reconcile to package totals.
# --------------------------------------------------------------------------- #
def br10_reconciliation(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    totals = model.current_totals
    if totals is not None:
        columns = [
            ("Current Budget", totals.current_budget, model.total_budget),
            ("Commitments", totals.commitments, model.total_commitments),
            ("Actual Cost", totals.actual_cost,
             sum((l.actual_cost for l in model.current.values()), ZERO)),
            ("Forecast to Complete", totals.forecast_to_complete,
             sum((l.forecast_to_complete for l in model.current.values()), ZERO)),
            ("Reported EAC", totals.reported_eac, model.total_reported_eac),
            ("Reported VAC", totals.reported_vac, model.total_reported_vac),
        ]
        for label, reported_total, package_sum in columns:
            diff = reported_total - package_sum
            if abs(diff) <= t.tolerance:
                continue
            out.append(_finding(
                model, t, rule_id="BR-10", cost_code="PROJECT",
                category=Category.RECONCILIATION_BREAK,
                finding_type=FindingType.CONFIRMED_ERROR,
                description=(
                    f"The reported project total for {label} ({fmt(reported_total)}) does "
                    f"not equal the sum of the package lines ({fmt(package_sum)})."),
                reported=reported_total, calculated=package_sum, difference=diff,
                exposure=ZERO,
                source_file=totals.source_file, source_reference=totals.source_reference,
                review="Identify which package line is excluded from or double counted in "
                       "the project total.",
                action="Rebuild the project total from the package lines.",
                evidence={"column": label, "exposure_bucket": "none_reconciliation"}))

    # The definitional reconciliation: does the reported EAC total obey BR-02?
    diff = model.total_reported_eac - model.total_calculated_eac
    if abs(diff) > t.tolerance:
        contributors = [
            {"cost_code": l.cost_code,
             "difference": str(l.reported_eac - l.calculated_eac)}
            for l in model.current.values()
            if abs(l.reported_eac - l.calculated_eac) > t.tolerance]
        out.append(_finding(
            model, t, rule_id="BR-10", cost_code="PROJECT",
            category=Category.RECONCILIATION_BREAK,
            finding_type=FindingType.CONFIRMED_ERROR,
            description=(
                f"Project reported EAC of {fmt(model.total_reported_eac)} does not equal "
                f"total Actual Cost plus total Forecast to Complete "
                f"({fmt(model.total_calculated_eac)}), a difference of {fmt(diff)}. "
                f"The whole difference arises from cost code "
                f"{', '.join(c['cost_code'] for c in contributors)}."),
            reported=model.total_reported_eac, calculated=model.total_calculated_eac,
            difference=diff, exposure=ZERO,
            source_file="cost_report_2026_07.csv",
            source_reference="all package rows (project roll-up)",
            review="Confirm the package-level EAC corrections and re-derive the project "
                   "total before it is reported to management.",
            action="Reissue the project total on the corrected package figures; project "
                   "VAC becomes " + fmt(model.total_calculated_vac) + ".",
            evidence={"contributing_packages": contributors,
                      "exposure_bucket": "none_reconciliation"}))

    # Commitment register against the cost report commitments column.
    by_code: dict[str, Decimal] = {}
    for c in model.commitments:
        by_code[c.cost_code] = by_code.get(c.cost_code, ZERO) + c.committed_amount
    for code, line in model.current.items():
        register = by_code.get(code, ZERO)
        diff = line.commitments - register
        if abs(diff) <= t.tolerance:
            continue
        out.append(_finding(
            model, t, rule_id="BR-10", cost_code=code,
            category=Category.RECONCILIATION_BREAK,
            finding_type=FindingType.CONFIRMED_ERROR,
            description=(
                f"The cost report shows commitments of {fmt(line.commitments)} for this "
                f"package but the commitment register totals {fmt(register)}, a difference "
                f"of {fmt(diff)}."),
            reported=line.commitments, calculated=register, difference=diff, exposure=ZERO,
            source_file=line.source_file,
            source_reference=f"{line.source_reference}; commitment_register.csv",
            review="Identify missing or duplicated commitments between the register and "
                   "the cost report.",
            action="Reconcile the two sources and correct whichever is wrong.",
            evidence={"exposure_bucket": "none_reconciliation"}))
    return out


# --------------------------------------------------------------------------- #
# BR-13: alias normalization must be visible, not silent.
# --------------------------------------------------------------------------- #
def br13_alias_normalization(model: CostModel, t: Thresholds) -> list[Finding]:
    out = []
    seen: set[tuple] = set()
    for res in model.codes.alias_normalizations:
        key = (res.raw, res.source_file, res.source_reference)
        if key in seen:
            continue
        seen.add(key)
        out.append(_finding(
            model, t, rule_id="BR-13", cost_code=res.canonical,
            category=Category.DATA_QUALITY, finding_type=FindingType.CONFIRMED_ERROR,
            description=(
                f"Record coded '{res.raw}' was normalized to canonical cost code "
                f"{res.canonical} using the mapping table. Without normalization this "
                f"record would not have joined to the cost report and its value would "
                f"have been omitted from reconciliation."),
            reported=None, calculated=None, difference=None, exposure=ZERO,
            source_file=res.source_file, source_reference=res.source_reference,
            review="Confirm the alias is expected and correct the source system so future "
                   "records use the canonical code.",
            action="No financial adjustment required; raise a data-quality ticket against "
                   "the originating register.",
            evidence={"raw_code": res.raw, "canonical_code": res.canonical,
                      "exposure_bucket": "none_data_quality"}))
    return out


# --------------------------------------------------------------------------- #
# Derived rules: implied by the stated rules but not named by them.
# --------------------------------------------------------------------------- #
def dr01_unapproved_in_forecast(model: CostModel, t: Thresholds) -> list[Finding]:
    """BR-06 covers excluded change. The mirror risk is unapproved change that
    has been *included*, which inflates the forecast on unauthorized scope."""
    out = []
    for change in model.changes:
        if not change.is_included or change.is_approved:
            continue
        line = model.current.get(change.cost_code)
        eac = line.calculated_eac if line else ZERO
        out.append(_finding(
            model, t, rule_id="DR-01", cost_code=change.cost_code,
            category=Category.UNAPPROVED_CHANGE_IN_FORECAST,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"{change.change_id} ({change.description}) has status "
                f"'{change.status}' and no approval reference, yet its {fmt(change.amount)} "
                f"is treated as Included in the forecast. The package EAC of {fmt(eac)} "
                f"therefore contains unapproved scope."),
            reported=eac, calculated=eac - change.amount, difference=-change.amount,
            exposure=ZERO,   # already inside EAC; not additive exposure
            source_file=change.source_file, source_reference=change.source_reference,
            review="Confirm whether the change is expected to be approved, and whether "
                   "including it pre-approval is consistent with the forecast policy.",
            action="Either progress the change to approval or remove it from the forecast "
                   "and disclose it as exposure instead.",
            severity_basis=change.amount,
            evidence={"change_id": change.change_id, "change_status": change.status,
                      "amount_in_forecast": str(change.amount),
                      "exposure_bucket": "unapproved_in_forecast"}))
    return out


def dr02_change_not_reflected(model: CostModel, t: Thresholds) -> list[Finding]:
    """An approved change funded from contingency should move Current Budget.
    If the package budget is unchanged month on month, the change has been
    absorbed into the forecast without being authorized into the budget, which
    makes package VAC look worse than it is and overstates free contingency."""
    out = []
    for change in model.changes:
        if not (change.is_approved and change.is_included):
            continue
        cur = model.current.get(change.cost_code)
        prev = model.previous.get(change.cost_code)
        if cur is None or prev is None:
            continue
        budget_movement = cur.current_budget - prev.current_budget
        if budget_movement >= change.amount - t.tolerance:
            continue
        adjusted_vac = cur.current_budget + change.amount - cur.calculated_eac
        out.append(_finding(
            model, t, rule_id="DR-02", cost_code=change.cost_code,
            category=Category.CHANGE_NOT_REFLECTED,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"{change.change_id} of {fmt(change.amount)} was approved "
                f"({change.approval_reference}) and drawn from contingency, but the "
                f"Current Budget for this package is unchanged between "
                f"{model.previous_period} and {model.current_period} "
                f"({fmt(cur.current_budget)}). The cost sits in the forecast while the "
                f"funding sits in contingency, so reported VAC of "
                f"{fmt(cur.calculated_vac)} would be {fmt(adjusted_vac)} once the "
                f"transfer is posted."),
            reported=cur.calculated_vac, calculated=adjusted_vac, difference=change.amount,
            exposure=ZERO,
            source_file=change.source_file,
            source_reference=f"{change.source_reference}; {cur.source_file} "
                             f"{cur.source_reference}",
            review="Confirm whether a budget transfer from contingency to this package was "
                   "raised, and if not, why the approved change has not been posted.",
            action="Post the budget transfer so Current Budget reflects approved change "
                   "and the contingency budget line reduces correspondingly.",
            severity_basis=change.amount,
            evidence={"change_id": change.change_id,
                      "approval_reference": change.approval_reference,
                      "budget_movement": str(budget_movement),
                      "exposure_bucket": "none_presentation"}))
    return out


def dr03_eac_below_commitment(model: CostModel, t: Thresholds) -> list[Finding]:
    """A forecast below executed contract value implies a descope, a claim, or a
    forecast that has not absorbed committed cost."""
    out = []
    for code, line in model.current.items():
        if line.commitments <= ZERO:
            continue
        shortfall = line.commitments - line.calculated_eac
        if shortfall <= t.tolerance:
            continue
        out.append(_finding(
            model, t, rule_id="DR-03", cost_code=code,
            category=Category.FORECAST_INTEGRITY,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"Calculated EAC of {fmt(line.calculated_eac)} is below executed "
                f"commitments of {fmt(line.commitments)} by {fmt(shortfall)}. The forecast "
                f"assumes the package will finish for less than the value already under "
                f"contract."),
            reported=line.calculated_eac, calculated=line.commitments,
            difference=-shortfall, exposure=shortfall,
            source_file=line.source_file, source_reference=line.source_reference,
            review="Confirm whether a descope, credit or unresolved claim supports "
                   "finishing below contract value.",
            action="Support the underrun with a signed change or restate EAC to at least "
                   "committed value.",
            severity_basis=shortfall,
            evidence={"commentary": line.commentary,
                      "exposure_bucket": "forecast_integrity"}))
    return out


def dr04_unattributed_contingency(model: CostModel, t: Thresholds) -> list[Finding]:
    """A draw that cannot be traced to a cost code cannot be reconciled to the
    package forecast that is meant to be carrying the cost."""
    out = []
    change_refs = {c.approval_reference.strip() for c in model.changes
                   if c.approval_reference.strip()}
    for txn in model.contingency:
        if not txn.is_draw:
            continue
        if txn.approval_reference.strip() in change_refs:
            continue
        out.append(_finding(
            model, t, rule_id="DR-04", cost_code="12000",
            category=Category.DATA_QUALITY,
            finding_type=FindingType.REQUIRES_EXPLANATION,
            description=(
                f"Contingency draw {txn.transaction_id} ({txn.category}, {fmt(txn.usage)}) "
                f"cannot be traced to a change record or cost code. The reserve has been "
                f"reduced without an identifiable package carrying the cost."),
            reported=txn.usage, calculated=None, difference=txn.usage, exposure=ZERO,
            source_file=txn.source_file, source_reference=txn.source_reference,
            review="Identify the cost code and change record the draw funds, and confirm "
                   "the cost appears in that package's forecast.",
            action="Attribute the draw to a cost code so it reconciles to package "
                   "forecast movement.",
            evidence={"transaction_id": txn.transaction_id, "status": txn.status,
                      "unattributed_amount": str(txn.usage),
                      "exposure_bucket": "none_traceability"}))
    return out


ALL_RULES: list[tuple[str, Callable[[CostModel, Thresholds], list[Finding]]]] = [
    ("BR-02", br02_eac_integrity),
    ("BR-03", br03_vac_integrity),
    ("BR-10", br10_reconciliation),
    ("BR-05", br05_unfunded_commitment),
    ("BR-07", br07_duplicate_changes),
    ("BR-04", br04_forecast_movement),
    ("BR-06", br06_excluded_change),
    ("DR-01", dr01_unapproved_in_forecast),
    ("DR-02", dr02_change_not_reflected),
    ("BR-08", br08_contingency_control),
    ("BR-09", br09_negative_ftc),
    ("DR-03", dr03_eac_below_commitment),
    ("DR-04", dr04_unattributed_contingency),
    ("BR-13", br13_alias_normalization),
]


def run_all(model: CostModel, thresholds: Thresholds | None = None) -> list[Finding]:
    thresholds = thresholds or Thresholds()
    findings: list[Finding] = []
    for _, rule in ALL_RULES:
        findings.extend(rule(model, thresholds))
    return findings
