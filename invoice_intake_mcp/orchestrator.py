"""A minimal hub. It talks to the *read* server for everything up to
staging and to the *commit* server for posting, exactly like an LLM
orchestrator would, but with a deterministic plan so the flow is
replayable without an API key.

    python -m invoice_intake_mcp.orchestrator            # process the inbox
    python -m invoice_intake_mcp.orchestrator --resume   # post approved items

Where an LLM belongs: choosing among ambiguous vendor candidates, reading
free-text remarks on the invoice, drafting the note to the requester.
Where it does not: the match rule, the tolerance, the approval limit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _params(role: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "invoice_intake_mcp.server", "--role", role])


async def call(session: ClientSession, tool: str, **args) -> dict:
    res = await session.call_tool(tool, args)
    if res.structured_content is not None and not res.is_error:
        payload = res.structured_content.get("result", res.structured_content)
    else:
        payload = json.loads(res.content[0].text) if res.content else {}
    if res.is_error:
        # typed error: decide per category, never blind-retry
        raise RuntimeError(f"{tool} -> {payload}")
    return payload


async def process_inbox(read: ClientSession, commit: ClientSession) -> None:
    inbox = await call(read, "invoices_list_new")
    print(f"plan: {inbox['count']} new invoices -> extract, resolve vendor, match, stage, post-or-approve")
    for invoice_id in inbox["invoice_ids"]:
        ex = await call(read, "invoices_extract", invoice_id=invoice_id)
        f = ex["fields"]
        vend = await call(read, "erp_search_vendor", name=f["vendor_name"])
        if len(vend["candidates"]) != 1:
            print(f"  {invoice_id}: vendor '{f['vendor_name']}' -> {len(vend['candidates'])} candidates -> REVIEW")
            continue
        vendor_id = vend["candidates"][0]["vendor_id"]
        if not f["po_reference"]:
            print(f"  {invoice_id}: no PO reference -> REVIEW (ask requester)")
            continue
        m = await call(read, "erp_match_po", vendor_id=vendor_id, po_number=f["po_reference"],
                       total=f["total"], currency=f["currency"])
        if m["status"] != "matched":
            print(f"  {invoice_id}: match={m['status']} {json.dumps({k: v for k, v in m.items() if k != 'status'})} -> REVIEW")
            continue
        try:
            st = await call(read, "erp_stage_invoice", invoice_id=invoice_id, vendor_id=vendor_id,
                            po_number=f["po_reference"], total=f["total"], currency=f["currency"])
        except RuntimeError as exc:
            print(f"  {invoice_id}: {exc} -> REVIEW")
            continue
        if st["needs_approval"]:
            ap = await call(read, "approvals_request", staged_id=st["staged_id"],
                            reason=f"total {f['total']:.2f} {f['currency']} above limit {st['approval_limit']:.0f}")
            print(f"  {invoice_id}: staged {st['staged_id']} -> WAITING for {ap['approval_id']}")
            continue
        posted = await call(commit, "erp_post_invoice", staged_id=st["staged_id"])
        print(f"  {invoice_id}: matched -> posted {posted['posted_id']}")


async def resume(read: ClientSession, commit: ClientSession) -> None:
    from . import erp  # local state only to enumerate; a real hub would use a queue
    state = erp.load()
    for staged in list(state["staged"].values()):
        if not staged["approval_id"]:
            continue
        ap = await call(read, "approvals_get", approval_id=staged["approval_id"])
        status = ap["approval"]["status"]
        if status == "approved":
            posted = await call(commit, "erp_post_invoice", staged_id=staged["staged_id"])
            print(f"  {staged['invoice_id']}: approved by {ap['approval']['decided_by']} -> posted {posted['posted_id']}")
        elif status == "rejected":
            print(f"  {staged['invoice_id']}: rejected -> returned to requester, nothing posted")
        else:
            print(f"  {staged['invoice_id']}: still waiting for {staged['approval_id']}")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--resume", action="store_true")
    a = p.parse_args()
    async with AsyncExitStack() as stack:
        sessions = {}
        for role in ("read", "commit"):
            r, w = await stack.enter_async_context(stdio_client(_params(role)))
            s = await stack.enter_async_context(ClientSession(r, w))
            await s.initialize()
            sessions[role] = s
        if a.resume:
            await resume(sessions["read"], sessions["commit"])
        else:
            await process_inbox(sessions["read"], sessions["commit"])


if __name__ == "__main__":
    asyncio.run(main())
