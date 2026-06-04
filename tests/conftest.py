import os
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.database as db_module
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def test_db(tmp_path):
    """Each test gets an isolated in-memory-adjacent SQLite DB."""
    db_path = str(tmp_path / "test.db")
    db_module.set_db_path(db_path)
    await db_module.init_db()
    yield
    db_module.set_db_path(os.getenv("DB_PATH", "store_intelligence.db"))


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def entry_event(
    store_id: str = "STORE_TEST",
    visitor_id: str = "VIS_aaa",
    is_staff: bool = False,
    ts: datetime | None = None,
) -> dict:
    return {
        "event_id": f"evt-{visitor_id}-entry",
        "store_id": store_id,
        "camera_id": "CAM_01",
        "visitor_id": visitor_id,
        "event_type": "ENTRY",
        "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
        "is_staff": is_staff,
        "confidence": 0.95,
        "metadata": {},
    }


def zone_event(
    visitor_id: str,
    zone_id: str,
    event_type: str = "ZONE_DWELL",
    dwell_ms: int = 5000,
    store_id: str = "STORE_TEST",
    ts: datetime | None = None,
) -> dict:
    return {
        "event_id": f"evt-{visitor_id}-{zone_id}-{event_type}",
        "store_id": store_id,
        "camera_id": "CAM_02",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {},
    }


def billing_event(
    visitor_id: str,
    event_type: str = "BILLING_QUEUE_JOIN",
    store_id: str = "STORE_TEST",
    ts: datetime | None = None,
) -> dict:
    return {
        "event_id": f"evt-{visitor_id}-{event_type}",
        "store_id": store_id,
        "camera_id": "CAM_03",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
        "is_staff": False,
        "confidence": 0.88,
        "metadata": {"queue_depth": 2},
    }
