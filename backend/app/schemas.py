"""The API contract. The frontend's `src/types.ts` mirrors these models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Source = Literal["invoice_data", "policy"]


# -- /ask -------------------------------------------------------------------

class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    # The client owns conversation state and sends recent turns for follow-ups
    # ("and only the inactive ones?"). The server stays stateless.
    history: list[Turn] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _alternating(self) -> "AskRequest":
        for i, turn in enumerate(self.history):
            if turn.role != ("user" if i % 2 == 0 else "assistant"):
                raise ValueError("history must alternate user/assistant, starting with user")
        return self


class FinalAnswer(BaseModel):
    """What the model must return as its final message (enforced by JSON schema)."""
    status: Literal["answered", "clarify", "declined"]
    answer: str
    sources: list[Source]
    route_rationale: str
    policy_sections: list[str]
    caveats: list[str]
    missing_information: str | None = None


class Route(BaseModel):
    declared: list[Source]
    observed: list[Source]
    rationale: str
    consistent: bool


class PolicyEvidence(BaseModel):
    section_id: str
    heading: str
    text: str
    cited_by: Literal["answer", "tool", "answer+tool"]


class ToolCallEvidence(BaseModel):
    tool: str
    source: Source | None
    args: dict[str, Any]
    ok: bool
    error: str | None = None
    result: dict[str, Any] | None = None


class FxRateUsed(BaseModel):
    currency: str
    rate_date: str
    rate: float


class Evidence(BaseModel):
    policy: list[PolicyEvidence]
    tool_calls: list[ToolCallEvidence]
    rows: list[dict[str, Any]]
    rows_truncated: bool
    fx_rates: list[FxRateUsed]
    fx_source: str | None


class Grounding(BaseModel):
    passed: bool
    ungrounded_numbers: list[str]
    unknown_invoices: list[str]
    unknown_sections: list[str]


class Meta(BaseModel):
    request_id: str
    mode: Literal["llm", "stub"]
    model: str | None
    prompt_version: str
    policy_version: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None


class AskResponse(BaseModel):
    status: Literal["answered", "clarify", "declined", "unverified"]
    answer: str
    missing_information: str | None
    caveats: list[str]
    route: Route
    evidence: Evidence
    grounding: Grounding
    meta: Meta


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool


# -- /summary ---------------------------------------------------------------

class CurrencyLine(BaseModel):
    currency: str
    invoice_count: int
    total_original: float
    total_eur: float


class StatusLine(BaseModel):
    vendor_status: str
    invoice_count: int
    total_eur: float
    vendors: list[str]


class ControlLine(BaseModel):
    key: str
    label: str
    invoice_count: int
    total_eur: float
    policy_section: str


class Summary(BaseModel):
    period: dict[str, str]
    invoice_count: int
    vendor_count: int
    total_eur: float
    gross_eur: float
    credit_notes_eur: float
    by_currency: list[CurrencyLine]
    by_vendor_status: list[StatusLine]
    controls: list[ControlLine]
    fx_basis: str
    policy: dict[str, str]
    source_hashes: dict[str, str]
    mode: Literal["llm", "stub"]
