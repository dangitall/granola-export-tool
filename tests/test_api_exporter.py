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
        patch("granola_export.exporters.api_exporter.GranolaAPIClient.from_token"),
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
        patch("granola_export.exporters.api_exporter.GranolaAPIClient.from_token"),
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
            {
                "id": "doc-1",
                "title": "Meeting 1 (edited)",
                "updated_at": "2025-01-15T00:00:00Z",
            },
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


class TestSyncDataSafety:
    """Failures during a sync must not silently lose data."""

    def _setup(self, exporter, docs):
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(docs)
        exporter.client.get_document_transcript.return_value = [{"text": "hi"}]
        exporter.client.get_people.return_value = {}

    def test_failed_transcript_is_retried_next_sync(self, sync_exporter):
        docs = [{"id": "doc-1", "title": "M", "updated_at": "2025-01-01T00:00:00Z"}]
        self._setup(sync_exporter, docs)
        sync_exporter.client.get_document_transcript.side_effect = _make_http_error(500)
        sync_exporter.export()

        # Same document, unchanged timestamp: the transcript is still retried.
        sync_exporter.client.get_all_documents.return_value = iter(docs)
        sync_exporter.client.get_document_transcript.side_effect = None
        result = sync_exporter.export()

        assert result.documents_exported == 1
        assert result.transcripts_exported == 1
        manifest = json.loads((sync_exporter.output_dir / "manifest.json").read_text())
        assert "transcript_pending" not in manifest["documents"]["doc-1"]

    @patch("granola_export.exporters.api_exporter.time.sleep")
    def test_transcripts_skipped_by_cutoff_are_pending(self, _sleep, sync_exporter):
        docs = [
            {"id": f"doc-{i}", "title": "M", "updated_at": "2025-01-01T00:00:00Z"}
            for i in range(7)
        ]
        self._setup(sync_exporter, docs)
        sync_exporter.client.get_document_transcript.side_effect = (
            urllib.error.URLError("down")
        )
        sync_exporter.export()

        manifest = json.loads((sync_exporter.output_dir / "manifest.json").read_text())
        assert all(e.get("transcript_pending") for e in manifest["documents"].values())

    def test_partial_listing_keeps_previous_output(self, sync_exporter):
        docs = [
            {"id": "doc-1", "title": "A", "updated_at": "2025-01-01T00:00:00Z"},
            {"id": "doc-2", "title": "B", "updated_at": "2025-01-01T00:00:00Z"},
        ]
        self._setup(sync_exporter, docs)
        sync_exporter.export()
        out = sync_exporter.output_dir
        before = (out / "all_meetings.json").read_text()

        def partial():
            yield docs[0]
            raise _make_http_error(500)

        sync_exporter.client.get_all_documents.return_value = partial()
        result = sync_exporter.export()

        assert not result.success
        assert (out / "all_meetings.json").read_text() == before
        manifest = json.loads((out / "manifest.json").read_text())
        assert set(manifest["documents"]) == {"doc-1", "doc-2"}

    def test_rename_removes_stale_file(self, sync_exporter):
        docs = [{"id": "doc-1abcd", "title": "Old", "updated_at": "2025-01-01T00:00Z"}]
        self._setup(sync_exporter, docs)
        sync_exporter.export()

        renamed = [
            {"id": "doc-1abcd", "title": "New", "updated_at": "2025-02-01T00:00Z"}
        ]
        sync_exporter.client.get_all_documents.return_value = iter(renamed)
        sync_exporter.export()

        names = sorted(
            p.name for p in (sync_exporter.output_dir / "meetings").iterdir()
        )
        assert names == ["New_doc-1abc.json"]

    def test_rename_cleanup_keeps_other_documents(self, sync_exporter):
        """A different document sharing the 8-char prefix is never deleted."""
        docs = [{"id": "doc-1abcd", "title": "Mine", "updated_at": "2025-01-01T00:00Z"}]
        self._setup(sync_exporter, docs)
        meetings = sync_exporter.output_dir / "meetings"
        meetings.mkdir(parents=True)
        (meetings / "Other_doc-1abc.json").write_text(json.dumps({"id": "doc-1abzz"}))

        sync_exporter.export()

        assert (meetings / "Other_doc-1abc.json").exists()


class TestFolderTracking:
    """Only confirmed 404s may mark a folder as gone."""

    def _setup(self, exporter):
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_all_documents.return_value = iter([])
        exporter.client.get_people.return_value = {}

    def _write_manifest(self, exporter, manifest):
        exporter.output_dir.mkdir(parents=True, exist_ok=True)
        (exporter.output_dir / "manifest.json").write_text(json.dumps(manifest))

    def test_legacy_deleted_ids_are_reprobed(self, sync_exporter):
        self._setup(sync_exporter)
        self._write_manifest(
            sync_exporter, {"folder_ids": [], "deleted_folder_ids": ["f1", "f2"]}
        )

        def lists(known_ids=None, missing=None):
            missing.add("f2")
            return [{"id": "f1", "title": "Real"}]

        sync_exporter.client.get_document_lists.side_effect = lists
        sync_exporter.export()

        known = sync_exporter.client.get_document_lists.call_args.kwargs["known_ids"]
        assert known == ["f1", "f2"]
        manifest = json.loads((sync_exporter.output_dir / "manifest.json").read_text())
        assert manifest["folder_ids"] == ["f1"]
        assert manifest["missing_folder_ids"] == ["f2"]
        assert "deleted_folder_ids" not in manifest

    def test_transient_failure_keeps_folder(self, sync_exporter):
        self._setup(sync_exporter)
        self._write_manifest(
            sync_exporter, {"folder_ids": ["f1"], "missing_folder_ids": []}
        )
        (sync_exporter.output_dir / "folders.json").write_text(
            json.dumps([{"id": "f1", "title": "Kept"}])
        )
        # Bulk and fallback both failed: nothing fetched, nothing 404'd.
        sync_exporter.client.get_document_lists.return_value = []
        sync_exporter.export()

        manifest = json.loads((sync_exporter.output_dir / "manifest.json").read_text())
        assert manifest["folder_ids"] == ["f1"]
        folders = json.loads((sync_exporter.output_dir / "folders.json").read_text())
        assert folders == [{"id": "f1", "title": "Kept"}]


class TestReviewRegressions:
    def _setup(self, exporter, docs):
        exporter.client.get_workspaces.return_value = []
        exporter.client.get_document_lists.return_value = []
        exporter.client.get_all_documents.return_value = iter(docs)
        exporter.client.get_document_transcript.return_value = None
        exporter.client.get_documents_batch.return_value = []
        exporter.client.get_people.return_value = {}

    def test_case_only_rename_keeps_the_file(self, sync_exporter):
        """On case-insensitive filesystems the current file keeps its old
        spelling; it must not be mistaken for a stale copy."""
        meetings = sync_exporter.output_dir / "meetings"
        meetings.mkdir(parents=True)
        (meetings / "Standup_doc-1abc.json").write_text(json.dumps({"id": "doc-1abcd"}))
        (meetings / "Old_doc-1abc.json").write_text(json.dumps({"id": "doc-1abcd"}))
        docs = [{"id": "doc-1abcd", "title": "standup", "updated_at": "2025-01-01"}]
        self._setup(sync_exporter, docs)

        sync_exporter.export()

        remaining = [
            p
            for p in meetings.iterdir()
            if json.loads(p.read_text()).get("id") == "doc-1abcd"
        ]
        assert len(remaining) == 1
        assert remaining[0].name.lower() == "standup_doc-1abc.json"

    def test_partial_listing_skips_shared_discovery(self, sync_exporter):
        def partial():
            yield {"id": "owned-1", "title": "A"}
            raise _make_http_error(500)

        self._setup(sync_exporter, [])
        sync_exporter.client.get_all_documents.return_value = partial()
        # owned-2 sits in a folder but was on the page that failed.
        sync_exporter.client.get_document_lists.return_value = [
            {"id": "f1", "documents": [{"id": "owned-2"}]}
        ]

        sync_exporter.export()

        sync_exporter.client.get_documents_batch.assert_not_called()

    def test_pending_flag_survives_non_sync_run(self, exporter):
        exporter.output_dir.mkdir(parents=True)
        (exporter.output_dir / "manifest.json").write_text(
            json.dumps(
                {"documents": {"d1": {"updated_at": "x", "transcript_pending": True}}}
            )
        )
        self._setup(exporter, [{"id": "d1", "title": "T", "updated_at": "x"}])
        exporter.include_transcripts = False

        exporter.export()

        manifest = json.loads((exporter.output_dir / "manifest.json").read_text())
        assert manifest["documents"]["d1"]["transcript_pending"] is True

    def test_reappearing_folder_leaves_missing_list(self, sync_exporter):
        sync_exporter.output_dir.mkdir(parents=True)
        (sync_exporter.output_dir / "manifest.json").write_text(
            json.dumps({"folder_ids": [], "missing_folder_ids": ["f1"]})
        )
        self._setup(sync_exporter, [])
        sync_exporter.client.get_document_lists.return_value = [{"id": "f1"}]

        sync_exporter.export()

        manifest = json.loads((sync_exporter.output_dir / "manifest.json").read_text())
        assert manifest["missing_folder_ids"] == []
        assert manifest["folder_ids"] == ["f1"]
