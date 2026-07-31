"""Append-only audit log.

Auditability here means: for any figure on screen, you can reconstruct which
input file produced it, which engine version computed it, which prompt version
worded it, and who changed its status. The log is JSONL so it appends safely
and never rewrites history.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path("out/audit_log.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(event_type: str, payload: dict, *, run_id: str = "",
              actor: str | None = None, path: Path | str = DEFAULT_LOG) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": _now(),
        "run_id": run_id,
        "event_type": event_type,
        "actor": actor or os.environ.get("COSTCTL_ACTOR", "unknown"),
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str) + "\n")
    return event


def read_events(path: Path | str = DEFAULT_LOG) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
