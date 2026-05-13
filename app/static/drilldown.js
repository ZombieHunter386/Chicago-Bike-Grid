// Drill-down view: zoomed pair + fact panel. Triggered by setting
// state.drilledPair from a click on a route line, dest pin, or
// avoided-intersection marker.
//
// renderDrilldown(map, state, allResults) — show panel + zoom to pair,
//   hide other destinations + their route layers.
// exitDrilldown(map, state) — restore overview visibility.
//
// allResults is a Map<destId, gapResult> the caller maintains in app.js
// from the last gap-analysis sweep; we read the headline candidate +
// route metadata from it for the fact-panel copy.

const ASSUMED_MPH = 10;

function metersToMiles(m) { return m / 1609.34; }
function metersToMinutes(m) { return (metersToMiles(m) / ASSUMED_MPH) * 60; }

function fmtMiles(m) { return `${metersToMiles(m).toFixed(2)} mi`; }
function fmtMinutes(m) {
  const mins = metersToMinutes(m);
  return `${Math.max(1, Math.round(mins))} min`;
}

function findDest(state, destId) {
  return state.destinations.find((d) => d.id === destId) || null;
}

function visibleDestIds(state) {
  const drilled = state.drilledPair && state.drilledPair.destId;
  return drilled ? new Set([drilled]) : new Set(state.destinations.map((d) => d.id));
}

// Set visibility on route layers + dest markers based on drilled state.
function applyDrillVisibility(map, state) {
  const keepIds = visibleDestIds(state);
  const drilled = !!state.drilledPair;

  for (const layer of map.getStyle().layers) {
    const m = /^route-(.+)-(fast|safe)-lyr$/.exec(layer.id);
    if (!m) continue;
    const destId = m[1];
    const kind = m[2];
    let visible = keepIds.has(destId);
    if (visible && drilled && state.drilledPair.kind && state.drilledPair.kind !== kind) {
      // When drilling into a specific route kind, still show the other
      // kind for the same pair so the user can compare. Keep visible.
      visible = true;
    }
    map.setLayoutProperty(layer.id, "visibility", visible ? "visible" : "none");
  }

  for (const el of document.querySelectorAll(".dest-marker")) {
    const titleId = el.dataset.destId;
    if (!titleId) continue;
    el.style.display = keepIds.has(titleId) ? "" : "none";
  }
  for (const el of document.querySelectorAll(".gap-marker")) {
    el.style.display = drilled ? "none" : "";
  }
}

function bboxOfPolylines(polylines) {
  let minLat = Infinity, minLon = Infinity, maxLat = -Infinity, maxLon = -Infinity;
  for (const pl of polylines) {
    if (!pl) continue;
    for (const p of pl) {
      if (p.lat < minLat) minLat = p.lat;
      if (p.lat > maxLat) maxLat = p.lat;
      if (p.lon < minLon) minLon = p.lon;
      if (p.lon > maxLon) maxLon = p.lon;
    }
  }
  if (!isFinite(minLat)) return null;
  return [[minLon, minLat], [maxLon, maxLat]];
}

export function renderDrilldown(map, state, allResults) {
  const pair = state.drilledPair;
  if (!pair) return exitDrilldown(map, state);
  const dest = findDest(state, pair.destId);
  const result = allResults ? allResults.get(pair.destId) : null;
  if (!dest) return exitDrilldown(map, state);

  applyDrillVisibility(map, state);

  // Fit map to the route bbox (clamped at zoom 13).
  const polylines = [];
  if (result && result.fast_route && result.fast_route.polyline) polylines.push(result.fast_route.polyline);
  if (result && result.safe_route && result.safe_route.polyline) polylines.push(result.safe_route.polyline);
  const bbox = bboxOfPolylines(polylines);
  if (bbox) {
    map.fitBounds(bbox, { padding: 60, maxZoom: 13, duration: 600 });
  }

  renderFactPanel(state, dest, result);
}

export function exitDrilldown(map, state) {
  applyDrillVisibility(map, state);
  closeFactPanel();
}

// ----- Fact panel ----------------------------------------------------------

function closeFactPanel() {
  const panel = document.getElementById("fact-panel");
  if (panel) panel.classList.remove("open");
}

function renderFactPanel(state, dest, result) {
  const panel = document.getElementById("fact-panel");
  if (!panel) return;

  const fast = result && result.fast_route;
  const safe = result && result.safe_route;
  const corridor = result && result.corridor;
  const intersections = (result && result.intersections) || [];
  const isFallback = !!(result && result.safe_route_is_fallback);

  const fastLen = fast ? fast.length_m : null;
  const safeLen = safe ? safe.length_m : null;
  const detourM = fastLen != null && safeLen != null ? Math.max(0, safeLen - fastLen) : null;

  const hasAdvocacy = corridor || intersections.length > 0;
  const corridorHtml = corridor ? renderCorridorCallout(corridor) : "";
  const intersectionsHtml = intersections.length
    ? renderIntersectionsList(intersections)
    : "";
  const emptyHtml = hasAdvocacy
    ? ""
    : '<p class="fp-empty">No notable gaps identified for this destination at the current tier.</p>';

  const fallbackBadge = isFallback
    ? '<div class="fp-warning">Best effort — no fully safe path at this tier.</div>'
    : "";

  panel.innerHTML = `
    <header class="fp-header">
      <button class="fp-back" type="button" aria-label="Back to overview">← Back to overview</button>
    </header>
    <div class="fp-body">
      <h2 class="fp-title">${escapeHtml(dest.name || dest.address || "Destination")}</h2>
      ${dest.address && dest.address !== dest.name ? `<p class="fp-sub">${escapeHtml(dest.address)}</p>` : ""}
      ${fallbackBadge}
      <div class="fp-metrics">
        <div class="fp-metric">
          <span class="fp-metric-label">Fast</span>
          <span class="fp-metric-value">${fastLen != null ? fmtMiles(fastLen) : "—"}</span>
          <span class="fp-metric-sub">${fastLen != null ? fmtMinutes(fastLen) : ""}</span>
        </div>
        <div class="fp-metric">
          <span class="fp-metric-label">Safe</span>
          <span class="fp-metric-value">${safeLen != null ? fmtMiles(safeLen) : "—"}</span>
          <span class="fp-metric-sub">${safeLen != null ? fmtMinutes(safeLen) : ""}</span>
        </div>
        <div class="fp-metric">
          <span class="fp-metric-label">Detour cost</span>
          <span class="fp-metric-value">${detourM != null ? fmtMiles(detourM) : "—"}</span>
          <span class="fp-metric-sub">${detourM != null ? `+${fmtMinutes(detourM)}` : ""}</span>
        </div>
      </div>
      <section class="fp-section">
        <h3>Advocacy ask</h3>
        ${corridorHtml}
        ${intersectionsHtml}
        ${emptyHtml}
      </section>
      <section class="fp-section">
        <h3>Permalink</h3>
        <button class="fp-copy-link" type="button">Copy permalink…</button>
      </section>
    </div>
  `;
  panel.classList.add("open");

  panel.querySelector(".fp-back").addEventListener("click", () => {
    // Caller (app.js) listens for state.drilledPair = null to call exitDrilldown.
    panel.dispatchEvent(new CustomEvent("fact-panel-back", { bubbles: true }));
  });
  panel.querySelector(".fp-copy-link").addEventListener("click", () => {
    panel.dispatchEvent(new CustomEvent("fact-panel-copy", { bubbles: true }));
  });
}

// D' corridor callout (spec §4.5): one combined advocacy ask covering every
// LTS-above-tier street on the fast route, with per-road marginal numbers so
// the advocate sees which streets are load-bearing.
function renderCorridorCallout(corridor) {
  const totalSavings = Math.round(corridor.combined_savings_m);
  const flips = !!corridor.flips_to_fully_safe;
  const flipsBadge = flips
    ? '<span class="fp-fix-badge" title="Fixing this corridor makes a fully on-tier safe route possible">FIX THIS</span>'
    : "";
  const headingText = flips
    ? `Fix this corridor to unlock a fully safe ride — saves ~${totalSavings} m`
    : `Fix this corridor — saves ~${totalSavings} m`;
  const roadsHtml = corridor.roads.map((r) => renderCorridorRoadRow(r, corridor.combined_savings_m)).join("");
  return `
    <div class="fp-callout${flips ? " fp-callout-unfallback" : ""}">
      <div class="fp-callout-head">
        <strong>${escapeHtml(headingText)}</strong>
        ${flipsBadge}
      </div>
      <ul class="fp-corridor-roads">${roadsHtml}</ul>
    </div>
  `;
}

// One per-road row. marginal_loss_m is "what you give up if this street is
// dropped from the upgrade set" — the load-bearing signal for advocates.
//
// Edge case to be honest about: marginal_loss_m can exceed combined_savings_m
// when dropping a road causes Dijkstra (under the partial-upgrade weights)
// to pick a route that's actually longer than the current safe route. The
// raw math is correct (marginal = combined - savings_without; if savings_
// without is negative because the partial-fix path is worse, marginal goes
// above combined). But "Drop and lose ~2701 m of the combined savings ~2507"
// reads as broken. We clamp the displayed loss to combined and label the
// road as "essential" — fixing this street is required to unlock ANY of
// the corridor's savings; without it you don't get partial improvement,
// you may end up worse than no fix.
function renderCorridorRoadRow(road, combinedSavings) {
  const name = road.name || `Unnamed segment ${road.road_ids[0]}`;
  const blocks = road.block_count;
  const blocksLabel = `${blocks} block${blocks === 1 ? "" : "s"}`;
  const rawMarginal = Math.max(0, Math.round(road.marginal_loss_m));
  const cappedCombined = Math.max(0, Math.round(combinedSavings));
  const isEssential = cappedCombined > 0 && rawMarginal >= cappedCombined;
  const marginal = Math.min(rawMarginal, cappedCombined);
  const hinTag = road.on_hin
    ? '<span class="fp-hin-badge" title="On the Cook County High-Injury Network">HIN</span>'
    : "";
  // Marginal=0 means dropping this street doesn't change the savings —
  // optional fix, not part of the critical ask. Greyed out so it reads as
  // "minor".
  const minorClass = marginal === 0 ? " fp-corridor-road-minor" : "";
  let body;
  if (marginal === 0) {
    body = "<em>minor — dropping has no impact</em>";
  } else if (isEssential) {
    body = `<strong>Essential</strong> — required for the corridor fix; drop this and you lose all ~${marginal} m of the combined savings`;
  } else {
    body = `Drop this and you lose ~${marginal} m of the combined savings`;
  }
  return `
    <li class="fp-corridor-road${minorClass}">
      <div class="fp-corridor-road-head">
        <strong>${escapeHtml(name)}</strong>
        <span class="fp-corridor-road-sub">${blocksLabel}</span>
        ${hinTag}
      </div>
      <div class="fp-corridor-road-marginal">${body}</div>
    </li>
  `;
}

// Danger-intersections list — surfaced separately from the corridor because
// intersections are point features without a corridor analog.
function renderIntersectionsList(intersections) {
  const rowsHtml = intersections.map((i) => {
    const name = i.name || `Intersection #${i.int_id}`;
    const savings = Math.round(i.savings_m);
    const flipsBadge = i.flips_to_fully_safe
      ? '<span class="fp-fix-badge" title="Fixing this alone flips the safe route to fully on-tier">FIX THIS</span>'
      : "";
    const hinTag = i.on_hin
      ? '<span class="fp-hin-badge" title="On the Cook County High-Injury Network">HIN</span>'
      : "";
    return `
      <li class="fp-intersection">
        <div class="fp-intersection-head">
          <strong>${escapeHtml(name)}</strong>
          ${flipsBadge}
          ${hinTag}
        </div>
        <div class="fp-intersection-sub">
          Fixing this intersection shortens the safe route by ~${savings} m.
        </div>
      </li>
    `;
  }).join("");
  return `
    <div class="fp-intersection-block">
      <h4 class="fp-section-sub">Danger intersections</h4>
      <ul class="fp-intersection-list">${rowsHtml}</ul>
    </div>
  `;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}
