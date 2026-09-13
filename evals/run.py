"""Replayable evaluation set. Runs the same decision policy the orchestrator
uses, directly against core (no transport), and compares to expectations.
Run on every prompt, model, tool or rule change:

    python evals/run.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invoice_intake_mcp import core, erp  # noqa: E402


def decide(state: dict, inv: dict) -> str:
    vend = core.search_vendor(state, inv["vendor_name"])
    if len(vend["candidates"]) != 1:
        return "review:vendor_unresolved"
    if not inv["po_reference"]:
        return "review:no_po_reference"
    vendor_id = vend["candidates"][0]["vendor_id"]
    m = core.match_po(state, vendor_id, inv["po_reference"], inv["total"], inv["currency"])
    if m["status"] != "matched":
        return f"review:{m['status']}"
    try:
        st = core.stage_invoice(state, "eval", vendor_id, inv["po_reference"], inv["total"], inv["currency"])
    except core.ToolFailure as e:
        return f"review:{e.payload['error']}"
    if st["needs_approval"]:
        return "waiting_approval"
    core.post_invoice(state, st["staged_id"])
    return "posted"


def main() -> int:
    import os, tempfile
    tmp = tempfile.mkdtemp()
    erp.STATE_PATH = Path(tmp) / "state.json"
    os.environ["INVOICE_INTAKE_AUDIT"] = str(Path(tmp) / "audit.jsonl")
    from invoice_intake_mcp import audit
    audit.AUDIT_PATH = Path(tmp) / "audit.jsonl"
    cases = [json.loads(l) for l in (Path(__file__).parent / "cases.jsonl").read_text().splitlines() if l.strip()]
    failures = 0
    for c in cases:
        state = erp.reset()
        got = decide(state, c["invoice"])
        ok = got == c["expect"]
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {c['name']:45} expected={c['expect']:30} got={got}")
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
