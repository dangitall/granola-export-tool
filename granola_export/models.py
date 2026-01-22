"""
Data models for Granola export.

These models provide structured access to Granola's meeting data,
transcripts, and associated metadata.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
import json


@dataclass
class Person:
    """Represents a meeting participant or contact."""

    id: str
    name: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Person":
        """Create a Person from a dictionary."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", data.get("displayName", "Unknown")),
            email=data.get("email"),
            avatar_url=data.get("avatarUrl"),
            raw_data=data,
        )


@dataclass
class TranscriptSegment:
    """A single segment of a transcript with timing information."""

    text: str
    start_time: float
    end_time: float
    speaker: Optional[str] = None
    confidence: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptSegment":
        """Create a TranscriptSegment from a dictionary."""
        # Handle various timestamp formats (startTime, start, start_timestamp)
        start_time = data.get("startTime", data.get("start", data.get("start_timestamp", 0)))
        end_time = data.get("endTime", data.get("end", data.get("end_timestamp", 0)))

        # Convert ISO timestamp strings to seconds if needed
        if isinstance(start_time, str):
            start_time = 0  # We'd need to parse ISO, but relative ordering matters more
        if isinstance(end_time, str):
            end_time = 0

        return cls(
            text=data.get("text", ""),
            start_time=float(start_time) if start_time else 0,
            end_time=float(end_time) if end_time else 0,
            speaker=data.get("speaker", data.get("source")),
            confidence=data.get("confidence"),
        )


@dataclass
class Transcript:
    """Full transcript for a meeting."""

    document_id: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    full_text: str = ""
    audio_source: Optional[str] = None
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, document_id: str, data: any) -> "Transcript":
        """Create a Transcript from a dictionary or list of segments."""
        segments = []
        raw_data = {}
        audio_source = None

        # Handle case where data is a list of segments directly
        if isinstance(data, list):
            raw_segments = data
            raw_data = {"segments": data}
        elif isinstance(data, dict):
            raw_segments = data.get("segments", data.get("transcript", []))
            raw_data = data
            audio_source = data.get("audioSource")
        else:
            raw_segments = []
            raw_data = {}

        # Parse segments
        for seg in raw_segments:
            if isinstance(seg, dict):
                segments.append(TranscriptSegment.from_dict(seg))

        # Build full text from segments or use provided text
        full_text = ""
        if isinstance(data, dict):
            full_text = data.get("text", "")
        if not full_text and segments:
            full_text = " ".join(seg.text for seg in segments)

        return cls(
            document_id=document_id,
            segments=segments,
            full_text=full_text,
            audio_source=audio_source,
            raw_data=raw_data,
        )

    @property
    def duration_seconds(self) -> float:
        """Calculate total duration from segments."""
        if not self.segments:
            return 0
        return max(seg.end_time for seg in self.segments)

    @property
    def word_count(self) -> int:
        """Count words in the transcript."""
        return len(self.full_text.split())


@dataclass
class Panel:
    """A panel/section within a meeting document (notes, action items, etc.)."""

    id: str
    panel_type: str
    title: str
    content: str
    order: int = 0
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Panel":
        """Create a Panel from a dictionary."""
        return cls(
            id=data.get("id", ""),
            panel_type=data.get("type", data.get("panelType", "unknown")),
            title=data.get("title", ""),
            content=data.get("content", data.get("text", "")),
            order=data.get("order", 0),
            raw_data=data,
        )


@dataclass
class Document:
    """A Granola meeting document containing notes and metadata."""

    id: str
    title: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    panels: list[Panel] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    calendar_event_id: Optional[str] = None
    workspace_id: Optional[str] = None
    folder_id: Optional[str] = None
    is_shared: bool = False
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, doc_id: str, data: dict, panels_data: list = None) -> "Document":
        """Create a Document from a dictionary."""
        # Parse timestamps (handle both camelCase and snake_case)
        created_at = None
        updated_at = None

        created_str = data.get("createdAt") or data.get("created_at")
        if created_str:
            try:
                if isinstance(created_str, (int, float)):
                    created_at = datetime.fromtimestamp(created_str / 1000)
                else:
                    created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        updated_str = data.get("updatedAt") or data.get("updated_at")
        if updated_str:
            try:
                if isinstance(updated_str, (int, float)):
                    updated_at = datetime.fromtimestamp(updated_str / 1000)
                else:
                    updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        # Parse panels
        panels = []
        if panels_data:
            for panel in panels_data:
                if isinstance(panel, dict):
                    panels.append(Panel.from_dict(panel))

        # Extract participants from people dict if present
        participants = data.get("participants", [])
        if not participants and "people" in data:
            people_data = data.get("people", {})
            if isinstance(people_data, dict):
                attendees = people_data.get("attendees", [])
                for att in attendees:
                    if isinstance(att, dict):
                        email = att.get("email", "")
                        details = att.get("details", {})
                        person = details.get("person", {}) if isinstance(details, dict) else {}
                        name_info = person.get("name", {}) if isinstance(person, dict) else {}
                        name = name_info.get("fullName", email) if isinstance(name_info, dict) else email
                        if name:
                            participants.append(name)

        return cls(
            id=doc_id,
            title=data.get("title", "Untitled Meeting"),
            created_at=created_at,
            updated_at=updated_at,
            panels=panels,
            participants=participants,
            calendar_event_id=data.get("calendarEventId") or data.get("calendar_event_id"),
            workspace_id=data.get("workspaceId") or data.get("workspace_id"),
            folder_id=data.get("folderId") or data.get("folder_id"),
            is_shared=data.get("isShared", data.get("is_shared", False)),
            raw_data=data,
        )

    @property
    def notes_text(self) -> str:
        """Get combined text from all panels."""
        return "\n\n".join(
            f"## {p.title}\n{p.content}" if p.title else p.content
            for p in self.panels
            if p.content
        )

    def to_dict(self) -> dict:
        """Convert to a dictionary for serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "panels": [
                {
                    "id": p.id,
                    "type": p.panel_type,
                    "title": p.title,
                    "content": p.content,
                }
                for p in self.panels
            ],
            "participants": self.participants,
            "calendar_event_id": self.calendar_event_id,
            "workspace_id": self.workspace_id,
            "folder_id": self.folder_id,
            "is_shared": self.is_shared,
        }


@dataclass
class Meeting:
    """Combined meeting data including document, transcript, and metadata."""

    document: Document
    transcript: Optional[Transcript] = None
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.document.id

    @property
    def title(self) -> str:
        return self.document.title

    @property
    def created_at(self) -> Optional[datetime]:
        return self.document.created_at

    @property
    def has_transcript(self) -> bool:
        return self.transcript is not None and bool(self.transcript.full_text)

    def to_dict(self) -> dict:
        """Convert to a dictionary for serialization."""
        result = self.document.to_dict()
        result["metadata"] = self.metadata

        if self.transcript:
            result["transcript"] = {
                "full_text": self.transcript.full_text,
                "word_count": self.transcript.word_count,
                "duration_seconds": self.transcript.duration_seconds,
                "segment_count": len(self.transcript.segments),
            }

        return result


@dataclass
class Workspace:
    """A Granola workspace containing folders and documents."""

    id: str
    name: str
    folders: list[dict] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, workspace_id: str, data: dict) -> "Workspace":
        """Create a Workspace from a dictionary."""
        return cls(
            id=workspace_id,
            name=data.get("name", "Default Workspace"),
            folders=data.get("folders", []),
            raw_data=data,
        )


@dataclass
class Calendar:
    """A connected calendar configuration."""

    id: str
    name: str
    provider: str
    email: Optional[str] = None
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Calendar":
        """Create a Calendar from a dictionary."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", data.get("summary", "Unknown Calendar")),
            provider=data.get("provider", "google"),
            email=data.get("email"),
            raw_data=data,
        )


@dataclass
class ExportResult:
    """Result of an export operation."""

    success: bool
    output_path: str
    documents_exported: int
    transcripts_exported: int
    format: str
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        status = "Success" if self.success else "Failed"
        return (
            f"{status}: Exported {self.documents_exported} documents, "
            f"{self.transcripts_exported} transcripts to {self.output_path}"
        )
