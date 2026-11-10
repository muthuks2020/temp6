"""Purchase register, product price analysis and costing - all read LIVE from the
replicated Odoo tables (<silver_schema>.<table_prefix><odoo table>). Nothing is written anywhere."""
import io
import re
from collections import defaultdict, deque
from datetime import date
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from .config import settings
from .db import q

router = APIRouter()

_ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
assert _ident.match(settings.silver_schema), "silver_schema must be a plain name"
assert settings.table_prefix == "" or _ident.match(settings.table_prefix), "table_prefix must be a plain name"


def T(t: str) -> str:
    return f"{settings.silver_schema}.{settings.table_prefix}{t}"


def tr(col: str) -> str:
    """English text of an Odoo name column (JSON {"en_US":..} or plain text)."""
    return (f"(CASE WHEN left(ltrim({col}::text), 1) = '{{' "
            f"THEN ({col}::text)::jsonb->>'en_US' ELSE {col}::text END)")


# ---------------------------------------------------------------- which optional columns exist
_cols = None


def cols():
    """{odoo_table: set(columns)} for the silver tables, read once."""
    global _cols
    if _cols is None:
        rows = q("""SELECT table_name, column_name FROM information_schema.columns
                    WHERE table_schema = %(s)s AND table_name LIKE %(p)s""",
                 {"s": settings.silver_schema, "p": settings.table_prefix.replace("_", r"\_") + "%"})
        d = defaultdict(set)
        n = len(settings.table_prefix)
        for r in rows:
            d[r["table_name"][n:]].add(r["column_name"])
        _cols = d
    return _cols


def has(table, col=None):
    c = cols()
    return table in c and (col is None or col in c[table])


# ---------------------------------------------------------------- lookups
@router.get("/api/meta")
def meta():
    return {
        "companies": q(f"SELECT id, {tr('name')} AS name FROM {T('res_company')} ORDER BY 2"),
        "categories": q(f"""SELECT DISTINCT complete_name AS name FROM {T('product_category')}
                            WHERE complete_name IS NOT NULL ORDER BY 1"""),
        "allow_run": False,
    }


@router.get("/api/gl-accounts")
def gl_accounts():
    """GL accounts used on vendor-bill product lines (for the GL filter)."""
    if has("account_account", "code_store"):
        code = "(SELECT value FROM jsonb_each_text(acc.code_store::jsonb) LIMIT 1)"
    elif has("account_account", "code"):
        code = "acc.code"
    else:
        code = "NULL::text"
    return q(f"""
        SELECT acc.id, {code} AS code, {tr('acc.name')} AS name, u.lines
        FROM (SELECT aml.account_id, COUNT(*) AS lines
              FROM {T('account_move_line')} aml
              JOIN {T('account_move')} am ON am.id = aml.move_id
              WHERE am.move_type IN ('in_invoice','in_refund') AND am.state = 'posted'
                AND aml.display_type = 'product' AND aml.account_id IS NOT NULL
              GROUP BY aml.account_id) u
        JOIN {T('account_account')} acc ON acc.id = u.account_id
        ORDER BY 2 NULLS LAST, 3""")


@router.get("/api/order-types")
def order_types():
    """Purchase order types (+ id 0 = bills without a PO)."""
    if not has("purchase_order_type"):
        return []
    sub = "is_subcontracting" if has("purchase_order_type", "is_subcontracting") else "false"
    rows = q(f"SELECT id, {tr('name')} AS name, {sub} AS is_subcontracting FROM {T('purchase_order_type')} ORDER BY 2")
    return rows + [{"id": 0, "name": "(bill without PO / no type)", "is_subcontracting": False}]


@router.get("/api/vendors")
def vendors(search: str = ""):
    return q(f"""SELECT DISTINCT p.id, p.name, p.vat AS gstin
                 FROM {T('account_move')} am JOIN {T('res_partner')} p ON p.id = am.partner_id
                 WHERE am.move_type IN ('in_invoice','in_refund') AND am.state = 'posted'
                   AND (p.name ILIKE %(s)s OR p.vat ILIKE %(s)s)
                 ORDER BY p.name LIMIT 30""", {"s": f"%{search}%"})


@router.get("/api/products")
def products(search: str = ""):
    return q(f"""SELECT pp.id, pp.default_code AS item_code, {tr('pt.name')} AS name
                 FROM {T('product_product')} pp JOIN {T('product_template')} pt ON pt.id = pp.product_tmpl_id
                 WHERE {tr('pt.name')} ILIKE %(s)s OR pp.default_code ILIKE %(s)s
                 ORDER BY 3 LIMIT 30""", {"s": f"%{search}%"})


# ---------------------------------------------------------------- line-level purchase register
def _tax_ok():
    return has("account_move_line_account_tax_rel") and has("account_tax", "amount")


def tax_ctes(src: str, idcol: str) -> str:
    """CTEs line_tax + tax_split for the lines in CTE <src> (id column <idcol>). Starts with a comma."""
    fil_ok = has("account_tax_filiation_rel") and has("account_tax", "amount_type")
    expand = (f"""LEFT JOIN {T('account_tax_filiation_rel')} f ON f.parent_tax = t.id AND t.amount_type = 'group'
                  LEFT JOIN {T('account_tax')} ch ON ch.id = f.child_tax""" if fil_ok else "")
    return f""",
line_tax AS (
    SELECT l.{idcol} AS line_id, {'COALESCE(ch.id, t.id)' if fil_ok else 't.id'} AS tax_id
    FROM {src} l
    JOIN {T('account_move_line_account_tax_rel')} r ON r.account_move_line_id = l.{idcol}
    JOIN {T('account_tax')} t ON t.id = r.account_tax_id
    {expand}
),
tax_split AS (
    SELECT lt.line_id,
           STRING_AGG({tr('t.name')}, ', ') AS tax_names,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE 'CGST%%' THEN t.amount END) AS cgst_rate,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE 'SGST%%' THEN t.amount END) AS sgst_rate,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE 'IGST%%' THEN t.amount END) AS igst_rate
    FROM line_tax lt
    JOIN {T('account_tax')} t        ON t.id = lt.tax_id
    LEFT JOIN {T('account_tax_group')} tg ON tg.id = t.tax_group_id
    GROUP BY lt.line_id
)"""


TAX_SELECT = """ts.tax_names,
        ts.cgst_rate, ROUND(({v} * COALESCE(ts.cgst_rate,0) / 100)::numeric, 2) AS cgst,
        ts.sgst_rate, ROUND(({v} * COALESCE(ts.sgst_rate,0) / 100)::numeric, 2) AS sgst,
        ts.igst_rate, ROUND(({v} * COALESCE(ts.igst_rate,0) / 100)::numeric, 2) AS igst"""
TAX_NULLS = """NULL::text AS tax_names, NULL::numeric AS cgst_rate, NULL::numeric AS cgst,
        NULL::numeric AS sgst_rate, NULL::numeric AS sgst, NULL::numeric AS igst_rate, NULL::numeric AS igst"""


def _pan(expr):
    """PAN = characters 3-12 of a GSTIN"""
    return f"upper(substr(regexp_replace(COALESCE({expr}, ''), '[[:space:]]', '', 'g'), 3, 10))"


def group_cte():
    """grp(pan): PANs of all own companies (from the companies' GSTINs)."""
    if has("res_company", "partner_id"):
        return (f"grp AS (SELECT DISTINCT {_pan('cp.vat')} AS pan FROM {T('res_company')} rc "
                f"JOIN {T('res_partner')} cp ON cp.id = rc.partner_id WHERE length(cp.vat) >= 12)")
    return "grp AS (SELECT NULL::text AS pan WHERE false)"


def txn_class_sql(vendor_vat, company_vat, ot_name):
    """External / Inter-branch / Intercompany"""
    return f"""(CASE WHEN {ot_name} ILIKE '%%branch%%' THEN 'Inter-branch'
                 WHEN {ot_name} ILIKE '%%intercompany%%' OR {ot_name} ILIKE '%%inter company%%' THEN 'Intercompany'
                 WHEN length({vendor_vat}) >= 12 AND length({company_vat}) >= 12
                      AND {_pan(vendor_vat)} = {_pan(company_vat)} THEN 'Inter-branch'
                 WHEN length({vendor_vat}) >= 12 AND {_pan(vendor_vat)} IN (SELECT pan FROM grp) THEN 'Intercompany'
                 ELSE 'External' END)"""


def source_sql(bill_ccy, company_ccy, vendor_country, company_partner_country, gst_treatment):
    """'Import' when the bill is in a foreign currency, the vendor is in another country,
    or the Indian GST treatment says overseas; otherwise 'Local'."""
    return f"""(CASE WHEN {bill_ccy} IS NOT NULL AND {bill_ccy} IS DISTINCT FROM {company_ccy} THEN 'Import'
                 WHEN {gst_treatment} = 'overseas' THEN 'Import'
                 WHEN {vendor_country} IS NOT NULL AND {company_partner_country} IS NOT NULL
                      AND {vendor_country} <> {company_partner_country} THEN 'Import'
                 ELSE 'Local' END)"""


MART_LINE_WHERE = """(%(date_from)s::date IS NULL OR bill_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR bill_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))
      AND (%(vendor_id)s::int  IS NULL OR vendor_id = %(vendor_id)s::int)
      AND (%(product_id)s::int IS NULL OR product_id = %(product_id)s::int)
      AND (%(gl)s::int[] IS NULL OR account_id = ANY(%(gl)s::int[]))
      AND (%(category)s::text IS NULL OR product_category = %(category)s::text
           OR product_category LIKE %(category)s::text || ' / %%')
      AND (%(source)s::text IS NULL OR source = %(source)s::text)
      AND (%(ot)s::int[] IS NULL OR order_type_id = ANY(%(ot)s::int[]))
      AND (%(txn)s::text IS NULL OR txn_class = %(txn)s::text)"""


def _mart_line_sql(with_tax):
    from .mart import M
    head = f"WITH grp AS (SELECT NULL::text AS pan WHERE false),\n"
    if not with_tax:
        return head + f"reg AS (SELECT * FROM {M('purchase_lines')} WHERE {MART_LINE_WHERE})\n"
    if _tax_ok():
        return (head + f"lines AS (SELECT * FROM {M('purchase_lines')} WHERE {MART_LINE_WHERE})"
                + tax_ctes("lines", "odoo_line_id")
                + f",\nreg AS (SELECT l.*, {TAX_SELECT.format(v='l.taxable_value')} FROM lines l "
                  f"LEFT JOIN tax_split ts ON ts.line_id = l.odoo_line_id)\n")
    return head + f"reg AS (SELECT l.*, {TAX_NULLS} FROM {M('purchase_lines')} l WHERE {MART_LINE_WHERE})\n"


def line_sql(with_tax: bool = True):
    """The line-level purchase register query (one row per vendor-bill product line).
    with_tax=False skips the GST split (much faster) - used for totals and price analysis.
    Reads procurement_mart.purchase_lines when it exists (same rows, saved daily), else silver."""
    from . import mart
    if mart.use("purchase_lines"):
        return _mart_line_sql(with_tax)
    if has("account_account", "code_store"):
        gl_code = "(acc.code_store::jsonb)->>(l.company_id::text)"
    elif has("account_account", "code"):
        gl_code = "acc.code"
    else:
        gl_code = "NULL::text"
    if has("product_template", "l10n_in_hsn_code"):
        hsn = "pt.l10n_in_hsn_code"
    elif has("account_move_line", "l10n_in_hsn_code"):
        hsn = "l.l10n_in_hsn_code"
    else:
        hsn = "NULL::text"
    uom_col = "product_uom_id" if has("account_move_line", "product_uom_id") else "NULL::int"
    if has("res_company", "partner_id"):
        cp_join = f"LEFT JOIN {T('res_partner')} cpar ON cpar.id = c.partner_id"
        cp_country = "cpar.country_id"
    else:
        cp_join, cp_country = "", "NULL::int"
    src = source_sql("l.bill_currency_id", "c.currency_id", "p.country_id", cp_country, "l.gst_treatment")
    if has("purchase_order", "order_type_id") and has("purchase_order_type"):
        ot_join = f"LEFT JOIN {T('purchase_order_type')} ot ON ot.id = po.order_type_id"
        ot_name = f"COALESCE({tr('ot.name')}, CASE WHEN po.id IS NULL THEN '(bill without PO)' ELSE '(no type)' END)"
        ot_id = "COALESCE(po.order_type_id, 0)"
    else:
        ot_join, ot_name, ot_id = "", "NULL::text", "0"
    ot_raw = tr("ot.name") if ot_join else "NULL::text"
    cvat = "cpar.vat" if cp_join else "NULL::text"
    txn = txn_class_sql("p.vat", cvat, ot_raw)

    if with_tax and _tax_ok():
        tax_cte = tax_ctes("lines", "id")
        tax_join = "LEFT JOIN tax_split ts ON ts.line_id = l.id"
        tax_cols = TAX_SELECT.format(v="l.balance") + ","
    elif with_tax:
        tax_cte, tax_join, tax_cols = "", "", TAX_NULLS + ","
    else:
        tax_cte, tax_join, tax_cols = "", "", ""

    return f"""
WITH {group_cte()},
lines AS (
    SELECT aml.id, aml.move_id, aml.name, aml.product_id, aml.quantity, aml.{uom_col} AS product_uom_id,
           aml.price_unit, aml.discount, aml.balance, aml.account_id, aml.purchase_line_id, aml.sequence,
           aml.write_date,
           am.name AS bill_number, am.ref AS vendor_bill_ref, am.invoice_date, am.company_id,
           am.partner_id, am.move_type, am.write_date AS am_write_date,
           am.currency_id AS bill_currency_id,
           {'aml.amount_currency' if has('account_move_line', 'amount_currency') else 'NULL::numeric'} AS amount_fc,
           {'am.l10n_in_gst_treatment' if has('account_move', 'l10n_in_gst_treatment') else 'NULL::text'} AS gst_treatment
    FROM {T('account_move_line')} aml
    JOIN {T('account_move')} am ON am.id = aml.move_id
    WHERE am.move_type IN ('in_invoice', 'in_refund')
      AND am.state = 'posted'
      AND aml.display_type = 'product'
      AND (%(date_from)s::date IS NULL OR am.invoice_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR am.invoice_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR am.company_id = ANY(%(company_id)s::int[]))
      AND (%(vendor_id)s::int  IS NULL OR am.partner_id = %(vendor_id)s::int)
      AND (%(product_id)s::int IS NULL OR aml.product_id = %(product_id)s::int)
      AND (%(gl)s::int[] IS NULL OR aml.account_id = ANY(%(gl)s::int[]))
      AND (%(category)s::text IS NULL OR aml.product_id IN (
            SELECT cpp.id FROM {T('product_product')} cpp
            JOIN {T('product_template')} cpt ON cpt.id = cpp.product_tmpl_id
            JOIN {T('product_category')} cc  ON cc.id = cpt.categ_id
            WHERE cc.complete_name = %(category)s::text OR cc.complete_name LIKE %(category)s::text || ' / %%'))
){tax_cte},
reg AS (
    SELECT
        l.bill_number,
        l.vendor_bill_ref,
        l.invoice_date                  AS bill_date,
        DATE_TRUNC('month', l.invoice_date)::date AS bill_month,
        CASE l.move_type WHEN 'in_invoice' THEN 'Bill' ELSE 'Debit Note' END AS doc_type,
        l.company_id,
        {tr('c.name')}                  AS company,
        l.partner_id                    AS vendor_id,
        p.name                          AS vendor,
        p.vat                           AS vendor_gstin,
        po.name                         AS po_number,
        l.product_id,
        pp.default_code                 AS item_code,
        {tr('pt.name')}                 AS product,
        categ.complete_name             AS product_category,
        l.name                          AS line_description,
        {hsn}                           AS hsn_code,
        l.quantity,
        {tr('u.name')}                  AS uom,
        l.price_unit,
        l.discount,
        {gl_code}                       AS gl_account_code,
        {tr('acc.name')}                AS gl_account,
        l.balance                       AS taxable_value,
        ROUND((l.balance / NULLIF(l.quantity, 0))::numeric, 4) AS net_unit_price,
        {tax_cols}
        bc.name                         AS currency,
        l.amount_fc,
        {src}                           AS source,
        {ot_name}                       AS order_type,
        {txn}                           AS txn_class,
        l.id                            AS odoo_line_id,
        l.sequence,
        GREATEST(l.write_date, l.am_write_date) AS last_modified
    FROM lines l
    JOIN {T('res_company')} c             ON c.id = l.company_id
    JOIN {T('res_partner')} p             ON p.id = l.partner_id
    LEFT JOIN {T('product_product')} pp   ON pp.id = l.product_id
    LEFT JOIN {T('product_template')} pt  ON pt.id = pp.product_tmpl_id
    LEFT JOIN {T('product_category')} categ ON categ.id = pt.categ_id
    LEFT JOIN {T('uom_uom')} u            ON u.id = l.product_uom_id
    LEFT JOIN {T('account_account')} acc  ON acc.id = l.account_id
    LEFT JOIN {T('purchase_order_line')} pol ON pol.id = l.purchase_line_id
    LEFT JOIN {T('purchase_order')} po    ON po.id = pol.order_id
    LEFT JOIN {T('res_currency')} bc      ON bc.id = l.bill_currency_id
    {ot_join}
    {cp_join}
    {tax_join}
    WHERE (%(source)s::text IS NULL OR {src} = %(source)s::text)
      AND (%(ot)s::int[] IS NULL OR {ot_id} = ANY(%(ot)s::int[]))
      AND (%(txn)s::text IS NULL OR {txn} = %(txn)s::text)
)
"""


REG_SEARCH = """(%(s)s::text IS NULL OR bill_number ILIKE %(s)s OR vendor_bill_ref ILIKE %(s)s OR vendor ILIKE %(s)s
       OR product ILIKE %(s)s OR item_code ILIKE %(s)s OR po_number ILIKE %(s)s OR hsn_code ILIKE %(s)s)"""

REG_COLS = ["bill_number", "bill_date", "doc_type", "company", "vendor", "vendor_gstin", "vendor_bill_ref",
            "po_number", "item_code", "product", "product_category", "line_description", "hsn_code", "quantity",
            "uom", "price_unit", "discount", "net_unit_price", "taxable_value", "tax_names", "cgst_rate", "cgst",
            "sgst_rate", "sgst", "igst_rate", "igst", "gl_account_code", "gl_account", "order_type", "txn_class", "source", "currency",
            "amount_fc", "odoo_line_id"]


def _gl(gl):
    """'12,15,40' -> [12, 15, 40] (None when empty)"""
    if not gl:
        return None
    ids = [int(x) for x in str(gl).split(",") if x.strip().isdigit()]
    return ids or None


def _p(company_id=None, vendor_id=None, product_id=None, category=None, date_from=None, date_to=None, search="",
       gl=None, source=None, ot=None, txn=None):
    return {"txn": txn if txn in ("External", "Inter-branch", "Intercompany") else None, "ot": _gl(ot) if ot not in (None, "") else None, "gl": _gl(gl), "source": source if source in ("Local", "Import") else None, "company_id": company_id, "vendor_id": vendor_id, "product_id": product_id,
            "category": category or None, "date_from": date_from, "date_to": date_to,
            "s": f"%{search}%" if search else None}


TAX_COLS = {"tax_names", "cgst_rate", "cgst", "sgst_rate", "sgst", "igst_rate", "igst"}


@router.get("/api/register")
def register(company_id: Optional[str] = None, vendor_id: Optional[int] = None,
             product_id: Optional[int] = None, category: Optional[str] = None,
             date_from: Optional[date] = None, date_to: Optional[date] = None,
             search: str = "", page: int = 1, page_size: int = Query(50, le=500),
             sort: str = "bill_date", order: str = "desc", gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    sort = sort if sort in REG_COLS else "bill_date"
    if sort in TAX_COLS:                      # tax is added after paging
        sort = "taxable_value"
    order = "ASC" if order.lower() == "asc" else "DESC"
    p = _p(company_id, vendor_id, product_id, category, date_from, date_to, search, gl=gl, source=source, ot=ot, txn=txn)
    p.update(limit=page_size, offset=(page - 1) * page_size)
    # 1) pick the page of lines WITHOUT tax  2) work out GST only for those rows
    sql = line_sql(with_tax=False) + f""",
page AS (SELECT * FROM reg WHERE {REG_SEARCH}
         ORDER BY {sort} {order} NULLS LAST, odoo_line_id DESC
         LIMIT %(limit)s OFFSET %(offset)s)"""
    if _tax_ok():
        sql += tax_ctes("page", "odoo_line_id") + f"""
SELECT page.*, {TAX_SELECT.format(v="page.taxable_value")}
FROM page LEFT JOIN tax_split ts ON ts.line_id = page.odoo_line_id"""
    else:
        sql += f"\nSELECT page.*, {TAX_NULLS} FROM page"
    sql += f"\nORDER BY {sort} {order} NULLS LAST, odoo_line_id DESC"
    return {"rows": q(sql, p)}


@router.get("/api/register/summary")
def register_summary(company_id: Optional[str] = None, vendor_id: Optional[int] = None,
                     product_id: Optional[int] = None, category: Optional[str] = None,
                     date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "", gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    p = _p(company_id, vendor_id, product_id, category, date_from, date_to, search, gl=gl, source=source, ot=ot, txn=txn)
    return _reg_bundle(p, date_from, date_to)["tiles"]


REG_TILES = {
    "spend":    "COALESCE(SUM(taxable_value), 0) AS value",
    "bills":    "COUNT(DISTINCT bill_number) AS value",
    "lines":    "COUNT(*) AS value",
    "vendors":  "COUNT(DISTINCT vendor_id) AS value",
    "products": "COUNT(DISTINCT product_id) AS value",
}


def _reg_bundle(p, date_from, date_to):
    """ONE pass over the filtered register: all KPI tiles + the 3 charts (cached together)."""
    span = ((date_to or date.today()) - date_from).days if date_from else 9999
    unit = "day" if span <= 62 else "month"
    rows = q(line_sql(with_tax=False) + f""",
f AS MATERIALIZED (SELECT bill_date, bill_number, vendor_id, vendor, product_id, product_category, taxable_value
                   FROM reg WHERE {REG_SEARCH})
SELECT 'tiles' AS kind, NULL::text AS name, COALESCE(SUM(taxable_value), 0) AS spend,
       COUNT(DISTINCT bill_number) AS bills, COUNT(*) AS lines, COUNT(DISTINCT vendor_id) AS vendors,
       COUNT(DISTINCT product_id) AS products
FROM f
UNION ALL
SELECT 'trend', DATE_TRUNC('{unit}', bill_date)::date::text, SUM(taxable_value), COUNT(DISTINCT bill_number), NULL, NULL, NULL
FROM f GROUP BY 2
UNION ALL
(SELECT 'vendors', vendor, SUM(taxable_value), NULL, NULL, NULL, NULL FROM f GROUP BY vendor_id, vendor
 ORDER BY 3 DESC NULLS LAST LIMIT 10)
UNION ALL
(SELECT 'categories', COALESCE(product_category, '(no category)'), SUM(taxable_value), NULL, NULL, NULL, NULL
 FROM f GROUP BY 2 ORDER BY 3 DESC NULLS LAST LIMIT 10)""", p)
    t = next((r for r in rows if r["kind"] == "tiles"), {})
    return {
        "tiles": {"spend": t.get("spend", 0), "bills": t.get("bills", 0), "lines": t.get("lines", 0),
                  "vendors": t.get("vendors", 0), "products": t.get("products", 0)},
        "trend": {"unit": unit, "rows": sorted([{"period": r["name"], "spend": r["spend"], "bills": r["bills"]}
                                                for r in rows if r["kind"] == "trend"], key=lambda x: x["period"] or "")},
        "vendors": [{"name": r["name"], "spend": r["spend"]} for r in rows if r["kind"] == "vendors"],
        "categories": [{"name": r["name"], "spend": r["spend"]} for r in rows if r["kind"] == "categories"],
    }


@router.get("/api/register/tile/{tile}")
def register_tile(tile: str, company_id: Optional[str] = None, vendor_id: Optional[int] = None,
                  product_id: Optional[int] = None, category: Optional[str] = None,
                  date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "", gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    """KPI tiles: the first tile runs the one shared query, the others are read from its cached result."""
    if tile not in REG_TILES:
        from fastapi import HTTPException
        raise HTTPException(404, f"unknown tile {tile}")
    p = _p(company_id, vendor_id, product_id, category, date_from, date_to, search, gl=gl, source=source, ot=ot, txn=txn)
    return {"value": _reg_bundle(p, date_from, date_to)["tiles"][tile]}


@router.get("/api/register/export")
def register_export(company_id: Optional[str] = None, vendor_id: Optional[int] = None,
                    product_id: Optional[int] = None, category: Optional[str] = None,
                    date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "", gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    from openpyxl import Workbook
    p = _p(company_id, vendor_id, product_id, category, date_from, date_to, search, gl=gl, source=source, ot=ot, txn=txn)
    rows = q(line_sql() + f"""SELECT * FROM reg WHERE {REG_SEARCH}
                              ORDER BY bill_date, bill_number, sequence LIMIT 500000""", p)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Purchase Register")
    ws.append([c.replace("_", " ").title() for c in REG_COLS])
    for r in rows:
        ws.append([r.get(c) for c in REG_COLS])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=purchase_register.xlsx"})


# ---------------------------------------------------------------- product price analysis (bills only)
@router.get("/api/register/chart/{kind}")
def register_chart(kind: str, company_id: Optional[str] = None, vendor_id: Optional[int] = None,
                   product_id: Optional[int] = None, category: Optional[str] = None,
                   date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "",
                   gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None,
                   txn: Optional[str] = None):
    """Charts with the same filters as the register - read from the same single cached pass as the tiles."""
    if kind not in ("trend", "vendors", "categories"):
        from fastapi import HTTPException
        raise HTTPException(404, "unknown chart")
    p = _p(company_id, vendor_id, product_id, category, date_from, date_to, search, gl=gl, source=source, ot=ot, txn=txn)
    return _reg_bundle(p, date_from, date_to)[kind]


@router.get("/api/product/{product_id}/vendors")
def product_vendors(product_id: int, company_id: Optional[str] = None,
                    date_from: Optional[date] = None, date_to: Optional[date] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    p = _p(company_id=company_id, product_id=product_id, date_from=date_from, date_to=date_to, source=source, ot=ot, txn=txn)
    return q(line_sql(with_tax=False) + """
        SELECT company, vendor, vendor_id,
               COUNT(DISTINCT bill_number) AS pos, SUM(quantity) AS qty, MAX(uom) AS base_uom,
               STRING_AGG(DISTINCT uom, ', ') AS uoms,
               MIN(net_unit_price) AS min_price, MAX(net_unit_price) AS max_price,
               ROUND((SUM(taxable_value) / NULLIF(SUM(quantity), 0))::numeric, 4) AS avg_price,
               (ARRAY_AGG(net_unit_price ORDER BY bill_date DESC, odoo_line_id DESC))[1] AS last_price,
               SUM(taxable_value) AS spend, MIN(bill_date) AS first_po, MAX(bill_date) AS last_po
        FROM reg WHERE doc_type = 'Bill' AND quantity > 0
        GROUP BY company, vendor, vendor_id ORDER BY avg_price""", p)


@router.get("/api/product/{product_id}/trend")
def product_trend(product_id: int, company_id: Optional[str] = None,
                  date_from: Optional[date] = None, date_to: Optional[date] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    p = _p(company_id=company_id, product_id=product_id, date_from=date_from, date_to=date_to, source=source, ot=ot, txn=txn)
    return q(line_sql(with_tax=False) + """
        SELECT bill_month AS month, vendor,
               ROUND((SUM(taxable_value) / NULLIF(SUM(quantity), 0))::numeric, 4) AS avg_price, SUM(quantity) AS qty
        FROM reg WHERE doc_type = 'Bill' AND quantity > 0
        GROUP BY bill_month, vendor ORDER BY bill_month""", p)


@router.get("/api/category/products")
def category_products(category: str, company_id: Optional[str] = None,
                      date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "", gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    """Every product in a category (incl. sub-categories) with each vendor and its prices (from bills)."""
    p = _p(company_id=company_id, category=category, date_from=date_from, date_to=date_to, search=search, gl=gl, source=source, ot=ot, txn=txn)
    return q(line_sql(with_tax=False) + f"""
        , pv AS (
            SELECT product_id, MAX(item_code) AS item_code, MAX(product) AS product,
                   MAX(product_category) AS product_category, company, vendor_id, MAX(vendor) AS vendor,
                   COUNT(DISTINCT bill_number) AS bills, SUM(quantity) AS qty,
                   STRING_AGG(DISTINCT uom, ', ') AS uoms,
                   MIN(net_unit_price) AS min_price, MAX(net_unit_price) AS max_price,
                   ROUND((SUM(taxable_value) / NULLIF(SUM(quantity), 0))::numeric, 4) AS avg_price,
                   (ARRAY_AGG(net_unit_price ORDER BY bill_date DESC, odoo_line_id DESC))[1] AS last_price,
                   MAX(bill_date) AS last_bill, SUM(taxable_value) AS spend
            FROM reg
            WHERE doc_type = 'Bill' AND quantity > 0 AND {REG_SEARCH}
            GROUP BY product_id, company, vendor_id)
        SELECT pv.*,
               COUNT(*) OVER (PARTITION BY product_id)                 AS vendors_for_product,
               RANK() OVER (PARTITION BY product_id ORDER BY avg_price) AS price_rank,
               ROUND((100.0 * (avg_price - MIN(avg_price) OVER (PARTITION BY product_id))
                     / NULLIF(MIN(avg_price) OVER (PARTITION BY product_id), 0))::numeric, 1) AS pct_above_best
        FROM pv
        ORDER BY product, price_rank, vendor
        LIMIT 5000""", p)


@router.get("/api/price-alerts")
def price_alerts(min_pct: float = 5, days: int = 90, company_id: Optional[str] = None):
    from datetime import timedelta
    p = _p(company_id=company_id, date_from=date.today() - timedelta(days=days + 365))
    p.update(m=min_pct, d=days)
    return q(line_sql(with_tax=False) + """
        SELECT * FROM (
          SELECT company, item_code, product, product_id, vendor, bill_number AS po_number, bill_date AS po_date,
                 net_unit_price AS current_price, prev_price, prev_po,
                 ROUND((100.0 * (net_unit_price - prev_price) / NULLIF(prev_price, 0))::numeric, 1) AS change_pct
          FROM (SELECT r.*, LAG(net_unit_price) OVER w AS prev_price, LAG(bill_number) OVER w AS prev_po
                FROM reg r WHERE doc_type = 'Bill' AND quantity > 0
                WINDOW w AS (PARTITION BY company_id, product_id, vendor_id, uom ORDER BY bill_date, odoo_line_id)) x
          WHERE prev_price IS NOT NULL AND bill_date >= CURRENT_DATE - %(d)s::int) y
        WHERE ABS(change_pct) >= %(m)s
        ORDER BY ABS(change_pct) DESC LIMIT 300""", p)


# ---------------------------------------------------------------- costing overview: all materials
FY = "EXTRACT(YEAR FROM (bill_date - INTERVAL '3 months'))::int"   # Indian FY start year (Apr-Mar)
BILLED = "doc_type = 'Bill' AND quantity > 0"


@router.get("/api/costing/categories")
def costing_categories(company_id: Optional[str] = None,
                       date_from: Optional[date] = None, date_to: Optional[date] = None, gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    """Per product category: purchased products, vendors, bills, spend - per fiscal year and in total."""
    p = _p(company_id=company_id, date_from=date_from, date_to=date_to, gl=gl, source=source, ot=ot, txn=txn)
    base = line_sql(with_tax=False)
    by_fy = q(base + f"""
        SELECT COALESCE(product_category, '(no category)') AS category, {FY} AS fy,
               COUNT(DISTINCT product_id) AS products, COUNT(DISTINCT vendor_id) AS vendors,
               COUNT(DISTINCT bill_number) AS bills, SUM(taxable_value) AS spend
        FROM reg WHERE {BILLED} GROUP BY 1, 2""", p)
    totals = q(base + f"""
        SELECT COALESCE(product_category, '(no category)') AS category,
               COUNT(DISTINCT product_id) AS products, COUNT(DISTINCT vendor_id) AS vendors,
               COUNT(DISTINCT bill_number) AS bills, SUM(taxable_value) AS spend
        FROM reg WHERE {BILLED} GROUP BY 1 ORDER BY spend DESC NULLS LAST""", p)
    return {"by_fy": by_fy, "totals": totals}


@router.get("/api/costing/category-products")
def costing_category_products(category: str, company_id: Optional[str] = None,
                              date_from: Optional[date] = None, date_to: Optional[date] = None, gl: Optional[str] = None, source: Optional[str] = None, ot: Optional[str] = None, txn: Optional[str] = None):
    """Purchase costing of every product in ONE category: period cost + cost per fiscal year."""
    p = _p(company_id=company_id, category=category, date_from=date_from, date_to=date_to, gl=gl, source=source, ot=ot, txn=txn)
    base = line_sql(with_tax=False)
    where = f"{BILLED} AND product_category = %(category)s"
    by_fy = q(base + f"""
        SELECT product_id, {FY} AS fy, SUM(quantity) AS qty, SUM(taxable_value) AS spend,
               ROUND((SUM(taxable_value) / NULLIF(SUM(quantity), 0))::numeric, 4) AS avg_cost
        FROM reg WHERE {where} GROUP BY 1, 2""", p)
    products = q(base + f"""
        SELECT product_id, MAX(item_code) AS item_code, MAX(product) AS product,
               STRING_AGG(DISTINCT uom, ', ') AS uoms,
               COUNT(DISTINCT vendor_id) AS vendors, COUNT(DISTINCT bill_number) AS bills,
               SUM(quantity) AS qty, SUM(taxable_value) AS spend,
               ROUND((SUM(taxable_value) / NULLIF(SUM(quantity), 0))::numeric, 4) AS avg_cost,
               MIN(net_unit_price) AS min_cost, MAX(net_unit_price) AS max_cost,
               (ARRAY_AGG(net_unit_price ORDER BY bill_date DESC, odoo_line_id DESC))[1] AS last_cost,
               (ARRAY_AGG(vendor ORDER BY bill_date DESC, odoo_line_id DESC))[1] AS last_vendor,
               MAX(bill_date) AS last_bill, MIN(bill_date) AS first_bill
        FROM reg WHERE {where} GROUP BY product_id ORDER BY spend DESC NULLS LAST LIMIT 5000""", p)
    # freight on exact links only (same bill / PO line / landed cost), freight bills in the same period
    from .freight import freight_for_products
    fr = freight_for_products([r["product_id"] for r in products if r["product_id"]], company_id, date_from, date_to)
    for r in products:
        f = fr.get(r["product_id"]) or {}
        fpu = f.get("freight_per_unit")
        r["freight"] = f.get("freight")
        r["freight_per_unit"] = fpu
        r["freight_pct"] = f.get("freight_pct")
        r["freight_linked_qty_pct"] = (round(100.0 * float(f["linked_qty"]) / float(r["qty"]), 1)
                                       if f.get("linked_qty") and r.get("qty") else None)
        # (material spend + linked freight) / qty bought - freight is not spread beyond what is linked
        r["cost_incl_freight"] = (round((float(r["spend"]) + float(f.get("freight") or 0)) / float(r["qty"]), 4)
                                  if r.get("qty") and r.get("spend") is not None else None)
    return {"by_fy": by_fy, "products": products}


# ---------------------------------------------------------------- costing (live, one product at a time)
def _movements_sql():
    qty = "sml.quantity_product_uom" if has("stock_move_line", "quantity_product_uom") else "sml.quantity"
    expiry = "lot.expiration_date" if has("stock_lot", "expiration_date") else "NULL::timestamp"
    return f"""
WITH bill AS (
    SELECT aml.purchase_line_id,
           SUM(CASE WHEN am.move_type = 'in_refund' THEN -aml.quantity ELSE aml.quantity END) AS billed_qty,
           SUM(aml.balance) AS billed_amount
    FROM {T('account_move_line')} aml JOIN {T('account_move')} am ON am.id = aml.move_id
    WHERE am.state = 'posted' AND am.move_type IN ('in_invoice','in_refund')
      AND aml.display_type = 'product' AND aml.product_id = %(p)s
    GROUP BY aml.purchase_line_id
)
SELECT sml.id AS move_line_id, sm.id AS move_id, sm.company_id, {tr('c.name')} AS company,
       sml.lot_id, lot.name AS lot_number, {expiry} AS lot_expiry,
       (sml.date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') AS move_datetime,
       sm.reference, src.usage AS src_usage, dst.usage AS dst_usage,
       {qty}::numeric AS qty, sm.purchase_line_id, sm.origin_returned_move_id,
       sm.price_unit::numeric AS odoo_cost, po.name AS po_number, v.name AS vendor,
       COALESCE(
         CASE WHEN b.billed_qty > 0 THEN b.billed_amount / b.billed_qty * pol.product_qty / NULLIF(pol.product_uom_qty, 0) END,
         pol.price_subtotal / NULLIF(po.currency_rate, 0) / NULLIF(pol.product_uom_qty, 0)
       )::numeric AS po_cost
FROM {T('stock_move_line')} sml
JOIN {T('stock_move')} sm            ON sm.id = sml.move_id
JOIN {T('res_company')} c            ON c.id = sm.company_id
JOIN {T('stock_location')} src       ON src.id = sml.location_id
JOIN {T('stock_location')} dst       ON dst.id = sml.location_dest_id
LEFT JOIN {T('stock_lot')} lot       ON lot.id = sml.lot_id
LEFT JOIN {T('purchase_order_line')} pol ON pol.id = sm.purchase_line_id
LEFT JOIN {T('purchase_order')} po   ON po.id = pol.order_id
LEFT JOIN {T('res_partner')} v       ON v.id = po.partner_id
LEFT JOIN bill b                     ON b.purchase_line_id = sm.purchase_line_id
WHERE sm.state = 'done' AND sm.product_id = %(p)s
  AND (%(c)s::int[] IS NULL OR sm.company_id = ANY(%(c)s::int[]))
  AND (sml.date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date <= %(d)s::date
ORDER BY sm.company_id, sml.date, sml.id
"""


INT = ("internal", "transit")


def _classify(src, dst):
    if src not in INT and dst in INT:
        d = 1
    elif src in INT and dst not in INT:
        d = -1
    else:
        return 0, None
    kind = {("supplier", "internal"): "PURCHASE_RECEIPT", ("internal", "supplier"): "VENDOR_RETURN",
            ("internal", "customer"): "SALE_DELIVERY", ("customer", "internal"): "CUSTOMER_RETURN",
            ("internal", "production"): "PRODUCTION_CONSUME", ("production", "internal"): "PRODUCTION_OUTPUT",
            ("internal", "inventory"): "ADJUSTMENT_OUT/SCRAP", ("inventory", "internal"): "ADJUSTMENT_IN"}
    return d, kind.get((src, dst), "OTHER")


def _r(x, n=2):
    return None if x is None else round(float(x), n)


@router.get("/api/costing/product/{product_id}")
def costing_product(product_id: int, company_id: Optional[str] = None, as_of: Optional[date] = None):
    as_of = as_of or date.today()
    rows = q(_movements_sql(), {"p": product_id, "c": company_id, "d": as_of})
    moves = []
    for r in rows:
        d, kind = _classify(r["src_usage"], r["dst_usage"])
        if d == 0:
            continue
        cost = None
        if d == 1:
            cost = r["po_cost"] if kind == "PURCHASE_RECEIPT" and r["po_cost"] is not None else r["odoo_cost"]
        moves.append({**r, "dir": d, "type": kind, "qty": float(r["qty"] or 0),
                      "cost": None if cost is None else float(cost)})

    by_co = defaultdict(list)
    for m in moves:
        by_co[m["company_id"]].append(m)

    card, summary, layers_out, lots = [], [], [], []
    for co, ms in by_co.items():
        # ---- weighted average (moving, like Odoo AVCO)
        qn = v = a = 0.0
        for m in ms:
            issue = None
            if m["dir"] == 1:
                v += m["qty"] * (m["cost"] if m["cost"] is not None else a)
                qn += m["qty"]
            else:
                issue = m["qty"] * a
                v -= issue
                qn -= m["qty"]
            if qn > 0:
                a = v / qn
            else:
                v = qn * a
            card.append({"move_datetime": m["move_datetime"], "company": m["company"], "reference": m["reference"],
                         "movement_type": m["type"], "lot_number": m["lot_number"], "signed_qty": m["dir"] * m["qty"],
                         "in_unit_cost": _r(m["cost"], 4) if m["dir"] == 1 else None, "issue_cost": _r(issue),
                         "running_qty": round(qn, 4), "running_value": _r(v), "avg_cost": _r(a, 4)})

        # ---- FIFO (per product and per batch)
        def fifo(key):
            queues = defaultdict(deque)          # key -> deque of [layer_dict]
            by_move = {}
            for m in ms:                         # build layers, vendor returns reduce their receipt
                if m["dir"] == 1:
                    L = {"move_id": m["move_id"], "date": m["move_datetime"], "lot_number": m["lot_number"],
                         "vendor": m["vendor"], "po_number": m["po_number"], "cost": m["cost"] or 0.0,
                         "qty": m["qty"], "lot_id": m["lot_id"]}
                    queues[key(m)].append(L)
                    by_move.setdefault((m["move_id"], key(m)), []).append(L)
            issues = []
            for m in ms:
                if m["dir"] == 1:
                    continue
                need = m["qty"]
                if m["type"] == "VENDOR_RETURN" and m["origin_returned_move_id"]:
                    for L in by_move.get((m["origin_returned_move_id"], key(m)), []):
                        take = min(need, L["qty"]); L["qty"] -= take; need -= take
                    continue
                cost = 0.0
                for L in queues[key(m)]:
                    if need <= 0:
                        break
                    take = min(need, L["qty"])
                    if take > 0:
                        cost += take * L["cost"]; L["qty"] -= take; need -= take
                issues.append(cost)
            remaining = [L for qd in queues.values() for L in qd if L["qty"] > 1e-9]
            return remaining, sum(issues)

        rem_p, _ = fifo(lambda m: 0)
        rem_l, _ = fifo(lambda m: m["lot_id"] or 0)
        for L in rem_l:
            layers_out.append({"company": ms[0]["company"], "lot_number": L["lot_number"], "receipt_date": L["date"],
                               "vendor": L["vendor"], "po_number": L["po_number"], "unit_cost": _r(L["cost"], 4),
                               "remaining_qty": round(L["qty"], 4), "fifo_value": _r(L["qty"] * L["cost"])})

        # ---- batch (lot) actual purchase cost
        lot = defaultdict(lambda: {"rq": 0.0, "rv": 0.0, "ret": 0.0, "bal": 0.0, "first": None,
                                   "vendors": set(), "pos": set(), "name": None, "exp": None})
        for m in ms:
            k = lot[m["lot_id"] or 0]
            k["name"], k["exp"] = m["lot_number"], m["lot_expiry"]
            k["bal"] += m["dir"] * m["qty"]
            if m["type"] == "PURCHASE_RECEIPT":
                k["rq"] += m["qty"]; k["rv"] += m["qty"] * (m["cost"] or 0)
                k["first"] = k["first"] or m["move_datetime"]
                if m["vendor"]: k["vendors"].add(m["vendor"])
                if m["po_number"]: k["pos"].add(m["po_number"])
            elif m["type"] == "VENDOR_RETURN":
                k["ret"] += m["qty"]
        batch_value = 0.0
        for lid, k in lot.items():
            unit = k["rv"] / k["rq"] if k["rq"] else None
            val = k["bal"] * unit if unit is not None else None
            if k["bal"] > 0 and val is not None:
                batch_value += val
            if lid:
                lots.append({"company": ms[0]["company"], "lot_number": k["name"], "lot_expiry": k["exp"],
                             "vendors": ", ".join(sorted(k["vendors"])), "po_numbers": ", ".join(sorted(k["pos"])),
                             "first_receipt_date": k["first"], "received_qty": round(k["rq"], 4),
                             "returned_qty": round(k["ret"], 4), "on_hand_qty": round(k["bal"], 4),
                             "batch_unit_cost": _r(unit, 4), "batch_stock_value": _r(val)})

        fifo_val = sum(L["qty"] * L["cost"] for L in rem_p)
        fifo_qty = sum(L["qty"] for L in rem_p)
        last_buy = next((m for m in reversed(ms) if m["type"] == "PURCHASE_RECEIPT"), None)
        summary.append({"company": ms[0]["company"], "on_hand_qty": round(qn, 4),
                        "wac_unit": _r(a, 4), "wac_value": _r(qn * a),
                        "fifo_unit": _r(fifo_val / fifo_qty, 4) if fifo_qty else None, "fifo_value": _r(fifo_val),
                        "batch_value": _r(batch_value),
                        "last_price": _r(last_buy["cost"], 4) if last_buy else None,
                        "last_vendor": last_buy["vendor"] if last_buy else None,
                        "fifo_minus_wac": _r(fifo_val - qn * a)})

    lots.sort(key=lambda x: (x["company"], str(x["first_receipt_date"] or "")))
    return {"as_of": as_of.isoformat(), "summary": summary, "lots": lots, "fifo_layers": layers_out, "card": card}
