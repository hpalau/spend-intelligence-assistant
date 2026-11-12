"""Question in, auditable answer out.

Orchestrates one /ask: run the engine, check grounding, build the evidence the UI
shows, and append an audit record. The evidence is assembled here from what the
tools actually returned, not from anything the model wrote.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .data import FX_SOURCE
from .engines import EngineOutput, LLMEngine, StubEngine
from .grounding import check_answer
from .prompts import PROMPT_VERSION
from .schemas import (AskResponse, Evidence, FxRateUsed, Grounding, Meta, PolicyEvidence, Route,
                      ToolCallEvidence, Turn)
from .tools import ROW_LIMIT, Toolbox

AUDIT_LOG = Path(__file__).resolve().parent.parent / "logs" / "ask_audit.jsonl"


class Assistant:
    def __init__(self, toolbox: Toolbox, engine: LLMEngine | StubEngine):
        self.toolbox = toolbox
        self.engine = engine

    def ask(self, question: str, history: list[Turn]) -> AskResponse:
        started = time.monotonic()
        request_id = uuid.uuid4().hex[:12]
        out = self.engine.run(question, history)  # raises EngineError on failure
        response = self._build_response(question, out, request_id, started)
        self._audit(question, history, out, response)
        return response

    def _build_response(self, question: str, out: EngineOutput, request_id: str, started: float) -> AskResponse:
        final = out.final
        results = [c.result for c in out.tool_calls if c.result is not None]
        grounding = check_answer(final.answer + "\n" + "\n".join(final.caveats),
                                 final.policy_sections, results, self.toolbox.policy, question)

        observed = {c.source for c in out.tool_calls if c.result is not None and c.source}
        if final.policy_sections:
            observed.add("policy")
        declared = sorted(set(final.sources))

        status = final.status
        if status == "answered" and not grounding.passed:
            status = "unverified"

        rows, truncated = self._rows(out)
        return AskResponse(
            status=status,
            answer=final.answer,
            missing_information=final.missing_information,
            caveats=final.caveats,
            route=Route(declared=declared, observed=sorted(observed), rationale=final.route_rationale,
                        consistent=set(declared) == observed),
            evidence=Evidence(
                policy=self._policy_evidence(final.policy_sections, results),
                tool_calls=[ToolCallEvidence(tool=c.tool, source=c.source, args=c.args, ok=c.error is None,
                                             error=c.error, result=c.result) for c in out.tool_calls],
                rows=rows,
                rows_truncated=truncated,
                fx_rates=self._fx_rates(rows),
                fx_source=FX_SOURCE if any(r["currency"] != "EUR" for r in rows) else None,
            ),
            grounding=Grounding(**grounding.as_dict()),
            meta=Meta(
                request_id=request_id, mode=self.engine.mode, model=out.model, prompt_version=PROMPT_VERSION,
                policy_version=self.toolbox.policy.version, latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=out.input_tokens, output_tokens=out.output_tokens,
            ),
        )

    def _rows(self, out: EngineOutput) -> tuple[list[dict], bool]:
        """Every invoice row behind the answer, in the order tools returned them."""
        numbers: list[str] = []
        for c in out.tool_calls:
            r = c.result or {}
            numbers += [row["invoice_number"] for row in r.get("rows", [])]
            numbers += [n for g in r.get("groups", []) for n in g["invoice_numbers"]]
            if "invoice" in r:
                numbers.append(r["invoice"]["invoice_number"])
        unique = list(dict.fromkeys(numbers))
        data = self.toolbox.data.by_number
        return [data[n].as_row() for n in unique[:ROW_LIMIT]], len(unique) > ROW_LIMIT

    def _policy_evidence(self, cited: list[str], results: list[dict]) -> list[PolicyEvidence]:
        from_tools: set[str] = set()
        for r in results:
            from_tools.update(c["policy_section"] for c in r.get("checks", []))
            if "policy_section" in r:
                from_tools.add(r["policy_section"])
        from_answer = {s.lstrip("§").strip() for s in cited}
        out = []
        for sid in sorted(from_answer | from_tools, key=lambda s: [int(p) for p in s.split(".") if p.isdigit()]):
            section = self.toolbox.policy.get(sid)
            if section is None:
                continue  # reported by the grounding check instead
            by = "answer+tool" if sid in from_answer and sid in from_tools else ("answer" if sid in from_answer else "tool")
            out.append(PolicyEvidence(section_id=sid, heading=section.heading, text=section.text, cited_by=by))
        return out

    @staticmethod
    def _fx_rates(rows: list[dict]) -> list[FxRateUsed]:
        seen = {(r["currency"], r["fx_rate_date"], r["fx_rate"]) for r in rows if r["currency"] != "EUR"}
        return [FxRateUsed(currency=c, rate_date=d, rate=x) for c, d, x in sorted(seen)]

    def _audit(self, question: str, history: list[Turn], out: EngineOutput, response: AskResponse) -> None:
        """Append-only lineage record (policy §8.2): enough to reproduce how an answer was produced."""
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "request_id": response.meta.request_id,
            "question": question,
            "history_turns": len(history),
            "mode": response.meta.mode,
            "model": response.meta.model,
            "prompt_version": PROMPT_VERSION,
            "policy_version": self.toolbox.policy.version,
            "source_hashes": self.toolbox.data.source_hashes,
            "tool_calls": [{"tool": c.tool, "args": c.args, "ok": c.error is None, "error": c.error} for c in out.tool_calls],
            "status": response.status,
            "answer": response.answer,
            "grounding": response.grounding.model_dump(),
        }
        AUDIT_LOG.parent.mkdir(exist_ok=True)
        with AUDIT_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
