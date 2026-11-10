"""Optional fast path: pre-computed materialized views in a separate reporting schema (default "procurement_mart").

The views are created from export_mart_sql.py - the SAME SQL the app runs on silver, saved once a day - so numbers
are identical. When a view exists and is filled, the app reads it (fast). When it does not, the app falls back to
the live silver query automatically, so a page is never empty because the mart is missing.

database.ini [app]:
    mart_schema = procurement_mart      ; schema holding the views
    use_mart    = auto                  ; auto (use what exists) | no (always silver)
Nothing here ever writes: the app only SELECTs from the views; the data engineer creates and refreshes them.
"""
import configparser
import threading

from .config import CONFIG_FILE

_ini = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
_ini.read(CONFIG_FILE, encoding="utf-8")
_app = _ini["app"] if _ini.has_section("app") else {}
SCHEMA = (_app.get("mart_schema", "procurement_mart") or "procurement_mart").strip()
MODE = (_app.get("use_mart", "auto") or "auto").strip().lower()

# columns each view must have to be used (an older or different view with the same name is ignored)
NEEDS = {
    "purchase_lines": {"odoo_line_id", "bill_date", "company_id", "vendor_id", "product_id", "account_id",
                       "order_type_id", "qty_product_uom", "txn_class", "source", "taxable_value", "quantity"},
    "vendor_bills": {"odoo_move_id", "bill_date", "company_id", "move_type", "vendor_id", "txn_class", "invoice_total"},
    "po_status": {"po_id", "po_date", "company_id", "vendor_id", "grn_status", "grn_pending_value", "overdue_amount"},
    "po_bill_links": {"po_id", "move_id", "move_type", "residual", "invoice_date_due"},
    "vendor_returns": {"picking_id", "doc_date", "company_id", "kind", "credit_status", "value"},
    "return_lines": {"picking_id", "doc_date", "company_id", "product_id", "qty", "unit_inr"},
    "receipt_lines": {"vendor_id", "company_id", "po_id", "picking_id", "received", "po_date", "product_id"},
    "stock_daily": {"product_id", "company_id", "day", "qty"},
    "freight_lines": {"line_id", "bill_date", "company_id", "amount"},
    "freight_alloc": {"line_id", "po_id", "freight_alloc"},
    "bill_po_rates": {"bill_date", "company_id", "billed_rate", "po_rate"},
    "transit_moves": {"picking_id", "doc_date", "company_id", "product_id", "qty", "direction"},
}
VIEWS = ["purchase_lines", "vendor_bills", "po_status", "po_bill_links", "vendor_returns", "return_lines",
         "receipt_lines", "stock_daily", "freight_lines", "freight_alloc", "bill_po_rates", "transit_moves"]

_lock = threading.Lock()
_found = None          # set of filled views, read once (and again after "Refresh data")
_force_silver = threading.local()


def M(view):
    return f"{SCHEMA}.{view}"


def _load():
    from .db import q
    rows = q("""SELECT c.relname AS name, COALESCE(mv.ispopulated, true) AS filled,
                       ARRAY(SELECT a.attname::text FROM pg_attribute a
                             WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS cols
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_matviews mv ON mv.schemaname = n.nspname AND mv.matviewname = c.relname
                WHERE n.nspname = %(s)s AND c.relkind IN ('m', 'v', 'r')
                  AND has_table_privilege(c.oid, 'SELECT')""", {"s": SCHEMA})
    return {r["name"] for r in rows if r["filled"] and NEEDS.get(r["name"], set()) <= set(r["cols"] or [])}


def use(view):
    """True when the app should read this view from the mart."""
    global _found
    if MODE in ("no", "false", "off", "0") or getattr(_force_silver, "on", False):
        return False
    if _found is None:
        with _lock:
            if _found is None:
                try:
                    _found = _load()
                except Exception:      # no permission / schema missing -> silver
                    _found = set()
    return view in _found


_last_error = None


def disable(reason=""):
    """A view failed while reading (dropped, being re-created, no permission): use silver until "Refresh data"."""
    global _found, _last_error
    with _lock:
        _found = set()
        _last_error = reason[:300]


def reset():
    """Re-detect the views (called by "Refresh data")."""
    global _found, _last_error
    with _lock:
        _found = None
        _last_error = None


class silver_only:
    """with silver_only(): ... -> build the plain silver SQL (used by export_mart_sql.py to write the views)."""
    def __enter__(self):
        _force_silver.on = True

    def __exit__(self, *a):
        _force_silver.on = False


def status():
    return {"schema": SCHEMA, "mode": MODE, "last_error": _last_error, "views_in_use": sorted(v for v in VIEWS if use(v)),
            "views_missing": sorted(v for v in VIEWS if not use(v))}
