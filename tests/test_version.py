"""Tests for src/core/version.py."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.version import (
    VERSION, UpdateCheckError, check_for_update, check_for_update_async, parse_version,
)


class FakeResponse:
    def __init__(self, body: str):
        self.body = body

    def read(self):
        return self.body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def serve(monkeypatch, body: str):
    """Answer every urlopen with `body`."""
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout: FakeResponse(body))


def release(tag="v9.9.9",
            url="https://github.com/trustxix/video-editor/releases/tag/v9.9.9",
            body="notes"):
    """A GitHub 'latest release' API payload (only the fields the app reads)."""
    return json.dumps({"tag_name": tag, "html_url": url, "body": body})


def test_version_constant_is_string():
    assert isinstance(VERSION, str)
    assert len(VERSION) > 0


def test_parse_version_basic():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.1.0") == (0, 1, 0)


def test_parse_version_two_components():
    assert parse_version("1.2") == (1, 2)


def test_parse_version_with_suffix():
    """A non-numeric suffix shouldn't crash — extract leading digits."""
    assert parse_version("1.2.beta")[0:2] == (1, 2)
    assert parse_version("2beta")[0] == 2


def test_parse_version_purely_alpha_component():
    """A wholly non-numeric component becomes 0."""
    assert parse_version("alpha") == (0,)


def test_version_comparison_ordering():
    """Tuple comparison gives the right semver-ish ordering."""
    assert parse_version("0.1.0") < parse_version("0.1.1")
    assert parse_version("0.1.0") < parse_version("0.2.0")
    assert parse_version("1.0.0") > parse_version("0.99.99")


def test_http_error_raises_instead_of_reporting_up_to_date(monkeypatch):
    """A failed check must never look like "you're on the latest version"."""
    def fake_open(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    with pytest.raises(UpdateCheckError, match="HTTP 404"):
        check_for_update(current="0.0.0")


@pytest.mark.parametrize("exc", [
    OSError("network unreachable"),
    urllib.error.URLError("getaddrinfo failed"),
    TimeoutError("timed out"),
])
def test_offline_raises_update_check_error(monkeypatch, exc):
    def fake_open(*args, **kwargs):
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    with pytest.raises(UpdateCheckError, match="could not reach GitHub"):
        check_for_update(current="0.0.0")


@pytest.mark.parametrize("body", ["<html>rate limited</html>", "[]", release(tag="")])
def test_unusable_response_raises(monkeypatch, body):
    serve(monkeypatch, body)
    with pytest.raises(UpdateCheckError):
        check_for_update(current="0.0.0")


def test_returns_dict_when_newer(monkeypatch):
    serve(monkeypatch, release())
    result = check_for_update(current="0.0.1")
    assert result == {
        "latest": "9.9.9",
        "url": "https://github.com/trustxix/video-editor/releases/tag/v9.9.9",
        "notes": "notes",
    }


def test_notes_are_the_first_paragraph_without_bold(monkeypatch):
    serve(monkeypatch, release(body="**Fixed:** a bug.\r\n\r\n## Download\r\n\r\nmore"))
    assert check_for_update(current="0.0.1")["notes"] == "Fixed: a bug."


def test_strips_non_github_url(monkeypatch):
    """Defense-in-depth: even if the response points the user at a non-GitHub
    URL, check_for_update must blank the url field so the UI can't open it."""
    for bad_url in [
        "https://evil.example.com/login",
        "http://github.com/trustxix/video-editor/releases/tag/v9",  # plain http, not https
        "https://github.com.evil.example.com/r",                    # subdomain trick
        "javascript:alert(1)",
        "",
    ]:
        serve(monkeypatch, release(url=bad_url))
        result = check_for_update(current="0.0.1")
        assert result is not None, f"Expected dict result for url={bad_url!r}"
        assert result["url"] == "", f"Expected url to be blanked for {bad_url!r}, got {result['url']!r}"


def test_returns_none_when_same_or_older(monkeypatch):
    serve(monkeypatch, release(tag="v0.0.1"))
    assert check_for_update(current="0.0.1") is None
    assert check_for_update(current="9.9.9") is None


def test_async_hands_the_error_to_the_callback(monkeypatch):
    def fake_open(*args, **kwargs):
        raise OSError("down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    got, done = [], threading.Event()
    check_for_update_async(lambda r: (got.append(r), done.set()))
    assert done.wait(5)
    assert isinstance(got[0], UpdateCheckError)
