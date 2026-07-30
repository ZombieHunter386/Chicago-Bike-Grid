"""Tests for the Geofabrik/osmium graph source.

The osmium invocations are mocked: shelling out to a real 355 MB extract
belongs in the integration build, not the fast suite. What's worth pinning
here is the argument translation (our bbox convention vs osmium's), the
download cache policy, and the failure messages.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prep.graph.pbf_extract import (
    GEOFABRIK_URL_TEMPLATE,
    OsmiumNotInstalledError,
    clip_and_filter_pbf,
    download_region_pbf,
)

# (min_lat, max_lat, min_lng, max_lng) — the TargetConfig.bbox convention.
COOK_BBOX = (41.4697, 42.1543, -88.2636, -87.5240)


@patch("prep.graph.pbf_extract.shutil.which", return_value=None)
def test_missing_osmium_names_the_install_command(_which: MagicMock, tmp_path: Path) -> None:
    """The failure has to be self-service — this is a prereq most people
    won't have, and a bare FileNotFoundError from subprocess wouldn't say so.
    """
    with pytest.raises(OsmiumNotInstalledError) as exc:
        clip_and_filter_pbf(tmp_path / "x.pbf", COOK_BBOX, tmp_path / "w")
    msg = str(exc.value)
    assert "brew install osmium-tool" in msg
    # Must also make clear it isn't needed to run the app.
    assert "prep" in msg.lower()


@patch("prep.graph.pbf_extract._run")
@patch("prep.graph.pbf_extract.shutil.which", return_value="/usr/local/bin/osmium")
def test_bbox_is_translated_to_osmium_west_south_east_north(
    _which: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    """Our bbox is (min_lat, max_lat, min_lng, max_lng); osmium wants
    W,S,E,N. Getting this backwards would silently extract the wrong region
    (or an empty one), which no downstream check would catch.
    """
    src = tmp_path / "illinois.pbf"
    src.write_bytes(b"\0" * 10)
    work = tmp_path / "work"

    # _run is mocked, so create the outputs its callers stat() afterwards.
    def _fake_run(cmd: list[str]) -> None:
        out = Path(cmd[cmd.index("-o") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\0" * 10)

    mock_run.side_effect = _fake_run
    clip_and_filter_pbf(src, COOK_BBOX, work)

    extract_cmd = mock_run.call_args_list[0].args[0]
    assert extract_cmd[1] == "extract"
    bbox_arg = extract_cmd[extract_cmd.index("-b") + 1]
    # W,S,E,N — longitudes first and last, latitudes in the middle.
    assert bbox_arg == "-88.2636,41.4697,-87.524,42.1543"


@patch("prep.graph.pbf_extract._run")
@patch("prep.graph.pbf_extract.shutil.which", return_value="/usr/local/bin/osmium")
def test_pipeline_filters_to_highways_and_emits_xml(
    _which: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    src = tmp_path / "illinois.pbf"
    src.write_bytes(b"\0" * 10)

    def _fake_run(cmd: list[str]) -> None:
        out = Path(cmd[cmd.index("-o") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\0" * 10)

    mock_run.side_effect = _fake_run
    result = clip_and_filter_pbf(src, COOK_BBOX, tmp_path / "work")

    subcommands = [c.args[0][1] for c in mock_run.call_args_list]
    assert subcommands == ["extract", "tags-filter", "cat"]
    # Only road ways survive — buildings/landuse are the bulk of OSM and osmnx
    # would discard them anyway, after paying to parse them as XML.
    assert "w/highway" in mock_run.call_args_list[1].args[0]
    # osmnx's reader takes uncompressed XML.
    assert result.suffix == ".osm"
    assert result.exists()


@patch("prep.graph.pbf_extract.requests.get")
def test_download_reuses_a_recent_file(mock_get: MagicMock, tmp_path: Path) -> None:
    """Geofabrik rebuilds nightly; re-pulling 355 MB every run is wasteful."""
    existing = tmp_path / "illinois-latest.osm.pbf"
    existing.write_bytes(b"\0" * 1000)

    assert download_region_pbf(tmp_path) == existing
    mock_get.assert_not_called()


@patch("prep.graph.pbf_extract.requests.get")
def test_download_refreshes_a_stale_file(mock_get: MagicMock, tmp_path: Path) -> None:
    stale = tmp_path / "illinois-latest.osm.pbf"
    stale.write_bytes(b"old")
    old = time.time() - 30 * 86400
    import os
    os.utime(stale, (old, old))

    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *a: None
    resp.raise_for_status.return_value = None
    resp.iter_content.return_value = [b"new-bytes"]
    mock_get.return_value = resp

    result = download_region_pbf(tmp_path)
    assert result.read_bytes() == b"new-bytes"
    assert mock_get.call_args.args[0] == GEOFABRIK_URL_TEMPLATE.format(region="illinois")


@patch("prep.graph.pbf_extract.requests.get")
def test_interrupted_download_leaves_no_usable_file(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    """A partial download must not be reused as if it were complete — the
    graph would silently cover only part of the county.
    """
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *a: None
    resp.raise_for_status.return_value = None
    resp.iter_content.side_effect = OSError("connection dropped")
    mock_get.return_value = resp

    with pytest.raises(OSError):
        download_region_pbf(tmp_path)
    assert not (tmp_path / "illinois-latest.osm.pbf").exists()
