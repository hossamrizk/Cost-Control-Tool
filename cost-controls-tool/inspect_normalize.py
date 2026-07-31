"""Ad-hoc inspection of what normalize.py produces.

Run with: python inspect_normalize.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from costctl.ingest import load_table
from costctl.normalize import CostCodeMap

DATA_DIR = Path(__file__).parent / "data"


def hr(char="─", width=78):
    print(char * width)


def main():
    # 1. Build the CostCodeMap from the mapping CSV
    mapping_table = load_table(DATA_DIR / "cost_code_mapping.csv", "cost_code_mapping")
    codes = CostCodeMap(mapping_table)

    hr("═")
    print("STEP 1 — CostCodeMap built from cost_code_mapping.csv")
    hr()
    print(f"  packages loaded: {len(codes.packages)}")
    print(f"  alias entries:   {len(codes.aliases)}  (includes identity mappings)")

    print("\n  codes.packages (canonical_code → Package):")
    for code, pkg in codes.packages.items():
        print(f"    {code:<6} {pkg.package_name:<30} "
              f"disc={pkg.discipline:<15} budget={pkg.mapping_budget}")

    print("\n  codes.aliases (raw → canonical):")
    for raw, canonical in codes.aliases.items():
        marker = "  ← ALIAS" if raw != canonical else ""
        print(f"    {raw:<12} → {canonical}{marker}")

    # 2. Normalize codes from real source rows
    hr("═")
    print("\nSTEP 2 — Normalizing codes from the commitment and change registers")
    hr()

    commit_table = load_table(DATA_DIR / "commitment_register.csv", "commitment_register")
    change_table = load_table(DATA_DIR / "change_register.csv", "change_register")

    print("\n  From commitment_register.csv:")
    print(f"    {'row':<4} {'raw code':<12} {'canonical':<10}  was_alias?")
    for row in commit_table:
        raw = row.data["cost_code"]
        canonical = codes.normalize(raw,
                                    source_file=row.source_file,
                                    source_reference=row.source_reference)
        marker = "  ← alias!" if raw != canonical else ""
        print(f"    {row.row_number:<4} {raw:<12} {canonical:<10}{marker}")

    print("\n  From change_register.csv:")
    print(f"    {'row':<4} {'raw code':<12} {'canonical':<10}  was_alias?")
    for row in change_table:
        raw = row.data["cost_code"]
        canonical = codes.normalize(raw,
                                    source_file=row.source_file,
                                    source_reference=row.source_reference)
        marker = "  ← alias!" if raw != canonical else ""
        print(f"    {row.row_number:<4} {raw:<12} {canonical:<10}{marker}")

    # 3. Everything that has happened is recorded
    hr("═")
    print(f"\nSTEP 3 — Full resolution log ({len(codes.resolutions)} entries total)")
    hr()
    print(f"  codes.resolutions           = {len(codes.resolutions)}  (every normalize() call)")
    print(f"  codes.alias_normalizations  = {len(codes.alias_normalizations)}  "
          f"(only rows where raw != canonical)")

    print("\n  alias_normalizations detail (what BR-13 rule reads):")
    for res in codes.alias_normalizations:
        print(f"    raw={res.raw!r}  →  canonical={res.canonical!r}")
        print(f"      from {res.source_file}, {res.source_reference}")

    # 4. The name() helper
    hr("═")
    print("\nSTEP 4 — codes.name(cost_code) lookup (used in Finding rendering)")
    hr()
    for code in ["1000", "5000", "12000", "9999"]:
        print(f"  codes.name({code!r}) = {codes.name(code)!r}")

    # 5. What happens with an unknown alias
    hr("═")
    print("\nSTEP 5 — Unknown code raises KeyError (BR-13: no silent guessing)")
    hr()
    try:
        codes.normalize("UNKNOWN-9999",
                        source_file="fake.csv",
                        source_reference="row 42")
    except KeyError as exc:
        print(f"  ✓ KeyError raised as expected:")
        print(f"    {exc}")


if __name__ == "__main__":
    main()
