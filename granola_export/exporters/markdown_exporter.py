"""
Markdown exporter for Granola data.

Exports meetings as formatted Markdown files suitable for
note-taking apps like Obsidian, Notion, or GitHub.
"""

from datetime import datetime
from pathlib import Path
from textwrap import dedent

from .base import BaseExporter
from ..models import ExportResult, Meeting


class MarkdownExporter(BaseExporter):
    """
    Export Granola data to Markdown format.

    Creates individual Markdown files for each meeting with:
    - YAML frontmatter for metadata
    - Formatted meeting notes
    - Optional transcript sections
    - Obsidian-compatible linking
    """

    format_name = "markdown"
    file_extension = ".md"

    def __init__(self, *args, frontmatter: bool = True, **kwargs):
        """
        Initialize the Markdown exporter.

        Args:
            frontmatter: Whether to include YAML frontmatter.
            *args, **kwargs: Passed to BaseExporter.
        """
        super().__init__(*args, **kwargs)
        self.frontmatter = frontmatter

    def export(self) -> ExportResult:
        """
        Export all meetings to Markdown files.

        Returns:
            ExportResult with export details.
        """
        self.prepare_output_dir()
        errors = []

        docs_exported = 0
        trans_exported = 0

        # Create index file content
        index_entries = []

        for meeting in sorted(
            self.cache.meetings(),
            key=lambda m: m.created_at or datetime.min,
            reverse=True,
        ):
            try:
                content = self._meeting_to_markdown(meeting)

                # Generate filename
                date_prefix = ""
                if meeting.created_at:
                    date_prefix = meeting.created_at.strftime("%Y-%m-%d_")

                safe_title = self._safe_filename(meeting.title)
                filename = f"{date_prefix}{safe_title}.md"
                filepath = self.output_dir / filename

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

                # Add to index
                index_entries.append({
                    "title": meeting.title,
                    "date": meeting.created_at,
                    "filename": filename,
                    "has_transcript": meeting.has_transcript,
                })

                docs_exported += 1
                if meeting.has_transcript:
                    trans_exported += 1

            except Exception as e:
                errors.append(f"Error exporting meeting {meeting.id}: {e}")

        # Write index file
        index_content = self._generate_index(index_entries)
        with open(self.output_dir / "INDEX.md", "w", encoding="utf-8") as f:
            f.write(index_content)

        return ExportResult(
            success=len(errors) == 0,
            output_path=str(self.output_dir),
            documents_exported=docs_exported,
            transcripts_exported=trans_exported,
            format="markdown",
            errors=errors,
        )

    def _meeting_to_markdown(self, meeting: Meeting) -> str:
        """Convert a Meeting to Markdown format."""
        lines = []

        # YAML frontmatter
        if self.frontmatter:
            lines.append("---")
            lines.append(f"title: \"{meeting.title.replace('\"', '\\')}\"")
            if meeting.created_at:
                lines.append(f"date: {meeting.created_at.isoformat()}")
            if meeting.document.participants:
                lines.append(f"participants: {meeting.document.participants}")
            lines.append(f"id: {meeting.id}")
            lines.append(f"has_transcript: {meeting.has_transcript}")
            if meeting.document.workspace_id:
                lines.append(f"workspace_id: {meeting.document.workspace_id}")
            lines.append("tags: [granola, meeting]")
            lines.append("---")
            lines.append("")

        # Title
        lines.append(f"# {meeting.title}")
        lines.append("")

        # Metadata section
        if meeting.created_at:
            lines.append(f"**Date:** {meeting.created_at.strftime('%B %d, %Y at %I:%M %p')}")
            lines.append("")

        if meeting.document.participants:
            lines.append("**Participants:**")
            for p in meeting.document.participants:
                lines.append(f"- {p}")
            lines.append("")

        # Meeting notes from panels
        if meeting.document.panels:
            lines.append("---")
            lines.append("")

            for panel in meeting.document.panels:
                if panel.title:
                    lines.append(f"## {panel.title}")
                    lines.append("")

                if panel.content:
                    lines.append(panel.content)
                    lines.append("")

        # Transcript section
        if self.include_transcripts and meeting.has_transcript:
            lines.append("---")
            lines.append("")
            lines.append("## Transcript")
            lines.append("")

            transcript = meeting.transcript

            # Summary stats
            lines.append(f"*{transcript.word_count:,} words")
            if transcript.duration_seconds > 0:
                minutes = int(transcript.duration_seconds // 60)
                seconds = int(transcript.duration_seconds % 60)
                lines.append(f" | Duration: {minutes}m {seconds}s")
            lines.append("*")
            lines.append("")

            # Full text or segments
            if transcript.segments:
                lines.append("<details>")
                lines.append("<summary>Click to expand transcript</summary>")
                lines.append("")

                for seg in transcript.segments:
                    timestamp = self._format_timestamp(seg.start_time)
                    speaker = f"**{seg.speaker}:** " if seg.speaker else ""
                    lines.append(f"`[{timestamp}]` {speaker}{seg.text}")
                    lines.append("")

                lines.append("</details>")
            elif transcript.full_text:
                lines.append("<details>")
                lines.append("<summary>Click to expand transcript</summary>")
                lines.append("")
                lines.append(transcript.full_text)
                lines.append("")
                lines.append("</details>")

        return "\n".join(lines)

    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds as MM:SS or HH:MM:SS."""
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _generate_index(self, entries: list[dict]) -> str:
        """Generate the index file content."""
        lines = [
            "# Granola Meeting Notes",
            "",
            f"*Exported on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}*",
            "",
            f"**Total Meetings:** {len(entries)}",
            "",
            "---",
            "",
            "## Meetings by Date",
            "",
        ]

        # Group by month
        current_month = None
        for entry in entries:
            if entry["date"]:
                month_key = entry["date"].strftime("%Y-%m")
                month_label = entry["date"].strftime("%B %Y")
            else:
                month_key = "unknown"
                month_label = "Unknown Date"

            if month_key != current_month:
                current_month = month_key
                lines.append(f"### {month_label}")
                lines.append("")

            # Format entry
            date_str = ""
            if entry["date"]:
                date_str = entry["date"].strftime("%d") + " - "

            transcript_badge = " `transcript`" if entry["has_transcript"] else ""
            lines.append(f"- {date_str}[[{entry['filename'][:-3]}|{entry['title']}]]{transcript_badge}")

        lines.append("")
        return "\n".join(lines)
