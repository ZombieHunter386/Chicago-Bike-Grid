// Entrypoint for the LTS Data Explorer at /explore.
// Plan 2D Tasks 4-5: map init + fetch + layer rendering + legend.

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

const map = new maplibregl.Map({
  container: document.getElementById("map"),
  style: STREETS_STYLE,
  center: CHICAGO_CENTER,
  zoom: DEFAULT_ZOOM,
});
window.__map = map;

let basemap = "streets";
const toggleBtn = document.getElementById("basemap-toggle");
toggleBtn.addEventListener("click", () => {
  basemap = basemap === "streets" ? "satellite" : "streets";
  map.setStyle(basemap === "satellite" ? SATELLITE_STYLE : STREETS_STYLE);
  toggleBtn.textContent = basemap === "streets" ? "Satellite" : "Streets";
  // setStyle wipes all sources/layers we added (LTS streets, intersections,
  // HIN). Re-add them once the new style is loaded. We listen to `data`
  // events with `dataType === "style"` rather than `style.load` — the
  // latter does NOT fire on subsequent setStyle calls in this maplibre
  // build (confirmed via event-trace; only `data:style`, `styledata:style`,
  // and `sourcedata:source` fire). One-shot listener: registers, fires
  // exactly once when the style data lands, removes itself.
  const onceStyleLoaded = (fn) => {
    const listener = (e) => {
      if (e.dataType !== "style") return;
      map.off("data", listener);
      fn();
    };
    map.on("data", listener);
  };
  onceStyleLoaded(() => {
    addLayers();
    applyInitialHin();
    applyIntersectionVisibility(intersectionsCheckbox.checked);
  });
});

// Module-scope cache so basemap toggle can re-add layers without re-fetching.
let streetsFC = null;
let intersectionsFC = null;
let hinFC = null;

const LTS_COLOR_EXPR = [
  "match",
  ["get", "lts"],
  1, "#16a34a",
  2, "#f59e0b",
  3, "#dc2626",
  "#999999",
];
const LTS_APPROACH_COLOR_EXPR = [
  "match",
  ["get", "lts_approach"],
  1, "#16a34a",
  2, "#f59e0b",
  3, "#dc2626",
  "#999999",
];

function addLayers() {
  if (!streetsFC || !intersectionsFC || !hinFC) return;

  if (!map.getSource("hin-source")) {
    map.addSource("hin-source", { type: "geojson", data: hinFC });
    map.addLayer({
      id: "hin-layer",
      type: "line",
      source: "hin-source",
      layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#dc2626", "line-width": 4 },
    });
  }

  if (!map.getSource("streets-source")) {
    map.addSource("streets-source", { type: "geojson", data: streetsFC });
    map.addLayer({
      id: "streets-layer",
      type: "line",
      source: "streets-source",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": LTS_COLOR_EXPR, "line-width": 2 },
    });
  }

  if (!map.getSource("intersections-source")) {
    map.addSource("intersections-source", { type: "geojson", data: intersectionsFC });
    map.addLayer({
      id: "intersections-layer",
      type: "circle",
      source: "intersections-source",
      paint: {
        "circle-color": LTS_APPROACH_COLOR_EXPR,
        "circle-radius": [
          "interpolate", ["linear"], ["zoom"],
          11, 2,
          14, 5,
        ],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.5,
      },
    });
  }
}

async function loadNetwork() {
  const resp = await fetch("/lts-network");
  if (!resp.ok) {
    const err = new Error(`HTTP ${resp.status}`);
    err.status = resp.status;
    throw err;
  }
  const fc = await resp.json();
  streetsFC = {
    type: "FeatureCollection",
    features: fc.features.filter((f) => f.geometry.type === "LineString"),
  };
  intersectionsFC = {
    type: "FeatureCollection",
    features: fc.features.filter((f) => f.geometry.type === "Point"),
  };
  hinFC = {
    type: "FeatureCollection",
    features: streetsFC.features.filter((f) => f.properties.on_hin === true),
  };
}

const errorCard = document.getElementById("explore-error");
const errorRetry = errorCard.querySelector(".ee-retry");
const errorText = errorCard.querySelector(".ee-text");

function showError(msg) {
  errorCard.hidden = false;
  if (msg) errorText.textContent = msg;
}
function hideError() {
  errorCard.hidden = true;
}

async function init() {
  hideError();
  toggleBtn.disabled = true;
  toggleBtn.textContent = "Loading data…";
  try {
    // Use isStyleLoaded() + style.load instead of loaded() + load — the
    // latter never resolves on this machine because openfreemap tile
    // requests continuously flap loaded() back to false, AND the load
    // event has already fired by the time we attach a listener here.
    // isStyleLoaded()/style.load is the same pattern the main app uses
    // in app.js (renderRoutes); it actually fires once and stays stable.
    const styleReady = map.isStyleLoaded()
      ? Promise.resolve()
      : new Promise((r) => map.once("style.load", r));
    await Promise.all([loadNetwork(), styleReady]);
    addLayers();
    applyInitialHin();
    applyIntersectionVisibility(intersectionsCheckbox.checked);
    document.getElementById("legend").hidden = false;
    document.getElementById("hin-toggle").hidden = false;
    document.getElementById("intersections-toggle").hidden = false;
    toggleBtn.disabled = false;
    toggleBtn.textContent = "Satellite";
  } catch (err) {
    console.error("LTS network load failed", err);
    // 404 = the prep pipeline hasn't written data/lts-network.geojson.gz yet
    // (dev) OR the upload-db flow skipped it (prod, see Plan 2C dep note).
    // Network errors, JSON parse failures, and addLayer throws all fall
    // through to the generic message — see spec §6.4.
    const msg = err && err.status === 404
      ? "LTS network data isn't available yet. Try again shortly."
      : "Couldn't load the LTS network.";
    showError(msg);
  }
}

errorRetry.addEventListener("click", () => init());
init();

// (Basemap swap re-add is wired inside the toggleBtn click handler via
//  map.once("style.load", ...) — see comment there for the reason a
//  permanent map.on(...) listener didn't work.)

// Intersection-layer toggle. Defaults to "on" because the layer was always
// visible before this control existed; users who want to inspect only the
// street stress without the intersection clutter can untick it.
const intersectionsCheckbox = document.getElementById("intersections-checkbox");

function applyIntersectionVisibility(checked) {
  if (!map.getLayer("intersections-layer")) return;
  map.setLayoutProperty("intersections-layer", "visibility", checked ? "visible" : "none");
}

intersectionsCheckbox.addEventListener("change", () => {
  applyIntersectionVisibility(intersectionsCheckbox.checked);
});

// HIN overlay toggle + URL permalink (?hin=1).
const hinCheckbox = document.getElementById("hin-checkbox");

function applyHinVisibility(checked) {
  if (!map.getLayer("hin-layer")) return;
  map.setLayoutProperty("hin-layer", "visibility", checked ? "visible" : "none");
}

function syncHinUrl(checked) {
  const path = window.location.pathname;
  history.replaceState(null, "", checked ? `${path}?hin=1` : path);
}

hinCheckbox.addEventListener("change", () => {
  applyHinVisibility(hinCheckbox.checked);
  syncHinUrl(hinCheckbox.checked);
});

// Honor ?hin=1 from the URL. Called from init() after addLayers() (so the
// hin-layer exists) and from the style.load handler after basemap swaps.
function applyInitialHin() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("hin") === "1") {
    hinCheckbox.checked = true;
    applyHinVisibility(true);
  }
}
