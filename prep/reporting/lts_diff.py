from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LtsDiff:
    total_segments: int
    changed: list[tuple[int, int, int]] = field(default_factory=list)  # (road_id, prev_lts, curr_lts)
    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            "# LTS Regression Diff",
            "",
            f"- Total segments in current run: **{self.total_segments}**",
            f"- LTS changed (vs previous): **{len(self.changed)}**",
            f"- New segments: **{len(self.added)}**",
            f"- Removed segments: **{len(self.removed)}**",
            "",
        ]
        if self.changed:
            buckets: dict[tuple[int, int], int] = {}
            for _, prev, curr in self.changed:
                buckets[(prev, curr)] = buckets.get((prev, curr), 0) + 1
            lines.append("## LTS transitions")
            lines.append("")
            lines.append("| Previous LTS | Current LTS | Count |")
            lines.append("|---|---|---|")
            for (p, c), n in sorted(buckets.items()):
                lines.append(f"| {p} | {c} | {n} |")
            lines.append("")
        return "\n".join(lines)


def _load_lts_map(db_path: Path) -> dict[int, int]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT road_id, lts FROM streets").fetchall()
    except sqlite3.OperationalError:
        # Schema mismatch (e.g., previous DB built before road_id PK migration).
        # Treat as empty — diff will report everything as "added" (acceptable
        # one-time signal when the schema bumps).
        return {}
    finally:
        conn.close()
    return dict(rows)


def diff_lts_against_previous(*, current_db: Path, previous_db: Path) -> LtsDiff:
    curr = _load_lts_map(current_db)
    prev = _load_lts_map(previous_db)

    changed: list[tuple[int, int, int]] = []
    added: list[int] = []
    removed: list[int] = []

    for road_id, curr_lts in curr.items():
        if road_id not in prev:
            added.append(road_id)
        elif prev[road_id] != curr_lts:
            changed.append((road_id, prev[road_id], curr_lts))

    for road_id in prev:
        if road_id not in curr:
            removed.append(road_id)

    return LtsDiff(
        total_segments=len(curr),
        changed=sorted(changed),
        added=sorted(added),
        removed=sorted(removed),
    )
