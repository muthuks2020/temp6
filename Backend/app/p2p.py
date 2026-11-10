"""Purchase order -> GRN -> bill -> payment tracker. READ-ONLY. Uses only Odoo's own links:
  PO line -> receipt (stock_move.purchase_line_id, done receipt from a vendor location)
  PO line -> bill line (account_move_line.purchase_line_id, posted vendor bill)
  bill -> payment (account_partial_reconcile: payment line reconciled with the bill's payable line)
Quantities received / billed come from the PO line itself (qty_received, qty_invoiced), as Odoo keeps them.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException

from .db import q
from .returns import SANE_DATE
from .silver import T, tr, has

router = APIRouter()

STAGES = {
    "grn_pending": "grn_status <> 'Received'",
    "to_bill": "to_bill_value > 0.5",
    "billed_not_received": "billed_not_received_value > 0.5",
    "unpaid": "payment_pending > 0.5",
    "overdue": "overdue_amount > 0.5",
    "paid_not_received": "pay_status = 'Paid' AND grn_status <> 'Received'",
    "closed": "grn_status = 'Received' AND bill_status = 'Billed' AND pay_status = 'Paid'",
    "all": "TRUE",
}
DIMS = {
    "company": "company", "vendor": "vendor", "order_type": "order_type", "month": "TO_CHAR(po_date, 'YYYY-MM')",
    "status": "grn_status || ' · ' || bill_status || ' · ' || pay_status",
}


def _p(company_id, date_from, date_to, vendor_id=None):
    return {"company_id": company_id, "date_from": date_from, "date_to": date_to, "vendor_id": vendor_id}


def base_sql():
    from . import mart
    if mart.use("po_status") and mart.use("po_bill_links"):
        # saved daily by procurement_mart; ages are worked out on today's date
        cols = PO_COLS.replace("age_days,", "")
        return f"""
WITH p2p AS (SELECT {cols}, CURRENT_DATE - po_date AS age_days FROM {mart.M('po_status')}
    WHERE (%(date_from)s::date IS NULL OR po_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR po_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))
      AND (%(vendor_id)s::int IS NULL OR vendor_id = %(vendor_id)s::int)),
bl AS (SELECT b.* FROM {mart.M('po_bill_links')} b WHERE b.po_id IN (SELECT po_id FROM p2p))
"""
    rate = "NULLIF(po.currency_rate, 0)" if has("purchase_order", "currency_rate") else "1"
    disp = "AND pol.display_type IS NULL" if has("purchase_order_line", "display_type") else ""
    ot_join = f"LEFT JOIN {T('purchase_order_type')} ot ON ot.id = po.order_type_id" \
        if has("purchase_order", "order_type_id") and has("purchase_order_type") else ""
    ot_name = tr("ot.name") if ot_join else "NULL::text"
    if has("account_move", "amount_residual_signed"):
        resid, total = "ABS(am.amount_residual_signed)", "ABS(am.amount_total_signed)"
    elif has("account_move", "amount_residual"):
        resid, total = "am.amount_residual", "am.amount_total"
    else:
        resid = total = "NULL::numeric"
    pay = ""
    if has("account_partial_reconcile", "credit_move_id"):
        # Only the bill's payable lines can be reconciled with a payment (Odoo: display_type 'payment_term').
        payable = "AND l.display_type = 'payment_term'" if has("account_move_line", "display_type") else ""
        # Two plain joins (credit side, debit side) instead of one OR-join: the OR forced a full pairwise scan.
        pay = f"""
, bmv AS (SELECT DISTINCT move_id FROM bl),
bln AS (SELECT l.id, l.move_id FROM {T('account_move_line')} l JOIN bmv ON bmv.move_id = l.move_id {payable}),
prl AS (
    SELECT bln.move_id, pr.debit_move_id AS other_line, pr.max_date
    FROM bln JOIN {T('account_partial_reconcile')} pr ON pr.credit_move_id = bln.id
    UNION ALL
    SELECT bln.move_id, pr.credit_move_id, pr.max_date
    FROM bln JOIN {T('account_partial_reconcile')} pr ON pr.debit_move_id = bln.id
),
payref AS (  -- payments reconciled against each bill
    SELECT prl.move_id, STRING_AGG(DISTINCT pm.name, ', ') AS payments, MAX(prl.max_date) AS last_payment
    FROM prl
    JOIN {T('account_move_line')} ol ON ol.id = prl.other_line
    JOIN {T('account_move')} pm ON pm.id = ol.move_id AND pm.id <> prl.move_id
    GROUP BY prl.move_id
)"""
    else:
        pay = "\n, payref AS (SELECT NULL::int AS move_id, NULL::text AS payments, NULL::date AS last_payment WHERE false)"
    return f"""
WITH pol AS (
    SELECT po.id AS po_id, po.name AS po_number, COALESCE(po.date_approve, po.date_order)::date AS po_date,
           po.company_id, {tr('c.name')} AS company, po.partner_id AS vendor_id, p.name AS vendor, {ot_name} AS order_type,
           pol.id AS line_id, pol.product_uom_qty::numeric AS qty,
           COALESCE(pol.qty_received, 0)::numeric AS rec, COALESCE(pol.qty_invoiced, 0)::numeric AS inv,
           (pol.price_subtotal / {rate})::numeric AS val,
           (pol.price_subtotal / NULLIF(pol.product_uom_qty, 0) / {rate})::numeric AS unit
    FROM {T('purchase_order')} po
    JOIN {T('purchase_order_line')} pol ON pol.order_id = po.id {disp}
    JOIN {T('res_company')} c ON c.id = po.company_id
    JOIN {T('res_partner')} p ON p.id = po.partner_id
    {ot_join}
    WHERE po.state IN ('purchase', 'done')
      AND (%(date_from)s::date IS NULL OR COALESCE(po.date_approve, po.date_order) >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR COALESCE(po.date_approve, po.date_order) <  %(date_to)s::date + 1)
      AND (%(company_id)s::int[] IS NULL OR po.company_id = ANY(%(company_id)s::int[]))
      AND (%(vendor_id)s::int IS NULL OR po.partner_id = %(vendor_id)s::int)
),
grn AS (
    SELECT pol.po_id, STRING_AGG(DISTINCT sp.name, ', ') AS grns, MAX(sp.date_done)::date AS last_grn
    FROM pol
    JOIN {T('stock_move')} sm ON sm.purchase_line_id = pol.line_id AND sm.state = 'done'
    JOIN {T('stock_picking')} sp ON sp.id = sm.picking_id AND sp.state = 'done' AND {SANE_DATE}
    JOIN {T('stock_location')} src ON src.id = sp.location_id AND src.usage = 'supplier'
    GROUP BY pol.po_id
),
bl AS (        -- posted vendor bills (and refunds) touching each PO
    SELECT DISTINCT pol.po_id, am.id AS move_id, am.name, am.move_type, am.invoice_date, am.invoice_date_due,
           am.payment_state, {resid} AS residual, {total} AS bill_total
    FROM pol
    JOIN {T('account_move_line')} aml ON aml.purchase_line_id = pol.line_id
    JOIN {T('account_move')} am ON am.id = aml.move_id AND am.state = 'posted'
         AND am.move_type IN ('in_invoice', 'in_refund')
){pay},
bills AS (
    SELECT bl.po_id, STRING_AGG(DISTINCT bl.name, ', ') AS bills, MAX(bl.invoice_date) AS last_bill,
           COUNT(DISTINCT bl.move_id) FILTER (WHERE bl.move_type = 'in_invoice') AS bill_count,
           BOOL_AND(bl.payment_state IN ('paid', 'in_payment', 'reversed')) FILTER (WHERE bl.move_type = 'in_invoice') AS all_paid,
           BOOL_OR(bl.payment_state IN ('paid', 'in_payment', 'partial')) FILTER (WHERE bl.move_type = 'in_invoice') AS any_paid,
           COALESCE(SUM(bl.residual) FILTER (WHERE bl.move_type = 'in_invoice'), 0) AS payment_pending,
           COALESCE(SUM(bl.residual) FILTER (WHERE bl.move_type = 'in_invoice' AND bl.invoice_date_due < CURRENT_DATE), 0)
               AS overdue_amount,
           MIN(bl.invoice_date_due) FILTER (WHERE bl.move_type = 'in_invoice' AND COALESCE(bl.residual, 1) > 0.5) AS oldest_due,
           STRING_AGG(DISTINCT pr.payments, ', ') AS payments, MAX(pr.last_payment) AS last_payment
    FROM bl LEFT JOIN payref pr ON pr.move_id = bl.move_id
    GROUP BY bl.po_id
),
po AS (
    SELECT pol.po_id, MAX(pol.po_number) AS po_number, MAX(pol.po_date) AS po_date, MAX(pol.company) AS company,
           MAX(pol.vendor_id) AS vendor_id, MAX(pol.vendor) AS vendor, MAX(pol.order_type) AS order_type,
           COUNT(*) AS lines, SUM(pol.val) AS ordered_value,
           SUM(LEAST(pol.rec, pol.qty) * pol.unit) AS received_value,
           SUM(LEAST(pol.inv, pol.qty) * pol.unit) AS billed_value,
           SUM(GREATEST(pol.qty - pol.rec, 0) * pol.unit) AS grn_pending_value,
           SUM(GREATEST(pol.rec - pol.inv, 0) * pol.unit) AS to_bill_value,
           SUM(GREATEST(pol.inv - pol.rec, 0) * pol.unit) AS billed_not_received_value,
           SUM(pol.qty) AS qty, SUM(pol.rec) AS rec, SUM(pol.inv) AS inv
    FROM pol GROUP BY pol.po_id
),
p2p AS (
    SELECT po.*, g.grns, g.last_grn, b.bills, b.last_bill, b.bill_count, b.payments, b.last_payment, b.oldest_due,
           COALESCE(b.payment_pending, 0) AS payment_pending, COALESCE(b.overdue_amount, 0) AS overdue_amount,
           CASE WHEN po.received_value <= 0.5 THEN 'GRN pending'
                WHEN po.grn_pending_value > 0.5 THEN 'Partly received' ELSE 'Received' END AS grn_status,
           CASE WHEN po.billed_value <= 0.5 THEN 'Not billed'
                WHEN po.ordered_value - po.billed_value > 0.5 THEN 'Partly billed' ELSE 'Billed' END AS bill_status,
           CASE WHEN COALESCE(b.bill_count, 0) = 0 THEN 'No bill'
                WHEN b.all_paid THEN 'Paid' WHEN b.any_paid THEN 'Partly paid' ELSE 'Unpaid' END AS pay_status,
           CURRENT_DATE - po.po_date AS age_days
    FROM po LEFT JOIN grn g ON g.po_id = po.po_id LEFT JOIN bills b ON b.po_id = po.po_id
)
"""


# ---------------------------------------------------------------- one database read per filter, the rest in Python
PO_COLS = """po_id, po_number, po_date, age_days, company, vendor_id, vendor, order_type, grn_status, bill_status, pay_status,
             ordered_value, received_value, billed_value, grn_pending_value, to_bill_value, billed_not_received_value,
             payment_pending, overdue_amount, oldest_due, grns, last_grn, bills, last_bill, payments, last_payment"""


def _data(company_id, date_from, date_to, vendor_id):
    """Two cached reads: one row per PO, and one row per (PO, bill). Everything else is summed here."""
    p = _p(company_id, date_from, date_to, vendor_id)
    pos = q(base_sql() + f"SELECT {PO_COLS} FROM p2p", p)
    bills = q(base_sql() + """SELECT DISTINCT po_id, move_id, residual, invoice_date_due
                               FROM bl WHERE move_type = 'in_invoice'""", p)
    return pos, bills


def _n(v):
    return float(v or 0)


def _overdue(b):
    return b["invoice_date_due"] is not None and str(b["invoice_date_due"]) < date.today().isoformat()


def _agg(rows, bills):
    """Totals for a group of PO rows; unpaid bills counted once each."""
    ids = {r["po_id"] for r in rows}
    seen, pend, over, nb = set(), 0.0, 0.0, 0
    for b in bills:
        if b["po_id"] in ids and b["move_id"] not in seen and _n(b["residual"]) > 0.5:
            seen.add(b["move_id"]); nb += 1; pend += _n(b["residual"])
            if _overdue(b):
                over += _n(b["residual"])
    s = lambda k: round(sum(_n(r[k]) for r in rows), 2)
    c = lambda f: sum(1 for r in rows if f(r))
    return {
        "pos": len(rows), "ordered_value": s("ordered_value"), "received_value": s("received_value"),
        "billed_value": s("billed_value"),
        "grn_pending_pos": c(lambda r: r["grn_status"] != "Received"), "grn_pending_value": s("grn_pending_value"),
        "to_bill_pos": c(lambda r: _n(r["to_bill_value"]) > 0.5), "to_bill_value": s("to_bill_value"),
        "billed_not_received_pos": c(lambda r: _n(r["billed_not_received_value"]) > 0.5),
        "billed_not_received_value": s("billed_not_received_value"),
        "paid_not_received_pos": c(lambda r: r["pay_status"] == "Paid" and r["grn_status"] != "Received"),
        "closed_pos": c(lambda r: r["grn_status"] == "Received" and r["bill_status"] == "Billed" and r["pay_status"] == "Paid"),
        "unpaid_bills": nb, "payment_pending": round(pend, 2), "overdue_amount": round(over, 2),
        "oldest_days": max((r["age_days"] or 0 for r in rows), default=None),
    }


ORDER = {"GRN pending": 1, "Partly received": 2, "Received": 3, "Not billed": 1, "Partly billed": 2, "Billed": 3,
         "No bill": 1, "Unpaid": 2, "Partly paid": 3, "Paid": 4}
KEYF = {
    "company": lambda r: r["company"], "vendor": lambda r: r["vendor"], "order_type": lambda r: r["order_type"] or "(none)",
    "month": lambda r: str(r["po_date"])[:7], "status": lambda r: f"{r['grn_status']} · {r['bill_status']} · {r['pay_status']}",
}
STAGE_F = {
    "grn_pending": lambda r: r["grn_status"] != "Received",
    "to_bill": lambda r: _n(r["to_bill_value"]) > 0.5,
    "billed_not_received": lambda r: _n(r["billed_not_received_value"]) > 0.5,
    "unpaid": lambda r: _n(r["payment_pending"]) > 0.5,
    "overdue": lambda r: _n(r["overdue_amount"]) > 0.5,
    "paid_not_received": lambda r: r["pay_status"] == "Paid" and r["grn_status"] != "Received",
    "closed": lambda r: r["grn_status"] == "Received" and r["bill_status"] == "Billed" and r["pay_status"] == "Paid",
    "all": lambda r: True,
}


@router.get("/api/p2p/summary")
def p2p_summary(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                vendor_id: Optional[int] = None):
    pos, bills = _data(company_id, date_from, date_to, vendor_id)
    a = _agg(pos, bills)
    a["overdue_bills"] = len({b["move_id"] for b in bills if _n(b["residual"]) > 0.5 and _overdue(b)})
    return a


@router.get("/api/p2p/combos")
def p2p_combos(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
               vendor_id: Optional[int] = None):
    """Every combination of GRN status x bill status x payment status that occurs."""
    pos, bills = _data(company_id, date_from, date_to, vendor_id)
    groups = {}
    for r in pos:
        groups.setdefault((r["grn_status"], r["bill_status"], r["pay_status"]), []).append(r)
    out = [{"grn_status": g, "bill_status": b, "pay_status": y, **_agg(rows, bills)} for (g, b, y), rows in groups.items()]
    return sorted(out, key=lambda x: (ORDER[x["grn_status"]], ORDER[x["bill_status"]], ORDER[x["pay_status"]]))


@router.get("/api/p2p/pivot")
def p2p_pivot(by: str = "company", company_id: Optional[str] = None, date_from: Optional[date] = None,
              date_to: Optional[date] = None, vendor_id: Optional[int] = None):
    """Consolidated report: one row per company / vendor / order type / month / status combination."""
    f = KEYF.get(by)
    if not f:
        raise HTTPException(400, "by must be one of " + ", ".join(KEYF))
    pos, bills = _data(company_id, date_from, date_to, vendor_id)
    groups = {}
    for r in pos:
        groups.setdefault(f(r), []).append(r)
    out = [{"name": k, **_agg(rows, bills)} for k, rows in groups.items()]
    return sorted(out, key=lambda x: -x["ordered_value"])


@router.get("/api/p2p/list")
def p2p_list(stage: str = "all", grn: Optional[str] = None, bill: Optional[str] = None, pay: Optional[str] = None,
             company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
             vendor_id: Optional[int] = None):
    """POs with every reference from order to payment, filtered by a stage or an exact status combination."""
    f = STAGE_F.get(stage)
    if not f:
        raise HTTPException(400, "unknown stage")
    pos, _ = _data(company_id, date_from, date_to, vendor_id)
    rows = [r for r in pos if f(r) and (grn is None or r["grn_status"] == grn) and (bill is None or r["bill_status"] == bill)
            and (pay is None or r["pay_status"] == pay)]
    rows = sorted(rows, key=lambda r: str(r["po_date"]))[:3000]
    return [{**{k: v for k, v in r.items() if k not in ("po_id", "vendor_id", "payment_pending", "overdue_amount")},
             "bill_balance": r["payment_pending"], "bill_overdue": r["overdue_amount"]} for r in rows]
