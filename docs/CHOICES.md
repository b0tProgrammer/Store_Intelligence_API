# Technical Choices

## 1. Detection Model: YOLOv8n

**What I chose**: YOLOv8n (nano) as the default detection model, with YOLOv8s available via flag for higher-accuracy runs.

**Why**: The primary constraint is inference latency. At 1080p / 15fps, we need ≤67ms per frame. YOLOv8n hits ~30ms on CPU (benchmarked at ~60ms on M1 CPU, ~12ms on GPU). YOLOv8s doubles that; RT-DETR triples it. Accuracy at nano-scale is sufficient for person detection (COCO person class mAP ~37 vs ~44 for small), and confidence thresholds compensate for borderline detections via the `confidence` field in the event schema.

**Alternatives considered**: RT-DETR (better accuracy, too slow for CPU), MediaPipe BlazePose (pose-focused, not detection), YOLOv9 (marginal accuracy gain, slower). For GPU-enabled inference, YOLOv8s or RT-DETR would be the right upgrade.

**Alternatives rejected**: RT-DETR for transformer-based accuracy advantages — disagreed for the CPU-latency constraint and documented the tradeoff.

---

## 2. Event Schema Design

**What I chose**: A flat-ish schema with a `metadata` sub-object for optional fields (queue_depth, sku_zone, session_seq).

**Why**: The core mandatory fields (`event_id`, `store_id`, `camera_id`, `visitor_id`, `event_type`, `timestamp`, `is_staff`, `confidence`) are needed for every query—visitor counts, staff exclusion, conversion funnel, and anomaly detection. Optional fields vary by event type: `zone_id` and `dwell_ms` for ZONE_* events; `queue_depth` for BILLING_QUEUE_* events. Keeping optionals in a `metadata` envelope avoids nullable columns at the top level while allowing the schema to evolve without migrations.

`event_id` as UUID v4 enables idempotent ingest: the pipeline can crash and re-emit without creating duplicate counts. `visitor_id` is a short hex token (`VIS_c8a2f1`) rather than a UUID to keep JSONL readable and match the problem statement's example format.

**What the schema enables**: ENTRY/REENTRY distinction supports re-entry detection. BILLING_QUEUE_JOIN + BILLING_QUEUE_ABANDON enables abandonment rate. ZONE_DWELL with `dwell_ms` enables heatmap scoring without session reconstruction.

---

## 3. API Computation: Real-Time SQL vs Materialized Views

**What I chose**: Real-time SQL aggregation on every request. No background jobs, no cached results.

**Why**: For a 48-hour challenge with one store's data (≤ ~50K events for a 20-minute clip at 15fps with sparse person detections), query latency is well within the <500ms target even with cold aggregations. A materialized view or background refresh job adds operational complexity (cache invalidation, stale-read risk, failure mode for the health endpoint's lag calculation) that isn't justified at this scale.

**The tradeoff**: At production scale (40 stores × continuous ingestion), this approach would not hold. The migration path is: add Redis for metric caching with 5-second TTL, or use PostgreSQL materialized views refreshed on ingest. That's documented in DESIGN.md but deferred here per YAGNI.

**Deferred**: A Redis caching layer was considered from the start but deferred — the acceptance gate requires correctness, not throughput, and adding Redis to docker-compose.yml increases the "git clone → docker compose up" surface area.
