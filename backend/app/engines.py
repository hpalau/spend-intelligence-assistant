"""Two interchangeable engines that turn a question into a FinalAnswer plus the
tool calls made along the way.

  LLMEngine  - Claude decides the route and the tool calls (the real path).
  StubEngine - keyword rules decide them. Clearly labelled, for running without an
               API key. It exercises the same tools, evidence and grounding checks,
               so the rest of the system can be reviewed offline. It is not an
               evaluation of answer quality.

If an API key is configured and the API fails, the request fails visibly. It never
falls back to the stub silently: the analyst would see a stub answer labelled as
model output.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import anthropic

from .prompts import FINAL_ANSWER_SCHEMA, build_system_prompt
from .schemas import FinalAnswer, Turn
from .tools import TOOL_DEFINITIONS, Toolbox, ToolError

MAX_STEPS = 6  # model turns per question; a well-routed question needs 2-3


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    source: str | None
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class EngineOutput:
    final: FinalAnswer
    tool_calls: list[ToolCall]
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class EngineError(Exception):
    code: str
    message: str
    retryable: bool
    status_code: int = 502


def run_tool(toolbox: Toolbox, name: str, args: dict) -> ToolCall:
    try:
        result, source = toolbox.run(name, args)
        return ToolCall(name, args, source, result=result)
    except ToolError as e:
        return ToolCall(name, args, Toolbox.REGISTRY.get(name, (None, None))[1], error=str(e))


# ---------------------------------------------------------------------------


class LLMEngine:
    mode = "llm"

    def __init__(self, toolbox: Toolbox):
        self.toolbox = toolbox
        self.client = anthropic.Anthropic(max_retries=2, timeout=90.0)
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
        self.effort = os.environ.get("ANTHROPIC_EFFORT", "medium")
        self.system = build_system_prompt(toolbox.policy)

    def run(self, question: str, history: list[Turn]) -> EngineOutput:
        messages: list[dict[str, Any]] = [{"role": t.role, "content": t.content} for t in history]
        messages.append({"role": "user", "content": question})
        calls: list[ToolCall] = []
        tokens_in = tokens_out = 0

        for _ in range(MAX_STEPS):
            response = self._create(messages)
            tokens_in += response.usage.input_tokens
            tokens_out += response.usage.output_tokens

            if response.stop_reason == "refusal":
                return EngineOutput(_refusal(), calls, self.model, tokens_in, tokens_out)
            if response.stop_reason == "max_tokens":
                raise EngineError("model_truncated", "The model's response was cut off.", retryable=True)

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return EngineOutput(self._parse_final(response), calls, self.model, tokens_in, tokens_out)

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in tool_uses:
                call = run_tool(self.toolbox, block.name, dict(block.input or {}))
                calls.append(call)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(call.result) if call.error is None else f"Error: {call.error}",
                    "is_error": call.error is not None,
                })
            # All results for one turn go back in a single message.
            messages.append({"role": "user", "content": results})

        raise EngineError("too_many_steps", f"No answer after {MAX_STEPS} model turns.", retryable=False)

    def _create(self, messages: list[dict[str, Any]]):
        try:
            return self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=TOOL_DEFINITIONS,
                messages=messages,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": FINAL_ANSWER_SCHEMA},
                },
                cache_control={"type": "ephemeral"},
                # On a safety decline, the API retries on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise EngineError("llm_auth", "The API key was rejected.", retryable=False, status_code=503) from e
        except anthropic.RateLimitError as e:
            raise EngineError("llm_rate_limited", "The model is rate limited. Try again shortly.", retryable=True, status_code=429) from e
        except anthropic.BadRequestError as e:
            raise EngineError("llm_bad_request", f"The model rejected the request: {e.message}", retryable=False) from e
        except anthropic.APIStatusError as e:
            raise EngineError("llm_unavailable", f"Model API error ({e.status_code}).", retryable=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise EngineError("llm_unreachable", "Could not reach the model API.", retryable=True) from e

    @staticmethod
    def _parse_final(response) -> FinalAnswer:
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            return FinalAnswer.model_validate_json(text)
        except ValueError as e:
            raise EngineError("model_output_invalid", "The model's answer did not match the expected format.", retryable=True) from e


def _refusal() -> FinalAnswer:
    return FinalAnswer(
        status="declined", answer="The model declined to answer this question.", sources=[],
        route_rationale="Declined by the model's safety system.", policy_sections=[], caveats=[],
        missing_information=None,
    )


# ---------------------------------------------------------------------------


AMOUNT = re.compile(r"(EUR|GBP|USD|€|£|\$)?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?(k\b)?\s?(EUR|GBP|USD)?", re.IGNORECASE)
SYMBOLS = {"€": "EUR", "£": "GBP", "$": "USD"}


class StubEngine:
    """Keyword routing over the same tools. Labelled as a stub everywhere it surfaces."""

    mode = "stub"

    def __init__(self, toolbox: Toolbox):
        self.toolbox = toolbox
        self.countries = sorted({v["country"] for v in toolbox.data.vendors.values()})

    def run(self, question: str, history: list[Turn]) -> EngineOutput:
        q = question.lower()
        calls: list[ToolCall] = []

        def call(name: str, args: dict) -> dict | None:
            c = run_tool(self.toolbox, name, args)
            calls.append(c)
            return c.result

        other_year = re.search(r"\b(20(?!25)\d{2})\b", q)
        invoice = re.search(r"\binv-\d+\b", q)
        country = next((c for c in self.countries if c.lower() in q), None)

        if other_year:
            return EngineOutput(_decline(f"The dataset covers 2025 only; there is no data for {other_year.group(1)}."), calls)

        if not invoice and re.search(r"\b(the|this|that) invoice\b", q):
            return EngineOutput(FinalAnswer(
                status="clarify", answer="Which invoice do you mean? Please give its invoice number (it starts with INV-).",
                sources=[], route_rationale="The question refers to an invoice without identifying it.",
                policy_sections=[], caveats=[], missing_information=None), calls)

        if invoice:
            r = call("check_invoice", {"invoice_number": invoice.group(0).upper()})
            if r is None:
                return EngineOutput(_decline(f"{invoice.group(0).upper()} is not in the 2025 dataset."), calls)
            lines = [f"- {c['rule']} (§{c['policy_section']}): {c['status']}. {c['detail']}" for c in r["checks"]]
            verdict = "Blocked: " + ", ".join(r["blocking_issues"]) if r["blocking_issues"] else "No blocking issue found in the available data."
            return EngineOutput(_answer(
                f"{r['invoice']['invoice_number']}: {verdict}\n" + "\n".join(lines),
                ["invoice_data", "policy"], "Invoice-specific question: checked the invoice against policy rules.",
                sorted({c["policy_section"] for c in r["checks"]})), calls)

        if "approv" in q and (m := AMOUNT.search(question)) and m.group(2):
            amount = float(m.group(2).replace(",", "")) * (1000 if m.group(3) else 1)
            cur = (m.group(1) or m.group(4) or "EUR").upper()
            r = call("approval_requirement", {"amount": amount, "currency": SYMBOLS.get(cur, cur)})
            text = f"An invoice of {r['currency']} {r['amount']:,.2f} (EUR {r['amount_eur']:,.2f}) requires approval by: {r['required_approver']}."
            if r["fx_note"]:
                text += f" {r['fx_note']}"
            return EngineOutput(_answer(text, ["policy"], "Approval authority is defined by the policy thresholds.", ["3"]), calls)

        if "top" in q and "vendor" in q:
            n = int(m.group(1)) if (m := re.search(r"top\s+(\d+)", q)) else 5
            r = call("spend_summary", {"group_by": "vendor", "top_n": n})
            lines = [f"{g['rank']}. {g['key']}: EUR {g['total_eur']:,.2f} ({g['invoice_count']} invoices)" for g in r["groups"]]
            caveats = _status_caveats(self.toolbox, r)
            return EngineOutput(_answer(f"Top {n} vendors by net 2025 spend (EUR-equivalent):\n" + "\n".join(lines),
                                        ["invoice_data", "policy"] if caveats else ["invoice_data"],
                                        "Spend ranking needs the invoice data; vendor status is checked against policy.",
                                        ["4.1"] if caveats else [], caveats), calls)

        if country:
            r = call("spend_summary", {"group_by": "vendor", "filters": {"countries": [country]}})
            caveats = _status_caveats(self.toolbox, r)
            lines = [f"- {g['key']}: EUR {g['total_eur']:,.2f}" for g in r["groups"]]
            return EngineOutput(_answer(
                f"Total net spend with vendors based in {country}: EUR {r['matched_total_eur']:,.2f} across {r['matched_invoice_count']} invoices.\n" + "\n".join(lines),
                ["invoice_data", "policy"] if caveats else ["invoice_data"],
                "Spend by vendor country needs the invoice data joined to the vendor master; vendor status is checked against policy.",
                ["4.1"] if caveats else [], caveats), calls)

        if re.search(r"(missing|without|no)\s+(a\s+)?po\b", q):
            r = call("find_invoices", {"filters": {"has_po": False, "min_amount_eur": 1000}, "limit": 200})
            return EngineOutput(_answer(
                f"{r['total_matching']} invoices of EUR 1,000 or more have no PO (EUR {r['total_eur_of_matching']:,.2f} in total). Policy treats these as maverick spend to flag to the Cost Center owner.",
                ["invoice_data", "policy"], "Needs the invoice data filtered by the policy's PO threshold.", ["2.1", "2.2"]), calls)

        if re.search(r"(missing|without|no)\s+(an\s+)?approv", q):
            r = call("find_invoices", {"filters": {"has_approver": False}, "limit": 200})
            return EngineOutput(_answer(
                f"{r['total_matching']} invoices have no approver recorded (EUR {r['total_eur_of_matching']:,.2f}). Under policy these must be held and may not be paid.",
                ["invoice_data", "policy"], "Needs the invoice data and the approval-evidence rule.", ["3.1"]), calls)

        if "total" in q and "spend" in q:
            r = call("spend_summary", {"group_by": "currency"})
            return EngineOutput(_answer(f"Total net 2025 spend: EUR {r['matched_total_eur']:,.2f} across {r['matched_invoice_count']} invoices.",
                                        ["invoice_data"], "Total spend needs the invoice data.", []), calls)

        return EngineOutput(_decline(
            "The offline stub recognises only a few question shapes (top vendors, spend by country, approval thresholds, "
            "missing PO or approver, a specific invoice). Set ANTHROPIC_API_KEY to use the model."), calls)


def _status_caveats(toolbox: Toolbox, summary: dict) -> list[str]:
    numbers = [n for g in summary["groups"] for n in g["invoice_numbers"]]
    flagged = sorted({toolbox.data.by_number[n].vendor_name + " is " + toolbox.data.by_number[n].vendor_status
                      for n in numbers if toolbox.data.by_number[n].vendor_status != "ACTIVE"})
    return [f"{f}: payments require escalation to Procurement Excellence (§4.1)." for f in flagged]


def _answer(text: str, sources: list[str], rationale: str, sections: list[str], caveats: list[str] | None = None) -> FinalAnswer:
    return FinalAnswer(status="answered", answer=text, sources=sources, route_rationale=rationale,
                       policy_sections=sections, caveats=caveats or [], missing_information=None)


def _decline(missing: str) -> FinalAnswer:
    return FinalAnswer(status="declined", answer="I can't answer that from the available data and policy.", sources=[],
                       route_rationale="The question needs information outside the invoice data and policy.",
                       policy_sections=[], caveats=[], missing_information=missing)

