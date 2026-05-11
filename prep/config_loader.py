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
class BrokenspokeConfig:
    image: str
    city_country: str
    city_name: str
    city_state: str
    city_fips: str
    database_url: str
    network_name: str
    compose_file: str  # path to docker/compose.brokenspoke.yml (Task 10a)


@dataclass(frozen=True)
class TargetConfig:
    name: str
    bbox: tuple[float, float, float, float]  # (min_lat, max_lat, min_lng, max_lng)


@dataclass(frozen=True)
class SourcesFile:
    sources: dict[str, SourceConfig]
    brokenspoke: BrokenspokeConfig
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

    if "brokenspoke" not in raw:
        raise KeyError("sources.yaml missing required section 'brokenspoke'")
    bs = raw["brokenspoke"]
    brokenspoke = BrokenspokeConfig(
        image=bs["image"],
        city_country=bs["city_country"],
        city_name=bs["city_name"],
        city_state=bs["city_state"],
        city_fips=bs["city_fips"],
        database_url=bs["database_url"],
        network_name=bs["network_name"],
        compose_file=bs.get("compose_file", "docker/compose.brokenspoke.yml"),
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

    return SourcesFile(sources=sources, brokenspoke=brokenspoke, target=target)
