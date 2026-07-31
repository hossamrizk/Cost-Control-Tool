from decimal import Decimal

import pytest

from conftest import D

# cost_code -> (actual, ftc, expected_calculated_eac, expected_calculated_vac)
EXPECTED_JULY = {
    "1000":  ("16500000",  "7500000", "24000000",   "1000000"),
    "2000":  ("40000000", "18500000", "58500000",   "1500000"),
    "3000":  ("52000000", "65500000", "117500000", "-7500000"),
    "4000":  ("47000000", "44000000", "91000000",  "-1000000"),
    "5000":  ("26000000", "42000000", "68000000",   "2000000"),
    "6000":  ("14000000", "34000000", "48000000",  "-3000000"),
    "7000":  ("8000000",  "10500000", "18500000",   "1500000"),
    "8000":  ("3500000",   "6000000",  "9500000",    "500000"),
    "9000":  ("2000000",  "21500000", "23500000",   "1500000"),
    "10000": ("12500000",  "1000000", "13500000",   "1500000"),
    "11000": ("9200000",   "-200000",  "9000000",   "1000000"),
    "12000": ("0",               "0",        "0",  "20000000"),
}

# cost_code -> forecast movement (current calculated EAC - prior calculated EAC)
EXPECTED_MOVEMENT = {
    "1000": "1500000", "2000": "1500000", "3000": "13500000", "4000": "8000000",
    "5000": "1000000", "6000": "5000000", "7000": "1500000",  "8000": "1500000",
    "9000": "1500000", "10000": "-500000", "11000": "-500000", "12000": "0",
}


@pytest.mark.parametrize("code,expected", EXPECTED_JULY.items())
def test_br02_eac_equals_actual_plus_ftc(model, code, expected):
    actual, ftc, eac, _ = expected
    line = model.current[code]
    assert line.actual_cost == D(actual)
    assert line.forecast_to_complete == D(ftc)
    assert line.calculated_eac == D(eac)


@pytest.mark.parametrize("code,expected", EXPECTED_JULY.items())
def test_br03_vac_equals_budget_minus_eac(model, code, expected):
    *_, vac = expected
    assert model.current[code].calculated_vac == D(vac)


@pytest.mark.parametrize("code,expected", EXPECTED_MOVEMENT.items())
def test_forecast_movement(model, code, expected):
    assert model.movement(code) == D(expected)


def test_only_cost_code_7000_disagrees_with_its_reported_eac(model):
    broken = {code: line.reported_eac - line.calculated_eac
              for code, line in model.current.items()
              if line.reported_eac != line.calculated_eac}
    assert broken == {"7000": D("750000")}


def test_previous_month_report_is_internally_consistent(model):
    """June is clean. If a change to the engine makes June dirty, the engine is
    wrong, not the data."""
    for code, line in model.previous.items():
        assert line.reported_eac == line.calculated_eac, code
        assert line.reported_vac == line.calculated_vac, code


def test_project_level_totals(model):
    assert model.total_budget == D("500000000")
    assert model.total_commitments == D("449000000")
    assert model.total_reported_eac == D("481750000")
    assert model.total_calculated_eac == D("481000000")
    assert model.total_reported_vac == D("18250000")
    assert model.total_calculated_vac == D("19000000")


def test_change_register_aggregates_after_deduplication(model):
    assert len(model.changes_raw) == 9
    assert len(model.changes) == 8
    assert [c.change_id for c in model.duplicate_changes] == ["CO-005"]
    # BR-07 exists precisely because these two numbers differ.
    raw = sum(c.amount for c in model.changes_raw
              if not c.is_included and not c.is_approved)
    assert raw == D("8200000")
    assert model.excluded_exposure() == D("7550000")


def test_excluded_exposure_by_package(model):
    assert model.excluded_exposure("5000") == D("3500000")
    assert model.excluded_exposure("10000") == D("2500000")
    assert model.excluded_exposure("7000") == D("900000")
    assert model.excluded_exposure("8000") == D("650000")
    assert model.excluded_exposure("3000") == D("0")


def test_unapproved_change_already_inside_the_forecast(model):
    assert model.unapproved_in_forecast() == D("9000000")
    assert model.unapproved_in_forecast("4000") == D("6000000")
    assert model.unapproved_in_forecast("6000") == D("3000000")


def test_contingency_arithmetic(model):
    assert model.contingency_opening == D("20000000")
    assert model.contingency_approved_usage == D("6450000")
    assert model.contingency_pending_usage == D("2000000")
    assert model.contingency_remaining_approved_basis == D("13550000")
    assert model.contingency_reported_remaining == D("11550000")


def test_contingency_running_balance_in_source_is_internally_consistent(model):
    """The register's own arithmetic is right; the defect is its basis."""
    balance = Decimal("0")
    for txn in model.contingency:
        balance += txn.increase - txn.usage
        assert balance == txn.reported_remaining_balance, txn.transaction_id


def test_br13_alias_normalized_to_canonical_code(model):
    aliases = {(r.raw, r.canonical) for r in model.codes.alias_normalizations}
    assert aliases == {("GEN-5000", "5000")}
    turbine = next(c for c in model.commitments if c.commitment_id == "C-005")
    assert turbine.raw_cost_code == "GEN-5000"
    assert turbine.cost_code == "5000"


def test_commitment_register_ties_to_cost_report(model):
    by_code = {}
    for c in model.commitments:
        by_code[c.cost_code] = by_code.get(c.cost_code, Decimal("0")) + c.committed_amount
    for code, line in model.current.items():
        expected = by_code.get(code, Decimal("0"))
        assert line.commitments == expected, f"{code}: report {line.commitments} vs register {expected}"
