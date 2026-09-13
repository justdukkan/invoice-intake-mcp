"""Append-only audit log: one JSON line per side-effecting call.
Written for the auditor, not the developer: who/what/when and the approval
reference, without needing a model transcript."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

AUDIT_PATH = Path(os.environ.get("INVOICE_INTAKE_AUDIT", "audit.jsonl"))


def log(tool: str, record: dict, actor: str | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor or os.environ.get("INVOICE_INTAKE_ACTOR", "unknown"),
        "tool": tool,
        **record,
    }
    with AUDIT_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")
