from decimal import Decimal

import pytest

from conftest import D, findings_for
from costctl.models import Category, FindingType, Severity, Status
from costctl.rules import Thresholds


def test_br02_flags_the_single_eac_error(result):
    found = findings_for(result, "BR-02")
    assert len(found) == 1
    finding = found[0]
    assert finding.cost_code == "7000"
    assert finding.reported_value == D("19250000")
    assert finding.calculated_value == D("18500000")
    assert finding.difference == D("750000")
    assert finding.finding_type is FindingType.CONFIRMED_ERROR
    assert finding.severity is Severity.CRITICAL
    assert finding.confidence == 100
    assert "cost_report_2026_07" in finding.source_file
    assert "row 7" in finding.source_reference


def test_br03_flags_the_consequential_vac_error(result):
    found = findings_for(result, "BR-03")
    assert len(found) == 1
    assert found[0].cost_code == "7000"
    assert found[0].reported_value == D("750000")
    assert found[0].calculated_value == D("1500000")
    assert found[0].evidence["consequential_of"] == "BR-02"


def test_br04_triggers_on_absolute_and_percentage_thresholds(result):
    triggered = {f.cost_code: f.difference for f in findings_for(result, "BR-04")}
    # 2000 (+1.5M) and 5000 (+1.0M) breach neither $2M nor 3% of their budgets.
    assert "2000" not in triggered
    assert "5000" not in triggered
    assert triggered["3000"] == D("13500000")
    assert triggered["4000"] == D("8000000")
    assert triggered["6000"] == D("5000000")
    # 8000 moves only $1.5M but that is 15% of a $10M budget.
    assert triggered["8000"] == D("1500000")


def test_br04_separates_explained_from_unattributed_movement(result):
    electrical = findings_for(result, "BR-04", "3000")[0]
    assert electrical.evidence["explained_by_approved_change"] == "4500000"
    assert electrical.evidence["unattributed"] == "9000000"

    mechanical = findings_for(result, "BR-04", "4000")[0]
    # The entire $8M movement is unapproved change plus a pending contingency draw.
    assert mechanical.evidence["unapproved_change_in_forecast"] == "6000000"
    assert mechanical.evidence["unattributed"] == "2000000"
    # The commentary is preserved verbatim so a reviewer can judge it.
    assert mechanical.evidence["commentary"] == "No material change this period"

    commissioning = findings_for(result, "BR-04", "9000")[0]
    assert commissioning.evidence["explained_by_approved_change"] == "1200000"
    assert commissioning.evidence["unattributed"] == "300000"


def test_br04_threshold_is_configurable(model):
    from costctl.rules import br04_forecast_movement
    strict = br04_forecast_movement(model, Thresholds())
    loose = br04_forecast_movement(
        model, Thresholds(movement_abs=Decimal("20000000"),
                          movement_pct=Decimal("1")))
    assert len(strict) > len(loose)
    assert loose == []


def test_br05_flags_the_unfunded_ups_commitment(result):
    found = findings_for(result, "BR-05")
    assert len(found) == 1
    finding = found[0]
    assert finding.cost_code == "6000"
    assert finding.difference == D("2000000")
    assert finding.potential_exposure == D("2000000")
    assert finding.finding_category is Category.UNFUNDED_COMMITMENT
    assert "C-006" in finding.evidence["commitment_ids"]


def test_br06_reports_excluded_change_as_exposure(result):
    found = {f.evidence["change_id"]: f for f in findings_for(result, "BR-06")}
    assert set(found) == {"CO-003", "CO-005", "CO-007", "CO-008"}
    assert sum(f.potential_exposure for f in found.values()) == D("7550000")
    # CO-005 is counted once despite appearing twice in the register.
    assert found["CO-005"].potential_exposure == D("650000")
    assert all(f.finding_type is FindingType.REQUIRES_EXPLANATION for f in found.values())


def test_br07_identifies_the_duplicate_before_aggregation(result):
    found = findings_for(result, "BR-07")
    assert len(found) == 1
    finding = found[0]
    assert finding.evidence["change_id"] == "CO-005"
    assert finding.evidence["overstatement_if_aggregated"] == "650000"
    # A duplicate is an overstatement risk, never additive exposure.
    assert finding.potential_exposure == D("0")
    assert "duplicate of" in finding.source_reference


def test_br08_flags_approved_draw_without_approval_reference(result):
    missing = [f for f in findings_for(result, "BR-08")
               if f.finding_type is FindingType.CONFIRMED_ERROR]
    assert len(missing) == 1
    assert missing[0].evidence["transaction_id"] == "CT-005"
    assert missing[0].reported_value == D("750000")


def test_br08_flags_the_reporting_basis_of_the_balance(result):
    basis = [f for f in findings_for(result, "BR-08")
             if f.finding_type is FindingType.REQUIRES_EXPLANATION]
    assert len(basis) == 1
    assert basis[0].reported_value == D("11550000")
    assert basis[0].calculated_value == D("13550000")
    assert basis[0].evidence["pending_usage"] == "2000000"


def test_br09_flags_negative_forecast_to_complete(result):
    found = findings_for(result, "BR-09")
    assert len(found) == 1
    assert found[0].cost_code == "11000"
    assert found[0].reported_value == D("-200000")


def test_br10_project_eac_does_not_reconcile_to_definition(result):
    breaks = findings_for(result, "BR-10")
    definitional = [f for f in breaks if f.cost_code == "PROJECT"]
    assert len(definitional) == 1
    assert definitional[0].reported_value == D("481750000")
    assert definitional[0].calculated_value == D("481000000")
    assert definitional[0].difference == D("750000")
    codes = [c["cost_code"] for c in definitional[0].evidence["contributing_packages"]]
    assert codes == ["7000"]


def test_br10_column_totals_and_commitment_register_do_reconcile(result):
    """The roll-up itself is sound; only the definitional check fails. A rule
    that fired here as well would mean the reconciliation logic is too loose."""
    column_breaks = [f for f in findings_for(result, "BR-10")
                     if f.evidence.get("column")]
    assert column_breaks == []
    per_package = [f for f in findings_for(result, "BR-10")
                   if f.cost_code not in ("PROJECT",)]
    assert per_package == []


def test_br13_records_the_alias_normalization(result):
    found = findings_for(result, "BR-13")
    assert len(found) == 1
    assert found[0].evidence == {
        "raw_code": "GEN-5000", "canonical_code": "5000",
        "exposure_bucket": "none_data_quality"}


def test_br14_every_finding_is_classified_as_error_or_open_question(result):
    assert all(f.finding_type in (FindingType.CONFIRMED_ERROR,
                                 FindingType.REQUIRES_EXPLANATION)
               for f in result.findings)
    assert result.summary.counts["confirmed_errors"] == 7
    assert result.summary.counts["requires_explanation"] == 23


def test_dr01_unapproved_change_inside_the_forecast(result):
    found = {f.evidence["change_id"]: f for f in findings_for(result, "DR-01")}
    assert set(found) == {"CO-002", "CO-004"}
    # Already inside EAC, so it is not additive exposure...
    assert all(f.potential_exposure == D("0") for f in found.values())
    # ...but $6,000,000 of unapproved scope is still a critical control issue.
    assert found["CO-002"].severity is Severity.CRITICAL
    assert found["CO-004"].severity is Severity.HIGH


def test_dr02_approved_change_never_reached_current_budget(result):
    found = {f.evidence["change_id"]: f for f in findings_for(result, "DR-02")}
    assert set(found) == {"CO-001", "CO-006"}
    electrical = found["CO-001"]
    assert electrical.reported_value == D("-7500000")     # VAC as reported
    assert electrical.calculated_value == D("-3000000")   # VAC once transfer posted
    assert electrical.evidence["budget_movement"] == "0"


def test_dr03_forecast_below_committed_value(result):
    found = {f.cost_code: f for f in findings_for(result, "DR-03")}
    assert set(found) == {"10000", "11000"}
    assert found["10000"].difference == D("-1000000")
    assert found["11000"].difference == D("-500000")


def test_dr04_contingency_draws_that_trace_to_no_change_record(result):
    found = {f.evidence["transaction_id"] for f in findings_for(result, "DR-04")}
    assert found == {"CT-004", "CT-005"}


def test_no_finding_is_raised_against_a_clean_package(result):
    """Cost code 2000 is fully consistent: reported equals calculated, movement
    is under both thresholds, it is funded and it carries no change records."""
    assert findings_for(result, cost_code="2000") == []


def test_finding_ids_are_stable_across_runs(result, tmp_path):
    from costctl.engine import run
    from conftest import DATA
    again = run(DATA, log=False)
    assert [(f.finding_id, f.rule_id, f.cost_code) for f in again.findings] == \
           [(f.finding_id, f.rule_id, f.cost_code) for f in result.findings]
