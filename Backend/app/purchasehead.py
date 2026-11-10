"""Purchase Head brief - one page for a new head of purchase: where the money goes, who the vendors are and how
they perform, where to save, what is open, and where the buying process leaks. READ-ONLY on the silver tables.
Everything reuses the same cached reads as the other pages, so numbers agree with them."""
from datetime import date
from typing import Optional

from fastapi import APIRouter

from .db import q
from .silver import line_sql, _p, T
from .insights import _vp, _gaps, _receipts_sql, _lp
from .p2p import _data as p2p_data, _overdue
from .returns import _docs as return_docs, PENDING
from .audit import audit_summary

router = APIRouter()


def _f(v):
    return float(v or 0)


def _fy():
    t = date.today()
    return date(t.year if t.month >= 4 else t.year - 1, 4, 1), t


def _spend_rows(company_id, date_from, date_to, txn):
    """Per vendor and per category spend in the period (posted bills less debit notes, INR excl. GST)."""
    p = _p(company_id=company_id, date_from=date_from, date_to=date_to, txn=txn)
    return q(line_sql(with_tax=False) + """
        SELECT 'v' AS kind, vendor_id::text AS k, MAX(vendor) AS name, MAX(source) AS source,
               SUM(taxable_value) AS spend, COUNT(DISTINCT bill_number) AS bills, COUNT(DISTINCT product_id) AS items,
               MIN(bill_date) AS first_bill, MAX(bill_date) AS last_bill
        FROM reg GROUP BY vendor_id
        UNION ALL
        SELECT 'c', COALESCE(product_category, 'No category'), COALESCE(product_category, 'No category'), NULL,
               SUM(taxable_value), COUNT(DISTINCT bill_number), COUNT(DISTINCT product_id), NULL, NULL
        FROM reg GROUP BY product_category
        UNION ALL
        SELECT 's', source, source, source, SUM(taxable_value), COUNT(DISTINCT bill_number), COUNT(DISTINCT product_id),
               NULL, NULL
        FROM reg GROUP BY source""", p)


def _lead_by_vendor(company_id, date_from, date_to):
    return {r["vendor_id"]: r for r in q(_receipts_sql() + """
        SELECT vendor_id, COUNT(*) AS receipts, ROUND((AVG(lead_days))::numeric, 1) AS avg_lead,
               ROUND((100.0 * COUNT(*) FILTER (WHERE days_late <= 0)
                      / NULLIF(COUNT(*) FILTER (WHERE days_late IS NOT NULL), 0))::numeric, 1) AS on_time_pct
        FROM lt GROUP BY vendor_id""", _lp(company_id, date_from, date_to))}


def _dates(date_from, date_to):
    f, t = _fy()
    return date_from or f, date_to or t


def _vendors_spend(company_id, date_from, date_to, txn):
    rows = _spend_rows(company_id, date_from, date_to, txn)
    vend = sorted([r for r in rows if r["kind"] == "v"], key=lambda r: -_f(r["spend"]))
    return rows, vend, sum(_f(r["spend"]) for r in vend)


@router.get("/api/purchase-head/brief")
def brief(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
          txn: Optional[str] = "External"):
    """Spend, concentration and charts - one read of the purchase register."""
    date_from, date_to = _dates(date_from, date_to)
    rows, vend, total = _vendors_spend(company_id, date_from, date_to, txn)
    cats = sorted([r for r in rows if r["kind"] == "c"], key=lambda r: -_f(r["spend"]))
    srcs = [r for r in rows if r["kind"] == "s"]
    run, n80 = 0.0, 0
    for r in vend:
        if total and run / total >= 0.8:
            break
        run += _f(r["spend"])
        n80 += 1
    return {
        "period": {"from": date_from, "to": date_to}, "spend": total, "vendors": len(vend),
        "bills": sum(_f(r["bills"]) for r in vend),
        "top10_share_pct": round(100.0 * sum(_f(r["spend"]) for r in vend[:10]) / total, 1) if total else None,
        "vendors_for_80pct": n80, "tail_vendors": sum(1 for r in vend if total and _f(r["spend"]) / total < 0.001),
        "by_source": [{"name": r["name"] or "Local", "spend": _f(r["spend"])} for r in srcs],
        "top_categories": [{"name": r["name"], "spend": _f(r["spend"]), "items": r["items"], "bills": r["bills"],
                            "share_pct": round(100.0 * _f(r["spend"]) / total, 1) if total else None} for r in cats[:12]],
        "top_vendors": [{"name": r["name"], "spend": _f(r["spend"])} for r in vend[:10]],
    }


def _groups(vp):
    g = {}
    for r in vp:
        g.setdefault((r["product_id"], r["uom"]), []).append(r)
    return g


@router.get("/api/purchase-head/savings")
def savings(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
            txn: Optional[str] = "External"):
    """Price gaps between vendors of the same item (same rules as Price & Vendor Watch)."""
    date_from, date_to = _dates(date_from, date_to)
    vp = _vp(company_id, date_from, date_to, None, txn)
    gaps = _gaps(vp, 10)
    single = [v[0] for v in _groups(vp).values() if len(v) == 1]
    return {"gap_items": len(gaps), "extra_paid": sum(_f(g["extra_paid"]) for g in gaps), "savings": gaps[:15],
            "single_source_items": len(single), "single_source_spend": sum(_f(r["spend"]) for r in single)}


@router.get("/api/purchase-head/scorecard")
def scorecard(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
              txn: Optional[str] = "External"):
    """Top 25 vendors: price vs cheapest, delivery, returns, open POs, overdue payments."""
    date_from, date_to = _dates(date_from, date_to)
    _, vend, total = _vendors_spend(company_id, date_from, date_to, txn)
    pos = {}
    for (pid, uom), vs in _groups(_vp(company_id, date_from, date_to, None, txn)).items():
        prices = [_f(v["avg_price"]) for v in vs if v["avg_price"] is not None and _f(v["avg_price"]) > 0]
        if len(prices) < 2:
            continue
        best = min(prices)
        for v in vs:
            if v["avg_price"] is None:
                continue
            e = pos.setdefault(v["vendor_id"], {"spend": 0.0, "extra": 0.0})
            e["spend"] += _f(v["spend"])
            e["extra"] += (_f(v["avg_price"]) - best) * _f(v["qty"])
    lead = _lead_by_vendor(company_id, date_from, date_to)
    rets = {}
    for r in return_docs(company_id, date_from, date_to, None):
        e = rets.setdefault(r["vendor_id"], {"value": 0.0, "to_recover": 0.0})
        e["value"] += _f(r["value"])
        if r["credit_status"] in PENDING:
            e["to_recover"] += _f(r["value"]) - _f(r["credited"])
    p_rows, p_bills = p2p_data(company_id, None, None, None)      # open POs of any date
    opn = {}
    for r in p_rows:
        e = opn.setdefault(r["vendor_id"], {"open_pos": 0, "grn_pending": 0.0, "oldest_open": None})
        if _f(r["grn_pending_value"]) > 0.5:
            e["open_pos"] += 1
            e["grn_pending"] += _f(r["grn_pending_value"])
            e["oldest_open"] = max(e["oldest_open"] or 0, r["age_days"] or 0)
    po_vendor = {r["po_id"]: r["vendor_id"] for r in p_rows}
    seen, overdue = set(), {}
    for b in p_bills:
        if b["move_id"] in seen or _f(b["residual"]) <= 0.5 or not _overdue(b):
            continue
        seen.add(b["move_id"])
        v = po_vendor.get(b["po_id"])
        overdue[v] = overdue.get(v, 0.0) + _f(b["residual"])
    score = []
    for r in vend[:25]:
        vid = int(r["k"]) if str(r["k"]).isdigit() else r["k"]
        lt, rt, op, ps = lead.get(vid, {}), rets.get(vid, {}), opn.get(vid, {}), pos.get(vid)
        flags = []
        if ps and ps["spend"] and 100.0 * ps["extra"] / ps["spend"] >= 5:
            flags.append("pricier than other vendors")
        if lt.get("on_time_pct") is not None and _f(lt["on_time_pct"]) < 80:
            flags.append("late deliveries")
        if _f(r["spend"]) and 100.0 * _f(rt.get("value")) / _f(r["spend"]) >= 2:
            flags.append("high returns")
        if _f(rt.get("to_recover")) > 0.5:
            flags.append("debit note pending")
        if _f(overdue.get(vid)) > 0.5:
            flags.append("we owe overdue")
        score.append({
            "vendor": r["name"], "source": r["source"], "spend": _f(r["spend"]),
            "share_pct": round(100.0 * _f(r["spend"]) / total, 1) if total else None,
            "bills": r["bills"], "items": r["items"],
            "price_vs_best_pct": round(100.0 * ps["extra"] / ps["spend"], 1) if ps and ps["spend"] else None,
            "extra_paid": round(ps["extra"], 2) if ps else None,
            "avg_lead": lt.get("avg_lead"), "on_time_pct": lt.get("on_time_pct"),
            "return_pct": round(100.0 * _f(rt.get("value")) / _f(r["spend"]), 2) if _f(r["spend"]) else None,
            "to_recover": round(_f(rt.get("to_recover")), 2),
            "open_pos": op.get("open_pos", 0), "grn_pending": round(_f(op.get("grn_pending")), 2),
            "oldest_open_days": op.get("oldest_open"), "overdue_to_vendor": round(_f(overdue.get(vid)), 2),
            "watch": ", ".join(flags),
        })
    return {"scorecard": score,
            "open_po_value": sum(_f(e["grn_pending"]) for e in opn.values()),
            "open_pos": sum(e["open_pos"] for e in opn.values()),
            "overdue_to_vendors": sum(overdue.values()),
            "returns_to_recover": sum(_f(e["to_recover"]) for e in rets.values())}


@router.get("/api/purchase-head/process")
def process(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
            txn: Optional[str] = "External"):
    """Buying discipline: same checks as Audit & Tax, plus split billing, new vendors, single source."""
    date_from, date_to = _dates(date_from, date_to)
    aud = {c["check"]: c for c in audit_summary(company_id=company_id, date_from=date_from, date_to=date_to)}
    pick = lambda k: {"n": aud.get(k, {}).get("n"), "value": aud.get(k, {}).get("value")}
    p = _p(company_id=company_id, date_from=date_from, date_to=date_to, txn=txn)
    split = q(line_sql(with_tax=False) + """
        , w AS (SELECT DATE_TRUNC('week', bill_date) AS wk, vendor_id, product_id, SUM(taxable_value) AS v
                FROM reg WHERE doc_type = 'Bill' AND product_id IS NOT NULL GROUP BY 1, 2, 3
                HAVING COUNT(DISTINCT bill_number) >= 3)
        SELECT COUNT(*) AS n, COALESCE(SUM(v), 0) AS value FROM w""", p)[0]
    _, vend, _ = _vendors_spend(company_id, date_from, date_to, txn)
    ids = [int(r["k"]) for r in vend if str(r["k"]).isdigit()]
    fe = {r["vendor_id"]: r["first_ever"] for r in q(f"""
        SELECT am.partner_id AS vendor_id, MIN(am.invoice_date) AS first_ever FROM {T('account_move')} am
        WHERE am.state = 'posted' AND am.move_type = 'in_invoice' AND am.partner_id = ANY(%(ids)s::int[])
        GROUP BY 1""", {"ids": ids})} if ids else {}
    new = [r for r in vend if str(r["k"]).isdigit() and fe.get(int(r["k"]))
           and str(fe[int(r["k"])])[:10] >= str(date_from)]
    sv = savings(company_id, date_from, date_to, txn)
    return {"bills_without_po": pick("no_po"), "rate_above_po": pick("rate_above_po"),
            "duplicates": pick("duplicates"), "unpaid_90": pick("unpaid_old"),
            "split_billing": {"n": split["n"], "value": split["value"]},
            "new_vendors": {"n": len(new), "value": sum(_f(r["spend"]) for r in new)},
            "single_source": {"n": sv["single_source_items"], "value": sv["single_source_spend"]}}
