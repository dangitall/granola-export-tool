"""
API-based exporter for Granola data.

Fetches data directly from Granola's servers instead of the local cache.
Useful for shared documents and team-wide exports.
"""

import json
import logging
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import Exporter, safe_filename
from ..api_client import GranolaAPIClient, get_token_from_local
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
        """
        self.output_dir = Path(output_dir)
        self.include_transcripts = include_transcripts
        self.include_shared = include_shared
        self.workspace_id = workspace_id

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

    def export(self) -> ExportResult:
        """
        Export all data from the API.

        Returns:
            ExportResult with export details.
        """
        self._prepare_output_dir()
        errors = []

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

        # Fetch folders
        logger.info("Fetching folders...")
        try:
            folders = self.client.get_document_lists()
            with open(self.output_dir / "folders.json", "w") as f:
                json.dump(folders, f, indent=2)
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
        docs_exported = 0
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
                    docs_exported += 1

            logger.info(f"Fetched {docs_exported} owned documents")
        except urllib.error.HTTPError as e:
            self._check_auth_error(e)
            logger.error(f"HTTP {e.code} fetching documents: {e}")
            errors.append(f"Error fetching documents: HTTP {e.code}")
        except urllib.error.URLError as e:
            logger.error(f"Network error fetching documents: {e.reason}")
            errors.append(f"Network error fetching documents: {e.reason}")

        # Fetch shared documents from folders
        if self.include_shared and folders:
            logger.info("Fetching shared documents from folders...")
            shared_doc_ids = set()

            for folder in folders:
                folder_docs = folder.get("documents") or folder.get("document_ids") or []
                for doc in folder_docs:
                    doc_id = doc.get("id") if isinstance(doc, dict) else doc
                    if doc_id and doc_id not in document_ids_seen:
                        shared_doc_ids.add(doc_id)

            if shared_doc_ids:
                logger.info(f"Fetching {len(shared_doc_ids)} shared documents...")
                try:
                    shared_docs = self.client.get_documents_batch(list(shared_doc_ids))
                    for doc in shared_docs:
                        doc["_shared"] = True
                        all_documents.append(doc)
                        docs_exported += 1
                    logger.info(f"Fetched {len(shared_docs)} shared documents")
                except urllib.error.HTTPError as e:
                    self._check_auth_error(e)
                    logger.error(f"HTTP {e.code} fetching shared documents: {e}")
                    errors.append(f"Error fetching shared documents: HTTP {e.code}")
                except urllib.error.URLError as e:
                    logger.error(f"Network error fetching shared documents: {e.reason}")
                    errors.append(f"Network error fetching shared documents: {e.reason}")

        # Write individual document files
        for doc in all_documents:
            doc_id = doc.get("id", "unknown")
            safe_title = safe_filename(doc.get("title") or "Untitled")
            filename = f"{safe_title}_{doc_id[:8]}.json"

            with open(meetings_dir / filename, "w") as f:
                json.dump(doc, f, indent=2)

        # Fetch transcripts
        trans_exported = 0
        if self.include_transcripts:
            logger.info("Fetching transcripts...")

            for doc in all_documents:
                doc_id = doc.get("id")
                if not doc_id:
                    continue

                try:
                    transcript = self.client.get_document_transcript(doc_id)
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
                    # get_document_transcript returns None on 404 (no transcript),
                    # so a 404 here shouldn't happen — but guard against it.
                    if e.code != 404:
                        logger.error(f"HTTP {e.code} fetching transcript for {doc_id}")
                        errors.append(f"Error fetching transcript for {doc_id}: HTTP {e.code}")
                except urllib.error.URLError as e:
                    logger.error(f"Network error fetching transcript for {doc_id}: {e.reason}")
                    errors.append(f"Network error fetching transcript for {doc_id}: {e.reason}")

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

        # Write combined documents file
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

        # Write manifest
        manifest = {
            "export_format": "api",
            "export_date": datetime.now().isoformat(),
            "source": "Granola API",
            "statistics": {
                "documents": docs_exported,
                "transcripts": trans_exported,
                "workspaces": len(workspaces),
                "folders": len(folders),
            },
            "options": {
                "include_transcripts": self.include_transcripts,
                "include_shared": self.include_shared,
                "workspace_id": self.workspace_id,
            },
            "errors": errors,
        }

        with open(self.output_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        return ExportResult(
            success=len(errors) == 0,
            output_path=str(self.output_dir),
            documents_exported=docs_exported,
            transcripts_exported=trans_exported,
            format="api",
            errors=errors,
        )
