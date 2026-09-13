# invoice-intake-mcp

<!-- mcp-name: io.github.justdukkan/invoice-intake-mcp -->

Reference implementation of the tool-design rules in
[Designing MCP servers and tools that agents can use safely](https://justdukkan.com/insights/mcp-server-and-tool-design/),
on a real back-office process: supplier invoice intake.

It is small on purpose. The fake ERP is a JSON file; everything else is the
shape we use in production systems.

## What it shows

| Rule | Where |
|---|---|
| Model the business operation, not the API | `erp_match_po`, `erp_stage_invoice`, not `PATCH /invoices` |
| Separate reads, drafts and commits | `--role read` server (reads + reversible staging) vs `--role commit` server (irreversible post) |
| Strict schemas | `Literal` enums for currency, ids not names, `found=false` as a valid empty result |
| Permissions on the server and credential | specialist agents get *read*; only the orchestrator connects to *commit* |
| Tool results are untrusted input | `invoices_extract` says so in its description; the eval set includes a prompt-injection case |
| Typed errors | `{"error": "approval_required" \| "validation_failed" \| "business_rule" \| "not_found", "retryable": bool, ...}` |
| Log for the auditor | `audit.jsonl`: actor, tool, ids, approval reference |
| Human checkpoint enforced server-side | `erp_post_invoice` refuses without a recorded approval, whatever the client believes |

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

python -m invoice_intake_mcp.orchestrator            # process the inbox
python -m invoice_intake_mcp.approve list            # see what is waiting
python -m invoice_intake_mcp.approve APR-xxxx approved --by cfo
python -m invoice_intake_mcp.orchestrator --resume   # post the approved item
cat audit.jsonl
```

Expected first run:

```
plan: 4 new invoices -> extract, resolve vendor, match, stage, post-or-approve
  invoice_2291.pdf: staged STG-… -> WAITING for APR-…        (7,420 EUR > 5,000 limit)
  invoice_2292.pdf: match=goods_not_received -> REVIEW
  invoice_2293.pdf: match=variance {"variance_pct": 2.34} -> REVIEW
  invoice_2294.pdf: no PO reference -> REVIEW (ask requester)
```

The orchestrator is deterministic so the flow replays without an API key.
An LLM belongs in the places marked in `orchestrator.py` (ambiguous vendor
candidates, free-text remarks, the note back to the requester), not in the
match rule, the tolerance or the approval limit.

## Use the servers from an MCP client

```json
{
  "mcpServers": {
    "invoice-intake-read":   { "command": "uvx", "args": ["invoice-intake-mcp", "--role", "read"] },
    "invoice-intake-commit": { "command": "uvx", "args": ["invoice-intake-mcp", "--role", "commit"] }
  }
}
```

Give an agent only the *read* server unless it is the orchestrator.

## Tests and evals

```bash
pytest                 # unit tests on the core operations and the gate
python evals/run.py    # replayable decision cases, run on every change
```

## Layout

```
invoice_intake_mcp/
  core.py          business operations + policy (tolerance, approval limit), pure functions
  server.py        the two MCP servers and their tool descriptions
  orchestrator.py  minimal hub over MCP stdio: read server for work, commit server for posting
  approve.py       the human decision, as a CLI
  audit.py         append-only JSONL audit log
  erp.py           fake ERP / inbox state (JSON file)
tests/             pytest
evals/             cases.jsonl + run.py
```

MIT. Built by [JustDukkan](https://justdukkan.com), AI solutions architecture.
