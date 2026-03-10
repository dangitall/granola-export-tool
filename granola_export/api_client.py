"""
Granola API client for direct server access.

Fetches data from Granola's API endpoints, useful for:
- Shared documents (not in local cache)
- Team-wide exports
- Fresh data without waiting for cache sync
"""

import gzip
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
import urllib.request
import urllib.error

from .paths import get_granola_data_dir, get_token_path

logger = logging.getLogger(__name__)


@dataclass
class APIConfig:
    """Configuration for API access."""

    access_token: str
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    base_url: str = "https://api.granola.ai"
    user_agent: str = "Granola/5.354.0"
    client_version: str = "5.354.0"


def get_token_from_local() -> Optional[APIConfig]:
    """
    Extract API token from Granola's local storage.

    Returns:
        APIConfig if token found, None otherwise.
    """
    token_path = get_token_path()

    if not token_path.exists():
        logger.warning(f"Token file not found at {token_path}")
        return None

    try:
        with open(token_path, "r") as f:
            data = json.load(f)

        # Token is in workos_tokens.access_token
        # workos_tokens may be a JSON string or a dict
        workos = data.get("workos_tokens", {})
        if isinstance(workos, str):
            workos = json.loads(workos)

        access_token = workos.get("access_token")

        if not access_token:
            logger.warning("No access_token found in supabase.json")
            return None

        return APIConfig(
            access_token=access_token,
            refresh_token=workos.get("refresh_token"),
        )

    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to read token file: {e}")
        return None


def get_folder_ids_from_local_cache() -> list[str]:
    """
    Read folder IDs from Granola's local cache file.

    The GUI client maintains folder state via websockets, so its local
    cache is the most up-to-date source of folder IDs — including newly
    created folders that the broken bulk API endpoint can't return.

    Returns:
        List of folder UUID strings, or empty list if unavailable.
    """
    cache_path = get_granola_data_dir() / "cache-v4.json"
    if not cache_path.exists():
        return []

    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        doc_lists = data.get("cache", {}).get("state", {}).get("documentLists", {})
        if isinstance(doc_lists, dict):
            ids = list(doc_lists.keys())
            if ids:
                logger.info(f"Found {len(ids)} folder IDs in local Granola cache")
            return ids
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Could not read local cache for folder IDs: {e}")

    return []


class GranolaAPIClient:
    """
    Client for Granola's REST API.

    Provides methods to fetch documents, transcripts, workspaces,
    and folders directly from Granola's servers.

    Example:
        >>> client = GranolaAPIClient.from_local_token()
        >>> for doc in client.get_all_documents():
        ...     print(doc['title'])
    """

    def __init__(self, config: APIConfig):
        """
        Initialize the API client.

        Args:
            config: API configuration with access token.
        """
        self.config = config
        self._headers = {
            "Authorization": f"Bearer {config.access_token}",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": config.user_agent,
            "X-Client-Version": config.client_version,
        }

    @classmethod
    def from_local_token(cls) -> "GranolaAPIClient":
        """
        Create a client using the locally stored token.

        Returns:
            GranolaAPIClient instance.

        Raises:
            ValueError: If no token is found.
        """
        config = get_token_from_local()
        if not config:
            raise ValueError(
                "Could not find Granola API token. "
                "Make sure Granola is installed and you're logged in."
            )
        return cls(config)

    @classmethod
    def from_token(cls, access_token: str) -> "GranolaAPIClient":
        """
        Create a client with an explicit token.

        Args:
            access_token: The API access token.

        Returns:
            GranolaAPIClient instance.
        """
        return cls(APIConfig(access_token=access_token))

    def _request(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[dict] = None,
        max_retries: int = 3,
    ) -> dict:
        """Make an API request with retry logic.

        Args:
            endpoint: API endpoint path.
            method: HTTP method.
            data: Request payload.
            max_retries: Maximum retry attempts for transient failures.

        Returns:
            Parsed JSON response.

        Raises:
            urllib.error.HTTPError: For non-retryable HTTP errors.
            urllib.error.URLError: After all retries exhausted.
        """
        url = f"{self.config.base_url}{endpoint}"
        body = json.dumps(data or {}).encode("utf-8")

        last_exception = None

        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                url,
                data=body,
                headers=self._headers,
                method=method,
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    raw_data = response.read()

                    # Check if response is gzip-compressed
                    if raw_data[:2] == b'\x1f\x8b':
                        raw_data = gzip.decompress(raw_data)

                    return json.loads(raw_data.decode("utf-8"))

            except urllib.error.HTTPError as e:
                # Retry on 429 (rate limit) and 5xx (server errors)
                if (e.code == 429 or e.code >= 500) and attempt < max_retries:
                    # Respect Retry-After header if present, otherwise exponential backoff
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    if retry_after and retry_after.isdigit():
                        delay = min(int(retry_after), 60)
                    else:
                        delay = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        f"HTTP {e.code}, retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    last_exception = e
                    continue

                # Non-retryable HTTP error
                error_body = ""
                if e.fp:
                    raw = e.read()
                    if raw[:2] == b'\x1f\x8b':
                        raw = gzip.decompress(raw)
                    error_body = raw.decode("utf-8")
                logger.error(f"API error {e.code}: {error_body}")
                raise

            except urllib.error.URLError as e:
                # Retry on network errors
                if attempt < max_retries:
                    delay = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        f"Network error: {e.reason}, retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    last_exception = e
                    continue

                logger.error(f"Network error after {max_retries} retries: {e.reason}")
                raise

            except (TimeoutError, OSError) as e:
                # TimeoutError is not a subclass of URLError, but can be
                # raised by socket operations during HTTP requests
                if attempt < max_retries:
                    delay = 2 ** attempt
                    logger.warning(
                        f"Timeout/connection error: {e}, retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    last_exception = e
                    continue

                logger.error(f"Timeout/connection error after {max_retries} retries: {e}")
                raise urllib.error.URLError(str(e)) from e

        # Should not reach here, but guard against it
        if last_exception:
            raise last_exception
        raise RuntimeError("_request completed without returning or raising")

    # -------------------------------------------------------------------------
    # Documents
    # -------------------------------------------------------------------------

    def get_documents(
        self,
        workspace_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """
        Fetch documents from the API.

        Note: This endpoint only returns OWNED documents, not shared ones.
        Use get_documents_batch() for shared documents.

        Args:
            workspace_id: Optional workspace filter.
            limit: Max documents per request.
            offset: Pagination offset.

        Returns:
            API response with 'docs' array.
        """
        data = {
            "limit": limit,
            "offset": offset,
            "include_last_viewed_panel": True,
        }

        if workspace_id:
            data["workspace_id"] = workspace_id

        return self._request("/v2/get-documents", data=data)

    def get_all_documents(
        self,
        workspace_id: Optional[str] = None,
        limit: int = 100,
    ) -> Iterator[dict]:
        """
        Iterate through all documents with automatic pagination.

        Errors on individual pages are logged and stop pagination
        (yielding whatever was fetched so far) rather than raising.

        Args:
            workspace_id: Optional workspace filter.
            limit: Batch size.

        Yields:
            Document dictionaries.
        """
        offset = 0
        consecutive_errors = 0

        while True:
            try:
                response = self.get_documents(
                    workspace_id=workspace_id,
                    limit=limit,
                    offset=offset,
                )
                consecutive_errors = 0
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                code = getattr(e, "code", None)
                # Auth errors should still propagate
                if code in (401, 403):
                    raise
                consecutive_errors += 1
                logger.error(f"Error fetching documents at offset {offset}: {e}")
                if consecutive_errors >= 2:
                    logger.error("Multiple consecutive page failures, stopping pagination")
                    break
                # Skip this page and try the next
                offset += limit
                continue

            docs = response.get("docs", [])
            if not docs:
                break

            for doc in docs:
                yield doc

            if len(docs) < limit:
                break

            offset += limit

    def get_documents_batch(
        self,
        document_ids: list[str],
        batch_size: int = 100,
    ) -> list[dict]:
        """
        Fetch multiple documents by ID, including shared documents.

        This is the ONLY way to fetch shared documents that aren't
        returned by get_documents().

        Args:
            document_ids: List of document IDs.
            batch_size: Documents per request.

        Returns:
            List of document dictionaries.
        """
        all_docs = []

        for i in range(0, len(document_ids), batch_size):
            batch = document_ids[i : i + batch_size]
            data = {
                "document_ids": batch,
                "include_last_viewed_panel": True,
            }

            response = self._request("/v1/get-documents-batch", data=data)
            docs = response.get("documents") or response.get("docs") or []
            all_docs.extend(docs)

        return all_docs

    # -------------------------------------------------------------------------
    # Transcripts
    # -------------------------------------------------------------------------

    def get_document_transcript(self, document_id: str) -> Optional[list]:
        """
        Fetch transcript for a specific document.

        Args:
            document_id: The document ID.

        Returns:
            List of transcript segments, or None if not found.
        """
        try:
            response = self._request(
                "/v1/get-document-transcript",
                data={"document_id": document_id},
            )
            # API returns transcript array directly or in 'transcript' key
            if isinstance(response, list):
                return response
            return response.get("transcript", [])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    # -------------------------------------------------------------------------
    # Workspaces and Folders
    # -------------------------------------------------------------------------

    def get_workspaces(self) -> list[dict]:
        """
        Fetch all accessible workspaces.

        Returns:
            List of workspace dictionaries.
        """
        response = self._request("/v1/get-workspaces", data={})

        if isinstance(response, list):
            return response
        return response.get("workspaces", [])

    def get_document_list(self, list_id: str) -> Optional[dict]:
        """
        Fetch a single document list (folder) by ID.

        This is the reliable endpoint — use this instead of the bulk
        get_document_lists which frequently 500s.

        Args:
            list_id: The folder UUID.

        Returns:
            Folder dictionary, or None if not found.
        """
        try:
            return self._request(
                "/v1/get-document-list", data={"list_id": list_id}
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def get_document_lists(
        self, known_ids: Optional[list[str]] = None
    ) -> list[dict]:
        """
        Fetch all document lists (folders).

        Tries the bulk endpoint first.  If it fails and known_ids are
        provided, falls back to fetching each folder individually via
        the reliable singular endpoint.

        Args:
            known_ids: Optional list of folder IDs from a previous export
                      to use as fallback when the bulk endpoint fails.

        Returns:
            List of folder dictionaries with document IDs.
        """
        # Try bulk endpoint first
        try:
            response = self._request(
                "/v2/get-document-lists", data={}, max_retries=1,
            )
            if isinstance(response, list):
                return response
            return (
                response.get("lists")
                or response.get("document_lists")
                or []
            )
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            logger.warning(f"Bulk folder endpoint failed: {e}")

        # Fall back to fetching individual folders if we have IDs
        if not known_ids:
            logger.warning("No known folder IDs for fallback — skipping folders")
            return []

        logger.info(
            f"Falling back to individual folder fetches ({len(known_ids)} folders)"
        )
        folders = []
        for list_id in known_ids:
            try:
                folder = self.get_document_list(list_id)
                if folder:
                    folders.append(folder)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                logger.warning(f"Failed to fetch folder {list_id}: {e}")
        return folders

    # -------------------------------------------------------------------------
    # Other Endpoints
    # -------------------------------------------------------------------------

    def get_people(self) -> dict:
        """Fetch people/contacts data."""
        return self._request("/v1/get-people", data={})

    def get_panel_templates(self) -> list[dict]:
        """Fetch available panel templates."""
        response = self._request("/v1/get-panel-templates", data={})
        if isinstance(response, list):
            return response
        return response.get("panel_templates", [])

    def get_feature_flags(self) -> dict:
        """Fetch feature flags."""
        return self._request("/v1/get-feature-flags", data={})

    def check_connection(self) -> bool:
        """
        Test if the API connection is working.

        Returns:
            True if connection successful.
        """
        try:
            self.get_workspaces()
            return True
        except urllib.error.HTTPError as e:
            logger.error(f"Connection check failed: HTTP {e.code}")
            return False
        except urllib.error.URLError as e:
            logger.error(f"Connection check failed: {e.reason}")
            return False
