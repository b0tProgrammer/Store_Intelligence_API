# Store Intelligence API — Full Explanation

## What This Project Does

This is a retail store analytics system built for the Purplle Engineering Hiring Challenge (Round 2). It takes raw CCTV footage from a physical store and turns it into a live analytics API that answers one core question: **how many people who walked into the store actually bought something?**

The system works in two phases:
1. **Detection Pipeline** — runs YOLOv8 AI on each video clip to detect and track every person, figure out which store zone they visited, and write structured events to a file.
2. **Intelligence API** — a FastAPI web server that accepts those events, stores them in a SQLite database, and exposes analytics endpoints (metrics, funnel, heatmap, anomalies, health).

There is also a live dashboard at `/dashboard` that auto-refreshes every 3 seconds using Server-Sent Events (SSE).

---

## How to Run the Project

### Option A — Docker (Recommended, One Command)

**Prerequisite:** Docker Desktop installed and running.

```bash
docker compose up
```

This builds the image, starts the API on port 8000, and creates a persistent database volume. No manual steps needed.

- API docs: http://localhost:8000/docs
- Live dashboard: http://localhost:8000/dashboard
- Health check: http://localhost:8000/health

---

### Option B — Run Locally (Without Docker)

**Step 1: Install Python dependencies**
```bash
pip install -r requirements.txt
```

**Step 2: Start the API server**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is now live at http://localhost:8000

---

### Step 3: Run the Detection Pipeline on CCTV Clips

This step processes the actual video files and generates events. Requires the `ultralytics` and `opencv-python` packages (not in the base requirements, install separately):

```bash
pip install ultralytics opencv-python
```

Then run the pipeline on all 5 cameras at once:
```bash
python run_pipeline.py
```

Or run a single clip manually:
```bash
python -m pipeline.detect \
  --store STORE_BLR_002 \
  --camera CAM_ENTRY_01 \
  --clip "CCTV Footage/CAM 1.mp4" \
  --layout store_layout.json \
  --output pipeline_output/CAM_ENTRY_01.jsonl \
  --start-time 2026-05-31T10:00:00+05:30
```

This writes a `.jsonl` file of events per camera into `pipeline_output/`.

---

### Step 4: Ingest Events into the API

After the pipeline runs, push the events into the running API:
```bash
python -c "
import json, urllib.request
events = [json.loads(l) for l in open('pipeline_output/all_events.jsonl') if l.strip()]
batches = [events[i:i+500] for i in range(0, len(events), 500)]
for batch in batches:
    body = json.dumps({'events': batch}).encode()
    req = urllib.request.Request('http://localhost:8000/events/ingest', data=body, headers={'Content-Type':'application/json'}, method='POST')
    print(urllib.request.urlopen(req).read().decode())
"
```

---

### Step 5: Check the Results

```bash
# Store metrics (visitors, conversion rate, queue depth)
curl http://localhost:8000/stores/STORE_BLR_002/metrics

# Conversion funnel (Entry → Zone Visit → Billing → Purchase)
curl http://localhost:8000/stores/STORE_BLR_002/funnel

# Zone heatmap (which areas get the most attention)
curl http://localhost:8000/stores/STORE_BLR_002/heatmap

# Active anomalies (queue spikes, conversion drops, dead zones)
curl http://localhost:8000/stores/STORE_BLR_002/anomalies

# Health of all stores (stale feed detection)
curl http://localhost:8000/health
```

---

### Running Tests

```bash
pytest tests/ -v
pytest tests/ --cov=app --cov-report=term-missing
```

---

## File-by-File Explanation

### Root Files

#### `run_pipeline.py`
The master script that processes all 5 CCTV clips in sequence. It knows which `.mp4` file maps to which camera ID and what time the recording started. For each clip it calls `pipeline.detect` as a subprocess, collects the per-camera `.jsonl` output files, and merges them all into one `pipeline_output/all_events.jsonl`. Run this once after installing `ultralytics` and `opencv-python`.

#### `summarise.py`
A quick diagnostic script. Reads all the per-camera `.jsonl` output files after the pipeline has run, counts events by type, lists which zones were visited and how often, and prints an approximate conversion rate (billing joins ÷ entries). Useful for sanity-checking pipeline output before ingesting into the API.

#### `store_layout.json`
Defines where each store zone sits inside the video frame, using normalised 0.0–1.0 coordinates relative to frame width/height. The six zones are: ENTRY (bottom strip), SKINCARE (left third, mid), MAKEUP (centre, mid), FRAGRANCE (right third, mid), HAIRCARE (upper left), and BILLING (upper right). The pipeline uses this file to decide which zone a detected person is standing in.

#### `requirements.txt`
Python package list for the API and tests: FastAPI, Uvicorn, aiosqlite (async SQLite), Pydantic v2, Jinja2, httpx, pytest, pytest-asyncio, pytest-cov, and ruff (linter). Note: `ultralytics` and `opencv-python` are NOT in this list because they are only needed for the detection pipeline, not for the API server itself.

#### `Dockerfile`
Builds a minimal Python 3.11 Docker image. Copies `requirements.txt` first (so pip install is cached on rebuilds), then copies only the `app/` and `pipeline/` directories. Starts the server with Uvicorn on port 8000.

#### `docker-compose.yml`
Defines the single `api` service. Maps container port 8000 to host port 8000. Uses a named Docker volume `db-data` mounted at `/app/data` inside the container so the SQLite database survives container restarts. Sets `DB_PATH=/app/data/store_intelligence.db` via environment variable. Includes a health check that polls `/health` every 30 seconds.

#### `yolov8n.pt`
The pre-trained YOLOv8-nano model weights file (downloaded by `ultralytics` on first use). Used by the detection pipeline for person detection. The "n" (nano) variant runs at ~30ms per frame on CPU — the smallest and fastest variant, which is why it was chosen for a real-time pipeline.

#### `pytest.ini`
Configures pytest to run in asyncio mode (`asyncio_mode = auto`) so all `async def` test functions work without decorators, and sets the default test discovery path to `tests/`.

---

### `app/` — The API Server

#### `app/main.py`
The FastAPI application entry point. Does four things:
1. On startup, calls `init_db()` to create tables if they don't exist.
2. Mounts all the route routers (ingest, metrics, funnel, heatmap, anomalies, health).
3. Runs an HTTP middleware on every request that generates a `trace_id`, measures latency, and logs a structured JSON line.
4. Exposes `/dashboard` (HTML page) and `/sse/metrics/{store_id}` (a Server-Sent Events stream that pushes metrics + funnel + anomalies every 3 seconds to the browser).

#### `app/models.py`
All Pydantic data models for the project. Defines: `EventType` enum (ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY), the `Event` schema (the core unit flowing through the whole system), `IngestRequest/Response`, `POSTransaction`, `MetricsResponse`, `FunnelResponse`, `HeatmapResponse`, `AnomalyResponse`, and `HealthResponse`. Every API request and response is validated against these models.

#### `app/database.py`
Manages the SQLite database. Defines the SQL schema for two tables:
- `events` — stores every visitor event with all fields indexed for fast querying by store+time, visitor_id, and store+event_type.
- `pos_transactions` — stores Point-of-Sale transaction records used for purchase correlation.

Uses `aiosqlite` for fully async, non-blocking database access. Also enables WAL (Write-Ahead Logging) mode for better concurrent read/write performance. The `DB_PATH` is configurable via environment variable, which is what the Docker setup uses to point to the persistent volume.

#### `app/ingestion.py`
Handles `POST /events/ingest` (up to 500 events per batch) and `POST /pos/ingest`. For events, uses `INSERT OR IGNORE` with `event_id` as primary key — so re-submitting the same event is a no-op (idempotent). Counts accepted vs duplicate vs rejected and logs the summary. The POS ingest endpoint is the same pattern.

#### `app/metrics.py`
Handles `GET /stores/{id}/metrics`. Runs four separate SQL queries to compute: unique visitor count (ENTRY events, excluding staff), conversion rate (visitors who joined billing queue within 5 minutes of a POS transaction), average dwell time per zone (from ZONE_DWELL events), current queue depth (how many visitors are currently in the billing queue based on their last known event), and queue abandonment rate. All staff events (`is_staff=1`) are excluded from every calculation. Accepts an optional `?date=YYYY-MM-DD` parameter to filter by day.

#### `app/funnel.py`
Handles `GET /stores/{id}/funnel`. Returns the visitor count at each stage of the purchase journey: Entry → Zone Visit → Billing Queue → Purchase. Each stage shows the absolute count and the percentage relative to total entries. This tells you where in the shopping journey visitors are dropping off.

#### `app/heatmap.py`
Handles `GET /stores/{id}/heatmap`. For each zone, computes a score = (number of visits × average dwell time), then normalises all scores to a 0–100 scale so the hottest zone is always 100. Also sets a `data_confidence` flag to `false` on zones with fewer than 20 unique visitor sessions, signalling that the score may not be statistically reliable.

#### `app/anomalies.py`
Handles `GET /stores/{id}/anomalies`. Detects three types of problems:
- **QUEUE_SPIKE** — current billing queue depth is 2× (WARN) or 3× (CRITICAL) the 7-day hourly average.
- **CONVERSION_DROP** — today's conversion rate is 20% (WARN) or 50% (CRITICAL) below the 7-day rolling average.
- **DEAD_ZONE** — a zone that has had zero visitor activity for 30+ consecutive minutes during what should be open hours.

Each anomaly has a severity level (INFO / WARN / CRITICAL) and a human-readable description.

#### `app/health.py`
Handles `GET /health`. Checks every store in the database and reports whether the event feed is fresh. If the most recent event for a store is more than 10 minutes old, the store is marked `STALE_FEED` — meaning the CCTV pipeline may have stalled. The overall API status is `OK` if all stores are fresh, otherwise `DEGRADED`.

#### `app/logging_config.py`
Provides a `get_logger()` factory that configures Python's standard `logging` module to emit structured JSON instead of plain text. Every log line is a JSON object with `ts`, `level`, `logger`, `msg`, and any extra fields passed in the `extra={}` dict. This makes logs parseable by tools like Datadog, Loki, or Splunk without extra configuration.

---

### `app/templates/` — Dashboard HTML

#### `app/templates/base.html`
The HTML shell: sets up the `<head>` with CSS and Chart.js CDN links, defines named Jinja2 blocks (`title`, `content`, `scripts`) that child templates fill in.

#### `app/templates/dashboard.html`
The live analytics dashboard. Shows four KPI cards (unique visitors, conversion rate, queue depth, abandonment rate), a conversion funnel bar chart powered by Chart.js, a zone heatmap grid, and an anomaly alert panel. All data comes from the `/sse/metrics/{store_id}` Server-Sent Events stream — the page updates itself every 3 seconds without refreshing.

---

### `app/static/` — Frontend Assets

#### `app/static/dashboard.css`
All the CSS for the dashboard: dark-ish card layout, KPI grid, heatmap colour blocks (green→yellow→red based on score), anomaly alert styling with WARN/CRITICAL colour coding.

#### `app/static/dashboard.js`
The browser-side JavaScript. Opens the SSE connection to `/sse/metrics/{store_id}`, receives JSON payloads every 3 seconds, and updates the DOM: overwrites the KPI card values, re-renders the Chart.js funnel bar chart, rebuilds the heatmap grid with colour-coded zone tiles, and renders anomaly alert badges.

---

### `pipeline/` — Detection Pipeline

#### `pipeline/detect.py`
The main detection engine. Takes a single video clip and runs it through YOLOv8 (person class only) with ByteTrack multi-object tracking. Samples every 3rd frame (30fps → effective 10fps) for speed. For each tracked person it:
1. Emits an `ENTRY` (or `REENTRY`) event when first seen.
2. Checks which zone the person's bounding-box centre is in using `store_layout.json`.
3. On zone change: emits `ZONE_EXIT` + optionally `ZONE_DWELL` (if they stayed ≥1500ms) for the old zone, then `ZONE_ENTER` for the new one.
4. Emits `BILLING_QUEUE_JOIN` the first time a person enters a billing-named zone.
5. Emits `EXIT` when a track disappears for more than 8 seconds.

Also flushes any still-active tracks at end-of-clip. Writes events to a `.jsonl` output file.

#### `pipeline/tracker.py`
The `VisitorTracker` class — an in-memory registry of all visitors seen in one store session. When a new detection comes in, it tries to match it to an existing track using cosine similarity on appearance embeddings. If similarity ≥ 0.75, it's the same person; if that person had previously exited, the event becomes a `REENTRY`. If no match, a new `visitor_id` is created (`VIS_<6hex>`). Also handles cross-camera deduplication: two cameras can share the same `VisitorTracker` instance, so the same physical person gets one `visitor_id` regardless of which camera sees them.

#### `pipeline/emit.py`
Utility functions for the pipeline: `make_event()` builds a fully-validated `Event` Pydantic object with a fresh UUID, `emit_jsonl()` serialises a list of events to a JSONL stream (one JSON object per line), and `load_jsonl()` reads a `.jsonl` file back into a list of `Event` objects. The Pydantic validation in `make_event()` is the quality gate — malformed events are caught here before they reach the API.

---

### `pipeline_output/` — Generated Data

These `.jsonl` files are produced by running the detection pipeline. Each line is one JSON event object. The files are:
- `CAM_ENTRY_01.jsonl`, `CAM_ENTRY_02.jsonl` — events from the two entry cameras
- `CAM_MAIN_01.jsonl`, `CAM_MAIN_02.jsonl` — events from the two main floor cameras
- `CAM_BILLING_01.jsonl` — events from the billing area camera
- `all_events.jsonl` — merged file containing events from all cameras combined

These files are the handoff point between the pipeline (which needs GPU/video) and the API (which just needs the events).

---

### `tests/` — Test Suite

#### `tests/conftest.py`
Sets up the pytest fixtures used by all tests. Creates an in-memory SQLite database for each test (so tests never touch the real database), initialises the schema, and provides an async `httpx` test client pointed at the FastAPI app.

#### `tests/test_ingestion.py`
Tests for `POST /events/ingest`: valid batch accepted, duplicate `event_id` counted as duplicate not accepted again, batch over 500 events rejected, staff events stored but excluded from counts.

#### `tests/test_metrics.py`
Tests for `GET /stores/{id}/metrics`: empty store returns zeroes (not null/error), all-staff store returns zero visitors, conversion rate calculation, zone dwell aggregation.

#### `tests/test_anomalies.py`
Tests for `GET /stores/{id}/anomalies`: no anomalies when store is healthy, dead zone detected after 30 minutes of inactivity, queue spike triggered at the right multiplier thresholds.

---

### `docs/` — Design Documents

#### `docs/DESIGN.md`
Architecture document covering the four-stage pipeline, technology choices, how the POS correlation works, re-entry and cross-camera deduplication logic, and the "AI-Assisted Decisions" section required by the challenge spec.

#### `docs/CHOICES.md`
Explains the three key technical decisions: why YOLOv8n was chosen (speed vs accuracy tradeoff for real-time retail), why the event schema was designed the way it is (every event is self-contained for idempotent replay), and one key API design decision.

---

## Data Flow Summary

```
CCTV Clips (.mp4)
      │
      ▼
pipeline/detect.py  ← YOLOv8 + ByteTrack + store_layout.json
      │  (person detections + zone classification)
      ▼
pipeline/tracker.py ← Re-ID / cross-camera deduplication
      │
      ▼
pipeline/emit.py    ← Validated Event objects
      │
      ▼
pipeline_output/*.jsonl  ← One JSON event per line
      │
      ▼  (POST /events/ingest)
app/ingestion.py    ← Idempotent insert into SQLite
      │
      ▼
store_intelligence.db   (events + pos_transactions tables)
      │
      ├──▶ app/metrics.py     → GET /stores/{id}/metrics
      ├──▶ app/funnel.py      → GET /stores/{id}/funnel
      ├──▶ app/heatmap.py     → GET /stores/{id}/heatmap
      ├──▶ app/anomalies.py   → GET /stores/{id}/anomalies
      └──▶ app/health.py      → GET /health
                                     │
                                     ▼
                             /dashboard (live SSE feed → browser)
```
