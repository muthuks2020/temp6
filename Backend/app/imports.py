"""Import landed cost: purchase_import_duty (customs breakup per import PO) + Odoo landed costs.
READ-ONLY.

Landed cost (INR) = assessable value + BCD + SWS + cess + misc charges + other landed costs
IGST on imports is shown separately (normally claimable as input credit, so not a cost).
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter

from .db import q
from .silver import T, tr, has

router = APIRouter()


def _p(company_id, date_from, date_to):
    return {"company_id": company_id, "date_from": date_from, "date_to": date_to}


def base_sql():
    n = lambda c: f"COALESCE(d.{c}, 0)::numeric" if has("purchase_import_duty", c) else "0::numeric"
    other_lc = ""
    if has("stock_landed_cost") and has("stock_landed_cost_stock_picking_rel"):
        other_lc = f"""
, lc_po AS (   -- Odoo landed costs (freight, clearing...) attached to receipts of the PO
    SELECT DISTINCT lc.id AS lc_id, pol.order_id AS po_id, lc.amount_total
    FROM {T('stock_landed_cost')} lc
    JOIN {T('stock_landed_cost_stock_picking_rel')} r ON r.stock_landed_cost_id = lc.id
    JOIN {T('stock_move')} sm ON sm.picking_id = r.stock_picking_id
    JOIN {T('purchase_order_line')} pol ON pol.id = sm.purchase_line_id
    WHERE lc.state = 'done'
),
lc AS (SELECT po_id, SUM(amount_total / cnt) AS other_landed
       FROM (SELECT *, COUNT(*) OVER (PARTITION BY lc_id) AS cnt FROM lc_po) x GROUP BY po_id)"""
    else:
        other_lc = "\n, lc AS (SELECT NULL::int AS po_id, 0::numeric AS other_landed WHERE false)"
    return f"""
WITH duty AS (
    SELECT d.id AS duty_id, d.purchase_id AS po_id, po.name AS po_number, po.company_id,
           {tr('c.name')} AS company, sup.name AS supplier, cur.name AS currency,
           d.date AS boe_date, d.boe_reference, d.state,
           {n('currency_exchange_rate')} AS exchange_rate,
           {n('fob_amount')} AS fob, {n('freight_amount')} AS freight, {n('insurance_amount')} AS insurance,
           {n('cif_amount')} AS cif, {n('assessment_amount')} AS assessable,
           {n('bcd_amount')} AS bcd, {n('sws_amount')} AS sws, {n('cess_amount')} AS cess,
           {n('igst_amount')} AS igst, {n('misc_charges')} AS misc,
           {n('duty_payable')} AS duty_payable, {n('total_amount')} AS total_amount
    FROM {T('purchase_import_duty')} d
    JOIN {T('purchase_order')} po ON po.id = d.purchase_id
    JOIN {T('res_company')} c ON c.id = po.company_id
    LEFT JOIN {T('res_partner')} sup ON sup.id = po.partner_id
    LEFT JOIN {T('res_currency')} cur ON cur.id = po.currency_id
    WHERE (%(date_from)s::date IS NULL OR d.date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR d.date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR po.company_id = ANY(%(company_id)s::int[]))
){other_lc},
imp AS (
    SELECT duty.*, COALESCE(lc.other_landed, 0) AS other_landed,
           duty.bcd + duty.sws + duty.cess AS customs_duty,
           duty.assessable + duty.bcd + duty.sws + duty.cess + duty.misc + COALESCE(lc.other_landed, 0) AS landed,
           ROUND((100.0 * (duty.bcd + duty.sws + duty.cess + duty.misc + COALESCE(lc.other_landed, 0))
                 / NULLIF(duty.assessable, 0))::numeric, 2) AS uplift_pct
    FROM duty LEFT JOIN lc ON lc.po_id = duty.po_id
)
"""


@router.get("/api/imports/summary")
def imports_summary(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None):
    if not has("purchase_import_duty"):
        return {"available": False}
    r = q(base_sql() + """
        SELECT COUNT(*) AS duty_entries, COUNT(DISTINCT po_id) AS pos, COUNT(DISTINCT supplier) AS suppliers,
               COALESCE(SUM(assessable), 0) AS assessable, COALESCE(SUM(customs_duty), 0) AS customs_duty,
               COALESCE(SUM(igst), 0) AS igst, COALESCE(SUM(freight + insurance), 0) AS freight_insurance,
               COALESCE(SUM(misc + other_landed), 0) AS other_costs, COALESCE(SUM(landed), 0) AS landed,
               ROUND((100.0 * SUM(landed - assessable) / NULLIF(SUM(assessable), 0))::numeric, 2) AS uplift_pct
        FROM imp""", _p(company_id, date_from, date_to))[0]
    r["available"] = True
    return r


@router.get("/api/imports/pos")
def imports_pos(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    if not has("purchase_import_duty"):
        return []
    return q(base_sql() + """
        SELECT boe_date, boe_reference, po_number, supplier, currency, exchange_rate, fob, freight, insurance,
               cif, assessable, bcd, sws, cess, customs_duty, misc, other_landed, landed, uplift_pct, igst,
               duty_payable, state, company
        FROM imp ORDER BY boe_date DESC NULLS LAST LIMIT 2000""", _p(company_id, date_from, date_to))


@router.get("/api/imports/suppliers")
def imports_suppliers(company_id: Optional[str] = None, date_from: Optional[date] = None,
                      date_to: Optional[date] = None):
    if not has("purchase_import_duty"):
        return []
    return q(base_sql() + """
        SELECT supplier, STRING_AGG(DISTINCT currency, ', ') AS currency, COUNT(DISTINCT po_id) AS pos,
               SUM(assessable) AS assessable, SUM(customs_duty) AS customs_duty, SUM(landed) AS landed,
               ROUND((100.0 * SUM(landed - assessable) / NULLIF(SUM(assessable), 0))::numeric, 2) AS uplift_pct,
               ROUND((AVG(exchange_rate))::numeric, 4) AS avg_rate, MIN(exchange_rate) AS min_rate, MAX(exchange_rate) AS max_rate
        FROM imp GROUP BY supplier ORDER BY landed DESC NULLS LAST""", _p(company_id, date_from, date_to))


@router.get("/api/imports/products")
def imports_products(company_id: Optional[str] = None, date_from: Optional[date] = None,
                     date_to: Optional[date] = None):
    """Landed cost per unit: PO price (foreign ccy x customs rate) uplifted by the PO's duty & charges %."""
    if not has("purchase_import_duty"):
        return []
    return q(base_sql() + f"""
        , pu AS (SELECT po_id, MAX(uplift_pct) AS uplift_pct, MAX(exchange_rate) AS rate,
                        MAX(supplier) AS supplier, MAX(boe_date) AS boe_date FROM imp GROUP BY po_id)
        SELECT pp.default_code AS item_code, {tr('pt.name')} AS product,
               STRING_AGG(DISTINCT pu.supplier, ', ') AS suppliers, COUNT(DISTINCT pol.order_id) AS pos,
               SUM(pol.product_uom_qty) AS qty,
               ROUND((SUM(pol.price_subtotal * NULLIF(pu.rate, 0)) / NULLIF(SUM(pol.product_uom_qty), 0))::numeric, 4) AS base_unit_inr,
               ROUND((SUM(pol.price_subtotal * NULLIF(pu.rate, 0) * (1 + COALESCE(pu.uplift_pct, 0) / 100))
                     / NULLIF(SUM(pol.product_uom_qty), 0))::numeric, 4) AS landed_unit_inr,
               ROUND((100.0 * SUM(pol.price_subtotal * pu.rate * COALESCE(pu.uplift_pct, 0) / 100)
                     / NULLIF(SUM(pol.price_subtotal * pu.rate), 0))::numeric, 2) AS uplift_pct,
               MAX(pu.boe_date) AS last_import
        FROM pu
        JOIN {T('purchase_order_line')} pol ON pol.order_id = pu.po_id
        LEFT JOIN {T('product_product')} pp ON pp.id = pol.product_id
        LEFT JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
        WHERE pol.product_uom_qty > 0
        GROUP BY 1, 2 ORDER BY SUM(pol.price_subtotal * pu.rate) DESC NULLS LAST LIMIT 1000""",
             _p(company_id, date_from, date_to))
