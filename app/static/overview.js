// Overview view: home + destinations + routes + avoided-intersection markers.
// Plan 2B Task 2 lands the map shell + basemap toggle. Later tasks
// (Task 5–8) add the rest.

const CHICAGO_CENTER = [-87.63, 41.88];
const DEFAULT_ZOOM = 11;

const STREETS_STYLE = "https://tiles.openfreemap.org/styles/liberty";

const SATELLITE_STYLE = {
  version: 8,
  sources: {
    "esri-imagery": {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      attribution:
        "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
    },
  },
  layers: [
    { id: "esri-imagery", type: "raster", source: "esri-imagery" },
  ],
};

export function initMap(container) {
  return new maplibregl.Map({
    container,
    style: STREETS_STYLE,
    center: CHICAGO_CENTER,
    zoom: DEFAULT_ZOOM,
  });
}

export function setBasemap(map, kind) {
  map.setStyle(kind === "satellite" ? SATELLITE_STYLE : STREETS_STYLE);
}

let homeMarker = null;

export function renderHome(map, home) {
  if (homeMarker) {
    homeMarker.remove();
    homeMarker = null;
  }
  if (!home) return;
  const el = document.createElement("div");
  el.className = "home-marker";
  el.title = home.displayName || "Home";
  homeMarker = new maplibregl.Marker({ element: el })
    .setLngLat([home.lon, home.lat])
    .addTo(map);
}

// Center the map on a point. Used after home is set so the user lands on
// their neighborhood rather than the default Chicago centroid.
export function flyTo(map, lat, lon, zoom = 13) {
  map.flyTo({ center: [lon, lat], zoom, speed: 1.4 });
}

const CATEGORY_ICONS = {
  school: "🏫",
  park: "🌳",
  grocery: "🛒",
  hospital: "🏥",
  alderman: "🏛️",
  library: "📚",
  transit: "🚆",
  custom: "📍",
};

// destMarkers maps dest.id -> maplibregl.Marker. Diff-rendered so that
// adding one dest doesn't tear down the others.
const destMarkers = new Map();

// Track which (destId, kind) pairs currently have layers on the map so
// removals are cheap and re-renders idempotent.
const routeLayers = new Set();

function routeSourceId(destId, kind) { return `route-${destId}-${kind}-src`; }
function routeLayerId(destId, kind) { return `route-${destId}-${kind}-lyr`; }

// Build GeoJSON for a route polyline. The endpoints are clipped to the
// user's home and destination markers (the polyline's first/last vertices
// are nearest-intersection snaps that overshoot the marker positions).
//
// `polyline_lts` is the per-segment effective LTS array from the backend
// (one entry per polyline segment, i.e. length == polyline.length - 1).
//
// `splitByLts` controls the output shape:
//   - false → single LineString Feature (used for the fast route, which
//             gets a uniform dashed orange paint regardless of stress).
//   - true  → FeatureCollection where each Feature is a contiguous run
//             of same-LTS segments, with properties.lts set. Used for
//             the safe route so the layer paint can color green / orange /
//             red per LTS level (mockup §2.1 + spec §2.2 advocacy framing).
function lineStringFromPolyline(polyline, polyline_lts, home, dest, splitByLts) {
  if (!polyline || polyline.length < 2) {
    return { type: "FeatureCollection", features: [] };
  }
  if (!splitByLts || !Array.isArray(polyline_lts) || polyline_lts.length === 0) {
    const coords = [
      [home.lon, home.lat],
      ...polyline.map((p) => [p.lon, p.lat]),
      [dest.lon, dest.lat],
    ];
    return {
      type: "Feature",
      geometry: { type: "LineString", coordinates: coords },
      properties: {},
    };
  }
  // Walk the polyline segment by segment, grouping consecutive same-LTS
  // segments into one Feature. Adjacent features share their boundary
  // vertex so the rendered line has no visible gap between color bands.
  const features = [];
  let runStart = 0;
  let runLts = polyline_lts[0];
  for (let segIdx = 1; segIdx <= polyline_lts.length; segIdx++) {
    const isEnd = segIdx === polyline_lts.length;
    const nextLts = isEnd ? null : polyline_lts[segIdx];
    if (isEnd || nextLts !== runLts) {
      const coords = [];
      for (let i = runStart; i <= segIdx; i++) {
        coords.push([polyline[i].lon, polyline[i].lat]);
      }
      // Endpoint clip — prepend home only on the first feature, append
      // dest only on the last.
      if (runStart === 0) coords.unshift([home.lon, home.lat]);
      if (isEnd) coords.push([dest.lon, dest.lat]);
      features.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: coords },
        properties: { lts: runLts },
      });
      runStart = segIdx;
      runLts = nextLts;
    }
  }
  return { type: "FeatureCollection", features };
}

function ensureRouteLayer(map, destId, kind, route, home, dest) {
  const srcId = routeSourceId(destId, kind);
  const lyrId = routeLayerId(destId, kind);
  // BOTH safe and fast routes get per-segment LTS coloring (same hue
  // scheme as /explore). They're visually distinguished by line style:
  //   safe → solid
  //   fast → dashed
  // The dashed-vs-solid signal preserves the fast/safe comparison while
  // letting the user see how stressful each route's segments actually
  // are. Coloring is identical across kinds so the same legend serves
  // both.
  const geojson = lineStringFromPolyline(route.polyline, route.polyline_lts, home, dest, true);

  if (map.getSource(srcId)) {
    map.getSource(srcId).setData(geojson);
  } else {
    map.addSource(srcId, { type: "geojson", data: geojson });
  }

  const ltsColorExpr = [
    // Same hex values used by /explore (LTS_COLOR_EXPR in explore.js)
    // and by the legend swatches below the tier selector.
    "match", ["get", "lts"],
    1, "#16a34a",   // green — LTS 1 (safe for kid)
    2, "#f59e0b",   // orange — LTS 2 (safe for parent)
    3, "#dc2626",   // red — LTS 3 (not safe)
    "#999999",      // fallback for unknown LTS values
  ];
  const paint = (kind === "fast")
    ? {
        "line-color": ltsColorExpr,
        "line-width": 4,
        "line-dasharray": [2, 2],   // dashed = fastest route
      }
    : {
        "line-color": ltsColorExpr,
        "line-width": 4,
        // no dasharray = safe route (solid)
      };

  if (map.getLayer(lyrId)) {
    map.removeLayer(lyrId);
  }
  map.addLayer({
    id: lyrId,
    type: "line",
    source: srcId,
    layout: { "line-cap": "round", "line-join": "round" },
    paint,
    metadata: { destId, kind, isFallback: !!route.is_fallback },
  });
  routeLayers.add(lyrId);
}

function removeRouteLayer(map, destId, kind) {
  const srcId = routeSourceId(destId, kind);
  const lyrId = routeLayerId(destId, kind);
  if (map.getLayer(lyrId)) map.removeLayer(lyrId);
  if (map.getSource(srcId)) map.removeSource(srcId);
  routeLayers.delete(lyrId);
}

// Synchronously prune route layers for destinations that no longer exist
// in state. Call this from a state subscriber on every state mutation so
// dest removal takes immediate visual effect — the async fetch loop below
// is NOT the right place because its snapshot of `dests` can be stale
// relative to newer state, and parallel runs of renderRoutes could nuke
// each other's layers based on those stale snapshots.
export function pruneStaleRouteLayers(map, currentDests) {
  const keepDestIds = new Set(currentDests.map((d) => d.id));
  for (const lyrId of [...routeLayers]) {
    const m = /^route-(.+)-(fast|safe)-lyr$/.exec(lyrId);
    if (m && !keepDestIds.has(m[1])) removeRouteLayer(map, m[1], m[2]);
  }
}

// Generation counter so a stale renderRoutes call (older than the latest
// state mutation) doesn't tear down layers a newer call just added.
let renderRoutesGen = 0;

// Re-render all (home, dest) route pairs at the current tier.
// `fetchRoutes` is injected so this module stays free of api.js dependency
// during testing (and lets callers pass a stub for the fallback codepath).
//
// IMPORTANT: this function does NOT prune layers for missing dests anymore
// — that lifecycle is in pruneStaleRouteLayers, called synchronously from
// a state subscriber. Here we only ADD layers for the dests passed in.
// Mixing the two created a race where:
//   - Run A (older state, fewer dests) fired and started fetching.
//   - Run B (newer state, more dests) fired before Run A's await resolved.
//   - Whichever finished last with a failed fetch called removeRouteLayer,
//     potentially nuking a layer the other run had just added — the user
//     saw a route "show and then hide" on rapid multi-click.
export async function renderRoutes(map, home, dests, tier, fetchRoutes) {
  const myGen = ++renderRoutesGen;

  if (!home) {
    // Special case: no home means no routes at all — wipe everything.
    // Safe to do synchronously here (no parallel race possible).
    for (const lyrId of [...routeLayers]) {
      const [, destId, kind] = /^route-(.+)-(fast|safe)-lyr$/.exec(lyrId);
      removeRouteLayer(map, destId, kind);
    }
    return new Map();
  }

  const results = new Map();
  const fetches = dests.map(async (d) => {
    try {
      const r = await fetchRoutes(
        { lat: home.lat, lon: home.lon },
        { lat: d.lat, lon: d.lon },
        tier,
      );
      // If a newer renderRoutes call has superseded us, don't apply our
      // results — the newer run is responsible for the truth now.
      if (myGen !== renderRoutesGen) return;
      results.set(d.id, r);
      const homeLL = { lat: home.lat, lon: home.lon };
      const destLL = { lat: d.lat, lon: d.lon };
      if (r.fast) ensureRouteLayer(map, d.id, "fast", r.fast, homeLL, destLL);
      else removeRouteLayer(map, d.id, "fast");
      if (r.safe) ensureRouteLayer(map, d.id, "safe", r.safe, homeLL, destLL);
      else removeRouteLayer(map, d.id, "safe");
      // Mark the dest marker as fallback if either route is best-effort.
      const isFallback = !!(r.safe && r.safe.is_fallback);
      const markerEl = destMarkers.get(d.id) && destMarkers.get(d.id).getElement();
      if (markerEl) markerEl.classList.toggle("fallback", isFallback);
    } catch (err) {
      console.warn(`route fetch failed for ${d.id}`, err);
      // Intentionally DO NOT remove layers on fetch error. The dest might
      // still be in state (we just transiently failed to refresh its
      // route — e.g., rate-limited). A parallel renderRoutes run might
      // have just successfully added that layer; removing it here would
      // be the "shows and then hides" race. If the dest is actually
      // gone, pruneStaleRouteLayers will catch it on the next state
      // change.
    }
  });
  await Promise.all(fetches);
  return results;
}

// ----- Gap aggregation + avoided-intersection markers (Task 8, spec §4.6) ----

// Parse WGS84 WKT into one of:
//   {kind: "point", lat, lon}
//   {kind: "line",  coords: [[lon, lat], ...]}
//   {kind: "multiline", lines: [[[lon, lat], ...], ...]}
// Backend emits POINT for intersections, LINESTRING (rare) or
// MULTILINESTRING (corridor overlay + per-road geometry) for segment groups.
export function parseWktWgs84(wkt) {
  if (!wkt) return null;
  const point = /^\s*POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)\s*$/i.exec(wkt);
  if (point) {
    return { kind: "point", lon: parseFloat(point[1]), lat: parseFloat(point[2]) };
  }
  const multi = /^\s*MULTILINESTRING\s*\(\s*(.+)\s*\)\s*$/i.exec(wkt);
  if (multi) {
    // Split parenthesized groups: ((a,b),(c,d)) → ["a,b", "c,d"]
    const inner = multi[1].trim();
    const groups = [];
    let depth = 0, start = -1;
    for (let i = 0; i < inner.length; i += 1) {
      const ch = inner[i];
      if (ch === "(") {
        if (depth === 0) start = i + 1;
        depth += 1;
      } else if (ch === ")") {
        depth -= 1;
        if (depth === 0 && start >= 0) {
          groups.push(inner.slice(start, i));
          start = -1;
        }
      }
    }
    const lines = groups.map((g) =>
      g.split(",")
        .map((pair) => pair.trim().split(/\s+/).map(parseFloat))
        .filter((p) => p.length >= 2),
    ).filter((coords) => coords.length >= 2);
    return { kind: "multiline", lines };
  }
  const line = /^\s*LINESTRING\s*\(\s*(.+)\s*\)\s*$/i.exec(wkt);
  if (line) {
    const coords = line[1]
      .split(",")
      .map((pair) => pair.trim().split(/\s+/).map(parseFloat))
      .filter((p) => p.length >= 2);
    return { kind: "line", coords };
  }
  return null;
}

function featureCenter(geom) {
  if (!geom) return null;
  if (geom.kind === "point") return { lat: geom.lat, lon: geom.lon };
  if (geom.kind === "line") {
    // Midpoint of the line by vertex count (segments are typically short, so
    // this is close enough to a length-weighted midpoint for marker placement).
    const mid = geom.coords[Math.floor(geom.coords.length / 2)];
    return { lat: mid[1], lon: mid[0] };
  }
  return null;
}

// ----- D' aggregation (spec §4.5 corridor framing) -----
//
// Two separate aggregations run on the per-dest GapResult set:
//   - aggregateCorridorOverlay: union of all corridor polylines (one map layer)
//   - aggregateIntersections:   per-int_id aggregation, marker per intersection
//
// The corridor itself (combined_savings, per-road marginals, flips badge) is
// PER-DESTINATION advocacy data — it doesn't reduce sensibly across multiple
// destinations because each destination has its own combined hypothesis. The
// overview shows the union of streets (visually) and the drilldown shows the
// destination's specific corridor breakdown.

// Aggregate corridor overlay geometries into one GeoJSON FeatureCollection.
// Returns null if no destination has a corridor.
export function aggregateCorridorOverlay(perPairResults) {
  const features = [];
  for (const [, result] of perPairResults) {
    const overlay = result && result.corridor && result.corridor.fast_lts_overlay_wkt;
    if (!overlay) continue;
    const parsed = parseWktWgs84(overlay);
    if (!parsed) continue;
    if (parsed.kind === "multiline") {
      for (const coords of parsed.lines) {
        features.push({
          type: "Feature",
          geometry: { type: "LineString", coordinates: coords },
          properties: {},
        });
      }
    } else if (parsed.kind === "line") {
      features.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: parsed.coords },
        properties: {},
      });
    }
  }
  if (!features.length) return null;
  return { type: "FeatureCollection", features };
}

// Aggregate danger-intersections across destinations. Same shape as the
// pre-D' aggregation but only considers result.intersections (no segments —
// segments live in the corridor now).
export function aggregateIntersections(perPairResults) {
  const byId = new Map();

  for (const [destId, result] of perPairResults) {
    if (!result || !Array.isArray(result.intersections)) continue;
    for (const it of result.intersections) {
      const key = `intersection:${it.int_id}`;
      let entry = byId.get(key);
      if (!entry) {
        entry = {
          int_id: it.int_id,
          name: it.name || null,
          flips_to_fully_safe: !!it.flips_to_fully_safe,
          geometry: parseWktWgs84(it.geometry_wkt),
          on_hin: !!it.on_hin,
          affectedDestIds: new Set(),
          total_savings_meters: 0,
        };
        byId.set(key, entry);
      } else if (!entry.name && it.name) {
        entry.name = it.name;
      }
      if (it.flips_to_fully_safe) entry.flips_to_fully_safe = true;
      if (!entry.affectedDestIds.has(destId)) {
        entry.affectedDestIds.add(destId);
        entry.total_savings_meters += Number(it.savings_m) || 0;
      }
    }
  }

  const aggregated = Array.from(byId.values()).map((e) => {
    const routes_affected = e.affectedDestIds.size;
    const priority = routes_affected * Math.log(1 + e.total_savings_meters);
    return {
      kind: "intersection",
      id: e.int_id,
      name: e.name,
      flips_to_fully_safe: e.flips_to_fully_safe,
      geometry: e.geometry,
      on_hin: e.on_hin,
      routes_affected,
      total_savings_meters: e.total_savings_meters,
      priority,
    };
  });

  aggregated.sort((a, b) => b.priority - a.priority);

  // Bucket markers by rank — same scheme as pre-D' (kept consistent so the
  // existing renderAvoidedIntersections logic doesn't change).
  return aggregated.map((m, idx) => {
    let size = null;
    if (idx === 0) size = "high";
    else if (idx < 3) size = "mid";
    else if (m.routes_affected >= 2) size = "low";
    else size = null;
    return { ...m, rank: idx, marker_size: size };
  });
}

// Single MapLibre line layer for the corridor overlay (the polyline showing
// every fast-route LTS-above-tier segment, across all current destinations).
// Idempotent: passing null clears the layer.
const CORRIDOR_OVERLAY_SRC = "corridor-overlay-src";
const CORRIDOR_OVERLAY_LYR = "corridor-overlay-lyr";

export function renderCorridorOverlay(map, geojson) {
  if (!geojson) {
    if (map.getLayer(CORRIDOR_OVERLAY_LYR)) map.removeLayer(CORRIDOR_OVERLAY_LYR);
    if (map.getSource(CORRIDOR_OVERLAY_SRC)) map.removeSource(CORRIDOR_OVERLAY_SRC);
    return;
  }
  if (map.getSource(CORRIDOR_OVERLAY_SRC)) {
    map.getSource(CORRIDOR_OVERLAY_SRC).setData(geojson);
  } else {
    map.addSource(CORRIDOR_OVERLAY_SRC, { type: "geojson", data: geojson });
  }
  if (!map.getLayer(CORRIDOR_OVERLAY_LYR)) {
    // Thick translucent red — visually distinct from the route polylines (4px)
    // and from the HIN overlay on /explore (#c026d3 magenta). Drawn under the
    // route layers in z-order so the dashed/solid route colors still read on
    // top, with the corridor showing as a glow underneath.
    map.addLayer({
      id: CORRIDOR_OVERLAY_LYR,
      type: "line",
      source: CORRIDOR_OVERLAY_SRC,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#dc2626",
        "line-width": 10,
        "line-opacity": 0.35,
      },
    });
  }
}

// MapLibre markers keyed by aggregate key ("kind:id"). Diff-rendered.
const gapMarkers = new Map();

export function renderAvoidedIntersections(map, aggregated) {
  const keepKeys = new Set(
    aggregated
      .filter((a) => a.marker_size && a.geometry)
      .map((a) => `${a.kind}:${a.id}`),
  );
  for (const [key, marker] of gapMarkers) {
    if (!keepKeys.has(key)) {
      marker.remove();
      gapMarkers.delete(key);
    }
  }
  for (const a of aggregated) {
    if (!a.marker_size || !a.geometry) continue;
    const key = `${a.kind}:${a.id}`;
    const center = featureCenter(a.geometry);
    if (!center) continue;
    let marker = gapMarkers.get(key);
    if (!marker) {
      const el = document.createElement("div");
      // Set the className BEFORE addTo so maplibre's Marker stacks the
      // `maplibregl-marker` class on top of ours (rather than overwriting
      // it later — which is what `el.className = ...` would do). The
      // `maplibregl-marker` class carries `position: absolute` + the
      // transform origin that makes the marker track lng/lat on pan/zoom.
      el.className = `gap-marker size-${a.marker_size}`;
      marker = new maplibregl.Marker({ element: el }).setLngLat([center.lon, center.lat]).addTo(map);
      gapMarkers.set(key, marker);
    } else {
      marker.setLngLat([center.lon, center.lat]);
    }
    const el = marker.getElement();
    // Update className additively so we don't strip `maplibregl-marker`
    // when the rank changes (e.g., a high-priority marker demoted to
    // mid on a re-render). Drop any prior size-* token first.
    el.classList.remove("size-high", "size-mid", "size-low");
    el.classList.add("gap-marker", `size-${a.marker_size}`);
    // Prefer the resolved street name in the hover tooltip; fall back to
    // "kind id" if name is missing (older bikemap.db without street names,
    // or features whose OSM `name` tag is empty).
    const label = a.name || `${a.kind} ${a.id}`;
    const routesLabel = `${a.routes_affected} route${a.routes_affected > 1 ? "s" : ""}`;
    el.title = `${label} — affects ${routesLabel}, ~${Math.round(a.total_savings_meters)} m savings`;
    el.innerHTML = "";

    // Inner badge: count for multi-route mid, "!" for high.
    if (a.marker_size === "high") {
      const badge = document.createElement("span");
      badge.className = "gap-marker-badge";
      badge.textContent = a.routes_affected > 1 ? String(a.routes_affected) : "!";
      el.appendChild(badge);
    } else if (a.marker_size === "mid" && a.routes_affected > 1) {
      const badge = document.createElement("span");
      badge.className = "gap-marker-count";
      badge.textContent = String(a.routes_affected);
      el.appendChild(badge);
    }

    // Mockup-style inline "FIX THIS" label, attached to the high-priority
    // marker only (one per overview, to keep the visual hierarchy clear).
    // Shows the resolved street name so the advocacy ask is concrete:
    // "Foster & Western" instead of "Intersection #12345".
    if (a.marker_size === "high" && a.name) {
      const tag = document.createElement("div");
      tag.className = "gap-marker-fix-tag" + (a.flips_to_fully_safe ? " flips" : "");
      const heading = document.createElement("div");
      heading.className = "gmft-heading";
      heading.textContent = a.flips_to_fully_safe ? "FIX THIS" : "BIGGEST GAP";
      const street = document.createElement("div");
      street.className = "gmft-street";
      street.textContent = a.name;
      tag.appendChild(heading);
      tag.appendChild(street);
      el.appendChild(tag);
    }
  }
}

export function renderDestinations(map, dests) {
  const seen = new Set(dests.map((d) => d.id));
  for (const [id, marker] of destMarkers) {
    if (!seen.has(id)) {
      marker.remove();
      destMarkers.delete(id);
    }
  }
  for (const d of dests) {
    if (destMarkers.has(d.id)) continue;
    const el = document.createElement("div");
    el.className = `dest-marker category-${d.category || "custom"}`;
    el.dataset.destId = d.id;
    el.textContent = CATEGORY_ICONS[d.icon] || CATEGORY_ICONS[d.category] || CATEGORY_ICONS.custom;
    el.title = d.name || d.address || "";
    const marker = new maplibregl.Marker({ element: el })
      .setLngLat([d.lon, d.lat])
      .addTo(map);
    destMarkers.set(d.id, marker);
  }
}
