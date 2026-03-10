#!/usr/bin/env python3
"""
Granola Export Tool CLI

A comprehensive command-line interface for exporting and analyzing
meeting notes and transcripts from the Granola app.

Usage:
    granola-export export [--format FORMAT] [--output DIR]
    granola-export list [--limit N]
    granola-export search QUERY
    granola-export stats
    granola-export show MEETING_ID
"""

import argparse
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import __version__
from .cache import GranolaCache, get_default_cache_path
from .exporters import get_exporter, AuthenticationError
from .search import MeetingSearcher, SearchQuery, quick_search


# ANSI color codes for terminal output
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def supports_color() -> bool:
    """Check if the terminal supports color."""
    import os

    if os.getenv("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


_color_override: Optional[bool] = None
_quiet: bool = False


def use_color() -> bool:
    """Check whether color output is enabled, respecting --no-color."""
    if _color_override is not None:
        return _color_override
    return supports_color()


def c(text: str, color: str) -> str:
    """Colorize text if colors are supported."""
    if use_color():
        return f"{color}{text}{Colors.RESET}"
    return text


def print_header(text: str) -> None:
    """Print a styled header. Suppressed by --quiet."""
    if _quiet:
        return
    print()
    print(c(f"{'─' * 60}", Colors.DIM))
    print(c(f"  {text}", Colors.BOLD + Colors.CYAN))
    print(c(f"{'─' * 60}", Colors.DIM))
    print()


def print_error(text: str) -> None:
    """Print an error message. Never suppressed."""
    print(c(f"Error: {text}", Colors.RED), file=sys.stderr)


def print_success(text: str) -> None:
    """Print a success message. Suppressed by --quiet."""
    if _quiet:
        return
    print(c(f"✓ {text}", Colors.GREEN))


def print_warning(text: str) -> None:
    """Print a warning message. Never suppressed."""
    print(c(f"! {text}", Colors.YELLOW))


def format_date(dt: Optional[datetime]) -> str:
    """Format a datetime for display."""
    if not dt:
        return "Unknown date"
    return dt.strftime("%Y-%m-%d %H:%M")


def truncate(text: str, max_length: int = 60) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def print_hint(text: str) -> None:
    """Print a hint/help message. Suppressed by --quiet."""
    if _quiet:
        return
    print(c(f"  → {text}", Colors.DIM))


def visible_len(text: str) -> int:
    """Return visible length of string (excluding ANSI codes)."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return len(ansi_escape.sub('', text))


def pad_right(text: str, width: int) -> str:
    """Pad string to width accounting for ANSI codes."""
    visible = visible_len(text)
    return text + " " * max(0, width - visible)


class Spinner:
    """Simple terminal spinner for long operations.

    Args:
        message: Text shown next to the spinner.
        show_elapsed: If True, print elapsed time when the spinner finishes.
    """

    def __init__(self, message: str = "Processing", show_elapsed: bool = False) -> None:
        self.message = message
        self._show_elapsed = show_elapsed
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start: float = 0

    def __enter__(self) -> "Spinner":
        self._start = time.monotonic()
        if _quiet or not sys.stdout.isatty():
            return self
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)
        elapsed = time.monotonic() - self._start
        if sys.stdout.isatty():
            sys.stdout.write("\r" + " " * (len(self.message) + 10) + "\r")
            sys.stdout.flush()
        if self._show_elapsed and not _quiet:
            print(c(f"{self.message} completed in {elapsed:.1f}s", Colors.DIM))

    def _spin(self) -> None:
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        idx = 0
        while self._running:
            frame = c(frames[idx % len(frames)], Colors.CYAN)
            sys.stdout.write(f"\r{frame} {self.message}")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.08)


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────


def cmd_export(args: argparse.Namespace) -> int:
    """Export Granola data to various formats."""
    print_header("Granola Export")

    # Load cache
    try:
        with Spinner("Loading cache"):
            cache = GranolaCache(args.cache_path)
            cache.load()
        print_success(f"Loaded cache from {cache.cache_path}")
    except FileNotFoundError as e:
        print_error(str(e))
        print_hint("Run 'granola-export check' to verify your Granola installation")
        return 1

    # Get stats
    stats = cache.get_stats()
    print(f"Found {stats['documents']} documents, {stats['transcripts']} transcripts")
    print()

    # Prepare output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get exporter
    try:
        exporter_class = get_exporter(args.format)
    except ValueError as e:
        print_error(str(e))
        return 1

    # Run export
    exporter = exporter_class(
        cache=cache,
        output_dir=output_dir,
        include_transcripts=not args.no_transcripts,
        include_raw=args.include_raw,
    )

    with Spinner(f"Exporting to {args.format.upper()}", show_elapsed=True):
        result = exporter.export()

    if not _quiet:
        print()
    if result.success:
        print_success(f"Exported {result.documents_exported} documents")
        print_success(f"Exported {result.transcripts_exported} transcripts")
        if not _quiet:
            print(f"\n📁 Output: {c(str(result.output_path), Colors.CYAN)}")
    else:
        print_warning(f"Export completed with {len(result.errors)} errors")
        for error in result.errors[:5]:
            print_error(error)
        if len(result.errors) > 5:
            print(f"  ... and {len(result.errors) - 5} more errors")

    return 0 if result.success else 1


def cmd_list(args: argparse.Namespace) -> int:
    """List all meetings."""
    try:
        with Spinner("Loading"):
            cache = GranolaCache(args.cache_path)
            cache.load()
    except FileNotFoundError as e:
        print_error(str(e))
        print_hint("Run 'granola-export check' to verify your Granola installation")
        return 1

    meetings = sorted(
        cache.meetings(),
        key=lambda m: m.created_at or datetime.min,
        reverse=True,
    )

    if args.limit:
        meetings = meetings[: args.limit]

    # JSON output for scripting
    if getattr(args, 'json', False):
        output = [
            {
                "id": m.id,
                "title": m.title,
                "date": m.created_at.isoformat() if m.created_at else None,
                "has_transcript": m.has_transcript,
            }
            for m in meetings
        ]
        print(json.dumps(output, indent=2))
        return 0

    print_header("Meetings")

    if not meetings:
        print(c("No meetings found.", Colors.DIM))
        print()
        print("This could mean:")
        print_hint("Granola hasn't synced any meetings yet")
        print_hint("The cache file is from a fresh install")
        print()
        print(f"Cache: {cache.cache_path}")
        return 0

    # Table header
    print(f"{'Date':<20} {'Title':<45} {'Trans':>6}")
    print(c("─" * 73, Colors.DIM))

    for meeting in meetings:
        date_str = format_date(meeting.created_at)
        title = truncate(meeting.title, 43)
        transcript = c(" ✓", Colors.GREEN) if meeting.has_transcript else c(" -", Colors.DIM)

        # Use pad_right for proper alignment with ANSI codes
        print(f"{date_str:<20} {title:<45}{transcript}")

    print(c("─" * 73, Colors.DIM))
    print(f"Total: {c(str(len(meetings)), Colors.CYAN)} meetings")

    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search meetings."""
    try:
        cache = GranolaCache(args.cache_path)
        cache.load()
    except FileNotFoundError as e:
        print_error(str(e))
        return 1

    print_header(f"Search: {args.query}")

    # Build search query
    search_query = SearchQuery(
        text=args.query,
        has_transcript=True if args.with_transcript else None,
        case_sensitive=args.case_sensitive,
        regex=args.regex,
    )

    if args.days:
        search_query.date_from = datetime.now() - timedelta(days=args.days)

    # Execute search
    searcher = MeetingSearcher(cache)
    results = list(searcher.search(search_query))

    if not results:
        print("No matching meetings found.")
        return 0

    # Sort by score
    results.sort(key=lambda r: r.score, reverse=True)

    if args.limit:
        results = results[: args.limit]

    for result in results:
        meeting = result.meeting
        date_str = format_date(meeting.created_at)

        # Match type indicator
        match_indicator = {
            "title": c("[title]", Colors.GREEN),
            "notes": c("[notes]", Colors.BLUE),
            "transcript": c("[transcript]", Colors.CYAN),
            "filter": c("[filter]", Colors.DIM),
        }.get(result.match_type, "")

        print(f"\n{c(meeting.title, Colors.BOLD)}")
        print(f"  {c(date_str, Colors.DIM)} {match_indicator}")

        if result.snippet and result.match_type != "filter":
            snippet = truncate(result.snippet.replace("\n", " "), 80)
            print(f"  {c('→', Colors.DIM)} {snippet}")

        print(f"  {c(f'ID: {meeting.id[:8]}...', Colors.DIM)}")

    print(f"\n{c(f'Found {len(results)} matching meetings', Colors.DIM)}")

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Show statistics about the Granola data."""
    try:
        cache = GranolaCache(args.cache_path)
        cache.load()
    except FileNotFoundError as e:
        print_error(str(e))
        return 1

    print_header("Granola Statistics")

    stats = cache.get_stats()

    # Format stats nicely
    stat_items = [
        ("Documents", stats["documents"]),
        ("Transcripts", stats["transcripts"]),
        ("With Transcripts", stats["meetings_with_transcripts"]),
        ("Folders", stats.get("folders", 0)),
        ("People", stats["people"]),
        ("Calendars", stats["calendars"]),
        ("Workspaces", stats["workspaces"]),
        ("Total Words (transcripts)", f"{stats['total_transcript_words']:,}"),
    ]

    max_label = max(len(item[0]) for item in stat_items)

    for label, value in stat_items:
        print(f"  {label:<{max_label + 2}} {c(str(value), Colors.CYAN)}")

    print()
    print(c(f"Cache: {stats['cache_path']}", Colors.DIM))

    # Recent activity
    print()
    print(c("Recent Activity:", Colors.BOLD))

    meetings = list(cache.meetings())
    recent = [
        m
        for m in meetings
        if m.created_at and m.created_at.replace(tzinfo=None) >= datetime.now() - timedelta(days=7)
    ]

    print(f"  Last 7 days: {len(recent)} meetings")

    if meetings:
        dates = [m.created_at for m in meetings if m.created_at]
        if dates:
            oldest = min(dates)
            newest = max(dates)
            print(f"  Date range: {oldest.strftime('%Y-%m-%d')} to {newest.strftime('%Y-%m-%d')}")

    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Show details of a specific meeting."""
    try:
        cache = GranolaCache(args.cache_path)
        cache.load()
    except FileNotFoundError as e:
        print_error(str(e))
        return 1

    # Find the meeting — collect all prefix matches so we can detect
    # ambiguity instead of silently returning the first hit.
    matches = []
    for m in cache.meetings():
        if m.id == args.meeting_id:
            # Exact match — use it immediately, no ambiguity possible.
            matches = [m]
            break
        if m.id.startswith(args.meeting_id):
            matches.append(m)

    if not matches:
        print_error(f"Meeting not found: {args.meeting_id}")
        return 1

    if len(matches) > 1:
        print_error(f"Ambiguous ID prefix '{args.meeting_id}' matches {len(matches)} meetings:")
        for m in matches[:10]:
            print(f"  {m.id[:12]}  {truncate(m.title, 50)}", file=sys.stderr)
        if len(matches) > 10:
            print(f"  ... and {len(matches) - 10} more", file=sys.stderr)
        print_hint("Provide more characters to narrow the match")
        return 1

    meeting = matches[0]

    print_header(meeting.title)

    # Metadata
    print(f"{c('ID:', Colors.BOLD)} {meeting.id}")
    print(f"{c('Date:', Colors.BOLD)} {format_date(meeting.created_at)}")

    if meeting.document.participants:
        print(f"{c('Participants:', Colors.BOLD)} {', '.join(meeting.document.participants)}")

    print(f"{c('Has Transcript:', Colors.BOLD)} {'Yes' if meeting.has_transcript else 'No'}")

    # Panels/Notes
    if meeting.document.panels:
        print()
        print(c("Notes:", Colors.BOLD))
        print(c("─" * 40, Colors.DIM))

        for panel in meeting.document.panels:
            if panel.title:
                print(f"\n{c(panel.title, Colors.CYAN)}")
            if panel.content:
                # Indent content
                for line in panel.content.split("\n"):
                    print(f"  {line}")

    # Transcript summary
    if meeting.has_transcript:
        print()
        print(c("Transcript:", Colors.BOLD))
        print(c("─" * 40, Colors.DIM))
        print(f"  Words: {meeting.transcript.word_count:,}")
        print(f"  Segments: {len(meeting.transcript.segments)}")

        if args.show_transcript:
            print()
            print(meeting.transcript.full_text)

    # JSON output
    if args.json:
        print()
        print(c("JSON:", Colors.BOLD))
        print(json.dumps(meeting.to_dict(), indent=2, default=str))

    return 0


def cmd_api_export(args: argparse.Namespace) -> int:
    """Export directly from Granola API."""
    if args.sync:
        print_header("Granola API Sync")
    else:
        print_header("Granola API Export")

    from .exporters.api_exporter import APIExporter
    from .api_client import get_token_from_local

    # Check for token
    if not args.token:
        with Spinner("Checking for API token"):
            config = get_token_from_local()
        if not config:
            print_error("No API token found")
            print_hint("Make sure Granola is installed and you're logged in")
            print_hint("Or provide a token with --token")
            return 1
        print_success("Found local API token")
    else:
        print_success("Using provided API token")

    if not _quiet:
        print(f"📁 Output: {c(str(args.output), Colors.CYAN)}")
        print()

    # Create exporter
    try:
        exporter = APIExporter(
            output_dir=args.output,
            access_token=args.token,
            include_transcripts=not args.no_transcripts,
            include_shared=not args.no_shared,
            workspace_id=args.workspace,
            sync_mode=args.sync,
        )
    except ValueError as e:
        print_error(str(e))
        return 1

    # Test connection
    with Spinner("Testing API connection"):
        connected = exporter.client.check_connection()
    if not connected:
        print_error("Failed to connect to Granola API")
        print_hint("Check your token and network connection")
        return 1
    print_success("API connection verified")
    print()

    # Run export (logging is configured in main() based on --verbose/--quiet)
    op_name = "API sync" if args.sync else "API export"
    start = time.monotonic()
    try:
        result = exporter.export()
    except AuthenticationError as e:
        print()
        print_error(str(e))
        print_hint("Try logging out and back in to Granola to refresh your token")
        return 1
    elapsed = time.monotonic() - start

    if not _quiet:
        print()
        print(c(f"{op_name} completed in {elapsed:.1f}s", Colors.DIM))

    if args.sync and result.success:
        sync_stats = result.metadata.get("sync_statistics", {})
        if sync_stats:
            print_success(f"New meetings: {sync_stats.get('new', 0)}")
            print_success(f"Updated meetings: {sync_stats.get('updated', 0)}")
            if not _quiet:
                print(f"  Unchanged (skipped): {c(str(sync_stats.get('skipped', 0)), Colors.DIM)}")
            print_success(f"Transcripts fetched: {result.transcripts_exported}")
        else:
            print_success(f"Exported {result.documents_exported} documents")
            print_success(f"Exported {result.transcripts_exported} transcripts")
    elif result.success:
        print_success(f"Exported {result.documents_exported} documents")
        print_success(f"Exported {result.transcripts_exported} transcripts")

    if not result.success:
        print_warning(f"Export completed with {len(result.errors)} errors")
        for error in result.errors[:5]:
            print_error(error)
        if len(result.errors) > 5:
            print(f"  ... and {len(result.errors) - 5} more errors",
                  file=sys.stderr)

    if not _quiet:
        # Summary
        print()
        print(c("📦 Export Contents:", Colors.BOLD))
        print(f"  {c('all_meetings.json', Colors.CYAN)}   Combined export")
        print(f"  {c('meetings/', Colors.CYAN)}           Individual meetings")
        print(f"  {c('transcripts/', Colors.CYAN)}        Transcript data")
        print(f"  {c('workspaces.json', Colors.CYAN)}     Workspace info")
        print(f"  {c('folders.json', Colors.CYAN)}        Folder structure")
        print(f"  {c('people.json', Colors.CYAN)}         Contacts")
        print(f"  {c('manifest.json', Colors.CYAN)}       Export metadata")

    return 0 if result.success else 1


def cmd_check(args: argparse.Namespace) -> int:
    """Check if Granola cache exists and is readable."""
    cache_path = args.cache_path or get_default_cache_path()

    print_header("Granola Cache Check")

    print(f"Expected path: {cache_path}")

    if not cache_path.exists():
        print_error("Cache file not found!")
        print()
        print("Make sure Granola is installed and has been run at least once.")
        print("The app stores its data at:")
        print(f"  macOS:   ~/Library/Application Support/Granola/cache-v3.json")
        print(f"  Windows: %APPDATA%\\Granola\\cache-v3.json")
        return 1

    print_success("Cache file found")

    # Try to load it
    try:
        cache = GranolaCache(cache_path)
        cache.load()
        print_success("Cache loaded successfully")

        stats = cache.get_stats()
        print(f"\n  Documents: {stats['documents']}")
        print(f"  Transcripts: {stats['transcripts']}")

    except json.JSONDecodeError:
        print_error("Cache file is corrupted or invalid JSON")
        return 1
    except Exception as e:
        print_error(f"Failed to load cache: {e}")
        return 1

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="granola-export",
        description="Export and analyze meeting notes from the Granola app.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s export                     Export to JSON (default)
  %(prog)s export -f markdown -o ~/notes  Export to Markdown
  %(prog)s list --limit 10            List recent 10 meetings
  %(prog)s search "product roadmap"   Search for meetings
  %(prog)s stats                      Show statistics
  %(prog)s show abc123                Show meeting details
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="Path to Granola cache file (default: auto-detect)",
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed progress (DEBUG-level logging)",
    )
    verbosity.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress non-essential output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Export command
    export_parser = subparsers.add_parser(
        "export",
        help="Export meetings to various formats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           Export to JSON (default)
  %(prog)s -f markdown -o ~/notes    Export to Markdown
  %(prog)s -f csv                    Export to CSV for spreadsheets
  %(prog)s -f html                   Generate searchable HTML report
        """,
    )
    export_parser.add_argument(
        "-f", "--format",
        choices=["json", "markdown", "md", "csv", "html"],
        default="json",
        help="Export format (default: json)",
    )
    export_parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path.home() / "granola-export",
        help="Output directory (default: ~/granola-export)",
    )
    export_parser.add_argument(
        "--no-transcripts",
        action="store_true",
        help="Exclude transcript data from export",
    )
    export_parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include raw/original data in export",
    )

    # List command
    list_parser = subparsers.add_parser(
        "list",
        help="List all meetings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    List all meetings
  %(prog)s -n 10              Show last 10 meetings
  %(prog)s --json             Output as JSON for scripting
        """,
    )
    list_parser.add_argument(
        "-n", "--limit",
        type=int,
        default=None,
        help="Limit number of results",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (for scripting)",
    )

    # Search command
    search_parser = subparsers.add_parser(
        "search",
        help="Search meetings",
    )
    search_parser.add_argument(
        "query",
        help="Search query",
    )
    search_parser.add_argument(
        "-n", "--limit",
        type=int,
        default=20,
        help="Limit number of results (default: 20)",
    )
    search_parser.add_argument(
        "--days",
        type=int,
        help="Only search meetings from last N days",
    )
    search_parser.add_argument(
        "--with-transcript",
        action="store_true",
        help="Only show meetings with transcripts",
    )
    search_parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Case-sensitive search",
    )
    search_parser.add_argument(
        "--regex",
        action="store_true",
        help="Treat query as regular expression",
    )

    # Stats command
    subparsers.add_parser(
        "stats",
        help="Show statistics about Granola data",
    )

    # Show command
    show_parser = subparsers.add_parser(
        "show",
        help="Show details of a specific meeting",
    )
    show_parser.add_argument(
        "meeting_id",
        help="Meeting ID (can be partial)",
    )
    show_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    show_parser.add_argument(
        "--show-transcript",
        action="store_true",
        help="Show full transcript text",
    )

    # Check command
    subparsers.add_parser(
        "check",
        help="Check if Granola cache is accessible",
    )

    # API Export command
    api_parser = subparsers.add_parser(
        "api-export",
        help="Export directly from Granola API (includes shared docs)",
    )
    api_parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path.home() / "granola-api-export",
        help="Output directory (default: ~/granola-api-export)",
    )
    api_parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="API access token (default: use local token)",
    )
    api_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Filter by workspace ID",
    )
    api_parser.add_argument(
        "--no-transcripts",
        action="store_true",
        help="Skip fetching transcripts",
    )
    api_parser.add_argument(
        "--no-shared",
        action="store_true",
        help="Skip fetching shared documents from folders",
    )
    api_parser.add_argument(
        "--sync",
        action="store_true",
        help="Incremental sync: only download new/changed meetings",
    )

    return parser


def main() -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Reset state on each invocation so repeated main() calls
    # (e.g. in tests) don't leak state from a previous run.
    global _color_override, _quiet
    _color_override = False if args.no_color else None
    _quiet = args.quiet

    # Configure logging on the package logger (not root) so we don't
    # clobber logging set up by test harnesses or embedding applications.
    if args.verbose:
        log_level = logging.DEBUG
        log_format = "  %(name)s: %(message)s"
    elif args.quiet:
        log_level = logging.WARNING
        log_format = "  %(message)s"
    else:
        log_level = logging.INFO
        log_format = "  %(message)s"
    pkg_logger = logging.getLogger("granola_export")
    pkg_logger.setLevel(log_level)
    if not pkg_logger.handlers:
        pkg_logger.addHandler(logging.StreamHandler())
    for h in pkg_logger.handlers:
        h.setFormatter(logging.Formatter(log_format))

    commands = {
        "export": cmd_export,
        "api-export": cmd_api_export,
        "list": cmd_list,
        "search": cmd_search,
        "stats": cmd_stats,
        "show": cmd_show,
        "check": cmd_check,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)

    # No subcommand provided — show help and exit with error (2 is the
    # conventional exit code for usage errors, matching argparse itself).
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
