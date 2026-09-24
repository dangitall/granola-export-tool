"""Tests for the `auth` CLI command (credential-store bootstrap)."""

import json
from unittest.mock import patch

from granola_export.api_client import APIConfig, AuthRefreshError
from granola_export.cli import cmd_auth


class _Args:
    """Minimal argparse.Namespace stand-in for cmd_auth."""

    def __init__(self, refresh_token=None, status=False):
        self.refresh_token = refresh_token
        self.status = status


def _creds_path(config_dir):
    return config_dir / "credentials.json"


class TestAuthSeed:
    def test_valid_token_verified_and_persisted(self, isolated_config_dir):
        """A good refresh token is verified against the endpoint, then saved."""
        minted = APIConfig(access_token="minted-access", refresh_token="good-refresh")
        with patch(
            "granola_export.api_client.refresh_access_token",
            return_value=minted,
        ) as mock_refresh:
            rc = cmd_auth(_Args(refresh_token="good-refresh"))

        assert rc == 0
        mock_refresh.assert_called_once_with("good-refresh")
        stored = json.loads(_creds_path(isolated_config_dir).read_text())
        assert stored["refresh_token"] == "good-refresh"
        assert stored["access_token"] == "minted-access"

    def test_revoked_token_not_persisted(self, isolated_config_dir):
        """A revoked token must fail loudly and never touch the store."""
        with patch(
            "granola_export.api_client.refresh_access_token",
            side_effect=AuthRefreshError("revoked", revoked=True),
        ):
            rc = cmd_auth(_Args(refresh_token="revoked-token"))

        assert rc == 1
        assert not _creds_path(isolated_config_dir).exists()

    def test_transient_failure_not_persisted(self, isolated_config_dir):
        """A 5xx during verification also blocks persistence."""
        with patch(
            "granola_export.api_client.refresh_access_token",
            side_effect=AuthRefreshError("server blip"),
        ):
            rc = cmd_auth(_Args(refresh_token="some-token"))

        assert rc == 1
        assert not _creds_path(isolated_config_dir).exists()

    def test_no_args_is_a_usage_error(self, isolated_config_dir):
        """Neither --refresh-token nor --status -> non-zero, no write."""
        rc = cmd_auth(_Args())
        assert rc == 1
        assert not _creds_path(isolated_config_dir).exists()


class TestAuthStatus:
    def test_status_absent(self, isolated_config_dir):
        rc = cmd_auth(_Args(status=True))
        assert rc == 1  # nothing stored

    def test_status_present(self, isolated_config_dir):
        isolated_config_dir.mkdir(parents=True, exist_ok=True)
        _creds_path(isolated_config_dir).write_text(
            json.dumps({"refresh_token": "r", "access_token": "a"})
        )
        rc = cmd_auth(_Args(status=True))
        assert rc == 0

    def test_status_present_without_refresh_token(self, isolated_config_dir):
        """A store with no refresh_token is useless -> reported as failure."""
        isolated_config_dir.mkdir(parents=True, exist_ok=True)
        _creds_path(isolated_config_dir).write_text(json.dumps({"access_token": "a"}))
        rc = cmd_auth(_Args(status=True))
        assert rc == 1

    def test_status_unreadable_store(self, isolated_config_dir):
        isolated_config_dir.mkdir(parents=True, exist_ok=True)
        _creds_path(isolated_config_dir).write_text("{ not json")
        rc = cmd_auth(_Args(status=True))
        assert rc == 1


class TestAuthSeedFromStdin:
    def test_dash_reads_token_from_stdin(self, isolated_config_dir, monkeypatch):
        """`--refresh-token -` keeps the secret out of argv."""
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("stdin-refresh\n"))
        minted = APIConfig(access_token="a", refresh_token="stdin-refresh")
        with patch(
            "granola_export.api_client.refresh_access_token", return_value=minted
        ) as mock_refresh:
            rc = cmd_auth(_Args(refresh_token="-"))

        assert rc == 0
        mock_refresh.assert_called_once_with("stdin-refresh")

    def test_empty_stdin_fails(self, isolated_config_dir, monkeypatch):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        assert cmd_auth(_Args(refresh_token="-")) == 1
