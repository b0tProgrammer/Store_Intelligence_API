# System Design

## Architecture Overview

```
CCTV Clips (MP4)
    │
    ▼
pipeline/detect.py          YOLOv8n detection, ByteTrack frame-level tracking,
                            VisitorTracker Re-ID, zone classification
    │  JSONL events
    ▼
POST /events/ingest         Validates Pydantic schema, inserts with INSERT OR IGNORE
                            (idempotency by event_id)
    │
    ▼
SQLite (WAL mode)           Two tables: events, pos_transactions
                            Indexed on (store_id, timestamp), visitor_id, event_type
    │
    ▼
FastAPI endpoints            Real-time SQL aggregation per request (not cached)
    │
    ├── /stores/{id}/metrics
    ├── /stores/{id}/funnel
    ├── /stores/{id}/heatmap
    ├── /stores/{id}/anomalies
    └── /health
    │
    ▼
/sse/metrics/{id}           Server-Sent Events loop, pushes metrics + funnel +
                            anomalies every 3 seconds
    │
    ▼
/dashboard                  Jinja2 template, Chart.js funnel bar chart, zone
                            heatmap bars, live KPI cards updated via SSE
```

## Key Design Decisions

### Event ingestion is append-only and idempotent

Events are written with `INSERT OR IGNORE` keyed on `event_id` (UUID). Re-submitting the same batch is safe; the API returns `duplicate` count so the caller can audit. This is critical for pipeline re-runs and crash recovery.

### Real-time computation over materialized views

All metric endpoints run SQL aggregations on the raw events table per request. This avoids stale materialized views and keeps the system simple enough to reason about during the 48-hour window. Acceptable given target latency (<500ms for /metrics at 1000-event scale).

### Conversion via POS time-window correlation

No customer identity links CCTV visitors to POS receipts. The correlation heuristic: if a visitor_id has a `BILLING_QUEUE_JOIN` event, and a POS transaction for the same store occurs within 5 minutes, that visitor is counted as a conversion. Multiple transactions in the same window count as one conversion per visitor.

## Staff Detection — Known Limitation

Accurately classifying staff vs customers is a hard open problem. We evaluated three approaches:

### Approaches Considered

**1. Uniform colour matching**
Extract HSV colour histogram from the upper-body crop and compare to a pre-configured staff uniform colour range. Simple to implement, zero extra model weight. Fails when a customer wears clothing of the same colour as the uniform — a realistic scenario in a cosmetics retail setting where pastel/neutral tones are common.

**2. Zone-based heuristic**
Anyone detected exclusively in Back-of-House (BOH) zones is staff. Already implemented via `staff_zones` in `store2_layout.json`. Fails because staff also walk the customer floor constantly.

**3. Duration heuristic**
Staff are present for the entire clip; customers come and go. A visitor whose events span >80% of the clip duration is likely staff. Fails for long-browsing customers or part-shift staff.

### What We Implemented
A best-effort combination of zone heuristic (BOH detection) + duration threshold, which handles the majority of cases in fixed-camera retail footage.

### Production-Grade Solution
The only reliable approaches are:
- **Pre-registration**: Staff biometrics (face embedding or appearance vector) are enrolled before each shift. The tracker matches live detections against the staff registry at entry.
- **Badge/lanyard detection**: A small object detector trained on staff badge crops. Distinct enough visually even at CCTV resolution.
- **Entry time window**: Staff arrive before store opening; any detection before opening hours = staff.

These require per-store configuration and labelled data, which were not available within the challenge window. The current implementation marks `is_staff=false` by default and sets it `true` only when a visitor is detected exclusively in defined staff zones.

## AI-Assisted Decisions

### 1. Detection model selection

Compared YOLOv8n, YOLOv8s, and RT-DETR for this use case (1080p, 15fps retail clips, edge deployment). Used an AI assistant to evaluate the latency vs accuracy tradeoff. YOLOv8n was chosen for the 30ms/frame target — RT-DETR would exceed the latency budget on CPU. YOLOv8s is available as an accuracy-focused alternative via the `--conf` flag.

### 2. SSE vs WebSocket for live dashboard

Used an AI assistant to evaluate whether the live dashboard should use SSE or WebSocket for 3-second metric pushes. SSE was recommended and chosen for its simplicity — no handshake required, browser reconnects natively, works behind standard reverse proxies. WebSocket is bidirectional and overkill for a read-only dashboard.

### 3. SQLite WAL mode for concurrent writes

Consulted an AI assistant about concurrent write contention between the ingest endpoint and read queries. WAL (Write-Ahead Log) mode was recommended as it allows concurrent readers during writes. Applied in `get_connection()`. The tradeoff (slightly larger disk footprint, checkpoint overhead) is acceptable at this scale.
