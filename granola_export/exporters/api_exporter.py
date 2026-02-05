"""
API-based exporter for Granola data.

Fetches data directly from Granola's servers instead of the local cache.
Useful for shared documents and team-wide exports.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

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
        # Don't call super().__init__ since we don't have a cache
        self.output_dir = Path(output_dir)
        self.include_transcripts = include_transcripts
        self.include_shared = include_shared
        self.workspace_id = workspace_id

        # Initialize API client
        if access_token:
            self.client = GranolaAPIClient.from_token(access_token)
        else:
            self.client = GranolaAPIClient.from_local_token()

    def prepare_output_dir(self) -> None:
        """Create the output directory if it doesn't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(self) -> ExportResult:
        """
        Export all data from the API.

        Returns:
            ExportResult with export details.
        """
        self.prepare_output_dir()
        errors = []

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
                        docs_exported += 1
                    logger.info(f"Fetched {len(shared_docs)} shared documents")
                except Exception as e:
                    errors.append(f"Error fetching shared documents: {e}")

        # Write individual document files
        for doc in all_documents:
            doc_id = doc.get("id", "unknown")
            safe_title = safe_filename(doc.get("title"))
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
