"""Ad-hoc inspection of what model.py produces.

Run with: python inspect_model.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from costctl.ingest import load_dataset
from costctl.model import build_model
from costctl.money import fmt

DATA_DIR = Path(__file__).parent / "data"


def hr(char="─", width=90):
    print(char * width)


def main():
    tables = load_dataset(DATA_DIR)
    model = build_model(tables)

    # ── 1. Shape ──────────────────────────────────────────────────────────
    hr("═")
    print("STEP 1 — CostModel shape")
    hr()
    print(f"  project           = {model.project!r}")
    print(f"  current_period    = {model.current_period!r}")
    print(f"  previous_period   = {model.previous_period!r}")
    print(f"  current           = {len(model.current)} PackageLine(s)")
    print(f"  previous          = {len(model.previous)} PackageLine(s)")
    print(f"  commitments       = {len(model.commitments)}")
    print(f"  changes_raw       = {len(model.changes_raw)}")
    print(f"  changes  (unique) = {len(model.changes)}")
    print(f"  duplicate_changes = {len(model.duplicate_changes)}")
    print(f"  contingency       = {len(model.contingency)}")
    print(f"  current_totals    = {'present' if model.current_totals else 'missing'}")
    print(f"  previous_totals   = {'present' if model.previous_totals else 'missing'}")

    # ── 2. Package lines with calculated EAC/VAC ──────────────────────────
    hr("═")
    print("\nSTEP 2 — Current period PackageLines (calculated EAC/VAC = BR-02, BR-03)")
    hr()
    print(f"  {'code':<6} {'package':<28} {'reported EAC':>15} {'calc EAC':>15} "
          f"{'reported VAC':>15} {'calc VAC':>15}  Δ?")
    for code, line in sorted(model.current.items(), key=lambda kv: int(kv[0])):
        eac_gap = line.reported_eac - line.calculated_eac
        vac_gap = line.reported_vac - line.calculated_vac
        marker = "  ← MISMATCH" if abs(eac_gap) > 1 or abs(vac_gap) > 1 else ""
        print(f"  {code:<6} {line.package:<28} "
              f"{fmt(line.reported_eac):>15} {fmt(line.calculated_eac):>15} "
              f"{fmt(line.reported_vac):>15} {fmt(line.calculated_vac):>15}{marker}")

    # ── 3. BR-07 deduplication ────────────────────────────────────────────
    hr("═")
    print("\nSTEP 3 — BR-07 change register deduplication")
    hr()
    print(f"  changes_raw       = {len(model.changes_raw)}  (as read from CSV)")
    print(f"  changes  (unique) = {len(model.changes)}   (what every rule reads)")
    print(f"  duplicate_changes = {len(model.duplicate_changes)}   (what BR-07 rule reads)")

    print("\n  changes_raw (all rows, IDs + amounts):")
    for c in model.changes_raw:
        print(f"    {c.source_reference:<8} {c.change_id:<8} cost_code={c.cost_code:<6} "
              f"{fmt(c.amount):>13}  status={c.status:<10} treatment={c.forecast_treatment}")

    print("\n  duplicate_changes (excluded from every aggregate):")
    for c in model.duplicate_changes:
        print(f"    {c.source_reference:<8} {c.change_id:<8} {fmt(c.amount):>13}  "
              f"{c.description}")
        print(f"      dedup_key = {c.dedup_key}")

    # ── 4. Commitments — canonical + raw ──────────────────────────────────
    hr("═")
    print("\nSTEP 4 — Commitments (raw_cost_code preserves the pre-normalize code)")
    hr()
    print(f"  {'id':<7} {'raw':<10} {'canonical':<10} {'package':<28} {'amount':>13}")
    for c in model.commitments:
        alias_marker = "  ← alias" if c.raw_cost_code != c.cost_code else ""
        print(f"  {c.commitment_id:<7} {c.raw_cost_code:<10} {c.cost_code:<10} "
              f"{c.package:<28} {fmt(c.committed_amount):>13}{alias_marker}")

    # ── 5. Contingency ────────────────────────────────────────────────────
    hr("═")
    print("\nSTEP 5 — Contingency transactions")
    hr()
    print(f"  {'id':<7} {'category':<28} {'type':<16} {'increase':>13} "
          f"{'usage':>13}  {'status':<10}")
    for t in model.contingency:
        print(f"  {t.transaction_id:<7} {t.category:<28} {t.transaction_type:<16} "
              f"{fmt(t.increase):>13} {fmt(t.usage):>13}  {t.status:<10}")

    # ── 6. Derived aggregates ─────────────────────────────────────────────
    hr("═")
    print("\nSTEP 6 — Derived aggregates on CostModel")
    hr()

    print("  Project totals (calculated basis):")
    print(f"    total_budget                          = {fmt(model.total_budget)}")
    print(f"    total_commitments                     = {fmt(model.total_commitments)}")
    print(f"    total_calculated_eac  (Actual + FTC)  = {fmt(model.total_calculated_eac)}")
    print(f"    total_reported_eac    (CSV sum)       = {fmt(model.total_reported_eac)}")
    print(f"    total_calculated_vac  (Budget - EAC)  = {fmt(model.total_calculated_vac)}")
    print(f"    total_reported_vac    (CSV sum)       = {fmt(model.total_reported_vac)}")

    print("\n  Contingency:")
    print(f"    contingency_opening                     = {fmt(model.contingency_opening)}")
    print(f"    contingency_approved_usage              = {fmt(model.contingency_approved_usage)}")
    print(f"    contingency_pending_usage               = {fmt(model.contingency_pending_usage)}")
    print(f"    contingency_remaining_approved_basis    = "
          f"{fmt(model.contingency_remaining_approved_basis)}   (definitional)")
    print(f"    contingency_reported_remaining          = "
          f"{fmt(model.contingency_reported_remaining)}   (from CSV)")

    print("\n  Change exposures (deduplicated):")
    print(f"    excluded_exposure()          (BR-06)  = {fmt(model.excluded_exposure())}")
    print(f"    unapproved_in_forecast()     (DR-01)  = {fmt(model.unapproved_in_forecast())}")

    # ── 7. Per-package movement (BR-04 view) ──────────────────────────────
    hr("═")
    print("\nSTEP 7 — Forecast movement per package (current calc EAC - previous calc EAC)")
    hr()
    print(f"  {'code':<6} {'package':<28} {'prev EAC':>15} {'curr EAC':>15} "
          f"{'movement':>15}  approved change")
    for code, line in sorted(model.current.items(), key=lambda kv: int(kv[0])):
        prev = model.previous.get(code)
        if prev is None:
            continue
        movement = model.movement(code)
        approved = model.approved_included_changes(code)
        print(f"  {code:<6} {line.package:<28} "
              f"{fmt(prev.calculated_eac):>15} {fmt(line.calculated_eac):>15} "
              f"{fmt(movement, signed=True):>15}   {fmt(approved)}")


if __name__ == "__main__":
    main()
