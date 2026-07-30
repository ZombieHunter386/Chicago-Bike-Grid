"""Tests for the shared osmnx/Overpass settings helper."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from prep.osm_config import CACHE_FOLDER, DEFAULT_OVERPASS_URL, configure_osmnx


def _fake_ox() -> SimpleNamespace:
    return SimpleNamespace(settings=SimpleNamespace())


def test_defaults_to_public_overpass_and_gitignored_cache() -> None:
    ox = _fake_ox()
    url = configure_osmnx(ox)
    assert url == DEFAULT_OVERPASS_URL
    assert ox.settings.overpass_url == DEFAULT_OVERPASS_URL
    # Must stay under data/ — osmnx otherwise writes ./cache at the repo root,
    # which is not gitignored.
    assert ox.settings.cache_folder == CACHE_FOLDER
    assert CACHE_FOLDER.startswith("data/")
    # osmnx's own limiter reads the server's slot availability; leaving it on is
    # what keeps a tiled county-scale download from tripping the abuse guard.
    assert ox.settings.overpass_rate_limit is True


def test_overpass_url_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebuild must be able to move to a mirror without a code change.

    The Cook County expansion (3.2x the old bbox) multiplied Overpass requests
    enough that the public instance refused connections mid-run and failed the
    build; with a single hard-coded endpoint there was no recourse.

    Uses a placeholder host deliberately: any real replacement must serve
    planet-wide data, and most public mirrors are regional extracts that answer
    an out-of-region query with a well-formed empty result rather than an
    error. See the module docstring for the two-city probe.
    """
    monkeypatch.setenv("OVERPASS_URL", "https://overpass.example.org/api")
    ox = _fake_ox()
    assert configure_osmnx(ox) == "https://overpass.example.org/api"
    assert ox.settings.overpass_url == "https://overpass.example.org/api"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_env_var_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """An empty OVERPASS_URL must not produce a request to "" ."""
    monkeypatch.setenv("OVERPASS_URL", value)
    assert configure_osmnx(_fake_ox()) == DEFAULT_OVERPASS_URL


def test_requests_timeout_only_set_when_given() -> None:
    ox = _fake_ox()
    configure_osmnx(ox)
    assert not hasattr(ox.settings, "requests_timeout")
    configure_osmnx(ox, requests_timeout=120)
    assert ox.settings.requests_timeout == 120
