"""Price Impact - base period vs current period, per product (the finance "Purchase Price Analysis" view).
READ-ONLY on the silver tables.

Per product (all vendors together, quantity in the product's own UoM):
  base rate      = base value / base qty            (posted vendor bills minus debit notes, INR, excl. GST)
  current rate   = current value / current qty
  price impact   = (current rate - base rate) x current qty
  closing stock  = done move lines into internal/transit locations - out of them, up to the period end
                   (same rule as the Costing tab)
  P&L hit        = (current rate - base rate) x max(current qty - closing stock, 0)
                   With FIFO the stock left at the period end is the latest purchases, so the quantity used
                   from current-period purchases is current qty - closing stock: this is the FIFO figure.
  still in stock = price impact - P&L hit
  import split   = supplier price effect (change in the foreign-currency rate x current qty x current exchange
                   rate) and forex effect (the rest), when both periods are billed in the same foreign currency.
Classification: a manual tag from config/price_impact_overrides.csv first, else by rule.
"""
import configparser
import csv
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .config import CONFIG_DIR, CONFIG_FILE
from .db import q
from .silver import T, tr, has, line_sql

router = APIRouter()

# ---------------------------------------------------------------- settings ([price_impact] in database.ini)
_ini = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
_ini.read(CONFIG_FILE, encoding="utf-8")
_pi = _ini["price_impact"] if _ini.has_section("price_impact") else {}
SEGMENT_FIELD = (_pi.get("segment_field", "") or "").strip()          # product_template column, e.g. x_business_segment
LINE_FIELD = (_pi.get("product_line_field", "") or "").strip()        # product_template column, e.g. x_product_line
NIL_BAND = float(_pi.get("nil_band_pct", "0") or 0)                   # |rate change| within this % = Nil effect (0 = exactly equal, as finance)
OVERRIDES_FILE = CONFIG_DIR / "price_impact_overrides.csv"           # item_code,tag

CLASSES = ["Price increase", "Price decrease", "Nil effect", "New in current period",
           "Not bought in current period", "Negative quantity"]


def _overrides():
    """item_code -> tag (finance keeps this list; read on every call so edits show after Refresh)."""
    out = {}
    if OVERRIDES_FILE.exists():
        with open(OVERRIDES_FILE, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                code, tag = (r.get("item_code") or "").strip(), (r.get("tag") or "").strip()
                if code and tag:
                    out[code.upper()] = tag
    return out


def _pt_col(field):
    return f"pt.{field}" if field and has("product_template", field) else None


def _uom_qty():
    """Bill quantity converted to the product's UoM (Odoo 18/19: factor = reference units per unit;
    older Odoo: factor = units per reference unit). Same UoM or no factors -> quantity as billed."""
    if not (has("uom_uom", "factor") and has("product_template", "uom_id") and has("account_move_line", "product_uom_id")):
        return "r.quantity", "", ""
    new = has("uom_uom", "relative_factor")
    conv = ("r.quantity * lu.factor / NULLIF(pu.factor, 0)" if new else "r.quantity / NULLIF(lu.factor, 0) * pu.factor")
    joins = f"""LEFT JOIN {T('uom_uom')} lu ON lu.id = aml.product_uom_id
               LEFT JOIN {T('uom_uom')} pu ON pu.id = pt.uom_id"""
    expr = f"""CASE WHEN lu.id IS NULL OR pu.id IS NULL OR lu.id = pu.id OR COALESCE(lu.factor, 0) = 0
                         OR COALESCE(pu.factor, 0) = 0 THEN r.quantity ELSE {conv} END"""
    return expr, joins, ""


_LINES_AGG = """
SELECT product_id, MAX(item_code) AS item_code, MAX(product) AS product, MAX(product_category) AS category,
       MAX(segment) AS segment, MAX(product_line) AS product_line, MAX(product_uom) AS uom,
       SUM(qty) FILTER (WHERE per = 'b') AS b_qty, SUM(value) FILTER (WHERE per = 'b') AS b_value,
       SUM(qty) FILTER (WHERE per = 'c') AS c_qty, SUM(value) FILTER (WHERE per = 'c') AS c_value,
       SUM(value) FILTER (WHERE source = 'Import') AS import_value, SUM(value) AS total_value,
       COUNT(DISTINCT currency) FILTER (WHERE fc_value IS NOT NULL) AS fc_currencies,
       MAX(currency) FILTER (WHERE fc_value IS NOT NULL) AS fc_currency,
       SUM(fc_value) FILTER (WHERE per = 'b') AS b_fc_value, SUM(qty) FILTER (WHERE per = 'b' AND fc_value IS NOT NULL) AS b_fc_qty,
       SUM(value) FILTER (WHERE per = 'b' AND fc_value IS NOT NULL) AS b_fc_inr,
       SUM(fc_value) FILTER (WHERE per = 'c') AS c_fc_value, SUM(qty) FILTER (WHERE per = 'c' AND fc_value IS NOT NULL) AS c_fc_qty,
       SUM(value) FILTER (WHERE per = 'c' AND fc_value IS NOT NULL) AS c_fc_inr
FROM x GROUP BY product_id"""


def _lines_sql():
    from . import mart
    if mart.use("purchase_lines"):
        sign = "CASE WHEN r.doc_type = 'Debit Note' THEN -1 ELSE 1 END"
        return line_sql(with_tax=False) + f""",
x AS (
    SELECT r.product_id, r.item_code, r.product, r.product_category, r.source, r.currency,
           CASE WHEN r.bill_date BETWEEN %(b_from)s::date AND %(b_to)s::date THEN 'b' ELSE 'c' END AS per,
           {sign} * COALESCE(r.qty_product_uom, r.quantity)::numeric AS qty,
           r.taxable_value::numeric AS value,
           CASE WHEN r.source = 'Import' AND r.amount_fc IS NOT NULL AND r.currency IS NOT NULL THEN r.amount_fc END AS fc_value,
           r.segment::text AS segment, r.product_line::text AS product_line, COALESCE(r.product_uom, r.uom) AS product_uom
    FROM reg r
    WHERE r.product_id IS NOT NULL
      AND (r.bill_date BETWEEN %(b_from)s::date AND %(b_to)s::date OR r.bill_date BETWEEN %(c_from)s::date AND %(c_to)s::date)
)""" + _LINES_AGG
    qty, uom_joins, _ = _uom_qty()
    seg, line = _pt_col(SEGMENT_FIELD), _pt_col(LINE_FIELD)
    puom = f"{tr('pu2.name')}" if has("product_template", "uom_id") else "MAX(r.uom)"
    puom_join = f"LEFT JOIN {T('uom_uom')} pu2 ON pu2.id = pt.uom_id" if has("product_template", "uom_id") else ""
    sign = "CASE WHEN r.doc_type = 'Debit Note' THEN -1 ELSE 1 END"
    return line_sql(with_tax=False) + f""",
x AS (
    SELECT r.product_id, r.item_code, r.product, r.product_category, r.source, r.currency,
           CASE WHEN r.bill_date BETWEEN %(b_from)s::date AND %(b_to)s::date THEN 'b' ELSE 'c' END AS per,
           {sign} * ({qty})::numeric AS qty,
           r.taxable_value::numeric AS value,
           CASE WHEN r.source = 'Import' AND r.amount_fc IS NOT NULL AND r.currency IS NOT NULL THEN r.amount_fc END AS fc_value,
           {seg or 'NULL::text'}::text AS segment, {line or 'NULL::text'}::text AS product_line,
           {puom if has('product_template', 'uom_id') else 'r.uom'} AS product_uom
    FROM reg r
    JOIN {T('account_move_line')} aml ON aml.id = r.odoo_line_id
    JOIN {T('product_product')} pp ON pp.id = r.product_id
    JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
    {uom_joins}
    {puom_join}
    WHERE r.product_id IS NOT NULL
      AND (r.bill_date BETWEEN %(b_from)s::date AND %(b_to)s::date OR r.bill_date BETWEEN %(c_from)s::date AND %(c_to)s::date)
)
""" + _LINES_AGG


OWN = "('internal', 'transit')"


def _sml_net_sql(where_date):
    """Net quantity per product moved INTO our own (internal / transit) locations, for the move lines in a date window."""
    qty = "sml.quantity_product_uom" if has("stock_move_line", "quantity_product_uom") else "sml.quantity"
    return f"""
SELECT sm.product_id,
       SUM(CASE WHEN dst.usage IN {OWN} AND src.usage NOT IN {OWN} THEN {qty}
                WHEN src.usage IN {OWN} AND dst.usage NOT IN {OWN} THEN -{qty} ELSE 0 END)::numeric AS stock
FROM {T('stock_move_line')} sml
JOIN {T('stock_move')} sm ON sm.id = sml.move_id
JOIN {T('stock_location')} src ON src.id = sml.location_id
JOIN {T('stock_location')} dst ON dst.id = sml.location_dest_id
WHERE sm.state = 'done' AND {where_date}
  AND (%(company_id)s::int[] IS NULL OR sm.company_id = ANY(%(company_id)s::int[]))
GROUP BY sm.product_id"""


def closing_stock(company_id, c_to):
    """On-hand quantity per product at the end of c_to (India time), product UoM, own locations = internal + transit
    (same rule as the Costing tab). ONE cached read per company + date, shared by every page and filter:
      - stock_quant in silver and c_to is today or later -> Odoo's current stock (fast, no history scan)
      - stock_quant in silver and c_to in the past      -> current stock minus the moves after c_to
      - no stock_quant                                  -> all done moves up to c_to"""
    from datetime import datetime, timedelta
    from . import mart
    if mart.use("stock_daily"):
        return {r["product_id"]: float(r["stock"] or 0) for r in q(f"""
            SELECT product_id, SUM(qty)::numeric AS stock FROM {mart.M('stock_daily')}
            WHERE day <= %(c_to)s::date AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))
            GROUP BY product_id""", {"company_id": company_id, "c_to": c_to})}
    end_utc = datetime(c_to.year, c_to.month, c_to.day) + timedelta(days=1) - timedelta(hours=5, minutes=30)
    p = {"company_id": company_id, "end_utc": end_utc}
    if has("stock_quant", "quantity") and has("stock_quant", "location_id"):
        co = "AND (%(company_id)s::int[] IS NULL OR sq.company_id = ANY(%(company_id)s::int[]))" \
            if has("stock_quant", "company_id") else ""
        now = {r["product_id"]: float(r["stock"] or 0) for r in q(f"""
            SELECT sq.product_id, SUM(sq.quantity)::numeric AS stock
            FROM {T('stock_quant')} sq JOIN {T('stock_location')} l ON l.id = sq.location_id
            WHERE l.usage IN {OWN} {co} GROUP BY sq.product_id""", p)}
        if c_to >= date.today():
            return now
        after = q(_sml_net_sql("sml.date >= %(end_utc)s"), p)
        for r in after:
            now[r["product_id"]] = now.get(r["product_id"], 0.0) - float(r["stock"] or 0)
        return now
    return {r["product_id"]: float(r["stock"] or 0) for r in q(_sml_net_sql("sml.date < %(end_utc)s"), p)}


def _params(company_id, b_from, b_to, c_from, c_to, txn, category=None, vendor_id=None, product_id=None):
    return {"company_id": company_id, "vendor_id": vendor_id, "product_id": product_id, "gl": None, "category": category,
            "date_from": min(b_from, c_from), "date_to": max(b_to, c_to), "source": None, "ot": None,
            "txn": txn if txn in ("External", "Inter-branch", "Intercompany") else None,
            "b_from": b_from, "b_to": b_to, "c_from": c_from, "c_to": c_to}


def _f(v):
    return float(v or 0)


def _periods(b_from, b_to, c_from, c_to):
    """Defaults: base = last complete fiscal year, current = this fiscal year to date."""
    today = date.today()
    fy = today.year if today.month >= 4 else today.year - 1
    b_from = b_from or date(fy - 1, 4, 1)
    b_to = b_to or date(fy, 3, 31)
    c_from = c_from or date(fy, 4, 1)
    c_to = c_to or today
    if b_from > b_to or c_from > c_to:
        raise HTTPException(400, "A period ends before it starts.")
    if not (b_to < c_from or c_to < b_from):
        raise HTTPException(400, "The base and current periods overlap. Pick two separate date ranges.")
    return b_from, b_to, c_from, c_to


def rows(company_id=None, b_from=None, b_to=None, c_from=None, c_to=None, txn="External", category=None,
         vendor_id=None, product_id=None):
    """One row per product with every figure the tab shows (two cached reads, worked out in Python)."""
    b_from, b_to, c_from, c_to = _periods(b_from, b_to, c_from, c_to)
    p = _params(company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id)
    data = q(_lines_sql(), p)
    stock = closing_stock(company_id, c_to)          # one shared read per company + period end
    tags = _overrides()
    out = []
    for r in data:
        bq, bv, cq, cv = _f(r["b_qty"]), _f(r["b_value"]), _f(r["c_qty"]), _f(r["c_value"])
        br = bv / bq if bq > 0 else None
        cr = cv / cq if cq > 0 else None
        st = stock.get(r["product_id"], 0.0)
        impact = hit = price_eff = fx_eff = None
        if br is not None and cr is not None:
            d = cr - br
            impact = d * cq
            used = max(cq - max(st, 0.0), 0.0)
            hit = d * used
            if (r["fc_currencies"] == 1 and _f(r["b_fc_qty"]) > 0 and _f(r["c_fc_qty"]) > 0
                    and _f(r["b_fc_value"]) and _f(r["c_fc_value"])):
                b_fc_rate = _f(r["b_fc_value"]) / _f(r["b_fc_qty"])
                c_fc_rate = _f(r["c_fc_value"]) / _f(r["c_fc_qty"])
                c_xr = _f(r["c_fc_inr"]) / _f(r["c_fc_value"])
                price_eff = (c_fc_rate - b_fc_rate) * cq * c_xr
                fx_eff = impact - price_eff
        tag = tags.get(str(r["item_code"] or "").upper())
        if bq < 0 or cq < 0:
            cls = "Negative quantity"
        elif bq > 0 and cq <= 0:
            cls = "Not bought in current period"
        elif bq <= 0 and cq > 0:
            cls = "New in current period"
        elif br and abs(100.0 * (cr - br) / br) <= NIL_BAND:
            cls = "Nil effect"
        elif cr is not None and br is not None and cr > br:
            cls = "Price increase"
        elif cr is not None and br is not None:
            cls = "Price decrease"
        else:
            cls = "Not bought in current period"
        excluded = bool(tag) and tag.lower() not in ("traded", "traded items")
        tv = _f(r["total_value"])
        source = "Import" if tv and _f(r["import_value"]) / tv >= 0.5 else "Domestic"
        if source != "Import":
            price_eff = fx_eff = None
        if cls in ("Nil effect", "Negative quantity") or excluded:
            impact_v, hit_v = (0.0, 0.0) if impact is not None else (None, None)
        else:
            impact_v, hit_v = impact, hit
        out.append({
            "product_id": r["product_id"], "item_code": r["item_code"], "product": r["product"],
            "category": r["category"], "segment": r["segment"] or (str(r["category"] or "").split(" / ")[1]
                                                                    if " / " in str(r["category"] or "") else r["category"]),
            "product_line": r["product_line"], "uom": r["uom"],
            "source": source,
            "b_qty": bq, "b_value": bv, "b_rate": br, "c_qty": cq, "c_value": cv, "c_rate": cr,
            "rate_change_pct": round(100.0 * (cr - br) / br, 2) if br and cr is not None else None,
            "closing_stock": st if r["product_id"] in stock or _f(r["c_qty"]) > 0 else None, "price_impact": impact_v, "pl_hit": hit_v,
            "in_stock": (impact_v - hit_v) if impact_v is not None and hit_v is not None else None,
            "realisation_pct": round(100.0 * hit_v / impact_v, 1) if impact_v and hit_v is not None else None,
            "stock_above_purchase": bool(cq > 0 and st >= cq and impact is not None),
            "fc_currency": r["fc_currency"] if r["fc_currencies"] == 1 else None,
            "price_effect": price_eff if not excluded and cls not in ("Nil effect", "Negative quantity") else None,
            "forex_effect": fx_eff if not excluded and cls not in ("Nil effect", "Negative quantity") else None,
            "classification": cls, "manual_tag": tag,
        })
    return out, (b_from, b_to, c_from, c_to)


def _sum(rs, k):
    return sum(_f(r.get(k)) for r in rs)


@router.get("/api/price-impact/summary")
def summary(company_id: Optional[str] = None, b_from: Optional[date] = None, b_to: Optional[date] = None,
            c_from: Optional[date] = None, c_to: Optional[date] = None, txn: Optional[str] = "External",
            category: Optional[str] = None, vendor_id: Optional[int] = None, product_id: Optional[int] = None):
    rs, (bf, bt, cf, ct) = rows(company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id)
    b, c = _sum(rs, "b_value"), _sum(rs, "c_value")
    imp, hit = _sum(rs, "price_impact"), _sum(rs, "pl_hit")
    hits = sorted((_f(r["pl_hit"]) for r in rs), reverse=True)
    imp_hit = sum(_f(r["pl_hit"]) for r in rs if r["source"] == "Import")
    by_cls = {}
    for r in rs:
        k = r["manual_tag"] or r["classification"]
        e = by_cls.setdefault(k, {"classification": k, "products": 0, "b_value": 0.0, "c_value": 0.0,
                                  "price_impact": 0.0, "pl_hit": 0.0})
        e["products"] += 1
        for f in ("b_value", "c_value", "price_impact", "pl_hit"):
            e[f] += _f(r[f])
    stock_above = [r for r in rs if r["stock_above_purchase"]]
    pe = _sum(rs, "price_effect")
    fx = _sum(rs, "forex_effect")
    return {
        "periods": {"b_from": bf, "b_to": bt, "c_from": cf, "c_to": ct},
        "products": len(rs), "b_value": b, "c_value": c, "c_vs_b_pct": round(100.0 * c / b, 1) if b else None,
        "price_impact": imp, "pl_hit": hit, "in_stock": imp - hit,
        "realisation_pct": round(100.0 * hit / imp, 1) if imp else None,
        "import_share_pct": round(100.0 * imp_hit / hit, 1) if hit else None,
        "top5_share_pct": round(100.0 * sum(hits[:5]) / hit, 1) if hit else None,
        "increase_products": sum(1 for r in rs if r["classification"] == "Price increase" and not r["manual_tag"]),
        "stock_above_products": len(stock_above), "stock_above_impact": _sum(stock_above, "price_impact"),
        "import_price_effect": pe, "import_forex_effect": fx,
        "by_classification": sorted(by_cls.values(), key=lambda e: -abs(e["price_impact"]) - e["products"] / 1e9),
        "overrides_file": OVERRIDES_FILE.exists(), "segment_field": bool(_pt_col(SEGMENT_FIELD)),
        "takeaways": _takeaways(rs, b, c, imp, hit, imp_hit, hits),
    }


def _cr(v):
    return f"₹{v / 1e7:,.2f} Cr" if abs(v) >= 1e6 else f"₹{v / 1e5:,.2f} L"


def _takeaways(rs, b, c, imp, hit, imp_hit, hits):
    out = []
    if b:
        out.append(f"Current-period purchases are {_cr(c)}, {100.0 * c / b:.1f}% of the base period ({_cr(b)}).")
    if imp:
        out.append(f"Price changes added {_cr(imp)} on current purchases; {_cr(hit)} of it ({100.0 * hit / imp:.1f}%) "
                   f"has reached the P&L and {_cr(imp - hit)} is still in closing stock.")
    seg = {}
    for r in rs:
        seg[r["segment"] or "Untagged"] = seg.get(r["segment"] or "Untagged", 0.0) + _f(r["pl_hit"])
    if seg and hit:
        top = max(seg.items(), key=lambda kv: kv[1])
        out.append(f"{top[0]} carries the largest P&L hit: {_cr(top[1])} ({100.0 * top[1] / hit:.0f}% of the total).")
    if hit:
        out.append(f"Imports are {100.0 * imp_hit / hit:.0f}% of the P&L hit; the top 5 products are "
                   f"{100.0 * sum(hits[:5]) / hit:.0f}% - negotiate those first.")
    dec = [r for r in rs if r["classification"] == "Price decrease" and not r["manual_tag"]]
    if dec:
        out.append(f"{len(dec)} products got cheaper, saving {_cr(-_sum(dec, 'price_impact'))} on current purchases.")
    return out


@router.get("/api/price-impact/split")
def split(by: str = "segment", company_id: Optional[str] = None, b_from: Optional[date] = None,
          b_to: Optional[date] = None, c_from: Optional[date] = None, c_to: Optional[date] = None,
          txn: Optional[str] = "External", category: Optional[str] = None, vendor_id: Optional[int] = None, product_id: Optional[int] = None):
    key = {"segment": "segment", "category": "category", "line": "product_line", "source": "source"}.get(by)
    if not key:
        raise HTTPException(400, "by must be segment, category, line or source")
    rs, _ = rows(company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id)
    g = {}
    for r in rs:
        k = r[key] or "Untagged"
        e = g.setdefault(k, {"name": k, "products": 0, "b_value": 0.0, "c_value": 0.0, "price_impact": 0.0,
                             "pl_hit": 0.0, "price_effect": 0.0, "forex_effect": 0.0})
        e["products"] += 1
        for f in ("b_value", "c_value", "price_impact", "pl_hit", "price_effect", "forex_effect"):
            e[f] += _f(r[f])
    for e in g.values():
        e["in_stock"] = e["price_impact"] - e["pl_hit"]
        e["realisation_pct"] = round(100.0 * e["pl_hit"] / e["price_impact"], 1) if e["price_impact"] else None
    return sorted(g.values(), key=lambda e: -e["pl_hit"])


@router.get("/api/price-impact/top")
def top(rank: str = "pl_hit", source: Optional[str] = None, n: int = 30, direction: str = "increase",
        company_id: Optional[str] = None, b_from: Optional[date] = None, b_to: Optional[date] = None,
        c_from: Optional[date] = None, c_to: Optional[date] = None, txn: Optional[str] = "External",
        category: Optional[str] = None, vendor_id: Optional[int] = None, product_id: Optional[int] = None):
    rs, _ = rows(company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id)
    k = rank if rank in ("pl_hit", "price_impact") else "pl_hit"
    rs = [r for r in rs if r[k] is not None and (_f(r[k]) > 0 if direction == "increase" else _f(r[k]) < 0)
          and (not source or r["source"] == source)]
    rs.sort(key=lambda r: -_f(r[k]) if direction == "increase" else _f(r[k]))
    return rs[:max(1, min(n, 500))]


@router.get("/api/price-impact/products")
def products(company_id: Optional[str] = None, b_from: Optional[date] = None, b_to: Optional[date] = None,
             c_from: Optional[date] = None, c_to: Optional[date] = None, txn: Optional[str] = "External",
             category: Optional[str] = None, vendor_id: Optional[int] = None, product_id: Optional[int] = None, classification: Optional[str] = None, search: str = "",
             limit: int = 2000):
    rs, _ = rows(company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id)
    s = search.strip().lower()
    rs = [r for r in rs if (not classification or (r["manual_tag"] or r["classification"]) == classification)
          and (not s or s in str(r["item_code"] or "").lower() or s in str(r["product"] or "").lower())]
    rs.sort(key=lambda r: -abs(_f(r["price_impact"])))
    return rs[:max(1, min(limit, 20000))]


EXPORT_COLS = [("item_code", "Product ref."), ("product", "Product name"), ("category", "Category"),
               ("segment", "Segment"), ("product_line", "Product line"), ("uom", "UoM"), ("source", "Domestic/Import"),
               ("b_qty", "Base qty"), ("b_value", "Base value INR"), ("b_rate", "Base avg rate INR"),
               ("c_qty", "Current qty"), ("c_value", "Current value INR"), ("c_rate", "Current avg rate INR"),
               ("rate_change_pct", "Rate change %"), ("classification", "Classification"), ("manual_tag", "Manual tag"),
               ("price_impact", "Price impact INR"), ("closing_stock", "Closing stock qty"), ("pl_hit", "P&L hit INR"),
               ("in_stock", "Impact still in stock INR"), ("realisation_pct", "Realisation %"),
               ("fc_currency", "Currency"), ("price_effect", "Supplier price effect INR"),
               ("forex_effect", "Forex effect INR")]


@router.get("/api/price-impact/export")
def export(company_id: Optional[str] = None, b_from: Optional[date] = None, b_to: Optional[date] = None,
           c_from: Optional[date] = None, c_to: Optional[date] = None, txn: Optional[str] = "External",
           category: Optional[str] = None, vendor_id: Optional[int] = None, product_id: Optional[int] = None):
    """Excel in the finance workbook's layout: Summary, full register, Top 30 (all), Top 30 Import."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    rs, (bf, bt, cf, ct) = rows(company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id)
    sm = summary(company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id)
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F3B73")
    inr = '#,##0;[Red]-#,##0'
    title = f"Base {bf} to {bt}  vs  current {cf} to {ct}  ·  {txn or 'All'} purchases  ·  from Odoo (silver, T-1)"

    def table(ws, cols, data, start=3):
        ws.append([])
        ws.append([c[1] for c in cols])
        for i in range(len(cols)):
            cell = ws.cell(start, i + 1)
            cell.font, cell.fill = head, fill
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        for r in data:
            ws.append([r.get(c[0]) for c in cols])
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[ws.cell(start, i).column_letter].width = 14 if i > 2 else (16 if i == 1 else 40)
            if any(w in c[1] for w in ("INR", "value", "impact", "hit", "effect", "rate")):
                for row in ws.iter_rows(min_row=start + 1, min_col=i, max_col=i):
                    row[0].number_format = inr
        ws.freeze_panes = ws.cell(start + 1, 3)

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["PURCHASE & PRICE IMPACT SUMMARY"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([title])
    ws.append([])
    k = [("Base purchase (INR Cr)", sm["b_value"] / 1e7), ("Current purchase (INR Cr)", sm["c_value"] / 1e7),
         ("Current / base", (sm["c_vs_b_pct"] or 0) / 100), ("Price impact (INR Cr)", sm["price_impact"] / 1e7),
         ("P&L hit (INR Cr)", sm["pl_hit"] / 1e7), ("Still in closing stock (INR Cr)", sm["in_stock"] / 1e7),
         ("P&L realisation", (sm["realisation_pct"] or 0) / 100), ("Import share of P&L hit", (sm["import_share_pct"] or 0) / 100),
         ("Top 5 concentration", (sm["top5_share_pct"] or 0) / 100), ("Product records", sm["products"])]
    for name, v in k:
        ws.append([name, v])
        ws.cell(ws.max_row, 2).number_format = "0.0%" if name in ("Current / base", "P&L realisation",
                                                                 "Import share of P&L hit", "Top 5 concentration") else "#,##0.00"
    ws.append([])
    ws.append(["MANAGEMENT TAKEAWAYS"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for t in sm["takeaways"]:
        ws.append(["• " + t])
    ws.append([])
    ws.append(["BY CLASSIFICATION", "Products", "Base value INR", "Current value INR", "Price impact INR", "P&L hit INR"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for e in sm["by_classification"]:
        ws.append([e["classification"], e["products"], e["b_value"], e["c_value"], e["price_impact"], e["pl_hit"]])
        for i in range(3, 7):
            ws.cell(ws.max_row, i).number_format = inr
    ws.append([])
    ws.append(["BY SEGMENT", "Products", "Base value INR", "Current value INR", "Price impact INR", "P&L hit INR"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for e in split("segment", company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id):
        ws.append([e["name"], e["products"], e["b_value"], e["c_value"], e["price_impact"], e["pl_hit"]])
        for i in range(3, 7):
            ws.cell(ws.max_row, i).number_format = inr
    ws.column_dimensions["A"].width = 42
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18

    reg = wb.create_sheet("Register")
    reg.append([title])
    table(reg, EXPORT_COLS, sorted(rs, key=lambda r: -abs(_f(r["price_impact"]))))

    t30 = wb.create_sheet("Top 30 Price Inc.")
    t30.append(["Top 30 products by price impact (domestic and import)"])
    table(t30, EXPORT_COLS, top("price_impact", None, 30, "increase", company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id))

    t30i = wb.create_sheet("Top 30 Import")
    t30i.append(["Top 30 import products: supplier price effect vs forex effect"])
    table(t30i, EXPORT_COLS, top("price_impact", "Import", 30, "increase", company_id, b_from, b_to, c_from, c_to, txn, category, vendor_id, product_id))

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename=Purchase_Price_Analysis_{bf}_{ct}.xlsx"})
