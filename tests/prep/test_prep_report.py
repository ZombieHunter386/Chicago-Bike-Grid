import datetime as dt
from pathlib import Path

from prep.reporting.prep_report import (
    SourceRunSummary,
    build_prep_report,
)


def test_prep_report_includes_per_source_status_and_deltas(tmp_path: Path) -> None:
    runs = [
        SourceRunSummary(
            name="hin",
            status="OK",
            record_count=1234,
            previous_record_count=1200,
            warnings=[],
        ),
        SourceRunSummary(
            name="cdot_facilities",
            status="WARN",
            record_count=400,
            previous_record_count=420,
            warnings=["3 rows missing geometry"],
        ),
        SourceRunSummary(
            name="mellow",
            status="OK",
            record_count=80000,
            previous_record_count=None,
            warnings=[],
        ),
    ]

    md = build_prep_report(
        run_started_at=dt.datetime(2026, 5, 5, 14, 0, 0, tzinfo=dt.UTC),
        run_finished_at=dt.datetime(2026, 5, 5, 15, 30, 0, tzinfo=dt.UTC),
        sources=runs,
        lts_diff_path=tmp_path / "lts_diff.md",
        hin_match_report_path=tmp_path / "hin_match_report.md",
    )

    assert "Prep Report" in md
    assert "2026-05-05" in md
    assert "1234" in md
    assert "+34" in md
    assert "WARN" in md
    assert "3 rows missing geometry" in md
    assert "first run" in md.lower()


def test_report_includes_lts_match_rate_when_provided() -> None:
    report = build_prep_report(
        run_started_at=dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC),
        run_finished_at=dt.datetime(2026, 7, 29, 12, 5, tzinfo=dt.UTC),
        sources=[],
        lts_matched_edges=9_000,
        lts_fallback_edges=1_000,
    )
    assert "## LTS way-ID match rate" in report
    assert "9000" in report.replace(",", "")
    assert "90.0%" in report


def test_report_omits_match_rate_section_when_absent() -> None:
    report = build_prep_report(
        run_started_at=dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC),
        run_finished_at=dt.datetime(2026, 7, 29, 12, 5, tzinfo=dt.UTC),
        sources=[],
    )
    assert "match rate" not in report.lower()
