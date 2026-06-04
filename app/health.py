from datetime import datetime, timezone

from fastapi import APIRouter

from app.database import get_connection
from app.models import HealthResponse, StoreHealth

router = APIRouter(tags=["health"])

_STALE_THRESHOLD_MINUTES = 10


async def compute_health() -> HealthResponse:
    now = datetime.now(timezone.utc)

    async with get_connection() as db:
        cur = await db.execute(
            "SELECT store_id, MAX(timestamp) AS last_event_at FROM events GROUP BY store_id"
        )
        rows = await cur.fetchall()

    stores: list[StoreHealth] = []
    for row in rows:
        last_event_at = datetime.fromisoformat(row["last_event_at"].replace("Z", "+00:00"))
        if last_event_at.tzinfo is None:
            last_event_at = last_event_at.replace(tzinfo=timezone.utc)
        lag_minutes = round((now - last_event_at).total_seconds() / 60, 2)
        stores.append(StoreHealth(
            store_id=row["store_id"],
            status="STALE_FEED" if lag_minutes > _STALE_THRESHOLD_MINUTES else "OK",
            last_event_at=last_event_at,
            lag_minutes=lag_minutes,
        ))

    overall = "OK" if all(s.status == "OK" for s in stores) else "DEGRADED"
    return HealthResponse(status=overall, stores=stores, checked_at=now)


@router.get("/health", response_model=HealthResponse)
async def health_endpoint() -> HealthResponse:
    return await compute_health()
