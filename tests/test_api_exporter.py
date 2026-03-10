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
    with patch(
        "granola_export.exporters.api_exporter.GranolaAPIClient.from_token"
    ) as mock_from_token:
        mock_client = MagicMock()
        mock_from_token.return_value = mock_client
        exp = APIExporter(
            output_dir=tmp_path / "export",
            access_token="fake-token",
        )
    return exp


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
