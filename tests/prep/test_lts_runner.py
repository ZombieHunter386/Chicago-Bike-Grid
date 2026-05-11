# tests/prep/test_lts_runner.py
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prep.lts.runner import BrokenspokeRunFailed, BrokenspokeRunner


def make_runner(results_dir: Path, tmp_path: Path) -> BrokenspokeRunner:
    compose_file = tmp_path / "compose.brokenspoke.yml"
    compose_file.write_text("name: bikemap-brokenspoke\nservices: {}\n")
    return BrokenspokeRunner(
        image="ghcr.io/peopleforbikes/brokenspoke-analyzer:3.1.1",
        city_country="united states",
        city_name="chicago",
        city_state="illinois",
        city_fips="1714000",
        database_url="postgresql://postgres:postgres@postgres:5432/postgres",
        network_name="bikemap-brokenspoke_default",
        results_dir=results_dir,
        compose_file=compose_file,
    )


@patch("prep.lts.runner.subprocess.run")
def test_runner_invokes_docker_with_correct_args(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    # Pre-create the expected results dir so _resolve_results_path doesn't raise
    expected_dir = output_dir / "united-states" / "illinois" / "chicago"
    expected_dir.mkdir(parents=True)
    (expected_dir / "23.11").mkdir()

    runner = make_runner(output_dir, tmp_path)
    runner.run()

    # Expect: compose up, configure, run, export, compose down — minimum 5 calls.
    assert mock_run.call_count >= 5
    # The 'run' call must include the city + FIPS args
    found_run = False
    for call in mock_run.call_args_list:
        args = call.args[0] if call.args else call.kwargs.get("args", [])
        if isinstance(args, list) and "run" in args and "1714000" in args:
            found_run = True
            assert "united states" in args
            assert "chicago" in args
            assert "illinois" in args
            break
    assert found_run, "expected 'run' invocation with chicago FIPS not found"


@patch("prep.lts.runner.subprocess.run")
def test_runner_passes_full_environment_not_only_database_url(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    """env= must merge os.environ on EVERY subprocess call; otherwise PATH
    is empty and `docker` is not found. Regression-tests against the trap
    where someone refactors a per-call env dict and breaks PATH on later calls.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    # Pre-create the expected results dir so _resolve_results_path doesn't raise
    expected_dir = output_dir / "united-states" / "illinois" / "chicago"
    expected_dir.mkdir(parents=True)
    (expected_dir / "23.11").mkdir()

    runner = make_runner(output_dir, tmp_path)
    runner.run()

    # Verify EVERY call (compose up, configure, run, export, compose down).
    assert mock_run.call_count >= 5, f"expected ≥5 subprocess calls, got {mock_run.call_count}"
    for i, call in enumerate(mock_run.call_args_list):
        passed_env = call.kwargs.get("env")
        assert passed_env is not None, f"call {i}: subprocess.run must receive env= explicitly"
        assert "PATH" in passed_env, f"call {i}: PATH must be propagated from os.environ"
        assert passed_env.get("DATABASE_URL", "").startswith("postgresql://"), \
            f"call {i}: DATABASE_URL must be set"


@patch("prep.lts.runner.subprocess.run")
def test_runner_raises_on_nonzero_exit(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="boom")

    runner = make_runner(output_dir, tmp_path)
    with pytest.raises(BrokenspokeRunFailed) as exc:
        runner.run()
    assert "boom" in str(exc.value)


@patch("prep.lts.runner.subprocess.run")
def test_runner_raises_when_multiple_version_dirs_present(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    """If results_dir has stale dirs from a prior analyzer version, fail loudly
    rather than guessing which one is current. Otherwise lex-sort would pick
    '9.5' over '10.0' on an upgrade."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    expected_dir = output_dir / "united-states" / "illinois" / "chicago"
    expected_dir.mkdir(parents=True)
    (expected_dir / "23.11").mkdir()
    (expected_dir / "24.05").mkdir()  # second, stale version dir

    runner = make_runner(output_dir, tmp_path)
    with pytest.raises(BrokenspokeRunFailed) as exc:
        runner.run()
    assert "expected exactly one version subdir" in str(exc.value)
    assert "23.11" in str(exc.value)
    assert "24.05" in str(exc.value)


@patch("prep.lts.runner.subprocess.run")
def test_runner_returns_results_path(
    mock_run: MagicMock, output_dir: Path, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    # Pretend the export step created the expected directory tree
    expected_dir = output_dir / "united-states" / "illinois" / "chicago"
    expected_dir.mkdir(parents=True)
    (expected_dir / "23.11").mkdir()

    runner = make_runner(output_dir, tmp_path)
    results_path = runner.run()
    # Should resolve to the deepest version subdir
    assert results_path.name == "23.11"
    assert results_path.parent == expected_dir
