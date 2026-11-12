"""Loads the three source files into memory and converts every invoice to EUR.

Everything downstream reads from `SpendData`. Nothing here calls an LLM.

FX: policy §5.1 says non-EUR invoices are converted at the ECB reference rate on
the invoice date. The pack ships no rates, so `ecb_eur_rates_2025.csv` is a snapshot
of the ECB's published historical series (USD, GBP). Weekends and holidays have no
fixing; we use the most recent fixing on or before the invoice date and record which
date was used, so every converted figure is traceable (§8.2).
"""

from __future__ import annotations

import bisect
import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INVOICES_FILE = DATA_DIR / "invoices_2025_clean.csv"
VENDORS_FILE = DATA_DIR / "vendor_master.csv"
POLICY_FILE = DATA_DIR / "procurement_policy.md"
FX_FILE = DATA_DIR / "ecb_eur_rates_2025.csv"

FX_SOURCE = "ECB euro foreign exchange reference rates (eurofxref-hist), snapshot in data/ecb_eur_rates_2025.csv"


@dataclass(frozen=True)
class Invoice:
    invoice_number: str
    invoice_date: str
    vendor_id: str
    vendor_name: str
    description: str
    amount: float
    currency: str
    cost_center: str
    po_number: str | None
    approved_by: str | None
    vendor_country: str
    vendor_category: str
    vendor_status: str
    amount_eur: float
    fx_rate: float  # units of `currency` per 1 EUR (ECB convention); 1.0 for EUR
    fx_rate_date: str

    def as_row(self) -> dict:
        return {
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "vendor_country": self.vendor_country,
            "vendor_status": self.vendor_status,
            "description": self.description,
            "amount": self.amount,
            "currency": self.currency,
            "amount_eur": self.amount_eur,
            "fx_rate": self.fx_rate,
            "fx_rate_date": self.fx_rate_date,
            "cost_center": self.cost_center,
            "po_number": self.po_number,
            "approved_by": self.approved_by,
        }


class FxTable:
    def __init__(self, path: Path):
        self._dates: list[str] = []
        self._rates: dict[str, dict[str, float]] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                self._dates.append(row["date"])
                self._rates[row["date"]] = {k: float(v) for k, v in row.items() if k != "date"}
        self._dates.sort()

    def to_eur(self, amount: float, currency: str, on_date: str) -> tuple[float, float, str]:
        """Returns (amount_eur, rate, rate_date). Raises ValueError if no rate exists."""
        if currency == "EUR":
            return round(amount, 2), 1.0, on_date
        i = bisect.bisect_right(self._dates, on_date) - 1
        if i < 0:
            raise ValueError(f"No ECB rate on or before {on_date}")
        rate_date = self._dates[i]
        rate = self._rates[rate_date].get(currency)
        if rate is None:
            raise ValueError(f"No ECB rate for {currency}")
        return round(amount / rate, 2), rate, rate_date

    @property
    def latest_date(self) -> str:
        return self._dates[-1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class SpendData:
    def __init__(self) -> None:
        self.fx = FxTable(FX_FILE)
        with VENDORS_FILE.open() as f:
            self.vendors = {v["vendor_id"]: v for v in csv.DictReader(f)}
        self.invoices: list[Invoice] = []
        with INVOICES_FILE.open() as f:
            for r in csv.DictReader(f):
                vendor = self.vendors[r["vendor_id"]]
                amount = float(r["amount"])
                amount_eur, rate, rate_date = self.fx.to_eur(amount, r["currency"], r["invoice_date"])
                self.invoices.append(
                    Invoice(
                        invoice_number=r["invoice_number"],
                        invoice_date=r["invoice_date"],
                        vendor_id=r["vendor_id"],
                        vendor_name=r["vendor_name"],
                        description=r["description"],
                        amount=amount,
                        currency=r["currency"],
                        cost_center=r["cost_center"],
                        po_number=r["po_number"] or None,
                        approved_by=r["approved_by"] or None,
                        vendor_country=vendor["country"],
                        vendor_category=vendor["category"],
                        vendor_status=vendor["status"],
                        amount_eur=amount_eur,
                        fx_rate=rate,
                        fx_rate_date=rate_date,
                    )
                )
        self.by_number = {inv.invoice_number: inv for inv in self.invoices}
        # Content hashes of every source file: the start of a lineage record (§8.2).
        self.source_hashes = {
            p.name: _sha256(p) for p in (INVOICES_FILE, VENDORS_FILE, POLICY_FILE, FX_FILE)
        }
