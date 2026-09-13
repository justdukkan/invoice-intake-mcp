"""Business operations behind the MCP tools. Pure functions over the state
dict so they can be unit-tested and replayed without a transport.

Conventions (see https://justdukkan.com/insights/mcp-server-and-tool-design/):
- "not found" is a valid empty result, never an error
- errors are typed: {"error": <category>, "retryable": bool, "message": str, ...}
- reads never mutate; drafts are reversible; commits are gated server-side
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import erp
from .audit import log

# ---- policy: business rules live here, not in prompts ---------------------
TOLERANCE_PCT = 2.0          # invoice may differ from PO by up to 2 %
APPROVAL_LIMIT = 5000.00     # totals above this need a human approval to post
CURRENCIES = ("EUR", "USD", "GBP", "TRY")


class ToolFailure(Exception):
    """Raised for typed, non-empty-result failures. The server turns it into
    an MCP error result whose text is the structured payload."""

    def __init__(self, category: str, message: str, retryable: bool = False, **extra):
        self.payload = {"error": category, "retryable": retryable, "message": message, **extra}
        super().__init__(message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- reads -------------------------------------------------------------------
def search_vendor(state: dict, name: str) -> dict:
    q = name.strip().lower()
    hits = [{"vendor_id": v["vendor_id"], "name": v["name"], "status": v["status"]}
            for v in state["vendors"].values() if q and q in v["name"].lower()]
    return {"found": bool(hits), "candidates": hits}


def get_purchase_order(state: dict, po_number: str) -> dict:
    po = state["purchase_orders"].get(po_number)
    if po is None:
        return {"found": False, "message": "Query succeeded; no purchase order with this number."}
    return {"found": True, "purchase_order": po}


def list_new_invoices(state: dict, since: str | None = None) -> dict:
    items = [i for i in state["inbox"].values()
             if i["invoice_id"] not in state["posted"] and (since is None or i["received_at"] >= since)]
    return {"count": len(items), "invoice_ids": [i["invoice_id"] for i in items]}


def extract_invoice(state: dict, invoice_id: str) -> dict:
    doc = state["inbox"].get(invoice_id)
    if doc is None:
        return {"found": False, "message": "No document with this id in the inbox."}
    return {"found": True, "fields": doc}


def match_po(state: dict, vendor_id: str, po_number: str, total: float, currency: str) -> dict:
    if currency not in CURRENCIES:
        raise ToolFailure("validation_failed", f"currency must be one of {CURRENCIES}", field="currency")
    if total <= 0:
        raise ToolFailure("validation_failed", "total must be positive", field="total")
    vendor = state["vendors"].get(vendor_id)
    if vendor is None:
        raise ToolFailure("not_found", "vendor_id does not exist; use erp_search_vendor first", field="vendor_id")
    po = state["purchase_orders"].get(po_number)
    if po is None:
        return {"status": "no_po", "message": "No purchase order with this number."}
    if po["vendor_id"] != vendor_id:
        return {"status": "vendor_mismatch", "po_vendor_id": po["vendor_id"]}
    if po["currency"] != currency:
        return {"status": "currency_mismatch", "po_currency": po["currency"]}
    variance_pct = round((total - po["total"]) / po["total"] * 100, 2)
    if abs(variance_pct) > TOLERANCE_PCT:
        return {"status": "variance", "variance_pct": variance_pct, "po_total": po["total"]}
    if not po["goods_received"]:
        return {"status": "goods_not_received", "variance_pct": variance_pct}
    return {"status": "matched", "variance_pct": variance_pct, "three_way_match": True}


# ---- drafts (reversible) -------------------------------------------------------
def stage_invoice(state: dict, invoice_id: str, vendor_id: str, po_number: str, total: float, currency: str) -> dict:
    match = match_po(state, vendor_id, po_number, total, currency)
    if match["status"] != "matched":
        raise ToolFailure("business_rule", f"cannot stage: PO match status is '{match['status']}'", match=match)
    if state["vendors"][vendor_id]["status"] != "active":
        raise ToolFailure("business_rule", "vendor is blocked; route to procurement", vendor_id=vendor_id)
    staged_id = erp.new_id("STG")
    needs_approval = total > APPROVAL_LIMIT
    state["staged"][staged_id] = {
        "staged_id": staged_id, "invoice_id": invoice_id, "vendor_id": vendor_id, "po_number": po_number,
        "total": total, "currency": currency, "needs_approval": needs_approval, "approval_id": None,
        "created_at": _now(),
    }
    erp.save(state)
    log("erp_stage_invoice", {"staged_id": staged_id, "invoice_id": invoice_id, "needs_approval": needs_approval})
    return {"staged_id": staged_id, "needs_approval": needs_approval, "approval_limit": APPROVAL_LIMIT}


def request_approval(state: dict, staged_id: str, reason: str) -> dict:
    staged = state["staged"].get(staged_id)
    if staged is None:
        return {"found": False, "message": "No staged invoice with this id."}
    if staged["approval_id"]:
        return {"approval_id": staged["approval_id"], "status": state["approvals"][staged["approval_id"]]["status"]}
    approval_id = erp.new_id("APR")
    state["approvals"][approval_id] = {"approval_id": approval_id, "staged_id": staged_id, "reason": reason,
                                       "status": "pending", "decided_by": None, "decided_at": None}
    staged["approval_id"] = approval_id
    erp.save(state)
    log("approvals_request", {"approval_id": approval_id, "staged_id": staged_id, "reason": reason})
    return {"approval_id": approval_id, "status": "pending"}


def get_approval(state: dict, approval_id: str) -> dict:
    a = state["approvals"].get(approval_id)
    if a is None:
        return {"found": False, "message": "No approval with this id."}
    return {"found": True, "approval": a}


# ---- human decision (comes from outside the agent loop) -------------------------
def decide_approval(state: dict, approval_id: str, decision: str, approver: str) -> dict:
    a = state["approvals"].get(approval_id)
    if a is None:
        raise ToolFailure("not_found", "No approval with this id.")
    if decision not in ("approved", "rejected"):
        raise ToolFailure("validation_failed", "decision must be 'approved' or 'rejected'", field="decision")
    a.update({"status": decision, "decided_by": approver, "decided_at": _now()})
    erp.save(state)
    log("approvals_decide", {"approval_id": approval_id, "decision": decision, "approver": approver})
    return {"approval_id": approval_id, "status": decision}


# ---- commits (irreversible; gated server-side) ---------------------------------
def post_invoice(state: dict, staged_id: str) -> dict:
    staged = state["staged"].get(staged_id)
    if staged is None:
        raise ToolFailure("not_found", "No staged invoice with this id; stage it first.")
    if staged["needs_approval"]:
        approval = state["approvals"].get(staged["approval_id"] or "")
        if approval is None or approval["status"] != "approved":
            # server-side lock: holds even if the client "decides" it is fine
            raise ToolFailure("approval_required",
                              f"total {staged['total']:.2f} exceeds {APPROVAL_LIMIT:.0f}; "
                              "a human approval must be recorded before posting",
                              approval_id=staged.get("approval_id"),
                              approval_status=approval["status"] if approval else None)
    posted_id = erp.new_id("INV")
    state["posted"][staged["invoice_id"]] = {"posted_id": posted_id, **staged, "posted_at": _now()}
    state["purchase_orders"][staged["po_number"]]["status"] = "invoiced"
    del state["staged"][staged_id]
    erp.save(state)
    log("erp_post_invoice", {"posted_id": posted_id, "staged_id": staged_id, "invoice_id": staged["invoice_id"],
                             "approval_id": staged.get("approval_id")})
    return {"posted_id": posted_id, "invoice_id": staged["invoice_id"]}
