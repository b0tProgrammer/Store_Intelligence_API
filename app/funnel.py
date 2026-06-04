import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.models import FunnelResponse, FunnelStage

router = APIRouter(prefix="/stores", tags=["analytics"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def compute_funnel(store_id: str, date: Optional[str] = None) -> FunnelResponse:
    if date and not _DATE_RE.match(date):
        raise HTTPException(400, "date must be YYYY-MM-DD")

    params: list = [store_id]
    date_clause = ""
    if date:
        date_clause = "AND date(timestamp) = ?"
        params.append(date)

    async with get_connection() as db:
        async def count(event_types: tuple[str, ...]) -> int:
            placeholders = ",".join("?" * len(event_types))
            cur = await db.execute(
                f"""SELECT COUNT(DISTINCT visitor_id) AS cnt
                    FROM events
                    WHERE store_id = ? AND is_staff = 0 {date_clause}
                      AND event_type IN ({placeholders})""",
                params + list(event_types),
            )
            row = await cur.fetchone()
            return row["cnt"] if row else 0

        entry_count = await count(("ENTRY",))
        zone_count = await count(("ZONE_ENTER", "ZONE_DWELL"))
        queue_count = await count(("BILLING_QUEUE_JOIN",))

        # Purchases: billing queue visit within 5 min of POS transaction
        cur = await db.execute(
            f"""WITH bv AS (
                    SELECT DISTINCT e.visitor_id, MIN(e.timestamp) AS billing_ts
                    FROM events e
                    WHERE e.store_id = ? AND e.is_staff = 0
                      AND e.event_type = 'BILLING_QUEUE_JOIN' {date_clause}
                    GROUP BY e.visitor_id
                ),
                purchases AS (
                    SELECT DISTINCT bv.visitor_id
                    FROM bv
                    JOIN pos_transactions pt ON pt.store_id = ?
                    WHERE pt.timestamp >= bv.billing_ts
                      AND (julianday(pt.timestamp) - julianday(bv.billing_ts)) * 1440 <= 5
                )
                SELECT COUNT(*) AS cnt FROM purchases""",
            params + [store_id],
        )
        row = await cur.fetchone()
        purchase_count: int = row["cnt"] if row else 0

    def pct(n: int) -> float:
        return round(n / entry_count * 100, 2) if entry_count else 0.0

    stages = [
        FunnelStage(stage="Entry", count=entry_count, pct_of_entry=100.0 if entry_count else 0.0),
        FunnelStage(stage="Zone Visit", count=zone_count, pct_of_entry=pct(zone_count)),
        FunnelStage(stage="Billing Queue", count=queue_count, pct_of_entry=pct(queue_count)),
        FunnelStage(stage="Purchase", count=purchase_count, pct_of_entry=pct(purchase_count)),
    ]

    return FunnelResponse(store_id=store_id, stages=stages, computed_at=datetime.now(timezone.utc))


@router.get("/{store_id}/funnel", response_model=FunnelResponse)
async def funnel_endpoint(store_id: str, date: Optional[str] = None) -> FunnelResponse:
    return await compute_funnel(store_id, date)
