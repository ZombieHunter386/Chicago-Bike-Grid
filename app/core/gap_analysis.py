"""Gap analysis algorithm (spec §4.5) + corridor detection.

Inputs: GraphSnapshot, src/dst vertex indices, tier name.
Output: GapResult with fast_route, safe_route, ranked candidates, and
optional corridor grouping.

Memory rule: never copy the graph. Per candidate, build a fresh weights
list (length = ecount); only entries for affected edges change from the
precomputed base_weights. Pass to igraph.get_shortest_paths().

Fix 3: candidate enumeration uses GraphSnapshot's in-memory road/vertex
arrays — no DB access at request time.

Fix 2: segments and intersections are merged into one list and sorted
together by (-violation, -length) — intersections use length=0 as the
tie-breaker, so a higher-violation intersection always outranks a
lower-violation segment regardless of feature kind.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from app.core.graph import GraphSnapshot, edges_for_road_id, vertex_for_int_id
from app.core.routing import Route, compute_fast_route, compute_safe_route
from app.core.weights import INF_WEIGHT, main_weight_for

DETOUR_BUFFER_M = 200.0
MAX_CANDIDATES = 100
CORRIDOR_ADJACENCY_M = 50.0
CORRIDOR_RELATIVE_THRESHOLD = 0.5  # candidate must save >=50% of headline

# tier_max_lts = highest LTS the user *prefers* — candidates are segments
# with lts > tier_max_lts (i.e., the LTS levels we'd want to fix to get on-tier).
# 'any' is set to 2 (not 3) so LTS-3 segments still surface as candidates;
# previously 3 made the filter `lts > 3` always-false, returning zero
# candidates regardless of input and silently breaking §6.4 #5.
_TIER_MAX_LTS = {"kid": 1, "parent": 2, "any": 2}
_INFEASIBLE_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link",
                        "railway", "aerialway", "waterway"}

_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class GapCandidate:
    feature_kind: str       # "segment" | "intersection"
    feature_id: int         # road_id (segment) or int_id (intersection)
    name: str | None        # human-readable street name (for segments) or comma-joined adjacent street names (for intersections); may be None if the OSM `name` tag is missing
    current_lts: int
    savings_m: float
    on_hin: bool
    flips_to_fully_safe: bool # True iff hypothesizing this candidate flips the safe route from fallback to a fully on-tier path; ranking-only signal (top-billed in the sort below) but the bool itself is also surfaced for frontend framing ("Fix THIS and you get a fully safe route")
    geometry_wkt: str       # for frontend display (in EPSG:4326)


@dataclass(frozen=True)
class GapResult:
    fast_route: Route | None
    safe_route: Route | None
    safe_route_is_fallback: bool
    headline: GapCandidate | None
    supporting: list[GapCandidate]   # ranks 2-5
    corridor: list[GapCandidate]


def _route_geometry_wgs84(snap: GraphSnapshot, route: Route) -> LineString:
    # vertex_coords_wgs84 is a (V, 2) numpy array with columns [lat, lon]; row
    # indexing returns a 2-element view — same access pattern as the prior
    # list-of-tuples representation.
    coords = [(float(snap.vertex_coords_wgs84[v][1]), float(snap.vertex_coords_wgs84[v][0]))
              for v in route.vertex_path]
    return LineString(coords)


def _detour_zone_proj(snap: GraphSnapshot, fast: Route, safe: Route) -> BaseGeometry:
    fast_geom = _route_geometry_wgs84(snap, fast)
    safe_geom = _route_geometry_wgs84(snap, safe)
    union = unary_union([fast_geom, safe_geom])
    proj = transform(_TO_IL_EAST_M, union)
    return proj.convex_hull.buffer(DETOUR_BUFFER_M)


def _enumerate_candidates(
    snap: GraphSnapshot, zone_proj: BaseGeometry, tier_max_lts: int,
) -> list[dict]:
    """In-memory candidate enumeration (Fix 3). Returns a unified list of
    candidate dicts; segments and intersections are merged for unified sort."""
    zminx, zminy, zmaxx, zmaxy = zone_proj.bounds
    candidates: list[dict] = []

    # ---- Segments (filter by lts > tier_max_lts and bbox overlap) ----
    bb = snap.road_bbox_proj  # shape (R, 4)
    if bb.shape[0] > 0:
        bbox_overlap = (
            (bb[:, 2] >= zminx) & (bb[:, 0] <= zmaxx) &
            (bb[:, 3] >= zminy) & (bb[:, 1] <= zmaxy)
        )
        lts_violates = snap.road_lts_array > tier_max_lts
        idx = np.where(bbox_overlap & lts_violates)[0]
        for i in idx:
            highway = snap.road_highway_list[i]
            if highway in _INFEASIBLE_HIGHWAYS:
                continue
            # Precise intersection test using projected endpoint LineString.
            hx, hy, tx, ty = snap.road_endpoints_proj[i]
            line = LineString([(hx, hy), (tx, ty)])
            if not zone_proj.intersects(line):
                continue
            # Reconstruct WGS84 LineString for frontend display.
            head_v = vertex_for_int_id(snap, int(snap.road_head_int_id_array[i]))
            tail_v = vertex_for_int_id(snap, int(snap.road_tail_int_id_array[i]))
            if head_v is None or tail_v is None:
                continue
            hlat = float(snap.vertex_coords_wgs84[head_v][0])
            hlon = float(snap.vertex_coords_wgs84[head_v][1])
            tlat = float(snap.vertex_coords_wgs84[tail_v][0])
            tlon = float(snap.vertex_coords_wgs84[tail_v][1])
            wgs_geom = LineString([(hlon, hlat), (tlon, tlat)])

            candidates.append({
                "feature_kind": "segment",
                "feature_id": int(snap.road_id_array[i]),
                "current_lts": int(snap.road_lts_array[i]),
                "length_m": float(snap.road_length_array[i]),  # tie-breaker
                "on_hin": bool(snap.road_on_hin_array[i]),
                "geometry_wkt": wgs_geom.wkt,
            })

    # ---- Intersections (filter by lts_approach > tier_max_lts and bbox) ----
    vp = snap.vertex_coords_proj  # shape (V, 2)
    if vp.shape[0] > 0:
        bbox_overlap = (
            (vp[:, 0] >= zminx) & (vp[:, 0] <= zmaxx) &
            (vp[:, 1] >= zminy) & (vp[:, 1] <= zmaxy)
        )
        lts_violates = snap.vertex_lts_approach > tier_max_lts
        idx = np.where(bbox_overlap & lts_violates)[0]
        for v in idx:
            x, y = vp[v]
            if not zone_proj.intersects(Point(float(x), float(y))):
                continue
            lat = float(snap.vertex_coords_wgs84[v][0])
            lon = float(snap.vertex_coords_wgs84[v][1])
            candidates.append({
                "feature_kind": "intersection",
                # Cast np.int64 → Python int so the value JSON-serializes cleanly
                # (numpy scalars aren't json-serializable in the stdlib encoder).
                "feature_id": int(snap.vertex_to_int_id[v]),
                "current_lts": int(snap.vertex_lts_approach[v]),
                "length_m": 0.0,    # tie-break LAST among same-violation features
                "on_hin": bool(snap.vertex_on_hin[v]),
                "geometry_wkt": Point(lon, lat).wkt,
            })

    # Unified sort by (-violation, -length) — Fix 2.
    candidates.sort(key=lambda c: (
        -(c["current_lts"] - tier_max_lts),
        -c["length_m"],
    ))
    return candidates[:MAX_CANDIDATES]


def _hypothesize_segment_weights(
    snap: GraphSnapshot, base_weights: np.ndarray, road_id: int,
    tier: str, tier_max_lts: int,
) -> np.ndarray:
    """Recompute weights for every directed edge sharing `road_id`, as if
    its segment_lts were tier_max_lts.

    Perf: previously did an O(E) scan over snap.edge_road_id (~700k Python
    comparisons per candidate, ~70M per gap query). Now uses
    edges_for_road_id() — O(log R) searchsorted + 2 edge updates.
    """
    weights = base_weights.copy()
    edge_pair = edges_for_road_id(snap, road_id)
    if edge_pair is None:
        return weights
    for eid in edge_pair:
        new_eff = max(tier_max_lts, int(snap.edge_head_lts[eid]))
        weights[eid] = float(snap.edge_length_m[eid]) * main_weight_for(tier, new_eff)
    return weights


def _hypothesize_intersection_weights(
    snap: GraphSnapshot, base_weights: np.ndarray, int_id: int,
    tier: str, tier_max_lts: int,
) -> np.ndarray:
    """Recompute weights for every edge ENTERING `int_id`, as if the head
    node's lts_approach were tier_max_lts."""
    weights = base_weights.copy()
    v = vertex_for_int_id(snap, int_id)
    if v is None:
        return weights
    for eid in snap.g.incident(v, mode="in"):
        new_eff = max(int(snap.edge_seg_lts[eid]), tier_max_lts)
        weights[eid] = float(snap.edge_length_m[eid]) * main_weight_for(tier, new_eff)
    return weights


def _hypothesize_segment_weights_under_table(
    snap: GraphSnapshot, base_weights: np.ndarray, road_id: int,
    weight_table: list[float], tier_max_lts: int,
) -> np.ndarray:
    """Like _hypothesize_segment_weights, but uses an arbitrary weight table
    (e.g., fallback weights) instead of main weights for the hypothesized
    edges. The hypothesis itself stays the same: segment_lts → tier_max_lts.
    Used by the fallback-path scoring pass below."""
    weights = base_weights.copy()
    edge_pair = edges_for_road_id(snap, road_id)
    if edge_pair is None:
        return weights
    for eid in edge_pair:
        new_eff = max(tier_max_lts, int(snap.edge_head_lts[eid]))
        weights[eid] = float(snap.edge_length_m[eid]) * weight_table[new_eff - 1]
    return weights


def _hypothesize_intersection_weights_under_table(
    snap: GraphSnapshot, base_weights: np.ndarray, int_id: int,
    weight_table: list[float], tier_max_lts: int,
) -> np.ndarray:
    """Like _hypothesize_intersection_weights but under an arbitrary weight
    table (fallback). See the segment-variant docstring above."""
    weights = base_weights.copy()
    v = vertex_for_int_id(snap, int_id)
    if v is None:
        return weights
    for eid in snap.g.incident(v, mode="in"):
        new_eff = max(int(snap.edge_seg_lts[eid]), tier_max_lts)
        weights[eid] = float(snap.edge_length_m[eid]) * weight_table[new_eff - 1]
    return weights


def _resolve_segment_name(snap: GraphSnapshot, road_id: int) -> str | None:
    """Look up snap.road_name_list[i] for the road_id."""
    edge_pair = edges_for_road_id(snap, road_id)
    if edge_pair is None:
        return None
    # edges_for_road_id returns (2k, 2k+1) in load order; k is the per-road
    # index into road_name_list.
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


def _safe_route_length(
    snap: GraphSnapshot, src: int, dst: int, weights: np.ndarray,
) -> float | None:
    """Dijkstra with custom weights. Returns path length in metres, or None
    if no path or path crosses INF_WEIGHT (Fix 1)."""
    paths = snap.g.get_shortest_paths(src, to=dst, weights=weights, output="epath")
    if not paths or not paths[0]:
        return None
    epath = paths[0]
    if any(weights[e] >= INF_WEIGHT for e in epath):
        return None
    return float(sum(snap.edge_length_m[e] for e in epath))


def _detect_corridor(candidates: list[GapCandidate], top_k: int = 5) -> list[GapCandidate]:
    """Group top-k candidates that are within CORRIDOR_ADJACENCY_M (50m)
    of each other and have savings >= CORRIDOR_RELATIVE_THRESHOLD of headline.
    Returns the corridor (or empty if no group of ≥2 forms)."""
    if len(candidates) < 2:
        return []
    headline = candidates[0]
    threshold = headline.savings_m * CORRIDOR_RELATIVE_THRESHOLD
    from shapely import wkt as _wkt
    geoms_proj: list[BaseGeometry] = []
    for c in candidates[:top_k]:
        g = _wkt.loads(c.geometry_wkt)
        geoms_proj.append(transform(_TO_IL_EAST_M, g))

    in_corridor = [False] * len(geoms_proj)
    in_corridor[0] = True
    changed = True
    while changed:
        changed = False
        for i in range(1, len(geoms_proj)):
            if in_corridor[i] or candidates[i].savings_m < threshold:
                continue
            for j in range(len(geoms_proj)):
                if not in_corridor[j]:
                    continue
                if geoms_proj[i].buffer(CORRIDOR_ADJACENCY_M).intersects(geoms_proj[j]):
                    in_corridor[i] = True
                    changed = True
                    break
    members = [candidates[i] for i in range(len(geoms_proj)) if in_corridor[i]]
    return members if len(members) >= 2 else []


def analyze_gap(
    snap: GraphSnapshot, src: int, dst: int, tier: str,
) -> GapResult:
    """Run the spec §4.5 gap algorithm. Returns GapResult.

    Amended from the original spec to also run when the safe route is a
    fallback (most realistic Chicago trips at tier=parent fall back, and
    short-circuiting there left §6.4 #5 dead in the water). The algorithm
    now scores every candidate under main weights first; for the fallback
    case, candidates that don't unfallback the route are re-scored under
    fallback weights so the "fix this segment" advocacy ask still surfaces.

    Ranking: lexicographic (flips_to_fully_safe, savings_m) descending —
    candidates that flip the safe route from fallback to fully on-tier
    take the top spot.

    No DB access at request time (Fix 3) — uses GraphSnapshot's in-memory
    road/vertex arrays.
    """
    fast = compute_fast_route(snap, src, dst)
    safe = compute_safe_route(snap, src, dst, tier)

    # True unreachable: no safe path even with fallback, OR safe == fast
    # (no detour to analyze).
    if safe is None or fast is None:
        return GapResult(
            fast_route=fast, safe_route=safe,
            safe_route_is_fallback=(safe.is_fallback if safe else True),
            headline=None, supporting=[], corridor=[],
        )
    if fast.edge_path == safe.edge_path:
        return GapResult(
            fast_route=fast, safe_route=safe,
            safe_route_is_fallback=safe.is_fallback,
            headline=None, supporting=[], corridor=[],
        )

    tier_max_lts = _TIER_MAX_LTS[tier]
    zone = _detour_zone_proj(snap, fast, safe)
    candidates = _enumerate_candidates(snap, zone, tier_max_lts)

    # Restrict candidates to features ON the fast route (option B in the
    # bug-2 design discussion). The detour-zone enumerator includes any
    # feature within DETOUR_BUFFER_M (200m) of either route; some of those
    # land 1-2 blocks off either polyline, which reads as "random nearby
    # street" to the user and adds noise to the advocacy ask. Restricting
    # to fast-route features makes every marker a literal "this is the
    # stressful street your safe route is detouring around" — directly
    # answering the user's "why is my safe route going here?" question.
    # Spec §4.5's "missing connection 2 blocks east" case is excluded as
    # a deliberate tradeoff for clarity over completeness.
    fast_road_ids = {int(snap.edge_road_id[e]) for e in fast.edge_path}
    fast_int_ids = {int(snap.vertex_to_int_id[v]) for v in fast.vertex_path}
    candidates = [
        c for c in candidates
        if (c["feature_kind"] == "segment" and c["feature_id"] in fast_road_ids)
        or (c["feature_kind"] == "intersection" and c["feature_id"] in fast_int_ids)
    ]

    base_main = snap.base_weights_by_tier[tier]
    base_fb = snap.fallback_weights_by_tier[tier]
    current_safe_length = safe.length_m
    safe_was_fallback = safe.is_fallback

    # Fallback weight table per LTS level (length-multipliers, indexed by
    # lts-1). Used to rebuild edge weights when the main-weights re-route
    # doesn't yield an in-tier path (fallback case).
    from app.core.weights import TIERS
    fb_table = TIERS[tier]["fallback"]

    scored: list[GapCandidate] = []
    for c in candidates:
        fid = c["feature_id"]
        kind = c["feature_kind"]

        # First pass: hypothesize under MAIN weights. If the safe route was
        # already in-tier this is the only pass that matters; if it was
        # fallback, a candidate that gets us a path under main weights is
        # the gold-standard advocacy ask ("fix this and you can ride on-tier").
        if kind == "segment":
            new_main_weights = _hypothesize_segment_weights(
                snap, base_main, fid, tier, tier_max_lts,
            )
        else:
            new_main_weights = _hypothesize_intersection_weights(
                snap, base_main, fid, tier, tier_max_lts,
            )
        new_length = _safe_route_length(snap, src, dst, new_main_weights)
        unfallbacks = False

        if new_length is None and safe_was_fallback:
            # Second pass (fallback only): the candidate alone doesn't
            # unfallback the route, but it may still meaningfully shorten
            # the fallback safe path by reducing one segment's stress
            # penalty (LTS-3 fallback weight is typically much higher than
            # LTS-tier_max fallback weight, e.g., 10x → 1.2x for tier=parent).
            if kind == "segment":
                new_fb_weights = _hypothesize_segment_weights_under_table(
                    snap, base_fb, fid, fb_table, tier_max_lts,
                )
            else:
                new_fb_weights = _hypothesize_intersection_weights_under_table(
                    snap, base_fb, fid, fb_table, tier_max_lts,
                )
            new_length = _safe_route_length_unrestricted(
                snap, src, dst, new_fb_weights,
            )
        elif new_length is not None and safe_was_fallback:
            # Main-weights path found AND safe was fallback → upgrading this
            # candidate flips the route to on-tier. Tag the candidate so it
            # leads the ranking.
            unfallbacks = True

        if new_length is None:
            continue
        savings = current_safe_length - new_length
        if savings <= 0:
            continue

        if kind == "segment":
            name = _resolve_segment_name(snap, fid)
        else:
            name = _resolve_intersection_name(snap, fid)

        scored.append(GapCandidate(
            feature_kind=kind,
            feature_id=fid,
            name=name,
            current_lts=c["current_lts"],
            savings_m=savings,
            on_hin=c["on_hin"],
            flips_to_fully_safe=unfallbacks,
            geometry_wkt=c["geometry_wkt"],
        ))

    # Rank: unfallback-flippers first (descending bool = True > False), then
    # savings descending.
    scored.sort(key=lambda c: (not c.flips_to_fully_safe, -c.savings_m))
    headline = scored[0] if scored else None
    supporting = scored[1:5]
    corridor = _detect_corridor(scored)

    return GapResult(
        fast_route=fast, safe_route=safe,
        safe_route_is_fallback=safe_was_fallback,
        headline=headline, supporting=supporting, corridor=corridor,
    )


def _safe_route_length_unrestricted(
    snap: GraphSnapshot, src: int, dst: int, weights: np.ndarray,
) -> float | None:
    """Dijkstra that accepts INF-weighted edges in the result (used only for
    the fallback-pass scoring). Returns the path length in metres, or None
    if no path exists at all. Unlike _safe_route_length(), it does NOT bail
    when an edge in the path has weight ≥ INF_WEIGHT — the caller's input
    is a fallback weight table, which by construction has no INFs."""
    paths = snap.g.get_shortest_paths(src, to=dst, weights=weights, output="epath")
    if not paths or not paths[0]:
        return None
    return float(sum(snap.edge_length_m[e] for e in paths[0]))
