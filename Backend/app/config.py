"""Loads backend/config/database.ini (+ PR_DB_* environment overrides)."""
import configparser
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
CONFIG_FILE = Path(os.environ.get("PR_CONFIG_FILE", CONFIG_DIR / "database.ini"))


@dataclass
class Settings:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str
    pool_max: int
    gold_schema: str
    silver_schema: str
    table_prefix: str
    statement_timeout_s: int
    cache_ttl_min: int
    freight_keywords: list
    transporter_keywords: list
    allow_run: bool


def load() -> Settings:
    if not CONFIG_FILE.exists():
        raise SystemExit(f"{CONFIG_FILE} is missing - copy database.ini.example to database.ini and fill in the login.")
    c = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    c.read(CONFIG_FILE, encoding="utf-8")
    pg = c["postgres"] if c.has_section("postgres") else {}
    ap = c["app"] if c.has_section("app") else {}
    fr = c["freight"] if c.has_section("freight") else {}
    split = lambda x: [w.strip().lower() for w in x.split(",") if w.strip()]

    def v(sec, key, env, default=""):
        return os.environ.get(env) or sec.get(key, default) or default

    return Settings(
        host=v(pg, "host", "PR_DB_HOST"),
        port=int(v(pg, "port", "PR_DB_PORT", "5432")),
        dbname=v(pg, "dbname", "PR_DB_NAME"),
        user=v(pg, "user", "PR_DB_USER"),
        password=v(pg, "password", "PR_DB_PASSWORD"),
        sslmode=v(pg, "sslmode", "PR_DB_SSLMODE", "prefer"),
        pool_max=int(v(pg, "pool_max", "PR_DB_POOL_MAX", "6")),
        gold_schema=v(ap, "gold_schema", "PR_GOLD_SCHEMA", "gold"),
        silver_schema=v(ap, "silver_schema", "PR_SILVER_SCHEMA", "silver"),
        table_prefix=ap.get("table_prefix", "odoo_") if hasattr(ap, "get") else "odoo_",
        statement_timeout_s=int(v(ap, "statement_timeout_seconds", "PR_STATEMENT_TIMEOUT", "300")),
        cache_ttl_min=int(v(ap, "cache_ttl_minutes", "PR_CACHE_TTL", "30")),
        # words that mark a bill line as freight (searched in GL account name and product name)
        freight_keywords=split(fr.get("keywords", "freight, carriage, cartage, transport, courier, logistic, "
                                                 "lorry, octroi, loading, unloading, handling charge, customs, duty, "
                                                 "clearing, forwarding, demurrage, port charge, ocean, airway")),
        # words that mark a VENDOR as a transporter (all its bill lines count as freight)
        transporter_keywords=split(fr.get("transporter_keywords", "transport, logistic, roadways, cargo, courier, "
                                                                  "carrier, movers, express, freight, shipping")),
        allow_run=v(ap, "allow_run", "PR_ALLOW_RUN", "false").lower() == "true",
    )


settings = load()
