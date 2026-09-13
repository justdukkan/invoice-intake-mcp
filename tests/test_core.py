import pytest

from invoice_intake_mcp import core, erp


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(erp, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("invoice_intake_mcp.audit.AUDIT_PATH", tmp_path / "audit.jsonl")
    return erp.reset()


def test_not_found_is_a_valid_empty_result(state):
    assert core.get_purchase_order(state, "PO-0000") == {
        "found": False, "message": "Query succeeded; no purchase order with this number."}
    assert core.search_vendor(state, "nobody") == {"found": False, "candidates": []}


def test_match_statuses(state):
    assert core.match_po(state, "V-1001", "PO-1187", 7420.0, "EUR")["status"] == "matched"
    assert core.match_po(state, "V-1002", "PO-1191", 655.0, "EUR")["status"] == "variance"      # 2.34 %
    assert core.match_po(state, "V-1002", "PO-1190", 1180.0, "EUR")["status"] == "goods_not_received"
    assert core.match_po(state, "V-1001", "PO-1191", 640.0, "EUR")["status"] == "vendor_mismatch"
    assert core.match_po(state, "V-1001", "PO-9999", 1.0, "EUR")["status"] == "no_po"


def test_validation_errors_are_typed(state):
    with pytest.raises(core.ToolFailure) as e:
        core.match_po(state, "V-1001", "PO-1187", 7420.0, "XXX")
    assert e.value.payload["error"] == "validation_failed"
    assert e.value.payload["field"] == "currency"
    assert e.value.payload["retryable"] is False


def test_small_invoice_posts_without_approval(state):
    st = core.stage_invoice(state, "invoice_x", "V-1002", "PO-1191", 640.0, "EUR")
    assert st["needs_approval"] is False
    posted = core.post_invoice(state, st["staged_id"])
    assert posted["posted_id"].startswith("INV-")
    assert state["purchase_orders"]["PO-1191"]["status"] == "invoiced"


def test_large_invoice_is_gated_server_side(state):
    st = core.stage_invoice(state, "invoice_2291.pdf", "V-1001", "PO-1187", 7420.0, "EUR")
    assert st["needs_approval"] is True
    with pytest.raises(core.ToolFailure) as e:
        core.post_invoice(state, st["staged_id"])          # no approval yet
    assert e.value.payload["error"] == "approval_required"

    ap = core.request_approval(state, st["staged_id"], "over limit")
    with pytest.raises(core.ToolFailure):
        core.post_invoice(state, st["staged_id"])          # pending is not approved

    core.decide_approval(state, ap["approval_id"], "rejected", "cfo")
    with pytest.raises(core.ToolFailure):
        core.post_invoice(state, st["staged_id"])          # rejected stays blocked

    core.decide_approval(state, ap["approval_id"], "approved", "cfo")
    assert core.post_invoice(state, st["staged_id"])["invoice_id"] == "invoice_2291.pdf"


def test_blocked_vendor_cannot_be_staged(state):
    state["purchase_orders"]["PO-X"] = {"po_number": "PO-X", "vendor_id": "V-1003", "total": 210.0,
                                        "currency": "EUR", "goods_received": True, "status": "open"}
    with pytest.raises(core.ToolFailure) as e:
        core.stage_invoice(state, "invoice_2294.pdf", "V-1003", "PO-X", 210.0, "EUR")
    assert e.value.payload["error"] == "business_rule"


def test_audit_log_records_commits(state, tmp_path):
    st = core.stage_invoice(state, "invoice_x", "V-1002", "PO-1191", 640.0, "EUR")
    core.post_invoice(state, st["staged_id"])
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert [l for l in lines if '"erp_post_invoice"' in l]
