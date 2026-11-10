"""Job work POs (order type flagged is_subcontracting / named 'job work') and DCs sent to vendors. READ-ONLY."""
from datetime import date
from typing import Optional

from fastapi import APIRouter

from .db import q
from .returns import base_sql as vendor_docs_sql
from .silver import T, tr, has

router = APIRouter()


def _p(company_id, date_from, date_to):
    return {"company_id": company_id, "date_from": date_from, "date_to": date_to, "kind": None}


def jw_sql():
    # job work = order type flagged is_subcontracting in Odoo (fallback only if that column does not exist)
    jw_where = "COALESCE(ot.is_subcontracting, false)" if has("purchase_order_type", "is_subcontracting") \
        else f"{tr('ot.name')} ILIKE '%%job work%%'"
    po_rate = "NULLIF(po.currency_rate, 0)" if has("purchase_order", "currency_rate") else "1"
    disp = "AND pol.display_type IS NULL" if has("purchase_order_line", "display_type") else ""
    return f"""
WITH jw AS (
    SELECT po.id AS po_id, po.name AS po_number, COALESCE(po.date_approve, po.date_order)::date AS po_date,
           po.state, po.company_id, {tr('c.name')} AS company, po.partner_id AS vendor_id, p.name AS vendor,
           {tr('ot.name')} AS order_type, pol.id AS line_id, pp.default_code AS item_code, {tr('pt.name')} AS product,
           pol.name AS description,
           pol.product_uom_qty::numeric AS ordered, COALESCE(pol.qty_received, 0)::numeric AS received,
           COALESCE(pol.qty_invoiced, 0)::numeric AS billed_qty,
           (pol.price_subtotal / NULLIF(pol.product_uom_qty, 0) / {po_rate})::numeric AS unit_inr
    FROM {T('purchase_order')} po
    JOIN {T('purchase_order_type')} ot ON ot.id = po.order_type_id
    JOIN {T('purchase_order_line')} pol ON pol.order_id = po.id {disp}
    JOIN {T('res_company')} c ON c.id = po.company_id
    LEFT JOIN {T('res_partner')} p ON p.id = po.partner_id
    LEFT JOIN {T('product_product')} pp ON pp.id = pol.product_id
    LEFT JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
    WHERE {jw_where} AND po.state IN ('purchase', 'done')
      AND (%(date_from)s::date IS NULL OR COALESCE(po.date_approve, po.date_order) >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR COALESCE(po.date_approve, po.date_order) <  %(date_to)s::date + 1)
      AND (%(company_id)s::int[] IS NULL OR po.company_id = ANY(%(company_id)s::int[]))
),
jl AS (
    SELECT jw.*, ordered * unit_inr AS ordered_value, LEAST(received, ordered) * unit_inr AS received_value,
           billed_qty * unit_inr AS billed_value, GREATEST(ordered - received, 0) AS pending_qty,
           GREATEST(ordered - received, 0) * unit_inr AS pending_value,
           GREATEST(received - billed_qty, 0) * unit_inr AS received_not_billed,
           CURRENT_DATE - po_date AS age_days
    FROM jw
)
"""


@router.get("/api/jobwork/summary")
def jobwork_summary(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None):
    p = _p(company_id, date_from, date_to)
    r = q(jw_sql() + """
        SELECT COUNT(DISTINCT po_id) AS pos, COUNT(DISTINCT vendor_id) AS vendors,
               COALESCE(SUM(ordered_value), 0) AS ordered_value, COALESCE(SUM(received_value), 0) AS received_value,
               COALESCE(SUM(billed_value), 0) AS billed_value,
               COUNT(DISTINCT po_id) FILTER (WHERE pending_qty > 0) AS open_pos,
               COALESCE(SUM(pending_value), 0) AS pending_value,
               COALESCE(SUM(pending_value) FILTER (WHERE age_days > 60), 0) AS pending_over_60,
               COALESCE(SUM(received_not_billed), 0) AS received_not_billed
        FROM jl""", p)[0]
    d = q(vendor_docs_sql("dc") + """
        SELECT COUNT(*) AS dcs, COUNT(DISTINCT vendor_id) AS dc_vendors, COALESCE(SUM(value), 0) AS dc_value
        FROM ret""", p)[0]
    return {**r, **d}


@router.get("/api/jobwork/vendors")
def jobwork_vendors(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None):
    return q(jw_sql() + """
        SELECT vendor, COUNT(DISTINCT po_id) AS pos, COUNT(*) AS lines, SUM(ordered_value) AS ordered_value,
               SUM(received_value) AS received_value, SUM(billed_value) AS billed_value,
               SUM(pending_value) AS pending_value, COUNT(DISTINCT po_id) FILTER (WHERE pending_qty > 0) AS open_pos,
               MAX(age_days) FILTER (WHERE pending_qty > 0) AS oldest_open_days,
               SUM(received_not_billed) AS received_not_billed,
               ROUND((100.0 * SUM(received_value) / NULLIF(SUM(ordered_value), 0))::numeric, 1) AS completion_pct
        FROM jl GROUP BY vendor ORDER BY ordered_value DESC NULLS LAST LIMIT 500""",
             _p(company_id, date_from, date_to))


@router.get("/api/jobwork/open")
def jobwork_open(company_id: Optional[str] = None, date_from: Optional[date] = None,
                 date_to: Optional[date] = None):
    """Job work lines not fully received back."""
    return q(jw_sql() + """
        SELECT po_number, po_date, age_days, vendor, order_type, item_code, COALESCE(product, description) AS product,
               ordered, received, pending_qty, unit_inr, pending_value, billed_qty, company
        FROM jl WHERE pending_qty > 0 ORDER BY age_days DESC LIMIT 2000""", _p(company_id, date_from, date_to))


@router.get("/api/jobwork/products")
def jobwork_products(company_id: Optional[str] = None, date_from: Optional[date] = None,
                     date_to: Optional[date] = None):
    """Job work rate per item and vendor - spot rate differences between job workers."""
    return q(jw_sql() + """
        SELECT item_code, COALESCE(product, MAX(description)) AS product, vendor, COUNT(DISTINCT po_id) AS pos,
               SUM(ordered) AS qty, ROUND((SUM(ordered_value) / NULLIF(SUM(ordered), 0))::numeric, 2) AS avg_rate,
               MIN(unit_inr) AS min_rate, MAX(unit_inr) AS max_rate, MAX(po_date) AS last_po
        FROM jl GROUP BY item_code, product, vendor
        ORDER BY item_code, avg_rate LIMIT 2000""", _p(company_id, date_from, date_to))


@router.get("/api/jobwork/dcs")
def jobwork_dcs(company_id: Optional[str] = None, date_from: Optional[date] = None,
                date_to: Optional[date] = None):
    """Material sent to a vendor location that is NOT a return (DC / rework / job work), as recorded in Odoo.
    Whether it came back is NOT shown: Odoo has no link from the DC to the receipt that brings it back."""
    return q(vendor_docs_sql("dc") + """
        SELECT r.doc_no AS dc_no, r.doc_date AS dc_date, r.kind, r.vendor, r.origin AS reference, r.po_numbers,
               r.lines, r.qty, r.value, CURRENT_DATE - r.doc_date AS days_since_sent, r.company
        FROM ret r ORDER BY r.doc_date DESC LIMIT 2000""", _p(company_id, date_from, date_to))
