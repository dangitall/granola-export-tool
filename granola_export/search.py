"""
Search and filtering utilities for Granola data.

Provides advanced search capabilities across meetings,
documents, and transcripts.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterator, Optional

from .cache import GranolaCache
from .models import Meeting, Document, Transcript


@dataclass
class SearchResult:
    """A search result with match information."""

    meeting: Meeting
    match_type: str  # 'title', 'notes', 'transcript'
    snippet: str
    score: float = 0.0

    @property
    def title(self) -> str:
        return self.meeting.title

    @property
    def id(self) -> str:
        return self.meeting.id


@dataclass
class SearchQuery:
    """
    Structured search query with filters.

    Example:
        query = SearchQuery(
            text="product roadmap",
            date_from=datetime(2024, 1, 1),
            has_transcript=True,
        )
    """

    text: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    has_transcript: Optional[bool] = None
    participants: list[str] = field(default_factory=list)
    workspace_id: Optional[str] = None
    case_sensitive: bool = False
    regex: bool = False


class MeetingSearcher:
    """
    Advanced search functionality for Granola meetings.

    Supports text search, date filtering, and various
    other criteria to find relevant meetings.
    """

    def __init__(self, cache: GranolaCache):
        """
        Initialize the searcher.

        Args:
            cache: A loaded GranolaCache instance.
        """
        self.cache = cache
        if not cache.is_loaded:
            cache.load()

    def search(self, query: SearchQuery) -> Iterator[SearchResult]:
        """
        Search meetings with the given query.

        Args:
            query: SearchQuery with search criteria.

        Yields:
            SearchResult objects for matching meetings.
        """
        for meeting in self.cache.meetings():
            result = self._match_meeting(meeting, query)
            if result:
                yield result

    def _match_meeting(
        self, meeting: Meeting, query: SearchQuery
    ) -> Optional[SearchResult]:
        """Check if a meeting matches the query."""
        # Date filters
        if query.date_from and meeting.created_at:
            if meeting.created_at < query.date_from:
                return None

        if query.date_to and meeting.created_at:
            if meeting.created_at > query.date_to:
                return None

        # Transcript filter
        if query.has_transcript is not None:
            if meeting.has_transcript != query.has_transcript:
                return None

        # Workspace filter
        if query.workspace_id:
            if meeting.document.workspace_id != query.workspace_id:
                return None

        # Participant filter
        if query.participants:
            meeting_participants = set(
                p.lower() for p in meeting.document.participants
            )
            query_participants = set(p.lower() for p in query.participants)
            if not query_participants.intersection(meeting_participants):
                return None

        # Text search
        if query.text:
            return self._text_search(meeting, query)

        # No text query, but passed all filters
        return SearchResult(
            meeting=meeting,
            match_type="filter",
            snippet="",
            score=1.0,
        )

    def _text_search(
        self, meeting: Meeting, query: SearchQuery
    ) -> Optional[SearchResult]:
        """Perform text search on a meeting."""
        search_text = query.text

        if query.regex:
            try:
                flags = 0 if query.case_sensitive else re.IGNORECASE
                pattern = re.compile(search_text, flags)
            except re.error:
                return None
        else:
            if not query.case_sensitive:
                search_text = search_text.lower()

        # Search in title (highest priority)
        title = meeting.title if query.case_sensitive else meeting.title.lower()
        if query.regex:
            match = pattern.search(meeting.title)
            if match:
                return SearchResult(
                    meeting=meeting,
                    match_type="title",
                    snippet=self._extract_snippet(meeting.title, match.start(), 50),
                    score=10.0,
                )
        elif search_text in title:
            return SearchResult(
                meeting=meeting,
                match_type="title",
                snippet=meeting.title,
                score=10.0,
            )

        # Search in notes
        notes = meeting.document.notes_text
        notes_search = notes if query.case_sensitive else notes.lower()

        if query.regex:
            match = pattern.search(notes)
            if match:
                return SearchResult(
                    meeting=meeting,
                    match_type="notes",
                    snippet=self._extract_snippet(notes, match.start(), 100),
                    score=5.0,
                )
        elif search_text in notes_search:
            idx = notes_search.find(search_text)
            return SearchResult(
                meeting=meeting,
                match_type="notes",
                snippet=self._extract_snippet(notes, idx, 100),
                score=5.0,
            )

        # Search in transcript
        if meeting.has_transcript:
            transcript_text = meeting.transcript.full_text
            transcript_search = (
                transcript_text if query.case_sensitive else transcript_text.lower()
            )

            if query.regex:
                match = pattern.search(transcript_text)
                if match:
                    return SearchResult(
                        meeting=meeting,
                        match_type="transcript",
                        snippet=self._extract_snippet(
                            transcript_text, match.start(), 100
                        ),
                        score=3.0,
                    )
            elif search_text in transcript_search:
                idx = transcript_search.find(search_text)
                return SearchResult(
                    meeting=meeting,
                    match_type="transcript",
                    snippet=self._extract_snippet(transcript_text, idx, 100),
                    score=3.0,
                )

        return None

    def _extract_snippet(self, text: str, pos: int, context: int) -> str:
        """Extract a snippet around a position in text."""
        start = max(0, pos - context)
        end = min(len(text), pos + context)

        snippet = text[start:end].strip()

        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."

        return snippet

    def recent(self, days: int = 7) -> Iterator[Meeting]:
        """
        Get meetings from the last N days.

        Args:
            days: Number of days to look back.

        Yields:
            Meeting objects from the specified period.
        """
        cutoff = datetime.now() - timedelta(days=days)

        for meeting in self.cache.meetings():
            if meeting.created_at and meeting.created_at >= cutoff:
                yield meeting

    def with_transcripts(self) -> Iterator[Meeting]:
        """
        Get all meetings that have transcripts.

        Yields:
            Meeting objects with transcripts.
        """
        for meeting in self.cache.meetings():
            if meeting.has_transcript:
                yield meeting

    def by_participant(self, name: str) -> Iterator[Meeting]:
        """
        Find meetings with a specific participant.

        Args:
            name: Participant name to search for (case-insensitive).

        Yields:
            Matching Meeting objects.
        """
        name_lower = name.lower()

        for meeting in self.cache.meetings():
            participants = [p.lower() for p in meeting.document.participants]
            if any(name_lower in p for p in participants):
                yield meeting


def quick_search(cache: GranolaCache, query: str) -> list[SearchResult]:
    """
    Convenience function for quick text search.

    Args:
        cache: A loaded GranolaCache.
        query: Search text.

    Returns:
        List of SearchResult objects sorted by score.
    """
    searcher = MeetingSearcher(cache)
    search_query = SearchQuery(text=query)

    results = list(searcher.search(search_query))
    return sorted(results, key=lambda r: r.score, reverse=True)
