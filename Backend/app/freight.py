"""Freight insights - READ-ONLY on the silver tables.

1. FIND freight: posted vendor-bill lines whose GL account or product name contains a freight word
   (freight, carriage, transport, courier ...) or whose vendor looks like a transporter.
   Words are set in config/database.ini [freight].
2. LINK each freight line to the purchase it belongs to, best evidence first:
     1 PO line on the freight line itself         (freight added to the PO)
     2 Same bill as material lines                 (freight charged inside the supplier's bill)
     3 Landed cost document                        (Odoo landed cost -> receipt -> PO)
     4 PO number written in the reference          (bill ref / origin / payment ref / narration / line text)
     5 Receipt (GRN) number written in the reference
     6 Supplier bill / invoice number written in the reference
   A freight line linked to several POs is shared out in proportion to PO value.
"""
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter

from .config import settings
from .db import q
from .silver import T, tr, has, source_sql

router = APIRouter()

METHODS = {1: "PO line on freight bill", 2: "Same bill as material", 3: "Landed cost",
           4: "PO no. in reference", 5: "Receipt (GRN) no. in reference", 6: "Supplier bill no. in reference"}


def _rx(words):
    return "(" + "|".join(re.escape(w) for w in words) + ")" if words else "(?!x)x"


def _params(company_id, date_from, date_to):
    return {"company_id": company_id, "date_from": date_from, "date_to": date_to,
            "kw": _rx(settings.freight_keywords), "tk": _rx(settings.transporter_keywords)}


def _fl_cte():
    """fl = freight lines on posted vendor bills (GL account / product name / transporter vendor words)."""
    txt_parts = ["am.ref", "aml.name"]
    if has("account_move", "invoice_origin"):
        txt_parts.append("am.invoice_origin")
    if has("account_move", "payment_reference"):
        txt_parts.append("am.payment_reference")
    if has("account_move", "narration"):
        txt_parts.append("regexp_replace(am.narration::text, '<[^>]+>', ' ', 'g')")
    txt = "concat_ws(' ', " + ", ".join(txt_parts) + ")"
    acc_name, prod_name = tr("acc.name"), tr("pt.name")
    return f"""fl AS (
    SELECT aml.id AS line_id, aml.move_id, am.name AS bill_number, am.ref AS bill_ref,
           am.invoice_date AS bill_date, am.company_id, {tr('c.name')} AS company,
           am.partner_id AS transporter_id, p.name AS transporter, aml.balance AS amount,
           aml.purchase_line_id, {acc_name} AS gl_account, {prod_name} AS product,
           CASE WHEN lower({acc_name}) ~ %(kw)s THEN 'GL account'
                WHEN lower({prod_name}) ~ %(kw)s THEN 'Product'
                ELSE 'Transporter vendor' END AS detected_by,
           {txt} AS txt
    FROM {T('account_move_line')} aml
    JOIN {T('account_move')} am         ON am.id = aml.move_id
    JOIN {T('res_company')} c           ON c.id = am.company_id
    JOIN {T('res_partner')} p           ON p.id = am.partner_id
    LEFT JOIN {T('account_account')} acc ON acc.id = aml.account_id
    LEFT JOIN {T('product_product')} pp  ON pp.id = aml.product_id
    LEFT JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
    WHERE am.move_type IN ('in_invoice', 'in_refund') AND am.state = 'posted'
      AND aml.display_type = 'product'
      AND (%(date_from)s::date IS NULL OR am.invoice_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR am.invoice_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR am.company_id = ANY(%(company_id)s::int[]))
      AND (lower({acc_name}) ~ %(kw)s OR lower({prod_name}) ~ %(kw)s OR lower(p.name) ~ %(tk)s)
)"""


def base_sql():
    """CTEs: fl (freight lines), m (all link evidence), lk (best links), alloc (freight shared over POs)."""
    from . import mart
    if mart.use("freight_lines") and mart.use("freight_alloc"):
        return f"""
WITH fl AS (SELECT * FROM {mart.M('freight_lines')}
    WHERE (%(date_from)s::date IS NULL OR bill_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR bill_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))),
alloc AS (SELECT * FROM {mart.M('freight_alloc')} WHERE line_id IN (SELECT line_id FROM fl))
"""
    txt_parts = ["am.ref", "aml.name"]
    if has("account_move", "invoice_origin"):
        txt_parts.append("am.invoice_origin")
    if has("account_move", "payment_reference"):
        txt_parts.append("am.payment_reference")
    if has("account_move", "narration"):
        txt_parts.append("regexp_replace(am.narration::text, '<[^>]+>', ' ', 'g')")
    txt = "concat_ws(' ', " + ", ".join(txt_parts) + ")"
    acc_name, prod_name = tr("acc.name"), tr("pt.name")

    landed = ""
    if has("stock_landed_cost", "vendor_bill_id") and has("stock_landed_cost_stock_picking_rel"):
        landed = f"""
    UNION
    SELECT fl.line_id, pol.order_id AS po_id, 3 AS prio
    FROM fl
    JOIN {T('stock_landed_cost')} lc ON lc.vendor_bill_id = fl.move_id
    JOIN {T('stock_landed_cost_stock_picking_rel')} r ON r.stock_landed_cost_id = lc.id
    JOIN {T('stock_move')} sm ON sm.picking_id = r.stock_picking_id
    JOIN {T('purchase_order_line')} pol ON pol.id = sm.purchase_line_id"""

    if has("res_company", "partner_id"):
        cp_join, cp_country = f"LEFT JOIN {T('res_partner')} cpar ON cpar.id = pc.partner_id", "cpar.country_id"
    else:
        cp_join, cp_country = "", "NULL::int"
    po_src = source_sql("po.currency_id", "pc.currency_id", "sup.country_id", cp_country, "NULL::text")

    return f"""
WITH {_fl_cte()},
tok AS (   -- reference-like tokens: letters + digits, >= 5 chars, may contain / - _
    SELECT DISTINCT fl.line_id, upper(x[1]) AS tok
    FROM fl CROSS JOIN LATERAL regexp_matches(fl.txt, '([A-Za-z0-9]+(?:[/_-][A-Za-z0-9]+)*)', 'g') AS x
    WHERE length(x[1]) >= 5 AND x[1] ~ '[0-9]' AND x[1] ~ '[A-Za-z]'
),
m AS (
    SELECT fl.line_id, pol.order_id AS po_id, 1 AS prio
    FROM fl JOIN {T('purchase_order_line')} pol ON pol.id = fl.purchase_line_id
    UNION
    SELECT fl.line_id, pol.order_id, 2
    FROM fl
    JOIN {T('account_move_line')} o ON o.move_id = fl.move_id AND o.id <> fl.line_id
                                   AND o.purchase_line_id IS NOT NULL
    JOIN {T('purchase_order_line')} pol ON pol.id = o.purchase_line_id
    {landed}
    UNION
    SELECT t.line_id, po.id, 4
    FROM tok t JOIN {T('purchase_order')} po ON upper(po.name) = t.tok
    UNION
    SELECT t.line_id, pol.order_id, 5
    FROM tok t
    JOIN {T('stock_picking')} sp ON upper(sp.name) = t.tok
    JOIN {T('stock_move')} sm ON sm.picking_id = sp.id
    JOIN {T('purchase_order_line')} pol ON pol.id = sm.purchase_line_id
    UNION
    SELECT t.line_id, pol.order_id, 6
    FROM tok t
    JOIN fl ON fl.line_id = t.line_id
    JOIN {T('account_move')} mb ON mb.move_type = 'in_invoice' AND mb.state = 'posted' AND mb.id <> fl.move_id
                               AND (upper(mb.name) = t.tok OR upper(mb.ref) = t.tok)
    JOIN {T('account_move_line')} ml ON ml.move_id = mb.id AND ml.purchase_line_id IS NOT NULL
    JOIN {T('purchase_order_line')} pol ON pol.id = ml.purchase_line_id
),
best AS (SELECT line_id, MIN(prio) AS prio FROM m GROUP BY line_id),
lk AS (SELECT DISTINCT m.line_id, m.po_id, m.prio FROM m JOIN best USING (line_id, prio)),
po_val AS (
    SELECT po.id AS po_id, po.name AS po_number, po.partner_id AS supplier_id, sup.name AS supplier,
           po.date_order::date AS po_date, {po_src} AS source, pcur.name AS po_currency,
           SUM(pol.price_subtotal / NULLIF(po.currency_rate, 0)) AS po_value,
           (ARRAY_AGG(categ.complete_name ORDER BY pol.price_subtotal DESC))[1] AS main_category
    FROM {T('purchase_order')} po
    JOIN {T('res_partner')} sup ON sup.id = po.partner_id
    JOIN {T('res_company')} pc ON pc.id = po.company_id
    LEFT JOIN {T('res_currency')} pcur ON pcur.id = po.currency_id
    {cp_join}
    JOIN {T('purchase_order_line')} pol ON pol.order_id = po.id
    LEFT JOIN {T('product_product')} pp2 ON pp2.id = pol.product_id
    LEFT JOIN {T('product_template')} pt2 ON pt2.id = pp2.product_tmpl_id
    LEFT JOIN {T('product_category')} categ ON categ.id = pt2.categ_id
    WHERE po.id IN (SELECT po_id FROM lk)
    GROUP BY po.id, po.name, po.partner_id, sup.name, po.date_order, 6, pcur.name
),
alloc AS (
    SELECT lk.line_id, lk.prio, pv.*,
           fl.amount * COALESCE(pv.po_value / NULLIF(SUM(pv.po_value) OVER (PARTITION BY lk.line_id), 0),
                                1.0 / COUNT(*) OVER (PARTITION BY lk.line_id)) AS freight_alloc
    FROM lk JOIN po_val pv USING (po_id) JOIN fl USING (line_id)
)
"""


MATERIAL = f"""
, mat AS (
    SELECT DATE_TRUNC('month', am.invoice_date)::date AS month, SUM(aml.balance) AS spend
    FROM {{aml}} aml JOIN {{am}} am ON am.id = aml.move_id
    WHERE am.move_type IN ('in_invoice', 'in_refund') AND am.state = 'posted' AND aml.display_type = 'product'
      AND (%(date_from)s::date IS NULL OR am.invoice_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR am.invoice_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR am.company_id = ANY(%(company_id)s::int[]))
      AND NOT EXISTS (SELECT 1 FROM fl WHERE fl.line_id = aml.id)
    GROUP BY 1
)"""


def mat_sql():
    return MATERIAL.replace("{aml}", T("account_move_line")).replace("{am}", T("account_move"))


FX_COLS = """fl.line_id, fl.move_id, fl.bill_date, fl.bill_number, fl.bill_ref, fl.company, fl.transporter_id,
             fl.transporter, fl.amount, fl.detected_by, fl.gl_account, fl.product, LEFT(fl.txt, 200) AS reference_text,
             a.prio, a.po_id, a.po_number, a.supplier, a.source, a.po_currency, a.main_category, a.po_value,
             a.freight_alloc"""


def _fx(company_id, date_from, date_to):
    """ONE read: every freight line with the PO(s) it was linked to (cached). All freight tables are built from it."""
    return q(base_sql() + f"SELECT {FX_COLS} FROM fl LEFT JOIN alloc a ON a.line_id = fl.line_id",
             _params(company_id, date_from, date_to))


def _material(company_id, date_from, date_to):
    return q(base_sql() + mat_sql() + " SELECT month, spend FROM mat", _params(company_id, date_from, date_to))


def _n(v):
    return float(v or 0)


def _lines(rows):
    """one entry per freight line: the line + its list of PO allocations"""
    out = {}
    for r in rows:
        x = out.setdefault(r["line_id"], {"row": r, "allocs": []})
        if r["po_id"] is not None:
            x["allocs"].append(r)
    return list(out.values())


@router.get("/api/freight/summary")
def freight_summary(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    ls = _lines(_fx(company_id, date_from, date_to))
    linked = [x for x in ls if x["allocs"]]
    allocs = [a for x in ls for a in x["allocs"]]
    return {
        "freight": round(sum(_n(x["row"]["amount"]) for x in ls), 2),
        "freight_bills": len({x["row"]["move_id"] for x in ls}),
        "transporters": len({x["row"]["transporter_id"] for x in ls}),
        "linked": round(sum(_n(x["row"]["amount"]) for x in linked), 2),
        "lines": len(ls), "linked_lines": len(linked),
        "freight_import": round(sum(_n(a["freight_alloc"]) for a in allocs if a["source"] == "Import"), 2),
        "freight_local": round(sum(_n(a["freight_alloc"]) for a in allocs if a["source"] == "Local"), 2),
        "material": round(sum(_n(m["spend"]) for m in _material(company_id, date_from, date_to)), 2),
        "keywords": settings.freight_keywords, "transporter_keywords": settings.transporter_keywords,
    }


@router.get("/api/freight/monthly")
def freight_monthly(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    g = {}
    for x in _lines(_fx(company_id, date_from, date_to)):
        r = x["row"]
        m = str(r["bill_date"])[:7] + "-01"
        e = g.setdefault(m, {"freight": 0.0, "bills": set(), "material": 0.0})
        e["freight"] += _n(r["amount"]); e["bills"].add(r["move_id"])
    for mrow in _material(company_id, date_from, date_to):
        m = str(mrow["month"])[:10]
        g.setdefault(m, {"freight": 0.0, "bills": set(), "material": 0.0})["material"] += _n(mrow["spend"])
    out = []
    for m, e in sorted(g.items()):
        out.append({"month": m, "freight": round(e["freight"], 2), "bills": len(e["bills"]),
                    "material": round(e["material"], 2),
                    "freight_pct": round(100.0 * e["freight"] / e["material"], 2) if e["material"] else None})
    return out


@router.get("/api/freight/transporters")
def freight_transporters(company_id: Optional[str] = None, date_from: Optional[date] = None,
                         date_to: Optional[date] = None):
    g = {}
    for x in _lines(_fx(company_id, date_from, date_to)):
        r = x["row"]
        e = g.setdefault(r["transporter"], {"transporter": r["transporter"], "bills": set(), "freight": 0.0,
                                            "linked": 0.0, "detected_by": set(), "last_bill": None})
        e["bills"].add(r["move_id"]); e["freight"] += _n(r["amount"]); e["detected_by"].add(r["detected_by"])
        if x["allocs"]:
            e["linked"] += _n(r["amount"])
        if r["bill_date"] and (e["last_bill"] is None or str(r["bill_date"]) > str(e["last_bill"])):
            e["last_bill"] = r["bill_date"]
    out = [{**e, "bills": len(e["bills"]), "freight": round(e["freight"], 2), "linked": round(e["linked"], 2),
            "linked_pct": round(100.0 * e["linked"] / e["freight"], 1) if e["freight"] else None,
            "detected_by": ", ".join(sorted(d for d in e["detected_by"] if d))} for e in g.values()]
    return sorted(out, key=lambda e: -e["freight"])[:500]


@router.get("/api/freight/suppliers")
def freight_suppliers(company_id: Optional[str] = None, date_from: Optional[date] = None,
                      date_to: Optional[date] = None):
    """Material suppliers: freight linked to their POs as % of PO value."""
    per_po = {}
    for x in _lines(_fx(company_id, date_from, date_to)):
        for a in x["allocs"]:
            e = per_po.setdefault(a["po_id"], {**a, "freight": 0.0})
            e["freight"] += _n(a["freight_alloc"])
    g = {}
    for e in per_po.values():
        s = g.setdefault(e["supplier"], {"supplier": e["supplier"], "source": set(), "currency": set(), "pos": 0,
                                         "po_value": 0.0, "freight": 0.0, "categories": set()})
        s["source"].add(e["source"]); s["currency"].add(e["po_currency"]); s["pos"] += 1
        s["po_value"] += _n(e["po_value"]); s["freight"] += e["freight"]; s["categories"].add(e["main_category"])
    j = lambda v: ", ".join(sorted(x for x in v if x))
    out = [{"supplier": s["supplier"], "source": j(s["source"]), "currency": j(s["currency"]), "pos": s["pos"],
            "po_value": round(s["po_value"], 2), "freight": round(s["freight"], 2),
            "freight_pct": round(100.0 * s["freight"] / s["po_value"], 2) if s["po_value"] else None,
            "categories": j(s["categories"])} for s in g.values()]
    return sorted(out, key=lambda e: -e["freight"])[:500]


@router.get("/api/freight/bills")
def freight_bills(linked: bool = True, company_id: Optional[str] = None,
                  date_from: Optional[date] = None, date_to: Optional[date] = None):
    """Freight lines with the purchase they were linked to (linked=true) or still unlinked (linked=false)."""
    ls = _lines(_fx(company_id, date_from, date_to))
    j = lambda v: ", ".join(sorted({x for x in v if x}))
    if linked:
        out = []
        for x in ls:
            if not x["allocs"]:
                continue
            r, al = x["row"], x["allocs"]
            pv = sum(_n(a["po_value"]) for a in al)
            out.append({"bill_date": r["bill_date"], "bill_number": r["bill_number"], "bill_ref": r["bill_ref"],
                        "company": r["company"], "transporter": r["transporter"], "amount": r["amount"],
                        "detected_by": r["detected_by"], "linked_by": METHODS.get(min(a["prio"] for a in al)),
                        "po_numbers": j(a["po_number"] for a in al), "suppliers": j(a["supplier"] for a in al),
                        "source": j(a["source"] for a in al), "categories": j(a["main_category"] for a in al),
                        "po_value": round(pv, 2), "freight_pct": round(100.0 * _n(r["amount"]) / pv, 2) if pv else None,
                        "reference_text": (r["reference_text"] or "")[:160]})
        return sorted(out, key=lambda e: (str(e["bill_date"]), e["bill_number"] or ""), reverse=True)[:2000]
    out = [{k: x["row"][k] for k in ("bill_date", "bill_number", "bill_ref", "company", "transporter", "amount",
                                       "detected_by", "gl_account", "product", "reference_text")}
           for x in ls if not x["allocs"]]
    return sorted(out, key=lambda e: -_n(e["amount"]))[:2000]


@router.get("/api/freight/methods")
def freight_methods(company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None):
    """How the links were found (to judge how reliable the matching is)."""
    g = {}
    for x in _lines(_fx(company_id, date_from, date_to)):
        prio = min(a["prio"] for a in x["allocs"]) if x["allocs"] else None
        e = g.setdefault(prio, {"method": METHODS.get(prio, "Not linked"), "lines": 0, "freight": 0.0})
        e["lines"] += 1; e["freight"] += _n(x["row"]["amount"])
    return [{**g[k], "freight": round(g[k]["freight"], 2)} for k in sorted(g, key=lambda k: (k is None, k or 0))]


# ---------------------------------------------------------------- freight per product (exact links only)
EXACT_METHODS = (1, 2, 3)


def product_freight_sql():
    """Freight shared onto material lines using ONLY exact Odoo links (no reference-text matching):
         1 freight line has a PO line      -> the other (non-freight) lines of that PO, by PO line value
         2 freight line on the same bill   -> the material lines of that bill, by line value
         3 Odoo landed cost on the bill    -> the receipt moves of that landed cost, by PO value of the move
       Each freight line uses its best (lowest) method. Result per product: freight, and qty / value of the
       material lines that carry it (each material line counted once)."""
    prod_name = tr("ptx.name")
    landed = ""
    if has("stock_landed_cost", "vendor_bill_id") and has("stock_landed_cost_stock_picking_rel"):
        from .returns import qty_expr
        landed = f"""
    UNION ALL
    SELECT fl.line_id, 'm' || sm.id, sm.product_id, {qty_expr()},
           {qty_expr()} * (pol.price_subtotal / NULLIF(pol.product_uom_qty, 0) / NULLIF(po.currency_rate, 0)), 3
    FROM fl
    JOIN {T('stock_landed_cost')} lc ON lc.vendor_bill_id = fl.move_id
    JOIN {T('stock_landed_cost_stock_picking_rel')} r ON r.stock_landed_cost_id = lc.id
    JOIN {T('stock_move')} sm ON sm.picking_id = r.stock_picking_id AND sm.state = 'done'
    JOIN {T('purchase_order_line')} pol ON pol.id = sm.purchase_line_id
    JOIN {T('purchase_order')} po ON po.id = pol.order_id"""
    return f"""
WITH {_fl_cte()},
tg AS (
    SELECT fl.line_id, 'p' || pol2.id AS target, pol2.product_id, pol2.product_uom_qty::numeric AS qty,
           (pol2.price_subtotal / NULLIF(po.currency_rate, 0))::numeric AS val, 1 AS prio
    FROM fl
    JOIN {T('purchase_order_line')} f ON f.id = fl.purchase_line_id
    JOIN {T('purchase_order')} po ON po.id = f.order_id
    JOIN {T('purchase_order_line')} pol2 ON pol2.order_id = po.id AND pol2.id <> f.id AND pol2.product_id IS NOT NULL
    LEFT JOIN {T('product_product')} ppx ON ppx.id = pol2.product_id
    LEFT JOIN {T('product_template')} ptx ON ptx.id = ppx.product_tmpl_id
    WHERE NOT (lower(COALESCE({prod_name}, '')) ~ %(kw)s)
    UNION ALL
    SELECT fl.line_id, 'b' || o.id, o.product_id, o.quantity::numeric, o.balance::numeric, 2
    FROM fl
    JOIN {T('account_move_line')} o ON o.move_id = fl.move_id AND o.display_type = 'product'
         AND o.product_id IS NOT NULL AND o.id NOT IN (SELECT line_id FROM fl){landed}
),
best AS (SELECT line_id, MIN(prio) AS prio FROM tg GROUP BY line_id),
tb AS (SELECT tg.* FROM tg JOIN best USING (line_id, prio) WHERE tg.val > 0),
al AS (
    SELECT tb.target, tb.product_id, tb.qty, tb.val, tb.prio,
           fl.amount * tb.val / NULLIF(SUM(tb.val) OVER (PARTITION BY tb.line_id), 0) AS freight
    FROM tb JOIN fl USING (line_id)
),
per_target AS (
    SELECT target, product_id, MAX(qty) AS qty, MAX(val) AS val, SUM(freight) AS freight
    FROM al GROUP BY target, product_id
),
fprod AS (
    SELECT product_id, SUM(freight) AS freight, SUM(qty) AS linked_qty, SUM(val) AS linked_value
    FROM per_target GROUP BY product_id
)
"""


def freight_for_products(product_ids, company_id=None, date_from=None, date_to=None):
    """{product_id: {freight, linked_qty, linked_value, freight_per_unit, freight_pct}} - freight bills in the period."""
    if not product_ids:
        return {}
    p = _params(company_id, date_from, date_to)
    p["pids"] = list(product_ids)
    rows = q(product_freight_sql() + """
        SELECT product_id, freight, linked_qty, linked_value,
               ROUND((freight / NULLIF(linked_qty, 0))::numeric, 4) AS freight_per_unit,
               ROUND((100.0 * freight / NULLIF(linked_value, 0))::numeric, 2) AS freight_pct
        FROM fprod WHERE product_id = ANY (%(pids)s::int[])""", p)
    return {r["product_id"]: r for r in rows}


@router.get("/api/freight/product/{product_id}")
def freight_product(product_id: int, company_id: Optional[str] = None, date_from: Optional[date] = None,
                    date_to: Optional[date] = None):
    return freight_for_products([product_id], company_id, date_from, date_to).get(product_id) or {}
