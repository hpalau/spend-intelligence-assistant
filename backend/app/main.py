"""FastAPI app: GET /summary, POST /ask, GET /health."""

from __future__ import annotations

import logging
import os
from collections import defaultdict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .assistant import Assistant
from .data import FX_SOURCE, SpendData
from .engines import EngineError, LLMEngine, StubEngine
from .policy import Policy
from .schemas import AskRequest, AskResponse, ControlLine, CurrencyLine, ErrorBody, StatusLine, Summary
from .tools import PO_THRESHOLD_EUR, Toolbox

log = logging.getLogger("spend")

data = SpendData()
policy = Policy()
toolbox = Toolbox(data, policy)


def _choose_engine() -> LLMEngine | StubEngine:
    if os.environ.get("ASSISTANT_MODE") == "stub" or not os.environ.get("ANTHROPIC_API_KEY"):
        return StubEngine(toolbox)
    return LLMEngine(toolbox)


assistant = Assistant(toolbox, _choose_engine())

app = FastAPI(title="Spend Intelligence Assistant", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "mode": assistant.engine.mode, "policy_version": policy.version,
            "rules_current": toolbox.rules_current}


@app.get("/summary", response_model=Summary)
def summary() -> Summary:
    inv = data.invoices
    by_cur: dict[str, list] = defaultdict(lambda: [0, 0.0, 0.0])
    by_status: dict[str, list] = defaultdict(lambda: [0, 0.0, set()])
    for i in inv:
        c = by_cur[i.currency]
        c[0] += 1; c[1] += i.amount; c[2] += i.amount_eur  # noqa: E702
        s = by_status[i.vendor_status]
        s[0] += 1; s[1] += i.amount_eur; s[2].add(i.vendor_name)  # noqa: E702

    def control(key: str, label: str, section: str, rows: list) -> ControlLine:
        return ControlLine(key=key, label=label, policy_section=section, invoice_count=len(rows),
                           total_eur=round(sum(r.amount_eur for r in rows), 2))

    return Summary(
        period={"from": min(i.invoice_date for i in inv), "to": max(i.invoice_date for i in inv)},
        invoice_count=len(inv),
        vendor_count=len({i.vendor_id for i in inv}),
        total_eur=round(sum(i.amount_eur for i in inv), 2),
        gross_eur=round(sum(i.amount_eur for i in inv if i.amount > 0), 2),
        credit_notes_eur=round(sum(i.amount_eur for i in inv if i.amount < 0), 2),
        by_currency=[CurrencyLine(currency=k, invoice_count=v[0], total_original=round(v[1], 2), total_eur=round(v[2], 2))
                     for k, v in sorted(by_cur.items(), key=lambda kv: -kv[1][2])],
        by_vendor_status=[StatusLine(vendor_status=k, invoice_count=v[0], total_eur=round(v[1], 2), vendors=sorted(v[2]))
                          for k, v in sorted(by_status.items(), key=lambda kv: -kv[1][1])],
        controls=[
            control("missing_po", "No PO, EUR 1,000 or more", "2.1",
                    [i for i in inv if not i.po_number and i.amount_eur >= PO_THRESHOLD_EUR]),
            control("missing_approver", "No approver recorded", "3.1", [i for i in inv if not i.approved_by]),
            control("non_active_vendor", "Vendor not ACTIVE", "4.1", [i for i in inv if i.vendor_status != "ACTIVE"]),
            control("credit_notes", "Credit notes (negative amounts)", "6.1", [i for i in inv if i.amount < 0]),
        ],
        fx_basis=f"EUR-equivalent at the ECB reference rate on each invoice date (policy §5.1). Source: {FX_SOURCE}.",
        policy={"doc_id": policy.doc_id, "version": policy.version},
        source_hashes=data.source_hashes,
        mode=assistant.engine.mode,
    )


@app.post("/ask", response_model=AskResponse, responses={429: {"model": ErrorBody}, 502: {"model": ErrorBody}, 503: {"model": ErrorBody}})
def ask(req: AskRequest):
    try:
        return assistant.ask(req.question.strip(), req.history)
    except EngineError as e:
        log.warning("ask failed: %s %s", e.code, e.message)
        return JSONResponse(status_code=e.status_code,
                            content=ErrorBody(code=e.code, message=e.message, retryable=e.retryable).model_dump())
