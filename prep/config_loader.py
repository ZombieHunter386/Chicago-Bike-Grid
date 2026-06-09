# prep/config_loader.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SourceConfig:
    name: str
    type: str
    refresh_cadence: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetConfig:
    name: str
    bbox: tuple[float, float, float, float]  # (min_lat, max_lat, min_lng, max_lng)


@dataclass(frozen=True)
class SourcesFile:
    sources: dict[str, SourceConfig]
    target: TargetConfig


def load_sources_config(path: Path) -> SourcesFile:
    """Load and parse sources.yaml into typed config objects."""
    if not path.exists():
        raise FileNotFoundError(f"sources config not found: {path}")
    raw = yaml.safe_load(path.read_text())

    sources: dict[str, SourceConfig] = {}
    for key, src in raw.get("sources", {}).items():
        known_keys = {"name", "type", "refresh_cadence"}
        sources[key] = SourceConfig(
            name=src["name"],
            type=src["type"],
            refresh_cadence=src["refresh_cadence"],
            extra={k: v for k, v in src.items() if k not in known_keys},
        )

    if "target" not in raw:
        raise KeyError("sources.yaml missing required section 'target'")
    tg = raw["target"]
    target = TargetConfig(
        name=tg["name"],
        bbox=(
            float(tg["bbox"]["min_lat"]),
            float(tg["bbox"]["max_lat"]),
            float(tg["bbox"]["min_lng"]),
            float(tg["bbox"]["max_lng"]),
        ),
    )

    return SourcesFile(sources=sources, target=target)
