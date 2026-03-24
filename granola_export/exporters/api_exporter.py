"""
API-based exporter for Granola data.

Fetches data directly from Granola's servers instead of the local cache.
Useful for shared documents and team-wide exports.
"""

import json
import logging
import time
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import Exporter, safe_filename
from ..api_client import (
    GranolaAPIClient,
    get_folder_ids_from_local_cache,
    get_owned_doc_ids_from_local_cache,
    get_shared_doc_ids_from_local_cache,
    get_viewed_meeting_ids_from_leveldb,
    get_token_from_local,
)
from ..models import ExportResult

logger = logging.getLogger(__name__)


class APIExportError(Exception):
    """Raised when the API export fails in an unrecoverable way."""


class AuthenticationError(APIExportError):
    """Raised when the API returns 401 or 403 — no point retrying."""


class APIExporter(Exporter):
    """
    Export Granola data by fetching from the API.

    This is intentionally *not* a BaseExporter subclass — it does not use
    a GranolaCache.  Both APIExporter and BaseExporter satisfy the Exporter
    interface so callers that only need export() can accept either.

    Example:
        >>> exporter = APIExporter(output_dir=Path("./export"))
        >>> result = exporter.export()
    """

    format_name = "api"
    file_extension = ".json"

    def __init__(
        self,
        output_dir: Path,
        access_token: Optional[str] = None,
        include_transcripts: bool = True,
        include_shared: bool = True,
        workspace_id: Optional[str] = None,
        sync_mode: bool = False,
    ):
        """
        Initialize the API exporter.

        Args:
            output_dir: Directory to write exported files.
            access_token: Optional explicit API token.
                         If not provided, uses local token.
            include_transcripts: Whether to fetch transcripts.
            include_shared: Whether to fetch shared documents from folders.
            workspace_id: Optional workspace filter.
            sync_mode: If True, only download new/changed documents.
        """
        self.output_dir = Path(output_dir)
        self.include_transcripts = include_transcripts
        self.include_shared = include_shared
        self.workspace_id = workspace_id
        self.sync_mode = sync_mode

        if access_token:
            self.client = GranolaAPIClient.from_token(access_token)
        else:
            self.client = GranolaAPIClient.from_local_token()

    def _prepare_output_dir(self) -> None:
        """Create the output directory if it doesn't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _check_auth_error(exc: urllib.error.HTTPError) -> None:
        """Raise AuthenticationError on 401/403 — these are unrecoverable."""
        if exc.code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed (HTTP {exc.code}). "
                "Check that your Granola token is valid and not expired."
            ) from exc

    def _load_previous_manifest(self) -> dict:
        """Load the previous export manifest if it exists."""
        manifest_path = self.output_dir / "manifest.json"
        if not manifest_path.exists():
            return {}

        try:
            with open(manifest_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load previous manifest: {e}")
            return {}

    @staticmethod
    def _parse_timestamp(value: str) -> Optional[datetime]:
        """Parse an ISO 8601 timestamp string, returning None on failure."""
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def _is_document_changed(
        self, doc: dict, previous_docs: dict
    ) -> tuple[bool, str]:
        """
        Check if a document has changed since the last export.

        Returns:
            Tuple of (is_changed, reason) where reason is 'new', 'updated', or 'unchanged'.
        """
        doc_id = doc.get("id")
        if not doc_id or doc_id not in previous_docs:
            return True, "new"

        prev_info = previous_docs[doc_id]
        doc_updated_str = doc.get("updated_at") or doc.get("updatedAt")
        prev_updated_str = prev_info.get("updated_at")

        # If we can't compare timestamps, assume changed
        if not doc_updated_str or not prev_updated_str:
            return True, "updated"

        # Parse to datetime to handle format variations (Z vs +00:00, etc.)
        doc_dt = self._parse_timestamp(doc_updated_str)
        prev_dt = self._parse_timestamp(prev_updated_str)

        if not doc_dt or not prev_dt:
            # Unparseable — assume changed to be safe
            return True, "updated"

        if doc_dt > prev_dt:
            return True, "updated"

        return False, "unchanged"

    def export(self) -> ExportResult:
        """
        Export all data from the API.

        Returns:
            ExportResult with export details.
        """
        self._prepare_output_dir()
        errors = []

        # Load previous manifest for sync mode
        previous_manifest = {}
        previous_docs = {}
        if self.sync_mode:
            previous_manifest = self._load_previous_manifest()
            previous_docs = previous_manifest.get("documents", {})
            if previous_docs:
                logger.info(f"Sync mode: found {len(previous_docs)} previously exported documents")
            else:
                logger.info("Sync mode: no previous export found, doing full export")

        # Create subdirectories
        meetings_dir = self.output_dir / "meetings"
        meetings_dir.mkdir(exist_ok=True)

        transcripts_dir = self.output_dir / "transcripts"
        transcripts_dir.mkdir(exist_ok=True)

        # Fetch workspaces — auth errors here are fatal (fail fast)
        logger.info("Fetching workspaces...")
        try:
            workspaces = self.client.get_workspaces()
            with open(self.output_dir / "workspaces.json", "w") as f:
                json.dump(workspaces, f, indent=2)
            logger.info(f"Found {len(workspaces)} workspaces")
        except urllib.error.HTTPError as e:
            # First API call: auth failure means nothing else will work
            self._check_auth_error(e)
            logger.error(f"HTTP {e.code} fetching workspaces: {e}")
            errors.append(f"Error fetching workspaces: HTTP {e.code}")
            workspaces = []
        except urllib.error.URLError as e:
            logger.error(f"Network error fetching workspaces: {e.reason}")
            errors.append(f"Network error fetching workspaces: {e.reason}")
            workspaces = []

        # Fetch folders — merge folder IDs from previous manifest and local
        # Granola cache so the client can fall back to the reliable singular
        # endpoint when the bulk endpoint (which frequently 500s) fails.
        # The local cache picks up new folders via websocket events.
        logger.info("Fetching folders...")
        manifest_ids = set(previous_manifest.get("folder_ids", []))
        deleted_folder_ids = set(previous_manifest.get("deleted_folder_ids", []))
        local_cache_ids = set(get_folder_ids_from_local_cache())
        known_folder_ids = list(
            (manifest_ids | local_cache_ids) - deleted_folder_ids
        )
        try:
            folders = self.client.get_document_lists(
                known_ids=known_folder_ids or None,
            )
            if folders:
                with open(self.output_dir / "folders.json", "w") as f:
                    json.dump(folders, f, indent=2)
            # Track which IDs came back as 404 (deleted/gone)
            fetched_ids = {f["id"] for f in folders if f.get("id")}
            newly_deleted = set(known_folder_ids) - fetched_ids
            deleted_folder_ids = deleted_folder_ids | newly_deleted
            logger.info(f"Found {len(folders)} folders")
        except urllib.error.HTTPError as e:
            self._check_auth_error(e)
            logger.error(f"HTTP {e.code} fetching folders: {e}")
            errors.append(f"Error fetching folders: HTTP {e.code}")
            folders = []
        except urllib.error.URLError as e:
            logger.error(f"Network error fetching folders: {e.reason}")
            errors.append(f"Network error fetching folders: {e.reason}")
            folders = []

        # Fetch documents
        logger.info("Fetching documents...")
        all_documents = []
        document_ids_seen = set()

        try:
            for doc in self.client.get_all_documents(
                workspace_id=self.workspace_id
            ):
                doc_id = doc.get("id")
                if doc_id:
                    document_ids_seen.add(doc_id)
                    all_documents.append(doc)

            logger.info(f"Found {len(all_documents)} owned documents")
        except urllib.error.HTTPError as e:
            self._check_auth_error(e)
            logger.error(f"HTTP {e.code} fetching documents: {e}")
            errors.append(f"Error fetching documents: HTTP {e.code}")
        except urllib.error.URLError as e:
            logger.error(f"Network error fetching documents: {e.reason}")
            errors.append(f"Network error fetching documents: {e.reason}")

        # Fetch shared documents from multiple discovery sources
        if self.include_shared:
            logger.info("Discovering shared documents...")
            shared_doc_ids: set[str] = set()

            # Source 1: documents in folders that aren't owned
            for folder in folders:
                folder_docs = folder.get("documents") or folder.get("document_ids") or []
                for doc in folder_docs:
                    doc_id = doc.get("id") if isinstance(doc, dict) else doc
                    if doc_id and doc_id not in document_ids_seen:
                        shared_doc_ids.add(doc_id)

            # Source 2: shared-with-me documents from local cache
            for doc_id in get_shared_doc_ids_from_local_cache():
                if doc_id not in document_ids_seen:
                    shared_doc_ids.add(doc_id)

            # Source 3: /v1/get-shared-documents API endpoint
            try:
                for doc in self.client.get_shared_documents():
                    doc_id = doc.get("id")
                    if doc_id and doc_id not in document_ids_seen:
                        shared_doc_ids.add(doc_id)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                logger.debug(f"Could not fetch shared documents API: {e}")

            # Source 4: meetings opened via share links (LevelDB navigation history)
            owned_cache_ids = get_owned_doc_ids_from_local_cache()
            for doc_id in get_viewed_meeting_ids_from_leveldb():
                if (
                    doc_id not in document_ids_seen
                    and doc_id not in owned_cache_ids
                ):
                    shared_doc_ids.add(doc_id)

            if shared_doc_ids:
                logger.info(f"Found {len(shared_doc_ids)} shared document IDs")

                # Step 1: try the batch API (works for workspace-shared docs)
                api_fetched_ids: set[str] = set()
                try:
                    shared_docs = self.client.get_documents_batch(
                        list(shared_doc_ids)
                    )
                    for doc in shared_docs:
                        doc["_shared"] = True
                        all_documents.append(doc)
                        if doc.get("id"):
                            api_fetched_ids.add(doc["id"])
                    if shared_docs:
                        logger.info(
                            f"Fetched {len(shared_docs)} shared documents via API"
                        )
                except urllib.error.HTTPError as e:
                    self._check_auth_error(e)
                    logger.error(f"HTTP {e.code} fetching shared documents: {e}")
                    errors.append(f"Error fetching shared documents: HTTP {e.code}")
                except urllib.error.URLError as e:
                    logger.error(f"Network error fetching shared documents: {e.reason}")
                    errors.append(f"Network error fetching shared documents: {e.reason}")

                # Step 2: fall back to web scraping for docs the API couldn't return
                remaining = shared_doc_ids - api_fetched_ids - document_ids_seen
                if remaining:
                    logger.info(
                        f"Fetching {len(remaining)} shared documents from web..."
                    )
                    web_count = 0
                    for doc_id in remaining:
                        doc = GranolaAPIClient.get_shared_document_from_web(
                            doc_id
                        )
                        if doc:
                            all_documents.append(doc)
                            web_count += 1
                            time.sleep(0.3)
                    if web_count:
                        logger.info(
                            f"Fetched {web_count} shared documents from web"
                        )

        # Determine which documents need to be written (sync mode filtering)
        docs_to_write = []
        docs_new = 0
        docs_updated = 0
        docs_skipped = 0

        for doc in all_documents:
            if self.sync_mode:
                changed, reason = self._is_document_changed(doc, previous_docs)
                if changed:
                    docs_to_write.append(doc)
                    if reason == "new":
                        docs_new += 1
                    else:
                        docs_updated += 1
                else:
                    docs_skipped += 1
            else:
                docs_to_write.append(doc)

        if self.sync_mode:
            logger.info(
                f"Sync: {docs_new} new, {docs_updated} updated, {docs_skipped} unchanged"
            )

        # Write individual document files (only changed ones in sync mode)
        for doc in docs_to_write:
            doc_id = doc.get("id", "unknown")
            safe_title = safe_filename(doc.get("title"))
            filename = f"{safe_title}_{doc_id[:8]}.json"

            with open(meetings_dir / filename, "w") as f:
                json.dump(doc, f, indent=2)

        # Fetch transcripts (only for documents we're writing)
        trans_exported = 0
        if self.include_transcripts:
            logger.info("Fetching transcripts...")
            consecutive_failures = 0

            for i, doc in enumerate(docs_to_write):
                doc_id = doc.get("id")
                if not doc_id:
                    continue

                # Brief pause between requests to avoid hammering the API
                if i > 0:
                    time.sleep(0.2)

                try:
                    transcript = self.client.get_document_transcript(doc_id)
                    consecutive_failures = 0
                    if transcript:
                        filename = f"transcript_{doc_id[:8]}.json"
                        with open(transcripts_dir / filename, "w") as f:
                            json.dump(
                                {
                                    "document_id": doc_id,
                                    "title": doc.get("title"),
                                    "transcript": transcript,
                                },
                                f,
                                indent=2,
                            )
                        trans_exported += 1
                except urllib.error.HTTPError as e:
                    self._check_auth_error(e)
                    if e.code != 404:
                        consecutive_failures += 1
                        logger.error(f"HTTP {e.code} fetching transcript for {doc_id}")
                        errors.append(f"Error fetching transcript for {doc_id}: HTTP {e.code}")
                except urllib.error.URLError as e:
                    consecutive_failures += 1
                    logger.error(f"Network error fetching transcript for {doc_id}: {e.reason}")
                    errors.append(f"Network error fetching transcript for {doc_id}: {e.reason}")

                if consecutive_failures >= 5:
                    remaining = len(docs_to_write) - i - 1
                    logger.error(
                        f"Too many consecutive transcript failures, "
                        f"skipping remaining {remaining} transcripts"
                    )
                    errors.append(
                        f"Stopped fetching transcripts after {consecutive_failures} "
                        f"consecutive failures ({remaining} skipped)"
                    )
                    break

            logger.info(f"Fetched {trans_exported} transcripts")

        # Fetch people
        logger.info("Fetching people...")
        try:
            people = self.client.get_people()
            with open(self.output_dir / "people.json", "w") as f:
                json.dump(people, f, indent=2)
        except urllib.error.HTTPError as e:
            self._check_auth_error(e)
            logger.error(f"HTTP {e.code} fetching people: {e}")
            errors.append(f"Error fetching people: HTTP {e.code}")
        except urllib.error.URLError as e:
            logger.error(f"Network error fetching people: {e.reason}")
            errors.append(f"Network error fetching people: {e.reason}")

        # Write combined documents file (all documents, not just changed)
        with open(self.output_dir / "all_meetings.json", "w") as f:
            json.dump(
                {
                    "export_date": datetime.now().isoformat(),
                    "export_source": "api",
                    "total_meetings": len(all_documents),
                    "meetings": all_documents,
                },
                f,
                indent=2,
            )

        # Build document tracking for manifest
        documents_manifest = {}
        for doc in all_documents:
            doc_id = doc.get("id")
            if doc_id:
                documents_manifest[doc_id] = {
                    "updated_at": doc.get("updated_at") or doc.get("updatedAt"),
                    "title": doc.get("title"),
                }

        # Write manifest with document tracking
        manifest = {
            "export_format": "api",
            "export_date": datetime.now().isoformat(),
            "source": "Granola API",
            "sync_enabled": self.sync_mode,
            "statistics": {
                "total_documents": len(all_documents),
                "documents_written": len(docs_to_write),
                "transcripts": trans_exported,
                "workspaces": len(workspaces),
                "folders": len(folders),
            },
            "options": {
                "include_transcripts": self.include_transcripts,
                "include_shared": self.include_shared,
                "workspace_id": self.workspace_id,
            },
            "documents": documents_manifest,
            "folder_ids": [f["id"] for f in folders if f.get("id")],
            "deleted_folder_ids": sorted(deleted_folder_ids),
            "errors": errors,
        }

        if self.sync_mode:
            manifest["sync_statistics"] = {
                "new": docs_new,
                "updated": docs_updated,
                "skipped": docs_skipped,
            }

        # Write manifest atomically so an interrupted sync doesn't leave
        # a half-written manifest that corrupts the next run.
        manifest_path = self.output_dir / "manifest.json"
        tmp_path = manifest_path.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(manifest, f, indent=2)
        tmp_path.replace(manifest_path)

        result_metadata = {}
        if self.sync_mode:
            result_metadata["sync_statistics"] = {
                "new": docs_new,
                "updated": docs_updated,
                "skipped": docs_skipped,
            }

        return ExportResult(
            success=len(errors) == 0,
            output_path=str(self.output_dir),
            documents_exported=len(docs_to_write),
            transcripts_exported=trans_exported,
            format="api",
            errors=errors,
            metadata=result_metadata,
        )
