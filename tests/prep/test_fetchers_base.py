# tests/prep/test_fetchers_base.py
import datetime as dt
from pathlib import Path

import pytest

from prep.fetchers.base import (
    Fetcher,
    FetchResult,
    rotate_snapshots,
    today_snapshot_dir,
)


def test_today_snapshot_dir_returns_dated_subdir(tmp_path: Path) -> None:
    out = today_snapshot_dir(tmp_path, today=dt.date(2026, 5, 5))
    assert out == tmp_path / "2026-05-05"
    assert out.exists()


def test_rotate_snapshots_keeps_only_n_most_recent(tmp_path: Path) -> None:
    # create 5 dated subdirs
    for d in ("2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "marker.txt").write_text(d)

    rotate_snapshots(tmp_path, keep=3)

    remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert remaining == ["2026-05-03", "2026-05-04", "2026-05-05"]


def test_fetcher_subclass_must_implement_fetch(tmp_path: Path) -> None:
    class Incomplete(Fetcher):
        name = "incomplete"

    f = Incomplete()
    with pytest.raises(NotImplementedError):
        f.fetch(tmp_path)


def test_fetcher_concrete_subclass_runs(tmp_path: Path) -> None:
    class FakeFetcher(Fetcher):
        name = "fake"

        def fetch(self, cache_dir: Path) -> FetchResult:
            target = cache_dir / "out.txt"
            target.write_text("data")
            return FetchResult(path=target, record_count=1, status="OK", warnings=[])

    f = FakeFetcher()
    result = f.fetch(tmp_path)
    assert result.path.read_text() == "data"
    assert result.record_count == 1
    assert result.status == "OK"
