"""Connection pool + query helpers."""
import json
import threading
import time
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import psycopg2
import psycopg2.errors
import psycopg2.extras
import psycopg2.pool
from fastapi import HTTPException

from .config import settings

_pool = None


_pool_lock = threading.Lock()
_running = {}                      # id -> (started, sql label) of queries in progress (for /api/status)

# ---- stop database work for requests the browser has abandoned (page / filter changed, tab closed)
import contextvars
current_request = contextvars.ContextVar("current_request", default=None)
_req_conns = {}                    # request id -> set of connections currently running a query for it
_req_lock = threading.Lock()


def cancel_request(rid):
    """Called when the browser disconnects: ask PostgreSQL to stop that request's running query."""
    with _req_lock:
        conns = list(_req_conns.get(rid, ()))
    for c in conns:
        try:
            c.cancel()
        except Exception:
            pass


def forget_request(rid):
    with _req_lock:
        _req_conns.pop(rid, None)


class Busy(Exception):
    pass
_slots = None                      # requests queue here instead of failing with "pool exhausted"


def _slot_sem():
    global _slots
    with _pool_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max(1, settings.pool_max))
        return _slots


def pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                1, settings.pool_max,
                host=settings.host, port=settings.port, dbname=settings.dbname,
                user=settings.user, password=settings.password, sslmode=settings.sslmode,
                connect_timeout=15, application_name="purchase_register_ui",
                options="-c default_transaction_read_only=on",   # the app can never write
            )
        return _pool


def _release(p, conn, key, close):
    """Give the connection back; never let a pool problem become a user-facing error."""
    try:
        p.putconn(conn, key=key, close=close)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def cursor(autocommit=False, timeout=True):
    # every checkout gets its OWN key: the default key is the thread id, so two requests served by
    # the same worker thread could share one connection ("trying to put unkeyed connection")
    key = object()
    sem = _slot_sem()
    if not sem.acquire(timeout=60):    # wait up to a minute for a free connection before saying "busy"
        raise Busy()
    try:
        p = pool()
        conn = p.getconn(key=key)
    except Exception:
        sem.release()                    # never leak a slot when the database is unreachable
        raise
    broken = False
    rid = current_request.get()
    if rid is not None:
        with _req_lock:
            _req_conns.setdefault(rid, set()).add(conn)
    try:
        if conn.closed:
            raise psycopg2.InterfaceError("connection already closed")
        conn.rollback()                      # always start clean
        if conn.autocommit != autocommit:
            conn.autocommit = autocommit
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET TIME ZONE 'Asia/Kolkata'")
            if conn.server_version >= 110000:
                # PostgreSQL's JIT compiler spends seconds compiling each big report query before running it
                # (measured here: 1.4 s compile vs 3 ms run). Off for this app's own session only.
                cur.execute("SET jit = off")
            cur.execute(f"SET statement_timeout = '{settings.statement_timeout_s if timeout else 0}s'")
            yield cur
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True                        # dropped connection: don't reuse it
        raise
    finally:
        try:
            if not conn.closed:
                conn.rollback()              # the UI only reads; end any open transaction
                if conn.autocommit:
                    conn.autocommit = False
        except Exception:
            broken = True
        if rid is not None:
            with _req_lock:
                _req_conns.get(rid, set()).discard(conn)
        _release(p, conn, key, broken or bool(conn.closed))
        sem.release()


def _clean(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


# ---------------------------------------------------------------- in-memory result cache
_cache = {}                     # key -> (expires_at, rows)
_cache_lock = threading.Lock()


def clear_cache():
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n


_inflight = {}                  # key -> lock, so identical queries arriving together run only once


def company_ids(v):
    """company filter -> sorted list of ints, or None for all companies. Accepts 3, '3', '1,3', [1, 3]."""
    if v is None or v == "":
        return None
    parts = v if isinstance(v, (list, tuple)) else str(v).split(",")
    ids = sorted({int(x) for x in parts if str(x).strip().lstrip("-").isdigit()})
    return ids or None


def _norm_companies(params):
    if isinstance(params, dict):
        for k in ("company_id", "c"):
            if k in params:
                params = {**params, k: company_ids(params[k])}
    return params


def q(sql, params=None):
    """Run a read-only query. Results are cached in memory for cache_ttl_minutes.
    If the same query is already running, wait for it instead of running it again."""
    params = _norm_companies(params)
    key = sql + "|" + json.dumps(params or {}, sort_keys=True, default=str)
    ttl = settings.cache_ttl_min * 60
    if ttl <= 0:
        return _run(sql, params, key, 0)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        lock = _inflight.setdefault(key, threading.Lock())
    with lock:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            return _run(sql, params, key, ttl)
        finally:
            with _cache_lock:
                _inflight.pop(key, None)


def _run(sql, params, key, ttl):
    now = time.time()
    t0 = time.time()
    label = " ".join(sql.split())[:70]
    tid = object()
    try:
        with cursor() as cur:
            _running[id(tid)] = (t0, label)
            cur.execute(sql, params or {})
            rows = [{k: _clean(v) for k, v in r.items()} for r in cur.fetchall()]
    except Busy:
        raise HTTPException(503, "The app is busy: earlier pages are still loading from the database. "
                                 "Wait a moment and press Refresh data (see /api/status).")
    except psycopg2.errors.QueryCanceled as e:
        if "user request" in str(e):
            raise HTTPException(499, "Cancelled - the page was closed or the filter changed.")
        raise HTTPException(504, f"This query took longer than {settings.statement_timeout_s}s and was stopped. "
                                 "Choose a shorter period or ask for the indexes in HOW_TO_INTEGRATE.md.")
    except psycopg2.OperationalError as e:
        raise HTTPException(503, f"Cannot reach the database: {e}")
    except psycopg2.Error as e:
        from . import mart
        if mart.SCHEMA in sql and isinstance(e, (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn,
                                                 psycopg2.errors.InsufficientPrivilege,
                                                 psycopg2.errors.ObjectNotInPrerequisiteState)):
            # a reporting view is missing / not filled / not readable: stop using the mart, the page retries on silver
            mart.disable(str(e.pgerror or e))
            raise HTTPException(503, "Reporting views not readable - switched to live silver data.",
                                headers={"X-Retry-Silver": "1"})
        raise HTTPException(500, f"Database error: {e.pgerror or e}")
    finally:
        _running.pop(id(tid), None)
    ms = (time.time() - t0) * 1000
    print(f"[query] {ms:7.0f} ms  {len(rows):6d} rows  {label}", flush=True)
    if ttl > 0:
        with _cache_lock:
            if len(_cache) > 500:          # keep memory small
                _cache.clear()
            _cache[key] = (now + ttl, rows)
    return rows


def status():
    """What the app is doing right now - for /api/status."""
    now = time.time()
    busy = [{"seconds": round(now - t0), "query": lbl} for t0, lbl in sorted(_running.values())]
    return {"connections_max": settings.pool_max, "queries_running": len(busy), "running": busy,
            "cached_results": len(_cache)}
