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
