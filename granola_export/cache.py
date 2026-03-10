"""
Granola cache loader and parser.

Handles loading the Granola local cache file and parsing its
double-encoded JSON structure into usable data models.
"""

import json
from pathlib import Path
from typing import Iterator, Optional
from datetime import datetime

from .models import (
    Document,
    Transcript,
    Meeting,
    Person,
    Workspace,
    Calendar,
    Folder,
)
from .paths import get_default_cache_path


class GranolaCache:
    """
    Interface for reading and querying Granola's local cache.

    The Granola app stores meeting data in a local JSON cache file.
    This class provides methods to load, parse, and query that data.

    Example:
        >>> cache = GranolaCache()
        >>> cache.load()
        >>> for meeting in cache.meetings():
        ...     print(meeting.title)
    """

    def __init__(self, cache_path: Optional[Path] = None):
        """
        Initialize the cache loader.

        Args:
            cache_path: Optional path to the cache file.
                       If not provided, uses the platform default.
        """
        self.cache_path = cache_path or get_default_cache_path()
        self._state: dict = {}
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        """Check if the cache has been loaded."""
        return self._loaded

    def exists(self) -> bool:
        """Check if the cache file exists."""
        return self.cache_path.exists()

    def load(self) -> "GranolaCache":
        """
        Load and parse the cache file.

        Returns:
            Self for method chaining.

        Raises:
            FileNotFoundError: If the cache file doesn't exist.
            json.JSONDecodeError: If the cache file is corrupted.
        """
        if not self.cache_path.exists():
            raise FileNotFoundError(
                f"Granola cache not found at {self.cache_path}\n"
                "Make sure Granola is installed and has been run at least once."
            )

        with open(self.cache_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        # Granola uses double-encoded JSON
        cache_str = raw_data.get("cache", "{}")
        inner_data = json.loads(cache_str)
        self._state = inner_data.get("state", {})
        self._loaded = True

        return self

    def _ensure_loaded(self) -> None:
        """Ensure the cache is loaded before accessing data."""
        if not self._loaded:
            self.load()

    # -------------------------------------------------------------------------
    # Document Access
    # -------------------------------------------------------------------------

    @property
    def document_count(self) -> int:
        """Get the total number of documents."""
        self._ensure_loaded()
        return len(self._state.get("documents", {}))

    @property
    def transcript_count(self) -> int:
        """Get the total number of transcripts."""
        self._ensure_loaded()
        return len(self._state.get("transcripts", {}))

    def get_document(self, doc_id: str) -> Optional[Document]:
        """
        Get a specific document by ID.

        Args:
            doc_id: The document ID.

        Returns:
            The Document, or None if not found.
        """
        self._ensure_loaded()
        docs = self._state.get("documents", {})
        panels = self._state.get("documentPanels", {})

        if doc_id not in docs:
            return None

        return Document.from_dict(
            doc_id,
            docs[doc_id],
            panels.get(doc_id, []),
        )

    def get_transcript(self, doc_id: str) -> Optional[Transcript]:
        """
        Get a transcript by document ID.

        Args:
            doc_id: The document ID.

        Returns:
            The Transcript, or None if not found.
        """
        self._ensure_loaded()
        transcripts = self._state.get("transcripts", {})

        if doc_id not in transcripts:
            return None

        return Transcript.from_dict(doc_id, transcripts[doc_id])

    def get_meeting(self, doc_id: str) -> Optional[Meeting]:
        """
        Get a complete meeting (document + transcript + metadata).

        Args:
            doc_id: The document ID.

        Returns:
            The Meeting, or None if not found.
        """
        doc = self.get_document(doc_id)
        if not doc:
            return None

        transcript = self.get_transcript(doc_id)
        metadata = self._state.get("meetingsMetadata", {}).get(doc_id, {})

        return Meeting(document=doc, transcript=transcript, metadata=metadata)

    def documents(self) -> Iterator[Document]:
        """
        Iterate over all documents.

        Yields:
            Document objects.
        """
        self._ensure_loaded()
        docs = self._state.get("documents", {})
        panels = self._state.get("documentPanels", {})

        for doc_id, doc_data in docs.items():
            yield Document.from_dict(doc_id, doc_data, panels.get(doc_id, []))

    def transcripts(self) -> Iterator[Transcript]:
        """
        Iterate over all transcripts.

        Yields:
            Transcript objects.
        """
        self._ensure_loaded()
        transcripts = self._state.get("transcripts", {})

        for doc_id, trans_data in transcripts.items():
            yield Transcript.from_dict(doc_id, trans_data)

    def meetings(self) -> Iterator[Meeting]:
        """
        Iterate over all meetings with their transcripts and metadata.

        Yields:
            Meeting objects combining document, transcript, and metadata.
        """
        self._ensure_loaded()
        docs = self._state.get("documents", {})
        panels = self._state.get("documentPanels", {})
        transcripts = self._state.get("transcripts", {})
        metadata = self._state.get("meetingsMetadata", {})

        for doc_id, doc_data in docs.items():
            doc = Document.from_dict(doc_id, doc_data, panels.get(doc_id, []))

            transcript = None
            if doc_id in transcripts:
                transcript = Transcript.from_dict(doc_id, transcripts[doc_id])

            yield Meeting(
                document=doc,
                transcript=transcript,
                metadata=metadata.get(doc_id, {}),
            )

    # -------------------------------------------------------------------------
    # People, Calendars, and Workspaces
    # -------------------------------------------------------------------------

    def people(self) -> Iterator[Person]:
        """
        Iterate over all people/contacts.

        Yields:
            Person objects.
        """
        self._ensure_loaded()
        people_data = self._state.get("people", [])

        for person_data in people_data:
            if isinstance(person_data, dict):
                yield Person.from_dict(person_data)

    def calendars(self) -> Iterator[Calendar]:
        """
        Iterate over all connected calendars.

        Yields:
            Calendar objects.
        """
        self._ensure_loaded()
        calendars_data = self._state.get("calendars", [])

        for cal_data in calendars_data:
            if isinstance(cal_data, dict):
                yield Calendar.from_dict(cal_data)

    def workspaces(self) -> Iterator[Workspace]:
        """
        Iterate over all workspaces.

        Yields:
            Workspace objects.
        """
        self._ensure_loaded()
        workspaces_data = self._state.get("workspacesById", {})

        for ws_id, ws_data in workspaces_data.items():
            if isinstance(ws_data, dict):
                yield Workspace.from_dict(ws_id, ws_data)

    def folders(self) -> Iterator[Folder]:
        """
        Iterate over all folders/document lists.

        Yields:
            Folder objects with document IDs.
        """
        self._ensure_loaded()
        lists_metadata = self._state.get("documentListsMetadata", {})
        lists_data = self._state.get("documentLists", {})

        for folder_id, metadata in lists_metadata.items():
            if isinstance(metadata, dict):
                # Get document IDs for this folder
                doc_ids = lists_data.get(folder_id, [])
                if isinstance(doc_ids, list):
                    # Extract just IDs if it's a list of dicts
                    doc_ids = [
                        d.get("id") if isinstance(d, dict) else d
                        for d in doc_ids
                    ]
                yield Folder.from_dict(folder_id, metadata, doc_ids)

    def get_folder_for_document(self, doc_id: str) -> list[Folder]:
        """
        Get all folders that contain a specific document.

        Args:
            doc_id: The document ID.

        Returns:
            List of Folder objects containing this document.
        """
        result = []
        for folder in self.folders():
            if doc_id in folder.document_ids:
                result.append(folder)
        return result

    # -------------------------------------------------------------------------
    # Search and Filtering
    # -------------------------------------------------------------------------

    def search_documents(
        self,
        query: str,
        case_sensitive: bool = False,
    ) -> Iterator[Document]:
        """
        Search documents by title or content.

        Args:
            query: Search string.
            case_sensitive: Whether to match case.

        Yields:
            Matching Document objects.
        """
        if not case_sensitive:
            query = query.lower()

        for doc in self.documents():
            title = doc.title if case_sensitive else doc.title.lower()
            notes = doc.notes_text if case_sensitive else doc.notes_text.lower()

            if query in title or query in notes:
                yield doc

    def search_transcripts(
        self,
        query: str,
        case_sensitive: bool = False,
    ) -> Iterator[Transcript]:
        """
        Search transcripts by content.

        Args:
            query: Search string.
            case_sensitive: Whether to match case.

        Yields:
            Matching Transcript objects.
        """
        if not case_sensitive:
            query = query.lower()

        for transcript in self.transcripts():
            text = transcript.full_text if case_sensitive else transcript.full_text.lower()

            if query in text:
                yield transcript

    def filter_meetings_by_date(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Iterator[Meeting]:
        """
        Filter meetings by date range.

        Args:
            start_date: Include meetings on or after this date.
            end_date: Include meetings on or before this date.

        Yields:
            Matching Meeting objects.
        """
        for meeting in self.meetings():
            if not meeting.created_at:
                continue

            if start_date and meeting.created_at < start_date:
                continue

            if end_date and meeting.created_at > end_date:
                continue

            yield meeting

    # -------------------------------------------------------------------------
    # Raw Data Access
    # -------------------------------------------------------------------------

    @property
    def raw_state(self) -> dict:
        """Get the raw state dictionary for advanced access."""
        self._ensure_loaded()
        return self._state

    def get_raw_key(self, key: str) -> Optional[any]:
        """
        Get a raw value from the state by key.

        Args:
            key: The state key to retrieve.

        Returns:
            The raw value, or None if not found.
        """
        self._ensure_loaded()
        return self._state.get(key)

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict:
        """
        Get statistics about the cached data.

        Returns:
            Dictionary with counts and metadata.
        """
        self._ensure_loaded()

        meetings = list(self.meetings())
        with_transcripts = sum(1 for m in meetings if m.has_transcript)

        total_words = 0
        for t in self.transcripts():
            total_words += t.word_count

        return {
            "documents": self.document_count,
            "transcripts": self.transcript_count,
            "meetings_with_transcripts": with_transcripts,
            "folders": len(self._state.get("documentListsMetadata", {})),
            "people": len(self._state.get("people", [])),
            "calendars": len(self._state.get("calendars", [])),
            "workspaces": len(self._state.get("workspacesById", {})),
            "total_transcript_words": total_words,
            "cache_path": str(self.cache_path),
        }
