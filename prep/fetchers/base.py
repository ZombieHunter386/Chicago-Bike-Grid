# prep/fetchers/base.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FetchResult:
    """Outcome of a single fetch."""

    path: Path
    record_count: int
    status: str  # "OK" | "WARN" | "FAIL"
    warnings: list[str] = field(default_factory=list)


class Fetcher:
    """Base class for source fetchers. Subclasses set `name` and implement `fetch`."""

    name: str = ""

    def fetch(self, cache_dir: Path) -> FetchResult:
        """Fetch the source data and return a FetchResult.

        cache_dir: an existing directory where the fetcher should write its output.
        """
        raise NotImplementedError(f"{type(self).__name__}.fetch not implemented")


def today_snapshot_dir(parent: Path, today: dt.date | None = None) -> Path:
    """Return parent/<YYYY-MM-DD>/, creating it if needed."""
    today = today or dt.date.today()
    out = parent / today.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    return out


def rotate_snapshots(parent: Path, keep: int = 3) -> None:
    """Delete dated subdirectories under parent, keeping only the `keep` most recent.

    Subdirectories are recognized by ISO date format (YYYY-MM-DD).
    """
    import shutil

    if not parent.exists():
        return

    dated: list[tuple[dt.date, Path]] = []
    for child in parent.iterdir():
        if not child.is_dir():
            continue
        try:
            d = dt.date.fromisoformat(child.name)
            dated.append((d, child))
        except ValueError:
            continue

    dated.sort(reverse=True)
    for _, path in dated[keep:]:
        shutil.rmtree(path)
