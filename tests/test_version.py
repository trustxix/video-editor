"""Tests for src/core/version.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.version import VERSION, check_for_update, parse_version


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


def test_check_for_update_handles_404_gracefully(monkeypatch):
    """Until release/latest.json exists at the published URL, requests 404.
    The function must swallow that and return None — never raise."""
    import urllib.error
    import urllib.request

    def fake_open(*args, **kwargs):
        raise urllib.error.HTTPError(
            "url", 404, "Not Found", hdrs=None, fp=None
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert check_for_update(current="0.0.0") is None


def test_check_for_update_offline_returns_none(monkeypatch):
    """If the URL is unreachable, returns None (no exception)."""
    import urllib.request
    from src.core import version as version_mod

    # Force the URL past the placeholder check
    monkeypatch.setattr(version_mod, "UPDATE_URL",
                        "https://example.invalid/latest.json")

    def fake_open(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert check_for_update(current="0.0.0") is None


def test_check_for_update_returns_dict_when_newer(monkeypatch):
    """Mock a JSON response advertising a higher version on a real github.com URL."""
    import urllib.request
    from src.core import version as version_mod

    monkeypatch.setattr(version_mod, "UPDATE_URL",
                        "https://example.invalid/latest.json")

    class FakeResponse:
        def __init__(self, body): self.body = body
        def read(self): return self.body.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *args): pass

    fake_body = ('{"latest": "9.9.9", '
                 '"url": "https://github.com/trustxix/video-editor/releases/tag/v9.9.9", '
                 '"notes": "test"}')
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout: FakeResponse(fake_body))

    result = check_for_update(current="0.0.1")
    assert result is not None
    assert result["latest"] == "9.9.9"
    assert result["url"] == "https://github.com/trustxix/video-editor/releases/tag/v9.9.9"


def test_check_for_update_strips_non_github_url(monkeypatch):
    """Defense-in-depth: even if the latest.json points the user at a non-GitHub
    URL (compromised CDN or hijacked repo), check_for_update must blank the
    url field so the UI can't open it."""
    import urllib.request
    from src.core import version as version_mod

    monkeypatch.setattr(version_mod, "UPDATE_URL",
                        "https://example.invalid/latest.json")

    class FakeResponse:
        def __init__(self, body): self.body = body
        def read(self): return self.body.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *args): pass

    # Adversarial payload: phishing URL where 'github.com' looks legit
    for bad_url in [
        "https://evil.example.com/login",
        "http://github.com/trustxix/video-editor/releases/tag/v9",  # plain http, not https
        "https://github.com.evil.example.com/r",                    # subdomain trick
        "javascript:alert(1)",
        "",
    ]:
        fake_body = (f'{{"latest": "9.9.9", "url": "{bad_url}", "notes": ""}}')
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda req, timeout: FakeResponse(fake_body))
        result = check_for_update(current="0.0.1")
        assert result is not None, f"Expected dict result for url={bad_url!r}"
        assert result["url"] == "", f"Expected url to be blanked for {bad_url!r}, got {result['url']!r}"


def test_check_for_update_returns_none_when_same_or_older(monkeypatch):
    """If latest <= current, return None (don't surface 'update')."""
    import urllib.request
    from src.core import version as version_mod

    monkeypatch.setattr(version_mod, "UPDATE_URL",
                        "https://example.invalid/latest.json")

    class FakeResponse:
        def __init__(self, body): self.body = body
        def read(self): return self.body.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *args): pass

    fake_body = '{"latest": "0.0.1", "url": "x", "notes": "x"}'
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout: FakeResponse(fake_body))

    assert check_for_update(current="0.0.1") is None
    assert check_for_update(current="9.9.9") is None
