"""Load the bikemap routing graph into igraph at startup.

`load_graph(db_path)` returns a GraphSnapshot bundling:
  - the directed graph (one vertex per intersection, two directed edges per
    street),
  - lookup maps (PFB int_id → vertex idx, and the inverse),
  - per-edge attribute arrays (segment_lts, head_node lts_approach, length_m,
    road_id) — stored as compact numpy arrays to keep the 707k-edge footprint
    in memory cheap; `highway` lives only on the per-road arrays since gap
    analysis is the only consumer,
  - per-tier precomputed MAIN and FALLBACK edge weights (length × weight[
    tier, effective_lts]),
  - a scipy KD-tree of intersection coordinates in EPSG:6454 metres for
    nearest-vertex lookups returning (idx, distance_m),
  - per-vertex arrays (lts_approach, on_hin) and per-road_id arrays
    (lts, length, on_hin, highway, head/tail int_id, bbox in EPSG:6454)
    used by gap_analysis for in-memory candidate enumeration without
    DB access at request time.

Read-only; shared across all request threads (gunicorn `-w 1 --threads 4`)
and never mutated after construction.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import igraph as ig
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely import wkb

from app.core.weights import TIERS

_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class GraphSnapshot:
    # Graph topology + per-edge attributes (one entry per directed edge,
    # E = 2 × number of bidirectional streets). Stored as numpy arrays so
    # ~707k-edge attribute lists don't balloon resident memory with Python
    # object headers (Plan 2A Task 14 follow-up).
    g: ig.Graph
    edge_seg_lts: np.ndarray                      # shape (E,) int8, values 1..3
    edge_head_lts: np.ndarray                     # shape (E,) int8, lts_approach of dest vertex
    edge_length_m: np.ndarray                     # shape (E,) float64
    edge_road_id: np.ndarray                      # shape (E,) int32, source PFB ROAD_ID (max ~1M on Chicago, well within int32)

    # Per-tier precomputed weights, length E. Both main and fallback so
    # routing.py and gap_analysis.py never allocate per-request (Fix 9).
    base_weights_by_tier: dict[str, np.ndarray]   # tier -> shape (E,) float64
    fallback_weights_by_tier: dict[str, np.ndarray]  # tier -> shape (E,) float64

    # Vertex-level data (length V).
    # Naming wart (Fix 12): osm_id_*/vertex_to_int_id refer to PFB's
    # intersection node IDs (the schema's `intersections.osm_id` column),
    # NOT real OpenStreetMap node IDs. Kept for parallelism with the schema.
    #
    # PFB int_id → vertex idx lookup is a sorted parallel-array pair queried
    # via `vertex_for_int_id()`; replaces the prior dict to save ~25 MB on
    # the 350k-vertex Chicago graph.
    osm_id_sorted: np.ndarray                     # shape (V,) int32, sorted ascending (PFB int_id max ~710k)
    osm_id_to_vertex_idx: np.ndarray              # shape (V,) int32, aligned to osm_id_sorted (values < V)
    vertex_to_int_id: np.ndarray                  # shape (V,) int32 (PFB int_id max ~710k)
    vertex_coords_wgs84: np.ndarray               # shape (V, 2) float64, columns [lat, lon]
    vertex_coords_proj: np.ndarray                # shape (V, 2) EPSG:6454 metres
    vertex_kdtree: cKDTree
    vertex_lts_approach: np.ndarray               # shape (V,) int8
    vertex_on_hin: np.ndarray                     # shape (V,) bool

    # Per-unique-road_id metadata for in-memory gap-analysis filtering (Fix 3).
    # The road_*_array fields stay in load order so that edges_for_road_id()
    # can derive the (forward, reverse) edge id pair as (2k, 2k+1) without
    # storing an explicit edge-id index. road_id_sorted + road_id_sorted_to_load_pos
    # provide the searchsorted lookup path.
    road_id_array: np.ndarray                     # shape (R,) int32, load order (max ~1M on Chicago)
    road_id_sorted: np.ndarray                    # shape (R,) int32, sorted ascending
    road_id_sorted_to_load_pos: np.ndarray        # shape (R,) int32, sorted_pos -> load_pos (values < R)
    road_osm_id_array: np.ndarray                 # shape (R,) int64 (OSM way ids — growing past 1.4B, kept wide)
    road_lts_array: np.ndarray                    # shape (R,) int8
    road_length_array: np.ndarray                 # shape (R,) float64
    road_on_hin_array: np.ndarray                 # shape (R,) bool
    road_highway_list: list[str | None]           # length R
    road_name_list: list[str | None]              # length R; OSM `name` tag, surfaced by gap analysis for human-readable callouts
    road_head_int_id_array: np.ndarray            # shape (R,) int32 (PFB int_id max ~710k)
    road_tail_int_id_array: np.ndarray            # shape (R,) int32 (PFB int_id max ~710k)
    road_bbox_proj: np.ndarray                    # shape (R, 4) — minx, miny, maxx, maxy
    road_endpoints_proj: np.ndarray               # shape (R, 4) — head_x, head_y, tail_x, tail_y


def vertex_for_int_id(snap: GraphSnapshot, int_id: int) -> int | None:
    """Return vertex_idx for the given PFB intersection int_id, or None if unknown.

    Uses np.searchsorted on the sorted osm_id array; the equality check is
    mandatory because searchsorted returns the insertion position for missing
    keys, which would otherwise silently map to the next-larger key's vertex.
    """
    sorted_arr = snap.osm_id_sorted
    pos = int(np.searchsorted(sorted_arr, int_id))
    if pos == len(sorted_arr) or int(sorted_arr[pos]) != int_id:
        return None
    return int(snap.osm_id_to_vertex_idx[pos])


def edges_for_road_id(snap: GraphSnapshot, road_id: int) -> tuple[int, int] | None:
    """Return (forward_eid, reverse_eid) for the given PFB road_id, or None.

    Exploits the load_graph invariant that edges are added in pairs per SQL
    row: forward at index 2k, reverse at 2k+1, where k is the load order
    position of the road. Replaces the prior O(E) scan over edge_road_id
    (~70M Python comparisons per gap call) with two O(log R) searchsorted
    lookups + an indirection through the sorted→load permutation.
    """
    sorted_arr = snap.road_id_sorted
    pos = int(np.searchsorted(sorted_arr, road_id))
    if pos == len(sorted_arr) or int(sorted_arr[pos]) != road_id:
        return None
    load_pos = int(snap.road_id_sorted_to_load_pos[pos])
    return (2 * load_pos, 2 * load_pos + 1)


def load_graph(db_path: Path) -> GraphSnapshot:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # ---- Intersections → vertices ------------------------------------------------
    int_rows = list(con.execute(
        "SELECT osm_id, geom, lts_approach, on_hin FROM intersections"
    ))
    n_vertices = len(int_rows)
    # Build a transient dict during the SQL load — the streets loop below
    # needs O(1) int_id → vertex idx lookups for ~700k street rows. Convert
    # to the sorted-parallel-array layout (~25 MB savings vs keeping the
    # dict in the dataclass) once construction is done.
    int_id_to_vertex: dict[int, int] = {}
    vertex_to_int_id_arr = np.empty(n_vertices, dtype=np.int32)
    coords_wgs84_arr = np.empty((n_vertices, 2), dtype=np.float64)
    coords_proj = np.empty((n_vertices, 2), dtype=np.float64)
    vertex_lts_approach = np.empty(n_vertices, dtype=np.int8)
    vertex_on_hin = np.empty(n_vertices, dtype=bool)

    for idx, r in enumerate(int_rows):
        int_id = int(r["osm_id"])
        int_id_to_vertex[int_id] = idx
        vertex_to_int_id_arr[idx] = int_id
        pt = wkb.loads(r["geom"])
        coords_wgs84_arr[idx, 0] = pt.y  # lat
        coords_wgs84_arr[idx, 1] = pt.x  # lon
        x_m, y_m = _TO_IL_EAST_M(pt.x, pt.y)
        coords_proj[idx] = (x_m, y_m)
        vertex_lts_approach[idx] = int(r["lts_approach"])
        vertex_on_hin[idx] = bool(r["on_hin"])

    kdtree = cKDTree(coords_proj)

    # Sorted parallel arrays for vertex_for_int_id helper (replaces the dict).
    osm_id_sort_perm = np.argsort(vertex_to_int_id_arr, kind="stable")
    osm_id_sorted_arr = vertex_to_int_id_arr[osm_id_sort_perm]  # int32, indexed copy
    osm_id_to_vertex_idx_arr = osm_id_sort_perm.astype(np.int32, copy=False)

    # ---- Streets → directed edges + per-road_id arrays --------------------------
    sql = """
        SELECT road_id, osm_id, head_node_osm_id, tail_node_osm_id,
               length_m, lts, highway, name, on_hin, geom
          FROM streets
         WHERE head_node_osm_id != tail_node_osm_id
    """
    edges: list[tuple[int, int]] = []
    seg_lts: list[int] = []
    head_lts: list[int] = []
    length_m: list[float] = []
    road_id_per_edge: list[int] = []

    # Per-road_id (one entry per row, since each PFB ROAD_ID is unique per row).
    road_ids: list[int] = []
    road_osm: list[int] = []
    road_lts: list[int] = []
    road_lengths: list[float] = []
    road_on_hin: list[bool] = []
    road_highways: list[str | None] = []
    road_names: list[str | None] = []
    road_heads: list[int] = []
    road_tails: list[int] = []
    bboxes: list[tuple[float, float, float, float]] = []
    endpoints: list[tuple[float, float, float, float]] = []

    for r in con.execute(sql):
        h_int = int(r["head_node_osm_id"])
        t_int = int(r["tail_node_osm_id"])
        if h_int not in int_id_to_vertex or t_int not in int_id_to_vertex:
            continue  # Defensive; shouldn't happen with Plan 1 schema invariants.
        h = int_id_to_vertex[h_int]
        t = int_id_to_vertex[t_int]
        sl = int(r["lts"])
        ll = float(r["length_m"])
        rid = int(r["road_id"])
        hw = r["highway"]
        nm = r["name"]
        on_hin = bool(r["on_hin"])

        # Forward + reverse directed edges.
        edges.append((h, t))
        seg_lts.append(sl)
        head_lts.append(int(vertex_lts_approach[t]))
        length_m.append(ll)
        road_id_per_edge.append(rid)

        edges.append((t, h))
        seg_lts.append(sl)
        head_lts.append(int(vertex_lts_approach[h]))
        length_m.append(ll)
        road_id_per_edge.append(rid)

        # Per-road_id metadata + projected bbox/endpoints for gap analysis.
        line = wkb.loads(r["geom"])
        # Project all coordinates of the LineString to EPSG:6454 once.
        proj_coords = [_TO_IL_EAST_M(x, y) for (x, y) in line.coords]
        xs = [c[0] for c in proj_coords]
        ys = [c[1] for c in proj_coords]
        head_x, head_y = proj_coords[0]
        tail_x, tail_y = proj_coords[-1]

        road_ids.append(rid)
        road_osm.append(int(r["osm_id"]))
        road_lts.append(sl)
        road_lengths.append(ll)
        road_on_hin.append(on_hin)
        road_highways.append(hw)
        road_names.append(nm)
        road_heads.append(h_int)
        road_tails.append(t_int)
        bboxes.append((min(xs), min(ys), max(xs), max(ys)))
        endpoints.append((head_x, head_y, tail_x, tail_y))

    con.close()

    g = ig.Graph(n=n_vertices, edges=edges, directed=True)

    # ---- Convert per-edge lists to numpy arrays (Plan 2A Task 14 follow-up) -----
    # Compact dtypes shave ~150 MB off the 707k-edge resident footprint vs
    # Python list-of-floats/ints (each Python object is 28-byte body + 8-byte
    # pointer; numpy stores pure scalars contiguously).
    edge_seg_lts_arr = np.asarray(seg_lts, dtype=np.int8)
    edge_head_lts_arr = np.asarray(head_lts, dtype=np.int8)
    edge_length_m_arr = np.asarray(length_m, dtype=np.float64)
    edge_road_id_arr = np.asarray(road_id_per_edge, dtype=np.int32)

    # ---- Per-tier weight precomputation (Fix 9: main + fallback) ---------------
    # Vectorized: eff_lts = max(seg_lts, head_lts); index per-tier weight tables
    # with eff_lts - 1; multiply elementwise by edge_length_m. Avoids the Python
    # loop over g.ecount() — both faster startup AND less peak allocation.
    seg_lts_int = edge_seg_lts_arr.astype(np.int64)   # widen for safe indexing
    head_lts_int = edge_head_lts_arr.astype(np.int64)
    eff_lts = np.maximum(seg_lts_int, head_lts_int)   # shape (E,) values 1..3
    eff_idx = eff_lts - 1

    base_weights_by_tier: dict[str, np.ndarray] = {}
    fallback_weights_by_tier: dict[str, np.ndarray] = {}
    for tier_name, tables in TIERS.items():
        main_w = np.asarray(tables["main"], dtype=np.float64)       # shape (3,)
        fb_w = np.asarray(tables["fallback"], dtype=np.float64)     # shape (3,)
        base_weights_by_tier[tier_name] = edge_length_m_arr * main_w[eff_idx]
        fallback_weights_by_tier[tier_name] = edge_length_m_arr * fb_w[eff_idx]

    # ---- Convert per-road lists to numpy arrays ---------------------------------
    # road_id_array stays in load order so the (2k, 2k+1) edge-pair invariant
    # in edges_for_road_id() holds. road_id_sorted + road_id_sorted_to_load_pos
    # give a fast searchsorted lookup that yields the load-order position.
    n_roads = len(road_ids)
    road_id_array = np.asarray(road_ids, dtype=np.int32)
    road_id_sort_perm = np.argsort(road_id_array, kind="stable")
    road_id_sorted_arr = road_id_array[road_id_sort_perm]  # int32, indexed copy
    road_id_sorted_to_load_pos_arr = road_id_sort_perm.astype(np.int32, copy=False)

    return GraphSnapshot(
        g=g,
        edge_seg_lts=edge_seg_lts_arr,
        edge_head_lts=edge_head_lts_arr,
        edge_length_m=edge_length_m_arr,
        edge_road_id=edge_road_id_arr,
        base_weights_by_tier=base_weights_by_tier,
        fallback_weights_by_tier=fallback_weights_by_tier,
        osm_id_sorted=osm_id_sorted_arr,
        osm_id_to_vertex_idx=osm_id_to_vertex_idx_arr,
        vertex_to_int_id=vertex_to_int_id_arr,
        vertex_coords_wgs84=coords_wgs84_arr,
        vertex_coords_proj=coords_proj,
        vertex_kdtree=kdtree,
        vertex_lts_approach=vertex_lts_approach,
        vertex_on_hin=vertex_on_hin,
        road_id_array=road_id_array,
        road_id_sorted=road_id_sorted_arr,
        road_id_sorted_to_load_pos=road_id_sorted_to_load_pos_arr,
        road_osm_id_array=np.asarray(road_osm, dtype=np.int64),
        road_lts_array=np.asarray(road_lts, dtype=np.int8),
        road_length_array=np.asarray(road_lengths, dtype=np.float64),
        road_on_hin_array=np.asarray(road_on_hin, dtype=bool),
        road_highway_list=road_highways,
        road_name_list=road_names,
        road_head_int_id_array=np.asarray(road_heads, dtype=np.int32),
        road_tail_int_id_array=np.asarray(road_tails, dtype=np.int32),
        road_bbox_proj=np.asarray(bboxes, dtype=np.float64).reshape(n_roads, 4) if n_roads else np.empty((0, 4)),
        road_endpoints_proj=np.asarray(endpoints, dtype=np.float64).reshape(n_roads, 4) if n_roads else np.empty((0, 4)),
    )


def nearest_vertex(snap: GraphSnapshot, lat: float, lon: float) -> tuple[int, float]:
    """Return (vertex_idx, distance_m) of the nearest intersection.

    Distance is in EPSG:6454 metres. Callers should reject queries snapping
    to a vertex >5 km away (likely outside Cook County) — see Task 10.
    """
    x_m, y_m = _TO_IL_EAST_M(lon, lat)
    distance, idx = snap.vertex_kdtree.query([x_m, y_m], k=1)
    return int(idx), float(distance)
