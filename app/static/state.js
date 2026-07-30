// Frontend state + URL-hash codec (spec §2.3).
//
// State shape:
//   { home: {lat, lon, displayName, approximate} | null,
//     destinations: [{id, lat, lon, name, address, category, icon, approximate}],
//     tier: "kid" | "inexperienced" | "experienced" | "death_wish",
//     drilledPair: {destId, kind: "fast"|"safe"} | null }
//
// Permalink: JSON.stringify -> LZString.compressToEncodedURIComponent ->
// location.hash. Reverse to parse. The compact JSON uses short keys + array
// form so 5 destinations stay under spec §2.3's 250-char target.

const DEFAULT_TIER = "death_wish";
const VALID_TIERS = new Set(["kid", "inexperienced", "experienced", "death_wish"]);
// Permalinks minted before the 4-level LTS migration (2026-07-29) carry the
// old 3-persona keys. Map them forward so shared URLs keep working: "parent"
// (LTS 1-2) and "any" (all levels) are the direct equivalents.
const LEGACY_TIERS = { parent: "inexperienced", any: "death_wish" };

function normalizeTier(t) {
  if (VALID_TIERS.has(t)) return t;
  return LEGACY_TIERS[t] || DEFAULT_TIER;
}

const DEFAULT_STATE = { home: null, destinations: [], tier: DEFAULT_TIER, drilledPair: null };

let state = structuredClone(DEFAULT_STATE);
const subscribers = [];

export function getState() {
  return state;
}

export function subscribe(fn) {
  subscribers.push(fn);
  return () => subscribers.splice(subscribers.indexOf(fn), 1);
}

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
    h: s.home
      ? [s.home.lat, s.home.lon, s.home.displayName, s.home.approximate ? 1 : 0]
      : null,
    d: s.destinations.map((d) => [
      d.id, d.lat, d.lon, d.name, d.address, d.category, d.icon,
      d.approximate ? 1 : 0,
    ]),
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
    home: compact.h
      ? { lat: compact.h[0], lon: compact.h[1], displayName: compact.h[2], approximate: !!compact.h[3] }
      : null,
    destinations: (compact.d || []).map((d) => ({
      id: d[0], lat: d[1], lon: d[2], name: d[3], address: d[4],
      category: d[5], icon: d[6], approximate: !!d[7],
    })),
    tier: normalizeTier(compact.t),
    drilledPair: compact.p ? { destId: compact.p[0], kind: compact.p[1] } : null,
  };
}

function syncToHash() {
  const encoded = encodeStateToHash(state);
  history.replaceState(null, "", encoded ? `#${encoded}` : "#");
}

export function loadFromHash() {
  const hash = window.location.hash.replace(/^#/, "");
  if (hash) {
    state = { ...DEFAULT_STATE, ...decodeHashToState(hash) };
    for (const fn of subscribers) fn(state);
  }
}
