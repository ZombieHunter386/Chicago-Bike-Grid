# Plan 2C — Render Deploy + Operational Tooling

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for code-touching tasks. Some tasks require user-side actions (Render dashboard, DNS) that subagents cannot complete — those are explicitly marked.

**Goal:** Deploy the Plan 2A backend + Plan 2B frontend to Render Web Service Starter ($7/mo) with a 1 GB persistent disk for `bikemap.db` + `cache.db`. Build operational tooling for monthly DB refresh.

**Architecture:** Single-process gunicorn (`-w 1 --threads 4`) running the Flask app inside the Docker container created in Plan 2A. `bikemap.db` lives on a Render persistent disk mounted at `/var/data`; replaced monthly via `make upload-db`. CI on push to `master` deploys via Render's Git integration.

**Spec sections this plan implements:** §3.9 (refresh), §3.10 (web service ops), §3.11 (CI/CD + schema versioning), §5.6 (render.yaml).

**Prerequisites:**
- Plan 2A complete (✓ already done) and Plan 2A.5 launch hardening landed (✓ commit `3091b81`).
- Plan 2B at minimum has a working frontend (Tasks 1-2 are the critical floor; Tasks 3-10 can land later).
- Render account + credit card on file. Hunter handles account creation; subagents cannot.
- A repo on GitHub. The current worktree branch will need to merge to `master` and `master` pushed to `origin`.

**Out of scope (deferred to v2):**
- Cloudflare CDN in front of Render.
- Authenticated quotas / paid tiers.
- Automated monthly cron-driven refresh (spec §3.14 — manual trigger via developer reminder for v1).
- Self-hosted Nominatim (spec §7.3 — fall back to public Nominatim with self-throttling).

**File structure (created/modified by this plan):**

```
chicago-bike-advocacy-map/
├── render.yaml                 # NEW: Render service definition
├── Dockerfile                  # MODIFIED: production polish (already exists from Plan 2A bench)
├── Makefile                    # NEW: refresh, upload-db, dev, test, deploy targets
└── prep/upload_db.py           # NEW: Render API client to PUT bikemap.db to persistent disk
```

---

## Task 1: render.yaml service definition

**Files:**
- Create: `chicago-bike-advocacy-map/render.yaml`

**Spec ref:** §5.6.

**Design notes:**
- Web service named `chicago-bike-advocacy-map`.
- Deploys via Docker (image source: this repo's Dockerfile). NOT via `env: python` — we want our Dockerfile's reproducibility.
- Persistent disk mounted at `/var/data`, sized 1 GB.
- Health check: `/health` with `initialDelaySeconds: 120` (covers the 30-90s startup graph load per spec §3.10).
- Env vars wired: BIKEMAP_DB_PATH, CACHE_DB_PATH, NOMINATIM_USER_AGENT, MIN_STREETS, APP_BOOTSTRAP=1.
- Plan tier: `starter` ($7/mo).

- [ ] **Step 1: Write `render.yaml`**

```yaml
services:
  - type: web
    name: chicago-bike-advocacy-map
    runtime: docker
    repo: https://github.com/ZombieHunter386/Lakeview-Bike-Grid  # (or current repo URL)
    branch: master
    rootDir: chicago-bike-advocacy-map
    dockerfilePath: ./Dockerfile
    plan: starter
    region: oregon  # or whichever region Hunter prefers; can change
    healthCheckPath: /health
    initialDeployHook: ""  # no first-deploy hook; bikemap.db uploaded separately
    disk:
      name: bikemap-data
      mountPath: /var/data
      sizeGB: 1
    envVars:
      - key: BIKEMAP_DB_PATH
        value: /var/data/bikemap.db
      - key: CACHE_DB_PATH
        value: /var/data/cache.db
      - key: NOMINATIM_USER_AGENT
        value: chicago-bike-advocacy-map/1.0
      - key: MIN_STREETS
        value: "10000"
      - key: APP_BOOTSTRAP
        value: "1"
      - key: PORT
        value: "8000"
```

- [ ] **Step 2: Commit**

```bash
git add chicago-bike-advocacy-map/render.yaml
git commit -m "chore(deploy): render.yaml — web service + 1 GB persistent disk"
```

Note: this commit alone does NOT deploy. Render needs the repo connected via dashboard (Task 5).

---

## Task 2: Production-polish the Dockerfile

**Files:**
- Modify: `chicago-bike-advocacy-map/Dockerfile`

**Design notes:**
- The existing Dockerfile from Plan 2A's Linux bench (commit `22fcdff`) is functional but optimized for benching. Production polish:
  - Run as non-root user (security best practice).
  - Healthcheck instruction in the Dockerfile (Render uses its own check, but local + other-platform deploys benefit).
  - Multi-stage build: builder installs deps; runtime stage copies only `/install` + `app/` + `prep/` + needed assets.
  - EXPOSE the port explicitly.
  - Pin Python base to `python:3.11-slim` with a digest if possible for reproducibility.
  - Drop the `tests/` copy from the runtime stage (only the builder + bench needed it).
  - Verify gunicorn starts cleanly: CMD `gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT:-8000} app.main:app`.

- [ ] **Step 1: Read current Dockerfile + revise**

Apply the changes above. Verify locally:
```
docker build --platform=linux/amd64 -t chicago-bike-prod:latest .
docker run --rm --platform=linux/amd64 -p 8000:8000 \
  -v $(pwd)/data:/var/data:ro \
  chicago-bike-prod:latest
```
Then `curl http://localhost:8000/health` should return `{"status":"ok",...}` after ~35s.

- [ ] **Step 2: Verify the smoke test still passes inside the production image**

```
docker run --rm --platform=linux/amd64 \
  -v $(pwd)/data:/app/data:ro \
  -e BIKEMAP_DB_PATH=/app/data/bikemap.db \
  --memory=512m \
  chicago-bike-prod:latest \
  pytest -m slow tests/app/test_smoke_real_db.py -v -s
```

If the production image excludes `tests/`, this won't work — that's OK; just confirm the live `/health` endpoint and `/routes` POST work via curl.

- [ ] **Step 3: Commit**

---

## Task 3: `prep/upload_db.py` — push bikemap.db to Render's persistent disk

**Files:**
- Create: `chicago-bike-advocacy-map/prep/upload_db.py`
- Create: `chicago-bike-advocacy-map/tests/prep/test_upload_db.py`

**Design notes:**
- Render does NOT expose a direct write API for persistent disks. The standard pattern is **upload via a one-off SSH session** or **upload via the running web service**. We pick option B for v1: a tiny authenticated POST endpoint on the web service that accepts a multipart upload and atomically replaces `/var/data/bikemap.db`.
- BUT spec §3.8 forbids server-side writes from untrusted callers. So this endpoint MUST require an upload-token env var. Set `UPLOAD_TOKEN` in Render dashboard; the upload script reads from local env / a secret file and sends `Authorization: Bearer <token>`.
- Atomic replace: write to `/var/data/bikemap.db.new`, then `os.rename(...)` (atomic on the same filesystem). On startup, the app reads the new file (the schema_version check guards against incompatible DBs).
- The route lives at `/admin/upload-bikemap-db` (NOT under the regular blueprint set; gated by token).

**Plan 2A patch needed:** add the upload route to `app/main.py` or as a separate `app/routes/admin.py`. This is technically a Plan 2A.5 change but folded here for cohesion.

> **Plan 2D dependency:** `prep/upload_db.py` must upload **both** `bikemap.db` AND `lts-network.geojson.gz` in the same atomic refresh. The Explorer view at `/explore` 404s until the geojson exists; if `bikemap.db` is newer than the geojson, the Explorer shows data older than the routing engine (skew). Upload them as two-file POST or as a tarball, but they must move together: the `/admin/upload-bikemap-db` endpoint must `os.replace` them into `/var/data/` only after both have been received successfully.

- [ ] **Step 1: Add `/admin/upload-bikemap-db` route to the Flask app**

`app/routes/admin.py`:
```python
"""Admin-only endpoints. Token-gated via UPLOAD_TOKEN env var."""
import os, hmac, shutil, tempfile
from pathlib import Path
from flask import Blueprint, request, jsonify

def build_admin_blueprint(bikemap_db_path: Path):
    bp = Blueprint("admin", __name__)
    
    @bp.post("/admin/upload-bikemap-db")
    def upload_bikemap():
        token = os.environ.get("UPLOAD_TOKEN")
        if not token:
            return jsonify({"error": "upload disabled (no UPLOAD_TOKEN)"}), 503
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], token):
            return jsonify({"error": "unauthorized"}), 401
        if "file" not in request.files:
            return jsonify({"error": "missing 'file' multipart field"}), 400
        f = request.files["file"]
        # Stream to .new, then atomic rename.
        tmp = bikemap_db_path.with_suffix(".db.new")
        f.save(str(tmp))
        # TODO: validate schema_version on tmp before swapping.
        os.replace(tmp, bikemap_db_path)
        return jsonify({"status": "ok", "size_bytes": bikemap_db_path.stat().st_size})
    
    return bp
```

Wire in `app/main.py` (only if `UPLOAD_TOKEN` env var is set, to avoid surprising surface area on untoken'd deploys).

- [ ] **Step 2: Implement `prep/upload_db.py`**

```python
"""Push bikemap.db to a deployed instance via /admin/upload-bikemap-db."""
import os, sys, time
from pathlib import Path
import requests

def upload(db_path: Path, base_url: str, token: str) -> None:
    print(f"Uploading {db_path} ({db_path.stat().st_size / 1e6:.1f} MB) to {base_url}...")
    started = time.time()
    with open(db_path, "rb") as f:
        resp = requests.post(
            f"{base_url}/admin/upload-bikemap-db",
            files={"file": (db_path.name, f, "application/octet-stream")},
            headers={"Authorization": f"Bearer {token}"},
            timeout=300,
        )
    resp.raise_for_status()
    elapsed = time.time() - started
    print(f"  Done in {elapsed:.1f}s. Server reports: {resp.json()}")

if __name__ == "__main__":
    db_path = Path(os.environ.get("BIKEMAP_DB_LOCAL", "data/bikemap.db"))
    base_url = os.environ["RENDER_BASE_URL"]  # e.g., https://chicago-bike-advocacy-map.onrender.com
    token = os.environ["UPLOAD_TOKEN"]
    upload(db_path, base_url, token)
```

- [ ] **Step 3: Add tests for the upload endpoint**

Use Flask's test client. Verify: 401 without token, 401 with wrong token, 200 with correct token, 400 without file, atomic rename (write fails halfway → original file intact).

- [ ] **Step 4: Commit**

---

## Task 4: Makefile — refresh, upload-db, dev, test targets

**Files:**
- Create: `chicago-bike-advocacy-map/Makefile`

**Design notes:**
- Targets per spec §5.7:
  - `dev`: starts Flask in debug mode against local bikemap.db
  - `refresh`: runs prep pipeline (`python -m prep.main`)
  - `upload-db`: uploads ./data/bikemap.db to Render
  - `test`: ruff + mypy + pytest (fast suite)
  - `test-slow`: pytest -m slow tests
  - `report`: opens prep_report.md
  - `docker-build`: build the production image
  - `docker-bench`: run the smoke test inside docker

- [ ] **Step 1: Write the Makefile**

```makefile
.PHONY: dev refresh upload-db test test-slow report docker-build docker-bench

PYTHON := .venv/bin/python
VENV   := .venv/bin

dev:
	APP_BOOTSTRAP=1 BIKEMAP_DB_PATH=data/bikemap.db CACHE_DB_PATH=/tmp/cache.db \
	NOMINATIM_USER_AGENT=dev/1.0 \
	$(VENV)/gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 app.main:app

refresh:
	$(PYTHON) -m prep.main

upload-db:
	@: $${RENDER_BASE_URL?Set RENDER_BASE_URL (e.g. https://chicago-bike-advocacy-map.onrender.com)}
	@: $${UPLOAD_TOKEN?Set UPLOAD_TOKEN (must match Render env var)}
	$(PYTHON) -m prep.upload_db

test:
	$(VENV)/ruff check app/ prep/ tests/
	$(VENV)/mypy app/ prep/
	$(VENV)/pytest

test-slow:
	$(VENV)/pytest -m slow

report:
	$(VENV)/python -c "from pathlib import Path; print(Path('prep_report.md').read_text())" || echo "no prep_report.md (run 'make refresh' first)"

docker-build:
	docker build --platform=linux/amd64 -t chicago-bike-prod:latest .

docker-bench:
	docker run --rm --platform=linux/amd64 \
	  -v $$(pwd)/data:/app/data:ro \
	  -e BIKEMAP_DB_PATH=/app/data/bikemap.db \
	  --memory=512m \
	  chicago-bike-prod:latest \
	  pytest -m slow tests/app/test_smoke_real_db.py -v -s
```

- [ ] **Step 2: Verify each target manually**

`make test` (should run ruff + mypy + pytest, all green).
`make dev` (starts gunicorn; ctrl-C to exit).
`make docker-build` (image builds).
`make refresh` is a long-running command (~5 min); skip in this verification — but `make refresh --dry-run` should show the right shell command if available.

- [ ] **Step 3: Commit**

---

## Task 5: USER ACTION — Connect repo to Render + first deploy

**This task requires Hunter to do work in the Render dashboard. Subagents cannot complete this.**

Steps Hunter performs:
1. Sign in to Render → New → Web Service → Connect this repo from GitHub.
2. Select the `master` branch + the `chicago-bike-advocacy-map` rootDir.
3. Verify Render auto-detects the Dockerfile + render.yaml.
4. In the dashboard, set `UPLOAD_TOKEN` to a strong random secret (e.g., `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
5. Click "Create Web Service".
6. Wait ~5 minutes for the first build + deploy. The first deploy will fail health-check because `bikemap.db` isn't uploaded yet — that's expected.
7. Once deployed, save the URL (e.g., `https://chicago-bike-advocacy-map.onrender.com`).

Then locally:
```bash
export RENDER_BASE_URL=https://chicago-bike-advocacy-map.onrender.com
export UPLOAD_TOKEN=<the secret you just generated>
make upload-db
```

Render auto-restarts the service on the next deploy push. After upload-db, the next start should pass health check (graph load against the freshly-uploaded bikemap.db).

- [ ] **Step 1: Hunter reports back the production URL + first-deploy success**

When Hunter confirms `/health` returns 200 in production, this task is done.

---

## Task 6: Verify spec §6.4 launch criteria in production

**This is a manual QA pass. Done collaboratively with Hunter.**

Walk through each criterion in spec §6.4:
1. **LTS sanity** — done in Plan 1.
2. **HIN join coverage ≥95%** — currently 75.8% per Plan 1 status. Document deviation; not a code change.
3. **End-to-end deploy** — covered by Task 5.
4. **Routing reasonableness** — 10 hand-tested addresses across diverse Chicago neighborhoods. POST /routes for each + visually inspect the response polylines. Document in `docs/launch-readiness.md`.
5. **Gap analysis quality** — same 10 addresses, POST /gap-analysis. Verify headline candidates name actual known-bad infrastructure (e.g., expect Western Ave or Kedzie Ave LTS-3 segments to surface).
6. **Permalink round-trip** — covered by Plan 2B Task 10 QA.
7. **Privacy verified** — inspect Render's request logs after a test session. Confirm zero coordinates / addresses appear. Plan 2A.5's ProxyFix should keep `X-Forwarded-For` honored without the actual coords being logged.
8. **Performance floor**:
   - Initial HTML/JS <3s — verify with browser DevTools.
   - First-visit time-to-first-route <10s — manual time.
   - Full overview <90s with 3 concurrent gap analyses — synthetic load test.
   - Cached gap query <200ms — verify with cURL after warming.
9. **Memory budget verified** — already confirmed via Plan 2A.5's sustained-load smoke test.
10. **No build-time errors or warnings** — `make test` passes clean.

For each criterion, mark PASS / FAIL / DEFERRED. Document in `docs/launch-readiness.md`.

- [ ] **Step 1: Run through each criterion + document**

- [ ] **Step 2: Commit `docs/launch-readiness.md`**

---

## Done

Plan 2C complete. The web service is live at the Render URL, `make upload-db` is the monthly refresh tool, and §6.4 launch criteria are documented.

**Path to v1 launch from here (per spec §6.5):**
1. Soft launch — share with 3-5 trusted Chicago bike advocates.
2. Targeted announce — Active Trans, Streetsblog Chicago.
3. General public — Twitter/Bluesky, neighborhood newsletters.

No PR push or paid promotion in v1.

**v2 backlog** (from spec §6.2):
- Mobile-specific layouts
- Swap-destination picker
- Multi-stop trip routing
- PDF / OG image / social-share artifacts
- Embeddable widgets
- Time-of-day variation, weather, elevation
- Multi-modal (bike + transit) routing
- Postgres + PostGIS migration
- Automated cron-driven refresh
