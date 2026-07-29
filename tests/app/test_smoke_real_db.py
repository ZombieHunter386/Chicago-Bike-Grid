"""End-to-end smoke test against the real Chicago bikemap.db.

Skipped by default (slow). Run explicitly:
    .venv/bin/pytest -m slow tests/app/test_smoke_real_db.py
"""
import platform
from pathlib import Path

import psutil
import pytest

REAL_DB = Path(__file__).parent.parent.parent / "data" / "bikemap.db"


@pytest.mark.slow
@pytest.mark.skipif(not REAL_DB.exists(), reason="real bikemap.db missing")
def test_routes_and_memory_against_real_db(tmp_path: Path) -> None:
    from app.main import create_app

    p = psutil.Process()
    pre_mb = p.memory_info().rss / 1024 / 1024

    app = create_app(
        bikemap_db=REAL_DB,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="smoke-test/1.0",
        min_streets=10000,
    )
    post_mb = p.memory_info().rss / 1024 / 1024

    print(f"\nMemory: pre={pre_mb:.0f} MB, post={post_mb:.0f} MB, delta={post_mb - pre_mb:.0f} MB")

    # Spec §6.4 #9: resident memory < 480 MB.
    assert post_mb < 480, f"memory budget exceeded: {post_mb:.0f} MB"

    client = app.test_client()
    # Lake View → Loop, 'death_wish' tier.
    resp = client.post("/routes", json={
        "home": {"lat": 41.9398, "lon": -87.6685},
        "dest": {"lat": 41.8819, "lon": -87.6278},
        "tier": "death_wish",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["fast"] is not None
    assert data["safe"] is not None
    fast_mi = data["fast"]["length_m"] / 1609.34
    safe_mi = data["safe"]["length_m"] / 1609.34
    print(f"Routes: fast={fast_mi:.2f} mi, safe={safe_mi:.2f} mi")
    # Crow-flies is ~4.5 mi; reasonable routes are 4.5-7 mi.
    assert 4.0 < fast_mi < 8.0, f"fast={fast_mi:.2f} mi outside expected range"
    assert 4.0 < safe_mi < 12.0, f"safe={safe_mi:.2f} mi outside expected range"

    # Health endpoint reports vertex/edge counts.
    h = client.get("/health").get_json()
    assert h["status"] == "ok"
    assert h["streets"] >= 100000   # Chicago has ~350k segments
    assert h["vertices"] >= 100000
    print(f"Health: streets={h['streets']}, vertices={h['vertices']}")


@pytest.mark.slow
@pytest.mark.skipif(not REAL_DB.exists(), reason="real bikemap.db missing")
@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="memory budget is for Linux/Render production; macOS over-reports RSS",
)
def test_memory_under_sustained_load(tmp_path: Path) -> None:
    """Spec §6.4 #9: <480 MB RSS while serving 60 req/min for 5 min.

    This is the canonical launch criterion. Single-shot startup measurement
    (in test_routes_and_memory_against_real_db) is a lower bound — it does
    not capture per-request leaks, growing caches, or other memory pressure
    that accumulates under sustained traffic.

    Mix is 80% /routes (dominant production traffic), 10% /pois, 10% /health.
    /gap-analysis is omitted: per-IP cap is 10/min, exceeding it would
    distort the test by hitting the rate limiter rather than the workload.
    """
    import time

    from app.main import create_app

    app = create_app(
        bikemap_db=REAL_DB,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="smoke-test/1.0",
        min_streets=10000,
    )
    client = app.test_client()
    proc = psutil.Process()
    pre_mb = proc.memory_info().rss / 1024 / 1024
    peak_mb = pre_mb

    # 60 requests/min × 5 min = 300 requests, paced at 1s intervals.
    n_requests = 300
    interval_s = 1.0

    routes_payload = {
        "home": {"lat": 41.9398, "lon": -87.6685},
        "dest": {"lat": 41.8819, "lon": -87.6278},
        "tier": "death_wish",
    }
    pois_payload = {"near": {"lat": 41.94, "lon": -87.67}, "category": "school"}

    started = time.time()
    for i in range(n_requests):
        kind = i % 10
        if kind < 8:
            client.post("/routes", json=routes_payload)
        elif kind == 8:
            client.post("/pois", json=pois_payload)
        else:
            client.get("/health")
        # Record RSS each request.
        rss_mb = proc.memory_info().rss / 1024 / 1024
        peak_mb = max(peak_mb, rss_mb)
        # Pace to the target interval.
        target_elapsed = (i + 1) * interval_s
        actual_elapsed = time.time() - started
        if actual_elapsed < target_elapsed:
            time.sleep(target_elapsed - actual_elapsed)

    duration = time.time() - started
    print(
        f"\nsustained load RSS: pre={pre_mb:.0f} MB, peak={peak_mb:.0f} MB, "
        f"duration={duration:.0f}s, requests={n_requests}",
    )
    # Spec §6.4 #9: <480 MB on Linux/Render.
    assert peak_mb < 480, (
        f"peak RSS {peak_mb:.0f} MB exceeded 480 MB ceiling under load"
    )
