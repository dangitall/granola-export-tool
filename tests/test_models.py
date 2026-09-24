"""Tests for data models."""

from granola_export.models import (
    Document,
    Meeting,
    Panel,
    Person,
    Transcript,
    TranscriptSegment,
)


class TestPerson:
    def test_from_dict(self):
        data = {
            "id": "person-123",
            "name": "John Doe",
            "email": "john@example.com",
            "avatarUrl": "https://example.com/avatar.jpg",
        }
        person = Person.from_dict(data)

        assert person.id == "person-123"
        assert person.name == "John Doe"
        assert person.email == "john@example.com"
        assert person.avatar_url == "https://example.com/avatar.jpg"

    def test_from_dict_minimal(self):
        data = {"id": "person-456"}
        person = Person.from_dict(data)

        assert person.id == "person-456"
        assert person.name == "Unknown"
        assert person.email is None


class TestTranscriptSegment:
    def test_from_dict(self):
        data = {
            "text": "Hello, world!",
            "startTime": 0.5,
            "endTime": 2.0,
            "speaker": "John",
            "confidence": 0.95,
        }
        segment = TranscriptSegment.from_dict(data)

        assert segment.text == "Hello, world!"
        assert segment.start_time == 0.5
        assert segment.end_time == 2.0
        assert segment.speaker == "John"
        assert segment.confidence == 0.95

    def test_from_dict_with_iso_timestamps(self):
        """ISO timestamp strings should be parsed, not silently zeroed."""
        data = {
            "text": "Hello",
            "startTime": "2024-06-15T10:00:00Z",
            "endTime": "2024-06-15T10:00:05Z",
            "speaker": "Alice",
        }
        segment = TranscriptSegment.from_dict(data)

        # Should preserve a non-zero value (exact value depends on epoch)
        assert segment.start_time > 0
        assert segment.end_time > segment.start_time
        # The gap should be ~5 seconds
        assert abs((segment.end_time - segment.start_time) - 5.0) < 0.01

    def test_from_dict_with_unparseable_string_timestamp(self):
        """Unparseable strings should fall back to 0 rather than crashing."""
        data = {
            "text": "Hello",
            "startTime": "not-a-timestamp",
            "endTime": "also-not-a-timestamp",
        }
        segment = TranscriptSegment.from_dict(data)
        assert segment.start_time == 0
        assert segment.end_time == 0


class TestTranscript:
    def test_from_dict_with_segments(self):
        data = {
            "segments": [
                {"text": "Hello", "startTime": 0, "endTime": 1},
                {"text": "World", "startTime": 1, "endTime": 2},
            ],
            "audioSource": "microphone",
        }
        transcript = Transcript.from_dict("doc-123", data)

        assert transcript.document_id == "doc-123"
        assert len(transcript.segments) == 2
        assert transcript.full_text == "Hello World"
        assert transcript.duration_seconds == 2
        assert transcript.word_count == 2

    def test_duration_seconds_with_iso_timestamps(self):
        """duration_seconds should be relative even with absolute ISO timestamps."""
        data = {
            "segments": [
                {
                    "text": "Hello",
                    "startTime": "2024-06-15T10:00:00Z",
                    "endTime": "2024-06-15T10:00:03Z",
                },
                {
                    "text": "World",
                    "startTime": "2024-06-15T10:00:03Z",
                    "endTime": "2024-06-15T10:00:10Z",
                },
            ],
        }
        transcript = Transcript.from_dict("doc-iso", data)
        # Total duration should be 10 seconds, not a billion-second epoch value
        assert abs(transcript.duration_seconds - 10.0) < 0.01

    def test_from_dict_with_text(self):
        data = {
            "text": "This is the full transcript text.",
            "segments": [],
        }
        transcript = Transcript.from_dict("doc-456", data)

        assert transcript.full_text == "This is the full transcript text."
        assert transcript.word_count == 6


class TestPanel:
    def test_from_dict(self):
        data = {
            "id": "panel-123",
            "type": "notes",
            "title": "Meeting Notes",
            "content": "Some content here",
            "order": 1,
        }
        panel = Panel.from_dict(data)

        assert panel.id == "panel-123"
        assert panel.panel_type == "notes"
        assert panel.title == "Meeting Notes"
        assert panel.content == "Some content here"
        assert panel.order == 1


class TestDocument:
    def test_from_dict(self):
        data = {
            "title": "Test Meeting",
            "createdAt": "2024-01-15T10:00:00Z",
            "participants": ["Alice", "Bob"],
            "workspaceId": "ws-123",
            "isShared": False,
        }
        panels_data = [
            {"id": "p1", "type": "notes", "title": "Notes", "content": "Content"},
        ]
        doc = Document.from_dict("doc-123", data, panels_data)

        assert doc.id == "doc-123"
        assert doc.title == "Test Meeting"
        assert len(doc.participants) == 2
        assert len(doc.panels) == 1
        assert doc.workspace_id == "ws-123"

    def test_notes_text(self):
        data = {"title": "Test"}
        panels_data = [
            {"id": "p1", "type": "notes", "title": "Section 1", "content": "Content 1"},
            {"id": "p2", "type": "notes", "title": "Section 2", "content": "Content 2"},
        ]
        doc = Document.from_dict("doc-123", data, panels_data)

        notes = doc.notes_text
        assert "## Section 1" in notes
        assert "Content 1" in notes
        assert "## Section 2" in notes
        assert "Content 2" in notes

    def test_to_dict(self):
        data = {"title": "Test Meeting"}
        doc = Document.from_dict("doc-123", data, [])
        result = doc.to_dict()

        assert result["id"] == "doc-123"
        assert result["title"] == "Test Meeting"
        assert "panels" in result


class TestMeeting:
    def test_with_transcript(self):
        doc = Document.from_dict("doc-123", {"title": "Meeting"}, [])
        transcript = Transcript.from_dict(
            "doc-123",
            {"text": "Hello world", "segments": []},
        )
        meeting = Meeting(document=doc, transcript=transcript)

        assert meeting.id == "doc-123"
        assert meeting.title == "Meeting"
        assert meeting.has_transcript is True

    def test_without_transcript(self):
        doc = Document.from_dict("doc-123", {"title": "Meeting"}, [])
        meeting = Meeting(document=doc, transcript=None)

        assert meeting.has_transcript is False

    def test_to_dict(self):
        doc = Document.from_dict("doc-123", {"title": "Meeting"}, [])
        transcript = Transcript.from_dict(
            "doc-123",
            {"text": "Hello world", "segments": []},
        )
        meeting = Meeting(
            document=doc, transcript=transcript, metadata={"key": "value"}
        )
        result = meeting.to_dict()

        assert result["id"] == "doc-123"
        assert result["metadata"] == {"key": "value"}
        assert result["transcript"]["word_count"] == 2


class TestDatetimeParsing:
    """All model dates are timezone-aware so they can be compared."""

    def test_epoch_ms_and_iso_are_both_aware_and_sortable(self):
        from granola_export.models import MIN_DATETIME

        docs = [
            Document.from_dict("a", {"created_at": "2026-01-01T10:00:00Z"}),
            Document.from_dict("b", {"createdAt": 1767261600000}),
            Document.from_dict("c", {}),
        ]

        assert docs[0].created_at.tzinfo is not None
        assert docs[1].created_at.tzinfo is not None
        ordered = sorted(docs, key=lambda d: d.created_at or MIN_DATETIME)
        assert [d.id for d in ordered] == ["c", "a", "b"]

    def test_naive_iso_is_read_as_utc(self):
        from datetime import UTC, datetime

        doc = Document.from_dict("a", {"created_at": "2026-01-01T10:00:00"})

        assert doc.created_at == datetime(2026, 1, 1, 10, tzinfo=UTC)

    def test_garbage_timestamp_is_none(self):
        assert Document.from_dict("a", {"created_at": "not a date"}).created_at is None
