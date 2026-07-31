"""BR-11 (source traceability) and BR-12 (human control of status)."""
import json

import pytest

from conftest import DATA, D
from costctl.engine import ValidationError, run, validate, write_outputs
from costctl.models import Status
from costctl.review import InvalidTransition, can_transition, set_status

SOURCE_FILES = {
    "cost_report_2026_07.csv", "cost_report_2026_06.csv", "change_register.csv",
    "contingency_register.csv", "commitment_register.csv", "cost_code_mapping.csv",
}


def test_br11_every_finding_cites_a_source_file_and_reference(result):
    for finding in result.findings:
        assert finding.source_file, finding.finding_id
        assert finding.source_reference, finding.finding_id
        assert any(name in finding.source_file for name in SOURCE_FILES), finding.source_file


def test_br11_source_reference_points_at_a_row_or_record(result):
    for finding in result.findings:
        reference = finding.source_reference
        assert ("row" in reference or "PROJECT TOTAL" in reference
                or "package rows" in reference), (finding.finding_id, reference)


def test_br12_all_findings_are_created_as_draft(result):
    assert all(f.status is Status.DRAFT for f in result.findings)
    assert result.summary.counts["by_status"] == {"Draft": 30}


def test_br12_validation_rejects_a_finding_created_in_a_non_draft_state(result):
    finding = result.findings[0]
    original = finding.status
    finding.status = Status.ACCEPTED
    try:
        with pytest.raises(ValidationError, match="BR-12"):
            validate(result.findings)
    finally:
        finding.status = original


def test_br11_validation_rejects_a_finding_without_a_source(result):
    finding = result.findings[0]
    original = finding.source_reference
    finding.source_reference = ""
    try:
        with pytest.raises(ValidationError, match="BR-11"):
            validate(result.findings)
    finally:
        finding.source_reference = original


def test_review_transitions(tmp_path):
    result = run(DATA, log=False)
    finding = result.findings[0]
    log = tmp_path / "audit.jsonl"

    assert can_transition(Status.DRAFT, Status.REVIEWED)
    assert not can_transition(Status.DRAFT, Status.ACCEPTED)   # no skipping review
    assert not can_transition(Status.CLOSED, Status.DRAFT)     # closed is terminal

    set_status(finding, Status.REVIEWED, reviewer="j.controls",
               note="traced to forecast workbook", run_id=result.run_id, log_path=log)
    assert finding.status is Status.REVIEWED
    set_status(finding, Status.ACCEPTED, reviewer="j.controls", log_path=log)
    assert finding.status is Status.ACCEPTED

    with pytest.raises(InvalidTransition):
        set_status(finding, Status.DRAFT, reviewer="j.controls", log_path=log)
    with pytest.raises(InvalidTransition):
        set_status(finding, Status.CLOSED, reviewer="", log_path=log)

    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [e["payload"]["to"] for e in events] == ["Reviewed", "Accepted"]
    assert events[0]["payload"]["reviewer"] == "j.controls"
    assert events[0]["payload"]["note"] == "traced to forecast workbook"


def test_audit_log_records_the_run_with_input_hashes(tmp_path):
    log = tmp_path / "audit.jsonl"
    result = run(DATA, log=True, log_path=log)
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert events[0]["run_id"] == result.run_id
    assert len(payload["inputs"]) == 6
    assert all(len(i["sha256"]) == 64 for i in payload["inputs"])


def test_findings_json_matches_the_required_output_structure(tmp_path):
    result = run(DATA, log=False)
    paths = write_outputs(result, tmp_path)
    document = json.loads(paths["findings"].read_text())

    required = {
        "finding_id", "project", "cost_code", "package", "finding_category",
        "finding_description", "reported_value", "calculated_value", "difference",
        "potential_exposure", "severity", "confidence", "source_file",
        "source_reference", "recommended_review", "recommended_action", "status",
    }
    for finding in document["findings"]:
        assert required.issubset(finding.keys())
        assert finding["status"] == "Draft"

    provenance = document["provenance"]
    assert provenance["engine_version"] and provenance["ruleset_version"]
    assert len(provenance["inputs"]) == 6


def test_executive_summary_bridge_is_arithmetically_closed(result):
    s = result.summary
    assert s.reported_vac == D("18250000")
    assert s.calculated_vac == D("19000000")
    assert s.excluded_change_exposure == D("7550000")
    assert s.adjusted_vac == D("11450000")
    assert s.adjusted_vac_pct == D("2.29")
    # Each bridge step must equal the previous position plus the movement.
    running = s.bridge[0].running
    for step in s.bridge[1:]:
        running += step.amount
        assert step.running == running
    assert running == s.adjusted_vac


def test_net_headroom_does_not_double_count_contingency_draws(result):
    s = result.summary
    assert s.net_headroom == (s.contingency_remaining_approved_basis
                              - s.excluded_change_exposure
                              - s.contingency_pending_usage)
    assert s.net_headroom == D("4000000")
