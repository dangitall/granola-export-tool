"""CLI read commands work from an api-export directory."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from granola_export import cli
from granola_export.models import ExportResult
from granola_export.paths import (
    DATA_DIR_ENV,
    get_default_data_dir,
    record_export_dir,
)


def _run(*argv):
    with patch.object(sys, "argv", ["granola-export", "--no-color", *argv]):
        return cli.main()


@pytest.fixture
def export_dir(tmp_path):
    root = tmp_path / "export"
    (root / "transcripts").mkdir(parents=True)
    docs = [
        {
            "id": f"{i:08d}-x",
            "title": f"Meeting {i}",
            "created_at": f"2026-01-0{i}T10:00:00Z",
        }
        for i in range(1, 4)
    ]
    (root / "all_meetings.json").write_text(json.dumps({"meetings": docs}))
    (root / "manifest.json").write_text(
        json.dumps({"export_date": "2026-01-04T00:00:00", "errors": []})
    )
    return root


class TestDataDirResolution:
    def test_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "env"))
        record_export_dir(tmp_path / "recorded")

        assert get_default_data_dir() == tmp_path / "env"

    def test_last_export_dir_used(self, tmp_path):
        record_export_dir(tmp_path / "recorded")

        assert get_default_data_dir() == (tmp_path / "recorded").resolve()

    def test_falls_back_to_home_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))

        assert get_default_data_dir() == tmp_path / "granola-api-export"


class TestReadCommands:
    def test_list_json(self, export_dir, capsys):
        assert _run("--data-dir", str(export_dir), "list", "--json") == 0

        rows = json.loads(capsys.readouterr().out)
        assert [r["title"] for r in rows] == ["Meeting 3", "Meeting 2", "Meeting 1"]
        assert rows[0]["has_transcript"] is False

    def test_list_uses_recorded_export_dir(self, export_dir, capsys):
        record_export_dir(export_dir)

        assert _run("list", "--json") == 0
        assert len(json.loads(capsys.readouterr().out)) == 3

    def test_missing_export_fails_with_hint(self, tmp_path, capsys):
        assert _run("--data-dir", str(tmp_path / "none"), "list") == 1
        assert "api-export --sync" in capsys.readouterr().err

    def test_check_ok(self, export_dir, capsys):
        assert _run("--data-dir", str(export_dir), "check") == 0
        assert "Documents:   3" in capsys.readouterr().out

    def test_check_missing_exits_1(self, tmp_path):
        assert _run("--data-dir", str(tmp_path / "none"), "check") == 1

    def test_check_reports_last_run_errors(self, export_dir):
        (export_dir / "manifest.json").write_text(
            json.dumps({"export_date": "2026-01-04T00:00:00", "errors": ["boom"]})
        )

        assert _run("--data-dir", str(export_dir), "check") == 1

    def test_export_refuses_to_overwrite_source(self, export_dir):
        before = (export_dir / "all_meetings.json").read_text()

        rc = _run("--data-dir", str(export_dir), "export", "-o", str(export_dir))

        assert rc == 1
        assert (export_dir / "all_meetings.json").read_text() == before

    def test_export_markdown(self, export_dir, tmp_path):
        out = tmp_path / "md"

        assert (
            _run("--data-dir", str(export_dir), "export", "-f", "md", "-o", str(out))
            == 0
        )
        assert len(list(out.glob("2026-01-0*_Meeting *.md"))) == 3


class TestApiExportRecordsDir:
    def test_output_dir_is_recorded(self, tmp_path):
        exporter = MagicMock()
        exporter.client.check_connection.return_value = True
        exporter.export.return_value = ExportResult(
            success=True,
            output_path="x",
            documents_exported=0,
            transcripts_exported=0,
            format="api",
        )
        out = tmp_path / "synced"
        with patch(
            "granola_export.exporters.api_exporter.APIExporter",
            return_value=exporter,
        ):
            assert _run("api-export", "--token", "t", "-o", str(out)) == 0

        assert get_default_data_dir() == out.resolve()
