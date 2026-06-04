# PROMPT: Write anomaly detection tests: dead zone after 30 min silence, no false
#         positives during stable periods, and health endpoint STALE_FEED flag.
# CHANGES MADE: Used datetime arithmetic for deterministic timestamps rather than
#               mocking time; separated each anomaly type into its own test function.

from datetime import datetime, timezone, timedelta

import pytest

from tests.conftest import billing_event, entry_event, zone_event

STORE = "STORE_TEST"


async def seed(client, events):
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_no_anomalies_on_empty_store(client):
    resp = await client.get(f"/stores/{STORE}/anomalies")
    assert resp.status_code == 200
    assert resp.json()["anomalies"] == []


@pytest.mark.asyncio
async def test_dead_zone_detected_after_silence(client):
    old_ts = datetime.now(timezone.utc) - timedelta(minutes=45)
    events = [
        entry_event(visitor_id="VIS_old"),
        {**zone_event("VIS_old", "PERFUME", event_type="ZONE_ENTER"), "timestamp": old_ts.isoformat()},
    ]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/anomalies")
    anomaly_types = [a["anomaly_type"] for a in resp.json()["anomalies"]]
    assert "DEAD_ZONE" in anomaly_types


@pytest.mark.asyncio
async def test_no_dead_zone_when_recent_activity(client):
    events = [
        entry_event(visitor_id="VIS_recent"),
        zone_event("VIS_recent", "SKINCARE", event_type="ZONE_ENTER"),
    ]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/anomalies")
    dead = [a for a in resp.json()["anomalies"] if a["anomaly_type"] == "DEAD_ZONE"]
    assert dead == []


@pytest.mark.asyncio
async def test_health_ok_with_recent_events(client):
    await seed(client, [entry_event(visitor_id="VIS_health")])
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    store = next(s for s in body["stores"] if s["store_id"] == STORE)
    assert store["status"] == "OK"
    assert store["lag_minutes"] is not None
    assert store["lag_minutes"] < 10


@pytest.mark.asyncio
async def test_health_stale_feed(client):
    old_ts = datetime.now(timezone.utc) - timedelta(minutes=15)
    event = {**entry_event(visitor_id="VIS_stale"), "timestamp": old_ts.isoformat(), "event_id": "stale-evt-001"}
    await seed(client, [event])
    resp = await client.get("/health")
    store = next(s for s in resp.json()["stores"] if s["store_id"] == STORE)
    assert store["status"] == "STALE_FEED"


@pytest.mark.asyncio
async def test_funnel_all_zeros_empty_store(client):
    resp = await client.get(f"/stores/{STORE}/funnel")
    assert resp.status_code == 200
    for stage in resp.json()["stages"]:
        assert stage["count"] == 0
        assert stage["pct_of_entry"] == 0.0


@pytest.mark.asyncio
async def test_funnel_stages_monotone(client):
    events = [
        entry_event(visitor_id="VIS_f1"),
        entry_event(visitor_id="VIS_f2"),
        zone_event("VIS_f1", "SKINCARE", event_type="ZONE_ENTER"),
        billing_event(visitor_id="VIS_f1"),
    ]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/funnel")
    counts = [s["count"] for s in resp.json()["stages"]]
    # Each subsequent stage must be ≤ previous
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i - 1], f"Stage {i} ({counts[i]}) > stage {i-1} ({counts[i-1]})"
