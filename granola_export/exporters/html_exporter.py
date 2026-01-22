"""
HTML exporter for Granola data.

Generates a self-contained HTML report with navigation,
search functionality, and a professional design.
"""

import html
import json
from datetime import datetime
from pathlib import Path

from .base import BaseExporter
from ..models import ExportResult, Meeting


class HTMLExporter(BaseExporter):
    """
    Export Granola data to a self-contained HTML report.

    Creates a single-page HTML application with:
    - Responsive design
    - Client-side search
    - Collapsible meeting sections
    - Print-friendly styles
    """

    format_name = "html"
    file_extension = ".html"

    def export(self) -> ExportResult:
        """
        Export all data to an HTML report.

        Returns:
            ExportResult with export details.
        """
        self.prepare_output_dir()
        errors = []

        docs_exported = 0
        trans_exported = 0

        # Collect meeting data
        meetings_data = []
        for meeting in sorted(
            self.cache.meetings(),
            key=lambda m: m.created_at or datetime.min,
            reverse=True,
        ):
            try:
                meetings_data.append(self._meeting_to_dict(meeting))
                docs_exported += 1
                if meeting.has_transcript:
                    trans_exported += 1
            except Exception as e:
                errors.append(f"Error processing meeting {meeting.id}: {e}")

        # Generate HTML
        stats = self.cache.get_stats()
        html_content = self._generate_html(meetings_data, stats)

        output_path = self.output_dir / "granola_export.html"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return ExportResult(
            success=len(errors) == 0,
            output_path=str(output_path),
            documents_exported=docs_exported,
            transcripts_exported=trans_exported,
            format="html",
            errors=errors,
        )

    def _meeting_to_dict(self, meeting: Meeting) -> dict:
        """Convert meeting to a dictionary for JSON embedding."""
        return {
            "id": meeting.id,
            "title": meeting.title,
            "date": meeting.created_at.isoformat() if meeting.created_at else None,
            "date_formatted": (
                meeting.created_at.strftime("%B %d, %Y at %I:%M %p")
                if meeting.created_at
                else "Unknown date"
            ),
            "participants": meeting.document.participants,
            "panels": [
                {
                    "title": p.title,
                    "type": p.panel_type,
                    "content": p.content,
                }
                for p in meeting.document.panels
            ],
            "has_transcript": meeting.has_transcript,
            "transcript_text": (
                meeting.transcript.full_text if meeting.has_transcript else ""
            ),
            "transcript_word_count": (
                meeting.transcript.word_count if meeting.has_transcript else 0
            ),
        }

    def _generate_html(self, meetings: list[dict], stats: dict) -> str:
        """Generate the complete HTML document."""
        meetings_json = json.dumps(meetings, ensure_ascii=False)

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Granola Export - Meeting Notes</title>
    <style>
        :root {{
            --bg-color: #f8f9fa;
            --card-bg: #ffffff;
            --text-color: #212529;
            --text-muted: #6c757d;
            --border-color: #dee2e6;
            --primary-color: #4f46e5;
            --primary-light: #eef2ff;
            --success-color: #10b981;
            --warning-color: #f59e0b;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            line-height: 1.6;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }}

        header {{
            text-align: center;
            margin-bottom: 2rem;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--border-color);
        }}

        header h1 {{
            font-size: 2.5rem;
            font-weight: 700;
            color: var(--primary-color);
            margin-bottom: 0.5rem;
        }}

        .subtitle {{
            color: var(--text-muted);
            font-size: 1.1rem;
        }}

        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem;
            margin: 2rem 0;
        }}

        .stat-card {{
            background: var(--card-bg);
            border-radius: 12px;
            padding: 1.5rem;
            text-align: center;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}

        .stat-value {{
            font-size: 2rem;
            font-weight: 700;
            color: var(--primary-color);
        }}

        .stat-label {{
            color: var(--text-muted);
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        .search-box {{
            margin: 2rem 0;
        }}

        .search-input {{
            width: 100%;
            padding: 1rem 1.5rem;
            font-size: 1rem;
            border: 2px solid var(--border-color);
            border-radius: 12px;
            outline: none;
            transition: border-color 0.2s, box-shadow 0.2s;
        }}

        .search-input:focus {{
            border-color: var(--primary-color);
            box-shadow: 0 0 0 3px var(--primary-light);
        }}

        .filter-bar {{
            display: flex;
            gap: 1rem;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
        }}

        .filter-btn {{
            padding: 0.5rem 1rem;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background: var(--card-bg);
            cursor: pointer;
            transition: all 0.2s;
        }}

        .filter-btn:hover, .filter-btn.active {{
            background: var(--primary-color);
            color: white;
            border-color: var(--primary-color);
        }}

        .meeting-list {{
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }}

        .meeting-card {{
            background: var(--card-bg);
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow: hidden;
            transition: box-shadow 0.2s;
        }}

        .meeting-card:hover {{
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}

        .meeting-header {{
            padding: 1.5rem;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
        }}

        .meeting-header:hover {{
            background: var(--bg-color);
        }}

        .meeting-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
        }}

        .meeting-meta {{
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            color: var(--text-muted);
            font-size: 0.9rem;
        }}

        .badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }}

        .badge-transcript {{
            background: #dcfce7;
            color: #166534;
        }}

        .expand-icon {{
            font-size: 1.5rem;
            color: var(--text-muted);
            transition: transform 0.2s;
        }}

        .meeting-card.expanded .expand-icon {{
            transform: rotate(180deg);
        }}

        .meeting-content {{
            display: none;
            padding: 0 1.5rem 1.5rem;
            border-top: 1px solid var(--border-color);
        }}

        .meeting-card.expanded .meeting-content {{
            display: block;
        }}

        .panel {{
            margin: 1.5rem 0;
        }}

        .panel-title {{
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--primary-color);
            margin-bottom: 0.75rem;
        }}

        .panel-content {{
            white-space: pre-wrap;
            background: var(--bg-color);
            padding: 1rem;
            border-radius: 8px;
            font-size: 0.95rem;
        }}

        .transcript-section {{
            margin-top: 1.5rem;
            padding-top: 1.5rem;
            border-top: 1px solid var(--border-color);
        }}

        .transcript-toggle {{
            padding: 0.75rem 1rem;
            background: var(--bg-color);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            cursor: pointer;
            width: 100%;
            text-align: left;
            font-size: 0.9rem;
        }}

        .transcript-text {{
            display: none;
            margin-top: 1rem;
            padding: 1rem;
            background: var(--bg-color);
            border-radius: 8px;
            max-height: 400px;
            overflow-y: auto;
            white-space: pre-wrap;
            font-size: 0.9rem;
        }}

        .transcript-text.visible {{
            display: block;
        }}

        .no-results {{
            text-align: center;
            padding: 3rem;
            color: var(--text-muted);
        }}

        footer {{
            text-align: center;
            margin-top: 3rem;
            padding-top: 2rem;
            border-top: 1px solid var(--border-color);
            color: var(--text-muted);
            font-size: 0.9rem;
        }}

        @media print {{
            .search-box, .filter-bar, .expand-icon, .transcript-toggle {{
                display: none;
            }}
            .meeting-content {{
                display: block !important;
            }}
            .meeting-card {{
                break-inside: avoid;
                box-shadow: none;
                border: 1px solid var(--border-color);
            }}
        }}

        @media (max-width: 640px) {{
            .container {{
                padding: 1rem;
            }}
            header h1 {{
                font-size: 1.75rem;
            }}
            .stats {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Granola Export</h1>
            <p class="subtitle">Meeting Notes & Transcripts</p>
        </header>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{stats.get('documents', 0)}</div>
                <div class="stat-label">Meetings</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('transcripts', 0)}</div>
                <div class="stat-label">Transcripts</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('total_transcript_words', 0):,}</div>
                <div class="stat-label">Words</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('people', 0)}</div>
                <div class="stat-label">People</div>
            </div>
        </div>

        <div class="search-box">
            <input type="text" class="search-input" placeholder="Search meetings..." id="searchInput">
        </div>

        <div class="filter-bar">
            <button class="filter-btn active" data-filter="all">All Meetings</button>
            <button class="filter-btn" data-filter="transcript">With Transcript</button>
        </div>

        <div class="meeting-list" id="meetingList"></div>

        <div class="no-results" id="noResults" style="display: none;">
            No meetings found matching your search.
        </div>

        <footer>
            <p>Exported on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
            <p>Generated by Granola Export Tool</p>
        </footer>
    </div>

    <script>
        const meetings = {meetings_json};

        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}

        function renderMeeting(meeting) {{
            const panels = meeting.panels.map(p => `
                <div class="panel">
                    ${{p.title ? `<h4 class="panel-title">${{escapeHtml(p.title)}}</h4>` : ''}}
                    <div class="panel-content">${{escapeHtml(p.content)}}</div>
                </div>
            `).join('');

            const transcript = meeting.has_transcript ? `
                <div class="transcript-section">
                    <button class="transcript-toggle" onclick="toggleTranscript(this)">
                        Show Transcript (${{meeting.transcript_word_count.toLocaleString()}} words)
                    </button>
                    <div class="transcript-text">${{escapeHtml(meeting.transcript_text)}}</div>
                </div>
            ` : '';

            return `
                <div class="meeting-card" data-has-transcript="${{meeting.has_transcript}}">
                    <div class="meeting-header" onclick="toggleMeeting(this)">
                        <div>
                            <h3 class="meeting-title">${{escapeHtml(meeting.title)}}</h3>
                            <div class="meeting-meta">
                                <span>${{meeting.date_formatted}}</span>
                                ${{meeting.has_transcript ? '<span class="badge badge-transcript">Transcript</span>' : ''}}
                            </div>
                        </div>
                        <span class="expand-icon">▼</span>
                    </div>
                    <div class="meeting-content">
                        ${{panels}}
                        ${{transcript}}
                    </div>
                </div>
            `;
        }}

        function toggleMeeting(header) {{
            header.parentElement.classList.toggle('expanded');
        }}

        function toggleTranscript(btn) {{
            const text = btn.nextElementSibling;
            text.classList.toggle('visible');
            btn.textContent = text.classList.contains('visible')
                ? 'Hide Transcript'
                : `Show Transcript (${{meetings.find(m => m.transcript_text === text.textContent)?.transcript_word_count.toLocaleString() || ''}} words)`;
        }}

        function renderMeetings(filteredMeetings) {{
            const list = document.getElementById('meetingList');
            const noResults = document.getElementById('noResults');

            if (filteredMeetings.length === 0) {{
                list.innerHTML = '';
                noResults.style.display = 'block';
            }} else {{
                noResults.style.display = 'none';
                list.innerHTML = filteredMeetings.map(renderMeeting).join('');
            }}
        }}

        // Search functionality
        let currentFilter = 'all';

        function filterMeetings() {{
            const query = document.getElementById('searchInput').value.toLowerCase();

            let filtered = meetings;

            if (currentFilter === 'transcript') {{
                filtered = filtered.filter(m => m.has_transcript);
            }}

            if (query) {{
                filtered = filtered.filter(m =>
                    m.title.toLowerCase().includes(query) ||
                    m.panels.some(p => p.content.toLowerCase().includes(query)) ||
                    m.transcript_text.toLowerCase().includes(query)
                );
            }}

            renderMeetings(filtered);
        }}

        document.getElementById('searchInput').addEventListener('input', filterMeetings);

        // Filter buttons
        document.querySelectorAll('.filter-btn').forEach(btn => {{
            btn.addEventListener('click', () => {{
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentFilter = btn.dataset.filter;
                filterMeetings();
            }});
        }});

        // Initial render
        renderMeetings(meetings);
    </script>
</body>
</html>'''
