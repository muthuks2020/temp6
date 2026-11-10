"""Bills / GST register - one row per posted vendor bill or debit note.
Reads the replicated Odoo tables directly: <silver_schema>.<table_prefix><odoo table>
(both set in config/database.ini)."""
import io
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from .config import settings
from .db import q
from .silver import has, group_cte, txn_class_sql

router = APIRouter()

_ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
assert _ident.match(settings.silver_schema), "silver_schema must be a plain name"
assert settings.table_prefix == "" or _ident.match(settings.table_prefix), "table_prefix must be a plain name"


def T(odoo_table: str) -> str:
    """account_move -> silver.odoo_account_move"""
    return f"{settings.silver_schema}.{settings.table_prefix}{odoo_table}"


def tr(col: str) -> str:
    """English text of an Odoo name column whether it is stored as JSON or plain text."""
    return (f"(CASE WHEN left(ltrim({col}::text), 1) = '{{' "
            f"THEN ({col}::text)::jsonb->>'en_US' ELSE {col}::text END)")


def bill_sql():
    from . import mart
    if mart.use("vendor_bills"):
        return f"""
WITH reg AS (SELECT * FROM {mart.M('vendor_bills')}
    WHERE (%(date_from)s::date IS NULL OR bill_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR bill_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[]))
      AND (%(vendor_id)s::int  IS NULL OR vendor_id = %(vendor_id)s::int)
      AND (%(doc_type)s::text  IS NULL OR move_type = %(doc_type)s::text))
"""
    """bill-level register query (built at call time so optional tables/columns are detected)"""
    if has("res_company", "partner_id"):
        cp_join, cvat = f"LEFT JOIN {T('res_partner')} cpar ON cpar.id = c.partner_id", "cpar.vat"
    else:
        cp_join, cvat = "", "NULL::text"
    has_ot = has("purchase_order", "order_type_id") and has("purchase_order_type")
    ot_join = f"LEFT JOIN {T('purchase_order_type')} ot ON ot.id = po.order_type_id" if has_ot else ""
    ot_name = tr("ot.name") if has_ot else "NULL::text"
    txn = txn_class_sql("p.vat", cvat, ot_name)
    return f"""
WITH {group_cte()},
bills AS (
    SELECT am.id AS move_id, am.company_id, am.name AS bill_number, am.ref AS vendor_bill_ref,
           am.invoice_date AS bill_date, am.date AS accounting_date, am.invoice_date_due AS due_date,
           am.move_type, am.partner_id, am.journal_id, am.currency_id,
           am.l10n_in_gst_treatment AS gst_treatment, am.l10n_in_state_id AS place_of_supply_id,
           am.payment_state, am.write_date
    FROM {T('account_move')} am
    WHERE am.move_type IN ('in_invoice', 'in_refund')
      AND am.state = 'posted'
      AND (%(date_from)s::date IS NULL OR am.invoice_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR am.invoice_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR am.company_id = ANY(%(company_id)s::int[]))
      AND (%(vendor_id)s::int  IS NULL OR am.partner_id = %(vendor_id)s::int)
      AND (%(doc_type)s::text  IS NULL OR am.move_type  = %(doc_type)s::text)
),
base AS (
    SELECT aml.move_id,
           SUM(aml.balance)         AS taxable_value_inr,
           SUM(aml.amount_currency) AS taxable_value_fc
    FROM {T('account_move_line')} aml
    JOIN bills b ON b.move_id = aml.move_id
    WHERE aml.display_type = 'product'
    GROUP BY aml.move_id
),
tax AS (
    SELECT aml.move_id,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE 'CGST%%' THEN aml.balance ELSE 0 END) AS cgst,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE 'SGST%%' THEN aml.balance ELSE 0 END) AS sgst,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE 'IGST%%' THEN aml.balance ELSE 0 END) AS igst,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE 'CESS%%' THEN aml.balance ELSE 0 END) AS cess,
           SUM(CASE WHEN UPPER({tr('tg.name')}) LIKE '%%TDS%%' THEN aml.balance ELSE 0 END) AS tds,
           SUM(aml.balance) AS total_tax
    FROM {T('account_move_line')} aml
    JOIN bills b                       ON b.move_id = aml.move_id
    JOIN {T('account_tax')} t          ON t.id = aml.tax_line_id
    JOIN {T('account_tax_group')} tg   ON tg.id = t.tax_group_id
    WHERE aml.display_type = 'tax'
    GROUP BY aml.move_id
),
po AS (
    SELECT aml.move_id,
           STRING_AGG(DISTINCT po.name, ', ') AS po_numbers,
           MIN(po.date_order)::date           AS po_date,
           MIN(po.order_type_id)              AS order_type_id
    FROM {T('account_move_line')} aml
    JOIN bills b                          ON b.move_id = aml.move_id
    JOIN {T('purchase_order_line')} pol   ON pol.id = aml.purchase_line_id
    JOIN {T('purchase_order')} po         ON po.id = pol.order_id
    GROUP BY aml.move_id
),
reg AS (
    SELECT
        {tr('c.name')}                       AS company,
        {tr('j.name')}                       AS journal,
        b.bill_number,
        b.vendor_bill_ref,
        b.bill_date,
        b.accounting_date,
        b.due_date,
        CASE b.move_type WHEN 'in_invoice' THEN 'Bill' ELSE 'Debit Note' END AS doc_type,
        p.name                               AS vendor,
        b.partner_id                         AS vendor_id,
        p.vat                                AS vendor_gstin,
        {cvat}                               AS company_gstin,
        {ot_name}                            AS order_type,
        {txn}                                AS txn_class,
        {tr('vs.name')}                      AS vendor_state,
        {tr('pos.name')}                     AS place_of_supply,
        b.gst_treatment,
        po.po_numbers,
        po.po_date,
        cur.name                             AS currency,
        COALESCE(base.taxable_value_fc, 0)   AS taxable_value_fc,
        COALESCE(base.taxable_value_inr, 0)  AS taxable_value,
        COALESCE(tax.cgst, 0)                AS cgst,
        COALESCE(tax.sgst, 0)                AS sgst,
        COALESCE(tax.igst, 0)                AS igst,
        COALESCE(tax.cess, 0)                AS cess,
        COALESCE(tax.tds, 0)                 AS tds,
        COALESCE(base.taxable_value_inr, 0) + COALESCE(tax.total_tax, 0) AS invoice_total,
        b.payment_state,
        b.move_id                            AS odoo_move_id,
        b.write_date                         AS last_modified
    FROM bills b
    JOIN {T('res_company')} c                 ON c.id = b.company_id
    JOIN {T('account_journal')} j             ON j.id = b.journal_id
    JOIN {T('res_partner')} p                 ON p.id = b.partner_id
    LEFT JOIN {T('res_country_state')} vs     ON vs.id = p.state_id
    LEFT JOIN {T('res_country_state')} pos    ON pos.id = b.place_of_supply_id
    LEFT JOIN {T('res_currency')} cur         ON cur.id = b.currency_id
    LEFT JOIN base ON base.move_id = b.move_id
    LEFT JOIN tax  ON tax.move_id  = b.move_id
    LEFT JOIN po   ON po.move_id   = b.move_id
    {cp_join}
    {ot_join.replace("po.order_type_id", "po.order_type_id")}
)
"""

SEARCH = """(%(s)s::text IS NULL OR bill_number ILIKE %(s)s OR vendor_bill_ref ILIKE %(s)s
             OR vendor ILIKE %(s)s OR vendor_gstin ILIKE %(s)s OR po_numbers ILIKE %(s)s)
             AND (%(txn)s::text IS NULL OR txn_class = %(txn)s::text)
             AND (%(pay)s::text[] IS NULL OR payment_state = ANY(%(pay)s::text[]))"""

# payment filter -> Odoo account_move.payment_state values
PAY = {"paid": ["paid", "in_payment"], "not_paid": ["not_paid"], "partial": ["partial"],
       "open": ["not_paid", "partial"], "reversed": ["reversed"]}

COLS = ["company", "journal", "bill_number", "vendor_bill_ref", "bill_date", "accounting_date", "due_date",
        "doc_type", "vendor", "vendor_gstin", "vendor_state", "place_of_supply", "gst_treatment",
        "po_numbers", "po_date", "order_type", "txn_class", "company_gstin", "currency", "taxable_value_fc", "taxable_value", "cgst", "sgst", "igst",
        "cess", "tds", "invoice_total", "payment_state", "odoo_move_id"]


def _params(company_id, vendor_id, date_from, date_to, doc_type, search, txn=None, pay=None):
    return {"pay": PAY.get(pay or ""), "txn": txn if txn in ("External", "Inter-branch", "Intercompany") else None, "company_id": company_id, "vendor_id": vendor_id, "date_from": date_from, "date_to": date_to,
            "doc_type": {"bill": "in_invoice", "debit_note": "in_refund"}.get(doc_type or ""),
            "s": f"%{search}%" if search else None}


@router.get("/api/bills")
def bills(company_id: Optional[str] = None, vendor_id: Optional[int] = None,
          date_from: Optional[date] = None, date_to: Optional[date] = None,
          doc_type: Optional[str] = None, search: str = "",
          page: int = 1, page_size: int = Query(50, le=500),
          sort: str = "bill_date", order: str = "desc", txn: Optional[str] = None, pay: Optional[str] = None):
    sort = sort if sort in COLS else "bill_date"
    order = "ASC" if order.lower() == "asc" else "DESC"
    p = _params(company_id, vendor_id, date_from, date_to, doc_type, search, txn=txn, pay=pay)
    p.update(limit=page_size, offset=(page - 1) * page_size)
    rows = q(bill_sql() + f"""SELECT * FROM reg WHERE {SEARCH}
                            ORDER BY {sort} {order} NULLS LAST, odoo_move_id DESC
                            LIMIT %(limit)s OFFSET %(offset)s""", p)
    return {"rows": rows}


@router.get("/api/bills/summary")
def bills_summary(company_id: Optional[str] = None, vendor_id: Optional[int] = None,
                  date_from: Optional[date] = None, date_to: Optional[date] = None,
                  doc_type: Optional[str] = None, search: str = "", txn: Optional[str] = None, pay: Optional[str] = None):
    p = _params(company_id, vendor_id, date_from, date_to, doc_type, search, txn=txn, pay=pay)
    return q(bill_sql() + f"""SELECT COUNT(*) AS bills, COUNT(DISTINCT vendor) AS vendors,
                                   COALESCE(SUM(taxable_value),0) AS taxable, COALESCE(SUM(cgst),0) AS cgst,
                                   COALESCE(SUM(sgst),0) AS sgst, COALESCE(SUM(igst),0) AS igst,
                                   COALESCE(SUM(cess),0) AS cess, COALESCE(SUM(tds),0) AS tds,
                                   COALESCE(SUM(invoice_total),0) AS total
                            FROM reg WHERE {SEARCH}""", p)[0]


BILL_TILES = {
    "bills":   "COUNT(*) AS bills, COUNT(DISTINCT vendor) AS vendors",
    "taxable": "COALESCE(SUM(taxable_value),0) AS taxable",
    "gst_cs":  "COALESCE(SUM(cgst),0) AS cgst, COALESCE(SUM(sgst),0) AS sgst",
    "igst":    "COALESCE(SUM(igst),0) AS igst, COALESCE(SUM(cess),0) AS cess",
    "tds":     "COALESCE(SUM(tds),0) AS tds",
    "total":   "COALESCE(SUM(invoice_total),0) AS total",
}


@router.get("/api/bills/tile/{tile}")
def bills_tile(tile: str, company_id: Optional[str] = None, vendor_id: Optional[int] = None,
               date_from: Optional[date] = None, date_to: Optional[date] = None,
               doc_type: Optional[str] = None, search: str = "", txn: Optional[str] = None, pay: Optional[str] = None):
    """KPI tiles: ONE query computes all of them (cached); each tile reads its value from it."""
    if tile not in BILL_TILES:
        from fastapi import HTTPException
        raise HTTPException(404, f"unknown tile {tile}")
    p = _params(company_id, vendor_id, date_from, date_to, doc_type, search, txn=txn, pay=pay)
    import re
    r = q(bill_sql() + f"SELECT {', '.join(BILL_TILES.values())} FROM reg WHERE {SEARCH}", p)[0]
    return {a: r[a] for a in re.findall(r"AS (\w+)", BILL_TILES[tile])}


@router.get("/api/bills/export")
def bills_export(company_id: Optional[str] = None, vendor_id: Optional[int] = None,
                 date_from: Optional[date] = None, date_to: Optional[date] = None,
                 doc_type: Optional[str] = None, search: str = "", txn: Optional[str] = None, pay: Optional[str] = None):
    from openpyxl import Workbook
    p = _params(company_id, vendor_id, date_from, date_to, doc_type, search, txn=txn, pay=pay)
    rows = q(bill_sql() + f"SELECT * FROM reg WHERE {SEARCH} ORDER BY bill_date, bill_number LIMIT 500000", p)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Bills GST Register")
    ws.append([c.replace("_", " ").title() for c in COLS])
    for r in rows:
        ws.append([r.get(c) for c in COLS])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=bills_gst_register.xlsx"})


@router.get("/api/bills/vendors")
def bill_vendors(search: str = ""):
    return q(f"""SELECT DISTINCT p.id, p.name, p.vat AS gstin
                 FROM {T('account_move')} am JOIN {T('res_partner')} p ON p.id = am.partner_id
                 WHERE am.move_type IN ('in_invoice','in_refund') AND am.state = 'posted'
                   AND (p.name ILIKE %(s)s OR p.vat ILIKE %(s)s)
                 ORDER BY p.name LIMIT 30""", {"s": f"%{search}%"})


@router.get("/api/bills/companies")
def bill_companies():
    return q(f"SELECT id, {tr('name')} AS name FROM {T('res_company')} ORDER BY 2")
