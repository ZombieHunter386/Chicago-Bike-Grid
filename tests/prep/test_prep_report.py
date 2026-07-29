import datetime as dt
from pathlib import Path

from prep.reporting.prep_report import (
    SourceRunSummary,
    build_prep_report,
)
from prep.scoring.classify_network import ClassifyStats


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
        lts_stats=ClassifyStats(matched=9_000, fallback=1_000),
    )
    assert "## LTS way-ID match rate" in report
    # Assert both lines in full: a fallback percentage accidentally bound to the
    # matched one would still satisfy a bare "90.0%" check.
    assert "9000 (90.0%)" in report.replace(",", "")
    assert "1000 (10.0%)" in report.replace(",", "")


def test_report_match_rate_empty_network_reads_zero_not_full() -> None:
    """An empty network must not render as a vacuous 100% fallback rate.

    Guards the deliberate divergence from HinMatchReport's empty-means-100%
    convention — see the comment in build_prep_report.
    """
    report = build_prep_report(
        run_started_at=dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC),
        run_finished_at=dt.datetime(2026, 7, 29, 12, 5, tzinfo=dt.UTC),
        sources=[],
        lts_stats=ClassifyStats(matched=0, fallback=0),
    )
    assert "## LTS way-ID match rate" in report
    assert report.count("0 (0.0%)") == 2
    assert "100.0%" not in report


def test_report_omits_match_rate_section_when_absent() -> None:
    report = build_prep_report(
        run_started_at=dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC),
        run_finished_at=dt.datetime(2026, 7, 29, 12, 5, tzinfo=dt.UTC),
        sources=[],
    )
    assert "match rate" not in report.lower()
