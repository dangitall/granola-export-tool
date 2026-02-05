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


def _parse_timestamp_string(value: str) -> float:
    """
    Best-effort parse of a timestamp string into epoch seconds.

    Handles ISO 8601 strings (e.g. from the Granola API).  Returns 0
    on failure so callers never crash on unexpected formats.
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0


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

        # Convert ISO timestamp strings to epoch seconds so timing info
        # is preserved for duration calculations and display.
        if isinstance(start_time, str):
            start_time = _parse_timestamp_string(start_time)
        if isinstance(end_time, str):
            end_time = _parse_timestamp_string(end_time)

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
    def from_dict(cls, document_id: str, data: Any) -> "Transcript":
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
        """Calculate total duration from segments.

        Works correctly whether timestamps are relative (seconds from
        start of recording) or absolute (epoch seconds from ISO parsing).
        """
        if not self.segments:
            return 0
        end = max(seg.end_time for seg in self.segments)
        start = min(seg.start_time for seg in self.segments)
        return end - start

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
class Attendee:
    """Detailed attendee information with company and social links."""

    email: str
    name: str
    company: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    avatar_url: Optional[str] = None
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Attendee":
        """Create an Attendee from Granola's people.attendees format."""
        email = data.get("email", "")
        details = data.get("details", {})
        person = details.get("person", {}) if isinstance(details, dict) else {}
        company_info = details.get("company", {}) if isinstance(details, dict) else {}

        name_info = person.get("name", {}) if isinstance(person, dict) else {}
        employment = person.get("employment", {}) if isinstance(person, dict) else {}
        linkedin = person.get("linkedin", {}) if isinstance(person, dict) else {}

        return cls(
            email=email,
            name=name_info.get("fullName", email) if isinstance(name_info, dict) else email,
            company=employment.get("name") or company_info.get("name"),
            title=employment.get("title"),
            linkedin_url=f"https://linkedin.com/{linkedin.get('handle')}" if linkedin.get("handle") else None,
            avatar_url=person.get("avatar"),
            raw_data=data,
        )


@dataclass
class CalendarEvent:
    """Google Calendar event details."""

    id: str
    summary: str
    location: Optional[str] = None
    description: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    html_link: Optional[str] = None
    organizer_email: Optional[str] = None
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "CalendarEvent":
        """Create a CalendarEvent from Granola's google_calendar_event format."""
        start_time = None
        end_time = None

        start_data = data.get("start", {})
        end_data = data.get("end", {})

        if start_data.get("dateTime"):
            try:
                start_time = datetime.fromisoformat(start_data["dateTime"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        if end_data.get("dateTime"):
            try:
                end_time = datetime.fromisoformat(end_data["dateTime"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        organizer = data.get("organizer", {})

        return cls(
            id=data.get("id", ""),
            summary=data.get("summary", ""),
            location=data.get("location"),
            description=data.get("description"),
            start_time=start_time,
            end_time=end_time,
            html_link=data.get("htmlLink"),
            organizer_email=organizer.get("email") if isinstance(organizer, dict) else None,
            raw_data=data,
        )


@dataclass
class Folder:
    """A Granola folder/document list."""

    id: str
    title: str
    description: Optional[str] = None
    icon: Optional[dict] = None
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    is_favourited: bool = False
    is_shared: bool = False
    visibility: str = "private"
    members: list[dict] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, folder_id: str, metadata: dict, document_ids: list = None) -> "Folder":
        """Create a Folder from metadata and document list."""
        return cls(
            id=folder_id,
            title=metadata.get("title", "Untitled Folder"),
            description=metadata.get("description"),
            icon=metadata.get("icon"),
            workspace_id=metadata.get("workspace_id"),
            workspace_name=metadata.get("workspace_display_name"),
            is_favourited=metadata.get("is_favourited", False),
            is_shared=metadata.get("is_shared", False),
            visibility=metadata.get("visibility", "private"),
            members=metadata.get("members", []),
            document_ids=document_ids or [],
            raw_data=metadata,
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
    attendees: list[Attendee] = field(default_factory=list)
    calendar_event: Optional[CalendarEvent] = None
    calendar_event_id: Optional[str] = None
    workspace_id: Optional[str] = None
    folder_id: Optional[str] = None
    is_shared: bool = False
    # Rich content fields
    notes_markdown: Optional[str] = None
    notes_plain: Optional[str] = None
    summary: Optional[str] = None
    overview: Optional[str] = None
    # CRM integration links
    hubspot_note_url: Optional[str] = None
    affinity_note_id: Optional[str] = None
    # Metadata
    creation_source: Optional[str] = None
    attachments: list[dict] = field(default_factory=list)
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

        # Extract participants and detailed attendees from people dict
        participants = data.get("participants", [])
        attendees = []
        people_data = data.get("people", {})
        if isinstance(people_data, dict):
            attendees_data = people_data.get("attendees", [])
            for att in attendees_data:
                if isinstance(att, dict):
                    attendee = Attendee.from_dict(att)
                    attendees.append(attendee)
                    if attendee.name and attendee.name not in participants:
                        participants.append(attendee.name)

        # Parse calendar event
        calendar_event = None
        cal_event_data = data.get("google_calendar_event")
        if isinstance(cal_event_data, dict):
            calendar_event = CalendarEvent.from_dict(cal_event_data)

        return cls(
            id=doc_id,
            title=data.get("title") or "Untitled Meeting",
            created_at=created_at,
            updated_at=updated_at,
            panels=panels,
            participants=participants,
            attendees=attendees,
            calendar_event=calendar_event,
            calendar_event_id=data.get("calendarEventId") or data.get("calendar_event_id"),
            workspace_id=data.get("workspaceId") or data.get("workspace_id"),
            folder_id=data.get("folderId") or data.get("folder_id"),
            is_shared=data.get("isShared", data.get("is_shared", False)),
            # Rich content
            notes_markdown=data.get("notes_markdown"),
            notes_plain=data.get("notes_plain"),
            summary=data.get("summary"),
            overview=data.get("overview"),
            # CRM links
            hubspot_note_url=data.get("hubspot_note_url"),
            affinity_note_id=data.get("affinity_note_id"),
            # Metadata
            creation_source=data.get("creation_source"),
            attachments=data.get("attachments", []),
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
        result = {
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
            "attendees": [
                {
                    "email": a.email,
                    "name": a.name,
                    "company": a.company,
                    "title": a.title,
                    "linkedin_url": a.linkedin_url,
                    "avatar_url": a.avatar_url,
                }
                for a in self.attendees
            ],
            "calendar_event_id": self.calendar_event_id,
            "workspace_id": self.workspace_id,
            "folder_id": self.folder_id,
            "is_shared": self.is_shared,
            "notes_markdown": self.notes_markdown,
            "notes_plain": self.notes_plain,
            "summary": self.summary,
            "overview": self.overview,
            "creation_source": self.creation_source,
            "attachments": self.attachments,
        }

        # Add calendar event details if present
        if self.calendar_event:
            result["calendar_event"] = {
                "id": self.calendar_event.id,
                "summary": self.calendar_event.summary,
                "location": self.calendar_event.location,
                "description": self.calendar_event.description,
                "start_time": self.calendar_event.start_time.isoformat() if self.calendar_event.start_time else None,
                "end_time": self.calendar_event.end_time.isoformat() if self.calendar_event.end_time else None,
                "html_link": self.calendar_event.html_link,
                "organizer_email": self.calendar_event.organizer_email,
            }

        # Add CRM links if present
        if self.hubspot_note_url or self.affinity_note_id:
            result["crm_links"] = {
                "hubspot_note_url": self.hubspot_note_url,
                "affinity_note_id": self.affinity_note_id,
            }

        return result


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
