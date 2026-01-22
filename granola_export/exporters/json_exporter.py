"""
JSON exporter for Granola data.

Exports meetings, documents, and transcripts to structured JSON files.
"""

import json
from datetime import datetime
from pathlib import Path

from .base import BaseExporter
from ..models import ExportResult


class JSONExporter(BaseExporter):
    """
    Export Granola data to JSON format.

    Creates a structured export with:
    - Individual meeting files in a meetings/ subdirectory
    - A combined all_meetings.json file
    - A manifest.json with export metadata
    - Optional full raw state backup
    """

    format_name = "json"
    file_extension = ".json"

    def export(self) -> ExportResult:
        """
        Export all data to JSON files.

        Returns:
            ExportResult with export details.
        """
        self.prepare_output_dir()
        errors = []

        # Create subdirectories
        meetings_dir = self.output_dir / "meetings"
        meetings_dir.mkdir(exist_ok=True)

        # Export individual meetings
        all_meetings = []
        docs_exported = 0
        trans_exported = 0

        for meeting in self.cache.meetings():
            try:
                meeting_data = self._meeting_to_dict(meeting)
                all_meetings.append(meeting_data)

                # Write individual file
                safe_title = self._safe_filename(meeting.title)
                filename = f"{safe_title}_{meeting.id[:8]}.json"
                filepath = meetings_dir / filename

                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(meeting_data, f, indent=2, ensure_ascii=False)

                docs_exported += 1
                if meeting.has_transcript:
                    trans_exported += 1

            except Exception as e:
                errors.append(f"Error exporting meeting {meeting.id}: {e}")

        # Write combined file
        combined_path = self.output_dir / "all_meetings.json"
        with open(combined_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "export_date": datetime.now().isoformat(),
                    "total_meetings": len(all_meetings),
                    "meetings": all_meetings,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        # Export people
        people_data = [p.raw_data for p in self.cache.people()]
        with open(self.output_dir / "people.json", "w", encoding="utf-8") as f:
            json.dump(people_data, f, indent=2, ensure_ascii=False)

        # Export calendars
        calendars_data = [c.raw_data for c in self.cache.calendars()]
        with open(self.output_dir / "calendars.json", "w", encoding="utf-8") as f:
            json.dump(calendars_data, f, indent=2, ensure_ascii=False)

        # Export workspaces
        workspaces_data = [
            {"id": w.id, "name": w.name, "folders": w.folders}
            for w in self.cache.workspaces()
        ]
        with open(self.output_dir / "workspaces.json", "w", encoding="utf-8") as f:
            json.dump(workspaces_data, f, indent=2, ensure_ascii=False)

        # Optional raw backup
        if self.include_raw:
            with open(self.output_dir / "raw_state.json", "w", encoding="utf-8") as f:
                json.dump(self.cache.raw_state, f, indent=2, default=str)

        # Write manifest
        manifest = {
            "export_format": "json",
            "export_date": datetime.now().isoformat(),
            "source_path": str(self.cache.cache_path),
            "statistics": self.cache.get_stats(),
            "files": {
                "meetings_directory": str(meetings_dir),
                "combined_meetings": str(combined_path),
                "people": str(self.output_dir / "people.json"),
                "calendars": str(self.output_dir / "calendars.json"),
                "workspaces": str(self.output_dir / "workspaces.json"),
            },
            "errors": errors,
        }

        with open(self.output_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return ExportResult(
            success=len(errors) == 0,
            output_path=str(self.output_dir),
            documents_exported=docs_exported,
            transcripts_exported=trans_exported,
            format="json",
            errors=errors,
        )

    def _meeting_to_dict(self, meeting) -> dict:
        """Convert a Meeting to a serializable dictionary."""
        result = meeting.to_dict()

        if self.include_transcripts and meeting.transcript:
            result["transcript_full"] = {
                "text": meeting.transcript.full_text,
                "segments": [
                    {
                        "text": seg.text,
                        "start": seg.start_time,
                        "end": seg.end_time,
                        "speaker": seg.speaker,
                    }
                    for seg in meeting.transcript.segments
                ],
            }

        if self.include_raw:
            result["raw_document"] = meeting.document.raw_data
            if meeting.transcript:
                result["raw_transcript"] = meeting.transcript.raw_data

        return result
