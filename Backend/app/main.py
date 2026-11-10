"""
Purchase Register & Costing - API + web UI (READ-ONLY on the silver tables)
Start:  .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8050
(or double-click "Start Purchase Register.bat" in the ceo_dashboard folder)
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
import asyncio

from .db import q, clear_cache, status as db_status, current_request, cancel_request, forget_request
from .bills import router as bills_router
from .silver import router as silver_router
from .freight import router as freight_router
from .returns import router as returns_router
from .imports import router as imports_router
from .audit import router as audit_router
from .jobwork import router as jobwork_router
from .transit import router as transit_router
from .insights import router as insights_router
from .p2p import router as p2p_router
from .priceimpact import router as priceimpact_router
from .home import router as home_router
from .purchasehead import router as purchasehead_router
from .explore import router as explore_router

app = FastAPI(title="Procurement Analytics - Finance edition")
class CancelOnDisconnect:
    """If the browser drops an /api request (page or filter changed), stop its database query at once,
    so abandoned pages never keep the app's database connections busy."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        rid = id(object())
        token = current_request.set(rid)
        inbox: asyncio.Queue = asyncio.Queue()

        async def watch():
            while True:
                msg = await receive()
                await inbox.put(msg)
                if msg["type"] == "http.disconnect":
                    cancel_request(rid)
                    return

        watcher = asyncio.create_task(watch())
        try:
            await self.app(scope, inbox.get, send)
        finally:
            watcher.cancel()
            forget_request(rid)
            current_request.reset(token)


app.add_middleware(CancelOnDisconnect)
app.include_router(home_router)
app.include_router(purchasehead_router)
app.include_router(explore_router)
app.include_router(priceimpact_router)
app.include_router(bills_router)    # Bills / GST register
app.include_router(silver_router)   # purchase register, price analysis, costing
app.include_router(freight_router)  # freight insights
app.include_router(returns_router)  # purchase returns
app.include_router(imports_router)  # import landed cost
app.include_router(audit_router)    # audit & tax checks
app.include_router(jobwork_router)  # job work POs & DCs to vendors
app.include_router(transit_router)  # inter-company stock in transit
app.include_router(insights_router) # price & vendor watch
app.include_router(p2p_router)      # PO -> GRN -> bill -> payment


@app.on_event("startup")
def _warm_up():
    """Load the landing page (Purchase Register, this month) in the background so the first visit is instant."""
    import threading
    from datetime import date

    def run():
        try:
            from .silver import meta, register, register_summary
            d = date.today().replace(day=1)
            meta()
            register_summary(date_from=d, date_to=None, company_id=None, vendor_id=None, product_id=None,
                             category=None, search="", gl=None, source=None, ot=None, txn=None)
            register(date_from=d, date_to=None, company_id=None, vendor_id=None, product_id=None, category=None,
                     search="", page=1, page_size=50, sort="bill_date", order="desc", gl=None, source=None, ot=None,
                     txn=None)
            print("[warm-up] landing page data ready", flush=True)
            # finance edition: Home tiles, Procurement Brief and Price Impact with their default filters,
            # one after another so the warm-up never uses more than one database connection
            from . import home, purchasehead
            for k in home.ORDER_WARM:
                try:
                    home.home_tile(k, company_id=None, date_from=None, date_to=None)
                    print(f"[warm-up] home: {k} ready", flush=True)
                except Exception as e:
                    print(f"[warm-up] home {k} skipped: {e}", flush=True)
            for fn in (purchasehead.brief, purchasehead.savings, purchasehead.scorecard, purchasehead.process):
                try:
                    fn(company_id=None, date_from=None, date_to=None, txn="External")
                    print(f"[warm-up] brief: {fn.__name__} ready", flush=True)
                except Exception as e:
                    print(f"[warm-up] brief {fn.__name__} skipped: {e}", flush=True)
        except Exception as e:  # never stop the app because of warm-up
            print(f"[warm-up] skipped: {e}", flush=True)
    threading.Thread(target=run, daemon=True).start()


@app.get("/api/health")
def health():
    q("SELECT 1 AS ok")
    from . import mart
    return {"ok": True, "silver": f"{settings.silver_schema}.{settings.table_prefix}*", "database": settings.dbname,
            "read_only": True, "mart": mart.status()}


@app.get("/api/status")
def app_status():
    """Queries in progress right now (open http://localhost:8050/api/status when a page keeps loading)."""
    return db_status()


@app.post("/api/cache/clear")
def cache_clear():
    from . import mart
    mart.reset()                       # pick up views the data engineer created or refreshed
    return {"cleared": clear_cache()}


# ---------------------------------------------------------------- serve React build
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):        # an /api address that does not exist -> clear JSON error, not the page
            from fastapi import HTTPException
            raise HTTPException(404, f"Unknown address /{full_path} - is the server running the latest version? Restart it.")
        return FileResponse(DIST / "index.html")
