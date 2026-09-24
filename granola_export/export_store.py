"""
Read meetings from an ``api-export`` output directory.

Granola now encrypts its local cache, so the read commands (list, search,
show, stats, and the Markdown/CSV/HTML/JSON exporters) work from the
directory ``granola-export api-export --sync`` maintains instead. The layout
is::

    <dir>/all_meetings.json            every document, as the API returns it
    <dir>/transcripts/transcript_<id8>.json
    <dir>/folders.json, people.json, workspaces.json, manifest.json

:class:`ExportStore` exposes the same interface as :class:`GranolaCache`, so
exporters and the search engine accept either.
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    Calendar,
    Document,
    Folder,
    Meeting,
    Person,
    Transcript,
    Workspace,
    ensure_aware,
)
from .prosemirror import to_markdown

logger = logging.getLogger(__name__)

COMBINED_FILENAME = "all_meetings.json"
MANIFEST_FILENAME = "manifest.json"


class ExportStore:
    """
    Interface for reading an api-export output directory.

    Example:
        >>> store = ExportStore(Path("~/granola-api-export").expanduser())
        >>> store.load()
        >>> for meeting in store.meetings():
        ...     print(meeting.title)
    """

    def __init__(self, data_dir: Path):
        """
        Initialize the reader.

        Args:
            data_dir: Directory written by ``granola-export api-export``.
        """
        self.data_dir = Path(data_dir).expanduser()
        self._documents: list[dict] = []
        # Transcript files are indexed at load() and parsed on demand: all
        # of them together are hundreds of MB, and `list`/`show` need few.
        self._transcript_files: dict[str, list[Path]] = {}
        self._transcript_cache: dict[str, Transcript | None] = {}
        self._loaded = False

    @property
    def source_path(self) -> Path:
        """The directory being read (shown to users as the data source)."""
        return self.data_dir

    @property
    def is_loaded(self) -> bool:
        """Check if the export has been loaded."""
        return self._loaded

    def exists(self) -> bool:
        """Check if the directory holds an api-export."""
        return (self.data_dir / COMBINED_FILENAME).exists()

    def load(self) -> "ExportStore":
        """
        Load the export's document index.

        Returns:
            Self for method chaining.

        Raises:
            FileNotFoundError: If the directory holds no api-export.
            json.JSONDecodeError: If all_meetings.json is corrupted.
        """
        combined = self.data_dir / COMBINED_FILENAME
        if not combined.exists():
            raise FileNotFoundError(
                f"No Granola export found at {self.data_dir}\n"
                "Create one with: granola-export api-export --sync "
                f"-o {self.data_dir}"
            )

        with open(combined, encoding="utf-8") as f:
            data = json.load(f)
        meetings = data.get("meetings") if isinstance(data, dict) else None
        self._documents = [
            d for d in (meetings or []) if isinstance(d, dict) and d.get("id")
        ]

        self._transcript_files = {}
        self._transcript_cache = {}
        transcripts_dir = self.data_dir / "transcripts"
        if transcripts_dir.is_dir():
            for path in transcripts_dir.glob("transcript_*.json"):
                prefix = path.stem.removeprefix("transcript_")
                self._transcript_files.setdefault(prefix, []).append(path)

        self._loaded = True
        return self

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _read_json(self, filename: str, default: Any) -> Any:
        path = self.data_dir / filename
        if not path.exists():
            return default
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not read {path}: {e}")
            return default

    # -------------------------------------------------------------------------
    # Document Access
    # -------------------------------------------------------------------------

    @property
    def document_count(self) -> int:
        """Get the total number of documents."""
        self._ensure_loaded()
        return len(self._documents)

    @property
    def transcript_count(self) -> int:
        """Get the number of documents with a transcript file."""
        self._ensure_loaded()
        return sum(1 for d in self._documents if d["id"][:8] in self._transcript_files)

    @staticmethod
    def _panels_for(doc: dict) -> list[dict]:
        """Build Panel dicts from a document's notes and AI summary panel."""
        panels = []

        # The user's own notes. ProseMirror JSON is the richest form; the
        # markdown/plain fields are fallbacks for older documents, and
        # web-scraped shared docs carry HTML in ``notes``.
        notes = to_markdown(doc.get("notes"))
        if not notes:
            notes = (doc.get("notes_markdown") or doc.get("notes_plain") or "").strip()
        if notes:
            panels.append(
                {
                    "id": f"{doc['id']}-notes",
                    "type": "notes",
                    "title": "My Notes",
                    "content": notes,
                }
            )

        panel = doc.get("last_viewed_panel")
        if isinstance(panel, dict):
            content = to_markdown(panel.get("content")) or to_markdown(
                panel.get("original_content")
            )
            if content:
                panels.append(
                    {
                        "id": panel.get("id") or f"{doc['id']}-panel",
                        "type": panel.get("template_slug") or "summary",
                        "title": panel.get("title") or "Summary",
                        "content": content,
                    }
                )
        return panels

    def _document(self, doc: dict) -> Document:
        document = Document.from_dict(doc["id"], doc, self._panels_for(doc))
        document.is_shared = bool(doc.get("_shared")) or document.is_shared
        return document

    def get_document(self, doc_id: str) -> Document | None:
        """Get a specific document by ID."""
        self._ensure_loaded()
        for doc in self._documents:
            if doc["id"] == doc_id:
                return self._document(doc)
        return None

    def get_transcript(self, doc_id: str) -> Transcript | None:
        """Get a transcript by document ID (parsed on first access)."""
        self._ensure_loaded()
        if doc_id in self._transcript_cache:
            return self._transcript_cache[doc_id]

        transcript = None
        # Filenames only carry an 8-char ID prefix, so confirm the full ID.
        for path in self._transcript_files.get(doc_id[:8], []):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not read transcript {path}: {e}")
                continue
            if isinstance(data, dict) and data.get("document_id") == doc_id:
                transcript = Transcript.from_dict(doc_id, data.get("transcript"))
                break

        self._transcript_cache[doc_id] = transcript
        return transcript

    def get_meeting(self, doc_id: str) -> Meeting | None:
        """Get a complete meeting (document + transcript)."""
        doc = self.get_document(doc_id)
        if not doc:
            return None
        return Meeting(document=doc, transcript=self.get_transcript(doc_id))

    def documents(self) -> Iterator[Document]:
        """Iterate over all documents."""
        self._ensure_loaded()
        for doc in self._documents:
            yield self._document(doc)

    def transcripts(self) -> Iterator[Transcript]:
        """Iterate over all transcripts."""
        self._ensure_loaded()
        for doc in self._documents:
            transcript = self.get_transcript(doc["id"])
            if transcript:
                yield transcript

    def has_transcript(self, doc_id: str) -> bool:
        """Cheap check for a transcript file, without parsing it."""
        self._ensure_loaded()
        return doc_id[:8] in self._transcript_files

    def meetings(self, with_transcripts: bool = True) -> Iterator[Meeting]:
        """Iterate over all meetings.

        Args:
            with_transcripts: Parse and attach transcripts. Pass False when
                only document fields are needed; parsing every transcript
                dominates load time. Use has_transcript() for presence.
        """
        self._ensure_loaded()
        for doc in self._documents:
            yield Meeting(
                document=self._document(doc),
                transcript=(
                    self.get_transcript(doc["id"]) if with_transcripts else None
                ),
            )

    # -------------------------------------------------------------------------
    # People, Calendars, Workspaces, Folders
    # -------------------------------------------------------------------------

    def people(self) -> Iterator[Person]:
        """Iterate over people from people.json."""
        data = self._read_json("people.json", [])
        if isinstance(data, dict):
            data = data.get("people") or []
        for person in data if isinstance(data, list) else []:
            if isinstance(person, dict):
                yield Person.from_dict(person)

    def calendars(self) -> Iterator[Calendar]:
        """Iterate over calendars (only present in older exports)."""
        data = self._read_json("calendars.json", [])
        for cal in data if isinstance(data, list) else []:
            if isinstance(cal, dict):
                yield Calendar.from_dict(cal)

    def workspaces(self) -> Iterator[Workspace]:
        """Iterate over workspaces from workspaces.json."""
        data = self._read_json("workspaces.json", [])
        for entry in data if isinstance(data, list) else []:
            if not isinstance(entry, dict):
                continue
            # API shape: {"workspace": {...}, "role": ..., "plan_type": ...}
            nested = entry.get("workspace")
            ws: dict = nested if isinstance(nested, dict) else entry
            ws_id = ws.get("workspace_id") or ws.get("id") or ""
            yield Workspace.from_dict(
                ws_id, {**ws, "name": ws.get("display_name") or ws.get("name")}
            )

    def folders(self) -> Iterator[Folder]:
        """Iterate over folders from folders.json."""
        data = self._read_json("folders.json", [])
        for folder in data if isinstance(data, list) else []:
            if not isinstance(folder, dict) or not folder.get("id"):
                continue
            docs = folder.get("documents") or folder.get("document_ids") or []
            doc_ids = [d.get("id") if isinstance(d, dict) else d for d in docs]
            yield Folder.from_dict(folder["id"], folder, [d for d in doc_ids if d])

    def get_folder_for_document(self, doc_id: str) -> list[Folder]:
        """Get all folders that contain a specific document."""
        return [f for f in self.folders() if doc_id in f.document_ids]

    # -------------------------------------------------------------------------
    # Filtering, raw access, statistics
    # -------------------------------------------------------------------------

    def filter_meetings_by_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> Iterator[Meeting]:
        """Yield meetings created within [start_date, end_date]."""
        for meeting in self.meetings():
            if not meeting.created_at:
                continue
            if start_date and meeting.created_at < ensure_aware(start_date):
                continue
            if end_date and meeting.created_at > ensure_aware(end_date):
                continue
            yield meeting

    @property
    def raw_state(self) -> dict:
        """Raw documents as exported, for --include-raw."""
        self._ensure_loaded()
        return {"documents": self._documents}

    @property
    def manifest(self) -> dict:
        """The api-export manifest (export date, errors), or {}."""
        data = self._read_json(MANIFEST_FILENAME, {})
        return data if isinstance(data, dict) else {}

    def get_stats(self) -> dict:
        """Get statistics about the exported data."""
        self._ensure_loaded()

        total_words = 0
        with_transcripts = 0
        for transcript in self.transcripts():
            if transcript.full_text:
                with_transcripts += 1
                total_words += transcript.word_count

        return {
            "documents": self.document_count,
            "transcripts": self.transcript_count,
            "meetings_with_transcripts": with_transcripts,
            "folders": sum(1 for _ in self.folders()),
            "people": sum(1 for _ in self.people()),
            "calendars": sum(1 for _ in self.calendars()),
            "workspaces": sum(1 for _ in self.workspaces()),
            "total_transcript_words": total_words,
            "cache_path": str(self.data_dir),
        }
