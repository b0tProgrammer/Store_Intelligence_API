"""Run detection pipeline on all CCTV clips for Store 1 and Store 2."""
import subprocess
import sys
from pathlib import Path

OUTPUT_DIR = Path("pipeline_output")
OUTPUT_DIR.mkdir(exist_ok=True)

STORES = [
    {
        "store_id": "STORE_1",
        "clips_dir": Path("Store 1"),
        "layout": "store1_layout.json",
        "cameras": {
            "CAM 3 - entry.mp4":   ("CAM_ENTRY_01",   "2026-03-08T10:00:00+05:30"),
            "CAM 1 - zone.mp4":    ("CAM_ZONE_01",    "2026-03-08T10:00:00+05:30"),
            "CAM 2 - zone.mp4":    ("CAM_ZONE_02",    "2026-03-08T10:00:00+05:30"),
            "CAM 5 - billing.mp4": ("CAM_BILLING_01", "2026-03-08T10:00:00+05:30"),
        },
    },
    {
        "store_id": "STORE_2",
        "clips_dir": Path("Store 2"),
        "layout": "store2_layout.json",
        "cameras": {
            "entry 1.mp4":      ("CAM_ENTRY_01",   "2026-03-08T10:00:00+05:30"),
            "entry 2.mp4":      ("CAM_ENTRY_02",   "2026-03-08T10:00:30+05:30"),
            "zone.mp4":         ("CAM_ZONE_01",    "2026-03-08T10:00:00+05:30"),
            "billing_area.mp4": ("CAM_BILLING_01", "2026-03-08T10:00:00+05:30"),
        },
    },
]

total_events = 0
all_jsonl = OUTPUT_DIR / "all_events.jsonl"

with open(all_jsonl, "w") as merged:
    for store in STORES:
        store_id = store["store_id"]
        store_out = OUTPUT_DIR / store_id
        store_out.mkdir(exist_ok=True)

        print(f"\n{'='*55}")
        print(f"  {store_id}")
        print(f"{'='*55}")

        for clip_name, (camera_id, start_time) in store["cameras"].items():
            clip_path = store["clips_dir"] / clip_name
            if not clip_path.exists():
                print(f"  SKIP: {clip_path} not found")
                continue

            out_file = store_out / f"{camera_id}.jsonl"
            print(f"\n  [{camera_id}] {clip_name} ...")

            cmd = [
                sys.executable, "-m", "pipeline.detect",
                "--store",      store_id,
                "--camera",     camera_id,
                "--clip",       str(clip_path),
                "--layout",     store["layout"],
                "--output",     str(out_file),
                "--conf",       "0.5",
                "--start-time", start_time,
            ]
            result = subprocess.run(cmd, text=True)
            if result.returncode != 0:
                print(f"  ERROR running pipeline for {clip_name}")
                continue

            if out_file.exists():
                lines = [l for l in out_file.read_text().splitlines() if l.strip()]
                total_events += len(lines)
                for line in lines:
                    merged.write(line + "\n")
                print(f"  -> {len(lines)} events written to {out_file}")

print(f"\nTotal events: {total_events}")
print(f"Merged:       {all_jsonl}")
print(f"\nNext — ingest into the API (make sure uvicorn is running):")
print(f"  python ingest_events.py")
