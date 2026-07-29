from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from prep.scoring.classify_network import ClassifyStats


@dataclass(frozen=True)
class SourceRunSummary:
    name: str
    status: str  # "OK" | "WARN" | "FAIL"
    record_count: int
    previous_record_count: int | None
    warnings: list[str]


def build_prep_report(
    *,
    run_started_at: dt.datetime,
    run_finished_at: dt.datetime,
    sources: list[SourceRunSummary],
    lts_diff_path: Path | None = None,
    hin_match_report_path: Path | None = None,
    lts_network_size_bytes: int | None = None,
    lts_stats: ClassifyStats | None = None,
) -> str:
    """Render the per-run prep report as Markdown.

    ``lts_stats`` is the :class:`~prep.scoring.classify_network.ClassifyStats`
    returned by :func:`prep.scoring.classify_network.classify_network`; when
    given, the report grows an "LTS way-ID match rate" section so 2023-snapshot
    way-ID drift is visible every run. Omit it to omit the section.
    """
    duration_s = (run_finished_at - run_started_at).total_seconds()
    lines = [
        "# Prep Report",
        "",
        f"- Run started: {run_started_at.isoformat()}",
        f"- Run finished: {run_finished_at.isoformat()}",
        f"- Duration: {duration_s:.0f} seconds",
        "",
        "## Per-source status",
        "",
        "| Source | Status | Records | Δ vs previous | Warnings |",
        "|---|---|---|---|---|",
    ]
    for s in sources:
        if s.previous_record_count is None:
            delta = "first run"
        else:
            d = s.record_count - s.previous_record_count
            delta = f"{'+' if d >= 0 else ''}{d}"
        warns = f"{len(s.warnings)} warning(s)" if s.warnings else "—"
        lines.append(f"| `{s.name}` | **{s.status}** | {s.record_count} | {delta} | {warns} |")
    lines.append("")

    if lts_stats is not None:
        # Empty network reads as 0% matched / 0% fallback, not a vacuous 100%:
        # same deliberate convention as ClassifyStats.match_rate_pct (no edges
        # means the OSM fetch or the county join broke). NB HinMatchReport
        # .segment_match_pct in this same package deliberately takes the
        # OPPOSITE convention (nothing to match is a complete outcome there),
        # so the two are inconsistent on purpose — see its docstring.
        total = lts_stats.total
        matched_pct = lts_stats.match_rate_pct
        fallback_pct = (100.0 * lts_stats.fallback / total) if total else 0.0
        lines += [
            "## LTS way-ID match rate",
            "",
            f"- Edges matched to a Cook County way_id: {lts_stats.matched:,} "
            f"({matched_pct:.1f}%)",
            f"- Edges on the road-class fallback: {lts_stats.fallback:,} "
            f"({fallback_pct:.1f}%)",
            "",
            "Expect ≥ 95%. A materially lower rate means 2023-snapshot way-ID "
            "drift and the road-class fallback is carrying too much of the network.",
            "",
        ]

    has_warnings = any(s.warnings for s in sources)
    if has_warnings:
        lines.append("## Warnings detail")
        lines.append("")
        for s in sources:
            if not s.warnings:
                continue
            lines.append(f"### `{s.name}`")
            lines.append("")
            for w in s.warnings:
                lines.append(f"- {w}")
            lines.append("")

    lines.append("## Detail reports")
    lines.append("")
    if lts_diff_path is not None:
        lines.append(f"- LTS regression diff: `{lts_diff_path}`")
    if hin_match_report_path is not None:
        lines.append(f"- HIN match report: `{hin_match_report_path}`")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    lines.append("- `bikemap.db` — primary SQLite database")
    if lts_network_size_bytes is not None:
        size_mb = lts_network_size_bytes / (1024 * 1024)
        lines.append(
            f"- `lts-network.geojson.gz` — static LTS-network export ({size_mb:.2f} MB)"
        )
    lines.append("")

    failed = [s for s in sources if s.status == "FAIL"]
    if failed:
        lines.append("## ⚠ Build outcome")
        lines.append("")
        lines.append(
            f"**FAIL** — {len(failed)} source(s) failed; previous `bikemap.db` retained "
            "(all-or-nothing semantics, spec §3.9). Fix the failed source(s) and re-run."
        )
    elif any(s.status == "WARN" for s in sources):
        lines.append("## Build outcome")
        lines.append("")
        lines.append("**OK with warnings** — `bikemap.db` updated. Review warnings above.")
    else:
        lines.append("## Build outcome")
        lines.append("")
        lines.append("**OK** — `bikemap.db` updated cleanly.")

    return "\n".join(lines)
