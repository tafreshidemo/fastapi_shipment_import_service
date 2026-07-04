from __future__ import annotations

from app.imports.services.duplicate_tracker import DuplicateTracker


def test_duplicate_tracker_tracks_codes_across_chunks() -> None:
    tracker = DuplicateTracker()

    first_chunk = {"SHP-1", "SHP-2"}
    second_chunk = {"SHP-2", "SHP-3"}

    assert tracker.unseen_codes(first_chunk) == first_chunk
    tracker.remember_codes(first_chunk)
    assert tracker.unseen_codes(second_chunk) == {"SHP-3"}
    tracker.remember_codes({"SHP-3"})
    assert tracker.unseen_codes(second_chunk) == set()
