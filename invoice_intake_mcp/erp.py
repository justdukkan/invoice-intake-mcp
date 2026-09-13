"""A tiny fake ERP + invoice inbox, persisted to a JSON file so that the
read server, the commit server and the approval CLI (separate processes)
share one state. Replace this module with real system adapters."""

from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from pathlib import Path

STATE_PATH = Path(os.environ.get("INVOICE_INTAKE_STATE", "state.json"))

SEED = {
    "vendors": {
        "V-1001": {"vendor_id": "V-1001", "name": "Norda Ltd", "terms": "net30", "status": "active"},
        "V-1002": {"vendor_id": "V-1002", "name": "Brightline Logistics", "terms": "net45", "status": "active"},
        "V-1003": {"vendor_id": "V-1003", "name": "Okta Print House", "terms": "net30", "status": "blocked"},
    },
    "purchase_orders": {
        "PO-1187": {"po_number": "PO-1187", "vendor_id": "V-1001", "total": 7420.00, "currency": "EUR",
                    "goods_received": True, "status": "open"},
        "PO-1190": {"po_number": "PO-1190", "vendor_id": "V-1002", "total": 1180.00, "currency": "EUR",
                    "goods_received": False, "status": "open"},
        "PO-1191": {"po_number": "PO-1191", "vendor_id": "V-1002", "total": 640.00, "currency": "EUR",
                    "goods_received": True, "status": "open"},
    },
    # what "OCR" would extract from each PDF in the inbox
    "inbox": {
        "invoice_2291.pdf": {"invoice_id": "invoice_2291.pdf", "vendor_name": "Norda Ltd", "total": 7420.00,
                             "currency": "EUR", "po_reference": "PO-1187", "invoice_date": "2026-09-11",
                             "received_at": "2026-09-13T07:14:00Z"},
        "invoice_2292.pdf": {"invoice_id": "invoice_2292.pdf", "vendor_name": "Brightline Logistics", "total": 1180.00,
                             "currency": "EUR", "po_reference": "PO-1190", "invoice_date": "2026-09-12",
                             "received_at": "2026-09-13T07:20:00Z"},
        "invoice_2293.pdf": {"invoice_id": "invoice_2293.pdf", "vendor_name": "Brightline Logistics", "total": 655.00,
                             "currency": "EUR", "po_reference": "PO-1191", "invoice_date": "2026-09-12",
                             "received_at": "2026-09-13T07:31:00Z"},
        "invoice_2294.pdf": {"invoice_id": "invoice_2294.pdf", "vendor_name": "Okta Print House", "total": 210.00,
                             "currency": "EUR", "po_reference": None, "invoice_date": "2026-09-10",
                             "received_at": "2026-09-13T08:02:00Z"},
    },
    "staged": {},
    "approvals": {},
    "posted": {},
}


def load() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    state = deepcopy(SEED)
    save(state)
    return state


def save(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def reset() -> dict:
    state = deepcopy(SEED)
    save(state)
    return state


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
