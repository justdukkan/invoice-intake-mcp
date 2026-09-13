"""The human side of the loop. In production this is a button in Slack,
the helpdesk or the ERP; here it is a CLI so the flow can be replayed.

    python -m invoice_intake_mcp.approve list
    python -m invoice_intake_mcp.approve APR-xxxx approved --by "j.doe"
"""

from __future__ import annotations

import argparse
import json
import os

from . import core, erp


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("approval_id", help="approval id, or 'list'")
    p.add_argument("decision", nargs="?", choices=["approved", "rejected"])
    p.add_argument("--by", default=os.environ.get("USER", "human"))
    a = p.parse_args()
    state = erp.load()
    if a.approval_id == "list":
        for ap in state["approvals"].values():
            st = state["staged"].get(ap["staged_id"], {})
            print(f'{ap["approval_id"]}  {ap["status"]:9}  {st.get("invoice_id","?"):18} '
                  f'{st.get("total","?")} {st.get("currency","")}  {ap["reason"]}')
        return
    if not a.decision:
        p.error("decision required: approved | rejected")
    os.environ["INVOICE_INTAKE_ACTOR"] = f"human:{a.by}"
    print(json.dumps(core.decide_approval(state, a.approval_id, a.decision, a.by)))


if __name__ == "__main__":
    main()
