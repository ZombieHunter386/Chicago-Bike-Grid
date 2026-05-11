# Plan 2B — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the MapLibre-based two-view (overview + drill-down) frontend that consumes Plan 2A's backend endpoints to produce a printable, shareable Chicago bike-advocacy artifact.

**Architecture:** Plain HTML + ES modules + CSS, served by Flask's `static_folder`. **No build step in v1** (spec §5.4). MapLibre GL JS via CDN; lz-string via CDN. State lives in a single in-memory object plus the URL hash fragment (compressed JSON). All API calls are POST with JSON bodies (privacy: spec §3.8).

**Tech Stack:** MapLibre GL JS (latest), lz-string (1.x), Flask static-asset serving, fetch API, vanilla ES modules. **No** React/Vue/Svelte/Vite/build tools.

**Spec sections this plan implements:** §2 (interaction model — overview + drill-down), §2.3 (permalink), §2.4 (tier selection + POI input), §3.5 (gap-analysis async polling — UI side), §3.6 (POI selection caveat text), §3.7 (map services), §4.3 (HIN annotation copy), §4.4 (best-effort fallback visual treatment), §4.6 (multi-route aggregation for avoided-intersection markers).

**Out of scope (deferred — explicit non-goals):**
- Mobile-specific layouts (spec §3.14). Desktop only; phone use is best-effort viewport scaling.
- Swap-destination picker (spec §3.6). Auto-picked nearest-by-category is what users get.
- PDF / OG image / social-share artifact generation (spec §3.14). Permalinks only.
- Embeddable widgets, citywide aggregate view (deferred to v2 per §6.2).
- Build tooling. Plain ESM, served as-is.

**File structure (created by this plan, per spec §5.1):**

```
chicago-bike-advocacy-map/app/static/
├── index.html
├── styles.css
├── app.js               # state, fetches, permalink codec, top-level wiring
├── overview.js          # overview view: home + destinations + routes + markers
├── drilldown.js         # drill-down view: zoomed pair + fact panel
└── icons/               # category icon SVGs (school, park, grocery, etc.)
```

A few additions to `app/main.py` to wire static serving:
- `Flask(__name__, static_folder="static", static_url_path="/static")`
- `@app.get("/")` returning `send_from_directory(app.static_folder, "index.html")`

**Testing strategy:**
- Pure-JS units (permalink codec, state mutation) get JS unit tests via a tiny `tests/static/` setup using **node** (no browser). Lightweight and avoids Selenium.
- UI integration: per Hunter's CLAUDE.md, **start the Flask dev server and exercise the feature in a browser** for every UI task before reporting it complete. Use the Claude Preview tools (preview_start, preview_click, preview_snapshot, preview_screenshot, preview_console_logs).
- The full `tests/app/` Python suite must continue to pass after Flask static-serving wiring.

**Visual style guidance (spec §2.1, §2.2, §4.4):**
- Map fills viewport; UI overlays are translucent panels with rounded corners + subtle drop shadow.
- Tier selector: top-center, three pill buttons with the active tier filled in green/amber/red.
- Basemap toggle: top-left, simple Streets/Satellite icon button.
- Legend: top-right, ~200px wide, route style key + marker size key.
- Fast route: dashed orange (`stroke-dasharray: 2,4` — exact CSS in Task 7).
- Safe route: solid green.
- Best-effort fallback route: dashed amber + "Best effort — no fully safe path" badge.
- High-stress segments along fast route: red outline beneath the orange dashes.
- Avoided-intersection markers: top-1 big red circle with alert badge, top 2-3 smaller red with count badge, rest amber.
- Home pin: yellow.
- Destination pins: category icon + colored ring.
- Fact panel: white background, 340px wide, slides in from right with `transform: translateX` + 200ms ease.

**Color tokens (in styles.css :root):**
```css
--c-fast: #f97316;     /* orange-500 */
--c-safe: #16a34a;     /* green-600 */
--c-fallback: #f59e0b; /* amber-500 */
--c-stress: #dc2626;   /* red-600 */
--c-home: #facc15;     /* yellow-400 */
--c-marker-high: #dc2626;
--c-marker-mid:  #ef4444;
--c-marker-low:  #f59e0b;
--c-bg: #ffffff;
--c-text: #0f172a;
--c-text-muted: #64748b;
--c-border: #e2e8f0;
```

---

## Task 1: Static serving + index.html shell + landing page

**Files:**
- Modify: `chicago-bike-advocacy-map/app/main.py` (configure static folder + add `/` route)
- Create: `chicago-bike-advocacy-map/app/static/index.html`
- Create: `chicago-bike-advocacy-map/app/static/styles.css` (just the body reset + token vars; populated by later tasks)
- Create: `chicago-bike-advocacy-map/app/static/app.js` (entry-point stub)

**Spec ref:** §5.1 (frontend file tree).

**Design notes:**
- Flask serves `app/static/` automatically via the `static_url_path="/static"` config. The `/` route returns `index.html`.
- `index.html` loads MapLibre + lz-string from CDN, has a single `<div id="app"></div>` mount point, and imports `app.js` as `<script type="module">`.
- `styles.css` defines the design tokens (color palette above) + a body reset that fills the viewport. No view-specific styling yet.

- [ ] **Step 1: Update `app/main.py`**

Find the `app = Flask(__name__)` line in `create_app` and change it to:
```python
app = Flask(__name__, static_folder="static", static_url_path="/static")
```

Add a `/` route that returns the SPA shell. Place AFTER `/health`:
```python
from flask import send_from_directory
...
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")
```

- [ ] **Step 2: Create `app/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=1024">
  <title>Chicago Bike Advocacy Map</title>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
  <link rel="stylesheet" href="/static/styles.css">
  <script src="https://unpkg.com/lz-string@1.5.0/libs/lz-string.min.js"></script>
</head>
<body>
  <div id="map"></div>
  <div id="ui-overlays"></div>
  <script type="module" src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create `app/static/styles.css` (token vars + reset)**

```css
:root {
  --c-fast: #f97316;
  --c-safe: #16a34a;
  --c-fallback: #f59e0b;
  --c-stress: #dc2626;
  --c-home: #facc15;
  --c-marker-high: #dc2626;
  --c-marker-mid:  #ef4444;
  --c-marker-low:  #f59e0b;
  --c-bg: #ffffff;
  --c-text: #0f172a;
  --c-text-muted: #64748b;
  --c-border: #e2e8f0;
  --shadow-panel: 0 4px 16px rgba(15, 23, 42, 0.12);
  --radius-panel: 8px;
  --font-stack: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; font-family: var(--font-stack); color: var(--c-text); }
#map { position: absolute; inset: 0; }
#ui-overlays { position: absolute; inset: 0; pointer-events: none; }
#ui-overlays > * { pointer-events: auto; }
```

- [ ] **Step 4: Create `app/static/app.js` (stub)**

```javascript
// Top-level frontend entry. Wires state, view rendering, and event handlers.
// Plan 2B Task 1: just bootstrap; subsequent tasks populate the views.

console.log("Chicago bike map frontend loaded");
```

- [ ] **Step 5: Run dev server + verify in browser**

Run from another terminal: `cd chicago-bike-advocacy-map && APP_BOOTSTRAP=1 BIKEMAP_DB_PATH=data/bikemap.db CACHE_DB_PATH=/tmp/cache.db NOMINATIM_USER_AGENT=dev/1.0 .venv/bin/gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 app.main:app` (or `flask --app app.main run --no-reload --port 8000` after setting env).

Use preview_start to point at `http://localhost:8000`. Then preview_snapshot and verify:
- Page loads without console errors
- "Chicago bike map frontend loaded" appears in console
- (Empty page is expected; map will populate in Task 2)

- [ ] **Step 6: Quick Python test that `/` returns index.html**

Add to `tests/app/test_main.py`:
```python
def test_root_serves_spa_shell(tiny_bikemap_db_with_pois: Path, tmp_path: Path) -> None:
    from app.main import create_app
    app = create_app(
        bikemap_db=tiny_bikemap_db_with_pois,
        cache_db=tmp_path / "cache.db",
        nominatim_user_agent="test/1.0",
        min_streets=1,
    )
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<title>Chicago Bike Advocacy Map</title>" in resp.data
```

Run: `cd chicago-bike-advocacy-map && .venv/bin/pytest tests/app/test_main.py -v`. Expect 6 passed (was 5).

- [ ] **Step 7: Commit**

```bash
git add chicago-bike-advocacy-map/app/main.py chicago-bike-advocacy-map/app/static/ chicago-bike-advocacy-map/tests/app/test_main.py
git commit -m "feat(frontend): static serving + SPA shell"
```

---

## Task 2: Map initialization + basemap toggle

**Files:**
- Create: `chicago-bike-advocacy-map/app/static/overview.js`
- Modify: `app/static/app.js`, `app/static/styles.css`, `app/static/index.html`

**Spec ref:** §2.1 (basemap toggle), §3.7 (OpenFreeMap streets + Esri/MapTiler satellite).

**Design notes:**
- Initialize MapLibre centered on Chicago (~41.88°, -87.63°), zoom 11.
- Two basemap sources: OpenFreeMap "liberty" style for streets, Esri World Imagery for satellite.
- Toggle button top-left; `<button>` with `type="button"`, two states.
- Streets-only is the default.
- The map's `style` is swapped via `map.setStyle(url)` when toggled.

**Style URLs:**
- Streets: `https://tiles.openfreemap.org/styles/liberty`
- Satellite: build a custom MapLibre style JSON inline that uses Esri's tile server as a `raster` source. Esri's ArcGIS World Imagery: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`.

- [ ] **Step 1: Implement `overview.js` with `initMap()` + `setBasemap(kind)`**

The exports:
```javascript
export function initMap(container) { /* returns maplibregl.Map */ }
export function setBasemap(map, kind) { /* "streets" | "satellite" */ }
```

Streets style: load OpenFreeMap's liberty style URL.
Satellite style: inline JSON object with ESRI raster tiles.

- [ ] **Step 2: Add basemap-toggle button HTML + CSS**

Add to `index.html` `<div id="ui-overlays">`:
```html
<button id="basemap-toggle" type="button">Satellite</button>
```

Add to `styles.css`:
```css
#basemap-toggle {
  position: absolute; top: 12px; left: 12px;
  background: var(--c-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-panel);
  padding: 8px 12px; font: 14px var(--font-stack); cursor: pointer;
}
#basemap-toggle:hover { background: #f8fafc; }
```

- [ ] **Step 3: Wire app.js to call initMap + handle toggle**

```javascript
import { initMap, setBasemap } from "/static/overview.js";

const map = initMap(document.getElementById("map"));
let basemap = "streets";
const toggleBtn = document.getElementById("basemap-toggle");
toggleBtn.addEventListener("click", () => {
  basemap = basemap === "streets" ? "satellite" : "streets";
  setBasemap(map, basemap);
  toggleBtn.textContent = basemap === "streets" ? "Satellite" : "Streets";
});
```

- [ ] **Step 4: Verify in browser**

preview_start, then snapshot. Expected: Chicago map loads in streets mode. Click satellite toggle (preview_click) — map switches to satellite. Click again — back to streets. Console clean.

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/static/
git commit -m "feat(frontend): MapLibre init + streets/satellite basemap toggle"
```

---

## Task 3: State module + URL hash codec

**Files:**
- Create: `chicago-bike-advocacy-map/app/static/state.js`
- Create: `chicago-bike-advocacy-map/tests/static/test_state.mjs` (Node-based)
- Modify: `app/static/app.js`

**Spec ref:** §2.3 (permalink: lz-string-compressed JSON in URL hash; round-trip property; never reaches server).

**Design notes:**
- State shape:
  ```javascript
  {
    home: { lat, lon, displayName, approximate: false } | null,
    destinations: [
      { id, lat, lon, name, address, category | "custom", icon, approximate? }
    ],
    tier: "kid" | "parent" | "any",  // default "any"
    drilledPair: { destId, kind: "fast" | "safe" } | null,
  }
  ```
- URL hash codec: JSON.stringify → LZString.compressToEncodedURIComponent → location.hash; reverse for parse.
- Round-trip property: tested via Node script using lz-string from npm (fetched once with `npm install --no-save lz-string`).

- [ ] **Step 1: Implement `state.js`**

```javascript
const DEFAULT_STATE = { home: null, destinations: [], tier: "any", drilledPair: null };

let state = structuredClone(DEFAULT_STATE);
const subscribers = [];

export function getState() { return state; }
export function subscribe(fn) { subscribers.push(fn); return () => subscribers.splice(subscribers.indexOf(fn), 1); }
export function setState(patch) {
  state = { ...state, ...patch };
  for (const fn of subscribers) fn(state);
  syncToHash();
}
export function setDestinations(dests) { setState({ destinations: dests }); }
export function setTier(tier) { setState({ tier }); }
export function setHome(home) { setState({ home }); }
export function setDrilledPair(p) { setState({ drilledPair: p }); }

export function encodeStateToHash(s) {
  const compact = {
    h: s.home ? [s.home.lat, s.home.lon, s.home.displayName, s.home.approximate ? 1 : 0] : null,
    d: s.destinations.map(d => [d.id, d.lat, d.lon, d.name, d.address, d.category, d.icon, d.approximate ? 1 : 0]),
    t: s.tier,
    p: s.drilledPair ? [s.drilledPair.destId, s.drilledPair.kind] : null,
  };
  return LZString.compressToEncodedURIComponent(JSON.stringify(compact));
}

export function decodeHashToState(hash) {
  if (!hash) return structuredClone(DEFAULT_STATE);
  const json = LZString.decompressFromEncodedURIComponent(hash);
  if (!json) return structuredClone(DEFAULT_STATE);
  const compact = JSON.parse(json);
  return {
    home: compact.h ? { lat: compact.h[0], lon: compact.h[1], displayName: compact.h[2], approximate: !!compact.h[3] } : null,
    destinations: (compact.d || []).map(d => ({ id: d[0], lat: d[1], lon: d[2], name: d[3], address: d[4], category: d[5], icon: d[6], approximate: !!d[7] })),
    tier: compact.t || "any",
    drilledPair: compact.p ? { destId: compact.p[0], kind: compact.p[1] } : null,
  };
}

function syncToHash() {
  const encoded = encodeStateToHash(state);
  // Avoid spurious history entries; replaceState
  history.replaceState(null, "", encoded ? `#${encoded}` : "#");
}

export function loadFromHash() {
  const hash = window.location.hash.replace(/^#/, "");
  if (hash) {
    state = { ...DEFAULT_STATE, ...decodeHashToState(hash) };
    for (const fn of subscribers) fn(state);
  }
}
```

- [ ] **Step 2: Add `app.js` state initialization**

```javascript
import * as state from "/static/state.js";
state.loadFromHash();
window.addEventListener("hashchange", state.loadFromHash);
```

- [ ] **Step 3: Write Node-based unit test for the codec**

Create `chicago-bike-advocacy-map/tests/static/` directory.

Add `tests/static/test_state.mjs`:
```javascript
import LZString from "lz-string";
globalThis.LZString = LZString;

// Need to fake `window` and `history` for state.js's syncToHash. Stub them.
globalThis.window = { location: { hash: "" }, addEventListener: () => {} };
globalThis.history = { replaceState: () => {} };

const { encodeStateToHash, decodeHashToState } = await import("../../app/static/state.js");

function assertEq(a, b, msg) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    console.error(`FAIL: ${msg}\n  expected: ${JSON.stringify(b)}\n  actual:   ${JSON.stringify(a)}`);
    process.exit(1);
  }
}

const original = {
  home: { lat: 41.94, lon: -87.68, displayName: "1234 W Foster Ave", approximate: false },
  destinations: [
    { id: "d1", lat: 41.94, lon: -87.67, name: "Audubon", address: "...", category: "school", icon: "school", approximate: false },
    { id: "d2", lat: 41.95, lon: -87.68, name: "Lincoln Park", address: null, category: "park", icon: "park", approximate: false },
  ],
  tier: "parent",
  drilledPair: { destId: "d1", kind: "safe" },
};

const encoded = encodeStateToHash(original);
const decoded = decodeHashToState(encoded);
assertEq(decoded, original, "round-trip");

// Empty hash returns defaults.
const empty = decodeHashToState("");
assertEq(empty, { home: null, destinations: [], tier: "any", drilledPair: null }, "empty hash defaults");

// Compressed payload size for 5+ destinations is reasonable (~250 chars).
const big = { ...original, destinations: Array.from({ length: 5 }, (_, i) => ({ id: `d${i}`, lat: 41.94, lon: -87.68 + i * 0.01, name: `dest${i}`, address: null, category: "park", icon: "park", approximate: false })) };
const bigEncoded = encodeStateToHash(big);
if (bigEncoded.length > 400) {
  console.error(`FAIL: 5-destination encoded length ${bigEncoded.length} > 400 chars (spec §2.3 wants < 250)`);
  process.exit(1);
}

console.log("ALL state.js tests passed");
```

- [ ] **Step 4: Run the JS test**

Install lz-string locally (one-shot): `cd chicago-bike-advocacy-map && npm install --no-save lz-string`. Run: `node tests/static/test_state.mjs`. Expect "ALL state.js tests passed".

(Note: `npm install` requires Node + npm. If unavailable, mark this task DONE_WITH_CONCERNS and document that the codec was visually inspected against the spec.)

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/static/state.js chicago-bike-advocacy-map/app/static/app.js chicago-bike-advocacy-map/tests/static/
git commit -m "feat(frontend): state module + lz-string permalink codec"
```

---

## Task 4: Tier selector UI

**Files:**
- Modify: `app/static/index.html`, `app/static/styles.css`, `app/static/app.js`

**Spec ref:** §0.1 (tier names + LTS allowance), §2.1 (tier selector top-center).

- [ ] **Step 1: Add tier selector HTML**

Add to `#ui-overlays`:
```html
<div id="tier-selector">
  <button data-tier="kid" type="button">Safe for kid <span class="lts-allowance">(LTS 1)</span></button>
  <button data-tier="parent" type="button">Safe for parent <span class="lts-allowance">(LTS 1-2)</span></button>
  <button data-tier="any" type="button" class="active">Not safe <span class="lts-allowance">(LTS 1-3)</span></button>
</div>
```

- [ ] **Step 2: Style with the active state**

```css
#tier-selector {
  position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
  display: flex; gap: 4px;
  background: var(--c-bg); border-radius: var(--radius-panel); box-shadow: var(--shadow-panel);
  padding: 4px;
}
#tier-selector button {
  background: transparent; border: none; padding: 8px 14px; cursor: pointer;
  font: 14px var(--font-stack); border-radius: 6px; color: var(--c-text);
}
#tier-selector button:hover { background: #f1f5f9; }
#tier-selector button.active { background: var(--c-safe); color: white; }
#tier-selector button[data-tier="parent"].active { background: #16a34a; }
#tier-selector button[data-tier="kid"].active { background: #15803d; }
#tier-selector button[data-tier="any"].active { background: #6b7280; }
.lts-allowance { font-size: 12px; opacity: 0.75; margin-left: 4px; }
```

- [ ] **Step 3: Wire click handler**

```javascript
const tierSelector = document.getElementById("tier-selector");
tierSelector.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tier]");
  if (!btn) return;
  state.setTier(btn.dataset.tier);
});

state.subscribe((s) => {
  for (const btn of tierSelector.querySelectorAll("button[data-tier]")) {
    btn.classList.toggle("active", btn.dataset.tier === s.tier);
  }
});
```

- [ ] **Step 4: Verify in browser**

preview_start. Snapshot — tier selector is centered top, "Not safe" highlighted by default. Click "Safe for parent" — that pill becomes active and URL hash updates (visible in URL bar). Click "Safe for kid" — same.

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/static/
git commit -m "feat(frontend): tier selector with active state + URL sync"
```

---

## Task 5: Address geocoding flow

**Files:**
- Modify: `app/static/index.html`, `app/static/styles.css`, `app/static/app.js`
- Create: `app/static/api.js` (thin fetch wrappers)

**Spec ref:** §2.4 step 1 (enter home address; autocomplete via Nominatim), §3.7 (Nominatim proxy via /geocode).

**Design notes:**
- Single search input visible top-center BELOW the tier selector. Initially has placeholder "Enter your home address (Chicago)".
- Submit (Enter) calls POST /geocode → if 200: set home in state, show yellow pin. If 404: show inline error.
- `api.js` module wraps `fetch`:
  ```javascript
  export async function geocode(address) { /* POST /geocode */ }
  export async function fetchRoutes(home, dest, tier) { /* POST /routes */ }
  export async function fetchPois(near, category) { /* POST /pois */ }
  export async function fetchTreatment(slug) { /* GET /treatments/:slug */ }
  export async function fetchGapAnalysis(home, dest, tier) { /* POST /gap-analysis + poll if 202 */ }
  ```

- [ ] **Step 1: Implement `api.js`**

Boilerplate POST helper + per-endpoint wrappers. Throws on non-2xx with `{status, body}`. Polls /gap-analysis/status every 1500ms when 202 received (cap 60s). Returns the same shape regardless of cache hit/miss.

- [ ] **Step 2: Add address-input HTML + CSS**

```html
<form id="home-form">
  <input id="home-input" type="text" placeholder="Enter your home address (Chicago)" autocomplete="off">
  <button type="submit">Set home</button>
</form>
```

CSS positions it ~60px below tier selector, 360px wide.

- [ ] **Step 3: Wire submit handler**

On submit: `e.preventDefault()`, call `api.geocode(input.value)`, on success `state.setHome({lat, lon, displayName, approximate: false})`, on failure show error inline.

- [ ] **Step 4: Render home pin on map**

In `overview.js`, add `renderHome(map, home)` that adds/replaces a yellow MapLibre marker at `[home.lon, home.lat]`. Subscribe to state in `app.js`: when `state.home` changes, call `renderHome`. Also recenter the map to the home point on first set.

- [ ] **Step 5: Verify in browser**

preview_start, preview_fill the home-input with `1234 W Foster Ave Chicago`, submit. Yellow pin appears at the geocoded location, map centers there. URL hash updates with the home coords. preview_console_logs should be clean.

(If Nominatim has rate-limit issues during dev, override the user-agent env var.)

- [ ] **Step 6: Commit**

```bash
git add chicago-bike-advocacy-map/app/static/
git commit -m "feat(frontend): address geocoding + home pin rendering"
```

---

## Task 6: Destination category checklist + custom-address input

**Files:**
- Modify: `app/static/index.html`, `app/static/styles.css`, `app/static/app.js`, `app/static/overview.js`

**Spec ref:** §2.4 step 3-4 (category checklist + auto-pick nearest + free-form custom), §3.6 (selection rules + caveat text).

**Design notes:**
- Sidebar panel slides in from left after home is set. Contains 7 category checkboxes:
  - School, Park, Grocery, Hospital, Alderman office, Library, CTA L stop
- Each checkbox has a category icon. Toggling on → fetch nearest POI in that category via /pois → add to state.destinations. Toggling off → remove from state.destinations.
- Custom address input below: "Add a custom destination". Type+Enter → /geocode → add as `category: "custom"`.
- Caveat text below the checklist: *"We picked the nearest by straight-line distance. If that's not the place you actually go, add it as a custom destination below."* (verbatim from spec §3.6).
- Each destination renders as a pin on the map. Pin: category icon SVG + colored ring matching the icon's tint.

**Categories (from spec §3.6 + Plan 1's POI tables):**
| UI label | category key | icon |
|---|---|---|
| School | school | 🏫 (svg) |
| Park | park | 🌳 |
| Grocery | grocery | 🛒 |
| Hospital | hospital | 🏥 |
| Alderman office | alderman | 🏛️ |
| Library | library | 📚 |
| CTA L stop | transit | 🚆 |

For v1, use Unicode emoji or simple inline SVGs. Don't bother with a per-icon SVG file unless the implementer wants to.

- [ ] **Step 1: Build the destinations sidebar HTML/CSS**

Hidden until home is set. Each checkbox row: icon + label + checkbox. Custom-address input at bottom.

- [ ] **Step 2: Wire checkbox handlers**

On check: `api.fetchPois(state.home, category)` → on success, append to `state.destinations` (via state.setDestinations).
On uncheck: filter out by category.
Custom address: similar but via /geocode → push `{id, lat, lon, name: input.value, address, category: "custom", icon: "custom"}`.

- [ ] **Step 3: Render destination pins**

In `overview.js`, add `renderDestinations(map, dests)` that diffs against existing markers. Each pin: small div with the icon emoji + colored ring CSS.

- [ ] **Step 4: Caveat text below the list**

Add the verbatim spec text. Style as `.caveat { color: var(--c-text-muted); font-size: 12px; margin-top: 8px; }`.

- [ ] **Step 5: Verify in browser**

preview: set home, then toggle each category — pins appear. Toggle off — pin disappears. Add a custom address — pin appears with custom icon. URL hash reflects all destinations.

- [ ] **Step 6: Commit**

---

## Task 7: Route rendering — fast + safe + fallback per pair

**Files:**
- Modify: `app/static/overview.js`, `app/static/app.js`

**Spec ref:** §2.1 (route styles), §4.1 (cost), §4.4 (best-effort fallback).

**Design notes:**
- For each `(home, dest)` pair: call `api.fetchRoutes(home, dest, state.tier)` → returns `{fast: {polyline, length_m, lts_distribution, is_fallback}, safe: same shape}`.
- Render fast route as dashed orange line (sourced as a MapLibre line layer with `line-dasharray: [2, 4]`). Render high-stress segments (LTS-3 segments along the fast route) as a thicker red line BENEATH the orange dashes. (For v1, treat all fast-route segments above the user's tier as high-stress.)
- Render safe route as solid green line.
- If `safe.is_fallback === true`: render as dashed amber line (`line-dasharray: [4, 4]`) instead of green; attach a `Best effort — no fully safe path` badge to the destination pin.
- All route layers per pair share an ID prefix `route-{destId}-fast`, `route-{destId}-safe`. Removing them on re-render is straightforward.

- [ ] **Step 1: Implement `renderRoutes(map, home, dests, tier)`**

For each dest in dests, fetch routes, then add layers. Run requests in parallel via `Promise.all`. Re-render replaces existing layers.

- [ ] **Step 2: Subscribe to state changes**

When `home`, `destinations`, or `tier` change, trigger `renderRoutes`. Debounce 200ms to avoid duplicate fetches when multiple state changes happen back-to-back.

- [ ] **Step 3: Verify in browser**

Set home + 3 destinations. Verify fast (orange dashed) + safe (green solid) lines render correctly between home and each pin. Toggle tier — lines re-fetch and update. Toggle a destination — that pair's lines disappear.

- [ ] **Step 4: Verify fallback rendering**

If at the synthetic test data scale we can't reproduce a fallback, just verify the UI codepath via DevTools console: simulate `fetchRoutes` returning `is_fallback: true` and confirm the line renders amber + badge appears.

- [ ] **Step 5: Commit**

---

## Task 8: Avoided-intersection markers (overview view multi-route aggregation)

**Files:**
- Modify: `app/static/overview.js`, `app/static/app.js`
- Create: helper `aggregateGaps(perPairResults)` — implements spec §4.6 multi-route aggregation client-side.

**Spec ref:** §2.1 (markers), §3.5 (gap-analysis loading widget), §4.5 (gap shape from API), §4.6 (multi-route aggregation).

**Design notes:**
- For each home→dest pair: `api.fetchGapAnalysis(home, dest, tier)` (cache-aware; first request may take 5-15s). Show a loading widget with progressive labels while pending.
- Once all pairs return, run §4.6 aggregation:
  - Collect every gap candidate's `feature_id` (and feature_kind) across all pair results.
  - Aggregate by `(feature_kind, feature_id)`:
    - `routes_affected = #destinations whose gap involves this feature`
    - `total_savings_meters = sum of savings across affected routes`
  - Compute `priority = routes_affected × log(1 + total_savings_meters)`.
  - Top 1 → big red marker with alert badge. Top 2-3 → smaller red with count badge. Rest → amber. Single-route low-savings avoidances are NOT shown on overview (drill-down only).
- Loading widget: a small panel bottom-center with sub-step labels: *"Building safe route... computing gap candidates... ranking..."*

- [ ] **Step 1: Implement `aggregateGaps(perPairResults)`**

Pure function, returns `[{ kind, id, geometry_wkt, priority, routes_affected, total_savings_meters, marker_size }, ...]` sorted by priority descending.

- [ ] **Step 2: Render markers**

For each aggregate marker: parse `geometry_wkt` (WKT → coords) and add a MapLibre `<div>` marker styled per spec.

- [ ] **Step 3: Loading widget**

Bottom-center fixed panel; show "Computing gap analysis... (X / N destinations)" while pending; remove on completion.

- [ ] **Step 4: Verify in browser**

Set home + 3-7 destinations. Loading widget appears. After ~5-15s, markers render. Top-1 marker is biggest + has an alert badge. Verify the URL hash continues to round-trip.

- [ ] **Step 5: Commit**

---

## Task 9: Drill-down view + fact panel

**Files:**
- Create: `app/static/drilldown.js`
- Modify: `app/static/app.js`, `app/static/overview.js`, `app/static/styles.css`

**Spec ref:** §2.2 (drill-down view), §4.3 (HIN annotation copy), §4.4 (fallback panel content), §4.5 (gap callout).

**Design notes:**
- Triggered by clicking any route line, destination pin, or avoided-intersection marker on the overview.
- Sets `state.drilledPair = {destId, kind: "fast" | "safe"}` (kind chosen by which line/marker was clicked; pin click defaults to "safe").
- `drilldown.js` exports `renderDrilldown(map, state)` and `exitDrilldown(map)`.
- Renders:
  - Map zooms to fit pair, capped at zoom 13. For pairs whose bounds don't fit at zoom 13 (long cross-town routes), render at zoom 13 centered on midpoint and allow scroll/pan along the route. Add a "Fit route" button that restores full-bounds view.
  - Other destinations hidden (use `setLayoutProperty` to set visibility to `none` on their layers).
  - Avoided-intersection marker for THIS pair stays prominent; others hidden.
  - Fact panel slides in from right (340px). Content:
    - Destination name + address
    - Side-by-side metrics: fast vs. safe distance + estimated time (assume 10 mph avg cycling speed → minutes)
    - Detour cost: extra miles + extra minutes
    - Key gap callout (per the gap-analysis result's `headline` field): named segment/intersection + HIN status if applicable + link to treatment
    - "Fast route crosses" list: HIN intersections, LTS-3 segments
    - "Safe route uses" list: LTS-1 streets, neighborhood greenways
    - "Copy permalink" button (Task 10)
  - Breadcrumb at top of fact panel: `← Back to overview` (clears drilledPair).

- [ ] **Step 1: Implement `renderDrilldown` + `exitDrilldown`**

State subscriber: when `state.drilledPair` changes from null to non-null, call renderDrilldown; from non-null to null, call exitDrilldown.

- [ ] **Step 2: Build the fact panel HTML structure + CSS slide-in**

```css
#fact-panel {
  position: absolute; right: 0; top: 0; bottom: 0; width: 340px;
  background: var(--c-bg); box-shadow: -4px 0 16px rgba(15,23,42,0.12);
  transform: translateX(100%); transition: transform 200ms ease;
  overflow-y: auto;
}
#fact-panel.open { transform: translateX(0); }
```

- [ ] **Step 3: Click handlers on routes/pins/markers**

Add MapLibre `click` event listeners that read the clicked feature ID and `setDrilledPair(...)`.

- [ ] **Step 4: HIN-annotation copy in the fact panel**

Per spec §4.3, draw the copy from the route's HIN counts (when /routes adds them — currently deferred per Plan 2A "Out of scope"). For v1, gather HIN data from the gap-analysis result's headline candidate (which has `on_hin`) and display "On the Cook County HIN" badge if true. Note: this is partial support; full HIN-on-route counts depends on a small Plan 2A patch.

- [ ] **Step 5: Verify in browser**

Click on a destination pin → drill-down activates, fact panel slides in. Other dests hidden. Click "← Back" → returns to overview, all dests visible again. URL hash reflects drill state.

- [ ] **Step 6: Commit**

---

## Task 10: Permalink modal + final QA

**Files:**
- Modify: `app/static/index.html`, `app/static/styles.css`, `app/static/app.js`, `app/static/state.js`, `app/static/drilldown.js`

**Spec ref:** §2.3 (sharing-foot-gun mitigation: precise vs approximate).

**Design notes:**
- Copy-permalink button (in fact panel) → opens modal:
  > *"This link encodes your home address. Share it with people you trust — your alderman, your block club, your neighbors. Don't post it publicly (Twitter, Reddit, etc.) unless you want everyone to know where you live."*
- Two buttons: **Copy precise link** | **Copy approximate link**.
- Approximate: round home coords to ~3 decimal places (~110m precision; effectively block-level). Set `home.approximate = true` in encoded state. Loaded approximate links show "Approximate home location — routes are illustrative" badge in the UI.
- Per-copy choice; remember last-used in sessionStorage as default for future copies in same session.

- [ ] **Step 1: Add modal HTML + CSS**

Modal centered over map; backdrop semi-opaque. ESC closes; click outside closes.

- [ ] **Step 2: Wire copy-permalink button → modal → clipboard write**

Use `navigator.clipboard.writeText(window.location.href)` for precise; for approximate, encode with rounded coords first, then write.

- [ ] **Step 3: "Approximate home location" UI badge when state.home.approximate is true**

Show in top-right area or inline in the fact panel header. Subtle yellow background.

- [ ] **Step 4: Final QA against spec §6.4 launch criteria 4-7**

Walk through each manually:
- #4 Routing reasonableness: 10 hand-tested home addresses across diverse neighborhoods.
- #5 Gap analysis quality: gap callouts name actual known-bad infrastructure.
- #6 Permalink round-trip: copy URL → paste in fresh browser → reproduces exact view (including drill-down).
- #7 Privacy: open DevTools network tab, set home + dests + drill, copy permalink. Verify no GET requests carry coordinates in query params; only POST bodies.

For each criterion, take a screenshot via preview_screenshot and document.

- [ ] **Step 5: Commit**

```bash
git add chicago-bike-advocacy-map/app/static/
git commit -m "feat(frontend): permalink modal with precise/approximate sharing"
```

---

## Done

After Task 10, Plan 2B is complete. The full advocacy artifact:

- Lands at `http://localhost:8000` (or wherever Flask is bound).
- Lets a Chicago resident enter their home address.
- Picks 7 categorized destinations + free-form custom destinations.
- Draws fast + safe routes per destination at all 3 tiers.
- Aggregates gap analysis across destinations into priority-ranked avoided-intersection markers.
- Drills down on click for per-destination detail + key gap callout + treatment links.
- Generates shareable permalinks (precise or approximate) that round-trip the entire view.

**What's still needed before launch (Plan 2C):**
- Render deploy + render.yaml + Dockerfile finalization (Dockerfile already exists from Plan 2A bench).
- `make upload-db` tool to push bikemap.db to Render's persistent disk.
- HIN-annotation counts in /routes payload (small Plan 2A patch — defer until Plan 2C).
- The 3 launch follow-ups already addressed in Plan 2A.5.

**Out-of-scope optimizations to revisit if launch criteria fail:**
- Pickled-igraph artifact for <10s startup (spec §3.10 — only if startup creeps above 60s).
- Async LRU cache eviction (spec §3.5 — only if cache nears 500 MB).
- Multi-vertex segment geometry in gap candidates (spec §4.5 — only if accuracy complaints).
