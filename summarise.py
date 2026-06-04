import json
from pathlib import Path
from collections import Counter

files = [
    "pipeline_output/CAM_ENTRY_01.jsonl",
    "pipeline_output/CAM_ENTRY_02.jsonl",
    "pipeline_output/CAM_MAIN_01.jsonl",
    "pipeline_output/CAM_MAIN_02.jsonl",
    "pipeline_output/CAM_BILLING_01.jsonl",
]
all_events = []
for f in files:
    lines = Path(f).read_text().strip().splitlines()
    all_events.extend([json.loads(l) for l in lines if l.strip()])

Path("pipeline_output/all_events.jsonl").write_text(
    "\n".join(json.dumps(e) for e in all_events) + "\n"
)

types = Counter(e["event_type"] for e in all_events)
visitors = set(e["visitor_id"] for e in all_events)
billing = [e for e in all_events if e["event_type"] == "BILLING_QUEUE_JOIN"]
entries = [e for e in all_events if e["event_type"] == "ENTRY"]
zone_entries = [e for e in all_events if e["event_type"] == "ZONE_ENTER"]
zone_counts = Counter(e["zone_id"] for e in zone_entries if e["zone_id"])

print(f"Total events    : {len(all_events)}")
print(f"Unique visitors : {len(visitors)}")
print()
print("By event type:")
for t, n in sorted(types.items(), key=lambda x: -x[1]):
    print(f"  {t:30s}: {n}")
print()
print("Zone visits (ZONE_ENTER):")
for z, n in zone_counts.most_common():
    print(f"  {z:20s}: {n}")
print()
print(f"BILLING_QUEUE_JOIN events: {len(billing)}")
conv = len(billing) / len(entries) * 100 if entries else 0
print(f"Approx conversion (billing/entry): {conv:.1f}%")
