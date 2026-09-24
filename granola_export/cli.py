#!/usr/bin/env python3
"""
Granola Export Tool CLI

A comprehensive command-line interface for exporting and analyzing
meeting notes and transcripts from the Granola app.

Usage:
    granola-export api-export --sync [-o DIR]   Download meetings from Granola
    granola-export export [--format FORMAT] [--output DIR]
    granola-export list [--limit N]
    granola-export search QUERY
    granola-export stats
    granola-export show MEETING_ID

Everything except api-export and auth reads the directory api-export
writes (see --data-dir); Granola's own local cache is now encrypted.
"""

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .exporters import AuthenticationError, get_exporter
from .models import MIN_DATETIME
from .paths import DATA_DIR_ENV, DEFAULT_DATA_DIRNAME, record_export_dir
from .search import MeetingSearcher, SearchQuery
from .sources import MeetingSource, open_source


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
    if os.getenv("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


_color_override: bool | None = None
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
    """Print a warning message to stderr. Never suppressed."""
    print(c(f"! {text}", Colors.YELLOW), file=sys.stderr)


def format_date(dt: datetime | None) -> str:
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
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return len(ansi_escape.sub("", text))


def pad_right(text: str, width: int) -> str:
    """Pad string to width accounting for ANSI codes."""
    visible = visible_len(text)
    return text + " " * max(0, width - visible)


def load_source(
    args: argparse.Namespace, spinner: str | None = None
) -> MeetingSource | None:
    """Open and load the meeting source for a read command.

    Returns the loaded source, or None after printing an error (the caller
    should exit 1).
    """
    source = open_source(data_dir=args.data_dir, cache_path=args.cache_path)
    try:
        if spinner:
            with Spinner(spinner):
                source.load()
        else:
            source.load()
    except FileNotFoundError as e:
        print_error(str(e))
        print_hint("Run 'granola-export check' to see where data is read from")
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print_error(f"Could not parse {source.source_path}: {e}")
        return None
    return source


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
        self._thread: threading.Thread | None = None
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


def _is_api_export_dir(path: Path) -> bool:
    """True if ``path`` holds api-export output (its manifest says so)."""
    try:
        with open(path / "manifest.json") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("export_format") == "api"


def cmd_export(args: argparse.Namespace) -> int:
    """Export Granola data to various formats."""
    print_header("Granola Export")

    cache = load_source(args, spinner="Loading meetings")
    if cache is None:
        return 1
    print_success(f"Loaded meetings from {cache.source_path}")

    # Get stats
    stats = cache.get_stats()
    print(f"Found {stats['documents']} documents, {stats['transcripts']} transcripts")
    print()

    # Prepare output directory. Refuse to write into any api-export
    # directory (the one being read, or any other, such as the cron job's):
    # the JSON exporter's all_meetings.json and manifest.json would
    # overwrite the sync's own files and reset its change tracking.
    output_dir = Path(args.output).expanduser()
    source_path = Path(cache.source_path)
    source_dir = source_path if source_path.is_dir() else source_path.parent
    if output_dir.resolve() == source_dir.resolve() or _is_api_export_dir(output_dir):
        print_error(f"Output directory {output_dir} holds an api-export")
        print_hint("Choose a different --output")
        return 1
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
    cache = load_source(args, spinner="Loading")
    if cache is None:
        return 1

    # Transcripts aren't needed to list meetings, and parsing them all
    # dominates load time; has_transcript() checks presence cheaply.
    meetings = sorted(
        cache.meetings(with_transcripts=False),
        key=lambda m: m.created_at or MIN_DATETIME,
        reverse=True,
    )

    if args.limit:
        meetings = meetings[: args.limit]

    # JSON output for scripting
    if getattr(args, "json", False):
        output = [
            {
                "id": m.id,
                "title": m.title,
                "date": m.created_at.isoformat() if m.created_at else None,
                "has_transcript": cache.has_transcript(m.id),
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
        print_hint("api-export hasn't downloaded any meetings yet")
        print_hint("--data-dir points at the wrong directory")
        print()
        print(f"Source: {cache.source_path}")
        return 0

    # Table header
    print(f"{'Date':<20} {'Title':<45} {'Trans':>6}")
    print(c("─" * 73, Colors.DIM))

    for meeting in meetings:
        date_str = format_date(meeting.created_at)
        title = truncate(meeting.title, 43)
        transcript = (
            c(" ✓", Colors.GREEN)
            if cache.has_transcript(meeting.id)
            else c(" -", Colors.DIM)
        )

        # Use pad_right for proper alignment with ANSI codes
        print(f"{date_str:<20} {title:<45}{transcript}")

    print(c("─" * 73, Colors.DIM))
    print(f"Total: {c(str(len(meetings)), Colors.CYAN)} meetings")

    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search meetings."""
    cache = load_source(args)
    if cache is None:
        return 1

    print_header(f"Search: {args.query}")

    if args.regex:
        try:
            re.compile(args.query)
        except re.error as e:
            print_error(f"Invalid regular expression: {e}")
            return 2

    # Build search query
    search_query = SearchQuery(
        text=args.query,
        has_transcript=True if args.with_transcript else None,
        case_sensitive=args.case_sensitive,
        regex=args.regex,
    )

    if args.days:
        search_query.date_from = datetime.now().astimezone() - timedelta(days=args.days)

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
    cache = load_source(args)
    if cache is None:
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
    print(c(f"Source: {stats['cache_path']}", Colors.DIM))

    # Recent activity
    print()
    print(c("Recent Activity:", Colors.BOLD))

    meetings = list(cache.meetings(with_transcripts=False))
    week_ago = datetime.now().astimezone() - timedelta(days=7)
    recent = [m for m in meetings if m.created_at and m.created_at >= week_ago]

    print(f"  Last 7 days: {len(recent)} meetings")

    if meetings:
        dates = [m.created_at for m in meetings if m.created_at]
        if dates:
            oldest = min(dates)
            newest = max(dates)
            print(
                f"  Date range: {oldest.strftime('%Y-%m-%d')} to {newest.strftime('%Y-%m-%d')}"
            )

    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Show details of a specific meeting."""
    cache = load_source(args)
    if cache is None:
        return 1

    # Find the meeting — collect all prefix matches so we can detect
    # ambiguity instead of silently returning the first hit. Transcripts
    # are attached below, only for the match.
    matches = []
    for m in cache.meetings(with_transcripts=False):
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
        print_error(
            f"Ambiguous ID prefix '{args.meeting_id}' matches {len(matches)} meetings:"
        )
        for m in matches[:10]:
            print(f"  {m.id[:12]}  {truncate(m.title, 50)}", file=sys.stderr)
        if len(matches) > 10:
            print(f"  ... and {len(matches) - 10} more", file=sys.stderr)
        print_hint("Provide more characters to narrow the match")
        return 1

    meeting = matches[0]
    meeting.transcript = cache.get_transcript(meeting.id)

    print_header(meeting.title)

    # Metadata
    print(f"{c('ID:', Colors.BOLD)} {meeting.id}")
    print(f"{c('Date:', Colors.BOLD)} {format_date(meeting.created_at)}")

    if meeting.document.participants:
        print(
            f"{c('Participants:', Colors.BOLD)} {', '.join(meeting.document.participants)}"
        )

    print(
        f"{c('Has Transcript:', Colors.BOLD)} {'Yes' if meeting.has_transcript else 'No'}"
    )

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

    from .api_client import AuthRefreshError, get_token_from_local
    from .exporters.api_exporter import APIExporter

    if args.output is None:
        env_dir = os.environ.get(DATA_DIR_ENV)
        args.output = Path(env_dir) if env_dir else Path.home() / DEFAULT_DATA_DIRNAME
    args.output = Path(args.output).expanduser()

    def _handle_refresh_error(e: AuthRefreshError) -> int:
        """Translate a refresh failure into a clean CLI exit.

        The ``revoked`` branch is unrecoverable from this side — the
        error message already tells the user to re-sign-in. Everything
        else (5xx, network blip, malformed body) is worth retrying.
        """
        print_error(str(e))
        if not e.revoked:
            print_hint("Transient refresh failure — try again in a moment")
        return 1

    # Check for token
    if not args.token:
        try:
            with Spinner("Checking for API token"):
                config = get_token_from_local()
        except AuthRefreshError as e:
            return _handle_refresh_error(e)
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
    except AuthRefreshError as e:
        return _handle_refresh_error(e)

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
    # Let list/search/show/stats/export find this directory without flags.
    # Only --sync runs count: that's the folder being kept current, and a
    # one-off export elsewhere shouldn't redirect the read commands.
    if args.sync:
        record_export_dir(args.output)

    if not _quiet:
        print()
        print(c(f"{op_name} completed in {elapsed:.1f}s", Colors.DIM))

    if args.sync and result.success:
        sync_stats = result.metadata.get("sync_statistics", {})
        if sync_stats:
            print_success(f"New meetings: {sync_stats.get('new', 0)}")
            print_success(f"Updated meetings: {sync_stats.get('updated', 0)}")
            if not _quiet:
                print(
                    f"  Unchanged (skipped): {c(str(sync_stats.get('skipped', 0)), Colors.DIM)}"
                )
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
            print(f"  ... and {len(result.errors) - 5} more errors", file=sys.stderr)

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
    """Check that the data the read commands use exists and is current."""
    source = open_source(data_dir=args.data_dir, cache_path=args.cache_path)
    legacy = args.cache_path is not None

    print_header("Granola Cache Check" if legacy else "Granola Export Check")
    print(f"Reading from: {source.source_path}")

    try:
        source.load()
    except FileNotFoundError as e:
        print_error(str(e).splitlines()[0])
        print()
        if legacy:
            print("Granola now encrypts its local cache (cache-v6.json.enc), so")
            print("--cache-path only works with an older plaintext cache file.")
        print("Download your meetings from Granola first:")
        print(f"  granola-export api-export --sync -o {source.source_path}")
        print_hint(f"Or point at an existing export with --data-dir or ${DATA_DIR_ENV}")
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print_error(f"Could not parse {source.source_path}: {e}")
        return 1

    print_success("Meetings loaded")
    print(f"\n  Documents:   {source.document_count}")
    print(f"  Transcripts: {source.transcript_count}")

    manifest = getattr(source, "manifest", None)
    if manifest is None:
        return 0

    exported = manifest.get("export_date")
    if exported:
        try:
            exported_at = datetime.fromisoformat(exported).astimezone()
            age = datetime.now().astimezone() - exported_at
            hours = age.total_seconds() / 3600
            print(f"  Last export: {format_date(exported_at)} ({hours:.1f}h ago)")
            if hours > 24:
                print_warning("Export is over a day old; is the sync still running?")
        except ValueError:
            print(f"  Last export: {exported}")

    errors = manifest.get("errors") or []
    if errors:
        print_warning(f"Last export reported {len(errors)} errors:")
        for error in errors[:5]:
            print(f"    {error}")
        return 1

    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    """Seed or inspect this tool's own persisted credential store.

    Recent Granola releases write auth tokens only to encrypted files whose
    key lives in a keychain we cannot read. This command lets you seed a
    refresh_token once; from then on exports mint access tokens from it
    without needing Granola's plaintext files.
    """
    from .api_client import (
        AuthRefreshError,
        _persist_credentials,
        refresh_access_token,
    )
    from .paths import get_credentials_path

    creds_path = get_credentials_path()

    if args.status:
        print_header("Granola Export Credentials")
        print(f"Store path: {creds_path}")
        if not creds_path.exists():
            print_error("No credentials stored")
            print_hint(
                "Seed one with: granola-export auth --refresh-token -  (reads stdin)"
            )
            return 1
        try:
            data = json.loads(creds_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print_error(f"Credential store unreadable: {e}")
            return 1
        has_refresh = bool(data.get("refresh_token"))
        has_access = bool(data.get("access_token"))
        print_success("Credential store present")
        print(f"  refresh_token: {'yes' if has_refresh else 'no'}")
        print(f"  access_token:  {'yes' if has_access else 'no'}")
        return 0 if has_refresh else 1

    if args.refresh_token == "-":
        # Read from stdin so the token stays out of shell history and `ps`.
        # Interactively, prompt without echo instead of waiting silently.
        if sys.stdin.isatty():
            import getpass

            args.refresh_token = getpass.getpass("Refresh token: ").strip()
        else:
            args.refresh_token = sys.stdin.readline().strip()
        if not args.refresh_token:
            print_error("No refresh token on stdin")
            return 1

    if not args.refresh_token:
        print_error("Nothing to do")
        print_hint(
            "Use --refresh-token - (reads stdin) to seed, or --status to inspect"
        )
        return 1

    print_header("Seeding Granola Export Credentials")
    # Verify the refresh_token works before persisting it — a bad token
    # stored silently would just fail on the next sync with no clue why.
    try:
        with Spinner("Verifying refresh token"):
            config = refresh_access_token(args.refresh_token)
    except AuthRefreshError as e:
        print_error(str(e))
        if e.revoked:
            print_hint("That refresh token has been revoked — grab a current one")
        return 1

    _persist_credentials(config)
    if not get_credentials_path().exists():
        print_error(f"Failed to write credential store at {creds_path}")
        return 1
    print_success(f"Refresh token verified and saved to {creds_path}")
    print_hint("Exports will now mint access tokens automatically")
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
Start by downloading your meetings (run it again, e.g. from cron, to sync):
  %(prog)s api-export --sync

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
        "--data-dir",
        type=Path,
        default=None,
        help=(
            "api-export output directory to read meetings from "
            f"(default: ${DATA_DIR_ENV}, else the last api-export -o, "
            f"else ~/{DEFAULT_DATA_DIRNAME})"
        ),
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help=(
            "Read a plaintext Granola cache file instead (older installs "
            "only; current Granola encrypts its cache)"
        ),
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress (DEBUG-level logging)",
    )
    verbosity.add_argument(
        "-q",
        "--quiet",
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
        "-f",
        "--format",
        choices=["json", "markdown", "md", "csv", "html"],
        default="json",
        help="Export format (default: json)",
    )
    export_parser.add_argument(
        "-o",
        "--output",
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
        "-n",
        "--limit",
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
        "-n",
        "--limit",
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
        help="Check that exported meetings exist and are up to date",
    )

    # API Export command
    api_parser = subparsers.add_parser(
        "api-export",
        help="Export directly from Granola API (includes shared docs)",
    )
    api_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=f"Output directory (default: ${DATA_DIR_ENV} or ~/{DEFAULT_DATA_DIRNAME})",
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

    # Auth command — seed/inspect our own persisted credential store.
    auth_parser = subparsers.add_parser(
        "auth",
        help="Seed a refresh token so exports work without Granola's plaintext files",
    )
    auth_parser.add_argument(
        "--refresh-token",
        type=str,
        default=None,
        help=(
            "Refresh token to verify and persist for future exports. Pass '-' "
            "to read it from stdin, keeping it out of shell history and ps"
        ),
    )
    auth_parser.add_argument(
        "--status",
        action="store_true",
        help="Show what is currently stored (no changes)",
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
        log_level = logging.INFO
        log_format = "%(asctime)s %(levelname)s %(message)s"
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
        "auth": cmd_auth,
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
