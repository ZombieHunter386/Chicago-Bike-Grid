# prep/graph/pbf_extract.py
"""Build the OSM street graph from a Geofabrik extract instead of Overpass.

Why not Overpass: osmnx tiles any bbox above its query limit, so the Cook
County target (3.2x the old Chicago box) fanned out into dozens of Overpass
requests. Partway through the first county build, overpass-api.de began
answering with TCP RSTs — verified afterwards as a genuine remote refusal
(``Connection refused`` from both of its IPs after real round-trips, while
other hosts on the same machine answered normally). Overpass exists for small
ad-hoc queries; pulling an entire county is what regional extracts are for,
and doing it over Overpass will keep earning bans on every rebuild.

Why this still uses osmnx: the pipeline downstream depends on osmnx's exact
graph semantics — simplification collapses interstitial nodes, ``retain_all``
picks the largest component, and the resulting edge identity feeds
``road_id`` and the whole DB shape. ``ox.graph_from_xml`` runs that identical
pipeline over a local file, so switching the *source* does not change the
*topology*. A ground-up pyosmium graph build would have changed both at once.

The pipeline (see ``build_graph_from_pbf``):

  1. Download ``<region>-latest.osm.pbf`` from Geofabrik once, cached on disk.
  2. ``osmium extract -b`` clips it to the target bbox, keeping node-reference
     completeness (ways crossing the edge keep the nodes they need).
  3. ``osmium tags-filter w/highway`` drops everything that is not a road —
     buildings and landuse are the bulk of OSM and osmnx would discard them
     anyway, but only after paying to parse them as XML.
  4. ``osmium cat -o .osm`` converts to the uncompressed XML osmnx reads.
  5. ``ox.graph_from_xml(simplify=True, retain_all=False)`` — same arguments
     ``build_graph_from_bbox`` passed, so the graph is equivalent.

``osmium`` is a prep-only prerequisite (``brew install osmium-tool``); the
deployed image never runs prep, so this adds nothing to the runtime.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

GEOFABRIK_URL_TEMPLATE = (
    "https://download.geofabrik.de/north-america/us/{region}-latest.osm.pbf"
)
DEFAULT_REGION = "illinois"
# Geofabrik rebuilds nightly. Re-downloading a 355 MB file on every prep run is
# wasteful and rude; reuse anything younger than this.
PBF_MAX_AGE_DAYS = 7


class OsmiumNotInstalledError(RuntimeError):
    """Raised when the osmium CLI is missing, with the install command."""


def _require_osmium() -> str:
    path = shutil.which("osmium")
    if path is None:
        raise OsmiumNotInstalledError(
            "osmium-tool is required to build the graph from a Geofabrik "
            "extract. Install it with:  brew install osmium-tool   "
            "(Linux: apt-get install osmium-tool). It is needed only for the "
            "prep pipeline, not to run the web app."
        )
    return path


def _run(cmd: list[str]) -> None:
    """Run an osmium subcommand, surfacing stderr on failure.

    osmium writes progress to stderr and returns non-zero on real errors, so
    the message is only interesting when the exit code is bad.
    """
    logger.info("running: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd[:2])} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:500]}"
        )


def download_region_pbf(
    dest_dir: Path,
    region: str = DEFAULT_REGION,
    max_age_days: int = PBF_MAX_AGE_DAYS,
    timeout: float = 600.0,
) -> Path:
    """Download the Geofabrik extract for ``region``, reusing a fresh copy.

    Streams to a ``.part`` file and renames on completion, so an interrupted
    download can never be mistaken for a complete one on the next run.
    """
    import time

    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{region}-latest.osm.pbf"

    if target.exists():
        age_days = (time.time() - target.stat().st_mtime) / 86400
        if age_days < max_age_days:
            logger.info(
                "reusing %s (%.1f days old, %.0f MB)",
                target.name, age_days, target.stat().st_size / 1e6,
            )
            return target
        logger.info("%s is %.1f days old; re-downloading", target.name, age_days)

    url = GEOFABRIK_URL_TEMPLATE.format(region=region)
    part = target.with_suffix(target.suffix + ".part")
    logger.info("downloading %s", url)
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with part.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    part.replace(target)
    logger.info("downloaded %.0f MB to %s", target.stat().st_size / 1e6, target)
    return target


def clip_and_filter_pbf(
    source_pbf: Path,
    target_bbox: tuple[float, float, float, float],
    work_dir: Path,
) -> Path:
    """Clip to bbox, keep only highway ways, and emit uncompressed .osm XML.

    ``target_bbox`` is (min_lat, max_lat, min_lng, max_lng) — the same shape
    as ``TargetConfig.bbox`` — while osmium wants W,S,E,N.
    """
    osmium = _require_osmium()
    work_dir.mkdir(parents=True, exist_ok=True)
    min_lat, max_lat, min_lng, max_lng = target_bbox
    bbox_arg = f"{min_lng},{min_lat},{max_lng},{max_lat}"

    clipped = work_dir / "target-clipped.osm.pbf"
    highways = work_dir / "target-highways.osm.pbf"
    xml_out = work_dir / "target-highways.osm"

    # --overwrite so a re-run after a failure doesn't need manual cleanup.
    _run([osmium, "extract", "-b", bbox_arg, str(source_pbf),
          "-o", str(clipped), "--overwrite"])
    # w/highway keeps ways carrying a highway tag *and* the nodes they
    # reference, which is what osmnx needs to build geometry.
    _run([osmium, "tags-filter", str(clipped), "w/highway",
          "-o", str(highways), "--overwrite"])
    _run([osmium, "cat", str(highways), "-o", str(xml_out), "--overwrite"])

    logger.info(
        "clipped %.0f MB -> highways %.0f MB -> xml %.0f MB",
        source_pbf.stat().st_size / 1e6,
        highways.stat().st_size / 1e6,
        xml_out.stat().st_size / 1e6,
    )
    # The intermediates are large; the XML is the only input osmnx needs.
    clipped.unlink(missing_ok=True)
    highways.unlink(missing_ok=True)
    return xml_out


def build_graph_from_pbf(
    target_bbox: tuple[float, float, float, float],
    cache_dir: Path,
    region: str = DEFAULT_REGION,
    network_type: str = "bike",
):  # type: ignore[no-untyped-def]  # -> nx.MultiDiGraph
    """Geofabrik-sourced equivalent of ``build_graph_from_bbox``.

    ``simplify`` and ``retain_all`` match that function exactly so the
    resulting topology — and therefore road_id assignment downstream — is the
    same as the Overpass path produced.

    Note ``graph_from_xml`` has no ``network_type``: the tag filtering already
    restricted the file to highway ways, and osmnx applies its own way-type
    exclusions when parsing. ``prune_to_routable_network`` still runs
    afterwards in main.py and drops service roads exactly as before, so the
    final network is filtered identically.
    """
    import osmnx as ox

    from prep.osm_config import configure_osmnx

    configure_osmnx(ox)
    pbf = download_region_pbf(cache_dir, region=region)
    xml_path = clip_and_filter_pbf(pbf, target_bbox, cache_dir / "pbf_work")
    logger.info("building graph from %s", xml_path)
    return ox.graph_from_xml(str(xml_path), simplify=True, retain_all=False)
