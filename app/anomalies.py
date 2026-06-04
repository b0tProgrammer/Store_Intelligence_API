from datetime import datetime, timezone

from fastapi import APIRouter

from app.database import get_connection
from app.models import Anomaly, AnomalyResponse

router = APIRouter(prefix="/stores", tags=["analytics"])

_QUEUE_SPIKE_WARN = 2.0
_QUEUE_SPIKE_CRIT = 3.0
_CONVERSION_DROP_WARN = 0.20
_CONVERSION_DROP_CRIT = 0.50
_DEAD_ZONE_MINUTES = 30


async def compute_anomalies(store_id: str) -> AnomalyResponse:
    now = datetime.now(timezone.utc)
    anomalies: list[Anomaly] = []

    async with get_connection() as db:
        # --- Queue spike ---
        # Current depth vs hourly avg over last 7 days (same hour-of-day)
        cur = await db.execute(
            """WITH ranked AS (
                SELECT visitor_id, event_type,
                       ROW_NUMBER() OVER (PARTITION BY visitor_id ORDER BY timestamp DESC) AS rn
                FROM events
                WHERE store_id = ? AND event_type IN ('BILLING_QUEUE_JOIN','BILLING_QUEUE_ABANDON','EXIT')
            )
            SELECT COUNT(*) AS depth FROM ranked WHERE rn = 1 AND event_type = 'BILLING_QUEUE_JOIN'""",
            (store_id,),
        )
        row = await cur.fetchone()
        current_depth: int = row["depth"] if row else 0

        cur = await db.execute(
            """SELECT AVG(hourly_joins) AS avg_depth
               FROM (
                   SELECT strftime('%Y-%m-%dT%H', timestamp) AS hour_bucket,
                          COUNT(*) AS hourly_joins
                   FROM events
                   WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
                     AND timestamp >= datetime('now', '-7 days')
                   GROUP BY hour_bucket
               )""",
            (store_id,),
        )
        row = await cur.fetchone()
        avg_depth: float = row["avg_depth"] or 0

        if avg_depth > 0:
            ratio = current_depth / avg_depth
            if ratio >= _QUEUE_SPIKE_CRIT:
                anomalies.append(Anomaly(
                    anomaly_type="QUEUE_SPIKE",
                    severity="CRITICAL",
                    description=f"Queue depth {current_depth} is {ratio:.1f}x the 7-day hourly average ({avg_depth:.1f})",
                    detected_at=now,
                ))
            elif ratio >= _QUEUE_SPIKE_WARN:
                anomalies.append(Anomaly(
                    anomaly_type="QUEUE_SPIKE",
                    severity="WARN",
                    description=f"Queue depth {current_depth} is {ratio:.1f}x the 7-day hourly average ({avg_depth:.1f})",
                    detected_at=now,
                ))

        # --- Conversion drop vs 7-day average ---
        cur = await db.execute(
            """WITH daily_stats AS (
                SELECT date(e.timestamp) AS day,
                       COUNT(DISTINCT e.visitor_id) AS visitors,
                       COUNT(DISTINCT p.visitor_id) AS conversions
                FROM events e
                LEFT JOIN (
                    SELECT DISTINCT bv.visitor_id
                    FROM events bv
                    JOIN pos_transactions pt ON pt.store_id = bv.store_id
                    WHERE bv.store_id = ? AND bv.event_type = 'BILLING_QUEUE_JOIN'
                      AND pt.timestamp >= bv.timestamp
                      AND (julianday(pt.timestamp) - julianday(bv.timestamp)) * 1440 <= 5
                ) p ON p.visitor_id = e.visitor_id
                WHERE e.store_id = ? AND e.event_type = 'ENTRY' AND e.is_staff = 0
                GROUP BY day
            )
            SELECT
                AVG(CASE WHEN day < date('now') THEN CAST(conversions AS REAL) / NULLIF(visitors, 0) END) AS hist_rate,
                MAX(CASE WHEN day = date('now') THEN CAST(conversions AS REAL) / NULLIF(visitors, 0) END) AS today_rate
            FROM daily_stats
            WHERE day >= date('now', '-7 days')""",
            (store_id, store_id),
        )
        row = await cur.fetchone()
        hist_rate = row["hist_rate"] or 0
        today_rate = row["today_rate"]

        if hist_rate > 0 and today_rate is not None:
            drop = (hist_rate - today_rate) / hist_rate
            if drop >= _CONVERSION_DROP_CRIT:
                anomalies.append(Anomaly(
                    anomaly_type="CONVERSION_DROP",
                    severity="CRITICAL",
                    description=f"Today's conversion {today_rate:.1%} is {drop:.0%} below 7-day avg {hist_rate:.1%}",
                    detected_at=now,
                ))
            elif drop >= _CONVERSION_DROP_WARN:
                anomalies.append(Anomaly(
                    anomaly_type="CONVERSION_DROP",
                    severity="WARN",
                    description=f"Today's conversion {today_rate:.1%} is {drop:.0%} below 7-day avg {hist_rate:.1%}",
                    detected_at=now,
                ))

        # --- Dead zones (no visits in last 30 min) ---
        cur = await db.execute(
            """SELECT zone_id, MAX(timestamp) AS last_visit
               FROM events
               WHERE store_id = ? AND is_staff = 0 AND zone_id IS NOT NULL
                 AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
               GROUP BY zone_id
               HAVING (julianday('now') - julianday(MAX(timestamp))) * 1440 > ?""",
            (store_id, _DEAD_ZONE_MINUTES),
        )
        dead_zones = await cur.fetchall()
        for zone in dead_zones:
            anomalies.append(Anomaly(
                anomaly_type="DEAD_ZONE",
                severity="INFO",
                description=f"Zone '{zone['zone_id']}' has had no visits for over {_DEAD_ZONE_MINUTES} minutes",
                zone_id=zone["zone_id"],
                detected_at=now,
            ))

    return AnomalyResponse(store_id=store_id, anomalies=anomalies, computed_at=now)


@router.get("/{store_id}/anomalies", response_model=AnomalyResponse)
async def anomalies_endpoint(store_id: str) -> AnomalyResponse:
    return await compute_anomalies(store_id)
