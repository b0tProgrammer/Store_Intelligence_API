# User Manual — Store Intelligence System

Complete walkthrough from a fresh machine to a live analytics dashboard.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Project Structure](#2-project-structure)
3. [Install Dependencies](#3-install-dependencies)
4. [Start the API](#4-start-the-api)
5. [Verify the API is Running](#5-verify-the-api-is-running)
6. [Run the Detection Pipeline on CCTV Clips](#6-run-the-detection-pipeline-on-cctv-clips)
7. [Ingest Events into the API](#7-ingest-events-into-the-api)
8. [Load POS Transactions (Optional)](#8-load-pos-transactions-optional)
9. [View the Live Dashboard](#9-view-the-live-dashboard)
10. [Query the API Directly](#10-query-the-api-directly)
11. [Run the Test Suite](#11-run-the-test-suite)
12. [Reference: Event Types](#12-reference-event-types)
13. [Reference: API Endpoints](#13-reference-api-endpoints)
14. [Reference: Anomaly Severity Levels](#14-reference-anomaly-severity-levels)
15. [Reference: Conversion Rate Logic](#15-reference-conversion-rate-logic)
16. [Reference: Zone Layout](#16-reference-zone-layout)
17. [Reference: Environment Variables](#17-reference-environment-variables)

---

## 1. Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11 or 3.12 | `python --version` to check |
| pip | any recent | bundled with Python |
| Docker Desktop | any | only needed for the Docker path |
| ~2 GB disk space | — | for model weights + DB |

> You do **not** need a GPU. YOLOv8n runs on CPU (slower — ~15 min for 5 clips).

---

## 2. Project Structure

> **Database**: The system uses **SQLite** (WAL mode) as its embedded database, managed via `aiosqlite`. The database file is `store_intelligence.db` by default (configurable via the `DB_PATH` environment variable). No separate database server is required — SQLite runs in-process with the FastAPI app.
>
> **Why SQLite is the optimal choice here**:
> - **Zero-setup**: no separate server process, no credentials, no port conflicts — `docker compose up` just works.
> - **WAL mode**: Write-Ahead Logging allows concurrent reads alongside writes, which suits the mixed ingest + query workload.
> - **Single-store scope**: each store's data volume (~thousands of events/hour) is well within SQLite's throughput ceiling.
> - **Portability**: the entire database is one file — trivial to back up, copy, or inspect with standard tooling.
> - **Migration path**: if scale demands it (40+ stores, high-frequency ingest), swapping to PostgreSQL requires only changing `database.py` — the SQL queries are standard and portable.

```
C:\Purplle_Hackathon\
├── app/                         Backend (FastAPI + SQLite)
│   ├── main.py                  App entry point, middleware, SSE, dashboard
│   ├── models.py                Pydantic schemas for all events and responses
│   ├── database.py              aiosqlite connection, schema, WAL mode
│   ├── ingestion.py             POST /events/ingest, POST /pos/ingest
│   ├── metrics.py               GET /stores/{id}/metrics
│   ├── funnel.py                GET /stores/{id}/funnel
│   ├── heatmap.py               GET /stores/{id}/heatmap
│   ├── anomalies.py             GET /stores/{id}/anomalies
│   ├── health.py                GET /health
│   ├── logging_config.py        JSON structured logger
│   ├── templates/               Jinja2 HTML (base.html, dashboard.html)
│   └── static/                  CSS + JS for the live dashboard
├── pipeline/                    CCTV detection pipeline
│   ├── detect.py                CLI: video clip -> events JSONL
│   ├── tracker.py               Re-ID visitor tracking (cosine similarity)
│   └── emit.py                  Event schema helpers
├── tests/
│   ├── conftest.py              Per-test isolated SQLite DB + event factories
│   ├── test_ingestion.py
│   ├── test_metrics.py
│   └── test_anomalies.py
├── docs/
│   ├── DESIGN.md                Architecture + AI-Assisted Decisions section
│   └── CHOICES.md               3 key technical decisions
├── CCTV Footage/                Raw video clips (5 x MP4, 1920x1080)
├── pipeline_output/             Generated JSONL events (created after pipeline run)
├── store_layout.json            Zone definitions (normalised 0-1 coordinates)
├── run_pipeline.py              Helper: run all 5 clips and merge output
├── summarise.py                 Helper: print per-camera event stats
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pytest.ini
```

---

## 3. Install Dependencies

### Option A — Docker (recommended, no Python setup needed)

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and make sure it is running. Skip ahead to Step 4A.

### Option B — Local Python

```powershell
# From C:\Purplle_Hackathon\

# Core API dependencies
pip install -r requirements.txt

# Detection pipeline (YOLOv8 + OpenCV) — only needed to run the pipeline
pip install ultralytics opencv-python lap
```

`requirements.txt` includes: `fastapi`, `uvicorn`, `aiosqlite`, `pydantic`, `httpx`, `pytest`, `pytest-asyncio`, `pytest-cov`, `jinja2`, `python-multipart`.

---

## 4. Start the API

### Option A — Docker

```powershell
# Build image and start container (runs on port 8000)
docker compose up --build
```

- Data is persisted in a Docker volume (`db-data`).
- Stop with `Ctrl+C`, then `docker compose down`.
- To wipe the database and start fresh: `docker compose down -v`

### Option B — Local Python

```powershell
# From C:\Purplle_Hackathon\
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Keep this terminal open — the API must be running for Steps 7–10.

---

## 5. Verify the API is Running

Open a second terminal and run:

```powershell
# Health check
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json

# Expected response (empty store, no events yet):
# { "status": "OK", "stores": [], "checked_at": "..." }
```

Or open `http://localhost:8000/docs` in a browser to see the interactive Swagger UI.

---

## 6. Run the Detection Pipeline on CCTV Clips

This step processes the 5 video clips in `CCTV Footage/` through YOLOv8n + ByteTrack and writes structured events to `pipeline_output/`.

### Clip inventory

| File | Camera ID | FPS | Duration |
|------|-----------|-----|----------|
| CAM 1.mp4 | CAM_ENTRY_01 | 30 | 2m 20s |
| CAM 2.mp4 | CAM_ENTRY_02 | 30 | 2m 06s |
| CAM 3.mp4 | CAM_MAIN_01  | 30 | 2m 28s |
| CAM 4.mp4 | CAM_MAIN_02  | 25 | 2m 26s |
| CAM 5.mp4 | CAM_BILLING_01 | 25 | 2m 19s |

### Run all 5 clips at once

```powershell
# From C:\Purplle_Hackathon\
python run_pipeline.py
```

**Expected runtime**: 15–20 minutes on CPU. On a GPU: ~2–3 minutes.

When it finishes you will see:

```
Total events across all clips: 748
Merged output: pipeline_output\all_events.jsonl
```

Output files written:

```
pipeline_output/
├── CAM_ENTRY_01.jsonl     208 events
├── CAM_ENTRY_02.jsonl     408 events
├── CAM_MAIN_01.jsonl      216 events
├── CAM_MAIN_02.jsonl        5 events  (sparse — overhead camera angle)
├── CAM_BILLING_01.jsonl   124 events
└── all_events.jsonl       961 events total  (~330 KB)
```

### Run a single clip manually

```powershell
python -m pipeline.detect `
  --store STORE_BLR_002 `
  --camera CAM_ENTRY_01 `
  --clip "CCTV Footage\CAM 1.mp4" `
  --layout store_layout.json `
  --output pipeline_output\CAM_ENTRY_01.jsonl `
  --conf 0.40 `
  --start-time 2026-05-31T10:00:00+05:30
```

| Flag | Default | Description |
|------|---------|-------------|
| `--store` | required | Store ID written into every event |
| `--camera` | `CAM_01` | Camera ID written into every event |
| `--clip` | required | Path to the video file |
| `--layout` | `store_layout.json` | Zone definitions file |
| `--output` | `events.jsonl` | Where to write the JSONL output |
| `--conf` | `0.40` | YOLOv8 confidence threshold (lower = more detections, more noise) |
| `--start-time` | now (UTC) | ISO 8601 datetime for the first frame's timestamp |

> **CAM 4 note**: This clip has very few detections at conf=0.40 (overhead/side-angle camera). Use `--conf 0.15` for it.

### What the pipeline emits

For each person the pipeline detects, it emits a state-machine sequence of events:

```
ENTRY  ->  ZONE_ENTER  ->  ZONE_DWELL  ->  ZONE_EXIT  ->  EXIT
                                   (if zone is BILLING)
                               BILLING_QUEUE_JOIN
```

- Events are timestamped from the video frame index + `--start-time`, not wall clock.
- Every 3rd frame is processed (~10 fps effective), balancing speed vs accuracy.
- A track that disappears for 8+ seconds triggers an EXIT event.

---

## 7. Ingest Events into the API

The API must be running (Step 4) before ingesting.

### Ingest all events from the pipeline run

```powershell
# From C:\Purplle_Hackathon\

$lines = Get-Content pipeline_output\all_events.jsonl
$events = $lines | ForEach-Object { $_ | ConvertFrom-Json }

# The API accepts up to 500 events per request — batch accordingly
$batchSize = 500
for ($i = 0; $i -lt $events.Count; $i += $batchSize) {
    $batch = $events[$i..([Math]::Min($i + $batchSize - 1, $events.Count - 1))]
    $body = @{ events = $batch } | ConvertTo-Json -Depth 10
    $result = Invoke-RestMethod -Uri "http://localhost:8000/events/ingest" `
        -Method POST -ContentType "application/json" -Body $body
    Write-Host "Batch $([Math]::Floor($i/$batchSize)+1): accepted=$($result.accepted) duplicate=$($result.duplicate)"
}
```

Expected output (first-time ingest):

```
Batch 1: accepted=500 duplicate=0
Batch 2: accepted=461 duplicate=0
```

Ingestion is **idempotent** — re-running the same events produces `duplicate` counts and never creates duplicates in the database.

### Ingest a single JSONL file (Linux/Mac)

```bash
python -c "
import json
lines = [json.loads(l) for l in open('pipeline_output/all_events.jsonl') if l.strip()]
# batch into 500
for i in range(0, len(lines), 500):
    batch = lines[i:i+500]
    import urllib.request
    body = json.dumps({'events': batch}).encode()
    req = urllib.request.Request('http://localhost:8000/events/ingest',
        data=body, headers={'Content-Type': 'application/json'}, method='POST')
    resp = urllib.request.urlopen(req).read()
    print(resp.decode())
"
```

### Quick single-event test

```powershell
$body = @{
    events = @(@{
        event_id  = "test-001"
        store_id  = "STORE_BLR_002"
        camera_id = "CAM_ENTRY_01"
        visitor_id = "VIS_test"
        event_type = "ENTRY"
        timestamp  = "2026-05-31T10:00:00Z"
        is_staff   = $false
        confidence = 0.95
        metadata   = @{}
    })
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://localhost:8000/events/ingest" `
    -Method POST -ContentType "application/json" -Body $body
# -> { "accepted": 1, "duplicate": 0, "rejected": 0 }
```

---

## 8. Load POS Transactions (Optional)

POS data is needed to calculate the **conversion rate** (visitors who purchased / total visitors). Without it, `conversion_rate` will always be `0.0`.

```powershell
# If you have a CSV with columns: transaction_id, store_id, timestamp, basket_value_inr
$rows = Import-Csv pos_transactions.csv
$txns = $rows | ForEach-Object {
    @{
        transaction_id   = $_.transaction_id
        store_id         = $_.store_id
        timestamp        = $_.timestamp
        basket_value_inr = [double]$_.basket_value_inr
    }
}
$body = @{ transactions = $txns } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "http://localhost:8000/pos/ingest" `
    -Method POST -ContentType "application/json" -Body $body
```

A visitor counts as a **purchase** when a POS transaction for the same store occurs within 5 minutes of their `BILLING_QUEUE_JOIN` event.

---

## 9. View the Live Dashboard

Open in a browser:

```
http://localhost:8000/dashboard/STORE_BLR_002
```

The dashboard refreshes every **3 seconds** via Server-Sent Events (SSE) and shows:

- **Visitor count** — unique non-staff visitors for the day
- **Conversion rate** — visitors who purchased / total visitors
- **Queue depth** — current number of people in the billing queue
- **Abandonment rate** — visitors who joined the queue but left without purchasing
- **Conversion funnel** — 4-stage bar chart: Entry -> Zone Visit -> Billing Queue -> Purchase
- **Zone heatmap** — 0–100 popularity score per zone + average dwell time
- **Active anomalies** — queue spikes, conversion drops, dead zones

Connection status badge (top right): **Live** = connected, **Disconnected** = auto-retrying every 5s.

To view a different store: `http://localhost:8000/dashboard/STORE_BLR_001`

---

## 10. Query the API Directly

### Metrics

```powershell
# Today's KPIs
Invoke-RestMethod http://localhost:8000/stores/STORE_BLR_002/metrics | ConvertTo-Json -Depth 5

# Filter by a specific date
Invoke-RestMethod "http://localhost:8000/stores/STORE_BLR_002/metrics?date=2026-05-31" | ConvertTo-Json -Depth 5
```

Sample response:

```json
{
  "store_id": "STORE_BLR_002",
  "unique_visitors": 120,
  "conversion_rate": 0.0,
  "avg_dwell_by_zone": [
    { "zone_id": "FRAGRANCE", "avg_dwell_ms": 4200, "visits": 70 }
  ],
  "queue_depth": 0,
  "abandonment_rate": 0.0,
  "computed_at": "2026-05-31T..."
}
```

### Funnel

```powershell
Invoke-RestMethod http://localhost:8000/stores/STORE_BLR_002/funnel | ConvertTo-Json -Depth 5
```

### Heatmap

```powershell
Invoke-RestMethod http://localhost:8000/stores/STORE_BLR_002/heatmap | ConvertTo-Json -Depth 5
```

### Anomalies

```powershell
Invoke-RestMethod http://localhost:8000/stores/STORE_BLR_002/anomalies | ConvertTo-Json -Depth 5
```

### Health

```powershell
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json
```

Returns `STALE_FEED` for any store whose last event is more than 10 minutes old.

### Interactive docs

`http://localhost:8000/docs` — Swagger UI, try every endpoint in the browser.

---

## 11. Run the Test Suite

```powershell
# All tests with coverage report (from C:\Purplle_Hackathon\)
pytest

# Verbose output
pytest -v

# Single file
pytest tests/test_metrics.py -v

# Single test by name
pytest -k "test_metrics_empty_store" -v

# Skip coverage (faster)
pytest --no-cov

# HTML coverage report -> opens htmlcov/index.html
pytest --cov=app --cov-report=html
```

### Last run results (2026-05-31)

**19 / 19 tests passed — 82.81% coverage** (threshold: 70%)

| Test | Result |
|------|--------|
| `test_anomalies::test_no_anomalies_on_empty_store` | PASSED |
| `test_anomalies::test_dead_zone_detected_after_silence` | PASSED |
| `test_anomalies::test_no_dead_zone_when_recent_activity` | PASSED |
| `test_anomalies::test_health_ok_with_recent_events` | PASSED |
| `test_anomalies::test_health_stale_feed` | PASSED |
| `test_anomalies::test_funnel_all_zeros_empty_store` | PASSED |
| `test_anomalies::test_funnel_stages_monotone` | PASSED |
| `test_ingestion::test_ingest_happy_path` | PASSED |
| `test_ingestion::test_ingest_idempotency` | PASSED |
| `test_ingestion::test_ingest_staff_events_accepted` | PASSED |
| `test_ingestion::test_ingest_max_batch` | PASSED |
| `test_ingestion::test_ingest_over_max_batch_rejected` | PASSED |
| `test_ingestion::test_ingest_mixed_event_types` | PASSED |
| `test_metrics::test_metrics_empty_store_returns_zeros` | PASSED |
| `test_metrics::test_metrics_staff_excluded_from_visitors` | PASSED |
| `test_metrics::test_metrics_zero_purchases_without_pos` | PASSED |
| `test_metrics::test_metrics_abandonment_rate` | PASSED |
| `test_metrics::test_metrics_queue_depth` | PASSED |
| `test_metrics::test_metrics_dwell_by_zone` | PASSED |

Each test runs against an isolated in-memory SQLite DB — no cleanup needed between runs.

---

## 12. Reference: Event Types

| Event Type | Fired when |
|------------|-----------|
| `ENTRY` | Person first detected crossing into the store |
| `EXIT` | Person not seen for 8+ seconds (end of track) |
| `REENTRY` | Same visitor reappears after an EXIT (Re-ID match) |
| `ZONE_ENTER` | Person's bbox centre moves into a named zone |
| `ZONE_EXIT` | Person's bbox centre leaves a named zone |
| `ZONE_DWELL` | Person stays in a zone for 1.5+ seconds (includes `dwell_ms`) |
| `BILLING_QUEUE_JOIN` | Person enters a zone listed under `billing_zones` in the layout |
| `BILLING_QUEUE_ABANDON` | Person leaves billing zone before a matching POS transaction |

Set `"is_staff": true` on any event to exclude that person from all customer-facing metrics.

---

## 13. Reference: API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/events/ingest` | Ingest up to 500 detection events. Idempotent by `event_id`. |
| `POST` | `/pos/ingest` | Ingest POS transactions for conversion rate calculation. |
| `GET` | `/stores/{id}/metrics` | Live KPIs: visitors, conversion, dwell by zone, queue depth, abandonment. |
| `GET` | `/stores/{id}/funnel` | 4-stage funnel: Entry -> Zone -> Billing Queue -> Purchase. |
| `GET` | `/stores/{id}/heatmap` | Zone popularity score (0–100) + avg dwell. Flags low-confidence zones (<20 sessions). |
| `GET` | `/stores/{id}/anomalies` | Active alerts: queue spike, conversion drop, dead zone (30+ min silence). |
| `GET` | `/health` | Per-store feed lag. Flags `STALE_FEED` if last event >10 min ago. |
| `GET` | `/dashboard/{id}` | Live browser dashboard for a store (SSE-driven, refreshes every 3s). |
| `GET` | `/sse/metrics/{id}` | Raw SSE stream consumed by the dashboard. |

All `GET /stores/...` endpoints accept an optional `?date=YYYY-MM-DD` query parameter.

---

## 14. Reference: Anomaly Severity Levels

| Severity | Trigger |
|----------|---------|
| `INFO` | Dead zone — no visits to a zone for 30+ consecutive minutes during open hours |
| `WARN` | Queue depth 2x the 7-day hourly average, OR conversion rate 20%+ below 7-day average |
| `CRITICAL` | Queue depth 3x the 7-day hourly average, OR conversion rate 50%+ below 7-day average |

---

## 15. Reference: Conversion Rate Logic

**Formula**: unique visitors with a purchase / total unique non-staff visitors

**Purchase definition**: A visitor has a `BILLING_QUEUE_JOIN` event AND a POS transaction for the same store occurs within **5 minutes** after that event. Multiple transactions in the same 5-minute window count as one conversion per visitor.

**Without POS data**: `conversion_rate` is always `0.0`. Load POS transactions via `POST /pos/ingest` to unlock this metric.

**From the pipeline run on the CCTV clips**:

| Metric | Value |
|--------|-------|
| Total unique visitors | 120 |
| Billing queue joins | 28 |
| Approximate conversion (billing/entry) | 23.3% |
| Most visited zone | FRAGRANCE (70 zone entries) |
| Zone with longest dwell | FRAGRANCE |

---

## 16. Reference: Zone Layout

Zones are defined in `store_layout.json` as axis-aligned rectangles with **normalised coordinates** (0.0 = left/top edge, 1.0 = right/bottom edge of frame). They scale automatically to any resolution.

```json
{
  "zones": [
    { "name": "ENTRY",     "x1": 0.0,  "y1": 0.75, "x2": 1.0,  "y2": 1.0  },
    { "name": "SKINCARE",  "x1": 0.0,  "y1": 0.45, "x2": 0.33, "y2": 0.75 },
    { "name": "MAKEUP",    "x1": 0.33, "y1": 0.45, "x2": 0.67, "y2": 0.75 },
    { "name": "FRAGRANCE", "x1": 0.67, "y1": 0.45, "x2": 1.0,  "y2": 0.75 },
    { "name": "HAIRCARE",  "x1": 0.0,  "y1": 0.2,  "x2": 0.5,  "y2": 0.45 },
    { "name": "BILLING",   "x1": 0.5,  "y1": 0.2,  "x2": 1.0,  "y2": 0.45 }
  ],
  "billing_zones": ["BILLING"]
}
```

To calibrate zones against your actual store layout:
1. Take a screenshot of any frame from the clip: `python -c "import cv2; cap=cv2.VideoCapture('CCTV Footage/CAM 1.mp4'); cap.set(1,100); _, f=cap.read(); cv2.imwrite('frame.jpg', f)"`
2. Open `frame.jpg` and note the pixel coordinates of each product area.
3. Divide x-coords by frame width (1920) and y-coords by frame height (1080) to get normalised values.
4. Update `store_layout.json` accordingly.

> **CAM 4 caveat**: This camera appears to be overhead or at a steep side angle — YOLOv8n detects persons at very low confidence (~0.14). If re-running this clip, use `--conf 0.15`. For production, consider a model trained on top-down views (e.g., `yolov8s` or a fisheye-specialised checkpoint).

---

## 17. Reference: Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `store_intelligence.db` | Path to the SQLite database file. |

In Docker, `DB_PATH` is set to `/app/data/store_intelligence.db` (persisted in the `db-data` volume).

To use a custom path locally:

```powershell
$env:DB_PATH = "C:\data\my_store.db"
uvicorn app.main:app --reload --port 8000
```
