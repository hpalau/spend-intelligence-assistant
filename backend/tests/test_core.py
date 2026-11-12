"""Tests for the deterministic layer: the parts that must be right regardless of the model."""

from app.data import SpendData
from app.grounding import check_answer, extract_numbers
from app.policy import Policy
from app.tools import ApprovalArgs, CheckInvoiceArgs, SpendSummaryArgs, Toolbox, required_approver

data, policy = SpendData(), Policy()
tools = Toolbox(data, policy)


def test_policy_sections_parsed():
    assert policy.version == "3.2"
    assert {"2.1", "3", "3.1", "4.1", "8.2"} <= set(policy.sections)
    assert "Finance Director" in policy.get("§3").text


def test_approval_band_boundaries_are_inclusive_at_upper_bound():
    assert required_approver(5000.00) == "Cost Center Owner"
    assert required_approver(5000.01) == "Department Head"
    assert required_approver(50000.00) == "Department Head"
    assert required_approver(50000.01) == "Finance Director"
    assert required_approver(250000.01) == "CFO + Procurement Excellence review"


def test_non_eur_amount_is_converted_before_banding():
    r = tools.approval_requirement(ApprovalArgs(amount=45000, currency="GBP", date="2025-06-02"))
    assert r["amount_eur"] > 50000 and r["required_approver"] == "Finance Director"


def test_weekend_invoice_uses_previous_fixing():
    amount_eur, rate, rate_date = data.fx.to_eur(100, "USD", "2025-03-09")  # a Sunday
    assert rate_date == "2025-03-07"


def test_totals_reconcile():
    by_vendor = tools.spend_summary(SpendSummaryArgs(group_by="vendor"))
    overall = tools.spend_summary(SpendSummaryArgs())
    assert round(sum(g["total_eur"] for g in by_vendor["groups"]), 2) == overall["matched_total_eur"]
    assert overall["matched_invoice_count"] == 140


def test_on_hold_vendor_blocks_payment():
    r = tools.check_invoice(CheckInvoiceArgs(invoice_number="INV-20118"))
    assert not r["payable_per_available_data"]
    assert "Vendor status" in r["blocking_issues"]


def test_number_extraction_ignores_identifiers():
    text = "INV-20118 under §3.1 in 2025: EUR 1,131,481.11 across 21 invoices (about 1.13M)"
    assert [v for _, v in extract_numbers(text)] == [1131481.11, 21, 1.13e6]


def test_grounding_flags_invented_figure():
    result = tools.spend_summary(SpendSummaryArgs())
    good = check_answer(f"Total is EUR {result['matched_total_eur']:,.2f}", [], [result], policy, "q")
    bad = check_answer("Total is EUR 7,250,000.00, see INV-99999", ["9.9"], [result], policy, "q")
    assert good.passed
    assert bad.ungrounded_numbers == ["7,250,000.00"]
    assert bad.unknown_invoices == ["INV-99999"] and bad.unknown_sections == ["9.9"]
