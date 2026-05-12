// Top-level frontend entry. Wires state, view rendering, and event handlers.
// Plan 2B Task 1: bootstrap.
// Plan 2B Task 2: map init + basemap toggle.
// Plan 2B Task 3: state module + URL hash codec.
// Plan 2B Task 4: tier selector.
// Plan 2B Task 5: address geocoding + home pin.

import {
  initMap,
  setBasemap,
  renderHome,
  renderDestinations,
  renderRoutes,
  renderAvoidedIntersections,
  aggregateGaps,
  flyTo,
} from "/static/overview.js";
import { renderDrilldown, exitDrilldown } from "/static/drilldown.js";
import * as state from "/static/state.js";
import * as api from "/static/api.js";

// Snapshot of the last gap-analysis sweep. drilldown.js reads this for
// the headline-callout and route-metrics in the fact panel.
const lastGapResults = new Map();

state.loadFromHash();
window.addEventListener("hashchange", state.loadFromHash);

const map = initMap(document.getElementById("map"));
// Expose for in-page debugging (e.g., simulating fallback rendering from
// DevTools). Not used by the app itself.
window.__map = map;

let basemap = "streets";
const toggleBtn = document.getElementById("basemap-toggle");
toggleBtn.addEventListener("click", () => {
  basemap = basemap === "streets" ? "satellite" : "streets";
  setBasemap(map, basemap);
  toggleBtn.textContent = basemap === "streets" ? "Satellite" : "Streets";
  // After the new style finishes loading, re-add route line layers
  // (setStyle wipes all custom sources/layers).
  map.once("style.load", () => {
    const s = state.getState();
    renderRoutes(map, s.home, s.destinations, s.tier, api.fetchRoutes);
  });
});

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

// Sync initial UI to state (in case loadFromHash pre-populated tier).
for (const btn of tierSelector.querySelectorAll("button[data-tier]")) {
  btn.classList.toggle("active", btn.dataset.tier === state.getState().tier);
}

// Home-address geocoding.
const homeForm = document.getElementById("home-form");
const homeInput = document.getElementById("home-input");
const homeError = document.getElementById("home-error");
const homeSubmit = homeForm.querySelector("button[type=submit]");

homeForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = homeInput.value.trim();
  if (!address) return;
  homeError.textContent = "";
  homeSubmit.disabled = true;
  try {
    const { lat, lon, display_name } = await api.geocode(address);
    state.setHome({ lat, lon, displayName: display_name, approximate: false });
  } catch (err) {
    if (err && err.status === 404) {
      homeError.textContent = "No results for that address.";
    } else if (err && err.status === 502) {
      homeError.textContent = "Geocoder unreachable. Try again.";
    } else {
      homeError.textContent = "Couldn't geocode that address.";
    }
  } finally {
    homeSubmit.disabled = false;
  }
});

// Re-render home pin whenever state.home changes. Track previous value so
// we only recenter the map on the FIRST set, not on every state mutation.
let lastHome = null;
state.subscribe((s) => {
  renderHome(map, s.home);
  if (s.home && !lastHome) {
    flyTo(map, s.home.lat, s.home.lon);
    homeInput.value = s.home.displayName || "";
  }
  lastHome = s.home;
});

// Pre-populate UI from loaded hash state.
{
  const s0 = state.getState();
  renderHome(map, s0.home);
  if (s0.home) {
    flyTo(map, s0.home.lat, s0.home.lon);
    homeInput.value = s0.home.displayName || "";
    lastHome = s0.home;
  }
}

// Destination categories + custom address (Task 6).
const destSidebar = document.getElementById("dest-sidebar");
const destCategories = document.getElementById("dest-categories");
const customForm = document.getElementById("custom-form");
const customInput = document.getElementById("custom-input");
const customError = document.getElementById("custom-error");
const customSubmit = customForm.querySelector("button[type=submit]");

function destIdForCategory(cat) {
  return `cat:${cat}`;
}

function showSidebarIfHomeSet(s) {
  destSidebar.hidden = !s.home;
}

destCategories.addEventListener("change", async (e) => {
  const cb = e.target.closest("input[type=checkbox][data-category]");
  if (!cb) return;
  const category = cb.dataset.category;
  const s = state.getState();
  if (!s.home) { cb.checked = false; return; }

  if (cb.checked) {
    cb.disabled = true;
    try {
      const poi = await api.fetchPois({ lat: s.home.lat, lon: s.home.lon }, category);
      const dest = {
        id: destIdForCategory(category),
        lat: poi.lat,
        lon: poi.lon,
        name: poi.name,
        address: poi.address,
        category: poi.category,
        icon: poi.category,
        approximate: false,
      };
      state.setDestinations([...state.getState().destinations, dest]);
    } catch (err) {
      cb.checked = false;
      console.warn("POI lookup failed", err);
    } finally {
      cb.disabled = false;
    }
  } else {
    const id = destIdForCategory(category);
    state.setDestinations(state.getState().destinations.filter((d) => d.id !== id));
  }
});

customForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = customInput.value.trim();
  if (!address) return;
  customError.textContent = "";
  customSubmit.disabled = true;
  try {
    const { lat, lon, display_name } = await api.geocode(address);
    const dest = {
      id: `custom:${Date.now()}`,
      lat, lon,
      name: address,
      address: display_name,
      category: "custom",
      icon: "custom",
      approximate: false,
    };
    state.setDestinations([...state.getState().destinations, dest]);
    customInput.value = "";
  } catch (err) {
    if (err && err.status === 404) {
      customError.textContent = "No results for that address.";
    } else {
      customError.textContent = "Couldn't geocode that address.";
    }
  } finally {
    customSubmit.disabled = false;
  }
});

function syncCategoryCheckboxes(dests) {
  const activeCats = new Set(
    dests.filter((d) => d.id.startsWith("cat:")).map((d) => d.id.slice(4)),
  );
  for (const cb of destCategories.querySelectorAll("input[type=checkbox][data-category]")) {
    cb.checked = activeCats.has(cb.dataset.category);
  }
}

// Resolved-destinations list: shows the actual name/address the app picked
// (POI for cat:* destinations, geocoded display_name for custom:*) plus
// straight-line distance to home and a × delete button. Without this list
// the user has no way to see which Park/Hospital/etc. the app chose, and
// no per-destination way to remove a custom address.
const ICONS = {
  school: "🏫", park: "🌳", grocery: "🛒", hospital: "🏥",
  alderman: "🏛️", library: "📚", transit: "🚆", custom: "📍",
};

function destDistanceMi(home, d) {
  if (!home) return null;
  const R = 3958.8;  // miles
  const toRad = (x) => x * Math.PI / 180;
  const dLat = toRad(d.lat - home.lat);
  const dLon = toRad(d.lon - home.lon);
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(home.lat)) * Math.cos(toRad(d.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

const destList = document.getElementById("dest-list");

function renderDestList(home, dests) {
  if (!destList) return;
  destList.innerHTML = "";
  for (const d of dests) {
    const li = document.createElement("li");
    li.className = "dest-list-item";
    li.dataset.destId = d.id;
    const icon = ICONS[d.icon] || ICONS[d.category] || "📍";
    const dist = destDistanceMi(home, d);
    const distLabel = dist != null ? ` · ${dist.toFixed(1)} mi` : "";
    li.innerHTML = `
      <span class="dl-icon" aria-hidden="true">${icon}</span>
      <span class="dl-text">
        <span class="dl-name">${escapeHtml(d.name || d.address || "Destination")}</span>
        <span class="dl-sub">${escapeHtml((d.address && d.address !== d.name) ? d.address : "")}${distLabel}</span>
      </span>
      <button class="dl-remove" type="button" aria-label="Remove ${escapeHtml(d.name || "destination")}" title="Remove">×</button>
    `;
    destList.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

destList?.addEventListener("click", (e) => {
  const btn = e.target.closest(".dl-remove");
  if (!btn) return;
  const li = btn.closest("[data-dest-id]");
  if (!li) return;
  const id = li.dataset.destId;
  state.setDestinations(state.getState().destinations.filter((d) => d.id !== id));
  // If the removed dest was a category, syncCategoryCheckboxes will untick
  // the matching checkbox on the next state notification.
});

// Auto-fit the map to home + all destinations whenever destinations change.
// Use a sticky "last fit signature" so we don't re-fit on every state change
// (e.g., drilling in/out) — only when the set of dest coords actually changes.
let lastFitSig = "";
function maybeAutoFit(s) {
  if (!s.home || !s.destinations.length) return;
  const sig = `${s.home.lat},${s.home.lon}|` +
    s.destinations.map((d) => `${d.id}:${d.lat},${d.lon}`).sort().join("|");
  if (sig === lastFitSig) return;
  lastFitSig = sig;
  let minLat = s.home.lat, maxLat = s.home.lat;
  let minLon = s.home.lon, maxLon = s.home.lon;
  for (const d of s.destinations) {
    if (d.lat < minLat) minLat = d.lat;
    if (d.lat > maxLat) maxLat = d.lat;
    if (d.lon < minLon) minLon = d.lon;
    if (d.lon > maxLon) maxLon = d.lon;
  }
  if (map.isStyleLoaded()) {
    map.fitBounds([[minLon, minLat], [maxLon, maxLat]],
      { padding: 80, maxZoom: 14, duration: 600 });
  } else {
    map.once("style.load", () => map.fitBounds(
      [[minLon, minLat], [maxLon, maxLat]],
      { padding: 80, maxZoom: 14, duration: 600 },
    ));
  }
}

state.subscribe((s) => {
  showSidebarIfHomeSet(s);
  renderDestinations(map, s.destinations);
  syncCategoryCheckboxes(s.destinations);
  renderDestList(s.home, s.destinations);
  maybeAutoFit(s);
});

// Initial sync from hash.
showSidebarIfHomeSet(state.getState());
renderDestinations(map, state.getState().destinations);
syncCategoryCheckboxes(state.getState().destinations);

// Routes — debounce so rapid back-to-back state mutations (toggling 3
// categories in a row) only trigger one fetch sweep.
let routesDebounceTimer = null;
function scheduleRouteRender() {
  if (routesDebounceTimer) clearTimeout(routesDebounceTimer);
  routesDebounceTimer = setTimeout(() => {
    routesDebounceTimer = null;
    const s = state.getState();
    if (map.isStyleLoaded()) {
      renderRoutes(map, s.home, s.destinations, s.tier, api.fetchRoutes);
    } else {
      map.once("style.load", () => {
        renderRoutes(map, s.home, s.destinations, s.tier, api.fetchRoutes);
      });
    }
  }, 200);
}

state.subscribe(scheduleRouteRender);

// Gap analysis aggregation (Task 8). Slower than /routes (cold cache 5-15s
// per dest, ~3 workers), so we run after routes and show a loading widget.
const gapLoading = document.getElementById("gap-loading");
const gapLoadingText = gapLoading.querySelector(".gap-loading-text");
let gapDebounceTimer = null;
let gapRunId = 0;

function scheduleGapAnalysis() {
  if (gapDebounceTimer) clearTimeout(gapDebounceTimer);
  gapDebounceTimer = setTimeout(async () => {
    gapDebounceTimer = null;
    const s = state.getState();
    if (!s.home || !s.destinations.length) {
      renderAvoidedIntersections(map, []);
      gapLoading.hidden = true;
      return;
    }

    const runId = ++gapRunId;
    const total = s.destinations.length;
    let done = 0;
    gapLoading.hidden = false;
    gapLoadingText.textContent = `Computing gap analysis… (0/${total} destinations)`;

    const home = { lat: s.home.lat, lon: s.home.lon };
    const tier = s.tier;
    const perPairResults = new Map();

    await Promise.all(
      s.destinations.map(async (d) => {
        try {
          const r = await api.fetchGapAnalysis(home, { lat: d.lat, lon: d.lon }, tier);
          perPairResults.set(d.id, r);
        } catch (err) {
          console.warn(`gap-analysis failed for ${d.id}`, err);
        } finally {
          done += 1;
          if (runId === gapRunId) {
            gapLoadingText.textContent =
              `Computing gap analysis… (${done}/${total} destinations)`;
          }
        }
      }),
    );

    // Bail if a newer run started.
    if (runId !== gapRunId) return;
    lastGapResults.clear();
    for (const [k, v] of perPairResults) lastGapResults.set(k, v);
    const aggregated = aggregateGaps(perPairResults);
    renderAvoidedIntersections(map, aggregated);
    gapLoading.hidden = true;
    // Re-render drill-down if the user is already drilled in (so the
    // fact-panel headline reflects the freshly-completed gap data).
    const s2 = state.getState();
    if (s2.drilledPair) renderDrilldown(map, s2, lastGapResults);
  }, 500); // bigger debounce than routes — gap is expensive
}

state.subscribe(scheduleGapAnalysis);

// Initial pass once the map is fully loaded. `load` is the right event
// (fires once when first style + first paint are complete); `style.load`
// races with our render that adds layers, and isStyleLoaded() can flip
// back to false transiently while sources load.
map.on("load", () => {
  scheduleRouteRender();
  scheduleGapAnalysis();
});

// ----- Drill-down (Task 9) -----

// Dest pin click → drill into "safe" route for that dest (default kind).
document.body.addEventListener("click", (e) => {
  const destEl = e.target.closest(".dest-marker[data-dest-id]");
  if (destEl) {
    state.setDrilledPair({ destId: destEl.dataset.destId, kind: "safe" });
  }
});

// Route line click. We can't attach DOM listeners directly to MapLibre
// line layers — listen to map clicks and use queryRenderedFeatures with
// the route-* layer ids. Picking the topmost feature gives us destId + kind.
map.on("click", (e) => {
  const routeLayerIds = map
    .getStyle()
    .layers
    .filter((l) => /^route-(.+)-(fast|safe)-lyr$/.test(l.id))
    .map((l) => l.id);
  if (!routeLayerIds.length) return;
  const features = map.queryRenderedFeatures(e.point, { layers: routeLayerIds });
  if (!features.length) return;
  const layerId = features[0].layer.id;
  const m = /^route-(.+)-(fast|safe)-lyr$/.exec(layerId);
  if (!m) return;
  state.setDrilledPair({ destId: m[1], kind: m[2] });
});
// Cursor hint when hovering a route line.
map.on("mousemove", (e) => {
  const routeLayerIds = map
    .getStyle()
    .layers
    .filter((l) => /^route-(.+)-(fast|safe)-lyr$/.test(l.id))
    .map((l) => l.id);
  if (!routeLayerIds.length) {
    map.getCanvas().style.cursor = "";
    return;
  }
  const features = map.queryRenderedFeatures(e.point, { layers: routeLayerIds });
  map.getCanvas().style.cursor = features.length ? "pointer" : "";
});

// Back button + copy-link button events from drilldown.js.
document.body.addEventListener("fact-panel-back", () => {
  state.setDrilledPair(null);
});

// State subscriber: enter/exit drill-down on drilledPair change.
let lastDrilledKey = null;
state.subscribe((s) => {
  const key = s.drilledPair ? `${s.drilledPair.destId}|${s.drilledPair.kind}` : null;
  if (key === lastDrilledKey) return;
  lastDrilledKey = key;
  if (s.drilledPair) {
    renderDrilldown(map, s, lastGapResults);
  } else {
    exitDrilldown(map, s);
  }
});

// Initial sync if state already has a drilledPair from the hash.
if (state.getState().drilledPair) {
  // Wait for the first gap sweep so the fact panel has data to show.
  // The subscriber will fire it on completion via scheduleGapAnalysis.
  renderDrilldown(map, state.getState(), lastGapResults);
}

// ----- Permalink modal (Task 10) -----

const permalinkModal = document.getElementById("permalink-modal");
const approxBadge = document.getElementById("approx-badge");
const PM_TOAST = permalinkModal.querySelector(".pm-toast");

function openPermalinkModal() {
  permalinkModal.hidden = false;
  PM_TOAST.hidden = true;
}
function closePermalinkModal() {
  permalinkModal.hidden = true;
}

document.body.addEventListener("fact-panel-copy", openPermalinkModal);

permalinkModal.querySelector(".pm-close").addEventListener("click", closePermalinkModal);
permalinkModal.querySelector(".pm-backdrop").addEventListener("click", closePermalinkModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !permalinkModal.hidden) closePermalinkModal();
});

function buildShareUrl(mode) {
  const s = state.getState();
  let payload = s;
  if (mode === "approximate" && s.home) {
    payload = {
      ...s,
      home: {
        ...s.home,
        lat: Math.round(s.home.lat * 1000) / 1000,
        lon: Math.round(s.home.lon * 1000) / 1000,
        approximate: true,
      },
    };
  }
  const hash = state.encodeStateToHash(payload);
  return `${window.location.origin}${window.location.pathname}#${hash}`;
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    return false;
  }
}

permalinkModal.querySelectorAll(".pm-btn[data-mode]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const mode = btn.dataset.mode;
    const url = buildShareUrl(mode);
    const ok = await copyToClipboard(url);
    PM_TOAST.hidden = false;
    PM_TOAST.textContent = ok
      ? `Copied ${mode} permalink to clipboard`
      : "Couldn't copy. Select the address bar and copy manually.";
    try {
      sessionStorage.setItem("permalink-mode", mode);
    } catch (_) { /* sessionStorage may be unavailable */ }
  });
});

// Restore last-used mode by reordering the buttons so the remembered
// mode is the visually primary one.
try {
  const lastMode = sessionStorage.getItem("permalink-mode");
  if (lastMode === "approximate") {
    const approxBtn = permalinkModal.querySelector('.pm-btn[data-mode="approximate"]');
    const preciseBtn = permalinkModal.querySelector('.pm-btn[data-mode="precise"]');
    approxBtn.classList.add("pm-btn-primary");
    approxBtn.classList.remove("pm-btn-secondary");
    preciseBtn.classList.add("pm-btn-secondary");
    preciseBtn.classList.remove("pm-btn-primary");
    approxBtn.parentNode.insertBefore(approxBtn, preciseBtn);
  }
} catch (_) { /* sessionStorage unavailable */ }

// Approximate-home badge.
state.subscribe((s) => {
  approxBadge.hidden = !(s.home && s.home.approximate);
});
approxBadge.hidden = !(state.getState().home && state.getState().home.approximate);
