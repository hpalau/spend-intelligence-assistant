"""Independent computation of the expected answers in cases.json.

Deliberately shares no code with the app: it reads the raw CSVs itself. If the app
and this script agree, two separate implementations produced the same figure;
if the golden values came from the app's own tools, the eval would be circular.

    python eval/compute_golden.py
"""

import bisect
import csv
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "backend" / "data"

rates = list(csv.DictReader(open(DATA / "ecb_eur_rates_2025.csv")))
dates = [r["date"] for r in rates]


def eur(amount, currency, day):
    if currency == "EUR":
        return amount
    r = rates[bisect.bisect_right(dates, day) - 1]
    return amount / float(r[currency])


vendors = {v["vendor_id"]: v for v in csv.DictReader(open(DATA / "vendor_master.csv"))}
invoices = list(csv.DictReader(open(DATA / "invoices_2025_clean.csv")))
for i in invoices:
    i["eur"] = round(eur(float(i["amount"]), i["currency"], i["invoice_date"]), 2)

by_vendor = defaultdict(float)
for i in invoices:
    by_vendor[i["vendor_name"]] += i["eur"]
print("Top 5 vendors (net, EUR):")
for name, total in sorted(by_vendor.items(), key=lambda kv: -kv[1])[:5]:
    print(f"  {name}: {total:,.2f}")

spain = [i for i in invoices if vendors[i["vendor_id"]]["country"] == "Spain"]
print(f"Spain total: {sum(i['eur'] for i in spain):,.2f} over {len(spain)} invoices")

no_po = [i for i in invoices if not i["po_number"] and i["eur"] >= 1000]
print(f"No PO, >= EUR 1,000: {len(no_po)} invoices, {sum(i['eur'] for i in no_po):,.2f}")

gbp_45k = eur(45000, "GBP", "2025-06-02")
print(f"GBP 45,000 on 2025-06-02 = EUR {gbp_45k:,.2f} -> {'Finance Director' if gbp_45k > 50000 else 'Department Head'}")

for n in ("INV-20118", "INV-20004", "INV-20010"):
    i = next(x for x in invoices if x["invoice_number"] == n)
    print(f"{n}: {i['vendor_name']} ({vendors[i['vendor_id']]['status']}), EUR {i['eur']:,.2f}, PO={i['po_number'] or '-'}, approver={i['approved_by'] or '-'}")
