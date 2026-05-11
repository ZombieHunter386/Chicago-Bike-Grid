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

function lineStringFromPolyline(polyline) {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: polyline.map((p) => [p.lon, p.lat]),
    },
    properties: {},
  };
}

function ensureRouteLayer(map, destId, kind, route) {
  const srcId = routeSourceId(destId, kind);
  const lyrId = routeLayerId(destId, kind);
  const geojson = lineStringFromPolyline(route.polyline);

  if (map.getSource(srcId)) {
    map.getSource(srcId).setData(geojson);
  } else {
    map.addSource(srcId, { type: "geojson", data: geojson });
  }

  // Paint properties differ by kind + fallback state.
  let paint;
  if (kind === "fast") {
    paint = {
      "line-color": "#f97316",      // var(--c-fast) — orange
      "line-width": 4,
      "line-dasharray": [2, 2],
    };
  } else if (route.is_fallback) {
    paint = {
      "line-color": "#f59e0b",      // var(--c-fallback) — amber
      "line-width": 4,
      "line-dasharray": [3, 3],
    };
  } else {
    paint = {
      "line-color": "#16a34a",      // var(--c-safe) — solid green
      "line-width": 4,
    };
  }

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

// Re-render all (home, dest) route pairs at the current tier.
// `fetchRoutes` is injected so this module stays free of api.js dependency
// during testing (and lets callers pass a stub for the fallback codepath).
export async function renderRoutes(map, home, dests, tier, fetchRoutes) {
  if (!home) {
    for (const lyrId of [...routeLayers]) {
      const [, destId, kind] = /^route-(.+)-(fast|safe)-lyr$/.exec(lyrId);
      removeRouteLayer(map, destId, kind);
    }
    return new Map();
  }

  // Tear down layers for destinations that no longer exist.
  const keepDestIds = new Set(dests.map((d) => d.id));
  for (const lyrId of [...routeLayers]) {
    const m = /^route-(.+)-(fast|safe)-lyr$/.exec(lyrId);
    if (m && !keepDestIds.has(m[1])) removeRouteLayer(map, m[1], m[2]);
  }

  const results = new Map();
  const fetches = dests.map(async (d) => {
    try {
      const r = await fetchRoutes(
        { lat: home.lat, lon: home.lon },
        { lat: d.lat, lon: d.lon },
        tier,
      );
      results.set(d.id, r);
      if (r.fast) ensureRouteLayer(map, d.id, "fast", r.fast);
      else removeRouteLayer(map, d.id, "fast");
      if (r.safe) ensureRouteLayer(map, d.id, "safe", r.safe);
      else removeRouteLayer(map, d.id, "safe");
      // Mark the dest marker as fallback if either route is best-effort.
      const isFallback = !!(r.safe && r.safe.is_fallback);
      const markerEl = destMarkers.get(d.id) && destMarkers.get(d.id).getElement();
      if (markerEl) markerEl.classList.toggle("fallback", isFallback);
    } catch (err) {
      console.warn(`route fetch failed for ${d.id}`, err);
      removeRouteLayer(map, d.id, "fast");
      removeRouteLayer(map, d.id, "safe");
    }
  });
  await Promise.all(fetches);
  return results;
}

// ----- Gap aggregation + avoided-intersection markers (Task 8, spec §4.6) ----

// Parse WGS84 WKT into either {kind: "point", lat, lon} or
// {kind: "line", coords: [[lon, lat], ...]}. Backend emits POINT for
// intersections and LINESTRING for segments — no other geometries.
export function parseWktWgs84(wkt) {
  if (!wkt) return null;
  const point = /^\s*POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)\s*$/i.exec(wkt);
  if (point) {
    return { kind: "point", lon: parseFloat(point[1]), lat: parseFloat(point[2]) };
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

// Per spec §4.6: aggregate gap candidates across all home→dest pairs.
// Each pair's gap result contributes its headline + supporting candidates.
// Output is sorted by descending priority; the caller bucketizes for marker
// styling (top-1 = high, top-2/3 = mid, rest = low).
export function aggregateGaps(perPairResults) {
  // perPairResults: Map<destId, gapResult>
  const byKey = new Map(); // key="kind:id" -> {kind, id, geometry, routes, savings, on_hin}

  for (const [destId, result] of perPairResults) {
    if (!result) continue;
    const candidates = [];
    if (result.headline) candidates.push(result.headline);
    if (Array.isArray(result.supporting)) candidates.push(...result.supporting);

    for (const c of candidates) {
      const key = `${c.feature_kind}:${c.feature_id}`;
      let entry = byKey.get(key);
      if (!entry) {
        entry = {
          kind: c.feature_kind,
          id: c.feature_id,
          geometry: parseWktWgs84(c.geometry_wkt),
          on_hin: !!c.on_hin,
          affectedDestIds: new Set(),
          total_savings_meters: 0,
        };
        byKey.set(key, entry);
      }
      if (!entry.affectedDestIds.has(destId)) {
        entry.affectedDestIds.add(destId);
        entry.total_savings_meters += Number(c.savings_m) || 0;
      }
    }
  }

  const aggregated = Array.from(byKey.values()).map((e) => {
    const routes_affected = e.affectedDestIds.size;
    const priority = routes_affected * Math.log(1 + e.total_savings_meters);
    return {
      kind: e.kind,
      id: e.id,
      geometry: e.geometry,
      on_hin: e.on_hin,
      routes_affected,
      total_savings_meters: e.total_savings_meters,
      priority,
    };
  });

  aggregated.sort((a, b) => b.priority - a.priority);

  // Bucket markers by rank. Spec §4.6: hide single-route low-savings items
  // from overview; they live in drill-down.
  return aggregated.map((m, idx) => {
    let size = null;
    if (idx === 0) size = "high";
    else if (idx < 3) size = "mid";
    else if (m.routes_affected >= 2) size = "low";
    else size = null; // single-route, low priority — overview-hidden
    return { ...m, rank: idx, marker_size: size };
  });
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
      marker = new maplibregl.Marker({ element: el }).setLngLat([center.lon, center.lat]).addTo(map);
      gapMarkers.set(key, marker);
    } else {
      marker.setLngLat([center.lon, center.lat]);
    }
    const el = marker.getElement();
    el.className = `gap-marker size-${a.marker_size}`;
    el.title = `${a.kind} ${a.id} — affects ${a.routes_affected} route${a.routes_affected > 1 ? "s" : ""}, ` +
      `~${Math.round(a.total_savings_meters)} m savings`;
    el.innerHTML = "";
    if (a.marker_size === "high") {
      const badge = document.createElement("span");
      badge.className = "gap-marker-badge";
      badge.textContent = "!";
      el.appendChild(badge);
    } else if (a.marker_size === "mid" && a.routes_affected > 1) {
      const badge = document.createElement("span");
      badge.className = "gap-marker-count";
      badge.textContent = String(a.routes_affected);
      el.appendChild(badge);
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
