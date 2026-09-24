"""Tests for MeetingSearcher filtering."""

from unittest.mock import MagicMock

from granola_export.models import Document, Meeting
from granola_export.search import MeetingSearcher, SearchQuery


def _searcher():
    """A MeetingSearcher over a stubbed, already-loaded cache."""
    cache = MagicMock()
    cache.is_loaded = True
    return MeetingSearcher(cache)


def _meeting(participants):
    return Meeting(document=Document(id="d1", title="T", participants=participants))


class TestParticipantFilter:
    def test_matches_case_insensitively(self):
        """A participant filter matches regardless of case."""
        searcher = _searcher()
        meeting = _meeting(["Alice Example", "Bob Roe"])
        query = SearchQuery(participants=["alice example"])

        assert searcher._match_meeting(meeting, query) is not None

    def test_no_overlap_is_filtered_out(self):
        """A meeting with no matching participant is excluded."""
        searcher = _searcher()
        meeting = _meeting(["Alice Example"])
        query = SearchQuery(participants=["carol nobody"])

        assert searcher._match_meeting(meeting, query) is None

    def test_partial_participant_overlap_matches(self):
        """Any intersecting participant is enough to match."""
        searcher = _searcher()
        meeting = _meeting(["Alice Example", "Bob Roe"])
        query = SearchQuery(participants=["carol nobody", "BOB ROE"])

        assert searcher._match_meeting(meeting, query) is not None


class TestDateFilter:
    def test_naive_bound_compares_with_aware_meeting_date(self):
        """Callers may pass naive (local) bounds; this must not raise."""
        from datetime import datetime

        searcher = _searcher()
        meeting = Meeting(
            document=Document.from_dict("d1", {"created_at": "2026-01-01T10:00:00Z"})
        )

        assert searcher._match_meeting(
            meeting, SearchQuery(date_from=datetime(2025, 1, 1))
        )
        assert not searcher._match_meeting(
            meeting, SearchQuery(date_from=datetime(2027, 1, 1))
        )
