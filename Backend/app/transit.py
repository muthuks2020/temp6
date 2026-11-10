"""Stock in the Inter Company Transit location. READ-ONLY. Exact stock movements only - no pairing guesses.

In transit (as of a date) = done moves INTO the transit location - done moves OUT of it, per product.
Sent / received in a period = done transfers internal -> transit / transit -> internal, per company and type.
Value = quantity x latest purchase price of the item (indicative; Odoo's own stock valuation is not used).
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter

from .db import q
from .returns import qty_expr, SANE_DATE
from .silver import T, tr

router = APIRouter()


def _p(company_id, date_from, date_to):
    return {"company_id": company_id, "date_from": date_from, "date_to": date_to}


def tm_sql():
    """every done move into or out of a transit location (CTE tm body) - saved by procurement_mart.transit_moves"""
    return f"""   -- every done move into or out of a transit location
    SELECT sp.id AS picking_id, sp.date_done::date AS doc_date, sp.company_id, {tr('c.name')} AS company,
           {tr('pt.name')} AS picking_type, sm.product_id, {qty_expr()} AS qty,
           CASE WHEN dst.usage = 'transit' THEN 'sent' ELSE 'received' END AS direction
    FROM {T('stock_picking')} sp
    JOIN {T('stock_move')} sm ON sm.picking_id = sp.id AND sm.state = 'done'
    JOIN {T('stock_location')} src ON src.id = sp.location_id
    JOIN {T('stock_location')} dst ON dst.id = sp.location_dest_id
    JOIN {T('res_company')} c ON c.id = sp.company_id
    LEFT JOIN {T('stock_picking_type')} pt ON pt.id = sp.picking_type_id
    WHERE sp.state = 'done' AND {SANE_DATE}
      AND ((src.usage <> 'transit' AND dst.usage = 'transit') OR (src.usage = 'transit' AND dst.usage <> 'transit'))
      AND (%(date_to)s::date IS NULL OR sp.date_done < %(date_to)s::date + 1)
"""


def moves_sql():
    po_rate = "NULLIF(po.currency_rate, 0)"
    from . import mart
    if mart.use("transit_moves"):
        tm_src = f"""SELECT * FROM {mart.M('transit_moves')}
    WHERE (%(date_to)s::date IS NULL OR doc_date <= %(date_to)s::date)"""
    else:
        tm_src = tm_sql()
    return f"""
WITH tm AS ({tm_src}),
cost AS (      -- latest purchase price per product, INR / unit
    SELECT DISTINCT ON (pol.product_id) pol.product_id,
           (pol.price_subtotal / NULLIF(pol.product_uom_qty, 0) / {po_rate})::numeric AS unit_cost
    FROM {T('purchase_order_line')} pol JOIN {T('purchase_order')} po ON po.id = pol.order_id
    WHERE po.state IN ('purchase', 'done') AND pol.product_uom_qty > 0
      AND pol.product_id IN (SELECT DISTINCT product_id FROM tm)
    ORDER BY pol.product_id, po.date_order DESC
),
tv AS (SELECT tm.*, tm.qty * cost.unit_cost AS value FROM tm LEFT JOIN cost ON cost.product_id = tm.product_id),
per AS (       -- the selected period / company
    SELECT * FROM tv
    WHERE (%(date_from)s::date IS NULL OR doc_date >= %(date_from)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))
),
bal AS (       -- all companies together: what is physically in transit at the end date
    SELECT product_id, SUM(qty) FILTER (WHERE direction = 'sent') AS sent_qty,
           COALESCE(SUM(qty) FILTER (WHERE direction = 'received'), 0) AS received_qty,
           SUM(CASE WHEN direction = 'sent' THEN qty ELSE -qty END) AS in_transit_qty,
           MAX(doc_date) FILTER (WHERE direction = 'sent') AS last_sent,
           MAX(doc_date) FILTER (WHERE direction = 'received') AS last_received
    FROM tv GROUP BY product_id
)
"""


@router.get("/api/transit/summary")
def transit_summary(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None):
    p = _p(company_id, date_from, date_to)
    r = q(moves_sql() + """
        SELECT COUNT(DISTINCT picking_id) FILTER (WHERE direction = 'sent') AS sent,
               COALESCE(SUM(value) FILTER (WHERE direction = 'sent'), 0) AS sent_value,
               COUNT(DISTINCT picking_id) FILTER (WHERE direction = 'received') AS received,
               COALESCE(SUM(value) FILTER (WHERE direction = 'received'), 0) AS received_value
        FROM per""", p)[0]
    b = q(moves_sql() + """
        SELECT COUNT(*) FILTER (WHERE b.in_transit_qty > 0) AS products_in_transit,
               COALESCE(SUM(b.in_transit_qty * c.unit_cost) FILTER (WHERE b.in_transit_qty > 0), 0) AS in_transit_value,
               COUNT(*) FILTER (WHERE b.in_transit_qty < 0) AS negative_products
        FROM bal b LEFT JOIN cost c ON c.product_id = b.product_id""", p)[0]
    return {**r, **b}


@router.get("/api/transit/balance")
def transit_balance(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None):
    """Items sitting in the transit location (all companies) at the end date. Negative = received more than sent."""
    return q(moves_sql() + f"""
        SELECT pp.default_code AS item_code, {tr('pt.name')} AS product, b.sent_qty, b.received_qty,
               b.in_transit_qty, c.unit_cost, b.in_transit_qty * c.unit_cost AS value,
               b.last_sent, b.last_received, CURRENT_DATE - b.last_sent AS days_since_last_sent
        FROM bal b
        LEFT JOIN cost c ON c.product_id = b.product_id
        LEFT JOIN {T('product_product')} pp ON pp.id = b.product_id
        LEFT JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
        WHERE b.in_transit_qty <> 0
        ORDER BY ABS(b.in_transit_qty * COALESCE(c.unit_cost, 0)) DESC, b.in_transit_qty DESC LIMIT 2000""",
             _p(company_id, date_from, date_to))


@router.get("/api/transit/companies")
def transit_companies(company_id: Optional[str] = None, date_from: Optional[date] = None,
                      date_to: Optional[date] = None):
    return q(moves_sql() + """
        SELECT company,
               COUNT(DISTINCT picking_id) FILTER (WHERE direction = 'sent') AS sent,
               SUM(qty) FILTER (WHERE direction = 'sent') AS sent_qty,
               SUM(value) FILTER (WHERE direction = 'sent') AS sent_value,
               COUNT(DISTINCT picking_id) FILTER (WHERE direction = 'received') AS received,
               SUM(qty) FILTER (WHERE direction = 'received') AS received_qty,
               SUM(value) FILTER (WHERE direction = 'received') AS received_value
        FROM per GROUP BY company ORDER BY sent_value DESC NULLS LAST""", _p(company_id, date_from, date_to))


@router.get("/api/transit/types")
def transit_types(company_id: Optional[str] = None, date_from: Optional[date] = None,
                  date_to: Optional[date] = None):
    return q(moves_sql() + """
        SELECT picking_type, direction, COUNT(DISTINCT picking_id) AS transfers, SUM(qty) AS qty, SUM(value) AS value
        FROM per GROUP BY 1, 2 ORDER BY value DESC NULLS LAST""", _p(company_id, date_from, date_to))
