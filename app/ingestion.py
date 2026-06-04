from fastapi import APIRouter

from app.database import get_connection
from app.logging_config import get_logger
from app.models import IngestRequest, IngestResponse, POSIngestRequest, POSIngestResponse

logger = get_logger(__name__)
router = APIRouter(tags=["ingest"])


@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(request: IngestRequest) -> IngestResponse:
    accepted = duplicate = rejected = 0

    async with get_connection() as db:
        for event in request.events:
            try:
                meta = event.metadata
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, store_id, camera_id, visitor_id, event_type, timestamp,
                        zone_id, dwell_ms, is_staff, confidence, queue_depth, sku_zone, session_seq,
                        gender_pred, age_pred, age_bucket, is_face_hidden, group_id, group_size)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.event_type.value,
                        event.timestamp.isoformat(),
                        event.zone_id,
                        event.dwell_ms,
                        1 if event.is_staff else 0,
                        event.confidence,
                        meta.queue_depth,
                        meta.sku_zone,
                        meta.session_seq,
                        event.gender_pred,
                        event.age_pred,
                        event.age_bucket,
                        1 if event.is_face_hidden else 0,
                        event.group_id,
                        event.group_size,
                    ),
                )
                if cursor.rowcount > 0:
                    accepted += 1
                else:
                    duplicate += 1
            except Exception as exc:
                logger.error("event_rejected", extra={"event_id": event.event_id, "error": str(exc)})
                rejected += 1

        await db.commit()

    logger.info("ingest_complete", extra={"accepted": accepted, "duplicate": duplicate, "rejected": rejected})
    return IngestResponse(accepted=accepted, duplicate=duplicate, rejected=rejected)


@router.post("/pos/ingest", response_model=POSIngestResponse)
async def ingest_pos(request: POSIngestRequest) -> POSIngestResponse:
    accepted = duplicate = 0

    async with get_connection() as db:
        for txn in request.transactions:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO pos_transactions
                   (transaction_id, store_id, timestamp, basket_value_inr)
                   VALUES (?, ?, ?, ?)""",
                (txn.transaction_id, txn.store_id, txn.timestamp.isoformat(), txn.basket_value_inr),
            )
            if cursor.rowcount > 0:
                accepted += 1
            else:
                duplicate += 1
        await db.commit()

    return POSIngestResponse(accepted=accepted, duplicate=duplicate)
