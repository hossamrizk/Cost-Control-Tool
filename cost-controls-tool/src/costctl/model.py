from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .ingest import Table
from .money import ZERO, parse_money
from .normalize import CostCodeMap

TOTAL_CODE = "TOTAL"


@dataclass
class PackageLine:
    cost_code: str
    package: str
    current_budget: Decimal
    commitments: Decimal
    actual_cost: Decimal
    forecast_to_complete: Decimal
    reported_eac: Decimal
    reported_vac: Decimal
    commentary: str
    source_file: str
    source_reference: str

    @property
    def calculated_eac(self) -> Decimal:
        """BR-02."""
        return self.actual_cost + self.forecast_to_complete

    @property
    def calculated_vac(self) -> Decimal:
        """BR-03."""
        return self.current_budget - self.calculated_eac

    @property
    def uncommitted_budget(self) -> Decimal:
        return self.current_budget - self.commitments


@dataclass
class ReportedTotals:
    current_budget: Decimal
    commitments: Decimal
    actual_cost: Decimal
    forecast_to_complete: Decimal
    reported_eac: Decimal
    reported_vac: Decimal
    source_file: str
    source_reference: str


@dataclass
class ChangeRecord:
    change_id: str
    cost_code: str
    package: str
    description: str
    amount: Decimal
    status: str                 # Approved | Pending | Potential
    date_identified: str
    forecast_treatment: str     # Included | Not Included
    approval_reference: str
    source_file: str
    source_reference: str

    @property
    def is_included(self) -> bool:
        return self.forecast_treatment.strip().lower() == "included"

    @property
    def is_approved(self) -> bool:
        return self.status.strip().lower() == "approved"

    @property
    def dedup_key(self) -> tuple:
        return (self.change_id, self.cost_code, self.amount,
                self.status.lower(), self.forecast_treatment.lower())


@dataclass
class Commitment:
    commitment_id: str
    cost_code: str
    raw_cost_code: str
    package: str
    vendor_contract: str
    committed_amount: Decimal
    status: str
    award_date: str
    source_file: str
    source_reference: str


@dataclass
class ContingencyTxn:
    transaction_id: str
    category: str
    transaction_type: str
    increase: Decimal
    usage: Decimal
    reported_remaining_balance: Decimal
    status: str
    date: str
    approval_reference: str
    source_file: str
    source_reference: str

    @property
    def is_draw(self) -> bool:
        return self.transaction_type.strip().lower() == "draw"

    @property
    def is_approved(self) -> bool:
        return self.status.strip().lower() == "approved"


@dataclass
class CostModel:
    project: str
    current_period: str
    previous_period: str
    codes: CostCodeMap
    current: dict[str, PackageLine]
    previous: dict[str, PackageLine]
    current_totals: Optional[ReportedTotals]
    previous_totals: Optional[ReportedTotals]
    commitments: list[Commitment]
    changes_raw: list[ChangeRecord]
    changes: list[ChangeRecord] = field(default_factory=list)      # deduplicated
    duplicate_changes: list[ChangeRecord] = field(default_factory=list)
    contingency: list[ContingencyTxn] = field(default_factory=list)

    # --- derived aggregates, all deterministic -------------------------------
    @property
    def total_budget(self) -> Decimal:
        return sum((l.current_budget for l in self.current.values()), ZERO)

    @property
    def total_calculated_eac(self) -> Decimal:
        return sum((l.calculated_eac for l in self.current.values()), ZERO)

    @property
    def total_reported_eac(self) -> Decimal:
        return sum((l.reported_eac for l in self.current.values()), ZERO)

    @property
    def total_calculated_vac(self) -> Decimal:
        return self.total_budget - self.total_calculated_eac

    @property
    def total_reported_vac(self) -> Decimal:
        return sum((l.reported_vac for l in self.current.values()), ZERO)

    @property
    def total_commitments(self) -> Decimal:
        return sum((l.commitments for l in self.current.values()), ZERO)

    def movement(self, cost_code: str) -> Decimal:
        """Forecast movement: current EAC - previous EAC (deterministic basis)."""
        cur = self.current.get(cost_code)
        prev = self.previous.get(cost_code)
        if cur is None or prev is None:
            return ZERO
        return cur.calculated_eac - prev.calculated_eac

    def approved_included_changes(self, cost_code: str) -> Decimal:
        return sum((c.amount for c in self.changes
                    if c.cost_code == cost_code and c.is_approved and c.is_included), ZERO)

    def excluded_exposure(self, cost_code: str | None = None) -> Decimal:
        """BR-06 exposure, on the deduplicated register."""
        return sum((c.amount for c in self.changes
                    if not c.is_included and not c.is_approved
                    and (cost_code is None or c.cost_code == cost_code)), ZERO)

    def unapproved_in_forecast(self, cost_code: str | None = None) -> Decimal:
        return sum((c.amount for c in self.changes
                    if c.is_included and not c.is_approved
                    and (cost_code is None or c.cost_code == cost_code)), ZERO)

    @property
    def contingency_opening(self) -> Decimal:
        return sum((t.increase for t in self.contingency), ZERO)

    @property
    def contingency_approved_usage(self) -> Decimal:
        return sum((t.usage for t in self.contingency if t.is_draw and t.is_approved), ZERO)

    @property
    def contingency_pending_usage(self) -> Decimal:
        return sum((t.usage for t in self.contingency if t.is_draw and not t.is_approved), ZERO)

    @property
    def contingency_remaining_approved_basis(self) -> Decimal:
        """Definition sheet: opening balance - approved usage."""
        return self.contingency_opening - self.contingency_approved_usage

    @property
    def contingency_reported_remaining(self) -> Decimal:
        return self.contingency[-1].reported_remaining_balance if self.contingency else ZERO


def _line(row, codes: CostCodeMap) -> PackageLine:
    code = codes.normalize(row.data["cost_code"],
                           source_file=row.source_file,
                           source_reference=row.source_reference)
    return PackageLine(
        cost_code=code,
        package=row.data["package"],
        current_budget=parse_money(row.data["current_budget"]),
        commitments=parse_money(row.data["commitments"]),
        actual_cost=parse_money(row.data["actual_cost"]),
        forecast_to_complete=parse_money(row.data["forecast_to_complete"]),
        reported_eac=parse_money(row.data["reported_eac"]),
        reported_vac=parse_money(row.data["reported_vac"]),
        commentary=row.data.get("management_commentary", ""),
        source_file=row.source_file,
        source_reference=row.ref(f"cost code {code}"),
    )


def _totals(row) -> ReportedTotals:
    return ReportedTotals(
        current_budget=parse_money(row.data["current_budget"]),
        commitments=parse_money(row.data["commitments"]),
        actual_cost=parse_money(row.data["actual_cost"]),
        forecast_to_complete=parse_money(row.data["forecast_to_complete"]),
        reported_eac=parse_money(row.data["reported_eac"]),
        reported_vac=parse_money(row.data["reported_vac"]),
        source_file=row.source_file,
        source_reference=row.ref("PROJECT TOTAL"),
    )


def build_model(tables: dict[str, Table], *, project: str = "Data Centre Project",
                current_period: str = "July 2026",
                previous_period: str = "June 2026") -> CostModel:
    codes = CostCodeMap(tables["cost_code_mapping"])

    def split(table):
        lines, totals = {}, None
        for row in table:
            if row.data["cost_code"].strip().upper() == TOTAL_CODE:
                totals = _totals(row)
                continue
            line = _line(row, codes)
            lines[line.cost_code] = line
        return lines, totals

    current, current_totals = split(tables["cost_report_current"])
    previous, previous_totals = split(tables["cost_report_previous"])

    commitments = []
    for row in tables["commitment_register"]:
        raw = row.data["cost_code"]
        code = codes.normalize(raw, source_file=row.source_file,
                               source_reference=row.source_reference)
        commitments.append(Commitment(
            commitment_id=row.data["commitment_id"], cost_code=code, raw_cost_code=raw,
            package=row.data["package"], vendor_contract=row.data["vendor_contract"],
            committed_amount=parse_money(row.data["committed_amount"]),
            status=row.data["status"], award_date=row.data["award_date"],
            source_file=row.source_file,
            source_reference=row.ref(row.data["commitment_id"])))

    changes_raw = []
    for row in tables["change_register"]:
        code = codes.normalize(row.data["cost_code"], source_file=row.source_file,
                               source_reference=row.source_reference)
        changes_raw.append(ChangeRecord(
            change_id=row.data["change_id"], cost_code=code, package=row.data["package"],
            description=row.data["description"], amount=parse_money(row.data["amount"]),
            status=row.data["status"], date_identified=row.data["date_identified"],
            forecast_treatment=row.data["forecast_treatment"],
            approval_reference=row.data.get("approval_reference", ""),
            source_file=row.source_file,
            source_reference=row.ref(row.data["change_id"])))

    # BR-07: deduplicate before any aggregation.
    seen: dict[tuple, ChangeRecord] = {}
    unique, duplicates = [], []
    for change in changes_raw:
        if change.dedup_key in seen:
            duplicates.append(change)
        else:
            seen[change.dedup_key] = change
            unique.append(change)

    contingency = []
    for row in tables["contingency_register"]:
        contingency.append(ContingencyTxn(
            transaction_id=row.data["transaction_id"], category=row.data["category"],
            transaction_type=row.data["transaction_type"],
            increase=parse_money(row.data["increase"]), usage=parse_money(row.data["usage"]),
            reported_remaining_balance=parse_money(row.data["reported_remaining_balance"]),
            status=row.data["status"], date=row.data["date"],
            approval_reference=row.data.get("approval_reference", ""),
            source_file=row.source_file,
            source_reference=row.ref(row.data["transaction_id"])))

    return CostModel(
        project=project, current_period=current_period, previous_period=previous_period,
        codes=codes, current=current, previous=previous,
        current_totals=current_totals, previous_totals=previous_totals,
        commitments=commitments, changes_raw=changes_raw, changes=unique,
        duplicate_changes=duplicates, contingency=contingency)
