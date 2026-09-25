"""``SBS_PUBLIC_URL`` — the externally-visible base URL (docs/design/npx.md §5.11).

The install command is absolute and the server composes it, so it must know the
URL a *user's terminal* can reach. That is not derivable from how the process is
bound: ``uvicorn.run`` is called without ``forwarded_allow_ips``, so behind a
container ingress the forwarded headers are ignored and ``request.base_url``
reports the internal bind address — which would hand out commands pointing at
``http://0.0.0.0:8000``, and the user only discovers it when npx fails.

Every case goes through the environment because that is the field's only input:
like every other ``SBSettings`` field it declares a ``validation_alias``, so the
env var *is* the interface.
"""

from __future__ import annotations

import pytest

from skillberry_store.fast_api.server import SBSettings


def _settings(monkeypatch, value=None):
    if value is None:
        monkeypatch.delenv("SBS_PUBLIC_URL", raising=False)
    else:
        monkeypatch.setenv("SBS_PUBLIC_URL", value)
    return SBSettings()


def test_unset_is_none(monkeypatch):
    assert _settings(monkeypatch).public_url is None


def test_env_var_is_read(monkeypatch):
    assert (
        _settings(monkeypatch, "https://store.example.com").public_url
        == "https://store.example.com"
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://store.example.com/", "https://store.example.com"),
        ("https://store.example.com///", "https://store.example.com"),
        ("http://localhost:8000/", "http://localhost:8000"),
        ("  https://store.example.com  ", "https://store.example.com"),
        ("https://store.example.com/base/", "https://store.example.com/base"),
    ],
)
def test_trailing_slash_is_normalised(monkeypatch, value, expected):
    """So a composed URL never contains ``//pub/``."""
    settings = _settings(monkeypatch, value)
    assert settings.public_url == expected
    assert "//pub/" not in f"{settings.public_url}/pub/x"


@pytest.mark.parametrize(
    "value",
    ["store.example.com", "ftp://store", "//store.example.com", "localhost:8000"],
)
def test_a_value_without_an_http_scheme_is_rejected_at_startup(monkeypatch, value):
    """A wrong URL is worse than an absent one — fail loudly, and early."""
    with pytest.raises(ValueError, match="SBS_PUBLIC_URL"):
        _settings(monkeypatch, value)


@pytest.mark.parametrize("value", ["", "   ", "/"])
def test_an_empty_value_collapses_to_none(monkeypatch, value):
    assert _settings(monkeypatch, value).public_url is None
