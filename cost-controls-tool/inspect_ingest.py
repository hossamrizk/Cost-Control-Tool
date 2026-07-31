import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from costctl.ingest import load_dataset, load_table

DATA_DIR = Path(__file__).parent / "data"


def hr(char="─", width=78):
    print(char * width)


def main():
    tables = load_dataset(DATA_DIR)
    print(f"load_dataset({DATA_DIR}) returned {len(tables)} tables\n")

    for name, table in tables.items():
        hr("═")
        print(f"TABLE KEY:  '{name}'")
        print(f"  Table.name        = {table.name!r}")
        print(f"  Table.path        = {table.path}")
        print(f"  Table.sha256      = {table.sha256}")
        print(f"  len(table)        = {len(table)} rows")

        if len(table) == 0:
            continue

        columns = list(table.rows[0].data.keys())
        print(f"  columns           = {columns}")

        print("\n  ── FIRST ROW (full) ──")
        first = table.rows[0]
        print(f"    Row.source_file       = {first.source_file!r}")
        print(f"    Row.row_number        = {first.row_number}")
        print(f"    Row.source_reference  = {first.source_reference!r}")
        print(f"    Row.ref('example')    = {first.ref('example')!r}")
        print(f"    Row.data:")
        for k, v in first.data.items():
            print(f"      {k:<28} = {v!r}")

        if len(table) > 1:
            print("\n  ── LAST ROW (abbreviated) ──")
            last = table.rows[-1]
            print(f"    Row.row_number        = {last.row_number}")
            print(f"    Row.source_reference  = {last.source_reference!r}")
            preview = list(last.data.items())[:4]
            for k, v in preview:
                print(f"    {k:<28} = {v!r}")
            if len(last.data) > 4:
                print(f"    ... ({len(last.data) - 4} more fields)")

        print()

    hr("═")
    print("\nCOMPACT SUMMARY")
    print(f"  {'table key':<25} {'file':<32} {'rows':>5}  {'sha256 (first 12)':<14}")
    for name, table in tables.items():
        print(f"  {name:<25} {table.path.name:<32} {len(table):>5}  {table.sha256[:12]}")


if __name__ == "__main__":
    main()
