"""Helpers for validating and emitting events as JSONL."""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional, TextIO

from app.models import Event, EventMetadata, EventType


def make_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: EventType,
    timestamp: Optional[datetime] = None,
    zone_id: Optional[str] = None,
    dwell_ms: Optional[int] = None,
    is_staff: bool = False,
    confidence: float = 1.0,
    queue_depth: Optional[int] = None,
    sku_zone: Optional[str] = None,
    session_seq: Optional[int] = None,
    gender_pred: Optional[str] = None,
    age_pred: Optional[int] = None,
    age_bucket: Optional[str] = None,
    is_face_hidden: bool = False,
    group_id: Optional[str] = None,
    group_size: Optional[int] = None,
) -> Event:
    return Event(
        event_id=str(uuid.uuid4()),
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=timestamp or datetime.now(timezone.utc),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=confidence,
        metadata=EventMetadata(queue_depth=queue_depth, sku_zone=sku_zone, session_seq=session_seq),
        gender_pred=gender_pred,
        age_pred=age_pred,
        age_bucket=age_bucket,
        is_face_hidden=is_face_hidden,
        group_id=group_id,
        group_size=group_size,
    )


def emit_jsonl(events: list[Event], out: TextIO = sys.stdout) -> None:
    for event in events:
        out.write(event.model_dump_json() + "\n")
    out.flush()


def load_jsonl(path: str) -> list[Event]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(Event.model_validate_json(line))
    return events
