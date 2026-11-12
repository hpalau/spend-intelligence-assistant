"""Post-hoc checks on the model's answer, run before anything reaches the user.

The prompt asks the model to use only tool output and cite policy sections. This
module checks that it did, because a rule the model is merely asked to follow
will eventually be broken. Three checks:

  1. Every number in the answer matches a number in the evidence (tool output,
     the policy text, or the question itself).
  2. Every invoice number mentioned was returned by a tool.
  3. Every cited policy section exists.

A failed check does not hide the answer; it marks it `unverified` and the UI says
which figure could not be traced. The analyst sees the claim and the warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .policy import Policy

INVOICE_ID = re.compile(r"\bINV-\d+\b", re.IGNORECASE)
# Identifiers and references that contain digits but are not quantities.
NOT_QUANTITIES = [
    re.compile(r"\b(?:INV|PO|V|CC|FIN-POL)-[A-Z0-9-]+\b", re.IGNORECASE),
    re.compile(r"§\s*\d+(?:\.\d+)?"),
    re.compile(r"\b(?:section|clause|policy)\s+\d+(?:\.\d+)?", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b"),  # dates and months
    re.compile(r"\b20\d{2}\b"),  # years
    re.compile(r"^\s*\d+[.)]\s", re.MULTILINE),  # list numbering
    re.compile(r"\bv\d+(?:\.\d+)*\b", re.IGNORECASE),  # versions
]
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(k|m|mn|bn|million|thousand)?\b", re.IGNORECASE)
MULTIPLIER = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9}


@dataclass
class GroundingReport:
    ungrounded_numbers: list[str] = field(default_factory=list)
    unknown_invoices: list[str] = field(default_factory=list)
    unknown_sections: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.ungrounded_numbers or self.unknown_invoices or self.unknown_sections)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "ungrounded_numbers": self.ungrounded_numbers,
            "unknown_invoices": self.unknown_invoices,
            "unknown_sections": self.unknown_sections,
        }


def extract_numbers(text: str) -> list[tuple[str, float]]:
    for pattern in NOT_QUANTITIES:
        text = pattern.sub(" ", text)
    found = []
    for m in NUMBER.finditer(text):
        whole, frac, suffix = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        value = float(whole.replace(",", "") + frac) * MULTIPLIER.get(suffix, 1)
        found.append((m.group(0).strip(), value))
    return found


def collect_numbers(obj: Any, out: set[float]) -> None:
    """Every number anywhere in a tool result, plus the length of every list
    (so a count like '21 invoices' is grounded by a 21-row result)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        out.add(float(len(obj)))
        for v in obj:
            collect_numbers(v, out)
    elif isinstance(obj, str):
        for _, v in extract_numbers(obj):
            out.add(v)


def collect_invoice_ids(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            collect_invoice_ids(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_invoice_ids(v, out)
    elif isinstance(obj, str):
        out.update(m.upper() for m in INVOICE_ID.findall(obj))


def _is_grounded(value: float, known: Iterable[float]) -> bool:
    # Exact to the cent, or within 0.5% when the answer rounds (e.g. "EUR 1.58M").
    return any(abs(value - k) <= max(0.01, 0.005 * k) for k in known)


def check_answer(answer: str, cited_sections: list[str], tool_results: list[Any],
                 policy: Policy, question: str) -> GroundingReport:
    report = GroundingReport()

    known: set[float] = set()
    for r in tool_results:
        collect_numbers(r, known)
    collect_numbers(policy.raw, known)
    collect_numbers(question, known)

    for raw, value in extract_numbers(answer):
        if not _is_grounded(value, known):
            report.ungrounded_numbers.append(raw)

    returned: set[str] = set()
    for r in [*tool_results, question]:
        collect_invoice_ids(r, returned)
    report.unknown_invoices = sorted({i.upper() for i in INVOICE_ID.findall(answer)} - returned)

    report.unknown_sections = [s for s in cited_sections if policy.get(s) is None]
    return report
