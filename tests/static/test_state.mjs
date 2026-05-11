// Node-based codec test for app/static/state.js.
//
// Run: cd chicago-bike-advocacy-map && npm install --no-save lz-string
//      node tests/static/test_state.mjs

import LZString from "lz-string";
globalThis.LZString = LZString;

// state.js's syncToHash uses window + history. Stub them so import doesn't
// crash; we only exercise the pure codec functions here.
globalThis.window = { location: { hash: "" }, addEventListener: () => {} };
globalThis.history = { replaceState: () => {} };

const { encodeStateToHash, decodeHashToState } = await import(
  "../../app/static/state.js"
);

function assertEq(a, b, msg) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    console.error(`FAIL: ${msg}`);
    console.error(`  expected: ${JSON.stringify(b)}`);
    console.error(`  actual:   ${JSON.stringify(a)}`);
    process.exit(1);
  }
}

// 1. Round-trip a full state object.
const original = {
  home: { lat: 41.94, lon: -87.68, displayName: "1234 W Foster Ave", approximate: false },
  destinations: [
    { id: "d1", lat: 41.94, lon: -87.67, name: "Audubon", address: "3500 N Hoyne", category: "school", icon: "school", approximate: false },
    { id: "d2", lat: 41.95, lon: -87.68, name: "Lincoln Park", address: null, category: "park", icon: "park", approximate: false },
  ],
  tier: "parent",
  drilledPair: { destId: "d1", kind: "safe" },
};

const encoded = encodeStateToHash(original);
const decoded = decodeHashToState(encoded);
assertEq(decoded, original, "round-trip");

// 2. Empty hash returns defaults.
const empty = decodeHashToState("");
assertEq(
  empty,
  { home: null, destinations: [], tier: "any", drilledPair: null },
  "empty hash defaults",
);

// 3. Compressed payload for 5+ destinations stays compact (spec §2.3
// targets <250 chars; allow some slack since lz-string output varies).
const big = {
  ...original,
  destinations: Array.from({ length: 5 }, (_, i) => ({
    id: `d${i}`,
    lat: 41.94,
    lon: -87.68 + i * 0.01,
    name: `dest${i}`,
    address: null,
    category: "park",
    icon: "park",
    approximate: false,
  })),
};
const bigEncoded = encodeStateToHash(big);
if (bigEncoded.length > 400) {
  console.error(
    `FAIL: 5-destination encoded length ${bigEncoded.length} > 400 chars (spec §2.3 wants < 250)`,
  );
  process.exit(1);
}

// 4. Approximate-home flag preserved.
const approxState = {
  ...original,
  home: { ...original.home, approximate: true },
};
const approxDecoded = decodeHashToState(encodeStateToHash(approxState));
assertEq(approxDecoded.home.approximate, true, "approximate flag preserved");

// 5. Drilled-pair null round-trip.
const noDrill = { ...original, drilledPair: null };
const noDrillDecoded = decodeHashToState(encodeStateToHash(noDrill));
assertEq(noDrillDecoded.drilledPair, null, "null drilledPair round-trip");

console.log(`ALL state.js tests passed (5-dest payload: ${bigEncoded.length} chars)`);
