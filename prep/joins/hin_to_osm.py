"""HIN-to-OSM spatial join.

Joins HIN (High Injury Network) segments to OSM segments via a 10m buffer +
±30° bearing match, and HIN intersections to OSM intersection nodes via 30m
nearest-neighbor.

CRS: all input geometries are EPSG:4326 (WGS84). Internally reprojected to
EPSG:6454 (NAD83(2011) / Illinois East, metres) for accurate metric distance
and bearing math at Chicago latitude. This is the metric variant of the
Illinois State Plane referenced in spec §3.2 (the spec literally cites
EPSG:3435, but that CRS uses US survey feet — EPSG:6454 is the metric
equivalent and is what the spec author intended).

Spec reference: §3.12.
"""
from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

# WGS84 → NAD83(2011) / Illinois East (EPSG:6454, metres) for accurate metric distance/bearing math.
_TO_IL_EAST_M = Transformer.from_crs("EPSG:4326", "EPSG:6454", always_xy=True).transform

SEG_BUFFER_METERS = 10.0
SEG_BEARING_TOLERANCE_DEG = 30.0
INT_NEAREST_METERS = 30.0


@dataclass(frozen=True)
class OsmSegment:
    osm_id: int
    geometry: LineString  # in EPSG:4326


@dataclass(frozen=True)
class OsmIntersection:
    osm_id: int
    geometry: Point  # in EPSG:4326


@dataclass(frozen=True)
class HinSegmentFeature:
    feature_id: str
    geometry: LineString  # in EPSG:4326
    modal_flags: dict[str, bool]
    severity_rank: int | None


@dataclass(frozen=True)
class HinIntersectionFeature:
    feature_id: str
    geometry: Point  # in EPSG:4326
    modal_flags: dict[str, bool]
    severity_rank: int | None


@dataclass(frozen=True)
class HinSegmentMatch:
    osm_id: int
    hin_feature_id: str
    modal_flags: dict[str, bool]
    severity_rank: int | None


@dataclass(frozen=True)
class HinIntersectionMatch:
    osm_id: int
    hin_feature_id: str
    modal_flags: dict[str, bool]
    severity_rank: int | None


def _project(g: BaseGeometry) -> BaseGeometry:
    return transform(_TO_IL_EAST_M, g)


def _bearing(line: LineString, near_point: Point | None = None) -> float:
    """Return bearing in degrees of a (projected) LineString.

    For curved or multi-segment lines, the start→end chord can be misleading.
    If `near_point` is provided, returns the bearing of the line's
    sub-segment closest to that point. Otherwise falls back to start→end.

    Bearings returned mod 180° (bidirectional — direction-independent for
    matching against HIN features that may be digitized either way).
    """
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0

    if near_point is not None:
        # Find the segment whose midpoint is closest to near_point.
        best_idx = 0
        best_dist = float("inf")
        for i in range(len(coords) - 1):
            mx = (coords[i][0] + coords[i + 1][0]) / 2
            my = (coords[i][1] + coords[i + 1][1]) / 2
            d = (mx - near_point.x) ** 2 + (my - near_point.y) ** 2
            if d < best_dist:
                best_dist = d
                best_idx = i
        x0, y0 = coords[best_idx][:2]
        x1, y1 = coords[best_idx + 1][:2]
    else:
        x0, y0 = coords[0][:2]
        x1, y1 = coords[-1][:2]

    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0


def _bearing_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def join_hin_segments_to_osm(
    *,
    hin_segments: list[HinSegmentFeature],
    osm_segments: list[OsmSegment],
) -> Iterator[HinSegmentMatch]:
    """Spatial-join HIN segments to OSM segments by buffer + bearing match.

    Uses an R-tree index (shapely STRtree) for the OSM side to make this
    O((N+M) log N) instead of O(N*M). At Chicago scale (~80k OSM segments,
    ~5k HIN), the naive nested loop is prohibitive (~400M comparisons);
    indexed lookup brings it to a few seconds.
    """
    from shapely.strtree import STRtree

    if not osm_segments:
        return

    osm_proj = [(s, _project(s.geometry)) for s in osm_segments]
    osm_geoms = [g for _, g in osm_proj]
    tree = STRtree(osm_geoms)

    for hin in hin_segments:
        hin_proj = _project(hin.geometry)
        hin_buffered = hin_proj.buffer(SEG_BUFFER_METERS)
        # Use the buffer's centroid as the "near point" for bearing calculation
        # — matches OSM segment bearing at the overlap region rather than chord.
        hin_centroid = hin_proj.centroid
        hin_bearing = _bearing(hin_proj, near_point=hin_centroid)

        # Index lookup with predicate filtering: returns indices of OSM segments
        # whose actual geometry (not just bbox) intersects the buffered HIN.
        # shapely 2.x's STRtree.query(predicate="intersects") performs both the
        # bbox prefilter and the exact predicate test in one call — no need for
        # a separate `osm_geom.intersects(hin_buffered)` check after.
        candidate_idxs = tree.query(hin_buffered, predicate="intersects")

        for idx in candidate_idxs:
            osm, osm_geom = osm_proj[idx]
            osm_bearing = _bearing(osm_geom, near_point=hin_centroid)
            if _bearing_diff(hin_bearing, osm_bearing) > SEG_BEARING_TOLERANCE_DEG:
                continue
            yield HinSegmentMatch(
                osm_id=osm.osm_id,
                hin_feature_id=hin.feature_id,
                modal_flags=hin.modal_flags,
                severity_rank=hin.severity_rank,
            )


def join_hin_intersections_to_osm(
    *,
    hin_intersections: list[HinIntersectionFeature],
    osm_intersections: list[OsmIntersection],
) -> Iterator[HinIntersectionMatch]:
    """Spatial-join HIN intersections to OSM intersection nodes by nearest-neighbor.

    Uses an STRtree-backed nearest-neighbor query for O(log N) lookups.
    """
    from shapely.strtree import STRtree

    if not osm_intersections:
        return

    osm_proj = [(o, _project(o.geometry)) for o in osm_intersections]
    osm_geoms = [g for _, g in osm_proj]
    tree = STRtree(osm_geoms)

    for hin in hin_intersections:
        hin_proj = _project(hin.geometry)
        # Query the single nearest OSM intersection.
        nearest_idx = tree.nearest(hin_proj)
        osm, osm_geom = osm_proj[nearest_idx]
        d = hin_proj.distance(osm_geom)
        if d > INT_NEAREST_METERS:
            continue

        yield HinIntersectionMatch(
            osm_id=osm.osm_id,
            hin_feature_id=hin.feature_id,
            modal_flags=hin.modal_flags,
            severity_rank=hin.severity_rank,
        )
