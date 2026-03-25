# Granola Export Tool

A comprehensive CLI utility for exporting and analyzing meeting notes from the [Granola](https://granola.ai) app.

Extract your meetings, transcripts, and notes in multiple formats for backup, migration, or analysis.

## Features

- **Multiple Export Formats**
  - **JSON**: Structured data with full metadata
  - **Markdown**: Obsidian/Notion-compatible with YAML frontmatter
  - **CSV**: Spreadsheet-friendly for analysis
  - **HTML**: Self-contained searchable report

- **Direct API Access**
  - Export directly from Granola's servers
  - Discovers shared documents from API, local cache, and navigation history
  - Falls back to web scraping for link-shared documents the API can't return
  - Incremental sync mode (`--sync`) to only download new/changed meetings
  - Fetches transcripts, workspaces, folders, and contacts
  - Auto-detects API token from local Granola installation

- **Powerful Search**
  - Full-text search across titles, notes, and transcripts
  - Filter by date range, transcript availability
  - Regex support for advanced queries

- **Rich CLI Experience**
  - Colored terminal output
  - Progress indicators
  - Detailed statistics

## Installation

### From Source

```bash
git clone https://github.com/haasonsaas/granola-export-tool.git
cd granola-export-tool
pip install -e .
```

### Using pip (once published)

```bash
pip install granola-export
```

## Quick Start

```bash
# Check if Granola data is accessible
granola-export check

# Export all meetings to JSON
granola-export export

# Export to Markdown for Obsidian
granola-export export --format markdown --output ~/obsidian-vault/meetings

# List recent meetings
granola-export list --limit 10

# Search meetings
granola-export search "product roadmap"

# View statistics
granola-export stats
```

## Commands

### `export`

Export meetings to various formats.

```bash
granola-export export [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --format` | Export format: `json`, `markdown`, `csv`, `html` (default: json) |
| `-o, --output` | Output directory (default: ~/granola-export) |
| `--no-transcripts` | Exclude transcript data |
| `--include-raw` | Include original/raw data structures |

**Examples:**

```bash
# Export to Markdown with transcripts
granola-export export -f markdown -o ~/notes/meetings

# Export to CSV for spreadsheet analysis
granola-export export -f csv -o ~/analysis

# Generate HTML report
granola-export export -f html
```

### `list`

List all meetings with dates and transcript status.

```bash
granola-export list [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-n, --limit` | Maximum number of meetings to show |

### `search`

Search across meeting titles, notes, and transcripts.

```bash
granola-export search QUERY [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-n, --limit` | Maximum results (default: 20) |
| `--days` | Only search last N days |
| `--with-transcript` | Only meetings with transcripts |
| `--case-sensitive` | Case-sensitive matching |
| `--regex` | Treat query as regex |

**Examples:**

```bash
# Basic search
granola-export search "quarterly review"

# Search recent meetings only
granola-export search "budget" --days 30

# Regex search
granola-export search "sprint\s+\d+" --regex
```

### `stats`

Display statistics about your Granola data.

```bash
granola-export stats
```

Shows:
- Total documents and transcripts
- Word counts
- Date ranges
- Recent activity

### `show`

Display details of a specific meeting.

```bash
granola-export show MEETING_ID [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON |
| `--show-transcript` | Include full transcript |

**Examples:**

```bash
# Show meeting (partial ID works)
granola-export show abc123

# Export single meeting as JSON
granola-export show abc123 --json > meeting.json
```

### `check`

Verify Granola cache is accessible.

```bash
granola-export check
```

### `api-export`

Export directly from Granola's servers instead of local cache. This is useful for:
- Fetching shared documents not in your local cache
- Getting fresher data without waiting for sync
- Team-wide exports with proper API access

```bash
granola-export api-export [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output` | Output directory (default: ~/granola-api-export) |
| `--token` | API access token (default: auto-detect from local storage) |
| `--workspace` | Filter by workspace ID |
| `--no-transcripts` | Skip fetching transcripts |
| `--no-shared` | Skip fetching shared documents |
| `--sync` | Incremental sync: only download new/changed meetings |

**Examples:**

```bash
# Full API export with all data
granola-export api-export

# Quick export without transcripts
granola-export api-export --no-transcripts

# Export to specific directory
granola-export api-export -o ~/backup/granola-api

# Incremental sync (only new/changed since last run)
granola-export api-export --sync -o ~/granola

# Export with explicit token
granola-export api-export --token "your-api-token"
```

**Output Structure:**

```
granola-api-export/
├── all_meetings.json      # Combined export with all meetings
├── meetings/              # Individual meeting JSON files
├── transcripts/           # Transcript data per meeting
├── workspaces.json        # Workspace information
├── folders.json           # Document lists/folders
├── people.json            # Contacts and user data
└── manifest.json          # Export metadata and statistics
```

## Export Formats

### JSON

Creates structured JSON files:

```
granola-export/
├── manifest.json          # Export metadata
├── all_meetings.json      # Combined meetings
├── meetings/              # Individual meeting files
│   ├── Meeting_Title_abc123.json
│   └── ...
├── people.json
├── calendars.json
└── workspaces.json
```

### Markdown

Obsidian-compatible Markdown files with YAML frontmatter:

```
granola-export/
├── INDEX.md               # Meeting index with links
├── 2024-01-15_Meeting_Title.md
└── ...
```

Each file includes:
- YAML frontmatter (date, participants, tags)
- Formatted notes from all panels
- Collapsible transcript section

### CSV

Spreadsheet-friendly format:

```
granola-export/
├── meetings.csv           # Core meeting data
├── panels.csv             # Note content by panel
├── transcripts.csv        # Transcript segments
├── people.csv
└── summary.csv
```

### HTML

Single self-contained HTML file with:
- Searchable meeting list
- Expandable meeting details
- Statistics dashboard
- Print-friendly styles

## Data Location

Granola stores data locally:

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/Granola/cache-v6.json` |
| Windows | `%APPDATA%\Granola\cache-v6.json` |
| Linux | `~/.config/Granola/cache-v6.json` |

Use `--cache-path` to specify a custom location:

```bash
granola-export export --cache-path /path/to/cache-v6.json
```

## Python API

Use as a library in your own scripts:

```python
from granola_export import GranolaCache
from granola_export.search import MeetingSearcher, SearchQuery

# Load cache
cache = GranolaCache()
cache.load()

# Iterate meetings
for meeting in cache.meetings():
    print(f"{meeting.title} - {meeting.created_at}")
    if meeting.has_transcript:
        print(f"  Transcript: {meeting.transcript.word_count} words")

# Search
searcher = MeetingSearcher(cache)
query = SearchQuery(text="roadmap", has_transcript=True)
for result in searcher.search(query):
    print(f"Found: {result.title} ({result.match_type})")

# Export programmatically
from granola_export.exporters import MarkdownExporter
from pathlib import Path

exporter = MarkdownExporter(cache, Path("./output"))
result = exporter.export()
print(f"Exported {result.documents_exported} documents")
```

### API Client

Access Granola's REST API directly:

```python
from granola_export import GranolaAPIClient

# Auto-detect token from local Granola installation
client = GranolaAPIClient.from_local_token()

# Or use explicit token
client = GranolaAPIClient.from_token("your-api-token")

# Fetch all documents (with pagination)
for doc in client.get_all_documents():
    print(f"{doc['title']} - {doc['created_at']}")

# Fetch shared documents by ID
shared_docs = client.get_documents_batch(["doc-id-1", "doc-id-2"])

# Get transcript for a document
transcript = client.get_document_transcript("document-id")

# Fetch workspaces and folders
workspaces = client.get_workspaces()
folders = client.get_document_lists()
```

## Use Cases

### Corporate Migration

Export all meeting data for migration to another tool or for compliance archival:

```bash
granola-export export -f json --include-raw -o /backup/granola
```

### Knowledge Base Integration

Export to Markdown for Obsidian, Notion, or other knowledge bases:

```bash
granola-export export -f markdown -o ~/obsidian-vault/meetings
```

### Meeting Analytics

Export to CSV for analysis in Excel, Google Sheets, or pandas:

```bash
granola-export export -f csv -o ~/analysis
```

### Quick Search

Find that meeting where you discussed something:

```bash
granola-export search "competitor analysis" --days 90
```

## Development

```bash
# Clone and install in development mode
git clone https://github.com/haasonsaas/granola-export-tool.git
cd granola-export-tool
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black granola_export
ruff check granola_export

# Type checking
mypy granola_export
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Disclaimer

This tool reads Granola's local cache file for data extraction. It is not affiliated with or endorsed by Granola. Use responsibly and respect Granola's terms of service.

## Contributing

Contributions welcome! Please open an issue or PR.
