"""Money handling.

All financial values are Decimal. Floats are never used for money anywhere in
this codebase: 0.1 + 0.2 != 0.3 in binary floating point, and a cost-control
tool that reports a $0.000001 variance is worse than useless. Decimal also
makes the test suite exact rather than tolerance-based.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

ZERO = Decimal("0")
_CLEAN = re.compile(r"[,\s$]")
_PARENS = re.compile(r"^\((.*)\)$")


class MoneyParseError(ValueError):
    """Raised when a source field cannot be interpreted as a monetary value."""


def parse_money(raw: object) -> Decimal:
    """Parse source formatting into a Decimal.

    Handles the shapes that actually appear in cost reports: "$25,000,000",
    "-$7,500,000", "($7,500,000)" (accounting negative), "$0", "" and None.
    Anything else raises rather than silently becoming zero, because a silent
    zero in a cost report is a defect that reconciliation will not catch.
    """
    if raw is None:
        return ZERO
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)

    text = str(raw).strip()
    if text in ("", "-", "--", "n/a", "N/A", "None"):
        return ZERO

    negative = False
    paren = _PARENS.match(text)
    if paren:
        negative = True
        text = paren.group(1)

    text = _CLEAN.sub("", text)
    if text.startswith("-"):
        negative = True
        text = text[1:]
    # "-$200,000" cleans to "-200000"; "$-200,000" cleans to "-200000" too.
    if text.startswith("-"):
        text = text[1:]

    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyParseError(f"cannot parse monetary value: {raw!r}") from exc

    return -value if negative else value


def fmt(value: Decimal, *, signed: bool = False) -> str:
    """Format for display: $1,234,567 or -$1,234,567."""
    value = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,}"


def fmt_m(value: Decimal) -> str:
    """Format in millions for narrative use: $13.5M."""
    millions = (Decimal(value) / Decimal("1000000")).quantize(Decimal("0.01"))
    sign = "-" if millions < 0 else ""
    return f"{sign}${abs(millions).normalize():f}M"
