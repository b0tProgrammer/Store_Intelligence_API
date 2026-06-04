import os
from contextlib import asynccontextmanager

import aiosqlite

_db_path: str = os.getenv("DB_PATH", "store_intelligence.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id       TEXT PRIMARY KEY,
    store_id       TEXT NOT NULL,
    camera_id      TEXT NOT NULL,
    visitor_id     TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    zone_id        TEXT,
    dwell_ms       INTEGER,
    is_staff       INTEGER NOT NULL DEFAULT 0,
    confidence     REAL NOT NULL,
    queue_depth    INTEGER,
    sku_zone       TEXT,
    session_seq    INTEGER,
    gender_pred    TEXT,
    age_pred       INTEGER,
    age_bucket     TEXT,
    is_face_hidden INTEGER NOT NULL DEFAULT 0,
    group_id       TEXT,
    group_size     INTEGER,
    inserted_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_store_time  ON events(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_visitor     ON events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_store_type  ON events(store_id, event_type);

CREATE TABLE IF NOT EXISTS pos_transactions (
    transaction_id   TEXT PRIMARY KEY,
    store_id         TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    basket_value_inr REAL NOT NULL,
    inserted_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pos_store_time ON pos_transactions(store_id, timestamp);
"""


def set_db_path(path: str) -> None:
    global _db_path
    _db_path = path


@asynccontextmanager
async def get_connection():
    async with aiosqlite.connect(_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        yield conn


async def init_db() -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()
