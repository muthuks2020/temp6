"""Internal audit & tax checks on vendor bills - READ-ONLY.
Each check returns the exceptions (bills / lines / vendors) so audit and tax teams can follow them up."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException

from .bills import bill_sql
from .db import q
from .silver import T

router = APIRouter()

GSTIN_RX = "^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$"
GST = "(cgst + sgst + igst)"

# name -> (title, why it matters, SQL on CTE "reg" (bill level), money column)
CHECKS = {
    "intergroup": (
        "Inter-branch & intercompany bills",
        "No real external purchase - only GST moves between own GSTINs / group companies. Keep out of "
        "material cost; the GST must match the other branch's outward invoice (GSTR-1 vs 2B).",
        """SELECT txn_class, bill_date, bill_number, vendor_bill_ref, vendor, vendor_gstin, company,
                  company_gstin, order_type, taxable_value, cgst, sgst, igst, invoice_total, payment_state
           FROM reg WHERE txn_class <> 'External'""", "invoice_total"),
    "gstin_missing": (
        "GST charged but vendor GSTIN missing or invalid",
        "Input tax credit can't be claimed without a valid supplier GSTIN.",
        f"""SELECT bill_date, bill_number, vendor, vendor_gstin, taxable_value, cgst, sgst, igst,
                  {GST} AS gst, company
           FROM reg WHERE {GST} <> 0 AND COALESCE(gst_treatment, '') <> 'overseas'
             AND (vendor_gstin IS NULL OR upper(regexp_replace(vendor_gstin, '[[:space:]]', '', 'g')) !~ '{GSTIN_RX}')""",
        "gst"),
    "tax_type": (
        "Wrong tax type for the vendor's state",
        "Same state (vendor GSTIN state code = company GSTIN state code) should be CGST+SGST; "
        "different state should be IGST. Wrong type = ITC at risk and re-invoicing.",
        """SELECT bill_date, bill_number, vendor, vendor_gstin, company_gstin,
                  CASE WHEN substr(vendor_gstin,1,2) = substr(company_gstin,1,2) THEN 'Same state - IGST charged'
                       ELSE 'Other state - CGST/SGST charged' END AS issue,
                  taxable_value, cgst, sgst, igst, company
           FROM reg
           WHERE length(vendor_gstin) >= 15 AND length(company_gstin) >= 15 AND txn_class = 'External'
             AND ((substr(vendor_gstin,1,2) = substr(company_gstin,1,2) AND igst <> 0)
               OR (substr(vendor_gstin,1,2) <> substr(company_gstin,1,2) AND (cgst + sgst) <> 0))""",
        "taxable_value"),
    "duplicates": (
        "Possible duplicate bills",
        "Same vendor and same vendor invoice number booked more than once - risk of double payment and double ITC.",
        """SELECT vendor, vendor_gstin, upper(trim(vendor_bill_ref)) AS vendor_invoice_no, COUNT(*) AS times_booked,
                  STRING_AGG(bill_number, ', ' ORDER BY bill_date) AS bills, SUM(invoice_total) AS invoice_total,
                  MIN(bill_date) AS first_date, MAX(bill_date) AS last_date
           FROM reg WHERE doc_type = 'Bill' AND COALESCE(trim(vendor_bill_ref), '') <> ''
           GROUP BY vendor_id, vendor, vendor_gstin, upper(trim(vendor_bill_ref)) HAVING COUNT(*) > 1""",
        "invoice_total"),
    "no_po": (
        "Bills without a purchase order (above ₹50,000)",
        "Purchases bypassing the PO / approval process.",
        """SELECT bill_date, bill_number, vendor_bill_ref, vendor, journal, taxable_value, invoice_total, company
           FROM reg WHERE doc_type = 'Bill' AND po_numbers IS NULL AND txn_class = 'External'
             AND taxable_value >= 50000""", "invoice_total"),
    "late_booking": (
        "Bills booked late / ITC time-limit risk",
        "Booked 90+ days after the invoice date, or after 30 Nov of the next financial year "
        "(section 16(4) ITC time limit).",
        """SELECT bill_date, accounting_date, (accounting_date - bill_date) AS days_late, bill_number, vendor,
                  {gst} AS gst, invoice_total, company,
                  CASE WHEN accounting_date > make_date(EXTRACT(YEAR FROM (bill_date - INTERVAL '3 months'))::int + 1, 11, 30)
                       THEN 'After ITC deadline' ELSE '90+ days late' END AS issue
           FROM reg WHERE doc_type = 'Bill' AND accounting_date - bill_date >= 90""".replace("{gst}", GST),
        "gst"),
    "tds_194q": (
        "TDS 194Q - vendors over ₹50 lakh in a year with no TDS",
        "Purchases of goods above ₹50 lakh from one seller in a financial year need TDS 0.1% on the excess "
        "(when your turnover is above ₹10 crore). Check these vendors.",
        """SELECT EXTRACT(YEAR FROM (bill_date - INTERVAL '3 months'))::int AS fy_start, vendor, vendor_gstin,
                  COUNT(*) AS bills, SUM(taxable_value) AS purchases, SUM(tds) AS tds_deducted
           FROM reg WHERE txn_class = 'External' AND doc_type = 'Bill'
             AND COALESCE(gst_treatment, '') <> 'overseas'
           GROUP BY 1, vendor_id, vendor, vendor_gstin
           HAVING SUM(taxable_value) > 5000000 AND COALESCE(SUM(tds), 0) = 0""", "purchases"),
    "unpaid_old": (
        "External bills not paid after 90 days",
        "Overdue payables - MSME vendors must be paid within 45 days (section 43B(h)).",
        """SELECT bill_date, due_date, (CURRENT_DATE - bill_date) AS age_days, bill_number, vendor, invoice_total,
                  payment_state, company
           FROM reg WHERE doc_type = 'Bill' AND txn_class = 'External'
             AND payment_state IN ('not_paid', 'partial') AND CURRENT_DATE - bill_date > 90""", "invoice_total"),
}

RATE_CHECK = ("rate_above_po", "Billed rate higher than PO rate (> 1%)",
              "The vendor billed a higher unit price than agreed on the PO.")


def _p(company_id, date_from, date_to):
    return {"company_id": company_id, "vendor_id": None, "date_from": date_from, "date_to": date_to,
            "doc_type": None, "s": None, "txn": None}


RATE_FINAL = """SELECT *, ROUND((100.0 * (billed_rate - po_rate) / NULLIF(po_rate, 0))::numeric, 2) AS above_po_pct,
       ROUND(((billed_rate - po_rate) * quantity)::numeric, 2) AS excess_amount
FROM x WHERE po_rate > 0 AND billed_rate > po_rate * 1.01"""


def rate_x_sql():
    """the bill line vs PO line rates (CTE x) - saved by procurement_mart.bill_po_rates"""
    return f"""
WITH x AS (
    SELECT am.invoice_date AS bill_date, am.company_id, am.name AS bill_number, p.name AS vendor, po.name AS po_number,
           pp.default_code AS item_code, aml.name AS line, aml.quantity,
           ROUND((aml.price_unit * (1 - COALESCE(aml.discount, 0) / 100))::numeric, 4) AS billed_rate,
           ROUND((pol.price_unit * (1 - COALESCE(pol.discount, 0) / 100))::numeric, 4) AS po_rate,
           aml.balance AS amount
    FROM {T('account_move_line')} aml
    JOIN {T('account_move')} am ON am.id = aml.move_id
    JOIN {T('purchase_order_line')} pol ON pol.id = aml.purchase_line_id
    JOIN {T('purchase_order')} po ON po.id = pol.order_id
    JOIN {T('res_partner')} p ON p.id = am.partner_id
    LEFT JOIN {T('product_product')} pp ON pp.id = aml.product_id
    WHERE am.move_type = 'in_invoice' AND am.state = 'posted' AND aml.display_type = 'product'
      AND am.currency_id IS NOT DISTINCT FROM po.currency_id
      AND (%(date_from)s::date IS NULL OR am.invoice_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR am.invoice_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR am.company_id = ANY(%(company_id)s::int[]))
)"""


def rate_sql():
    from . import mart
    if mart.use("bill_po_rates"):
        return f"""
WITH x AS (SELECT * FROM {mart.M('bill_po_rates')}
    WHERE (%(date_from)s::date IS NULL OR bill_date >= %(date_from)s::date)
      AND (%(date_to)s::date   IS NULL OR bill_date <= %(date_to)s::date)
      AND (%(company_id)s::int[] IS NULL OR company_id = ANY(%(company_id)s::int[])))
""" + RATE_FINAL
    return rate_x_sql() + "\n" + RATE_FINAL


@router.get("/api/audit/summary")
def audit_summary(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """Count and value of exceptions for every check (run one after another)."""
    p = _p(company_id, date_from, date_to)
    out = []
    names = list(CHECKS)
    try:   # all checks in ONE statement: the bill register is read once and shared by every check
        ctes = ", ".join(f"c_{n} AS ({CHECKS[n][2]})" for n in names)
        union = " UNION ALL ".join(
            f"SELECT '{n}' AS chk, COUNT(*) AS n, COALESCE(SUM({CHECKS[n][3]}), 0) AS value FROM c_{n}" for n in names)
        got = {r["chk"]: r for r in q(bill_sql() + ", " + ctes + " " + union, p)}
        for n in names:
            title, why, _, _ = CHECKS[n]
            out.append({"check": n, "title": title, "why": why, "n": got[n]["n"], "value": got[n]["value"]})
    except HTTPException:  # fall back to one query per check so one bad check cannot hide the others
        for name, (title, why, sql, money) in CHECKS.items():
            try:
                r = q(bill_sql() + f"SELECT COUNT(*) AS n, COALESCE(SUM({money}), 0) AS value FROM ({sql}) z", p)[0]
                out.append({"check": name, "title": title, "why": why, **r})
            except HTTPException as e:
                out.append({"check": name, "title": title, "why": why, "n": None, "value": None, "error": e.detail})
    try:
        r = q(f"SELECT COUNT(*) AS n, COALESCE(SUM(excess_amount), 0) AS value FROM ({rate_sql()}) z", p)[0]
        out.append({"check": RATE_CHECK[0], "title": RATE_CHECK[1], "why": RATE_CHECK[2], **r})
    except HTTPException as e:
        out.append({"check": RATE_CHECK[0], "title": RATE_CHECK[1], "why": RATE_CHECK[2], "n": None, "error": e.detail})
    return out


@router.get("/api/audit/check/{name}")
def audit_check(name: str, company_id: Optional[str] = None, date_from: Optional[date] = None,
                date_to: Optional[date] = None):
    p = _p(company_id, date_from, date_to)
    if name == RATE_CHECK[0]:
        return q(rate_sql() + " ORDER BY excess_amount DESC LIMIT 2000", p)
    if name not in CHECKS:
        raise HTTPException(404, "unknown check")
    title, why, sql, money = CHECKS[name]
    return q(bill_sql() + f"SELECT * FROM ({sql}) z ORDER BY {money} DESC NULLS LAST LIMIT 2000", p)


@router.get("/api/audit/intergroup-summary")
def intergroup_summary(company_id: Optional[str] = None, date_from: Optional[date] = None,
                       date_to: Optional[date] = None):
    """External vs Inter-branch vs Intercompany: value and GST."""
    return q(bill_sql() + """
        SELECT txn_class, COUNT(*) AS bills, SUM(taxable_value) AS taxable, SUM(cgst) AS cgst, SUM(sgst) AS sgst,
               SUM(igst) AS igst, SUM(cgst + sgst + igst) AS gst, SUM(invoice_total) AS total,
               COUNT(*) FILTER (WHERE payment_state IN ('paid', 'in_payment', 'reversed')) AS settled,
               COUNT(*) FILTER (WHERE payment_state IN ('not_paid', 'partial')) AS open
        FROM reg GROUP BY txn_class ORDER BY txn_class = 'External' DESC, taxable DESC""",
             _p(company_id, date_from, date_to))


@router.get("/api/audit/intergroup-pairs")
def intergroup_pairs(company_id: Optional[str] = None, date_from: Optional[date] = None,
                     date_to: Optional[date] = None):
    """From (vendor branch / group company GSTIN) -> To (buying company GSTIN)."""
    return q(bill_sql() + """
        SELECT txn_class, vendor AS from_party, vendor_gstin AS from_gstin, company AS to_company,
               company_gstin AS to_gstin, COUNT(*) AS bills, SUM(taxable_value) AS taxable,
               SUM(cgst + sgst + igst) AS gst, SUM(invoice_total) AS total, MAX(bill_date) AS last_bill
        FROM reg WHERE txn_class <> 'External'
        GROUP BY 1, 2, 3, 4, 5 ORDER BY total DESC NULLS LAST LIMIT 500""", _p(company_id, date_from, date_to))
