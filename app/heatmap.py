import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.database import get_connection
from app.models import HeatmapResponse, ZoneHeatmap

router = APIRouter(prefix="/stores", tags=["analytics"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MIN_SESSIONS_FOR_CONFIDENCE = 20


async def compute_heatmap(store_id: str, date: Optional[str] = None) -> HeatmapResponse:
    if date and not _DATE_RE.match(date):
        raise HTTPException(400, "date must be YYYY-MM-DD")

    params: list = [store_id]
    date_clause = ""
    if date:
        date_clause = "AND date(timestamp) = ?"
        params.append(date)

    async with get_connection() as db:
        cur = await db.execute(
            f"""SELECT zone_id,
                       COUNT(*) AS visits,
                       AVG(dwell_ms) AS avg_dwell,
                       COUNT(DISTINCT visitor_id) AS sessions
                FROM events
                WHERE store_id = ? AND is_staff = 0 AND zone_id IS NOT NULL
                  AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL') {date_clause}
                GROUP BY zone_id""",
            params,
        )
        rows = await cur.fetchall()

    if not rows:
        return HeatmapResponse(store_id=store_id, zones=[], computed_at=datetime.now(timezone.utc))

    # Score = visits × avg_dwell; normalize to 0-100
    raw_scores = [r["visits"] * (r["avg_dwell"] or 0) for r in rows]
    max_score = max(raw_scores) or 1

    zones = [
        ZoneHeatmap(
            zone_id=r["zone_id"],
            score=round(raw / max_score * 100, 2),
            visits=r["visits"],
            avg_dwell_ms=round(r["avg_dwell"] or 0, 2),
            data_confidence=r["sessions"] >= _MIN_SESSIONS_FOR_CONFIDENCE,
        )
        for r, raw in zip(rows, raw_scores)
    ]
    zones.sort(key=lambda z: z.score, reverse=True)

    return HeatmapResponse(store_id=store_id, zones=zones, computed_at=datetime.now(timezone.utc))


@router.get("/{store_id}/heatmap", response_model=HeatmapResponse)
async def heatmap_endpoint(store_id: str, date: Optional[str] = None) -> HeatmapResponse:
    return await compute_heatmap(store_id, date)
