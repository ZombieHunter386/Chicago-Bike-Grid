"""OSM graph builder (Phase 3).

Feeds a hand-built osmnx-style MultiDiGraph (EPSG:4326; node attrs x/y; edge
attrs osmid/highway/name/length/geometry) and asserts build_street_edges +
build_nodes emit consistent topology records the DbBuilder can consume.
"""

from unittest.mock import patch

import networkx as nx
import pytest
from shapely.geometry import LineString
from shapely.wkt import loads as wkt_loads

from prep.graph.osm_builder import (
    OsmEdge,
    OsmNode,
    bbox_to_osmnx,
    build_graph_from_bbox,
    build_nodes,
    build_street_edges,
    prune_to_routable_network,
)


@pytest.fixture
def tiny_graph() -> nx.MultiDiGraph:
    """A connected 4-node loop mirroring osmnx output conventions.

        4 --- 3
        |     |
        1 --- 2

    Edge 1<->2 is present in BOTH directions (dedup target). Edge 2->3 has a
    list `osmid` (simplified merge) and a curved `geometry` but no `length`
    (fallback compute). Edge 4->1 has a list `name`.
    """
    g = nx.MultiDiGraph()
    g.graph["crs"] = "epsg:4326"
    g.add_node(1, x=-87.68, y=41.94)
    g.add_node(2, x=-87.67, y=41.94)
    g.add_node(3, x=-87.67, y=41.95)
    g.add_node(4, x=-87.68, y=41.95)

    g.add_edge(1, 2, osmid=100, highway="residential", name="A St", length=825.0)
    g.add_edge(2, 1, osmid=100, highway="residential", name="A St", length=825.0)
    g.add_edge(
        2, 3, osmid=[200, 201], highway="cycleway", name="B St",
        geometry=LineString([(-87.67, 41.94), (-87.671, 41.945), (-87.67, 41.95)]),
    )
    g.add_edge(3, 4, osmid=300, highway="residential", length=825.0)
    g.add_edge(4, 1, osmid=400, highway="tertiary", name=["C St", "C Ave"], length=1100.0)
    return g


def test_build_graph_routes_osmnx_cache_under_data() -> None:
    """osmnx caches Overpass responses; left unconfigured it writes `./cache` at
    the repo root (NOT gitignored). The builder must point osmnx's cache under
    `data/cache/` (which is gitignored) so a real `make refresh` doesn't litter
    the repo root. Regression: surfaced by the Phase 6 integration build.
    """
    import osmnx as ox

    with patch("osmnx.graph_from_bbox", return_value=nx.MultiDiGraph()) as m:
        build_graph_from_bbox((41.6440, 42.0230, -87.9402, -87.5240))

    assert m.called
    cache = str(ox.settings.cache_folder).replace("\\", "/")
    assert "data/cache" in cache


def test_bbox_to_osmnx_reorders() -> None:
    # target.bbox order is (min_lat, max_lat, min_lng, max_lng);
    # osmnx 2.x wants (left/W, bottom/S, right/E, top/N) = (min_lng,min_lat,max_lng,max_lat)
    target_bbox = (41.6440, 42.0230, -87.9402, -87.5240)
    assert bbox_to_osmnx(target_bbox) == (-87.9402, 41.6440, -87.5240, 42.0230)


def test_build_nodes_yields_points(tiny_graph: nx.MultiDiGraph) -> None:
    nodes = {n.node_id: n for n in build_nodes(tiny_graph)}
    assert set(nodes) == {1, 2, 3, 4}
    assert all(isinstance(n, OsmNode) for n in nodes.values())
    pt = wkt_loads(nodes[1].geometry_wkt)
    assert (pt.x, pt.y) == (-87.68, 41.94)


def test_build_street_edges_fields(tiny_graph: nx.MultiDiGraph) -> None:
    edges = list(build_street_edges(tiny_graph))
    assert all(isinstance(e, OsmEdge) for e in edges)

    # road_id is unique
    assert len({e.road_id for e in edges}) == len(edges)

    by_pair = {frozenset((e.head_node_id, e.tail_node_id)): e for e in edges}
    a = by_pair[frozenset((1, 2))]
    assert a.name == "A St"
    assert a.highway == "residential"
    assert a.length_m > 0
    geom = wkt_loads(a.geometry_wkt)
    assert isinstance(geom, LineString)
    # EPSG:4326: coords in lng/lat range
    assert -88 < geom.coords[0][0] < -87 and 41 < geom.coords[0][1] < 42


def test_build_street_edges_collapses_osmid_list(tiny_graph: nx.MultiDiGraph) -> None:
    by_pair = {frozenset((e.head_node_id, e.tail_node_id)): e for e in build_street_edges(tiny_graph)}
    e = by_pair[frozenset((2, 3))]
    assert e.osm_id == 200  # first element, schema needs a single int
    assert e.osm_way_ids == ("200", "201")  # full list for the county LTS join
    # geometry came from the edge's curved LineString (3 vertices), not node coords
    assert len(wkt_loads(e.geometry_wkt).coords) == 3
    # no `length` attr on this edge -> length_m computed from geometry
    assert e.length_m > 0


def test_build_street_edges_collapses_name_list(tiny_graph: nx.MultiDiGraph) -> None:
    by_pair = {frozenset((e.head_node_id, e.tail_node_id)): e for e in build_street_edges(tiny_graph)}
    assert by_pair[frozenset((4, 1))].name == "C St"  # first of the list


def test_prune_to_routable_network_drops_orphans_and_islands() -> None:
    """Regression: dropping service roads left ~79k intersections that only
    touched alleys as isolated vertices, plus small disconnected islands. They
    stayed in the DB and `nearest_vertex` could snap a home/dest to one, so
    routing returned no path ("sometimes no routes"). Pruning to the largest
    weakly-connected component (after removing service edges) must drop both the
    service-only orphans AND the islands, leaving one fully-routable graph."""
    g = nx.MultiDiGraph()
    g.graph["crs"] = "epsg:4326"
    # Main routable component: 1-2-3 (residential)
    for n, (x, y) in {1: (-87.68, 41.94), 2: (-87.67, 41.94), 3: (-87.67, 41.95)}.items():
        g.add_node(n, x=x, y=y)
    g.add_edge(1, 2, osmid=10, highway="residential", length=100.0)
    g.add_edge(2, 3, osmid=11, highway="residential", length=100.0)
    # Node 4 reachable ONLY via a service road (alley) -> orphaned once service drops.
    g.add_node(4, x=-87.69, y=41.94)
    g.add_edge(1, 4, osmid=12, highway="service", length=50.0)
    # Disconnected island 5-6 (residential but unreachable from the main grid).
    g.add_node(5, x=-87.60, y=41.99)
    g.add_node(6, x=-87.60, y=41.991)
    g.add_edge(5, 6, osmid=13, highway="residential", length=80.0)

    pruned = prune_to_routable_network(g)

    node_ids = {n.node_id for n in build_nodes(pruned)}
    assert node_ids == {1, 2, 3}  # service-only orphan (4) and island (5,6) dropped
    assert nx.is_weakly_connected(pruned)  # single routable component, no isolated vertices
    # No service edges survive.
    assert all(e.highway != "service" for e in build_street_edges(pruned))


def test_build_street_edges_drops_service_roads(tiny_graph: nx.MultiDiGraph) -> None:
    """Service roads (alleys, driveways, parking aisles) are excluded from the
    network. They aren't useful bike routes;
    left in, they dominated the map as tier-3 clutter (~half of all streets)."""
    tiny_graph.add_node(5, x=-87.69, y=41.94)
    tiny_graph.add_edge(1, 5, osmid=500, highway="service", name="Alley", length=100.0)
    edges = list(build_street_edges(tiny_graph))
    assert all(e.highway != "service" for e in edges)
    assert 500 not in {e.osm_id for e in edges}
    # the four real streets remain
    assert len(edges) == 4


def test_build_street_edges_dedupes_reverse_direction(tiny_graph: nx.MultiDiGraph) -> None:
    edges = list(build_street_edges(tiny_graph))
    # 4 undirected streets even though 1<->2 appears in both directions
    assert len(edges) == 4
    pairs = [frozenset((e.head_node_id, e.tail_node_id)) for e in edges]
    assert len(pairs) == len(set(pairs))


def test_edge_node_ids_consistent_with_nodes(tiny_graph: nx.MultiDiGraph) -> None:
    """Every edge endpoint must be a real node so the router can traverse."""
    node_ids = {n.node_id for n in build_nodes(tiny_graph)}
    for e in build_street_edges(tiny_graph):
        assert e.head_node_id in node_ids
        assert e.tail_node_id in node_ids
    # the fixture graph is connected (single routable component)
    assert nx.is_weakly_connected(tiny_graph)
