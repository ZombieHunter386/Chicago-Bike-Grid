from __future__ import annotations

from dataclasses import dataclass, field

from prep.joins.hin_to_osm import (
    HinIntersectionFeature,
    HinIntersectionMatch,
    HinSegmentFeature,
    HinSegmentMatch,
)


@dataclass(frozen=True)
class HinMatchReport:
    total_segments: int
    matched_segments: int
    total_intersections: int
    matched_intersections: int
    unmatched_segment_ids: list[str] = field(default_factory=list)
    unmatched_intersection_ids: list[str] = field(default_factory=list)

    @property
    def segment_match_pct(self) -> float:
        if self.total_segments == 0:
            return 100.0
        return 100.0 * self.matched_segments / self.total_segments

    @property
    def intersection_match_pct(self) -> float:
        if self.total_intersections == 0:
            return 100.0
        return 100.0 * self.matched_intersections / self.total_intersections

    @property
    def overall_match_pct(self) -> float:
        total = self.total_segments + self.total_intersections
        if total == 0:
            return 100.0
        return 100.0 * (self.matched_segments + self.matched_intersections) / total

    def to_markdown(self) -> str:
        lines = [
            "# HIN Match Report",
            "",
            f"- Segment match rate: **{self.matched_segments}/{self.total_segments} "
            f"({self.segment_match_pct:.1f}%)**",
            f"- Intersection match rate: **{self.matched_intersections}/{self.total_intersections} "
            f"({self.intersection_match_pct:.1f}%)**",
            f"- Overall match rate: **{self.overall_match_pct:.1f}%**",
            "",
            "Launch criterion (spec §6.4 #2): ≥ 95% overall match rate.",
            "",
        ]
        if self.unmatched_segment_ids:
            lines.append("## Unmatched HIN segments")
            lines.append("")
            for fid in self.unmatched_segment_ids:
                lines.append(f"- `{fid}`")
            lines.append("")
        if self.unmatched_intersection_ids:
            lines.append("## Unmatched HIN intersections")
            lines.append("")
            for fid in self.unmatched_intersection_ids:
                lines.append(f"- `{fid}`")
            lines.append("")
        return "\n".join(lines)


def build_hin_match_report(
    *,
    hin_segments: list[HinSegmentFeature],
    hin_intersections: list[HinIntersectionFeature],
    segment_matches: list[HinSegmentMatch],
    intersection_matches: list[HinIntersectionMatch],
) -> HinMatchReport:
    matched_seg_ids = {m.hin_feature_id for m in segment_matches}
    matched_int_ids = {m.hin_feature_id for m in intersection_matches}

    return HinMatchReport(
        total_segments=len(hin_segments),
        matched_segments=len(matched_seg_ids),
        total_intersections=len(hin_intersections),
        matched_intersections=len(matched_int_ids),
        unmatched_segment_ids=sorted(
            f.feature_id for f in hin_segments if f.feature_id not in matched_seg_ids
        ),
        unmatched_intersection_ids=sorted(
            f.feature_id for f in hin_intersections if f.feature_id not in matched_int_ids
        ),
    )
