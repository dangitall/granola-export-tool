"""Tests for API exporter error handling."""

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from granola_export.exporters.api_exporter import (
    APIExporter,
    APIExportError,
    AuthenticationError,
)


def _make_http_error(code: int) -> urllib.error.HTTPError:
    """Create an HTTPError with the given status code."""
    return urllib.error.HTTPError(
        url="https://api.granola.ai/test",
        code=code,
        msg=f"HTTP {code}",
        hdrs={},
        fp=BytesIO(b""),
    )


@pytest.fixture
def exporter(tmp_path):
    """Create an APIExporter with a mocked client."""
    patches = [
        patch(
            "granola_export.exporters.api_exporter.GranolaAPIClient.from_token"
        ),
        patch(
            "granola_export.exporters.api_exporter.get_shared_doc_ids_from_local_cache",
            return_value=[],
        ),
        patch(
            "granola_export.exporters.api_exporter.get_owned_doc_ids_from_local_cache",
            return_value=set(),
        ),
        patch(
            "granola_export.exporters.api_exporter.get_viewed_meeting_ids_from_leveldb",
            return_value=set(),
        ),
        patch(
            "granola_export.exporters.api_exporter.get_folder_ids_from_local_cache",
            return_value=[],
        ),
    ]
    mocks = [p.start() for p in patches]
    mock_from_token = mocks[0]

    mock_client = MagicMock()
    mock_client.get_shared_documents.return_value = []
    mock_from_token.return_value = mock_client
    exp = APIExporter(
        output_dir=tmp_path / "export",
        access_token="fake-token",
    )

    yield exp

    for p in patches:
        p.stop()


class TestAuthenticationError:
    """Auth errors (401/403) should abort the export immediately."""

    def test_401_raises_authentication_error(self, exporter):
        exporter.client.get_workspaces.side_effect = _make_http_error(401)

        with pytest.raises(AuthenticationError, match="Authentication failed"):
            exporter.export()

    def test_403_raises_authentication_error(self, exporter):
        exporter.client.get_workspaces.side_effect = _make_http_error(403)

        with pytest.raises(AuthenticationError, match="Authentication failed"):
            exporter.export()

    def test_auth_error_is_api_export_error(self):
        """AuthenticationError should be catchable as APIExportError."""
        assert issubclass(AuthenticationError, APIExportError)

    def test_401_on_later_call_still_aborts(self, exporter):
        """Auth failure mid-export (not just on first call) should still abort."""
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter([])
        # Auth error on a later API call (people)
        exporter.client.get_people.side_effect = _make_http_error(401)

        with pytest.raises(AuthenticationError, match="Authentication failed"):
            exporter.export()

    def test_403_on_transcript_aborts(self, exporter):
        """Auth failure during transcript fetching should abort."""
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(
            [{"id": "doc-1", "title": "Test"}]
        )
        exporter.client.get_document_transcript.side_effect = _make_http_error(403)
        exporter.client.get_people.return_value = {}

        with pytest.raises(AuthenticationError, match="Authentication failed"):
            exporter.export()


class TestRecoverableErrors:
    """Non-auth HTTP errors should be collected, not raised."""

    def test_500_on_workspaces_continues_export(self, exporter):
        exporter.client.get_workspaces.side_effect = _make_http_error(500)
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter([])
        exporter.client.get_people.return_value = {}

        result = exporter.export()

        assert not result.success
        assert any("workspaces" in e for e in result.errors)

    def test_network_error_continues_export(self, exporter):
        exporter.client.get_workspaces.side_effect = urllib.error.URLError(
            "DNS resolution failed"
        )
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter([])
        exporter.client.get_people.return_value = {}

        result = exporter.export()

        assert not result.success
        assert any("Network error" in e for e in result.errors)

    def test_transcript_404_is_silently_skipped(self, exporter):
        """404 on transcripts is normal (doc has no transcript)."""
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(
            [{"id": "doc-1", "title": "Test"}]
        )
        exporter.client.get_document_transcript.side_effect = _make_http_error(404)
        exporter.client.get_people.return_value = {}

        result = exporter.export()

        # 404 on transcript should not produce an error
        assert result.success
        assert len(result.errors) == 0

    def test_transcript_500_is_recorded(self, exporter):
        """Non-404 transcript errors should be recorded."""
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(
            [{"id": "doc-1", "title": "Test"}]
        )
        exporter.client.get_document_transcript.side_effect = _make_http_error(500)
        exporter.client.get_people.return_value = {}

        result = exporter.export()

        assert not result.success
        assert any("transcript" in e for e in result.errors)

    @patch("granola_export.exporters.api_exporter.time.sleep")
    def test_transcript_bail_out_after_consecutive_failures(self, mock_sleep, exporter):
        """Stop fetching transcripts after 5 consecutive failures."""
        docs = [{"id": f"doc-{i}", "title": f"Meeting {i}"} for i in range(10)]
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(docs)
        exporter.client.get_document_transcript.side_effect = _make_http_error(500)
        exporter.client.get_people.return_value = {}

        result = exporter.export()

        assert not result.success
        # Should have bailed after 5 consecutive failures, not tried all 10
        assert exporter.client.get_document_transcript.call_count == 5
        assert any("skipped" in e.lower() for e in result.errors)


class TestSuccessfulExport:
    """Verify a successful export writes the expected files."""

    def test_writes_manifest_and_meetings(self, exporter):
        exporter.client.get_workspaces.return_value = [{"id": "ws-1"}]
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(
            [{"id": "doc-1", "title": "Meeting One"}]
        )
        exporter.client.get_document_transcript.return_value = [
            {"text": "hello", "start": 0, "end": 1}
        ]
        exporter.client.get_people.return_value = {"people": []}

        result = exporter.export()

        assert result.success
        assert result.documents_exported == 1
        assert result.transcripts_exported == 1

        # Check key files exist
        out = Path(result.output_path)
        assert (out / "manifest.json").exists()
        assert (out / "all_meetings.json").exists()
        assert (out / "workspaces.json").exists()
        assert list((out / "meetings").iterdir())
        assert list((out / "transcripts").iterdir())


@pytest.fixture
def sync_exporter(tmp_path):
    """Create an APIExporter in sync mode with a mocked client."""
    patches = [
        patch(
            "granola_export.exporters.api_exporter.GranolaAPIClient.from_token"
        ),
        patch(
            "granola_export.exporters.api_exporter.get_shared_doc_ids_from_local_cache",
            return_value=[],
        ),
        patch(
            "granola_export.exporters.api_exporter.get_owned_doc_ids_from_local_cache",
            return_value=set(),
        ),
        patch(
            "granola_export.exporters.api_exporter.get_viewed_meeting_ids_from_leveldb",
            return_value=set(),
        ),
        patch(
            "granola_export.exporters.api_exporter.get_folder_ids_from_local_cache",
            return_value=[],
        ),
    ]
    mocks = [p.start() for p in patches]
    mock_from_token = mocks[0]

    mock_client = MagicMock()
    mock_client.get_shared_documents.return_value = []
    mock_from_token.return_value = mock_client
    exp = APIExporter(
        output_dir=tmp_path / "export",
        access_token="fake-token",
        sync_mode=True,
    )

    yield exp

    for p in patches:
        p.stop()


class TestSyncMode:
    """Tests for incremental sync mode."""

    def _setup_basic_mocks(self, exporter, docs):
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(docs)
        exporter.client.get_document_transcript.return_value = None
        exporter.client.get_people.return_value = {}

    def test_first_sync_exports_everything(self, sync_exporter):
        """First sync (no previous manifest) should export all documents."""
        docs = [
            {"id": "doc-1", "title": "Meeting 1", "updated_at": "2025-01-01T00:00:00Z"},
            {"id": "doc-2", "title": "Meeting 2", "updated_at": "2025-01-02T00:00:00Z"},
        ]
        self._setup_basic_mocks(sync_exporter, docs)

        result = sync_exporter.export()

        assert result.documents_exported == 2
        assert result.metadata["sync_statistics"]["new"] == 2
        assert result.metadata["sync_statistics"]["skipped"] == 0

    def test_second_sync_skips_unchanged(self, sync_exporter):
        """Second sync should skip documents with unchanged timestamps."""
        docs = [
            {"id": "doc-1", "title": "Meeting 1", "updated_at": "2025-01-01T00:00:00Z"},
            {"id": "doc-2", "title": "Meeting 2", "updated_at": "2025-01-02T00:00:00Z"},
        ]
        self._setup_basic_mocks(sync_exporter, docs)

        # First sync
        sync_exporter.export()

        # Second sync with same data
        sync_exporter.client.get_all_documents.return_value = iter(docs)
        result = sync_exporter.export()

        assert result.documents_exported == 0
        assert result.metadata["sync_statistics"]["skipped"] == 2
        assert result.metadata["sync_statistics"]["new"] == 0

    def test_sync_detects_updated_documents(self, sync_exporter):
        """Sync should detect documents with newer timestamps."""
        docs_v1 = [
            {"id": "doc-1", "title": "Meeting 1", "updated_at": "2025-01-01T00:00:00Z"},
        ]
        self._setup_basic_mocks(sync_exporter, docs_v1)
        sync_exporter.export()

        # Second sync with updated timestamp
        docs_v2 = [
            {"id": "doc-1", "title": "Meeting 1 (edited)", "updated_at": "2025-01-15T00:00:00Z"},
        ]
        sync_exporter.client.get_all_documents.return_value = iter(docs_v2)
        result = sync_exporter.export()

        assert result.documents_exported == 1
        assert result.metadata["sync_statistics"]["updated"] == 1

    def test_timestamp_comparison_handles_format_variations(self, sync_exporter):
        """Sync should correctly compare Z vs +00:00 format timestamps."""
        docs_v1 = [
            {"id": "doc-1", "title": "Test", "updated_at": "2025-01-01T00:00:00Z"},
        ]
        self._setup_basic_mocks(sync_exporter, docs_v1)
        sync_exporter.export()

        # Same timestamp in different format — should be unchanged
        docs_v2 = [
            {"id": "doc-1", "title": "Test", "updated_at": "2025-01-01T00:00:00+00:00"},
        ]
        sync_exporter.client.get_all_documents.return_value = iter(docs_v2)
        result = sync_exporter.export()

        assert result.documents_exported == 0
        assert result.metadata["sync_statistics"]["skipped"] == 1

    def test_manifest_written_atomically(self, sync_exporter):
        """Manifest should be written via temp file (no .tmp left behind)."""
        self._setup_basic_mocks(sync_exporter, [{"id": "d1", "title": "T"}])
        sync_exporter.export()

        out = Path(sync_exporter.output_dir)
        assert (out / "manifest.json").exists()
        assert not (out / "manifest.json.tmp").exists()
