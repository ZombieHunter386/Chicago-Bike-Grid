"""Tests for app.core.cache — gap_cache schema, R/W, fingerprint check."""
from pathlib import Path

from app.core.cache import (
    bikemap_fingerprint,
    cache_key,
    get_cached_gap,
    init_cache_db,
    put_cached_gap,
)


def test_cache_key_deterministic() -> None:
    k1 = cache_key((41.9, -87.7), (41.88, -87.62), "kid")
    k2 = cache_key((41.9, -87.7), (41.88, -87.62), "kid")
    assert k1 == k2
    # Different tier → different key.
    k3 = cache_key((41.9, -87.7), (41.88, -87.62), "any")
    assert k1 != k3


def test_cache_key_hides_raw_coords() -> None:
    """Cache key should not contain raw lat/lon (spec §3.5: privacy)."""
    k = cache_key((41.9, -87.7), (41.88, -87.62), "kid")
    assert "41.9" not in k
    assert "87.7" not in k


def test_init_cache_db_creates_schema(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="abc123")
    assert cache_path.exists()
    # Re-init with same fingerprint preserves data.
    put_cached_gap(cache_path, "k1", {"foo": "bar"})
    init_cache_db(cache_path, fingerprint="abc123")
    assert get_cached_gap(cache_path, "k1") == {"foo": "bar"}


def test_init_cache_db_truncates_on_fingerprint_mismatch(tmp_path: Path) -> None:
    """Bumped bikemap.db schema/record_count → cache is wiped (spec §3.5)."""
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="abc123")
    put_cached_gap(cache_path, "k1", {"foo": "bar"})
    init_cache_db(cache_path, fingerprint="DIFFERENT")
    assert get_cached_gap(cache_path, "k1") is None


def test_put_then_get_roundtrips_json(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="x")
    payload = {"length_m": 7654.3, "headline": {"road_id": 42}}
    put_cached_gap(cache_path, "key1", payload)
    assert get_cached_gap(cache_path, "key1") == payload


def test_get_returns_none_for_unknown_key(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint="x")
    assert get_cached_gap(cache_path, "missing") is None


def test_bikemap_fingerprint_combines_schema_version_and_record_count(
    tiny_bikemap_db: Path,
) -> None:
    fp = bikemap_fingerprint(tiny_bikemap_db)
    # Fingerprint format is opaque but stable.
    assert isinstance(fp, str)
    assert len(fp) > 0
    assert fp == bikemap_fingerprint(tiny_bikemap_db)
