"""The money parser is the first place a cost tool can silently lose money."""
from decimal import Decimal

import pytest

from costctl.money import MoneyParseError, ZERO, fmt, fmt_m, parse_money


@pytest.mark.parametrize("raw,expected", [
    ("$25,000,000", "25000000"),
    ("$500,000,000", "500000000"),
    ("$0", "0"),
    ("0", "0"),
    ("-$200,000", "-200000"),
    ("-$7,500,000", "-7500000"),
    ("($7,500,000)", "-7500000"),          # accounting negative
    ("$-200,000", "-200000"),
    ("$1,234.56", "1234.56"),
    ("", "0"),
    ("   ", "0"),
    (None, "0"),
    ("n/a", "0"),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == Decimal(expected)


def test_parse_money_is_exact_not_floating_point():
    total = parse_money("$0.10") + parse_money("$0.20")
    assert total == Decimal("0.30")
    assert str(total) == "0.30"


@pytest.mark.parametrize("raw", ["abc", "$twelve", "12,00,0x"])
def test_unparseable_values_raise_rather_than_becoming_zero(raw):
    with pytest.raises(MoneyParseError):
        parse_money(raw)


def test_formatting():
    assert fmt(Decimal("19250000")) == "$19,250,000"
    assert fmt(Decimal("-7500000")) == "-$7,500,000"
    assert fmt(Decimal("750000"), signed=True) == "+$750,000"
    assert fmt_m(Decimal("13500000")) == "$13.5M"
