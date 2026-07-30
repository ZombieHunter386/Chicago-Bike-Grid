# prep/graph/osm_builder.py
"""Build a routable street graph from OpenStreetMap via osmnx (Phase 3).

Replaces PFB/brokenspoke as the source of the routing topology. Emits lightweight
OsmEdge / OsmNode records (geometry in EPSG:4326). Phase 4 attaches a tier and
converts these into the SegmentRecord / IntersectionRecord the DbBuilder consumes.

Gotchas handled here (plan review F2/F3):
  - osmnx 2.x `graph_from_bbox(bbox, *, ...)` takes a single tuple ordered
    (left/W, bottom/S, right/E, top/N). Our `target.bbox` is
    (min_lat, max_lat, min_lng, max_lng), so reorder via `bbox_to_osmnx`.
  - Simplified osmnx edges carry a *list* of `osmid`s and sometimes a list
    `name`/`highway`; collapse to a single value for the schema, but keep the
    full osmid list (`osm_way_ids`) for the Cook County LTS way-ID join.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import transform

logger = logging.getLogger(__name__)

# WGS84 -> NAD83(2011) Illinois East (metres); matches prep/db/builder.py.
_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform


@dataclass(frozen=True)
class OsmEdge:
    """One undirected street edge from the OSM graph (geometry in EPSG:4326)."""

    road_id: int  # synthesized stable unique int — HIN match key (passed as OsmSegment.osm_id)
    osm_id: int  # single OSM way id for the schema (first of the osmid list)
    osm_way_ids: tuple[str, ...]  # all OSM way ids on this edge — county LTS join key
    head_node_id: int  # osmnx u
    tail_node_id: int  # osmnx v
    name: str | None
    highway: str | None
    length_m: float
    geometry_wkt: str


@dataclass(frozen=True)
class OsmNode:
    node_id: int  # osmnx node id — also the intersection osm_id
    geometry_wkt: str  # POINT, EPSG:4326


def bbox_to_osmnx(
    target_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Reorder our (min_lat, max_lat, min_lng, max_lng) to osmnx's (W, S, E, N)."""
    min_lat, max_lat, min_lng, max_lng = target_bbox
    return (min_lng, min_lat, max_lng, max_lat)


# OSM highway classes excluded from the routable/displayed network. `service`
# covers alleys, driveways, and parking aisles — not useful bike routes.
_EXCLUDED_HIGHWAYS = frozenset({"service"})


def _first(value: Any) -> Any:
    """osmnx attrs can be a scalar or a list; return the first scalar."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _all_way_ids(osmid: Any) -> tuple[str, ...]:
    if osmid is None:
        return ()
    if isinstance(osmid, list):
        return tuple(str(o) for o in osmid)
    return (str(osmid),)


def build_graph_from_bbox(
    target_bbox: tuple[float, float, float, float],
    network_type: str = "bike",
) -> nx.MultiDiGraph:
    """Download + simplify the OSM street graph for `target_bbox` via Overpass.

    Returns a simplified MultiDiGraph in EPSG:4326 (osmnx's default output CRS),
    retaining only the largest connected component so the result is routable.
    Network-bound; exercised in the Phase 6 integration build, not unit tests.

    Superseded as the default by the Geofabrik path in
    ``prep.graph.pbf_extract`` — at county scale osmnx tiles the bbox into
    dozens of Overpass requests and the public instance banned us mid-build.
    Kept as an escape hatch (``GRAPH_SOURCE=overpass``) for small bboxes and
    for cross-checking the two sources against each other.
    """
    import osmnx as ox

    from prep.osm_config import configure_osmnx

    # Shared cache dir (data/cache/, gitignored — left unset osmnx defaults to
    # `./cache` at the repo root) + the OVERPASS_URL-configurable endpoint.
    configure_osmnx(ox)

    return ox.graph_from_bbox(
        bbox_to_osmnx(target_bbox),
        network_type=network_type,
        simplify=True,
        retain_all=False,
    )


def build_graph(
    target_bbox: tuple[float, float, float, float],
    cache_dir: Path,
    network_type: str = "bike",
) -> nx.MultiDiGraph:
    """Build the street graph from the configured source.

    ``GRAPH_SOURCE`` selects: ``geofabrik`` (default — one cached regional
    extract, clipped locally with osmium) or ``overpass`` (the original
    tiled download, kept for small bboxes and for comparing sources).

    Both paths end in the same osmnx call shape (``simplify=True``,
    ``retain_all=False``), so the topology — and therefore the ``road_id``
    assignment the DB and gap analysis depend on — is equivalent either way.
    """
    source = os.environ.get("GRAPH_SOURCE", "geofabrik").strip().lower()
    if source == "overpass":
        logger.info("building graph via Overpass (GRAPH_SOURCE=overpass)")
        return build_graph_from_bbox(target_bbox, network_type=network_type)
    if source != "geofabrik":
        raise ValueError(
            f"GRAPH_SOURCE must be 'geofabrik' or 'overpass' (got {source!r})"
        )
    from prep.graph.pbf_extract import build_graph_from_pbf

    logger.info("building graph from Geofabrik extract")
    return build_graph_from_pbf(
        target_bbox, cache_dir=cache_dir, network_type=network_type,
    )


def prune_to_routable_network(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Return a routable subgraph: drop excluded-highway edges, then keep only
    the largest weakly-connected component.

    osmnx's ``graph_from_bbox(retain_all=False)`` already returns the largest
    component, but that's computed *with* service roads still present. Once we
    remove service roads (alleys/driveways), the ~intersections that only touched
    them become isolated vertices, and a few areas split into small islands. Left
    in, those dead vertices stay in the DB and ``nearest_vertex`` can snap a
    home/dest onto one, so routing finds no path ("sometimes no routes"). Taking
    the largest weakly-connected component *after* the removal drops both the
    isolated orphans and the islands in one step, leaving a fully-routable graph.
    """
    g = graph.copy()
    to_remove = [
        (u, v, k)
        for u, v, k, data in g.edges(keys=True, data=True)
        if _first(data.get("highway")) in _EXCLUDED_HIGHWAYS
    ]
    g.remove_edges_from(to_remove)
    if g.number_of_nodes() == 0:
        return g
    largest = max(nx.weakly_connected_components(g), key=len)
    return g.subgraph(largest).copy()


def build_nodes(graph: nx.MultiDiGraph) -> Iterator[OsmNode]:
    """Yield an OsmNode per graph node (x = lng, y = lat)."""
    for node_id, data in graph.nodes(data=True):
        pt = Point(data["x"], data["y"])
        yield OsmNode(node_id=int(node_id), geometry_wkt=pt.wkt)


def build_street_edges(graph: nx.MultiDiGraph) -> Iterator[OsmEdge]:
    """Yield one OsmEdge per *undirected* street edge.

    A MultiDiGraph carries both directions of a two-way street; we collapse them
    to a single record keyed on (unordered node pair, osm_id). road_id is a
    monotonic counter (stable for a given deterministic graph build).

    Service roads (``highway=service`` — alleys, driveways, parking aisles) are
    skipped: they aren't useful bike routes, and left in they make up ~half of
    all OSM ways, dominating the map as clutter.
    """
    road_id = 0
    seen: set[tuple[frozenset[int], int]] = set()
    for u, v, data in graph.edges(data=True):
        if _first(data.get("highway")) in _EXCLUDED_HIGHWAYS:
            continue
        osmid = data.get("osmid")
        osm_id = int(_first(osmid))
        key = (frozenset((int(u), int(v))), osm_id)
        if key in seen:
            continue
        seen.add(key)

        geom = data.get("geometry")
        if geom is None:
            geom = LineString([
                (graph.nodes[u]["x"], graph.nodes[u]["y"]),
                (graph.nodes[v]["x"], graph.nodes[v]["y"]),
            ])

        length_m = data.get("length")
        if length_m is None:
            length_m = transform(_TO_IL_EAST_M, geom).length

        road_id += 1
        yield OsmEdge(
            road_id=road_id,
            osm_id=osm_id,
            osm_way_ids=_all_way_ids(osmid),
            head_node_id=int(u),
            tail_node_id=int(v),
            name=_first(data.get("name")),
            highway=_first(data.get("highway")),
            length_m=float(length_m),
            geometry_wkt=geom.wkt,
        )
