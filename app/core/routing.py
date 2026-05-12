"""Shortest-path routing on the GraphSnapshot.

Per spec §4.1:
  - Fast route: minimize edge_length_m only (LTS / HIN ignored). Bike-routability
    is implicit — PFB only emits LTS-evaluable bike-routable ways.
  - Safe route: minimize length × tier_weight[effective_lts] using main weights;
    if any edge in the result has weight >= INF_WEIGHT (i.e., the only path
    requires crossing a disallowed-tier edge), retry with fallback weights
    and flag is_fallback=True.

v1 uses Dijkstra (igraph.Graph.get_shortest_paths). A* is a deferred
optimization — see plan §"Out of scope".
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from app.core.graph import GraphSnapshot
from app.core.weights import INF_WEIGHT


@dataclass(frozen=True)
class Route:
    edge_path: list[int]               # igraph edge indices in order
    vertex_path: list[int]             # igraph vertex indices
    edge_lts: list[int]                # per-edge effective LTS (max of seg_lts and head_lts), length = len(edge_path); empty for trivial routes
    length_m: float                    # sum of edge_length_m along the path
    weighted_cost: float               # sum of weights along the path
    is_fallback: bool                  # True if main weights yielded no path
    lts_distribution: dict[int, int]   # effective_lts -> edge count


def _path_or_none(snap: GraphSnapshot, src: int, dst: int,
                   weights: np.ndarray) -> list[int] | None:
    paths = snap.g.get_shortest_paths(src, to=dst, weights=weights, output="epath")
    if not paths or not paths[0]:
        return None
    return paths[0]


def _build_route(snap: GraphSnapshot, edge_path: list[int],
                 weights: np.ndarray, is_fallback: bool) -> Route:
    length = float(sum(snap.edge_length_m[e] for e in edge_path))
    cost = float(sum(weights[e] for e in edge_path))
    lts_hist: Counter[int] = Counter()
    # Per-edge effective LTS. Used by the frontend to color the safe route
    # green-on-tier / amber-off-tier per segment (so a best-effort safe
    # route is visibly distinct in its stressful subsegments instead of
    # showing as a single amber blob).
    edge_lts: list[int] = []
    for e in edge_path:
        # Cast np.int8 scalar to Python int so Counter keys are clean ints
        # (avoids `np.int8` keys leaking into JSON serialization downstream).
        eff = int(max(snap.edge_seg_lts[e], snap.edge_head_lts[e]))
        lts_hist[eff] += 1
        edge_lts.append(eff)
    vertices = [snap.g.es[edge_path[0]].source]
    for e in edge_path:
        vertices.append(snap.g.es[e].target)
    return Route(
        edge_path=list(edge_path),
        vertex_path=vertices,
        edge_lts=edge_lts,
        length_m=length,
        weighted_cost=cost,
        is_fallback=is_fallback,
        lts_distribution=dict(lts_hist),
    )


def _trivial_route(src: int) -> Route:
    """Zero-length route for the src == dst case (Fix F)."""
    return Route(
        edge_path=[],
        vertex_path=[src],
        edge_lts=[],
        length_m=0.0,
        weighted_cost=0.0,
        is_fallback=False,
        lts_distribution={},
    )


def compute_fast_route(snap: GraphSnapshot, src: int, dst: int) -> Route | None:
    """Minimize edge_length_m. LTS and HIN ignored (spec §4.1)."""
    if src == dst:
        return _trivial_route(src)
    weights = snap.edge_length_m
    epath = _path_or_none(snap, src, dst, weights)
    if epath is None:
        return None
    return _build_route(snap, epath, weights, is_fallback=False)


def compute_safe_route(snap: GraphSnapshot, src: int, dst: int, tier: str) -> Route | None:
    """Minimize stress-weighted distance for the given tier.

    Fallback detection (Fix 1): after Dijkstra returns a path, check whether
    ANY edge has weight >= INF_WEIGHT. If yes, the only path crosses a
    disallowed-tier edge — re-run with precomputed fallback weights from
    spec §0.1.
    """
    if src == dst:
        return _trivial_route(src)
    main_weights = snap.base_weights_by_tier[tier]
    epath = _path_or_none(snap, src, dst, main_weights)
    if epath is not None and not any(main_weights[e] >= INF_WEIGHT for e in epath):
        return _build_route(snap, epath, main_weights, is_fallback=False)
    # epath is None or path crossed an INF edge → fall through to fallback.

    fallback_weights = snap.fallback_weights_by_tier[tier]
    epath_fb = _path_or_none(snap, src, dst, fallback_weights)
    if epath_fb is None:
        return None
    return _build_route(snap, epath_fb, fallback_weights, is_fallback=True)
