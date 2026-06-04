from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: Optional[int] = None
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)
    # Demographic attributes (populated when attribute model is available)
    gender_pred: Optional[Literal["M", "F"]] = None
    age_pred: Optional[int] = None
    age_bucket: Optional[str] = None
    is_face_hidden: bool = False
    group_id: Optional[str] = None
    group_size: Optional[int] = None


class IngestRequest(BaseModel):
    events: list[Event] = Field(max_length=500)


class IngestResponse(BaseModel):
    accepted: int
    duplicate: int
    rejected: int


class POSTransaction(BaseModel):
    transaction_id: str
    store_id: str
    timestamp: datetime
    basket_value_inr: float = Field(ge=0)


class POSIngestRequest(BaseModel):
    transactions: list[POSTransaction]


class POSIngestResponse(BaseModel):
    accepted: int
    duplicate: int


class ZoneDwell(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visits: int


class MetricsResponse(BaseModel):
    store_id: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_by_zone: list[ZoneDwell]
    queue_depth: int
    abandonment_rate: float
    computed_at: datetime


class FunnelStage(BaseModel):
    stage: str
    count: int
    pct_of_entry: float


class FunnelResponse(BaseModel):
    store_id: str
    stages: list[FunnelStage]
    computed_at: datetime


class ZoneHeatmap(BaseModel):
    zone_id: str
    score: float
    visits: int
    avg_dwell_ms: float
    data_confidence: bool


class HeatmapResponse(BaseModel):
    store_id: str
    zones: list[ZoneHeatmap]
    computed_at: datetime


class Anomaly(BaseModel):
    anomaly_type: Literal["QUEUE_SPIKE", "CONVERSION_DROP", "DEAD_ZONE"]
    severity: Literal["INFO", "WARN", "CRITICAL"]
    description: str
    zone_id: Optional[str] = None
    detected_at: datetime


class AnomalyResponse(BaseModel):
    store_id: str
    anomalies: list[Anomaly]
    computed_at: datetime


class StoreHealth(BaseModel):
    store_id: str
    status: Literal["OK", "STALE_FEED"]
    last_event_at: Optional[datetime] = None
    lag_minutes: Optional[float] = None


class HealthResponse(BaseModel):
    status: str
    stores: list[StoreHealth]
    checked_at: datetime
