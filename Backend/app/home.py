"""Home - "What needs attention": one number per area, each linked to the page behind it, plus the
items to act on first. Reuses the same calculations as the pages (same cache), so the numbers always agree.
READ-ONLY on the silver tables."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException

from .insights import insights_summary
from .p2p import p2p_summary, p2p_list
from .returns import returns_summary, returns_list
from .audit import audit_summary
from .jobwork import jobwork_summary
from .transit import transit_summary
from . import priceimpact

router = APIRouter()


def _fy(today=None):
    t = today or date.today()
    y = t.year if t.month >= 4 else t.year - 1
    return date(y, 4, 1), t


def _f(v):
    return float(v or 0)


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k), None
    except HTTPException as e:
        return None, str(e.detail)
    except Exception as e:  # one area failing must not blank the whole page
        return None, str(e)


def _t_impact(company_id, date_from, date_to):
    pi = priceimpact.summary(company_id=company_id, b_from=None, b_to=None, c_from=None, c_to=None,
                             txn="External", category=None)
    return [{"key": "pl_hit", "label": "Price rise hit to P&L", "value": pi["pl_hit"], "page": "impact",
             "sub": f"of {pi['price_impact']:,.0f} price impact · {pi['realisation_pct'] or 0:.0f}% realised",
             "tone": "bad" if pi["pl_hit"] > 0 else "good"}], pi


def _t_vendors(company_id, date_from, date_to):
    ins = insights_summary(company_id=company_id, date_from=date_from, date_to=date_to, category=None, txn=None, min_pct=10)
    return [{"key": "extra_paid", "label": "Paid above the best vendor", "value": ins.get("extra_paid"), "page": "vendors",
             "sub": f"{ins.get('gap_products', 0)} items bought at 2+ prices", "tone": "warn"}], None


def _t_p2p(company_id, date_from, date_to):
    # every open PO, whatever its date (same cached read as the Procurement Brief scorecard)
    p2p = p2p_summary(company_id=company_id, date_from=None, date_to=None, vendor_id=None)
    return [{"key": "to_bill", "label": "Received, not yet billed (accrual)", "value": p2p.get("to_bill_value"),
             "page": "p2p", "sub": f"{p2p.get('to_bill_pos', 0)} POs · book as GRNI at month end", "tone": "warn"},
            {"key": "overdue", "label": "Vendor payments overdue", "value": p2p.get("overdue_amount"),
             "page": "p2p", "sub": f"{p2p.get('overdue_bills', 0)} bills past due date", "tone": "bad"},
            {"key": "grn_pending", "label": "Ordered, not yet received", "value": p2p.get("grn_pending_value"),
             "page": "p2p", "sub": f"{p2p.get('grn_pending_pos', 0)} POs open", "tone": ""}], None


def _t_returns(company_id, date_from, date_to):
    ret = returns_summary(company_id=company_id, date_from=date_from, date_to=date_to, kind=None)
    return [{"key": "returns", "label": "Returns not credited by vendors", "value": ret.get("value_without_debit_note"),
             "page": "vendors", "sub": f"{ret.get('without_debit_note', 0)} returns without a full debit note",
             "tone": "bad"}], None


def _t_audit(company_id, date_from, date_to):
    aud = audit_summary(company_id=company_id, date_from=date_from, date_to=date_to)
    flagged = [c for c in aud if c.get("n")]
    itc = sum(_f(c.get("value")) for c in aud if c["check"] in ("gstin_missing", "tax_type", "late_booking"))
    return [{"key": "audit", "label": "GST / ITC at risk", "value": itc, "page": "control",
             "sub": f"{len(flagged)} of {len(aud)} checks have exceptions", "tone": "bad" if itc else "good"}], None


def _t_outside(company_id, date_from, date_to):
    tr_, _ = _safe(transit_summary, company_id=company_id, date_from=date_from, date_to=date_to)
    jw, _ = _safe(jobwork_summary, company_id=company_id, date_from=date_from, date_to=date_to)
    v = _f((tr_ or {}).get("in_transit_value")) + _f((jw or {}).get("pending_value"))
    return [{"key": "outside", "label": "Stock outside our stores", "value": v, "page": "control",
             "sub": f"in transit {_f((tr_ or {}).get('in_transit_value')):,.0f} · with job workers "
                    f"{_f((jw or {}).get('pending_value')):,.0f}", "tone": "warn"}], None


ORDER_WARM = ["p2p", "vendors", "returns", "audit", "outside", "impact"]
TILES = {"impact": _t_impact, "p2p": _t_p2p, "vendors": _t_vendors, "returns": _t_returns, "audit": _t_audit,
         "outside": _t_outside}


@router.get("/api/home/tile/{key}")
def home_tile(key: str, company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """One group of home tiles - the page asks for each group at the same time, so each shows as soon as it is ready."""
    if key not in TILES:
        raise HTTPException(404, "unknown tile")
    f, t = _fy()
    tiles, extra = TILES[key](company_id, date_from or f, date_to or t)
    out = {"tiles": tiles}
    if key == "impact" and extra:
        out["takeaways"] = extra.get("takeaways", [])
        out["periods"] = extra.get("periods")
    return out


@router.get("/api/home/summary")
def home_summary(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """All tiles in one call (kept for scripts); the page itself uses /api/home/tile/{key}."""
    f, t = _fy()
    date_from, date_to = date_from or f, date_to or t
    tiles, errors, pi = [], {}, None
    for k, fn in TILES.items():
        res, e = _safe(fn, company_id, date_from, date_to)
        if res:
            tiles += res[0]
            pi = res[1] if k == "impact" else pi
        else:
            errors[k] = e
    return {"period": {"from": date_from, "to": date_to}, "impact_periods": pi["periods"] if pi else None,
            "takeaways": (pi or {}).get("takeaways", []), "tiles": tiles, "errors": errors}


@router.get("/api/home/actions")
def home_actions(company_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    """The first things to act on: biggest P&L exposures, overdue payments, uncredited returns."""
    f, t = _fy()
    date_from, date_to = date_from or f, date_to or t
    out = []
    top, _ = _safe(priceimpact.top, rank="pl_hit", source=None, n=4, direction="increase", company_id=company_id,
                   b_from=None, b_to=None, c_from=None, c_to=None, txn="External", category=None)
    for r in top or []:
        out.append({"area": "Price rise", "page": "impact", "ref": r["item_code"] or "", "name": r["product"],
                    "amount": r["pl_hit"],
                    "what": f"rate +{r['rate_change_pct'] or 0:.1f}% vs base; negotiate or re-source"})
    od, _ = _safe(p2p_list, stage="overdue", grn=None, bill=None, pay=None, company_id=company_id,
                  date_from=None, date_to=None, vendor_id=None)
    for r in sorted(od or [], key=lambda r: -_f(r.get("bill_overdue")))[:3]:
        out.append({"area": "Overdue payment", "page": "p2p", "ref": r.get("po_number"), "name": r.get("vendor"),
                    "amount": r.get("bill_overdue"),
                    "what": f"bills {r.get('bills') or ''} · oldest due {str(r.get('oldest_due') or '')[:10]}"})
    rl, _ = _safe(returns_list, pending_only=True, company_id=company_id, date_from=date_from, date_to=date_to, kind=None)
    for r in (rl or [])[:3]:
        out.append({"area": "Return not credited", "page": "vendors", "ref": r.get("return_no"), "name": r.get("vendor"),
                    "amount": r.get("to_recover"),
                    "what": f"returned {str(r.get('return_date') or '')[:10]} · ask for the debit note"})
    return out
