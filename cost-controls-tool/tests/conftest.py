from decimal import Decimal
from pathlib import Path

import pytest

from costctl.engine import run
from costctl.ingest import load_dataset
from costctl.model import build_model

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def tables():
    return load_dataset(DATA)


@pytest.fixture(scope="session")
def model(tables):
    return build_model(tables)


@pytest.fixture(scope="session")
def result(tmp_path_factory):
    log = tmp_path_factory.mktemp("audit") / "audit_log.jsonl"
    return run(DATA, log=True, log_path=log)


def D(value) -> Decimal:
    return Decimal(str(value))


def findings_for(result, rule_id=None, cost_code=None):
    out = result.findings
    if rule_id:
        out = [f for f in out if f.rule_id == rule_id]
    if cost_code:
        out = [f for f in out if f.cost_code == cost_code]
    return out
