"""Tests for API client retry logic."""

import base64
import json
import os
import stat
import time
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from granola_export.api_client import (
    APIConfig,
    AuthRefreshError,
    GranolaAPIClient,
    _is_jwt_expired,
    get_token_from_local,
    refresh_access_token,
)


def _make_jwt(exp_offset_seconds: int) -> str:
    """Build a fake JWT whose ``exp`` is ``now + exp_offset_seconds``.

    Only the payload's ``exp`` claim matters for ``_is_jwt_expired``; the
    header and signature can be arbitrary base64url-encoded bytes.
    """
    payload = json.dumps({"exp": int(time.time()) + exp_offset_seconds}).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


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


class TestPersistedCredentialStore:
    """The tool's own credential store: fallback + write-back behavior.

    Granola now writes tokens only to encrypted files whose key we cannot
    read, so we mirror any refresh_token we do see into our own store and
    fall back to it when Granola's plaintext files are gone.
    """

    def _write_stored_accounts(self, dir_path, access_token, refresh_token=None):
        tokens = {"access_token": access_token}
        if refresh_token is not None:
            tokens["refresh_token"] = refresh_token
        accounts = [{"userId": "u1", "tokens": json.dumps(tokens)}]
        (dir_path / "stored-accounts.json").write_text(
            json.dumps({"accounts": json.dumps(accounts)})
        )

    def test_falls_back_to_persisted_store(self, tmp_path, isolated_config_dir):
        """No Granola files, but our store has a fresh token -> use it."""
        creds = isolated_config_dir / "credentials.json"
        creds.parent.mkdir(parents=True, exist_ok=True)
        creds.write_text(
            json.dumps(
                {
                    "access_token": _make_jwt(3600),  # still valid
                    "refresh_token": "persisted-refresh",
                }
            )
        )

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,  # empty -> no Granola token files
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.refresh_token == "persisted-refresh"

    def test_reading_granola_token_writes_back_to_store(
        self, tmp_path, isolated_config_dir
    ):
        """A refresh_token read from Granola is mirrored into our store."""
        self._write_stored_accounts(tmp_path, _make_jwt(3600), "granola-refresh")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            get_token_from_local()

        creds = isolated_config_dir / "credentials.json"
        assert creds.exists()
        stored = json.loads(creds.read_text())
        assert stored["refresh_token"] == "granola-refresh"

    @patch("urllib.request.urlopen")
    def test_persisted_only_refresh_triggers_refresh(
        self, mock_urlopen, tmp_path, isolated_config_dir
    ):
        """Store with a refresh_token but no access_token refreshes on read."""
        mock_urlopen.return_value = _make_response(
            json.dumps(
                {
                    "access_token": "minted-access",
                    "refresh_token": "persisted-refresh",
                }
            ).encode()
        )
        creds = isolated_config_dir / "credentials.json"
        creds.parent.mkdir(parents=True, exist_ok=True)
        creds.write_text(json.dumps({"refresh_token": "persisted-refresh"}))

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            config = get_token_from_local()

        assert config is not None
        assert config.access_token == "minted-access"
        # Refreshed result is written back so subsequent runs stay warm.
        stored = json.loads(creds.read_text())
        assert stored["access_token"] == "minted-access"

    def test_returns_none_when_no_files_and_no_store(
        self, tmp_path, isolated_config_dir
    ):
        """Nothing anywhere -> None, and the warning names our store path."""
        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ):
            assert get_token_from_local() is None

    def test_corrupt_store_falls_through_to_none(
        self, tmp_path, isolated_config_dir
    ):
        """A garbage credentials.json must not crash discovery."""
        creds = isolated_config_dir / "credentials.json"
        creds.parent.mkdir(parents=True, exist_ok=True)
        creds.write_text("{ not valid json")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,  # no Granola files either
        ):
            assert get_token_from_local() is None

    @pytest.mark.skipif(
        os.name == "nt", reason="POSIX file permissions not meaningful on Windows"
    )
    def test_store_written_with_owner_only_perms(self, isolated_config_dir):
        """The credential file holds a long-lived secret -> 0600."""
        from granola_export.api_client import _persist_credentials

        _persist_credentials(APIConfig(access_token="a", refresh_token="r"))

        creds = isolated_config_dir / "credentials.json"
        assert creds.exists()
        assert stat.S_IMODE(creds.stat().st_mode) == 0o600


class TestIsJwtExpired:
    """`_is_jwt_expired` is the gate that decides whether to refresh."""

    def test_expired_jwt_returns_true(self):
        """A JWT whose exp is in the past must be flagged as expired."""
        assert _is_jwt_expired(_make_jwt(exp_offset_seconds=-3600)) is True

    def test_fresh_jwt_returns_false(self):
        """A JWT with an exp comfortably in the future is not expired."""
        assert _is_jwt_expired(_make_jwt(exp_offset_seconds=3600)) is False

    def test_jwt_within_margin_returns_true(self):
        """An exp inside the safety margin (default 60s) is treated as expired.

        This protects against refresh-token races and small clock skew when
        the access token would otherwise expire mid-request.
        """
        assert _is_jwt_expired(_make_jwt(exp_offset_seconds=30)) is True

    def test_non_jwt_token_returns_false(self):
        """Unparseable tokens preserve current behavior: don't force a refresh.

        Old supabase-format tokens were opaque strings rather than JWTs;
        defaulting to "not expired" keeps those code paths working.
        """
        assert _is_jwt_expired("not-a-jwt") is False

    def test_jwt_without_exp_claim_returns_false(self):
        """A well-formed JWT missing the ``exp`` claim is treated as fresh.

        We have no evidence of expiry, so refusing to refresh is the
        conservative choice — the request will fail at use-time if the
        token really is bad.
        """
        payload = base64.urlsafe_b64encode(b'{"sub":"x"}').rstrip(b"=").decode()
        assert _is_jwt_expired(f"h.{payload}.s") is False


class TestRefreshAccessToken:
    """`refresh_access_token` calls Granola's `/v1/refresh-access-token`."""

    def _refresh_response(self, **overrides) -> bytes:
        """Build a realistic refresh-endpoint success payload."""
        payload = {
            "access_token": "fresh-access-token",
            "refresh_token": "same-refresh-token",
            "expires_in": 21599,
            "token_type": "Bearer",
            "obtained_at": int(time.time() * 1000),
            "external_id": "external-id",
            "session_id": "session_id",
            "sign_in_method": "GoogleOAuth",
        }
        payload.update(overrides)
        return json.dumps(payload).encode()

    @patch("urllib.request.urlopen")
    def test_success_returns_new_apiconfig(self, mock_urlopen):
        """200 response yields an APIConfig with the new access_token."""
        mock_urlopen.return_value = _make_response(self._refresh_response())

        config = refresh_access_token("stale-refresh-token")

        assert isinstance(config, APIConfig)
        assert config.access_token == "fresh-access-token"
        assert config.refresh_token == "same-refresh-token"

    @patch("urllib.request.urlopen")
    def test_posts_to_correct_endpoint_with_no_bearer(self, mock_urlopen):
        """The refresh endpoint must be POSTed *without* an Authorization header.

        Granola's request interceptor explicitly excludes this URL from the
        bearer-injecting middleware; sending a Bearer token would be wrong
        and may cause the server to reject the request.
        """
        mock_urlopen.return_value = _make_response(self._refresh_response())

        refresh_access_token("a-refresh-token")

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.granola.ai/v1/refresh-access-token"
        assert req.get_method() == "POST"
        # urllib lowercases header names internally; check both forms.
        assert "authorization" not in {k.lower() for k in req.headers}
        assert json.loads(req.data) == {"refresh_token": "a-refresh-token"}

    @patch("urllib.request.urlopen")
    def test_logout_user_raises_auth_refresh_error(self, mock_urlopen):
        """A 401 with body ``{"error": "logout_user"}`` means the refresh
        token itself was revoked. The user must re-sign-in via Granola;
        retrying won't help, so we raise a typed error.
        """
        err = _make_http_error(401)
        err.fp = BytesIO(b'{"error":"logout_user"}')
        mock_urlopen.side_effect = err

        with pytest.raises(AuthRefreshError):
            refresh_access_token("revoked-token")

    @patch("urllib.request.urlopen")
    def test_5xx_wrapped_in_auth_refresh_error(self, mock_urlopen):
        """Transient server errors during refresh must surface as the
        same typed exception the CLI already handles — not as a raw
        urllib HTTPError, which would leak as a traceback from cron.
        """
        mock_urlopen.side_effect = _make_http_error(500)

        with pytest.raises(AuthRefreshError) as excinfo:
            refresh_access_token("any-token")

        # The original urllib error must be chained so logs can surface it.
        assert isinstance(excinfo.value.__cause__, urllib.error.HTTPError)
        assert excinfo.value.__cause__.code == 500

    @patch("urllib.request.urlopen")
    def test_network_error_wrapped_in_auth_refresh_error(self, mock_urlopen):
        """URLError (DNS, connection refused, timeout) is wrapped too."""
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        with pytest.raises(AuthRefreshError) as excinfo:
            refresh_access_token("any-token")

        assert isinstance(excinfo.value.__cause__, urllib.error.URLError)

    @patch("urllib.request.urlopen")
    def test_non_logout_401_wrapped_with_body_preserved(self, mock_urlopen):
        """A 401 with a non-``logout_user`` body is wrapped, and the
        original HTTPError remains inspectable (body preserved) for
        diagnostic logging downstream.
        """
        err = _make_http_error(401)
        err.fp = BytesIO(b'{"error":"some_other_code"}')
        mock_urlopen.side_effect = err

        with pytest.raises(AuthRefreshError) as excinfo:
            refresh_access_token("any-token")

        cause = excinfo.value.__cause__
        assert isinstance(cause, urllib.error.HTTPError)
        # If we forgot to restore the body, this would read as b"".
        assert cause.fp.read() == b'{"error":"some_other_code"}'

    @patch("urllib.request.urlopen")
    def test_malformed_response_wrapped_in_auth_refresh_error(self, mock_urlopen):
        """A 200 with no ``access_token`` is a protocol violation; wrap it."""
        mock_urlopen.return_value = _make_response(b'{"unexpected": "shape"}')

        with pytest.raises(AuthRefreshError):
            refresh_access_token("any-token")


class TestGetTokenFromLocalRefresh:
    """`get_token_from_local` transparently refreshes expired access tokens."""

    def _write_accounts(self, dir_path, access_token, refresh_token):
        accounts = [
            {
                "userId": "u1",
                "email": "test@example.com",
                "tokens": json.dumps(
                    {"access_token": access_token, "refresh_token": refresh_token}
                ),
            }
        ]
        (dir_path / "stored-accounts.json").write_text(
            json.dumps({"accounts": json.dumps(accounts)})
        )

    def test_fresh_token_is_used_as_is(self, tmp_path):
        """Don't hit the network when the cached access_token is still valid."""
        self._write_accounts(tmp_path, _make_jwt(exp_offset_seconds=3600), "r1")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ), patch(
            "granola_export.api_client.refresh_access_token"
        ) as mock_refresh:
            config = get_token_from_local()

        assert config is not None
        assert config.refresh_token == "r1"
        mock_refresh.assert_not_called()

    def test_expired_token_triggers_refresh(self, tmp_path):
        """The whole point of this change: stale JWT → refresh, return new config."""
        self._write_accounts(tmp_path, _make_jwt(exp_offset_seconds=-3600), "r1")

        refreshed = APIConfig(access_token="new-jwt", refresh_token="r1")
        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ), patch(
            "granola_export.api_client.refresh_access_token",
            return_value=refreshed,
        ) as mock_refresh:
            config = get_token_from_local()

        mock_refresh.assert_called_once_with("r1")
        assert config is refreshed

    def test_expired_token_without_refresh_token_returns_stale(self, tmp_path):
        """If we have nothing to refresh with, return what we've got rather
        than blow up here. The caller will see a 401 and surface it via the
        existing AuthenticationError path — same behavior as before this fix.
        """
        self._write_accounts(tmp_path, _make_jwt(exp_offset_seconds=-3600), "")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ), patch(
            "granola_export.api_client.refresh_access_token"
        ) as mock_refresh:
            config = get_token_from_local()

        mock_refresh.assert_not_called()
        assert config is not None
        # The stale access_token comes through unchanged.
        assert config.access_token.startswith("header.")

    def test_refresh_logout_user_propagates(self, tmp_path):
        """If the refresh_token itself is revoked, propagate AuthRefreshError
        so the CLI can tell the user to re-sign-in via Granola.
        """
        self._write_accounts(tmp_path, _make_jwt(exp_offset_seconds=-3600), "r1")

        with patch(
            "granola_export.paths.get_granola_data_dir",
            return_value=tmp_path,
        ), patch(
            "granola_export.api_client.refresh_access_token",
            side_effect=AuthRefreshError("sign-in expired"),
        ):
            with pytest.raises(AuthRefreshError):
                get_token_from_local()
