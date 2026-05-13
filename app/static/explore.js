// Entrypoint for the LTS Data Explorer at /explore.
// Plan 2D Tasks 4-5: map init + fetch + layer rendering + legend.

const CHICAGO_CENTER = [-87.63, 41.88];
const DEFAULT_ZOOM = 11;
const STREETS_STYLE = "https://tiles.openfreemap.org/styles/liberty";

// Esri imagery raster + OFM symbol labels (street names + place names) make
// up the satellite-with-labels hybrid. We don't use setStyle to swap to a
// pure-raster satellite style anymore — that wipes every custom layer we
// added (LTS streets, intersections, HIN) and the post-toggle handler then
// has to re-add them. Mirror /index's approach: add the imagery as one more
// layer at the bottom of the OFM style, flip visibility on toggle, hide the
// OFM `fill` and `line` layers in satellite mode so imagery shows through,
// keep `symbol` layers visible so street names render in both modes.
const ESRI_IMAGERY_SRC = "esri-imagery-src";
const ESRI_IMAGERY_LYR = "esri-imagery-lyr";
const ESRI_IMAGERY_TILES = [
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
];

const map = new maplibregl.Map({
  container: document.getElementById("map"),
  style: STREETS_STYLE,
  center: CHICAGO_CENTER,
  zoom: DEFAULT_ZOOM,
});
window.__map = map;

function ensureSatelliteLayer() {
  if (map.getSource(ESRI_IMAGERY_SRC)) return;
  map.addSource(ESRI_IMAGERY_SRC, {
    type: "raster",
    tiles: ESRI_IMAGERY_TILES,
    tileSize: 256,
    attribution:
      "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, " +
      "GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
  });
  const layers = map.getStyle().layers || [];
  const firstNonBackground = layers.find((l) => l.id !== "background");
  map.addLayer(
    {
      id: ESRI_IMAGERY_LYR,
      type: "raster",
      source: ESRI_IMAGERY_SRC,
      layout: { visibility: "none" },
    },
    firstNonBackground ? firstNonBackground.id : undefined,
  );
}

function isOfmBasemapToHide(layer) {
  if (!layer) return false;
  if (layer.id === "background") return true;
  if (layer.id === ESRI_IMAGERY_LYR) return false;
  // Skip our custom layers — LTS streets, intersections, HIN.
  if (layer.id === "streets-layer" || layer.id === "intersections-layer" || layer.id === "hin-layer") return false;
  return layer.type === "fill" || layer.type === "line";
}

let basemap = "streets";
const toggleBtn = document.getElementById("basemap-toggle");
toggleBtn.addEventListener("click", () => {
  basemap = basemap === "streets" ? "satellite" : "streets";
  toggleBtn.textContent = basemap === "streets" ? "Satellite" : "Streets";
  const showImagery = basemap === "satellite";
  ensureSatelliteLayer();
  map.setLayoutProperty(ESRI_IMAGERY_LYR, "visibility", showImagery ? "visible" : "none");
  for (const layer of map.getStyle().layers || []) {
    if (!isOfmBasemapToHide(layer)) continue;
    map.setLayoutProperty(layer.id, "visibility", showImagery ? "none" : "visible");
  }
});

// `ensureSatelliteLayer` is called lazily inside the toggle handler — not
// on map load — so streets-mode users never trigger an Esri raster source
// load. Eager init added latency to the initial style processing even when
// the imagery layer was hidden.

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
      // Distinct from LTS-3 (#dc2626) so HIN streets are visible as a
      // separate signal — a street can be on the HIN OR LTS-3 OR both,
      // and using the same red for both blended them into one indistinct
      // line. Magenta + line-opacity 0.7 lets the underlying LTS color
      // show through, so the user sees both axes at once.
      paint: {
        "line-color": "#c026d3",
        "line-width": 5,
        "line-opacity": 0.7,
      },
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
    applyLtsVisibility(ltsCheckbox.checked);
    document.getElementById("legend").hidden = false;
    document.getElementById("hin-toggle").hidden = false;
    document.getElementById("intersections-toggle").hidden = false;
    document.getElementById("lts-toggle").hidden = false;
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

// LTS-network-layer toggle. Defaults to "on" — the LTS streets are the
// main /explore artifact. Lets the user untick to see ONLY the HIN
// highlights without the LTS color noise.
const ltsCheckbox = document.getElementById("lts-checkbox");

function applyLtsVisibility(checked) {
  if (!map.getLayer("streets-layer")) return;
  map.setLayoutProperty("streets-layer", "visibility", checked ? "visible" : "none");
}

ltsCheckbox.addEventListener("change", () => {
  applyLtsVisibility(ltsCheckbox.checked);
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
