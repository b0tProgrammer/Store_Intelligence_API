"""Detection pipeline entry point.

Processes a video clip through:
  1. Frame sampling (every PROCESS_EVERY frames)
  2. Person detection + ByteTrack (YOLOv8 model.track)
  3. Zone classification (bbox centre vs store_layout.json rectangles)
  4. State machine per track: ENTRY → ZONE_ENTER → ZONE_DWELL → ZONE_EXIT → EXIT
  5. Event emission → JSONL

Usage:
  python -m pipeline.detect --store STORE_BLR_002 --camera CAM_ENTRY_01 \
      --clip "CCTV Footage/CAM 1.mp4" --layout store_layout.json --output events.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pipeline.emit import emit_jsonl, make_event
from pipeline.tracker import VisitorTracker
from app.models import EventType

# ── Tuning knobs ─────────────────────────────────────────────────────────────
PROCESS_EVERY = 1          # process every frame — gives ByteTrack maximum continuity
EXIT_PATIENCE_S = 30.0     # seconds without detection before emitting EXIT
MIN_DWELL_MS = 1500        # minimum milliseconds before emitting a ZONE_DWELL
BILLING_ZONE_NAMES = {"BILLING", "CHECKOUT", "BILLING_QUEUE"}
MAX_CENTRE_DIST_PX = 150   # spatial Re-ID: same person if bbox centres within this many pixels


# ── Track state ───────────────────────────────────────────────────────────────
@dataclass
class TrackState:
    visitor_id: str
    entry_ts: datetime
    last_ts: datetime
    last_bbox: Optional[list] = None
    current_zone: Optional[str] = None
    zone_entry_ts: Optional[datetime] = None
    session_seq: int = 0
    emitted_billing: bool = False


def _frame_ts(frame_idx: int, fps: float, clip_start: datetime) -> datetime:
    return clip_start + timedelta(seconds=frame_idx / fps)


def _bbox_centre_in_zone(
    bbox: list[float], zone: dict, frame_w: int, frame_h: int
) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    # Zone coords: normalised [0,1] if all ≤ 1, else pixel coords
    if zone.get("normalised", True):
        x1 = zone["x1"] * frame_w
        y1 = zone["y1"] * frame_h
        x2 = zone["x2"] * frame_w
        y2 = zone["y2"] * frame_h
    else:
        x1, y1, x2, y2 = zone["x1"], zone["y1"], zone["x2"], zone["y2"]
    return x1 <= cx <= x2 and y1 <= cy <= y2


def _zone_for_bbox(
    bbox: list[float], zones: list[dict], frame_w: int, frame_h: int
) -> Optional[str]:
    for zone in zones:
        if _bbox_centre_in_zone(bbox, zone, frame_w, frame_h):
            return zone["name"]
    return None


def _centre_dist(a: list[float], b: list[float]) -> float:
    """Euclidean distance between centres of two [x1,y1,x2,y2] bboxes."""
    return (((a[0]+a[2])/2 - (b[0]+b[2])/2)**2 + ((a[1]+a[3])/2 - (b[1]+b[3])/2)**2) ** 0.5


def _find_active_spatial_match(bbox: list[float], active: dict, exclude_tid: int) -> Optional[int]:
    """Return the tid of the closest active track within MAX_CENTRE_DIST_PX, or None.

    Handles the common case where ByteTrack silently re-assigns a new track ID
    to a person who is still standing in roughly the same spot.
    """
    best_tid: Optional[int] = None
    best_dist = MAX_CENTRE_DIST_PX
    for tid, state in active.items():
        if tid == exclude_tid or state.last_bbox is None:
            continue
        dist = _centre_dist(bbox, state.last_bbox)
        if dist < best_dist:
            best_dist = dist
            best_tid = tid
    return best_tid


def _extract_embedding(frame, bbox: list[float]) -> Optional[list[float]]:
    """HSV colour histogram from upper-body crop — lightweight Re-ID proxy.

    Uses H (18 bins) + S (16 bins) channels so it is invariant to brightness
    changes but still discriminates clothing colour.  Returns a unit-norm
    vector so cosine similarity works directly.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    upper = crop[:max(1, int(crop.shape[0] * 0.6))]   # torso region
    hsv = cv2.cvtColor(upper, cv2.COLOR_BGR2HSV)
    h_hist = cv2.calcHist([hsv], [0], None, [18], [0, 180]).flatten()
    s_hist = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
    feat = np.concatenate([h_hist, s_hist])
    norm = np.linalg.norm(feat)
    return (feat / norm).tolist() if norm > 0 else None


def run_detection(
    store_id: str,
    camera_id: str,
    clip_path: str,
    layout: dict,
    output_path: Optional[str] = None,
    conf_threshold: float = 0.4,
    clip_start: Optional[datetime] = None,
) -> list:
    """Run YOLOv8 + ByteTrack on *clip_path*, emit structured events."""
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError:
        print(
            "ERROR: install ultralytics and opencv-python before running detection.",
            file=sys.stderr,
        )
        return []

    model = YOLO("yolov8n.pt")
    visitor_tracker = VisitorTracker(store_id=store_id)
    zones = layout.get("zones", [])
    start_ts = clip_start or datetime.now(timezone.utc)
    events: list = []

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        print(f"ERROR: cannot open {clip_path}", file=sys.stderr)
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(
        f"  {Path(clip_path).name}: {frame_w}x{frame_h} @ {fps:.1f}fps"
        f" | {total_frames} frames ({total_frames/fps:.0f}s)"
    )

    active: dict[int, TrackState] = {}   # bytetrack_id → TrackState
    frame_idx = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % PROCESS_EVERY == 0:
            ts = _frame_ts(frame_idx, fps, start_ts)

            results = model.track(
                frame,
                persist=True,
                tracker=str(Path(__file__).parent.parent / "bytetrack_custom.yaml"),
                classes=[0],          # persons only
                conf=0.25,            # low threshold so ByteTrack sees weak detections for 2nd-stage matching
                iou=0.5,
                verbose=False,
            )

            seen_ids: set[int] = set()
            if results[0].boxes.id is not None:
                for box, tid in zip(results[0].boxes, results[0].boxes.id.int().tolist()):
                    bbox = box.xyxy[0].tolist()
                    confidence = float(box.conf[0])
                    seen_ids.add(tid)

                    if tid not in active:
                        # 1. Spatial check: ByteTrack silently re-assigned an ID
                        #    to someone already being tracked
                        dup_tid = _find_active_spatial_match(bbox, active, exclude_tid=tid)
                        if dup_tid is not None:
                            active[tid] = active.pop(dup_tid)
                        elif confidence >= conf_threshold:
                            # 2. Only register genuinely new high-confidence detections
                            visitor_id, is_reentry = visitor_tracker.match_or_create(
                                embedding=_extract_embedding(frame, bbox),
                                camera_id=camera_id,
                                timestamp=ts,
                            )
                            active[tid] = TrackState(
                                visitor_id=visitor_id,
                                entry_ts=ts,
                                last_ts=ts,
                                last_bbox=bbox,
                            )
                            ev_type = EventType.REENTRY if is_reentry else EventType.ENTRY
                            events.append(make_event(
                                store_id=store_id,
                                camera_id=camera_id,
                                visitor_id=visitor_id,
                                event_type=ev_type,
                                timestamp=ts,
                                confidence=confidence,
                                session_seq=1,
                            ))
                        else:
                            # Low-confidence new detection — let ByteTrack track it
                            # internally but don't create a visitor record yet
                            continue

                    state = active[tid]
                    state.last_ts = ts
                    state.last_bbox = bbox
                    state.session_seq += 1

                    # Keep embedding fresh so colour Re-ID works after EXIT
                    emb = _extract_embedding(frame, bbox)
                    if emb is not None:
                        visitor_tracker.update_embedding(state.visitor_id, emb)

                    # Zone classification
                    new_zone = _zone_for_bbox(bbox, zones, frame_w, frame_h)

                    if new_zone != state.current_zone:
                        # Leaving old zone
                        if state.current_zone and state.zone_entry_ts:
                            dwell_ms = int((ts - state.zone_entry_ts).total_seconds() * 1000)
                            if dwell_ms >= MIN_DWELL_MS:
                                events.append(make_event(
                                    store_id=store_id,
                                    camera_id=camera_id,
                                    visitor_id=state.visitor_id,
                                    event_type=EventType.ZONE_DWELL,
                                    timestamp=ts,
                                    zone_id=state.current_zone,
                                    dwell_ms=dwell_ms,
                                    confidence=confidence,
                                    session_seq=state.session_seq,
                                ))
                            events.append(make_event(
                                store_id=store_id,
                                camera_id=camera_id,
                                visitor_id=state.visitor_id,
                                event_type=EventType.ZONE_EXIT,
                                timestamp=ts,
                                zone_id=state.current_zone,
                                confidence=confidence,
                                session_seq=state.session_seq,
                            ))

                        # Entering new zone
                        if new_zone:
                            events.append(make_event(
                                store_id=store_id,
                                camera_id=camera_id,
                                visitor_id=state.visitor_id,
                                event_type=EventType.ZONE_ENTER,
                                timestamp=ts,
                                zone_id=new_zone,
                                confidence=confidence,
                                session_seq=state.session_seq,
                            ))
                            # Billing queue join
                            if new_zone.upper() in BILLING_ZONE_NAMES and not state.emitted_billing:
                                events.append(make_event(
                                    store_id=store_id,
                                    camera_id=camera_id,
                                    visitor_id=state.visitor_id,
                                    event_type=EventType.BILLING_QUEUE_JOIN,
                                    timestamp=ts,
                                    zone_id=new_zone,
                                    confidence=confidence,
                                    session_seq=state.session_seq,
                                ))
                                state.emitted_billing = True

                        state.current_zone = new_zone
                        state.zone_entry_ts = ts if new_zone else None

            # EXIT: tracks not seen for EXIT_PATIENCE_S seconds
            for tid in list(active.keys()):
                if tid in seen_ids:
                    continue
                state = active[tid]
                gap_s = (ts - state.last_ts).total_seconds()
                if gap_s < EXIT_PATIENCE_S:
                    continue

                # Flush zone dwell
                if state.current_zone and state.zone_entry_ts:
                    dwell_ms = int((state.last_ts - state.zone_entry_ts).total_seconds() * 1000)
                    if dwell_ms >= MIN_DWELL_MS:
                        events.append(make_event(
                            store_id=store_id,
                            camera_id=camera_id,
                            visitor_id=state.visitor_id,
                            event_type=EventType.ZONE_DWELL,
                            timestamp=state.last_ts,
                            zone_id=state.current_zone,
                            dwell_ms=dwell_ms,
                            confidence=0.5,
                        ))
                    events.append(make_event(
                        store_id=store_id,
                        camera_id=camera_id,
                        visitor_id=state.visitor_id,
                        event_type=EventType.ZONE_EXIT,
                        timestamp=state.last_ts,
                        zone_id=state.current_zone,
                        confidence=0.5,
                    ))

                events.append(make_event(
                    store_id=store_id,
                    camera_id=camera_id,
                    visitor_id=state.visitor_id,
                    event_type=EventType.EXIT,
                    timestamp=state.last_ts,
                    confidence=0.5,
                ))
                visitor_tracker.mark_exited(state.visitor_id)
                del active[tid]

        frame_idx += 1

    cap.release()

    # Flush any still-active tracks at end of clip
    end_ts = _frame_ts(frame_idx, fps, start_ts)
    for state in active.values():
        if state.current_zone and state.zone_entry_ts:
            dwell_ms = int((end_ts - state.zone_entry_ts).total_seconds() * 1000)
            if dwell_ms >= MIN_DWELL_MS:
                events.append(make_event(
                    store_id=store_id,
                    camera_id=camera_id,
                    visitor_id=state.visitor_id,
                    event_type=EventType.ZONE_DWELL,
                    timestamp=end_ts,
                    zone_id=state.current_zone,
                    dwell_ms=dwell_ms,
                    confidence=0.5,
                ))
        events.append(make_event(
            store_id=store_id,
            camera_id=camera_id,
            visitor_id=state.visitor_id,
            event_type=EventType.EXIT,
            timestamp=end_ts,
            confidence=0.5,
        ))

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            emit_jsonl(events, f)

    return events


def main():
    parser = argparse.ArgumentParser(description="Run YOLOv8 detection pipeline on a video clip")
    parser.add_argument("--store", required=True, help="Store ID, e.g. STORE_BLR_002")
    parser.add_argument("--camera", default="CAM_01", help="Camera ID, e.g. CAM_ENTRY_01")
    parser.add_argument("--clip", required=True, help="Path to video file")
    parser.add_argument("--layout", default="store_layout.json", help="Zone layout JSON")
    parser.add_argument("--output", default="events.jsonl", help="Output JSONL path")
    parser.add_argument("--conf", type=float, default=0.4, help="Detection confidence threshold")
    parser.add_argument(
        "--start-time",
        default=None,
        help="Clip start datetime ISO8601, e.g. 2026-05-31T09:00:00+05:30",
    )
    args = parser.parse_args()

    clip_start = None
    if args.start_time:
        clip_start = datetime.fromisoformat(args.start_time).astimezone(timezone.utc)

    if not Path(args.layout).exists():
        print(f"WARNING: layout file '{args.layout}' not found — zone detection disabled.")
        layout = {"zones": []}
    else:
        with open(args.layout) as f:
            layout = json.load(f)

    print(f"Processing: {args.clip}")
    events = run_detection(
        store_id=args.store,
        camera_id=args.camera,
        clip_path=args.clip,
        layout=layout,
        output_path=args.output,
        conf_threshold=args.conf,
        clip_start=clip_start,
    )
    print(f"Done: {len(events)} events -> {args.output}")


if __name__ == "__main__":
    main()
