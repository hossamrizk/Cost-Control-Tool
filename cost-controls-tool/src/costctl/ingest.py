import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Row:
    data: dict[str, str]
    source_file: str
    row_number: int          # 1-based, excludes the header

    @property
    def source_reference(self) -> str:
        return f"row {self.row_number}"

    def ref(self, *extra: str) -> str:
        parts = [f"row {self.row_number}"] + [e for e in extra if e]
        return ", ".join(parts)


@dataclass(frozen=True)
class Table:
    name: str
    path: Path
    sha256: str
    rows: list[Row]

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_table(path: str | Path, name: str | None = None) -> Table:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"source file not found: {path}")
    filename = path.name
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [
            Row(data={(k or "").strip(): (v or "").strip() for k, v in raw.items()},
                source_file=filename,
                row_number=index)
            for index, raw in enumerate(reader, start=1)
        ]
    return Table(name=name or path.stem, path=path, sha256=sha256_file(path), rows=rows)


def load_dataset(data_dir: str | Path) -> dict[str, Table]:
    data_dir = Path(data_dir)
    spec = {
        "cost_code_mapping": "cost_code_mapping.csv",
        "cost_report_previous": "cost_report_2026_06.csv",
        "cost_report_current": "cost_report_2026_07.csv",
        "commitment_register": "commitment_register.csv",
        "change_register": "change_register.csv",
        "contingency_register": "contingency_register.csv",
    }
    return {name: load_table(data_dir / fn, name) for name, fn in spec.items()}
