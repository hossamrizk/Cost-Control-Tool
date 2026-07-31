"""BR-01: the model interprets, it does not calculate.

These tests are the enforcement mechanism. The prompt asks the model not to do
arithmetic; the guardrail makes it impossible for arithmetic that leaks through
to reach a reader, and these tests prove the guardrail works.
"""
import json

import pytest

from conftest import DATA, D
from costctl.ai import (DeterministicInterpreter, Interpreter,
                        check_numeric_guardrail)
from costctl.engine import run
from costctl.models import Severity, Status


@pytest.fixture
def eac_finding(result):
    return next(f for f in result.findings if f.rule_id == "BR-02")


def test_guardrail_accepts_figures_the_engine_computed(eac_finding):
    ok, detail = check_numeric_guardrail(
        eac_finding,
        "The report shows $19,250,000 against a calculated $18,500,000, "
        "overstating EAC by $750,000.")
    assert ok, detail


def test_guardrail_accepts_the_same_figures_written_in_millions(eac_finding):
    ok, _ = check_numeric_guardrail(
        eac_finding, "EAC is overstated by $0.75M against a calculated $18.5M.")
    assert ok


def test_guardrail_blocks_a_fabricated_figure(eac_finding):
    ok, detail = check_numeric_guardrail(
        eac_finding, "This puts the package $4,200,000 over budget.")
    assert not ok
    assert "4,200,000" in detail


def test_guardrail_blocks_arithmetic_the_model_performed_itself(eac_finding):
    """8,000,000 + 10,500,000 is in the facts; 18,600,000 is not."""
    ok, detail = check_numeric_guardrail(
        eac_finding, "Actual plus forecast comes to $18,600,000.")
    assert not ok


def test_guardrail_ignores_non_monetary_numbers(eac_finding):
    ok, detail = check_numeric_guardrail(
        eac_finding,
        "Cost code 7000 breached the 3% threshold in July 2026; see row 7 of "
        "the report and escalate within 5 days.")
    assert ok, detail


def test_deterministic_interpreter_never_trips_the_guardrail(result):
    interpreter = DeterministicInterpreter()
    for finding in result.findings:
        interpretation = interpreter.interpret(finding)
        assert interpretation.guardrail == "passed", (finding.finding_id,
                                                      interpretation.guardrail_detail)
        assert interpretation.provider == "deterministic-fallback"


class FabricatingInterpreter(Interpreter):
    """Stands in for a model that hallucinates a number."""
    provider = "test-fabricator"
    model = "fake"

    def _generate(self, facts):
        return json.dumps({
            "explanation": "The package is $88,888,888 over budget and slipping.",
            "recommended_review": "Review immediately.",
            "recommended_action": "Escalate.",
            "proposed_severity": "Critical",
            "proposed_confidence": 99,
            "severity_rationale": "Large overrun.",
        })


class BrokenInterpreter(Interpreter):
    provider = "test-broken"
    model = "fake"

    def _generate(self, facts):
        raise RuntimeError("upstream returned 503")


def test_fabricated_output_is_withheld_and_the_run_warns():
    result = run(DATA, interpreter=FabricatingInterpreter(), log=False)
    blocked = [f for f in result.findings if f.ai.guardrail == "blocked_numeric"]
    assert len(blocked) == len(result.findings)
    for finding in blocked:
        assert "88,888,888" not in finding.ai.explanation
        assert "withheld" in finding.ai.explanation
        assert finding.ai.proposed_severity is None
    assert any("guardrail blocked" in w for w in result.warnings)


def test_ai_failure_degrades_without_breaking_the_run():
    result = run(DATA, interpreter=BrokenInterpreter(), log=False)
    assert all(f.ai.guardrail == "error" for f in result.findings)
    assert "503" in result.findings[0].ai.guardrail_detail
    # Deterministic guidance still stands in for the AI text.
    assert result.findings[0].ai.recommended_action
    assert any("interpretation failed" in w for w in result.warnings)


def test_ai_cannot_alter_any_deterministic_figure():
    without_ai = run(DATA, log=False)
    with_ai = run(DATA, interpreter=FabricatingInterpreter(), log=False)
    for plain, interpreted in zip(without_ai.findings, with_ai.findings):
        assert plain.finding_id == interpreted.finding_id
        assert plain.reported_value == interpreted.reported_value
        assert plain.calculated_value == interpreted.calculated_value
        assert plain.difference == interpreted.difference
        assert plain.potential_exposure == interpreted.potential_exposure
        assert plain.severity is interpreted.severity
        assert plain.confidence == interpreted.confidence
    assert without_ai.summary.adjusted_vac == with_ai.summary.adjusted_vac == D("11450000")


def test_ai_cannot_move_a_finding_out_of_draft():
    result = run(DATA, interpreter=DeterministicInterpreter(), log=False)
    assert all(f.status is Status.DRAFT for f in result.findings)


def test_ai_severity_is_a_proposal_not_a_decision():
    """A proposed severity is recorded beside the engine's, never over it."""
    class Escalating(Interpreter):
        provider, model = "test-escalating", "fake"

        def _generate(self, facts):
            return json.dumps({
                "explanation": "Worth attention.", "recommended_review": "Check.",
                "recommended_action": "Act.", "proposed_severity": "Critical",
                "proposed_confidence": 100, "severity_rationale": "Judgement."})

    result = run(DATA, interpreter=Escalating(), log=False)
    medium = [f for f in result.findings if f.severity is Severity.MEDIUM]
    assert medium, "expected at least one Medium finding to test against"
    for finding in medium:
        assert finding.severity is Severity.MEDIUM          # engine value survives
        assert finding.ai.proposed_severity == "Critical"   # proposal recorded separately


def test_the_model_only_ever_sees_precomputed_facts(eac_finding):
    from costctl.ai import _facts
    facts = _facts(eac_finding)
    assert facts["reported_value"] == "19250000"
    assert facts["calculated_value"] == "18500000"
    assert facts["difference"] == "750000"
    # No raw file contents, no other packages, nothing to compute across.
    assert "rows" not in facts and "table" not in facts
    assert set(facts) == {
        "rule_id", "cost_code", "package", "category", "finding_type",
        "deterministic_description", "reported_value", "calculated_value",
        "difference", "potential_exposure", "engine_severity", "source_file",
        "source_reference", "supporting_evidence"}
