# prep/main.py
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shapely import wkt
from shapely.geometry import shape

from prep.config_loader import load_sources_config
from prep.db.builder import DbBuilder
from prep.db.treatments_loader import load_treatments
from prep.fetchers.base import rotate_snapshots, today_snapshot_dir
from prep.fetchers.cdot_facilities import (
    OFF_STREET_FILENAME as CDOT_OFF_STREET_FILENAME,
)
from prep.fetchers.cdot_facilities import (
    ON_STREET_FILENAME as CDOT_ON_STREET_FILENAME,
)
from prep.fetchers.cdot_facilities import (
    CdotFacilitiesFetcher,
    parse_cdot_facilities,
)
from prep.fetchers.cook_lts import (
    SNAPSHOT_FILENAME as COOK_LTS_FILENAME,
)
from prep.fetchers.cook_lts import (
    CookLtsFetcher,
    parse_cook_lts,
)
from prep.fetchers.hin import HinFetcher
from prep.fetchers.pois_cdp import CdpPoisFetcher
from prep.fetchers.pois_osm import OsmPoisFetcher
from prep.fetchers.speed_limits import SpeedLimitsFetcher
from prep.graph.osm_builder import (
    build_graph_from_bbox,
    build_nodes,
    build_street_edges,
    prune_to_routable_network,
)
from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinIntersectionMatch,
    HinSegmentFeature,
    HinSegmentMatch,
    OsmIntersection,
    OsmSegment,
    join_hin_intersections_to_osm,
    join_hin_segments_to_osm,
)
from prep.lts.ingest import (
    ingest_cdp_pois,
    ingest_osm_pois,
)
from prep.lts_network_export import export_lts_network
from prep.reporting.hin_match_report import build_hin_match_report
from prep.reporting.lts_diff import diff_lts_against_previous
from prep.reporting.prep_report import SourceRunSummary, build_prep_report
from prep.scoring.classify_network import ClassifyStats, classify_network
from prep.scoring.intersection_tiers import build_intersection_records

CODE_VERSION = "0.1.0"


def _accumulate_segment_matches(
    matches: list[HinSegmentMatch],
) -> dict[int, HinSegmentMatch]:
    """Reduce 1:N HIN→OSM segment matches to one per street record.

    Keyed on the segment record's `osm_id` field (which now carries
    road_id — see comment at OsmSegment construction site below).

    Modal flags are OR'd across matches (any match where bike=true → bike=true).
    Severity rank takes the max (worst severity wins).
    """
    out: dict[int, HinSegmentMatch] = {}
    for m in matches:
        existing = out.get(m.osm_id)
        if existing is None:
            out[m.osm_id] = m
            continue
        merged_flags = {
            "bike": existing.modal_flags.get("bike", False) or m.modal_flags.get("bike", False),
            "ped": existing.modal_flags.get("ped", False) or m.modal_flags.get("ped", False),
        }
        if existing.severity_rank is None:
            sev = m.severity_rank
        elif m.severity_rank is None:
            sev = existing.severity_rank
        else:
            sev = max(existing.severity_rank, m.severity_rank)
        out[m.osm_id] = HinSegmentMatch(
            osm_id=m.osm_id,
            hin_feature_id=existing.hin_feature_id,
            modal_flags=merged_flags,
            severity_rank=sev,
        )
    return out


def _accumulate_intersection_matches(
    matches: list[HinIntersectionMatch],
) -> dict[int, HinIntersectionMatch]:
    """Same reducer as _accumulate_segment_matches, for intersection matches."""
    out: dict[int, HinIntersectionMatch] = {}
    for m in matches:
        existing = out.get(m.osm_id)
        if existing is None:
            out[m.osm_id] = m
            continue
        merged_flags = {
            "bike": existing.modal_flags.get("bike", False) or m.modal_flags.get("bike", False),
            "ped": existing.modal_flags.get("ped", False) or m.modal_flags.get("ped", False),
        }
        if existing.severity_rank is None:
            sev = m.severity_rank
        elif m.severity_rank is None:
            sev = existing.severity_rank
        else:
            sev = max(existing.severity_rank, m.severity_rank)
        out[m.osm_id] = HinIntersectionMatch(
            osm_id=m.osm_id,
            hin_feature_id=existing.hin_feature_id,
            modal_flags=merged_flags,
            severity_rank=sev,
        )
    return out


@dataclass(frozen=True)
class PipelineResult:
    status: str  # "OK" | "WARN" | "FAIL"
    sources: list[SourceRunSummary]


def _hin_features_from_geojson(
    path: Path,
    kind: str,
) -> tuple[list[HinSegmentFeature], list[HinIntersectionFeature]]:
    """Parse hin_segments.geojson or hin_intersections.geojson into typed features.

    Field mapping is CMAP Cook County SAP Traffic Safety Analysis-specific
    (verified 2026-05-05 against AGOL item 1ee2e1bd...c938). CMAP does NOT
    publish per-mode HIN breakdowns; the segment layer has no ped/bike fields
    at all, and the intersection layer publishes only combined `PedBike_*`
    counts. Best-effort mapping:

    - Segment modal_flags: always {bike: False, ped: False}. Source has no
      modal data. Spec §4.3 modal callouts ("on the cyclist HIN") are not
      reachable for segments — generic "on the Cook County HIN" only.
    - Segment severity_rank: Sum_of_KA_Crashes (count of fatal + serious
      injury crashes). Higher = worse. Not a discrete rank but works for
      max-aggregation in `_accumulate_segment_matches`.
    - Intersection modal_flags: bike=ped=True iff PedBike_Fatalities>0 OR
      PedBike_A_injuries>0 (best proxy — we know some vulnerable user was
      hit, but CMAP doesn't tell us which mode). Otherwise both False.
    - Intersection severity_rank: HIN_Intx_CPM_Rank (CMAP's collision
      priority metric rank, integer).
    """
    if not path.exists():
        return [], []
    data = json.loads(path.read_text())
    segs: list[HinSegmentFeature] = []
    ints: list[HinIntersectionFeature] = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        geom = shape(feat["geometry"])
        fid = str(props.get("OBJECTID") or props.get("GlobalID") or props.get("id"))
        if kind == "segment":
            segs.append(HinSegmentFeature(
                feature_id=fid,
                geometry=geom,
                modal_flags={"bike": False, "ped": False},
                severity_rank=props.get("Sum_of_KA_Crashes"),
            ))
        else:
            pb_fatal = props.get("PedBike_Fatalities") or 0
            pb_ainjury = props.get("PedBike_A_injuries") or 0
            vulnerable_hit = pb_fatal > 0 or pb_ainjury > 0
            ints.append(HinIntersectionFeature(
                feature_id=fid,
                geometry=geom,
                modal_flags={"bike": vulnerable_hit, "ped": vulnerable_hit},
                severity_rank=props.get("HIN_Intx_CPM_Rank"),
            ))
    return segs, ints


def run_pipeline(
    *,
    config_path: Path,
    cache_dir: Path,
    db_path: Path,
    treatments_dir: Path,
    report_path: Path,
) -> PipelineResult:
    """Run the full prep pipeline. All-or-nothing: failures preserve previous DB."""
    started = dt.datetime.now(dt.UTC)
    cfg = load_sources_config(config_path)
    snapshot_dir = today_snapshot_dir(cache_dir)

    sources: list[SourceRunSummary] = []

    # 1. Run all fetchers
    hin_src = cfg.sources.get("hin")
    if hin_src is not None:
        hin = HinFetcher(
            segments_url=hin_src.extra["segments_url"],
            intersections_url=hin_src.extra["intersections_url"],
        )
        r = hin.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="hin", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    cook_lts_src = cfg.sources.get("cook_lts")
    if cook_lts_src is not None:
        cook_lts = CookLtsFetcher(layer_url=cook_lts_src.extra["layer_url"])
        r = cook_lts.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="cook_lts", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    cdot_net_src = cfg.sources.get("cdot_bike_network")
    cdot_trails_src = cfg.sources.get("cdot_off_street_trails")
    if cdot_net_src is not None and cdot_trails_src is not None:
        cdot_fac = CdotFacilitiesFetcher(
            on_street_url=cdot_net_src.extra["on_street_url"],
            facility_type_field=cdot_net_src.extra["facility_type_field"],
            trails_url=cdot_trails_src.extra["trails_url"],
        )
        r = cdot_fac.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="cdot_facilities", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    speed_src = cfg.sources.get("chicago_speed_limits")
    if speed_src is not None:
        speed = SpeedLimitsFetcher(
            domain=speed_src.extra["domain"],
            dataset_id=speed_src.extra["dataset_id"],
        )
        r = speed.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="chicago_speed_limits", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    aldr_src = cfg.sources.get("cdp_alderman_offices")
    lib_src = cfg.sources.get("cdp_library_branches")
    if aldr_src is not None and lib_src is not None:
        cdp = CdpPoisFetcher(
            domain=aldr_src.extra["domain"],
            alderman_dataset_id=aldr_src.extra["dataset_id"],
            library_dataset_id=lib_src.extra["dataset_id"],
        )
        r = cdp.fetch(snapshot_dir)
        sources.append(SourceRunSummary(
            name="cdp_pois", status=r.status, record_count=r.record_count,
            previous_record_count=None, warnings=r.warnings,
        ))

    osm_pois_fetcher = OsmPoisFetcher(bbox=cfg.target.bbox)
    r = osm_pois_fetcher.fetch(snapshot_dir)
    sources.append(SourceRunSummary(
        name="osm_pois", status=r.status, record_count=r.record_count,
        previous_record_count=None, warnings=r.warnings,
    ))

    # 2. (Topology now comes from OSM in the build step below — no external runner.)

    # 3. Read previous DB's meta for delta calculation (before FAIL check).
    previous_record_counts: dict[str, int] = {}
    if db_path.exists():
        try:
            prev_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = prev_conn.execute(
                    "SELECT source, record_count FROM meta"
                ).fetchall()
                previous_record_counts = dict(rows)
            finally:
                prev_conn.close()
        except sqlite3.Error:
            previous_record_counts = {}

    sources = [
        SourceRunSummary(
            name=s.name,
            status=s.status,
            record_count=s.record_count,
            previous_record_count=previous_record_counts.get(s.name),
            warnings=list(s.warnings),
        )
        for s in sources
    ]

    # 4. All-or-nothing check
    if any(s.status == "FAIL" for s in sources):
        finished = dt.datetime.now(dt.UTC)
        report = build_prep_report(
            run_started_at=started, run_finished_at=finished, sources=sources,
        )
        report_path.write_text(report)
        return PipelineResult(status="FAIL", sources=sources)

    # 5. Build new DB to a temp path; swap atomically only on success.
    # classify_stats stays None if the build fails before classification, so the
    # report simply omits the match-rate section rather than reporting zeros.
    classify_stats: ClassifyStats | None = None
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".db", dir=db_path.parent)
    os.close(tmp_fd)
    tmp_db = Path(tmp_name)
    try:
        builder = DbBuilder(tmp_db)
        builder.create_schema()

        # Topology from OSM (osmnx); LTS 1-4 from the Cook County way-ID join,
        # with CDOT bike facilities as an improve-only override on top.
        # Prune to the routable network *after* osmnx's build: removing service
        # roads (alleys) orphans the intersections that only touched them, so we
        # re-take the largest weakly-connected component to drop those dead
        # vertices (otherwise nearest_vertex can snap onto an unroutable node).
        graph = prune_to_routable_network(build_graph_from_bbox(cfg.target.bbox))
        edges = list(build_street_edges(graph))
        nodes = list(build_nodes(graph))

        way_lts = (
            parse_cook_lts(snapshot_dir / COOK_LTS_FILENAME)
            if cook_lts_src is not None
            else {}
        )
        cdot_facilities = (
            list(parse_cdot_facilities(
                snapshot_dir / CDOT_ON_STREET_FILENAME,
                snapshot_dir / CDOT_OFF_STREET_FILENAME,
                cdot_net_src.extra["facility_type_field"],
            ))
            if cdot_net_src is not None and cdot_trails_src is not None
            else []
        )

        segs, classify_stats = classify_network(edges, way_lts, cdot_facilities)
        ints = build_intersection_records(nodes, segs)

        pois = list(ingest_osm_pois(snapshot_dir))
        pois.extend(ingest_cdp_pois(snapshot_dir))

        hin_seg_path = snapshot_dir / "hin_segments.geojson"
        hin_int_path = snapshot_dir / "hin_intersections.geojson"
        hin_segs, _ = _hin_features_from_geojson(hin_seg_path, "segment")
        _, hin_ints = _hin_features_from_geojson(hin_int_path, "intersection")

        # OsmSegment.osm_id carries the unique segment key for HIN matching — we
        # pass each edge's synthesized road_id so HIN attributes apply per-block.
        # OsmIntersection.osm_id carries the OSM node id (IntersectionRecord.osm_id).
        osm_segs = [
            OsmSegment(osm_id=s.road_id, geometry=wkt.loads(s.geometry_wkt))
            for s in segs
        ]
        osm_ints = [
            OsmIntersection(osm_id=i.osm_id, geometry=wkt.loads(i.geometry_wkt))
            for i in ints
        ]

        seg_matches = list(join_hin_segments_to_osm(
            hin_segments=hin_segs, osm_segments=osm_segs,
        ))
        int_matches = list(join_hin_intersections_to_osm(
            hin_intersections=hin_ints, osm_intersections=osm_ints,
        ))

        seg_match_map = _accumulate_segment_matches(seg_matches)
        int_match_map = _accumulate_intersection_matches(int_matches)

        builder.insert_hin_features(hin_segs, hin_ints)
        builder.insert_streets(segs, hin_matches=seg_match_map)
        builder.insert_intersections(ints, hin_matches=int_match_map)
        builder.insert_pois(pois)

        hin_report = build_hin_match_report(
            hin_segments=hin_segs,
            hin_intersections=hin_ints,
            segment_matches=seg_matches,
            intersection_matches=int_matches,
        )
        (report_path.parent / "hin_match_report.md").write_text(hin_report.to_markdown())

        load_treatments(treatments_dir, builder)

        for s in sources:
            builder.record_meta(s.name, s.record_count, s.status)
        builder.record_schema_meta(code_version=CODE_VERSION)
        builder.close()

        if db_path.exists():
            diff = diff_lts_against_previous(current_db=tmp_db, previous_db=db_path)
            (report_path.parent / "lts_diff.md").write_text(diff.to_markdown())

        shutil.move(str(tmp_db), str(db_path))

    except Exception as e:  # noqa: BLE001
        if tmp_db.exists():
            tmp_db.unlink()
        sources.append(SourceRunSummary(
            name="db_build", status="FAIL",
            record_count=0, previous_record_count=None,
            warnings=[f"build failed: {e}"],
        ))

    # 6. Export the static LTS-network artifact consumed by the /explore view.
    # Lives next to bikemap.db so the upload-db flow ships both together.
    # Only run on successful builds — if db_build FAILED, the previous bikemap.db
    # was retained and the existing lts-network.geojson.gz already matches it.
    lts_network_path = db_path.parent / "lts-network.geojson.gz"
    lts_network_size: int | None = None
    if not any(s.status == "FAIL" for s in sources) and db_path.exists():
        try:
            lts_network_size = export_lts_network(db_path, lts_network_path)
        except Exception as e:  # noqa: BLE001
            sources.append(SourceRunSummary(
                name="lts_network_export", status="WARN",
                record_count=0, previous_record_count=None,
                warnings=[f"lts-network export failed: {e}"],
            ))

    finished = dt.datetime.now(dt.UTC)
    rotate_snapshots(cache_dir, keep=3)

    overall = "OK"
    if any(s.status == "FAIL" for s in sources):
        overall = "FAIL"
    elif any(s.status == "WARN" for s in sources):
        overall = "WARN"

    report = build_prep_report(
        run_started_at=started,
        run_finished_at=finished,
        sources=sources,
        lts_diff_path=report_path.parent / "lts_diff.md",
        hin_match_report_path=report_path.parent / "hin_match_report.md",
        lts_network_size_bytes=lts_network_size,
        lts_stats=classify_stats,
    )
    report_path.write_text(report)

    return PipelineResult(status=overall, sources=sources)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bikemap prep pipeline.")
    parser.add_argument(
        "--config", type=Path, default=Path("prep/config/sources.yaml"),
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--db", type=Path, default=Path("data/bikemap.db"))
    parser.add_argument("--treatments-dir", type=Path, default=Path("treatments"))
    parser.add_argument("--report", type=Path, default=Path("prep_report.md"))
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.db.parent.mkdir(parents=True, exist_ok=True)

    result = run_pipeline(
        config_path=args.config,
        cache_dir=args.cache_dir,
        db_path=args.db,
        treatments_dir=args.treatments_dir,
        report_path=args.report,
    )

    return 0 if result.status != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
