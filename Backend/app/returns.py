"""Material sent to vendors - READ-ONLY.

Every DONE transfer from an internal location to a vendor (supplier) location is one document, classified as:
  * 'PO receipt return'   - Odoo "Return" on a purchase receipt (stock_move.origin_returned_move_id set)
  * 'Bulk return form'    - linked from stock_return_line.picking_id (custom bulk purchase return form)
  * 'Sent against PO'     - has a PO line but is not a return (job work / rework on a PO)
  * 'DC - no PO'          - nothing linked (delivery challan to vendor)
Returns page = the first two; Job work & DC page = the last two.

Debit notes (exact Odoo links only, no date / text guessing):
  * PO line: share credited = refunded qty on posted vendor refunds of that PO line / all-time returned qty
  * bulk form: rel_stock_return_invoice (return form -> debit note)
  * no PO line and no form link: shown as 'No PO line - cannot check'
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter

from .db import q
from .silver import T, tr, has

router = APIRouter()

RETURN_KINDS = ("PO receipt return", "Bulk return form")
DC_KINDS = ("Sent against PO", "DC - no PO")
SANE_DATE = "sp.date_done >= DATE '2000-01-01' AND sp.date_done < CURRENT_DATE + 366"


def _p(company_id, date_from, date_to, kind=None):
    return {"company_id": company_id, "date_from": date_from, "date_to": date_to, "kind": kind}


def kind_filter(kind):
    """'returns' (default), 'dc', 'all' or one exact kind."""
    if kind in (None, "", "returns"):
        return "kind IN ('PO receipt return', 'Bulk return form')"
    if kind == "dc":
        return "kind IN ('Sent against PO', 'DC - no PO')"
    if kind == "all":
        return "TRUE"
    return "kind = %(kind)s"


def qty_expr(a="sm"):
    if has("stock_move", "quantity"):
        return f"COALESCE(NULLIF({a}.quantity, 0), {a}.product_uom_qty, 0)::numeric"
    return f"COALESCE({a}.product_uom_qty, 0)::numeric"


def base_sql(kind=None):
    from . import mart
    if mart.use("vendor_returns") and mart.use("return_lines"):
        w = """(%(date_from)s::date IS NULL OR doc_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR doc_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))"""
        return f"""
WITH rl AS (SELECT * FROM {mart.M('return_lines')} WHERE {w}),
docf AS (SELECT * FROM {mart.M('vendor_returns')} WHERE {w} AND {kind_filter(kind)}),
ret AS (SELECT * FROM docf)
"""
    has_bulk = has("stock_return") and has("stock_return_line", "picking_id")
    has_reason = has_bulk and has("return_reason")
    bulk = f"""
, bulk AS (
    SELECT srl.picking_id, STRING_AGG(DISTINCT sr.name, ', ') AS form_no,
           STRING_AGG(DISTINCT {tr('rr.name') if has_reason else 'NULL::text'}, ', ') AS reason,
           ARRAY_AGG(DISTINCT sr.id) AS form_ids
    FROM {T('stock_return_line')} srl
    JOIN {T('stock_return')} sr ON sr.id = srl.return_id
    {f"LEFT JOIN {T('return_reason')} rr ON rr.id = sr.reason_id" if has_reason else ''}
    WHERE srl.picking_id IN (SELECT picking_id FROM vp)
    GROUP BY srl.picking_id
)""" if has_bulk else """
, bulk AS (SELECT NULL::int AS picking_id, NULL::text AS form_no, NULL::text AS reason, NULL::int[] AS form_ids WHERE false)"""
    form_credit = f""",
fcr AS (       -- bulk return form: explicit link return form -> debit note
    SELECT b.picking_id, STRING_AGG(DISTINCT am.name, ', ') AS debit_notes, SUM(-aml.balance) AS credited
    FROM bulk b
    JOIN {T('rel_stock_return_invoice')} rel ON rel.return_id = ANY (b.form_ids)
    JOIN {T('account_move')} am ON am.id = rel.invoice_id AND am.state = 'posted'
    JOIN {T('account_move_line')} aml ON aml.move_id = am.id AND aml.display_type = 'product'
    GROUP BY b.picking_id
)""" if has_bulk and has("rel_stock_return_invoice") else """,
fcr AS (SELECT NULL::int AS picking_id, NULL::text AS debit_notes, NULL::numeric AS credited WHERE false)"""
    origin_ret = "sm.origin_returned_move_id" if has("stock_move", "origin_returned_move_id") else "NULL::int"
    po_rate = "NULLIF(po.currency_rate, 0)" if has("purchase_order", "currency_rate") else "1"
    return f"""
WITH vp AS (   -- done transfers internal -> vendor location
    SELECT sp.id AS picking_id, sp.name AS doc_no, sp.date_done::date AS doc_date, sp.origin,
           sp.company_id, {tr('c.name')} AS company, sp.partner_id AS vendor_id, p.name AS vendor
    FROM {T('stock_picking')} sp
    JOIN {T('stock_location')} src ON src.id = sp.location_id AND src.usage = 'internal'
    JOIN {T('stock_location')} dst ON dst.id = sp.location_dest_id AND dst.usage = 'supplier'
    JOIN {T('res_company')} c ON c.id = sp.company_id
    LEFT JOIN {T('res_partner')} p ON p.id = sp.partner_id
    WHERE sp.state = 'done' AND {SANE_DATE}
      AND (%(date_from)s::date IS NULL OR sp.date_done >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR sp.date_done <  %(date_to)s::date + 1)
      AND (%(company_id)s::int[] IS NULL OR sp.company_id = ANY(%(company_id)s::int[]))
){bulk},
rl AS (        -- lines
    SELECT vp.*, sm.product_id, {qty_expr()} AS qty, sm.purchase_line_id, {origin_ret} AS returned_move_id,
           COALESCE(pol.price_subtotal / NULLIF(pol.product_uom_qty, 0) / {po_rate},
                    ABS(sm.price_unit))::numeric AS unit_inr,
           po.name AS po_number
    FROM vp
    JOIN {T('stock_move')} sm ON sm.picking_id = vp.picking_id AND sm.state = 'done'
    LEFT JOIN {T('purchase_order_line')} pol ON pol.id = sm.purchase_line_id
    LEFT JOIN {T('purchase_order')} po ON po.id = pol.order_id
),
doc AS (
    SELECT rl.picking_id, MAX(rl.doc_no) AS doc_no, MAX(rl.doc_date) AS doc_date, MAX(rl.origin) AS origin,
           MAX(rl.company_id) AS company_id, MAX(rl.company) AS company, MAX(rl.vendor_id) AS vendor_id,
           MAX(rl.vendor) AS vendor, COUNT(*) AS lines, SUM(rl.qty) AS qty, SUM(rl.qty * rl.unit_inr) AS value,
           STRING_AGG(DISTINCT rl.po_number, ', ') AS po_numbers,
           ARRAY_AGG(DISTINCT rl.purchase_line_id) FILTER (WHERE rl.purchase_line_id IS NOT NULL) AS pol_ids,
           MAX(b.form_no) AS form_no, MAX(b.reason) AS reason,
           CASE WHEN BOOL_OR(b.picking_id IS NOT NULL) THEN 'Bulk return form'
                WHEN BOOL_OR(rl.returned_move_id IS NOT NULL) THEN 'PO receipt return'
                WHEN BOOL_OR(rl.purchase_line_id IS NOT NULL) THEN 'Sent against PO'
                ELSE 'DC - no PO' END AS kind
    FROM rl LEFT JOIN bulk b ON b.picking_id = rl.picking_id
    GROUP BY rl.picking_id
),
docf AS (SELECT * FROM doc WHERE {kind_filter(kind)}),
pl AS (SELECT DISTINCT purchase_line_id FROM rl WHERE purchase_line_id IS NOT NULL
         AND picking_id IN (SELECT picking_id FROM docf)),
pl_ret AS (    -- all-time quantity returned to the vendor on each PO line
    SELECT sm.purchase_line_id, SUM({qty_expr()}) AS returned_qty
    FROM {T('stock_move')} sm
    JOIN {T('stock_picking')} sp ON sp.id = sm.picking_id AND sp.state = 'done'
    JOIN {T('stock_location')} src ON src.id = sp.location_id AND src.usage = 'internal'
    JOIN {T('stock_location')} dst ON dst.id = sp.location_dest_id AND dst.usage = 'supplier'
    WHERE sm.state = 'done' AND sm.purchase_line_id IN (SELECT purchase_line_id FROM pl)
    GROUP BY 1
),
pl_ref AS (    -- posted vendor refunds (debit notes) on the same PO line
    SELECT aml.purchase_line_id, SUM(ABS(aml.quantity)) AS refunded_qty,
           STRING_AGG(DISTINCT am.name, ', ') AS debit_notes
    FROM {T('account_move_line')} aml
    JOIN {T('account_move')} am ON am.id = aml.move_id AND am.move_type = 'in_refund' AND am.state = 'posted'
    WHERE aml.display_type = 'product' AND aml.purchase_line_id IN (SELECT purchase_line_id FROM pl)
    GROUP BY 1
),
lcr AS (       -- share of each returned line covered by debit notes (0..1)
    SELECT rl.picking_id, rl.qty * rl.unit_inr AS value, r.debit_notes,
           CASE WHEN rl.purchase_line_id IS NULL THEN NULL
                ELSE LEAST(COALESCE(r.refunded_qty, 0) / NULLIF(x.returned_qty, 0), 1) END AS covered
    FROM rl
    LEFT JOIN pl_ret x ON x.purchase_line_id = rl.purchase_line_id
    LEFT JOIN pl_ref r ON r.purchase_line_id = rl.purchase_line_id
    WHERE rl.picking_id IN (SELECT picking_id FROM docf)
),
crd AS (
    SELECT picking_id, STRING_AGG(DISTINCT debit_notes, ', ') AS debit_notes,
           SUM(value * covered) AS credited,
           CASE WHEN BOOL_AND(covered IS NULL) THEN NULL
                WHEN BOOL_AND(covered >= 1) THEN 'Credited'
                WHEN BOOL_OR(covered > 0) THEN 'Partly credited'
                ELSE 'No debit note' END AS credit_status
    FROM lcr GROUP BY picking_id
){form_credit},
ret AS (
    SELECT d.*, d.doc_no AS return_no, d.doc_date AS return_date,
           COALESCE(c.debit_notes, f.debit_notes) AS debit_notes,
           COALESCE(c.credited, f.credited) AS credited,
           COALESCE(c.credit_status, CASE WHEN f.picking_id IS NOT NULL THEN 'Credited (return form link)' END,
                    'No PO line - cannot check') AS credit_status
    FROM docf d LEFT JOIN crd c ON c.picking_id = d.picking_id LEFT JOIN fcr f ON f.picking_id = d.picking_id
)
"""


RET_COLS = """picking_id, return_no, return_date, kind, form_no, company, vendor_id, vendor, reason, lines, qty, value,
              po_numbers, origin, debit_notes, credited, credit_status"""
PENDING = ("No debit note", "Partly credited")


def _all_docs(company_id, date_from, date_to):
    """ONE read of every transfer to a vendor in the period (all kinds, with debit-note status) - cached.
    Summary, reasons, months, lists and the kind filter are all worked out from these rows."""
    return q(base_sql("all") + f"SELECT {RET_COLS} FROM ret", _p(company_id, date_from, date_to))


def _docs(company_id, date_from, date_to, kind):
    rows = _all_docs(company_id, date_from, date_to)
    if kind in (None, "", "returns"):
        return [r for r in rows if r["kind"] in RETURN_KINDS]
    if kind == "dc":
        return [r for r in rows if r["kind"] in DC_KINDS]
    if kind == "all":
        return rows
    return [r for r in rows if r["kind"] == kind]


def _v(x):
    return float(x or 0)


@router.get("/api/returns/types")
def return_types(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """Counts per kind (for the filter)."""
    c = {}
    for r in _all_docs(company_id, date_from, date_to):
        c[r["kind"]] = c.get(r["kind"], 0) + 1
    return sorted([{"value": k, "returns": n} for k, n in c.items()], key=lambda x: -x["returns"])


@router.get("/api/returns/summary")
def returns_summary(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None, kind: Optional[str] = None):
    rows = _docs(company_id, date_from, date_to, kind)
    pend = [r for r in rows if r["credit_status"] in PENDING]
    return {"returns": len(rows), "lines": sum(_v(r["lines"]) for r in rows),
            "vendors": len({r["vendor_id"] for r in rows}), "value": round(sum(_v(r["value"]) for r in rows), 2),
            "credited": round(sum(_v(r["credited"]) for r in rows), 2), "without_debit_note": len(pend),
            "value_without_debit_note": round(sum(_v(r["value"]) - _v(r["credited"]) for r in pend), 2),
            "not_checkable": sum(1 for r in rows if r["credit_status"] == "No PO line - cannot check")}


@router.get("/api/returns/by-vendor")
def returns_by_vendor(company_id: Optional[str] = None, date_from: Optional[date] = None,
                      date_to: Optional[date] = None, kind: Optional[str] = None):
    """Per vendor: returns, value, credited, pending - and return rate vs what we bought from them."""
    return q(base_sql(kind) + f"""
        , bought AS (
            SELECT am.partner_id AS vendor_id, SUM(aml.balance) AS billed
            FROM {T('account_move_line')} aml JOIN {T('account_move')} am ON am.id = aml.move_id
            WHERE am.move_type = 'in_invoice' AND am.state = 'posted' AND aml.display_type = 'product'
              AND (%(date_from)s::date IS NULL OR am.invoice_date >= %(date_from)s::date)
              AND (%(date_to)s::date   IS NULL OR am.invoice_date <= %(date_to)s::date)
              AND (%(company_id)s::int[] IS NULL OR am.company_id = ANY(%(company_id)s::int[]))
              AND am.partner_id IN (SELECT vendor_id FROM ret)
            GROUP BY 1)
        SELECT r.vendor, COUNT(*) AS returns, SUM(r.qty) AS qty, SUM(r.value) AS value,
               SUM(r.credited) AS credited,
               SUM(r.value - COALESCE(r.credited, 0)) FILTER (WHERE r.credit_status IN ('No debit note', 'Partly credited')) AS pending_value,
               COUNT(*) FILTER (WHERE r.credit_status IN ('No debit note', 'Partly credited')) AS pending_returns,
               MAX(b.billed) AS billed,
               ROUND((100.0 * SUM(r.value) / NULLIF(MAX(b.billed), 0))::numeric, 2) AS return_rate_pct,
               STRING_AGG(DISTINCT r.reason, ', ') AS reasons
        FROM ret r LEFT JOIN bought b ON b.vendor_id = r.vendor_id
        GROUP BY r.vendor ORDER BY value DESC NULLS LAST LIMIT 500""", _p(company_id, date_from, date_to, kind))


@router.get("/api/returns/by-reason")
def returns_by_reason(company_id: Optional[str] = None, date_from: Optional[date] = None,
                      date_to: Optional[date] = None, kind: Optional[str] = None):
    g = {}
    for r in _docs(company_id, date_from, date_to, kind):
        k = r["reason"] or ("Not recorded (Odoo return)" if r["kind"] == "PO receipt return" else "(no reason)")
        x = g.setdefault(k, {"reason": k, "returns": 0, "value": 0.0, "vendors": set(), "without_debit_note": 0})
        x["returns"] += 1; x["value"] += _v(r["value"]); x["vendors"].add(r["vendor_id"])
        x["without_debit_note"] += r["credit_status"] in PENDING
    return sorted([{**x, "value": round(x["value"], 2), "vendors": len(x["vendors"])} for x in g.values()],
                  key=lambda x: -x["value"])


@router.get("/api/returns/by-product")
def returns_by_product(company_id: Optional[str] = None, date_from: Optional[date] = None,
                       date_to: Optional[date] = None, kind: Optional[str] = None):
    return q(base_sql(kind) + f"""
        SELECT pp.default_code AS item_code, {tr('pt.name')} AS product, COUNT(DISTINCT rl.picking_id) AS returns,
               SUM(rl.qty) AS qty, SUM(rl.qty * rl.unit_inr) AS value, COUNT(DISTINCT rl.vendor_id) AS vendors,
               STRING_AGG(DISTINCT d.reason, ', ') AS reasons
        FROM rl
        JOIN docf d ON d.picking_id = rl.picking_id
        LEFT JOIN {T('product_product')} pp ON pp.id = rl.product_id
        LEFT JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
        GROUP BY 1, 2 ORDER BY value DESC NULLS LAST, qty DESC LIMIT 300""",
             _p(company_id, date_from, date_to, kind))


@router.get("/api/returns/monthly")
def returns_monthly(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None, kind: Optional[str] = None):
    g = {}
    for r in _docs(company_id, date_from, date_to, kind):
        m = str(r["return_date"])[:7] + "-01"
        x = g.setdefault(m, {"month": m, "returns": 0, "value": 0.0, "credited": 0.0})
        x["returns"] += 1; x["value"] += _v(r["value"]); x["credited"] += _v(r["credited"])
    return [{**x, "value": round(x["value"], 2), "credited": round(x["credited"], 2)} for _, x in sorted(g.items())]


@router.get("/api/returns/list")
def returns_list(pending_only: bool = False, company_id: Optional[str] = None, date_from: Optional[date] = None,
                 date_to: Optional[date] = None, kind: Optional[str] = None):
    rows = _docs(company_id, date_from, date_to, kind)
    if pending_only:
        rows = sorted([r for r in rows if r["credit_status"] in PENDING], key=lambda r: -_v(r["value"]))
    else:
        rows = sorted(rows, key=lambda r: str(r["return_date"]), reverse=True)
    today = date.today()
    out = []
    for r in rows[:2000]:
        d = {k: r[k] for k in ("return_no", "return_date", "kind", "form_no", "company", "vendor", "reason", "lines",
                               "qty", "value", "po_numbers", "origin", "credit_status", "debit_notes", "credited")}
        d["to_recover"] = round(_v(r["value"]) - _v(r["credited"]), 2)
        d["age_days"] = (today - date.fromisoformat(str(r["return_date"])[:10])).days if r["return_date"] else None
        out.append(d)
    return out
