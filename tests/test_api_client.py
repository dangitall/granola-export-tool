"""Tests for API client retry logic."""

import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from granola_export.api_client import APIConfig, GranolaAPIClient


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
