from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path


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
) -> str:
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
