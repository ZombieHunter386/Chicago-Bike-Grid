"""Tests for /gap-analysis + /gap-analysis/status."""
import time
from pathlib import Path

import pytest


@pytest.fixture
def gap_app(tmp_path: Path, tiny_bikemap_db: Path):
    from flask import Flask
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    from app.core.cache import bikemap_fingerprint, init_cache_db
    from app.core.graph import load_graph
    from app.routes.gap_analysis import build_gap_analysis_blueprint

    cache_path = tmp_path / "cache.db"
    init_cache_db(cache_path, fingerprint=bikemap_fingerprint(tiny_bikemap_db))

    app = Flask(__name__)
    # Build a real Limiter; tests don't actually exhaust the rate limit.
    limiter = Limiter(get_remote_address, app=app)
    app.register_blueprint(build_gap_analysis_blueprint(
        snap=load_graph(tiny_bikemap_db),
        cache_db=cache_path,
        limiter=limiter,
    ))
    return app


def _wait_until_ready(client, job_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = client.get(f"/gap-analysis/status?job={job_id}")
        data = resp.get_json()
        if data["status"] in ("ready", "error"):
            return data
        time.sleep(0.05)
    raise TimeoutError("gap-analysis job did not complete")


def test_gap_analysis_first_call_returns_running_then_ready(gap_app) -> None:
    client = gap_app.test_client()
    resp = client.post("/gap-analysis", json={
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.935, "lon": -87.675},
        "tier": "death_wish",
    })
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "running"
    assert "job_id" in data
    final = _wait_until_ready(client, data["job_id"])
    assert final["status"] == "ready"
    assert "result" in final


def test_gap_analysis_cache_hit_returns_ready_immediately(gap_app) -> None:
    client = gap_app.test_client()
    body = {
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.935, "lon": -87.675},
        "tier": "death_wish",
    }
    # Prime cache
    first = client.post("/gap-analysis", json=body)
    job_id = first.get_json()["job_id"]
    _wait_until_ready(client, job_id)
    # Second call should be cache hit
    second = client.post("/gap-analysis", json=body)
    assert second.status_code == 200
    data = second.get_json()
    assert data["status"] == "ready"
    assert "result" in data


def test_gap_analysis_status_404_for_unknown_job(gap_app) -> None:
    client = gap_app.test_client()
    resp = client.get("/gap-analysis/status?job=nonsense")
    assert resp.status_code == 404


def test_gap_analysis_dedupes_in_flight_jobs_by_cache_key(gap_app) -> None:
    """Two rapid POSTs with identical (home, dest, tier) must share a single
    Future, not queue duplicate work in the executor. The endpoint returns
    the same job_id (== cache_key) for both, and polling either job_id
    converges to the same ready result.

    Pre-fix, a state-mutation storm (drill-down enter/exit, tier toggle,
    etc.) could burn the 10-req/min rate limit budget on duplicate work
    AND swamp the 3-worker executor pool. After dedup, identical input
    short-circuits to the existing future.
    """
    client = gap_app.test_client()
    body = {
        "home": {"lat": 41.940, "lon": -87.680},
        "dest": {"lat": 41.935, "lon": -87.675},
        "tier": "death_wish",
    }
    first = client.post("/gap-analysis", json=body)
    assert first.status_code == 202
    job_id_1 = first.get_json()["job_id"]

    # Second POST while the first is still in flight (no wait between)
    # should return the same job_id.
    second = client.post("/gap-analysis", json=body)
    # If the future completed between the two POSTs, the second is a
    # cache hit (200, "ready") — also acceptable. The bug we're guarding
    # against is a NEW running job with a different job_id.
    if second.status_code == 202:
        job_id_2 = second.get_json()["job_id"]
        assert job_id_2 == job_id_1, (
            "duplicate POST returned a new job_id; dedup is broken"
        )
    else:
        # Cache hit path — fine; the test for that lives above.
        assert second.status_code == 200
        assert second.get_json()["status"] == "ready"

    # Either way, the original job should complete.
    final = _wait_until_ready(client, job_id_1)
    assert final["status"] == "ready"
