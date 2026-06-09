# tests/prep/test_config_loader.py
from pathlib import Path

import pytest

from prep.config_loader import (
    SourceConfig,
    SourcesFile,
    TargetConfig,
    load_sources_config,
)


def test_load_sources_config_returns_typed_object(tmp_path: Path) -> None:
    yaml_text = """
sources:
  hin:
    name: "Test HIN"
    type: "arcgis_feature_service"
    segments_url: "https://example.com/segments"
    intersections_url: "https://example.com/intersections"
    refresh_cadence: "monthly"
target:
  name: "Test"
  bbox:
    min_lat: 41.0
    max_lat: 42.0
    min_lng: -88.0
    max_lng: -87.0
"""
    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(yaml_text)

    cfg = load_sources_config(cfg_path)

    assert isinstance(cfg, SourcesFile)
    assert isinstance(cfg.sources["hin"], SourceConfig)
    assert "hin" in cfg.sources
    assert cfg.sources["hin"].name == "Test HIN"
    assert cfg.sources["hin"].extra["segments_url"] == "https://example.com/segments"
    assert isinstance(cfg.target, TargetConfig)
    assert cfg.target.bbox == (41.0, 42.0, -88.0, -87.0)


def test_load_sources_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_sources_config(tmp_path / "missing.yaml")


def test_load_sources_config_missing_target_section_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(
        """
sources: {}
"""
    )
    with pytest.raises(KeyError, match="target"):
        load_sources_config(cfg_path)
