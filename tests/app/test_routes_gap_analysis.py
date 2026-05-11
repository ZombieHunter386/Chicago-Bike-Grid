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
        "tier": "any",
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
        "tier": "any",
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
