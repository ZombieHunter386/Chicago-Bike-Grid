"""Gap analysis algorithm (spec §4.5 — corridor framing).

The unit of analysis is the *fast-route corridor*: the set of LTS-above-tier
segments + intersections on the fastest path. Rather than scoring each block
in isolation (which goes silent on tight detour zones where no single fix
flips the route, see Bug 2 / Sheffield-Halsted-Waveland Lakeview case), we
hypothesize all of them upgraded together and report:

  - `combined_savings_m`  — meters saved if every named street in the
                            corridor is brought on-tier
  - `flips_to_fully_safe` — was the safe route fallback? Does the full
                            upgrade flip it to a fully on-tier route?
  - per-road `marginal_loss_m` — if you drop this street from the upgrade
                            set, how much of the combined savings do you
                            lose? This is what advocates use to prioritize
                            the ask when not every street can be fixed.

Intersections are surfaced as a separate "danger intersections" group,
each scored individually under the existing per-vertex hypothesis. They
aren't grouped because intersections are point features without a corridor
analog — a single dangerous intersection IS the ask.

Threshold (Hunter, 2026-05-12): the corridor is surfaced iff
`flips_to_fully_safe OR combined_savings_m > 50`. Below the threshold for
non-flipping cases, the advocacy story is too thin to act on — better to
return None than a marker with negligible savings.

Memory rule: never copy the graph. Per hypothesis, build a fresh weights
list (length = ecount); only entries for affected edges change from the
precomputed base_weights. Pass to igraph.get_shortest_paths().
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point

from app.core.graph import GraphSnapshot, edges_for_road_id, vertex_for_int_id
from app.core.routing import Route, compute_fast_route, compute_safe_route
from app.core.weights import INF_WEIGHT, TIERS

# Surface the corridor when total impact warrants action (50m floor) OR when
# fixing it flips a fallback safe route to a fully on-tier one (any flip is
# worth surfacing regardless of metric savings).
CORRIDOR_SAVINGS_FLOOR_M = 50.0

# Per-road marginals are computed in parallel — each is an independent Dijkstra
# on a fresh weights array. igraph.get_shortest_paths releases the GIL during
# the C call so threads give real wall-clock speedup. Pool size matched to
# typical worker thread count; bumping higher costs more memory (each thread
# materializes its own weights copy) without much speedup on commodity CPUs.
_MARGINAL_POOL_WORKERS = 4
_marginal_pool = ThreadPoolExecutor(max_workers=_MARGINAL_POOL_WORKERS)

# tier_max_lts = highest LTS the user *prefers* — corridor enumeration finds
# fast-route segments/intersections with lts > tier_max_lts.
# 'any' is set to 2 (not 3) so LTS-3 segments still surface as corridor
# members; previously 3 made the filter `lts > 3` always-false, returning
# zero candidates regardless of input and silently breaking §6.4 #5.
_TIER_MAX_LTS = {"kid": 1, "parent": 2, "any": 2}

_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class CorridorRoad:
    """One named street in the corridor. `road_ids` are the OSM road_ids
    on the fast route that share this street's `name`. `marginal_loss_m`
    is how much of the corridor's combined_savings is attributable to
    THIS street being upgraded (computed by dropping its road_ids from
    the combined hypothesis set and re-running)."""
    name: str | None
    road_ids: tuple[int, ...]
    block_count: int
    on_hin: bool
    geometry_wkt: str           # MultiLineString WGS84
    savings_without_m: float    # corridor savings if this street is dropped
    marginal_loss_m: float      # combined_savings_m - savings_without_m


@dataclass(frozen=True)
class GapCorridor:
    """The corridor-level advocacy ask. Roads are sorted by marginal_loss
    descending — load-bearing streets first. fast_lts_overlay_wkt is the
    geometry the frontend renders as the polyline overlay on the map."""
    combined_savings_m: float
    flips_to_fully_safe: bool
    fast_lts_overlay_wkt: str   # MultiLineString WGS84, all upgraded segments
    roads: tuple[CorridorRoad, ...]


@dataclass(frozen=True)
class GapIntersection:
    """A single dangerous intersection on the fast route. Scored
    independently under the per-vertex hypothesis (existing logic)."""
    int_id: int
    name: str | None
    current_lts_approach: int
    savings_m: float
    on_hin: bool
    flips_to_fully_safe: bool
    geometry_wkt: str           # Point WGS84


@dataclass(frozen=True)
class GapResult:
    fast_route: Route | None
    safe_route: Route | None
    safe_route_is_fallback: bool
    corridor: GapCorridor | None
    intersections: tuple[GapIntersection, ...] = field(default_factory=tuple)


# ===========================================================================
# Geometry + naming helpers
# ===========================================================================

def _segment_wgs84_line(snap: GraphSnapshot, road_id: int) -> LineString | None:
    """Reconstruct a road segment's WGS84 LineString (head→tail of its
    representative edge). Returns None if the road's endpoints can't be
    resolved (shouldn't happen on well-formed snapshots)."""
    edge_pair = edges_for_road_id(snap, road_id)
    if edge_pair is None:
        return None
    # Use the first directed edge as representative; both directions share
    # the same underlying segment geometry.
    head_v = vertex_for_int_id(snap, int(snap.road_head_int_id_array[edge_pair[0] // 2]))
    tail_v = vertex_for_int_id(snap, int(snap.road_tail_int_id_array[edge_pair[0] // 2]))
    if head_v is None or tail_v is None:
        return None
    hlat = float(snap.vertex_coords_wgs84[head_v][0])
    hlon = float(snap.vertex_coords_wgs84[head_v][1])
    tlat = float(snap.vertex_coords_wgs84[tail_v][0])
    tlon = float(snap.vertex_coords_wgs84[tail_v][1])
    return LineString([(hlon, hlat), (tlon, tlat)])


def _multilinestring_wkt(lines: list[LineString]) -> str:
    if not lines:
        return MultiLineString().wkt
    if len(lines) == 1:
        # MultiLineString of one line is fine for consistent frontend parsing.
        return MultiLineString([lines[0]]).wkt
    return MultiLineString(lines).wkt


def _resolve_segment_name(snap: GraphSnapshot, road_id: int) -> str | None:
    """Look up snap.road_name_list[i] for the road_id. Returns None if the
    OSM `name` tag was empty or the road_id isn't found."""
    edge_pair = edges_for_road_id(snap, road_id)
    if edge_pair is None:
        return None
    load_pos = edge_pair[0] // 2
    if 0 <= load_pos < len(snap.road_name_list):
        return snap.road_name_list[load_pos]
    return None


def _resolve_intersection_name(snap: GraphSnapshot, int_id: int) -> str | None:
    """Name an intersection by its adjacent streets, e.g., 'Foster & Western'.
    Returns up to 2 distinct adjacent street names joined by ' & '."""
    head_matches = np.where(snap.road_head_int_id_array == int_id)[0]
    tail_matches = np.where(snap.road_tail_int_id_array == int_id)[0]
    seen: list[str] = []
    for idx in list(head_matches) + list(tail_matches):
        nm = snap.road_name_list[int(idx)] if int(idx) < len(snap.road_name_list) else None
        if nm and nm not in seen:
            seen.append(nm)
        if len(seen) >= 2:
            break
    if not seen:
        return None
    return " & ".join(seen)


# ===========================================================================
# Combined hypothesis (spec §4.5 corridor algorithm)
# ===========================================================================

def _apply_combined_upgrades(
    snap: GraphSnapshot, weights: np.ndarray,
    road_ids: list[int] | tuple[int, ...],
    int_ids: list[int] | tuple[int, ...],
    weight_table: list[float], tier_max_lts: int,
) -> None:
    """Joint segment + intersection upgrade. Computes each affected edge's
    new weight knowing BOTH its segment LTS and its head-intersection LTS
    may be in the upgrade set — otherwise the naive sequential approach
    (segment-pass then intersection-pass overwriting each other) keeps an
    edge at INF whenever either component still reads its un-upgraded
    raw value via the max-rule.

    `weight_table` is the LTS-indexed weight vector for the tier — pass
    TIERS[tier]['main'] for the main-weights pass or TIERS[tier]['fallback']
    for the fallback pass. `tier_max_lts` is the effective LTS used in
    place of raw segment_lts/head_lts when that component is upgraded.

    Affected edges = (both directions of every road_id in `road_ids`) UNION
    (every incoming edge of every int_id in `int_ids`). For each, the new
    effective LTS is `max(seg_eff, head_eff)`, where each component is
    `tier_max_lts` if upgraded else its raw value.
    """
    # Track edges affected by a SEGMENT upgrade vs a HEAD-INTERSECTION upgrade
    # separately, so we can OR them together at write time. Edges affected by
    # both — the joint case the original bug missed — set seg_eff AND head_eff
    # to tier_max_lts at once.
    #
    # We avoid touching `snap.g.es[eid].target` from this routine because it's
    # called from inside a ThreadPoolExecutor. Concurrent access to igraph's
    # EdgeSeq Python wrapper from multiple worker threads has been observed to
    # hang indefinitely under load (Bug 2 follow-up, 2026-05-12). Instead we
    # derive the "is head intersection upgraded?" flag from membership in the
    # set of edges marked by g.incident(v, mode="in") for each upgraded
    # intersection — igraph's `incident` IS safe under our use pattern.
    road_set = {int(r) for r in road_ids}
    seg_up_eids: set[int] = set()
    for rid in road_set:
        edge_pair = edges_for_road_id(snap, rid)
        if edge_pair is None:
            continue
        seg_up_eids.update(int(e) for e in edge_pair)
    head_up_eids: set[int] = set()
    for iid in int_ids:
        v = vertex_for_int_id(snap, iid)
        if v is None:
            continue
        head_up_eids.update(int(e) for e in snap.g.incident(v, mode="in"))

    for eid in seg_up_eids | head_up_eids:
        seg_eff = tier_max_lts if eid in seg_up_eids else int(snap.edge_seg_lts[eid])
        head_eff = tier_max_lts if eid in head_up_eids else int(snap.edge_head_lts[eid])
        new_eff = max(seg_eff, head_eff)
        weights[eid] = float(snap.edge_length_m[eid]) * weight_table[new_eff - 1]


def _path_length_in_tier(
    snap: GraphSnapshot, src: int, dst: int, weights: np.ndarray,
) -> float | None:
    """Dijkstra under the given weights. Returns metres if the path crosses
    only finite-weighted (in-tier) edges; None if no path or path crosses
    INF_WEIGHT. Used to detect "is this hypothesis a fully on-tier route?"."""
    paths = snap.g.get_shortest_paths(src, to=dst, weights=weights, output="epath")
    if not paths or not paths[0]:
        return None
    epath = paths[0]
    if any(weights[e] >= INF_WEIGHT for e in epath):
        return None
    return float(sum(snap.edge_length_m[e] for e in epath))


def _path_length_unrestricted(
    snap: GraphSnapshot, src: int, dst: int, weights: np.ndarray,
) -> float | None:
    """Dijkstra under given weights, accepting INF-weighted edges in the
    result. Used for the fallback pass — the caller supplies a fallback
    weight table that by construction has no INF entries, so the result
    is always feasible if any path exists."""
    paths = snap.g.get_shortest_paths(src, to=dst, weights=weights, output="epath")
    if not paths or not paths[0]:
        return None
    return float(sum(snap.edge_length_m[e] for e in paths[0]))


def _hypothesized_safe_length(
    snap: GraphSnapshot, src: int, dst: int, tier: str, tier_max_lts: int,
    upgrade_road_ids: list[int] | tuple[int, ...],
    upgrade_int_ids: list[int] | tuple[int, ...],
    safe_was_fallback: bool,
) -> tuple[float | None, bool]:
    """Compute hypothesized safe-route length under the combined upgrade
    set. Returns (length_m, is_in_tier).

      - If a main-weights path exists with no INF edges → (length, True).
      - Otherwise if safe was fallback, retry under fallback weights →
        (length, False).
      - Otherwise (no path even in fallback) → (None, False).
    """
    main_table = TIERS[tier]["main"]
    base_main = snap.base_weights_by_tier[tier]
    new_main = base_main.copy()
    _apply_combined_upgrades(
        snap, new_main, upgrade_road_ids, upgrade_int_ids, main_table, tier_max_lts,
    )
    in_tier_len = _path_length_in_tier(snap, src, dst, new_main)
    if in_tier_len is not None:
        return in_tier_len, True
    if not safe_was_fallback:
        return None, False
    fb_table = TIERS[tier]["fallback"]
    base_fb = snap.fallback_weights_by_tier[tier]
    new_fb = base_fb.copy()
    _apply_combined_upgrades(
        snap, new_fb, upgrade_road_ids, upgrade_int_ids, fb_table, tier_max_lts,
    )
    fb_len = _path_length_unrestricted(snap, src, dst, new_fb)
    return fb_len, False


# ===========================================================================
# Intersection scoring (one hypothesis per intersection — existing pattern)
# ===========================================================================

def _hypothesize_intersection_only(
    snap: GraphSnapshot, int_id: int, tier: str, tier_max_lts: int,
    safe_was_fallback: bool, src: int, dst: int, safe_length: float,
) -> tuple[float, bool] | None:
    """Score a single intersection in isolation. Returns (savings_m, flips)
    if positive, else None."""
    new_len, in_tier = _hypothesized_safe_length(
        snap, src, dst, tier, tier_max_lts,
        upgrade_road_ids=(), upgrade_int_ids=(int_id,),
        safe_was_fallback=safe_was_fallback,
    )
    if new_len is None:
        return None
    savings = safe_length - new_len
    if savings <= 0:
        return None
    flips = safe_was_fallback and in_tier
    return savings, flips


# ===========================================================================
# analyze_gap — main entry point
# ===========================================================================

def analyze_gap(
    snap: GraphSnapshot, src: int, dst: int, tier: str,
) -> GapResult:
    """Run the spec §4.5 corridor algorithm. Returns GapResult.

    Algorithm:
      1. Compute fast and safe routes.
      2. If either is None, or they coincide, return empty result.
      3. Enumerate fast-route members:
         - segments where edge_seg_lts > tier_max_lts (grouped by road_id)
         - intersections where vertex_lts_approach > tier_max_lts
      4. If no off-tier members, return empty result.
      5. Combined hypothesis: hypothesize all members upgraded together.
         Compute combined_savings + flips_to_fully_safe.
      6. Per-road marginal: group segment road_ids by OSM name. For each
         group, recompute combined hypothesis with that group dropped,
         derive marginal_loss.
      7. Score each intersection independently for the
         "danger intersections" group.
      8. Threshold: surface corridor iff flips_to_fully_safe OR
         combined_savings > CORRIDOR_SAVINGS_FLOOR_M.
    """
    fast = compute_fast_route(snap, src, dst)
    safe = compute_safe_route(snap, src, dst, tier)
    safe_is_fallback = safe.is_fallback if safe else True

    empty_result = GapResult(
        fast_route=fast, safe_route=safe,
        safe_route_is_fallback=safe_is_fallback,
        corridor=None, intersections=(),
    )

    if safe is None or fast is None:
        return empty_result
    if fast.edge_path == safe.edge_path:
        return empty_result

    tier_max_lts = _TIER_MAX_LTS[tier]

    # ----- Enumerate fast-route off-tier members --------------------------
    fast_segment_road_ids: list[int] = []
    seen_rids: set[int] = set()
    for eid in fast.edge_path:
        rid = int(snap.edge_road_id[eid])
        seg_lts = int(snap.edge_seg_lts[eid])
        if seg_lts > tier_max_lts and rid not in seen_rids:
            seen_rids.add(rid)
            fast_segment_road_ids.append(rid)

    fast_intersection_int_ids: list[int] = []
    seen_iids: set[int] = set()
    for v in fast.vertex_path:
        if int(snap.vertex_lts_approach[v]) > tier_max_lts:
            iid = int(snap.vertex_to_int_id[v])
            if iid not in seen_iids:
                seen_iids.add(iid)
                fast_intersection_int_ids.append(iid)

    if not fast_segment_road_ids and not fast_intersection_int_ids:
        return empty_result

    current_safe_length = safe.length_m

    # ----- Combined hypothesis --------------------------------------------
    new_len, in_tier = _hypothesized_safe_length(
        snap, src, dst, tier, tier_max_lts,
        upgrade_road_ids=fast_segment_road_ids,
        upgrade_int_ids=fast_intersection_int_ids,
        safe_was_fallback=safe_is_fallback,
    )

    corridor: GapCorridor | None = None
    if new_len is not None:
        combined_savings = current_safe_length - new_len
        flips = safe_is_fallback and in_tier
        if combined_savings > CORRIDOR_SAVINGS_FLOOR_M or flips:
            corridor = _build_corridor(
                snap, src, dst, tier, tier_max_lts,
                fast_segment_road_ids, fast_intersection_int_ids,
                safe_is_fallback, current_safe_length,
                combined_savings, flips,
            )

    # ----- Intersection scoring (separate group) --------------------------
    # Same parallelization rationale as the corridor marginal loop above —
    # each per-intersection hypothesis is an independent Dijkstra, and on
    # long cross-town trips a fast route can cross many off-tier intersections.
    def _score_int(iid: int):
        return iid, _hypothesize_intersection_only(
            snap, iid, tier, tier_max_lts, safe_is_fallback,
            src, dst, current_safe_length,
        )

    intersection_records: list[GapIntersection] = []
    for iid, result in _marginal_pool.map(_score_int, fast_intersection_int_ids):
        if result is None:
            continue
        savings, flips = result
        vtx = vertex_for_int_id(snap, iid)
        if vtx is None:
            continue
        lat = float(snap.vertex_coords_wgs84[vtx][0])
        lon = float(snap.vertex_coords_wgs84[vtx][1])
        intersection_records.append(GapIntersection(
            int_id=iid,
            name=_resolve_intersection_name(snap, iid),
            current_lts_approach=int(snap.vertex_lts_approach[vtx]),
            savings_m=savings,
            on_hin=bool(snap.vertex_on_hin[vtx]),
            flips_to_fully_safe=flips,
            geometry_wkt=Point(lon, lat).wkt,
        ))
    # Rank flips first, then by savings desc.
    intersection_records.sort(key=lambda x: (not x.flips_to_fully_safe, -x.savings_m))

    return GapResult(
        fast_route=fast, safe_route=safe,
        safe_route_is_fallback=safe_is_fallback,
        corridor=corridor,
        intersections=tuple(intersection_records),
    )


def _build_corridor(
    snap: GraphSnapshot, src: int, dst: int, tier: str, tier_max_lts: int,
    segment_road_ids: list[int], intersection_int_ids: list[int],
    safe_is_fallback: bool, current_safe_length: float,
    combined_savings: float, flips: bool,
) -> GapCorridor:
    """Build the corridor object: per-road grouping + marginals + overlay
    geometry. Called after the combined hypothesis has been computed."""
    # Group segments by OSM road name (None becomes per-road_id singletons).
    by_name: dict[str, list[int]] = {}
    for rid in segment_road_ids:
        nm = _resolve_segment_name(snap, rid)
        # Unnamed roads each become their own group so the UI lists them
        # distinctly rather than lumping them under one "(unnamed)" bucket.
        key = nm if nm else f"__unnamed_{rid}"
        by_name.setdefault(key, []).append(rid)

    # Compute geometry for the polyline overlay (all upgraded segments).
    overlay_lines: list[LineString] = []
    for rid in segment_road_ids:
        line = _segment_wgs84_line(snap, rid)
        if line is not None:
            overlay_lines.append(line)

    # Marginals: for each named group, recompute hypothesized safe length
    # with this group dropped. Run the per-group Dijkstras in parallel — on a
    # long cross-town trip a corridor can have 20+ named streets, and a
    # sequential loop (1-2s per Dijkstra) easily blows past the frontend's
    # 60s poll timeout. igraph releases the GIL during shortest_paths, so
    # threading delivers a real wall-clock speedup.
    def _marginal_for(rids: list[int]) -> float | None:
        kept_rids = [r for r in segment_road_ids if r not in rids]
        new_len_without, _ = _hypothesized_safe_length(
            snap, src, dst, tier, tier_max_lts,
            upgrade_road_ids=kept_rids,
            upgrade_int_ids=intersection_int_ids,
            safe_was_fallback=safe_is_fallback,
        )
        return new_len_without

    name_keys = list(by_name.keys())
    new_lens_without = list(_marginal_pool.map(
        _marginal_for, (by_name[k] for k in name_keys),
    ))

    roads: list[CorridorRoad] = []
    for name_key, new_len_without in zip(name_keys, new_lens_without, strict=False):
        rids = by_name[name_key]
        # new_len_without is None => couldn't reach dst without this group's
        # upgrades; treat savings_without = 0 (this road is single-handedly
        # responsible, so marginal_loss = combined_savings).
        savings_without = (
            0.0 if new_len_without is None else current_safe_length - new_len_without
        )
        marginal_loss = combined_savings - savings_without

        # Per-group geometry (MultiLineString of this road's blocks).
        group_lines = [
            ln for ln in (_segment_wgs84_line(snap, r) for r in rids) if ln is not None
        ]
        # HIN flag: any block on HIN.
        on_hin = False
        for r in rids:
            eids = edges_for_road_id(snap, r)
            if eids is not None and bool(snap.road_on_hin_array[eids[0] // 2]):
                on_hin = True
                break

        display_name = name_key if not name_key.startswith("__unnamed_") else None

        roads.append(CorridorRoad(
            name=display_name,
            road_ids=tuple(rids),
            block_count=len(rids),
            on_hin=on_hin,
            geometry_wkt=_multilinestring_wkt(group_lines),
            savings_without_m=savings_without,
            marginal_loss_m=marginal_loss,
        ))

    # Sort by marginal_loss desc, ties broken by block_count desc, then name.
    roads.sort(key=lambda r: (-r.marginal_loss_m, -r.block_count, r.name or ""))

    return GapCorridor(
        combined_savings_m=combined_savings,
        flips_to_fully_safe=flips,
        fast_lts_overlay_wkt=_multilinestring_wkt(overlay_lines),
        roads=tuple(roads),
    )
