"""Product & Vendor explorer - one product across all its vendors, or one vendor across all its products.
Company filter takes one or several companies. READ-ONLY on the silver tables; reuses the same cached reads
and rules as Price & Vendor Watch, PO -> Payment and Returns, so the numbers agree with those pages."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException

from .db import q
from .silver import line_sql, _p
from .insights import item_vendor, _receipts_sql, _lp
from .p2p import _data as p2p_data, _overdue
from .returns import _docs as return_docs, PENDING

router = APIRouter()


def _f(v):
    return float(v or 0)


def _fy():
    t = date.today()
    return date(t.year if t.month >= 4 else t.year - 1, 4, 1), t


@router.get("/api/explore/product/{product_id}")
def explore_product(product_id: int, company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None, txn: Optional[str] = "External"):
    f, t = _fy()
    date_from, date_to = date_from or f, date_to or t
    rows = item_vendor(company_id=company_id, date_from=date_from, date_to=date_to, category=None, txn=txn,
                       multi=False, product_id=product_id)
    trend = q(line_sql(with_tax=False) + """
        SELECT bill_month AS month, vendor, uom,
               ROUND((SUM(taxable_value) / NULLIF(SUM(quantity), 0))::numeric, 4) AS avg_price, SUM(quantity) AS qty
        FROM reg WHERE doc_type = 'Bill' AND quantity > 0
        GROUP BY bill_month, vendor, uom ORDER BY bill_month""",
              _p(company_id=company_id, product_id=product_id, date_from=date_from, date_to=date_to, txn=txn))
    qty = sum(_f(r["qty"]) for r in rows)
    spend = sum(_f(r["spend"]) for r in rows)
    uq = {}
    for r in rows:
        uq[r["uom"] or ""] = uq.get(r["uom"] or "", 0.0) + _f(r["qty"])
    uoms = sorted(uq, key=lambda u: -uq[u])          # main unit (most quantity) first
    best = [r for r in rows if r["vs_best_pct"] == 0]
    leads = [r for r in rows if r["avg_lead"] is not None]
    return {
        "product": rows[0]["product"] if rows else None, "item_code": rows[0]["item_code"] if rows else None,
        "category": rows[0]["category"] if rows else None,
        "period": {"from": date_from, "to": date_to},
        "vendors": len({r["vendor"] for r in rows}), "qty": qty, "spend": spend, "uoms": uoms,
        "avg_price": spend / qty if qty and len(uoms) == 1 else None,
        "extra_paid": sum(_f(r["extra_paid"]) for r in rows),
        "cheapest": best[0]["vendor"] if best and len(rows) > 1 else None,
        "fastest": min(leads, key=lambda r: r["avg_lead"])["vendor"] if len(leads) > 1 else None,
        "open_po_qty": sum(_f(r["open_po_qty"]) for r in rows),
        "vendors_rows": rows, "trend": trend,
    }


@router.get("/api/explore/vendor/{vendor_id}")
def explore_vendor(vendor_id: int, company_id: Optional[str] = None, date_from: Optional[date] = None,
                   date_to: Optional[date] = None, txn: Optional[str] = "External"):
    f, t = _fy()
    date_from, date_to = date_from or f, date_to or t
    allrows = item_vendor(company_id=company_id, date_from=date_from, date_to=date_to, category=None, txn=txn,
                          multi=False, vendor_id=vendor_id)
    # one row per item the vendor supplied, compared with the other vendors of the same item & UoM
    groups = {}
    for r in allrows:
        groups.setdefault((r["product_id"], r["uom"]), []).append(r)
    items = []
    for (pid, uom), vs in groups.items():
        me = next((v for v in vs if v["vendor_id"] == vendor_id), None)
        if not me:
            continue
        others = [v for v in vs if v["vendor_id"] != vendor_id and v["avg_price"] is not None]
        cheapest_other = min(others, key=lambda v: v["avg_price"]) if others else None
        items.append({**me, "other_vendors": len(others),
                      "cheapest_other": cheapest_other["vendor"] if cheapest_other else None,
                      "cheapest_other_price": cheapest_other["avg_price"] if cheapest_other else None,
                      "vs_other_pct": round(100.0 * (_f(me["avg_price"]) - _f(cheapest_other["avg_price"]))
                                            / _f(cheapest_other["avg_price"]), 1)
                      if cheapest_other and _f(cheapest_other["avg_price"]) else None})
    items.sort(key=lambda r: -_f(r["spend"]))

    p = _p(company_id=company_id, vendor_id=vendor_id, date_from=date_from, date_to=date_to, txn=txn)
    monthly = q(line_sql(with_tax=False) + """
        SELECT bill_month AS month, SUM(taxable_value) AS spend, COUNT(DISTINCT bill_number) AS bills
        FROM reg GROUP BY bill_month ORDER BY bill_month""", p)
    info = q(line_sql(with_tax=False) + """
        SELECT MAX(vendor) AS vendor, MAX(vendor_gstin) AS gstin, MAX(source) AS source,
               SUM(taxable_value) AS spend, COUNT(DISTINCT bill_number) AS bills, COUNT(DISTINCT product_id) AS items,
               MIN(bill_date) AS first_bill, MAX(bill_date) AS last_bill
        FROM reg""", p)[0]

    lead = q(_receipts_sql() + """
        SELECT COUNT(*) AS receipts, ROUND((AVG(lead_days))::numeric, 1) AS avg_lead,
               ROUND((100.0 * COUNT(*) FILTER (WHERE days_late <= 0)
                      / NULLIF(COUNT(*) FILTER (WHERE days_late IS NOT NULL), 0))::numeric, 1) AS on_time_pct
        FROM lt WHERE vendor_id = %(vid)s""", {**_lp(company_id, date_from, date_to), "vid": vendor_id})[0]

    rets = [r for r in return_docs(company_id, date_from, date_to, None) if r["vendor_id"] == vendor_id]
    pend = [r for r in rets if r["credit_status"] in PENDING]

    pos, bills = p2p_data(company_id, None, None, vendor_id)          # every PO of this vendor, any date
    open_pos = sorted([r for r in pos if _f(r["grn_pending_value"]) > 0.5], key=lambda r: str(r["po_date"]))
    seen, unpaid, overdue = set(), 0.0, 0.0
    for b in bills:
        if b["move_id"] in seen or _f(b["residual"]) <= 0.5:
            continue
        seen.add(b["move_id"])
        unpaid += _f(b["residual"])
        if _overdue(b):
            overdue += _f(b["residual"])
    return {
        "vendor": info["vendor"], "gstin": info["gstin"], "source": info["source"],
        "period": {"from": date_from, "to": date_to},
        "spend": _f(info["spend"]), "bills": info["bills"], "items": info["items"],
        "first_bill": info["first_bill"], "last_bill": info["last_bill"],
        "extra_paid": sum(_f(r["extra_paid"]) for r in items),
        "items_dearer": sum(1 for r in items if (r["vs_other_pct"] or 0) > 0),
        "items_cheapest": sum(1 for r in items if r["other_vendors"] and (r["vs_other_pct"] or 0) <= 0),
        "receipts": lead["receipts"], "avg_lead": lead["avg_lead"], "on_time_pct": lead["on_time_pct"],
        "returns": len(rets), "returned_value": sum(_f(r["value"]) for r in rets),
        "return_pct": round(100.0 * sum(_f(r["value"]) for r in rets) / _f(info["spend"]), 2) if _f(info["spend"]) else None,
        "to_recover": sum(_f(r["value"]) - _f(r["credited"]) for r in pend),
        "open_pos": len(open_pos), "open_po_value": sum(_f(r["grn_pending_value"]) for r in open_pos),
        "unpaid": unpaid, "overdue": overdue,
        "items_rows": items, "monthly": monthly,
        "open_po_rows": [{k: r[k] for k in ("po_number", "po_date", "age_days", "company", "grn_status", "bill_status",
                                             "pay_status", "ordered_value", "grn_pending_value", "to_bill_value")}
                         for r in open_pos][:200],
        "pending_returns": [{k: r[k] for k in ("return_no", "return_date", "kind", "po_numbers", "value", "credited",
                                                "credit_status")} for r in pend][:200],
    }
