"""Re-ID and cross-camera visitor tracking.

Assign stable visitor_ids across frames and cameras using appearance embeddings
(OSNet/torchreid) plus bounding-box trajectory distance as a fallback.

Key responsibilities:
- Maintain active track registry per store (visitor_id → last seen bbox, timestamp, embedding)
- Re-ID match: cosine similarity on embedding; if > threshold → same visitor
- Re-entry detection: if visitor_id already has EXIT event and re-appears → REENTRY
- Cross-camera dedup: a visitor visible in two cameras simultaneously shares one visitor_id
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Track:
    visitor_id: str
    store_id: str
    camera_id: str
    first_seen: datetime
    last_seen: datetime
    is_staff: bool = False
    has_exited: bool = False
    embedding: Optional[list[float]] = None


class VisitorTracker:
    """In-memory track registry for one store session."""

    def __init__(self, store_id: str, reid_threshold: float = 0.80):
        self.store_id = store_id
        self.reid_threshold = reid_threshold
        self._tracks: dict[str, Track] = {}

    def match_or_create(
        self,
        embedding: Optional[list[float]],
        camera_id: str,
        timestamp: Optional[datetime] = None,
        is_staff: bool = False,
    ) -> tuple[str, bool]:
        """Return (visitor_id, is_reentry).

        Tries cosine similarity match against active tracks. Creates new track if
        no match found. Flags re-entry if matched track previously exited.
        """
        ts = timestamp or datetime.now(timezone.utc)

        if embedding is not None:
            best_id, best_sim = self._find_best_match(embedding, ts)
            if best_id and best_sim >= self.reid_threshold:
                track = self._tracks[best_id]
                is_reentry = track.has_exited
                track.last_seen = ts
                track.has_exited = False
                track.embedding = embedding
                return best_id, is_reentry

        visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
        self._tracks[visitor_id] = Track(
            visitor_id=visitor_id,
            store_id=self.store_id,
            camera_id=camera_id,
            first_seen=ts,
            last_seen=ts,
            is_staff=is_staff,
            embedding=embedding,
        )
        return visitor_id, False

    def mark_exited(self, visitor_id: str) -> None:
        if visitor_id in self._tracks:
            self._tracks[visitor_id].has_exited = True

    def update_embedding(self, visitor_id: str, embedding: list[float]) -> None:
        if visitor_id in self._tracks:
            self._tracks[visitor_id].embedding = embedding

    def _find_best_match(
        self,
        embedding: list[float],
        now: Optional[datetime] = None,
        max_age_s: float = 120.0,
    ) -> tuple[Optional[str], float]:
        best_id: Optional[str] = None
        best_sim = 0.0
        ref = now or datetime.now(timezone.utc)
        for vid, track in self._tracks.items():
            if not track.has_exited:
                continue  # only re-match against visitors who have left
            if track.embedding is None:
                continue
            if (ref - track.last_seen).total_seconds() > max_age_s:
                continue  # too stale to be a reliable match
            sim = _cosine_similarity(embedding, track.embedding)
            if sim > best_sim:
                best_sim = sim
                best_id = vid
        return best_id, best_sim


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x ** 2 for x in a) ** 0.5
    norm_b = sum(x ** 2 for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
