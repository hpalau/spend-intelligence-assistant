"""The only way the assistant can touch invoice data.

Design choice: a small set of typed tools, not text-to-SQL. The model chooses a
tool and its arguments; deterministic Python does every calculation and returns
the exact invoice rows used. That gives three properties text-to-SQL would not:
  - every figure in an answer can be traced to rows (evidence is produced here,
    never by the model);
  - arguments are validated before anything runs;
  - a question the tools cannot express is declined rather than improvised.
The cost is flexibility, which is the right trade for finance data.

Each tool returns a JSON-serialisable dict. `source` tags which kind of evidence
it produces, so the route the user sees is derived from what actually ran.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .data import Invoice, SpendData
from .policy import Policy

# Rules below are hand-encoded from this policy version. If the loaded policy has a
# different version, compliance checks are reported as stale rather than applied.
RULES_POLICY_VERSION = "3.2"

PO_THRESHOLD_EUR = 1000.00  # §2.1
APPROVAL_BANDS = [  # §3: (upper bound inclusive, required approver)
    (5000.00, "Cost Center Owner"),
    (50000.00, "Department Head"),
    (250000.00, "Finance Director"),
    (float("inf"), "CFO + Procurement Excellence review"),
]
ROW_LIMIT = 200

GroupBy = Literal["vendor", "country", "vendor_status", "currency", "cost_center", "category", "month", "none"]


class Filters(BaseModel):
    vendor_ids: list[str] | None = None
    vendor_name_contains: str | None = None
    countries: list[str] | None = None
    vendor_statuses: list[Literal["ACTIVE", "INACTIVE", "ON_HOLD"]] | None = None
    currencies: list[Literal["EUR", "GBP", "USD"]] | None = None
    cost_centers: list[str] | None = None
    categories: list[str] | None = None
    date_from: str | None = Field(None, description="YYYY-MM-DD inclusive")
    date_to: str | None = Field(None, description="YYYY-MM-DD inclusive")
    has_po: bool | None = None
    has_approver: bool | None = None
    min_amount_eur: float | None = None
    max_amount_eur: float | None = None
    credit_notes: Literal["include", "exclude", "only"] = "include"

    def match(self, inv: Invoice) -> bool:
        lower = lambda xs: {x.lower() for x in xs}  # noqa: E731
        checks = [
            self.vendor_ids is None or inv.vendor_id in self.vendor_ids,
            self.vendor_name_contains is None or self.vendor_name_contains.lower() in inv.vendor_name.lower(),
            self.countries is None or inv.vendor_country.lower() in lower(self.countries),
            self.vendor_statuses is None or inv.vendor_status in self.vendor_statuses,
            self.currencies is None or inv.currency in self.currencies,
            self.cost_centers is None or inv.cost_center in self.cost_centers,
            self.categories is None or inv.vendor_category.lower() in lower(self.categories),
            self.date_from is None or inv.invoice_date >= self.date_from,
            self.date_to is None or inv.invoice_date <= self.date_to,
            self.has_po is None or (inv.po_number is not None) == self.has_po,
            self.has_approver is None or (inv.approved_by is not None) == self.has_approver,
            self.min_amount_eur is None or inv.amount_eur >= self.min_amount_eur,
            self.max_amount_eur is None or inv.amount_eur <= self.max_amount_eur,
            self.credit_notes == "include"
            or (self.credit_notes == "exclude" and inv.amount >= 0)
            or (self.credit_notes == "only" and inv.amount < 0),
        ]
        return all(checks)


class SpendSummaryArgs(BaseModel):
    group_by: GroupBy = "none"
    filters: Filters = Filters()
    top_n: int | None = Field(None, ge=1, le=50)


class FindInvoicesArgs(BaseModel):
    filters: Filters = Filters()
    sort_by: Literal["amount_eur_desc", "amount_eur_asc", "date_asc", "date_desc"] = "amount_eur_desc"
    limit: int = Field(50, ge=1, le=ROW_LIMIT)


class CheckInvoiceArgs(BaseModel):
    invoice_number: str


class ApprovalArgs(BaseModel):
    amount: float = Field(gt=0)
    currency: Literal["EUR", "GBP", "USD"] = "EUR"
    date: str | None = Field(None, description="YYYY-MM-DD; needed to convert non-EUR amounts")


def _group_key(inv: Invoice, group_by: GroupBy) -> str:
    return {
        "vendor": lambda: f"{inv.vendor_name} ({inv.vendor_id})",
        "country": lambda: inv.vendor_country,
        "vendor_status": lambda: inv.vendor_status,
        "currency": lambda: inv.currency,
        "cost_center": lambda: inv.cost_center,
        "category": lambda: inv.vendor_category,
        "month": lambda: inv.invoice_date[:7],
        "none": lambda: "all",
    }[group_by]()


def required_approver(amount_eur: float) -> str:
    for upper, role in APPROVAL_BANDS:
        if abs(amount_eur) <= upper:
            return role
    raise AssertionError("unreachable")


class Toolbox:
    def __init__(self, data: SpendData, policy: Policy):
        self.data = data
        self.policy = policy
        self.rules_current = policy.version == RULES_POLICY_VERSION

    # -- tools ---------------------------------------------------------------

    def spend_summary(self, args: SpendSummaryArgs) -> dict:
        rows = [inv for inv in self.data.invoices if args.filters.match(inv)]
        groups: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"total_eur": 0.0, "invoice_count": 0, "by_currency": defaultdict(float), "invoice_numbers": []}
        )
        for inv in rows:
            g = groups[_group_key(inv, args.group_by)]
            g["total_eur"] += inv.amount_eur
            g["invoice_count"] += 1
            g["by_currency"][inv.currency] += inv.amount
            g["invoice_numbers"].append(inv.invoice_number)
        ranked = sorted(groups.items(), key=lambda kv: kv[1]["total_eur"], reverse=True)
        if args.top_n:
            ranked = ranked[: args.top_n]
        out_groups = [
            {
                "rank": i + 1,
                "key": key,
                "total_eur": round(g["total_eur"], 2),
                "invoice_count": g["invoice_count"],
                "by_currency_original": {c: round(v, 2) for c, v in sorted(g["by_currency"].items())},
                "invoice_numbers": g["invoice_numbers"],
            }
            for i, (key, g) in enumerate(ranked)
        ]
        credit_notes = [inv.invoice_number for inv in rows if inv.amount < 0]
        return {
            "matched_invoice_count": len(rows),
            "matched_total_eur": round(sum(inv.amount_eur for inv in rows), 2),
            "groups": out_groups,
            "credit_notes_in_scope": credit_notes,
            "basis": "Net of credit notes unless filtered out; EUR-equivalent at ECB rate on invoice date (policy §5.1).",
        }

    def find_invoices(self, args: FindInvoicesArgs) -> dict:
        rows = [inv for inv in self.data.invoices if args.filters.match(inv)]
        key, reverse = {
            "amount_eur_desc": (lambda i: i.amount_eur, True),
            "amount_eur_asc": (lambda i: i.amount_eur, False),
            "date_asc": (lambda i: i.invoice_date, False),
            "date_desc": (lambda i: i.invoice_date, True),
        }[args.sort_by]
        rows.sort(key=key, reverse=reverse)
        return {
            "total_matching": len(rows),
            "returned": min(len(rows), args.limit),
            "total_eur_of_matching": round(sum(i.amount_eur for i in rows), 2),
            "rows": [i.as_row() for i in rows[: args.limit]],
        }

    def check_invoice(self, args: CheckInvoiceArgs) -> dict:
        inv = self.data.by_number.get(args.invoice_number.strip().upper())
        if inv is None:
            raise LookupError(f"Invoice {args.invoice_number} is not in the 2025 dataset")
        if not self.rules_current:
            return {
                "invoice": inv.as_row(),
                "checks": [],
                "stale_rules": f"Rules encode policy v{RULES_POLICY_VERSION}; loaded policy is v{self.policy.version}. Not applied.",
            }
        checks = []
        if inv.amount < 0:
            checks.append(_check("Credit note matching", "6.1", "cannot_verify",
                                 "Negative amount: must be matched to an original invoice. The data has no link field, so matching cannot be verified."))
        elif inv.amount_eur >= PO_THRESHOLD_EUR:
            checks.append(_check("PO required", "2.1", "pass" if inv.po_number else "fail",
                                 f"PO {inv.po_number} present." if inv.po_number else
                                 "No PO for an invoice at or above EUR 1,000: maverick spend, flag to Cost Center owner (§2.2)."))
        checks.append(_check("Approval recorded", "3.1", "pass" if inv.approved_by else "fail",
                             f"Approved by {inv.approved_by}." if inv.approved_by else
                             "No approver record: invoice must be held and may not be paid."))
        role = required_approver(inv.amount_eur)
        checks.append(_check("Approver authority", "3", "cannot_verify",
                             f"EUR {abs(inv.amount_eur):,.2f} requires a {role}. The data records a username, not a role, so authority cannot be verified."))
        checks.append(_check("Vendor status", "4.1", "pass" if inv.vendor_status == "ACTIVE" else "fail",
                             f"Vendor is {inv.vendor_status}." + ("" if inv.vendor_status == "ACTIVE" else
                             " Escalate to Procurement Excellence before processing.")))
        checks.append(_check("Segregation of duties", "7.1", "cannot_verify",
                             "The data has no requester field, so self-approval cannot be checked."))
        blocking = [c["rule"] for c in checks if c["status"] == "fail"]
        return {
            "invoice": inv.as_row(),
            "required_approver_role": role,
            "checks": checks,
            "blocking_issues": blocking,
            "payable_per_available_data": not blocking,
        }

    def approval_requirement(self, args: ApprovalArgs) -> dict:
        fx_note = None
        if args.currency == "EUR":
            amount_eur = round(args.amount, 2)
        else:
            on_date = args.date or self.data.fx.latest_date
            if not args.date:
                fx_note = f"No date given; used the latest available ECB rate ({on_date})."
            amount_eur, rate, rate_date = self.data.fx.to_eur(args.amount, args.currency, on_date)
            fx_note = (fx_note or "") + f" Converted at {rate} {args.currency}/EUR (ECB, {rate_date})."
        return {
            "amount": args.amount,
            "currency": args.currency,
            "amount_eur": amount_eur,
            "required_approver": required_approver(amount_eur),
            "policy_section": "3",
            "fx_note": fx_note.strip() if fx_note else None,
            "note": "Bands are inclusive at the upper bound (e.g. EUR 50,000.00 is Department Head; 50,000.01 is Finance Director).",
        }

    # -- dispatch ------------------------------------------------------------

    REGISTRY = {
        "spend_summary": (SpendSummaryArgs, "invoice_data"),
        "find_invoices": (FindInvoicesArgs, "invoice_data"),
        "check_invoice": (CheckInvoiceArgs, "invoice_data"),
        "approval_requirement": (ApprovalArgs, "policy"),
    }

    def run(self, name: str, raw_args: dict) -> tuple[dict, str]:
        """Validates and executes a tool. Returns (result, source). Raises ToolError."""
        if name not in self.REGISTRY:
            raise ToolError(f"Unknown tool {name!r}")
        model, source = self.REGISTRY[name]
        try:
            args = model.model_validate(raw_args or {})
        except ValidationError as e:
            raise ToolError(f"Invalid arguments for {name}: {e.errors(include_url=False)}") from e
        try:
            return getattr(self, name)(args), source
        except (LookupError, ValueError) as e:
            raise ToolError(str(e)) from e


class ToolError(Exception):
    pass


def _check(rule: str, section: str, status: str, detail: str) -> dict:
    return {"rule": rule, "policy_section": section, "status": status, "detail": detail}


_FILTERS_SCHEMA = {
    "type": "object",
    "description": "All fields optional; omitted fields do not filter. Credit notes are included by default (net spend).",
    "properties": {
        "vendor_ids": {"type": "array", "items": {"type": "string"}},
        "vendor_name_contains": {"type": "string"},
        "countries": {"type": "array", "items": {"type": "string"}, "description": "Vendor country, e.g. Spain, UK"},
        "vendor_statuses": {"type": "array", "items": {"enum": ["ACTIVE", "INACTIVE", "ON_HOLD"]}},
        "currencies": {"type": "array", "items": {"enum": ["EUR", "GBP", "USD"]}},
        "cost_centers": {"type": "array", "items": {"type": "string"}},
        "categories": {"type": "array", "items": {"type": "string"}, "description": "Vendor category from vendor master"},
        "date_from": {"type": "string", "description": "YYYY-MM-DD inclusive"},
        "date_to": {"type": "string", "description": "YYYY-MM-DD inclusive"},
        "has_po": {"type": "boolean"},
        "has_approver": {"type": "boolean"},
        "min_amount_eur": {"type": "number"},
        "max_amount_eur": {"type": "number"},
        "credit_notes": {"enum": ["include", "exclude", "only"]},
    },
    "additionalProperties": False,
}

TOOL_DEFINITIONS = [
    {
        "name": "spend_summary",
        "description": "Aggregate invoice spend in EUR-equivalent, optionally grouped and ranked. Use for totals, rankings and breakdowns. Returns totals, original-currency breakdowns and the invoice numbers behind each figure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by": {"enum": ["vendor", "country", "vendor_status", "currency", "cost_center", "category", "month", "none"]},
                "filters": _FILTERS_SCHEMA,
                "top_n": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "find_invoices",
        "description": "List individual invoices matching filters (e.g. missing PO, missing approver, vendor status). Returns full rows including EUR conversion.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": _FILTERS_SCHEMA,
                "sort_by": {"enum": ["amount_eur_desc", "amount_eur_asc", "date_asc", "date_desc"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": ROW_LIMIT},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "check_invoice",
        "description": "Check one invoice against the procurement policy rules (PO, approval record, approver band, vendor status, credit notes, segregation of duties). Each check is pass, fail or cannot_verify.",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_number": {"type": "string", "description": "e.g. INV-20042"}},
            "required": ["invoice_number"],
            "additionalProperties": False,
        },
    },
    {
        "name": "approval_requirement",
        "description": "Who must approve an invoice of a given amount under policy §3. Converts non-EUR amounts at the ECB rate for the given date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "exclusiveMinimum": 0},
                "currency": {"enum": ["EUR", "GBP", "USD"]},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["amount"],
            "additionalProperties": False,
        },
    },
]
