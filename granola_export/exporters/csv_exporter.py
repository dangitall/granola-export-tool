"""
CSV exporter for Granola data.

Exports meeting data to CSV format for spreadsheet analysis,
reporting, and data processing workflows.
"""

import csv
from datetime import datetime
from pathlib import Path

from .base import BaseExporter
from ..models import ExportResult


class CSVExporter(BaseExporter):
    """
    Export Granola data to CSV format.

    Creates multiple CSV files:
    - meetings.csv: Core meeting metadata
    - panels.csv: Meeting notes/content panels
    - transcripts.csv: Transcript data (if included)
    - people.csv: Contact information
    """

    format_name = "csv"
    file_extension = ".csv"

    def export(self) -> ExportResult:
        """
        Export all data to CSV files.

        Returns:
            ExportResult with export details.
        """
        self.prepare_output_dir()
        errors = []

        docs_exported = 0
        trans_exported = 0

        # Collect data for CSV files
        meetings_rows = []
        panels_rows = []
        transcripts_rows = []

        for meeting in self.cache.meetings():
            try:
                # Meeting row
                meetings_rows.append({
                    "id": meeting.id,
                    "title": meeting.title,
                    "created_at": meeting.created_at.isoformat() if meeting.created_at else "",
                    "updated_at": (
                        meeting.document.updated_at.isoformat()
                        if meeting.document.updated_at
                        else ""
                    ),
                    "participants": "; ".join(meeting.document.participants),
                    "workspace_id": meeting.document.workspace_id or "",
                    "folder_id": meeting.document.folder_id or "",
                    "is_shared": meeting.document.is_shared,
                    "has_transcript": meeting.has_transcript,
                    "panel_count": len(meeting.document.panels),
                    "notes_word_count": len(meeting.document.notes_text.split()),
                    "transcript_word_count": (
                        meeting.transcript.word_count if meeting.has_transcript else 0
                    ),
                })

                # Panel rows
                for i, panel in enumerate(meeting.document.panels):
                    panels_rows.append({
                        "meeting_id": meeting.id,
                        "meeting_title": meeting.title,
                        "panel_id": panel.id,
                        "panel_order": i,
                        "panel_type": panel.panel_type,
                        "panel_title": panel.title,
                        "content": panel.content,
                        "word_count": len(panel.content.split()) if panel.content else 0,
                    })

                # Transcript rows
                if self.include_transcripts and meeting.has_transcript:
                    for seg in meeting.transcript.segments:
                        transcripts_rows.append({
                            "meeting_id": meeting.id,
                            "meeting_title": meeting.title,
                            "start_time": seg.start_time,
                            "end_time": seg.end_time,
                            "speaker": seg.speaker or "",
                            "text": seg.text,
                            "confidence": seg.confidence or "",
                        })
                    trans_exported += 1

                docs_exported += 1

            except Exception as e:
                errors.append(f"Error processing meeting {meeting.id}: {e}")

        # Write meetings CSV
        self._write_csv(
            self.output_dir / "meetings.csv",
            meetings_rows,
            [
                "id", "title", "created_at", "updated_at", "participants",
                "workspace_id", "folder_id", "is_shared", "has_transcript",
                "panel_count", "notes_word_count", "transcript_word_count",
            ],
        )

        # Write panels CSV
        self._write_csv(
            self.output_dir / "panels.csv",
            panels_rows,
            [
                "meeting_id", "meeting_title", "panel_id", "panel_order",
                "panel_type", "panel_title", "content", "word_count",
            ],
        )

        # Write transcripts CSV
        if self.include_transcripts and transcripts_rows:
            self._write_csv(
                self.output_dir / "transcripts.csv",
                transcripts_rows,
                [
                    "meeting_id", "meeting_title", "start_time", "end_time",
                    "speaker", "text", "confidence",
                ],
            )

        # Write people CSV
        people_rows = []
        for person in self.cache.people():
            people_rows.append({
                "id": person.id,
                "name": person.name,
                "email": person.email or "",
            })

        self._write_csv(
            self.output_dir / "people.csv",
            people_rows,
            ["id", "name", "email"],
        )

        # Write summary CSV
        stats = self.cache.get_stats()
        summary_rows = [
            {"metric": k, "value": str(v)}
            for k, v in stats.items()
        ]
        summary_rows.append({"metric": "export_date", "value": datetime.now().isoformat()})

        self._write_csv(
            self.output_dir / "summary.csv",
            summary_rows,
            ["metric", "value"],
        )

        return ExportResult(
            success=len(errors) == 0,
            output_path=str(self.output_dir),
            documents_exported=docs_exported,
            transcripts_exported=trans_exported,
            format="csv",
            errors=errors,
        )

    def _write_csv(
        self,
        filepath: Path,
        rows: list[dict],
        fieldnames: list[str],
    ) -> None:
        """Write rows to a CSV file."""
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
