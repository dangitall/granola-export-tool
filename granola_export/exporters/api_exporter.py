"""
API-based exporter for Granola data.

Fetches data directly from Granola's servers instead of the local cache.
Useful for shared documents and team-wide exports.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .base import BaseExporter
from ..api_client import GranolaAPIClient, get_token_from_local
from ..models import ExportResult
from ..utils import safe_filename

logger = logging.getLogger(__name__)


class APIExporter(BaseExporter):
    """
    Export Granola data by fetching from the API.

    Unlike cache-based exporters, this fetches fresh data from
    Granola's servers and can access shared documents.

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
        # Don't call super().__init__ since we don't have a cache
        self.output_dir = Path(output_dir)
        self.include_transcripts = include_transcripts
        self.include_shared = include_shared
        self.workspace_id = workspace_id
        self.sync_mode = sync_mode

        # Initialize API client
        if access_token:
            self.client = GranolaAPIClient.from_token(access_token)
        else:
            self.client = GranolaAPIClient.from_local_token()

    def prepare_output_dir(self) -> None:
        """Create the output directory if it doesn't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

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
        doc_updated = doc.get("updated_at") or doc.get("updatedAt")
        prev_updated = prev_info.get("updated_at")

        # If we can't compare timestamps, assume changed
        if not doc_updated or not prev_updated:
            return True, "updated"

        # Compare timestamps
        if doc_updated > prev_updated:
            return True, "updated"

        return False, "unchanged"

    def export(self) -> ExportResult:
        """
        Export all data from the API.

        Returns:
            ExportResult with export details.
        """
        self.prepare_output_dir()
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

        # Fetch workspaces
        logger.info("Fetching workspaces...")
        try:
            workspaces = self.client.get_workspaces()
            with open(self.output_dir / "workspaces.json", "w") as f:
                json.dump(workspaces, f, indent=2)
            logger.info(f"Found {len(workspaces)} workspaces")
        except Exception as e:
            errors.append(f"Error fetching workspaces: {e}")
            workspaces = []

        # Fetch folders
        logger.info("Fetching folders...")
        try:
            folders = self.client.get_document_lists()
            with open(self.output_dir / "folders.json", "w") as f:
                json.dump(folders, f, indent=2)
            logger.info(f"Found {len(folders)} folders")
        except Exception as e:
            errors.append(f"Error fetching folders: {e}")
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
        except Exception as e:
            errors.append(f"Error fetching documents: {e}")

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
                    logger.info(f"Fetched {len(shared_docs)} shared documents")
                except Exception as e:
                    errors.append(f"Error fetching shared documents: {e}")

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
        if self.include_transcripts and docs_to_write:
            # Use progress bar if running in a terminal
            use_progress = sys.stdout.isatty()

            if use_progress:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    transient=True,
                ) as progress:
                    task = progress.add_task(
                        "Fetching transcripts...", total=len(docs_to_write)
                    )

                    for doc in docs_to_write:
                        doc_id = doc.get("id")
                        if not doc_id:
                            progress.advance(task)
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
                        except Exception as e:
                            # 404 is expected for docs without transcripts
                            if "404" not in str(e):
                                errors.append(f"Error fetching transcript for {doc_id}: {e}")

                        progress.advance(task)
            else:
                # Non-TTY mode: use logger
                logger.info("Fetching transcripts...")
                for doc in docs_to_write:
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
                    except Exception as e:
                        # 404 is expected for docs without transcripts
                        if "404" not in str(e):
                            errors.append(f"Error fetching transcript for {doc_id}: {e}")

            logger.info(f"Fetched {trans_exported} transcripts")

        # Fetch people
        logger.info("Fetching people...")
        try:
            people = self.client.get_people()
            with open(self.output_dir / "people.json", "w") as f:
                json.dump(people, f, indent=2)
        except Exception as e:
            errors.append(f"Error fetching people: {e}")

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
            "errors": errors,
        }

        if self.sync_mode:
            manifest["sync_statistics"] = {
                "new": docs_new,
                "updated": docs_updated,
                "skipped": docs_skipped,
            }

        with open(self.output_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        return ExportResult(
            success=len(errors) == 0,
            output_path=str(self.output_dir),
            documents_exported=len(docs_to_write),
            transcripts_exported=trans_exported,
            format="api",
            errors=errors,
        )
