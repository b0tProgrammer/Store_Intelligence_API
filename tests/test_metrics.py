# PROMPT: Generate metrics endpoint tests covering: empty store, all-staff clips,
#         zero purchases, staff exclusion from visitor count, abandonment rate,
#         and queue depth calculation.
# CHANGES MADE: Removed assertion that conversion_rate > 0 without POS data (it cannot be);
#               added explicit staff exclusion check; split into focused single-assertion tests.

import pytest
from tests.conftest import billing_event, entry_event, zone_event


STORE = "STORE_TEST"


async def seed(client, events):
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_metrics_empty_store_returns_zeros(client):
    resp = await client.get(f"/stores/{STORE}/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["unique_visitors"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["queue_depth"] == 0
    assert body["abandonment_rate"] == 0.0


@pytest.mark.asyncio
async def test_metrics_staff_excluded_from_visitors(client):
    events = [
        entry_event(visitor_id="VIS_cust", is_staff=False),
        entry_event(visitor_id="VIS_staff", is_staff=True),
    ]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/metrics")
    assert resp.json()["unique_visitors"] == 1  # staff not counted


@pytest.mark.asyncio
async def test_metrics_zero_purchases_without_pos(client):
    events = [entry_event(visitor_id="VIS_shopper"), billing_event(visitor_id="VIS_shopper")]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/metrics")
    # No POS data → 0 conversions
    assert resp.json()["conversion_rate"] == 0.0


@pytest.mark.asyncio
async def test_metrics_abandonment_rate(client):
    events = [
        entry_event(visitor_id="VIS_a"),
        billing_event(visitor_id="VIS_a", event_type="BILLING_QUEUE_JOIN"),
        entry_event(visitor_id="VIS_b"),
        billing_event(visitor_id="VIS_b", event_type="BILLING_QUEUE_JOIN"),
        billing_event(visitor_id="VIS_b", event_type="BILLING_QUEUE_ABANDON"),
    ]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/metrics")
    body = resp.json()
    # 1 abandon out of 3 queue events → 1/3
    assert abs(body["abandonment_rate"] - 1 / 3) < 0.01


@pytest.mark.asyncio
async def test_metrics_queue_depth(client):
    events = [
        billing_event(visitor_id="VIS_q1", event_type="BILLING_QUEUE_JOIN"),
        billing_event(visitor_id="VIS_q2", event_type="BILLING_QUEUE_JOIN"),
        billing_event(visitor_id="VIS_q2", event_type="BILLING_QUEUE_ABANDON"),
    ]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/metrics")
    # VIS_q1 still in queue; VIS_q2 abandoned
    assert resp.json()["queue_depth"] == 1


@pytest.mark.asyncio
async def test_metrics_dwell_by_zone(client):
    events = [
        zone_event("VIS_a", "SKINCARE", dwell_ms=4000),
        zone_event("VIS_b", "SKINCARE", dwell_ms=6000),
        zone_event("VIS_c", "HAIRCARE", dwell_ms=3000),
    ]
    await seed(client, events)
    resp = await client.get(f"/stores/{STORE}/metrics")
    zones = {z["zone_id"]: z for z in resp.json()["avg_dwell_by_zone"]}
    assert "SKINCARE" in zones
    assert abs(zones["SKINCARE"]["avg_dwell_ms"] - 5000) < 1
