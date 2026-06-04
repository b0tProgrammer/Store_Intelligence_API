import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.models import MetricsResponse, ZoneDwell

router = APIRouter(prefix="/stores", tags=["analytics"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def compute_metrics(store_id: str, date: Optional[str] = None) -> MetricsResponse:
    if date and not _DATE_RE.match(date):
        raise HTTPException(400, "date must be YYYY-MM-DD")

    async with get_connection() as db:
        # --- unique visitors (exclude staff) ---
        params: list = [store_id]
        date_clause = ""
        if date:
            date_clause = "AND date(timestamp) = ?"
            params.append(date)

        cur = await db.execute(
            f"""SELECT COUNT(DISTINCT visitor_id) AS cnt
                FROM events
                WHERE store_id = ? AND event_type = 'ENTRY' AND is_staff = 0 {date_clause}""",
            params,
        )
        row = await cur.fetchone()
        unique_visitors: int = row["cnt"] if row else 0

        # --- conversions via POS correlation (billing zone ±5 min window) ---
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
            [store_id] + ([date] if date else []) + [store_id],
        )
        row = await cur.fetchone()
        conversions: int = row["cnt"] if row else 0
        conversion_rate = conversions / unique_visitors if unique_visitors else 0.0

        # --- avg dwell by zone ---
        cur = await db.execute(
            f"""SELECT zone_id, AVG(dwell_ms) AS avg_dwell, COUNT(*) AS visits
                FROM events
                WHERE store_id = ? AND event_type = 'ZONE_DWELL'
                  AND is_staff = 0 AND zone_id IS NOT NULL {date_clause}
                GROUP BY zone_id
                ORDER BY visits DESC""",
            params,
        )
        rows = await cur.fetchall()
        dwell_by_zone = [
            ZoneDwell(zone_id=r["zone_id"], avg_dwell_ms=round(r["avg_dwell"] or 0, 2), visits=r["visits"])
            for r in rows
        ]

        # --- current queue depth (last known state per visitor) ---
        cur = await db.execute(
            """WITH ranked AS (
                SELECT visitor_id, event_type,
                       ROW_NUMBER() OVER (PARTITION BY visitor_id ORDER BY timestamp DESC) AS rn
                FROM events
                WHERE store_id = ?
                  AND event_type IN ('BILLING_QUEUE_JOIN', 'BILLING_QUEUE_ABANDON', 'EXIT')
            )
            SELECT COUNT(*) AS depth FROM ranked WHERE rn = 1 AND event_type = 'BILLING_QUEUE_JOIN'""",
            (store_id,),
        )
        row = await cur.fetchone()
        queue_depth: int = row["depth"] if row else 0

        # --- abandonment rate ---
        cur = await db.execute(
            f"""SELECT
                    CAST(SUM(CASE WHEN event_type = 'BILLING_QUEUE_ABANDON' THEN 1 ELSE 0 END) AS REAL)
                    / NULLIF(COUNT(*), 0) AS rate
                FROM events
                WHERE store_id = ? AND is_staff = 0 {date_clause}
                  AND event_type IN ('BILLING_QUEUE_JOIN', 'BILLING_QUEUE_ABANDON')""",
            params,
        )
        row = await cur.fetchone()
        abandonment_rate: float = (row["rate"] or 0.0) if row else 0.0

    return MetricsResponse(
        store_id=store_id,
        unique_visitors=unique_visitors,
        conversion_rate=round(conversion_rate, 4),
        avg_dwell_by_zone=dwell_by_zone,
        queue_depth=queue_depth,
        abandonment_rate=round(abandonment_rate, 4),
        computed_at=datetime.now(timezone.utc),
    )


@router.get("/{store_id}/metrics", response_model=MetricsResponse)
async def metrics_endpoint(store_id: str, date: Optional[str] = None) -> MetricsResponse:
    return await compute_metrics(store_id, date)
