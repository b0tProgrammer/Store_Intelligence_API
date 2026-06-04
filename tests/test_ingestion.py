# PROMPT: Generate tests for POST /events/ingest covering: happy path batch, idempotency
#         (same event_id submitted twice), max batch size (500), and staff event tagging.
# CHANGES MADE: Added explicit assertion on `duplicate` count for idempotency test;
#               replaced generic status check with field-level assertions.

import pytest
from tests.conftest import billing_event, entry_event, zone_event


@pytest.mark.asyncio
async def test_ingest_happy_path(client):
    events = [entry_event(visitor_id=f"VIS_{i:03}") for i in range(5)]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 5
    assert body["duplicate"] == 0
    assert body["rejected"] == 0


@pytest.mark.asyncio
async def test_ingest_idempotency(client):
    event = entry_event(visitor_id="VIS_abc")
    payload = {"events": [event]}

    r1 = await client.post("/events/ingest", json=payload)
    assert r1.json()["accepted"] == 1

    # Second call with identical event_id must be a duplicate, not an error
    r2 = await client.post("/events/ingest", json=payload)
    assert r2.status_code == 200
    assert r2.json()["accepted"] == 0
    assert r2.json()["duplicate"] == 1


@pytest.mark.asyncio
async def test_ingest_staff_events_accepted(client):
    staff = entry_event(visitor_id="VIS_staff01", is_staff=True)
    resp = await client.post("/events/ingest", json={"events": [staff]})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1


@pytest.mark.asyncio
async def test_ingest_max_batch(client):
    events = [entry_event(visitor_id=f"VIS_{i:04}") for i in range(500)]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 500


@pytest.mark.asyncio
async def test_ingest_over_max_batch_rejected(client):
    events = [entry_event(visitor_id=f"VIS_{i:04}") for i in range(501)]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_mixed_event_types(client):
    events = [
        entry_event(visitor_id="VIS_x01"),
        zone_event(visitor_id="VIS_x01", zone_id="SKINCARE"),
        billing_event(visitor_id="VIS_x01"),
    ]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 3
