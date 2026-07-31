"""Field normalization (BR-13).

Cost-code aliases are resolved through the mapping table, never through
guesswork or string similarity. An unmapped alias is an error, not something
to infer: silently coercing GEN-5000 to 5000 without a mapping entry would
break traceability the first time a genuinely new code appeared.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .ingest import Table
from .money import parse_money


@dataclass(frozen=True)
class Package:
    cost_code: str
    package_name: str
    discipline: str
    mapping_budget: Decimal


@dataclass
class AliasResolution:
    raw: str
    canonical: str
    was_alias: bool
    source_file: str
    source_reference: str


class CostCodeMap:
    def __init__(self, mapping_table: Table):
        self.packages: dict[str, Package] = {}
        self.aliases: dict[str, str] = {}
        for row in mapping_table:
            code = row.data["canonical_cost_code"].strip()
            self.packages[code] = Package(
                cost_code=code,
                package_name=row.data["package_name"],
                discipline=row.data["discipline"],
                mapping_budget=parse_money(row.data["current_budget"]),
            )
            self.aliases[code] = code
            alias = (row.data.get("known_alias") or "").strip()
            if alias:
                self.aliases[alias.upper()] = code

        self.resolutions: list[AliasResolution] = []

    def normalize(self, raw_code: str, *, source_file: str, source_reference: str) -> str:
        raw = (raw_code or "").strip()
        canonical = self.aliases.get(raw) or self.aliases.get(raw.upper())
        if canonical is None:
            raise KeyError(
                f"cost code {raw!r} in {source_file} ({source_reference}) is not in "
                f"the mapping table; add a mapping entry before ingesting"
            )
        self.resolutions.append(
            AliasResolution(raw=raw, canonical=canonical, was_alias=(raw != canonical),
                            source_file=source_file, source_reference=source_reference)
        )
        return canonical

    def name(self, cost_code: str) -> str:
        pkg = self.packages.get(cost_code)
        return pkg.package_name if pkg else f"Unknown ({cost_code})"

    @property
    def alias_normalizations(self) -> list[AliasResolution]:
        return [r for r in self.resolutions if r.was_alias]
