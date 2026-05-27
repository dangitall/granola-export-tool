"""
Granola API client for direct server access.

Fetches data from Granola's API endpoints, useful for:
- Shared documents (not in local cache)
- Team-wide exports
- Fresh data without waiting for cache sync
"""

import base64
import binascii
import gzip
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
import urllib.request
import urllib.error

from .paths import (
    CACHE_FILENAME,
    get_accounts_path,
    get_granola_data_dir,
    get_token_path,
)

logger = logging.getLogger(__name__)

# Endpoint Granola's app uses to mint a new access_token from a refresh_token.
# It is intentionally excluded from the bearer-injecting request interceptor —
# the body's refresh_token is the only credential.
REFRESH_TOKEN_URL = "https://api.granola.ai/v1/refresh-access-token"


class AuthRefreshError(Exception):
    """Raised when refreshing the access_token fails.

    Covers all failure modes of the refresh exchange — revoked
    refresh_token (the only unrecoverable case), transient server
    errors, network failures, malformed responses — so callers can
    handle "I couldn't get a fresh token" with a single catch.

    ``revoked`` distinguishes the unrecoverable case: the server told
    us the refresh_token has been invalidated and the user must
    re-sign-in via the Granola app. For all other cases (5xx, DNS,
    timeout, garbage response) a retry on the next run is reasonable.
    """

    def __init__(self, message: str, *, revoked: bool = False):
        super().__init__(message)
        self.revoked = revoked


@dataclass
class APIConfig:
    """Configuration for API access."""

    access_token: str
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    base_url: str = "https://api.granola.ai"
    user_agent: str = "Granola/5.354.0"
    client_version: str = "5.354.0"


def _is_jwt_expired(token: str, margin_seconds: int = 60) -> bool:
    """Return True if ``token`` is a JWT whose ``exp`` is at or near the past.

    The margin protects against the access_token expiring mid-request — if
    we'd be using it within ``margin_seconds`` of its deadline, we'd rather
    refresh now than 401 halfway through a run.

    Unparseable tokens and JWTs without an ``exp`` claim return False:
    that preserves prior behavior for opaque/legacy tokens, at the cost of
    not pre-empting their expiry. Those cases still surface as 401 at
    use-time via the existing error path.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return False
    try:
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        )
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return exp <= time.time() + margin_seconds


def refresh_access_token(refresh_token: str) -> APIConfig:
    """Exchange a refresh_token for a fresh access_token.

    Mirrors what the Granola Electron app does in
    ``nodeRefreshWorkOsAccessToken``: POST the refresh_token to
    ``/v1/refresh-access-token`` with no Authorization header (the
    endpoint is excluded from the bearer-injecting interceptor).

    Raises:
        AuthRefreshError: any failure of the refresh exchange. The
            ``revoked`` attribute is True only when the refresh_token
            itself was rejected (server returned ``error: logout_user``);
            all other failures (5xx, network errors, malformed body)
            chain the original cause via ``__cause__``.
    """
    body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
    # Standard client headers, sans Authorization. The server identifies the
    # session via the refresh_token, not a bearer.
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": "Granola/5.354.0",
        "X-Client-Version": "5.354.0",
        "X-Granola-Platform": "darwin",
    }
    req = urllib.request.Request(
        REFRESH_TOKEN_URL, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401 and e.fp is not None:
            body_bytes = e.fp.read()
            # We consumed e.fp to classify the error; restore it so any
            # caller that inspects the chained __cause__ still sees the body.
            e.fp = io.BytesIO(body_bytes)
            try:
                err_payload = json.loads(body_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                err_payload = None
            if isinstance(err_payload, dict) and err_payload.get("error") == "logout_user":
                raise AuthRefreshError(
                    "Granola sign-in expired — open the Granola app "
                    "and sign in again.",
                    revoked=True,
                ) from e
        raise AuthRefreshError(
            f"Could not refresh Granola access token (HTTP {e.code})"
        ) from e
    except urllib.error.URLError as e:
        raise AuthRefreshError(
            f"Could not refresh Granola access token: {e.reason}"
        ) from e

    new_access = payload.get("access_token")
    if not new_access:
        raise AuthRefreshError(
            "refresh-access-token response missing access_token"
        )
    return APIConfig(
        access_token=new_access,
        # Granola does not rotate refresh tokens today, but the server is
        # free to return a new one — prefer whatever it sent us.
        refresh_token=payload.get("refresh_token") or refresh_token,
    )


def _load_token_from_stored_accounts() -> Optional[APIConfig]:
    """Read the active access token from Granola v7+ stored-accounts.json.

    The file is a JSON document like ``{"accounts": "<json string>"}``
    where the inner value is a list of accounts. Each account has a
    ``tokens`` field that is *also* a JSON-encoded string containing
    ``access_token`` / ``refresh_token``. Both the outer ``accounts``
    field and the inner ``tokens`` field may also be plain JSON values
    (list / dict) — both forms are accepted.

    When the file holds multiple accounts, the most-recently-saved one
    (highest ``savedAt``) wins — Granola has no explicit active-account
    marker, but the freshest entry is the safest heuristic.

    Granola rewrites this file on every token refresh, so it stays fresh
    while the legacy supabase.json does not.
    """
    accounts_path = get_accounts_path()
    if not accounts_path.exists():
        return None

    try:
        with open(accounts_path, "r") as f:
            data = json.load(f)

        accounts = data.get("accounts")
        if isinstance(accounts, str):
            accounts = json.loads(accounts)
        if not accounts:
            return None

        valid_accounts = [a for a in accounts if isinstance(a, dict)]
        if not valid_accounts:
            return None
        # Most-recently-saved account wins; missing savedAt sorts last.
        account = max(valid_accounts, key=lambda a: a.get("savedAt") or 0)

        tokens = account.get("tokens")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if not isinstance(tokens, dict):
            return None

        access_token = tokens.get("access_token")
        if not access_token:
            return None

        logger.debug(
            "Using account %s (%d total in stored-accounts.json)",
            account.get("email", "<unknown>"),
            len(valid_accounts),
        )
        return APIConfig(
            access_token=access_token,
            refresh_token=tokens.get("refresh_token"),
        )
    except (
        json.JSONDecodeError,
        IOError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
    ) as e:
        # Stay quiet here; the dispatcher emits a single user-facing
        # warning if both this and the legacy path also fail.
        logger.debug(f"Failed to read stored-accounts.json: {e}")
        return None


def _load_token_from_supabase() -> Optional[APIConfig]:
    """Read the access token from the legacy supabase.json file.

    Older Granola versions (pre-v7) wrote tokens here. The file is no
    longer kept fresh on newer installs but is still useful as a fallback.
    """
    token_path = get_token_path()
    if not token_path.exists():
        return None

    try:
        with open(token_path, "r") as f:
            data = json.load(f)

        # workos_tokens may be a JSON string or a dict
        workos = data.get("workos_tokens", {})
        if isinstance(workos, str):
            workos = json.loads(workos)

        access_token = workos.get("access_token")
        if not access_token:
            return None

        return APIConfig(
            access_token=access_token,
            refresh_token=workos.get("refresh_token"),
        )
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Failed to read supabase.json: {e}")
        return None


def get_token_from_local() -> Optional[APIConfig]:
    """
    Extract API token from Granola's local storage.

    Prefers the v7+ ``stored-accounts.json`` and falls back to the legacy
    ``supabase.json``. If the access_token is an expired JWT and a
    refresh_token is available, transparently exchanges it for a fresh
    access_token via Granola's refresh endpoint.

    Why this matters: recent Granola releases write tokens only to the
    encrypted ``stored-accounts.json.enc``; the plaintext mirror we read
    here is no longer kept fresh. The plaintext refresh_token is still
    valid (Granola doesn't rotate refresh tokens), so we can exchange
    it for a current access_token without touching the encrypted file.

    Returns:
        APIConfig if a token was found, None otherwise.

    Raises:
        AuthRefreshError: if the refresh_token itself was revoked.
    """
    config = _load_token_from_stored_accounts() or _load_token_from_supabase()
    if not config:
        logger.warning(
            f"No Granola auth token found (checked {get_accounts_path()} "
            f"and {get_token_path()}). Is Granola installed and signed in?"
        )
        return None

    if _is_jwt_expired(config.access_token) and config.refresh_token:
        logger.info(
            "Cached access token expired; refreshing via Granola endpoint"
        )
        return refresh_access_token(config.refresh_token)

    return config


def get_folder_ids_from_local_cache() -> list[str]:
    """
    Read folder IDs from Granola's local cache file.

    The GUI client maintains folder state via websockets, so its local
    cache is the most up-to-date source of folder IDs — including newly
    created folders that the broken bulk API endpoint can't return.

    Returns:
        List of folder UUID strings, or empty list if unavailable.
    """
    cache_path = get_granola_data_dir() / CACHE_FILENAME
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


def get_shared_doc_ids_from_local_cache() -> list[str]:
    """
    Read shared document IDs from Granola's local cache file.

    The GUI client tracks documents shared with the current user in
    ``cache.state.sharedDocuments`` (a dict keyed by document ID).

    Returns:
        List of document UUID strings, or empty list if unavailable.
    """
    cache_path = get_granola_data_dir() / CACHE_FILENAME
    if not cache_path.exists():
        return []

    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        shared_docs = data.get("cache", {}).get("state", {}).get("sharedDocuments", {})
        if isinstance(shared_docs, dict):
            ids = list(shared_docs.keys())
            if ids:
                logger.info(f"Found {len(ids)} shared document IDs in local Granola cache")
            return ids
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Could not read local cache for shared document IDs: {e}")

    return []


def get_owned_doc_ids_from_local_cache() -> set[str]:
    """
    Return the set of owned document IDs from the local cache.

    Used to filter out owned documents when discovering shared ones.
    """
    cache_path = get_granola_data_dir() / CACHE_FILENAME
    if not cache_path.exists():
        return set()

    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        docs = data.get("cache", {}).get("state", {}).get("documents", {})
        if isinstance(docs, dict):
            return set(docs.keys())
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Could not read local cache for owned doc IDs: {e}")

    return set()


_UUID_RE = re.compile(
    rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def get_viewed_meeting_ids_from_leveldb() -> set[str]:
    """
    Scan Granola's Local Storage LevelDB files for meeting IDs.

    The Granola desktop app records Amplitude analytics events in
    Electron's Local Storage, stored as LevelDB table files.  Meeting
    navigation entries follow the pattern ``meeting/<UUID>``.

    Because the LevelDB is locked by the running app, this reads the
    ``.ldb`` and ``.log`` files directly rather than opening the
    database.

    Returns:
        Set of document UUID strings the user has viewed.
    """
    leveldb_dir = get_granola_data_dir() / "Local Storage" / "leveldb"
    if not leveldb_dir.is_dir():
        return set()

    content = bytearray()
    for path in leveldb_dir.iterdir():
        if path.suffix in (".ldb", ".log"):
            try:
                content.extend(path.read_bytes())
            except IOError:
                continue

    if not content:
        return set()

    ids: set[str] = set()
    for match in re.finditer(rb"meeting/" + _UUID_RE.pattern, content):
        uuid = _UUID_RE.search(match.group())
        if uuid:
            ids.add(uuid.group().decode("ascii"))

    if ids:
        logger.info(
            f"Found {len(ids)} meeting IDs in Local Storage LevelDB"
        )
    return ids


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

    def get_shared_documents(self) -> list[dict]:
        """
        Fetch documents that have been shared with the current user.

        Returns:
            List of document dictionaries.
        """
        response = self._request("/v1/get-shared-documents", data={})
        return response.get("docs", [])

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

    @staticmethod
    def get_shared_document_from_web(document_id: str) -> Optional[dict]:
        """
        Fetch a shared document from the Granola web share page.

        Some shared documents are not accessible via the API but are
        publicly readable at ``notes.granola.ai/d/<id>``.  This method
        scrapes the server-rendered page to extract metadata and notes.

        Args:
            document_id: The document UUID.

        Returns:
            A dict with id, title, creator, notes_html, and _shared
            flag, or None if the page doesn't contain usable content.
        """
        url = f"https://notes.granola.ai/d/{document_id}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8")
        except (urllib.error.URLError, OSError) as e:
            logger.warning(f"Could not fetch share page for {document_id}: {e}")
            return None

        # RSC payload chunks are embedded as self.__next_f.push([1,"..."])
        chunks = re.findall(
            r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html
        )

        title = None
        creator = None
        notes_html = None

        for chunk in chunks:
            try:
                text = json.loads('"' + chunk + '"')
            except (json.JSONDecodeError, ValueError):
                continue

            if title is None and '"og:title"' in text:
                m = re.search(r'"og:title","content":"([^"]+)"', text)
                if m:
                    title = m.group(1)

            if creator is None and "creatorName" in text:
                m = re.search(r'"creatorName":"([^"]+)"', text)
                if m:
                    creator = m.group(1)

            stripped = text.strip()
            if notes_html is None and (
                stripped.startswith("<h") or stripped.startswith("<p")
            ):
                notes_html = text

        if not title and not notes_html:
            return None

        return {
            "id": document_id,
            "title": title or "Untitled",
            "notes": notes_html or "",
            "user_id": None,
            "_shared": True,
            "_source": "web",
            "_creator": creator,
        }

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
