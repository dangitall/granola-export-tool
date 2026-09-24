"""
Where the read commands get meetings from.

Two sources satisfy :class:`MeetingSource`:

- :class:`~granola_export.export_store.ExportStore` reads the directory
  ``api-export`` maintains. This is the default: Granola now encrypts its
  local cache.
- :class:`~granola_export.cache.GranolaCache` reads a plaintext Granola cache
  file, for older installs or saved copies (``--cache-path``).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from .cache import GranolaCache
from .export_store import ExportStore
from .models import Calendar, Folder, Meeting, Person, Transcript, Workspace
from .paths import get_default_data_dir


class MeetingSource(Protocol):
    """The read interface exporters, search, and the CLI rely on."""

    @property
    def source_path(self) -> Path: ...

    @property
    def is_loaded(self) -> bool: ...

    @property
    def raw_state(self) -> dict: ...

    def load(self) -> Any: ...

    def meetings(self, with_transcripts: bool = True) -> Iterator[Meeting]: ...

    def has_transcript(self, doc_id: str) -> bool: ...

    def get_transcript(self, doc_id: str) -> Transcript | None: ...

    @property
    def document_count(self) -> int: ...

    @property
    def transcript_count(self) -> int: ...

    def people(self) -> Iterator[Person]: ...

    def calendars(self) -> Iterator[Calendar]: ...

    def workspaces(self) -> Iterator[Workspace]: ...

    def folders(self) -> Iterator[Folder]: ...

    def get_stats(self) -> dict: ...


def open_source(
    data_dir: Path | None = None, cache_path: Path | None = None
) -> MeetingSource:
    """Return the (unloaded) source for the given CLI options.

    An explicit ``cache_path`` selects the legacy cache reader; otherwise
    the api-export directory (``data_dir`` or the resolved default).
    """
    if cache_path:
        return GranolaCache(cache_path)
    return ExportStore(data_dir or get_default_data_dir())
