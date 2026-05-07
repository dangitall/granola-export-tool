"""Tests for API client retry logic."""

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from granola_export.api_client import (
    APIConfig,
    GranolaAPIClient,
    get_token_from_local,
)


@pytest.fixture
def client():
    """Create a GranolaAPIClient with a fake token."""
    return GranolaAPIClient(APIConfig(access_token="fake-token"))


def _make_http_error(code: int, headers=None) -> urllib.error.HTTPError:
    """Create an HTTPError with the given status code and optional headers."""
    return urllib.error.HTTPError(
        url="https://api.granola.ai/test",
        code=code,
        msg=f"HTTP {code}",
        hdrs=headers or {},
        fp=BytesIO(b""),
    )


def _make_response(data: bytes = b'{}'):
    """Create a mock urllib response."""
    resp = MagicMock()
    resp.read.return_value = data
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestRetryLogic:
    """Tests for _request retry behavior."""

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_retries_on_500_then_succeeds(self, mock_sleep, mock_urlopen, client):
        """500 error should be retried; success on second attempt."""
        mock_urlopen.side_effect = [
            _make_http_error(500),
            _make_response(b'{"ok": true}'),
        ]

        result = client._request("/test", max_retries=3)

        assert result == {"ok": True}
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(1)  # 2**0 = 1

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_retries_exhausted_raises(self, mock_sleep, mock_urlopen, client):
        """After all retries exhausted, the error should be raised."""
        mock_urlopen.side_effect = [
            _make_http_error(500),
            _make_http_error(500),
            _make_http_error(500),
            _make_http_error(500),
        ]

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            client._request("/test", max_retries=3)

        assert exc_info.value.code == 500
        assert mock_sleep.call_count == 3

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_429_respects_retry_after_header(self, mock_sleep, mock_urlopen, client):
        """429 with Retry-After header should use that delay."""
        headers = MagicMock()
        headers.get.return_value = "5"
        err = _make_http_error(429, headers=headers)

        mock_urlopen.side_effect = [
            err,
            _make_response(b'{"ok": true}'),
        ]

        result = client._request("/test", max_retries=3)

        assert result == {"ok": True}
        mock_sleep.assert_called_with(5)

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_retry_after_capped_at_60(self, mock_sleep, mock_urlopen, client):
        """Retry-After values above 60 should be capped."""
        headers = MagicMock()
        headers.get.return_value = "3600"
        err = _make_http_error(429, headers=headers)

        mock_urlopen.side_effect = [
            err,
            _make_response(b'{"ok": true}'),
        ]

        client._request("/test", max_retries=3)

        mock_sleep.assert_called_with(60)

    @patch("urllib.request.urlopen")
    def test_401_not_retried(self, mock_urlopen, client):
        """401 should raise immediately without retrying."""
        mock_urlopen.side_effect = _make_http_error(401)

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            client._request("/test", max_retries=3)

        assert exc_info.value.code == 401
        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_403_not_retried(self, mock_urlopen, client):
        """403 should raise immediately without retrying."""
        mock_urlopen.side_effect = _make_http_error(403)

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            client._request("/test", max_retries=3)

        assert exc_info.value.code == 403
        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_network_error_retried(self, mock_sleep, mock_urlopen, client):
        """URLError (network error) should be retried."""
        mock_urlopen.side_effect = [
            urllib.error.URLError("DNS failed"),
            _make_response(b'{"ok": true}'),
        ]

        result = client._request("/test", max_retries=3)

        assert result == {"ok": True}
        assert mock_sleep.call_count == 1

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_network_error_exhausted_raises(self, mock_sleep, mock_urlopen, client):
        """Network errors after all retries should raise."""
        mock_urlopen.side_effect = urllib.error.URLError("DNS failed")

        with pytest.raises(urllib.error.URLError):
            client._request("/test", max_retries=2)

        assert mock_urlopen.call_count == 3  # initial + 2 retries


class TestPaginationResilience:
    """Tests for get_all_documents error handling during pagination."""

    def test_500_mid_pagination_yields_partial_results(self, client):
        """A 500 during pagination should yield docs fetched so far."""
        page1 = {"docs": [{"id": "d1"}, {"id": "d2"}]}
        page2_error = _make_http_error(500)
        # After the error, pagination skips ahead; second consecutive error stops it
        page3_error = _make_http_error(500)

        with patch.object(client, "get_documents") as mock_get:
            mock_get.side_effect = [page1, page2_error, page3_error]
            docs = list(client.get_all_documents(limit=2))

        assert len(docs) == 2
        assert docs[0]["id"] == "d1"

    def test_two_consecutive_page_errors_stops(self, client):
        """Two consecutive page failures should stop pagination."""
        with patch.object(client, "get_documents") as mock_get:
            mock_get.side_effect = [_make_http_error(500), _make_http_error(500)]
            docs = list(client.get_all_documents(limit=2))

        assert len(docs) == 0

    def test_auth_error_during_pagination_raises(self, client):
        """401/403 during pagination should still propagate."""
        page1 = {"docs": [{"id": "d1"}, {"id": "d2"}]}

        with patch.object(client, "get_documents") as mock_get:
            mock_get.side_effect = [page1, _make_http_error(401)]
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                list(client.get_all_documents(limit=2))

        assert exc_info.value.code == 401


class TestGetTokenFromLocal:
    """Token discovery across Granola v7+ and legacy storage formats."""

    def _write_stored_accounts(self, dir_path, access_token, refresh_token=None):
        """Write a v7-style stored-accounts.json into ``dir_path``."""
        tokens = {"access_token": access_token}
        if refresh_token is not None:
            tokens["refresh_token"] = refresh_token
        accounts = [
            {
                "userId": "u1",
                "email": "test@example.com",
                "tokens": json.dumps(tokens),
            }
        ]
        (dir_path / "stored-accounts.json").write_text(
            json.dumps({"accounts": json.dumps(accounts)})
        )

    def _write_supabase(self, dir_path, access_token, refresh_token=None):
        """Write a legacy supabase.json into ``dir_path``."""
        workos = {"access_token": access_token}
        if refresh_token is not None:
            workos["refresh_token"] = refresh_token
        (dir_path / "supabase.json").write_text(
            json.dumps({"workos_tokens": workos})
        )

    def test_reads_token_from_stored_accounts(self, tmp_path):
        """v7+ format: token comes from stored-accounts.json."""
        self._write_stored_accounts(tmp_path, "v7-token", "v7-refresh")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "v7-token"
        assert config.refresh_token == "v7-refresh"

    def test_prefers_stored_accounts_over_supabase(self, tmp_path):
        """When both files exist, stored-accounts.json wins (it's fresher)."""
        self._write_stored_accounts(tmp_path, "fresh-token")
        self._write_supabase(tmp_path, "stale-token")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "fresh-token"

    def test_falls_back_to_supabase_when_accounts_missing(self, tmp_path):
        """Pre-v7 installs only have supabase.json — still supported."""
        self._write_supabase(tmp_path, "legacy-token", "legacy-refresh")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "legacy-token"
        assert config.refresh_token == "legacy-refresh"

    def test_returns_none_when_no_files_exist(self, tmp_path):
        """No token files at all -> None (caller decides what to do)."""
        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            assert get_token_from_local() is None

    def test_falls_back_when_stored_accounts_malformed(self, tmp_path):
        """A malformed v7 file should not block reading the legacy file."""
        (tmp_path / "stored-accounts.json").write_text("{not valid json")
        self._write_supabase(tmp_path, "legacy-token")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "legacy-token"

    def test_falls_back_when_stored_accounts_has_no_tokens(self, tmp_path):
        """Empty accounts list in v7 file should fall through to legacy."""
        (tmp_path / "stored-accounts.json").write_text(
            json.dumps({"accounts": json.dumps([])})
        )
        self._write_supabase(tmp_path, "legacy-token")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "legacy-token"

    def test_empty_access_token_string_falls_through(self, tmp_path):
        """Treat access_token == '' as missing, not present."""
        self._write_stored_accounts(tmp_path, "")
        self._write_supabase(tmp_path, "legacy-token")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "legacy-token"

    def test_accepts_unwrapped_accounts_and_tokens(self, tmp_path):
        """If Granola ever stops double-encoding, the helper still works."""
        accounts = [
            {
                "userId": "u1",
                "email": "test@example.com",
                # Plain dict, not a JSON-encoded string.
                "tokens": {"access_token": "raw-token", "refresh_token": "raw-r"},
            }
        ]
        # Plain list, not a JSON-encoded string either.
        (tmp_path / "stored-accounts.json").write_text(
            json.dumps({"accounts": accounts})
        )

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "raw-token"
        assert config.refresh_token == "raw-r"

    def test_picks_most_recently_saved_account(self, tmp_path):
        """With multiple accounts, the highest savedAt wins."""
        accounts = [
            {
                "userId": "old",
                "email": "old@example.com",
                "tokens": json.dumps({"access_token": "old-token"}),
                "savedAt": 1_000_000_000_000,
            },
            {
                "userId": "new",
                "email": "new@example.com",
                "tokens": json.dumps({"access_token": "new-token"}),
                "savedAt": 2_000_000_000_000,
            },
        ]
        (tmp_path / "stored-accounts.json").write_text(
            json.dumps({"accounts": json.dumps(accounts)})
        )

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "new-token"
