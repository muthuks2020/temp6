"""Price & vendor watch - READ-ONLY. Every figure is computed from posted vendor bills, POs and receipts.

Price comparisons are made only within the SAME product AND the SAME unit of measure (bill UoM), at the
net INR price per unit after discount (excl. GST). Default: external purchases only.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter

from .db import q
from .returns import qty_expr, SANE_DATE
from .silver import T, has, line_sql, _p, BILLED

router = APIRouter()


def _ip(company_id, date_from, date_to, category, txn):
    return _p(company_id=company_id, category=category, date_from=date_from, date_to=date_to,
              txn=txn if txn is not None else "External")


VP = f"""
, vp AS (      -- one row per product + UoM + vendor in the period
    SELECT product_id, MAX(item_code) AS item_code, MAX(product) AS product, MAX(product_category) AS category, uom,
           vendor_id, MAX(vendor) AS vendor, SUM(quantity) AS qty, SUM(taxable_value) AS spend,
           SUM(taxable_value) / NULLIF(SUM(quantity), 0) AS avg_price,
           MIN(net_unit_price) AS min_price, MAX(net_unit_price) AS max_price,
           COUNT(DISTINCT bill_number) AS bills, MAX(bill_date) AS last_bill
    FROM reg WHERE {BILLED} AND product_id IS NOT NULL
    GROUP BY product_id, uom, vendor_id
),
spread AS (    -- products bought from 2+ vendors: gap between cheapest and dearest vendor average
    SELECT product_id, uom, COUNT(*) AS vendors, SUM(qty) AS qty, SUM(spend) AS spend,
           MIN(avg_price) AS best_price, MAX(avg_price) AS worst_price,
           ROUND((100.0 * (MAX(avg_price) - MIN(avg_price)) / NULLIF(MIN(avg_price), 0))::numeric, 1) AS spread_pct
    FROM vp GROUP BY product_id, uom HAVING COUNT(*) >= 2
)
"""


def _vp(company_id, date_from, date_to, category, txn):
    """ONE pass: per item + UoM + vendor totals for the period (cached). Gap / swing / single-source / summary
    are all worked out from these rows."""
    return q(line_sql(with_tax=False) + VP + "SELECT * FROM vp", _ip(company_id, date_from, date_to, category, txn))


def _f(v):
    return float(v or 0)


def _groups(rows):
    g = {}
    for r in rows:
        g.setdefault((r["product_id"], r["uom"]), []).append(r)
    return g


def _gaps(rows, min_pct):
    out = []
    for (pid, uom), vs in _groups(rows).items():
        vs = [v for v in vs if v["avg_price"] is not None]
        if len(vs) < 2:
            continue
        best = min(_f(v["avg_price"]) for v in vs)
        worst = max(_f(v["avg_price"]) for v in vs)
        spread = round(100.0 * (worst - best) / best, 1) if best else None
        if spread is None or spread < min_pct:
            continue
        vs = sorted(vs, key=lambda v: _f(v["avg_price"]))
        out.append({
            "item_code": vs[0]["item_code"], "product": vs[0]["product"], "category": vs[0]["category"], "uom": uom,
            "vendors": len(vs), "qty": sum(_f(v["qty"]) for v in vs), "spend": sum(_f(v["spend"]) for v in vs),
            "best_price": round(best, 4), "worst_price": round(worst, 4), "spread_pct": spread,
            "extra_paid": round(sum((_f(v["avg_price"]) - best) * _f(v["qty"]) for v in vs), 2),
            "cheapest_vendor": vs[0]["vendor"], "dearest_vendor": vs[-1]["vendor"],
            "vendor_prices": "  |  ".join(f"{v['vendor']}: ₹{_f(v['avg_price']):.2f} × {_f(v['qty']):.2f}" for v in vs),
        })
    return sorted(out, key=lambda x: -x["extra_paid"])


@router.get("/api/insights/summary")
def insights_summary(company_id: Optional[str] = None, date_from: Optional[date] = None,
                     date_to: Optional[date] = None, category: Optional[str] = None, txn: Optional[str] = None,
                     min_pct: float = 10):
    rows = _vp(company_id, date_from, date_to, category, txn)
    g = _groups(rows)
    single = [vs[0] for vs in g.values() if len(vs) == 1]
    return {"multi_vendor_products": sum(1 for vs in g.values() if len(vs) >= 2),
            "gap_products": len(_gaps(rows, min_pct)),
            "extra_paid": round(sum(x["extra_paid"] for x in _gaps(rows, min_pct)), 2),
            "single_source_products": len(single), "single_source_spend": round(sum(_f(v["spend"]) for v in single), 2),
            "spend": round(sum(_f(r["spend"]) for r in rows), 2)}


@router.get("/api/insights/price-gap")
def price_gap(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
              category: Optional[str] = None, txn: Optional[str] = None, min_pct: float = 10):
    """Same product (same UoM) bought from different vendors at different prices.
    extra_paid = what was paid above the cheapest vendor's average price on the same item in the period."""
    return _gaps(_vp(company_id, date_from, date_to, category, txn), min_pct)[:1000]


@router.get("/api/insights/price-swing")
def price_swing(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                category: Optional[str] = None, txn: Optional[str] = None, min_pct: float = 15):
    """Same vendor, same product & UoM, very different prices within the period."""
    out = []
    for r in _vp(company_id, date_from, date_to, category, txn):
        lo, hi = _f(r["min_price"]), _f(r["max_price"])
        if (r["bills"] or 0) >= 2 and lo > 0 and 100.0 * (hi - lo) / lo >= min_pct:
            out.append({"item_code": r["item_code"], "product": r["product"], "category": r["category"], "uom": r["uom"],
                        "vendor": r["vendor"], "bills": r["bills"], "qty": r["qty"], "min_price": round(lo, 4),
                        "max_price": round(hi, 4), "avg_price": round(_f(r["avg_price"]), 4),
                        "swing_pct": round(100.0 * (hi - lo) / lo, 1),
                        "spend_at_risk": round((hi - _f(r["avg_price"])) * _f(r["qty"]), 2), "last_bill": r["last_bill"]})
    return sorted(out, key=lambda x: -x["swing_pct"])[:1000]


@router.get("/api/insights/single-source")
def single_source(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                  category: Optional[str] = None, txn: Optional[str] = None):
    """Items bought from only ONE vendor in the period - dependency risk, no price comparison possible."""
    rows = [vs[0] for vs in _groups(_vp(company_id, date_from, date_to, category, txn)).values() if len(vs) == 1]
    return [{k: r[k] for k in ("item_code", "product", "category", "uom", "vendor", "bills", "qty", "spend",
                                "avg_price", "last_bill")} for r in sorted(rows, key=lambda r: -_f(r["spend"]))][:1000]


@router.get("/api/insights/split-bills")
def split_bills(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                category: Optional[str] = None, txn: Optional[str] = None, min_bills: int = 3):
    """Same vendor + same product billed in 3+ separate bills in the same week (possible splitting)."""
    p = _ip(company_id, date_from, date_to, category, txn)
    p["n"] = min_bills
    return q(line_sql(with_tax=False) + f"""
        SELECT DATE_TRUNC('week', bill_date)::date AS week_of, vendor, MAX(item_code) AS item_code,
               MAX(product) AS product, COUNT(DISTINCT bill_number) AS bills,
               STRING_AGG(DISTINCT bill_number, ', ') AS bill_numbers,
               STRING_AGG(DISTINCT po_number, ', ') AS po_numbers,
               SUM(quantity) AS qty, SUM(taxable_value) AS value, MAX(taxable_value) AS largest_line
        FROM reg WHERE {BILLED} AND product_id IS NOT NULL
        GROUP BY 1, vendor_id, vendor, product_id
        HAVING COUNT(DISTINCT bill_number) >= %(n)s
        ORDER BY value DESC LIMIT 1000""", p)


@router.get("/api/insights/new-vendors")
def new_vendors(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                category: Optional[str] = None, txn: Optional[str] = None):
    """Vendors whose FIRST ever posted bill is in the period, with how much they were paid."""
    p = _ip(company_id, date_from, date_to, category, txn)
    return q(line_sql(with_tax=False) + f"""
        , firsts AS (SELECT partner_id AS vendor_id, MIN(invoice_date) AS first_bill
                     FROM {T('account_move')} WHERE move_type = 'in_invoice' AND state = 'posted' GROUP BY 1)
        SELECT r.vendor, r.vendor_gstin, f.first_bill, COUNT(DISTINCT r.bill_number) AS bills,
               COUNT(DISTINCT r.product_id) AS products, SUM(r.taxable_value) AS value,
               COUNT(DISTINCT r.bill_number) FILTER (WHERE r.po_number IS NULL) AS bills_without_po
        FROM reg r JOIN firsts f ON f.vendor_id = r.vendor_id
        WHERE {BILLED.replace('doc_type', 'r.doc_type').replace('quantity', 'r.quantity')}
          AND (%(date_from)s::date IS NULL OR f.first_bill >= %(date_from)s::date)
          AND (%(date_to)s::date   IS NULL OR f.first_bill <= %(date_to)s::date)
        GROUP BY r.vendor_id, r.vendor, r.vendor_gstin, f.first_bill
        ORDER BY value DESC LIMIT 500""", p)


# ---------------------------------------------------------------- vendor lead time & supply rhythm
LT_CTE = """lt AS (        -- one row per receipt per PO: days from PO approval to goods received
    SELECT vendor_id, MAX(vendor) AS vendor, po_id, MAX(po_number) AS po_number, picking_id,
           MAX(po_date) AS po_date, MIN(promised) AS promised, MAX(received) AS received,
           MAX(received) - MAX(po_date) AS lead_days,
           CASE WHEN MIN(promised) IS NULL THEN NULL ELSE MAX(received) - MIN(promised) END AS days_late
    FROM rc GROUP BY vendor_id, po_id, picking_id
    HAVING MAX(received) >= MAX(po_date)
)
"""


def _receipts_sql():
    from . import mart
    if mart.use("receipt_lines"):
        return f"""
WITH rc AS (SELECT * FROM {mart.M('receipt_lines')}
    WHERE (%(date_from)s::date IS NULL OR received >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR received <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))),
""" + LT_CTE.format()
    planned = "pol.date_planned" if has("purchase_order_line", "date_planned") else "NULL::timestamp"
    return f"""
WITH rc AS (   -- every done receipt line from a vendor location against a PO line
    SELECT po.partner_id AS vendor_id, p.name AS vendor, po.company_id, po.id AS po_id, po.name AS po_number,
           COALESCE(po.date_approve, po.date_order)::date AS po_date, {planned}::date AS promised,
           sp.id AS picking_id, sp.date_done::date AS received, sm.product_id, {qty_expr()} AS qty
    FROM {T('stock_move')} sm
    JOIN {T('stock_picking')} sp ON sp.id = sm.picking_id AND sp.state = 'done'
    JOIN {T('stock_location')} src ON src.id = sp.location_id AND src.usage = 'supplier'
    JOIN {T('stock_location')} dst ON dst.id = sp.location_dest_id AND dst.usage = 'internal'
    JOIN {T('purchase_order_line')} pol ON pol.id = sm.purchase_line_id
    JOIN {T('purchase_order')} po ON po.id = pol.order_id
    JOIN {T('res_partner')} p ON p.id = po.partner_id
    WHERE sm.state = 'done' AND {SANE_DATE}
      AND (%(date_from)s::date IS NULL OR sp.date_done >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR sp.date_done <  %(date_to)s::date + 1)
      AND (%(company_id)s::int[] IS NULL OR po.company_id = ANY(%(company_id)s::int[]))
),
""" + LT_CTE.format()


def _lp(company_id, date_from, date_to):
    return {"company_id": company_id, "date_from": date_from, "date_to": date_to}


@router.get("/api/insights/lead-time")
def lead_time(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """Per vendor: days from PO approval to receipt (avg / median / slowest 10%%), and on-time vs promised date."""
    return q(_receipts_sql() + """
        SELECT vendor, COUNT(DISTINCT po_id) AS pos, COUNT(*) AS receipts,
               ROUND((AVG(lead_days))::numeric, 1) AS avg_days,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY lead_days) AS median_days,
               PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY lead_days) AS p90_days,
               MIN(lead_days) AS fastest, MAX(lead_days) AS slowest,
               COUNT(*) FILTER (WHERE days_late IS NOT NULL) AS with_promise,
               ROUND((100.0 * COUNT(*) FILTER (WHERE days_late <= 0) / NULLIF(COUNT(*) FILTER (WHERE days_late IS NOT NULL), 0))::numeric, 1)
                   AS on_time_pct,
               ROUND((AVG(days_late) FILTER (WHERE days_late > 0))::numeric, 1) AS avg_days_late,
               MAX(received) AS last_receipt
        FROM lt GROUP BY vendor_id, vendor ORDER BY receipts DESC LIMIT 1000""", _lp(company_id, date_from, date_to))


@router.get("/api/insights/supply-rhythm")
def supply_rhythm(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """Per vendor + item: how often goods arrive (average gap between receipt dates) and when the next is due."""
    from .silver import tr
    return q(_receipts_sql() + f"""
        , days AS (SELECT DISTINCT vendor_id, vendor, product_id, received FROM rc),
        gaps AS (SELECT d.*, received - LAG(received) OVER (PARTITION BY vendor_id, product_id ORDER BY received) AS gap
                 FROM days d),
        agg AS (SELECT vendor_id, MAX(vendor) AS vendor, product_id, COUNT(*) AS receipts,
                       ROUND((AVG(gap))::numeric, 1) AS avg_gap_days, MIN(gap) AS min_gap, MAX(gap) AS max_gap,
                       MAX(received) AS last_receipt
                FROM gaps GROUP BY vendor_id, product_id HAVING COUNT(*) >= 3),
        ltp AS (SELECT rc.vendor_id, rc.product_id, ROUND((AVG(rc.received - rc.po_date))::numeric, 1) AS avg_lead_days
                FROM rc WHERE rc.received >= rc.po_date GROUP BY 1, 2)
        SELECT a.vendor, pp.default_code AS item_code, {tr('pt.name')} AS product, a.receipts, a.avg_gap_days,
               a.min_gap, a.max_gap, l.avg_lead_days, a.last_receipt,
               (a.last_receipt + ROUND(a.avg_gap_days)::int) AS next_expected,
               CURRENT_DATE - (a.last_receipt + ROUND(a.avg_gap_days)::int) AS overdue_days
        FROM agg a
        LEFT JOIN ltp l ON l.vendor_id = a.vendor_id AND l.product_id = a.product_id
        LEFT JOIN {T('product_product')} pp ON pp.id = a.product_id
        LEFT JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
        ORDER BY a.receipts DESC LIMIT 1000""", _lp(company_id, date_from, date_to))


# ---------------------------------------------------------------- item x vendor: price vs lead time
def _vkey(alias):
    """Vendor key that is the same for a company and its contacts (bill and PO may use different contacts)."""
    if has("res_partner", "commercial_partner_id"):
        return f"COALESCE({alias}.commercial_partner_id, {alias}.id)"
    if has("res_partner", "parent_id"):
        return f"COALESCE({alias}.parent_id, {alias}.id)"
    return f"{alias}.id"


def _vendor_keys(ids):
    """partner id -> vendor key (cached query over the partners in the result)."""
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    rows = q(f"SELECT p.id, {_vkey('p')} AS k FROM {T('res_partner')} p WHERE p.id = ANY(%(ids)s::int[])", {"ids": ids})
    return {r["id"]: r["k"] for r in rows}


def _item_lead(company_id, date_from, date_to):
    """Per vendor key + product: lead time of each receipt (PO approval -> goods received), exact PO-line links."""
    promised = "ROUND((AVG(promised - po_date) FILTER (WHERE promised IS NOT NULL))::numeric, 1)"
    return q(_receipts_sql() + f"""
        , il AS (SELECT {_vkey('vp_')} AS vkey, rc.product_id, rc.po_id, rc.picking_id,
                        MAX(rc.received) - MAX(rc.po_date) AS lead_days, MAX(rc.po_date) AS po_date,
                        MIN(rc.promised) AS promised,
                        CASE WHEN MIN(rc.promised) IS NULL THEN NULL ELSE MAX(rc.received) - MIN(rc.promised) END AS days_late,
                        MAX(rc.received) AS received
                 FROM rc JOIN {T('res_partner')} vp_ ON vp_.id = rc.vendor_id
                 GROUP BY 1, rc.product_id, rc.po_id, rc.picking_id HAVING MAX(rc.received) >= MAX(rc.po_date))
        SELECT vkey, product_id, COUNT(*) AS receipts,
               ROUND((AVG(lead_days))::numeric, 1) AS avg_lead,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY lead_days) AS median_lead,
               MIN(lead_days) AS min_lead, MAX(lead_days) AS max_lead,
               {promised} AS promised_lead,
               ROUND((100.0 * COUNT(*) FILTER (WHERE days_late <= 0)
                      / NULLIF(COUNT(*) FILTER (WHERE days_late IS NOT NULL), 0))::numeric, 1) AS on_time_pct,
               MAX(received) AS last_receipt
        FROM il GROUP BY vkey, product_id""", _lp(company_id, date_from, date_to))


def _first_last(company_id, date_from, date_to, category, txn):
    """Per product + UoM + vendor: first and last billed net price in the period."""
    return q(line_sql(with_tax=False) + f"""
        , b AS (SELECT product_id, uom, vendor_id, bill_date, odoo_line_id, net_unit_price FROM reg
                WHERE {BILLED} AND product_id IS NOT NULL AND net_unit_price IS NOT NULL),
        f AS (SELECT DISTINCT ON (product_id, uom, vendor_id) product_id, uom, vendor_id, net_unit_price AS first_price
              FROM b ORDER BY product_id, uom, vendor_id, bill_date, odoo_line_id),
        l AS (SELECT DISTINCT ON (product_id, uom, vendor_id) product_id, uom, vendor_id, net_unit_price AS last_price,
                     bill_date AS last_price_date
              FROM b ORDER BY product_id, uom, vendor_id, bill_date DESC, odoo_line_id DESC)
        SELECT l.*, f.first_price FROM l JOIN f USING (product_id, uom, vendor_id)""",
             _ip(company_id, date_from, date_to, category, txn))


def _open_po(company_id):
    """Per vendor key + product: quantity still to be received on confirmed POs (Odoo's own qty_received)."""
    disp = "AND pol.display_type IS NULL" if has("purchase_order_line", "display_type") else ""
    return q(f"""
        SELECT {_vkey('p')} AS vkey, pol.product_id,
               SUM(GREATEST(pol.product_qty - COALESCE(pol.qty_received, 0), 0)) AS open_qty,
               COUNT(DISTINCT po.id) FILTER (WHERE pol.product_qty - COALESCE(pol.qty_received, 0) > 0) AS open_pos
        FROM {T('purchase_order_line')} pol
        JOIN {T('purchase_order')} po ON po.id = pol.order_id
        JOIN {T('res_partner')} p ON p.id = po.partner_id
        WHERE po.state IN ('purchase', 'done') AND pol.product_id IS NOT NULL {disp}
          AND (%(company_id)s::int[] IS NULL OR po.company_id = ANY(%(company_id)s::int[]))
        GROUP BY 1, 2""", {"company_id": company_id})


@router.get("/api/insights/item-vendor")
def item_vendor(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                category: Optional[str] = None, txn: Optional[str] = None, multi: bool = False,
                product_id: Optional[int] = None, vendor_id: Optional[int] = None):
    """Each purchased item x vendor: billed price (net INR / unit, same UoM, quantity-weighted average) next to
    lead time (PO approval -> receipt), with the gap to the cheapest and to the fastest vendor of the same item.
    Price = posted bills dated in the period; lead time = receipts done in the period (exact PO-line links only).
    Bill vendor and PO vendor are matched on the commercial partner (company), so contacts do not break the link."""
    prices = _vp(company_id, date_from, date_to, category, txn)
    vk = _vendor_keys([r["vendor_id"] for r in prices])
    lead = {(r["vkey"], r["product_id"]): r for r in _item_lead(company_id, date_from, date_to)}
    fl = {(r["product_id"], r["uom"], r["vendor_id"]): r for r in _first_last(company_id, date_from, date_to, category, txn)}
    opn = {(r["vkey"], r["product_id"]): r for r in _open_po(company_id)}
    out = []
    vend_items = {r["product_id"] for r in prices if vendor_id and r["vendor_id"] == vendor_id}
    for (pid, uom), vs in _groups(prices).items():
        if multi and len(vs) < 2:
            continue
        if product_id and pid != product_id:
            continue
        if vendor_id and pid not in vend_items:
            continue
        key = lambda v: (vk.get(v["vendor_id"], v["vendor_id"]), pid)
        priced = [_f(v["avg_price"]) for v in vs if v["avg_price"] is not None and _f(v["avg_price"]) > 0]
        best = min(priced) if priced else None
        leads = [_f(lead[key(v)]["avg_lead"]) for v in vs if key(v) in lead and lead[key(v)]["avg_lead"] is not None]
        fastest = min(leads) if leads else None
        total_qty = sum(_f(v["qty"]) for v in vs)
        for v in vs:
            lt = lead.get(key(v)) or {}
            f = fl.get((pid, uom, v["vendor_id"])) or {}
            o = opn.get(key(v)) or {}
            ap = _f(v["avg_price"]) if v["avg_price"] is not None else None
            al = _f(lt["avg_lead"]) if lt.get("avg_lead") is not None else None
            cheap = (best is not None and ap is not None and len(priced) > 1 and max(priced) - best > 1e-9
                     and abs(ap - best) < 1e-9)
            fast = (fastest is not None and al is not None and len(leads) > 1 and max(leads) - fastest > 1e-9
                    and abs(al - fastest) < 1e-9)
            fp, lp = f.get("first_price"), f.get("last_price")
            out.append({
                "product_id": pid, "vendor_id": v["vendor_id"],
                "item_code": v["item_code"], "product": v["product"], "category": v["category"], "uom": uom,
                "vendor": v["vendor"], "vendors": len(vs), "bills": v["bills"], "qty": v["qty"], "spend": v["spend"],
                "qty_share_pct": round(100.0 * _f(v["qty"]) / total_qty, 1) if total_qty else None,
                "avg_price": round(ap, 4) if ap is not None else None,
                "min_price": v["min_price"], "max_price": v["max_price"],
                "last_price": lp, "last_price_date": f.get("last_price_date"),
                "price_trend_pct": round(100.0 * (_f(lp) - _f(fp)) / _f(fp), 1) if fp and lp is not None and _f(fp) else None,
                "best_price": round(best, 4) if best is not None else None,
                "vs_best_pct": round(100.0 * (ap - best) / best, 1) if best and ap is not None else None,
                "extra_paid": round((ap - best) * _f(v["qty"]), 2) if best and ap is not None else None,
                "receipts": lt.get("receipts"), "avg_lead": al, "median_lead": lt.get("median_lead"),
                "min_lead": lt.get("min_lead"), "max_lead": lt.get("max_lead"),
                "promised_lead": lt.get("promised_lead"), "on_time_pct": lt.get("on_time_pct"),
                "last_receipt": lt.get("last_receipt"),
                "fastest_lead": fastest,
                "vs_fastest_days": round(al - fastest, 1) if fastest is not None and al is not None else None,
                "open_po_qty": o.get("open_qty"), "open_pos": o.get("open_pos"),
                "tag": ("Cheapest & fastest" if cheap and fast else "Cheapest" if cheap else "Fastest" if fast else ""),
            })
    out.sort(key=lambda r: (str(r["item_code"] or r["product"] or ""), str(r["uom"] or ""), r["avg_price"] or 0))
    return out[:5000]


# ---------------------------------------------------------------- approval limits (po_approval_line_one)
def _limits_ok():
    return has("po_approval_line_one", "po_level_validation_amount") and has("po_approval_line_one", "company_id")


@router.get("/api/insights/approval-limits")
def approval_limits():
    """The approval matrix as set in Odoo: company, limit, L1 approvers."""
    if not _limits_ok():
        return []
    from .silver import tr
    users = ""
    if has("po_approval_line_one_res_users_rel") and has("res_users", "partner_id"):
        users = f"""LEFT JOIN {T('po_approval_line_one_res_users_rel')} r ON r.po_approval_line_one_id = l.id
                    LEFT JOIN {T('res_users')} u ON u.id = r.res_users_id
                    LEFT JOIN {T('res_partner')} up ON up.id = u.partner_id"""
    approvers = "STRING_AGG(DISTINCT up.name, ', ')" if users else "NULL::text"
    return q(f"""SELECT {tr('c.name')} AS company, l.po_level_validation_amount AS limit_amount,
                        {approvers} AS l1_approvers
                 FROM {T('po_approval_line_one')} l JOIN {T('res_company')} c ON c.id = l.company_id {users}
                 GROUP BY c.name, l.id, l.po_level_validation_amount ORDER BY 1, 2""")
