# prep/lts/runner.py
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class BrokenspokeRunFailed(Exception):  # noqa: N818
    pass


@dataclass
class BrokenspokeRunner:
    image: str
    city_country: str
    city_name: str
    city_state: str
    city_fips: str
    database_url: str
    network_name: str
    results_dir: Path
    compose_file: Path  # Path to docker/compose.brokenspoke.yml (Task 10a)
    compose_project: str = "bikemap-brokenspoke"  # matches the `name:` in compose.brokenspoke.yml

    def run(self) -> Path:
        """Run the full brokenspoke pipeline. Returns path to the results directory.

        Steps (per brokenspoke README, adapted for our compose file):
          1. docker compose -f <our compose> -p <project> up -d (start postgres)
          2. configure database
          3. run analysis
          4. export local (bind-mount results into container)
          5. docker compose down (always, even on failure)
        """
        # CRITICAL: env must include the user's PATH or `docker` won't be found.
        # Build env by *extending* os.environ, not replacing it.
        env = {**os.environ, "DATABASE_URL": self.database_url}

        # 1. compose up our postgres
        self._run_cmd([
            "docker", "compose",
            "-f", str(self.compose_file),
            "-p", self.compose_project,
            "up", "-d", "--wait",
        ], env)

        try:
            # 2. configure brokenspoke against postgres
            # Memory budget: total Docker allocation is divided between postgres
            # (shared_buffers etc.) and osm2pgrouting + the analyzer's working
            # set. On constrained hosts (≤8 GB Mac), giving postgres only 1 GB
            # leaves 4-5 GB for osm2pgrouting to import the full Chicago road
            # graph without OOM.
            self._run_cmd([
                "docker", "run", "--rm",
                "--network", self.network_name,
                "-e", "DATABASE_URL",
                self.image,
                "-vv", "configure", "custom", "2", "1024", "postgres",
            ], env)

            # 3. run analysis
            self._run_cmd([
                "docker", "run", "--rm",
                "--network", self.network_name,
                "-e", "DATABASE_URL",
                self.image,
                "-vv", "run", "--no-cache",
                self.city_country, self.city_name, self.city_state, self.city_fips,
            ], env)

            # 4. export local — bind-mount results dir into the container
            self.results_dir.mkdir(parents=True, exist_ok=True)
            uid_gid = f"{os.getuid()}:{os.getgid()}"
            self._run_cmd([
                "docker", "run", "--rm",
                "--network", self.network_name,
                "-u", uid_gid,
                "-v", f"{self.results_dir.resolve()}:/usr/src/app/results",
                "-e", "DATABASE_URL",
                self.image,
                "-vv", "export", "local",
                self.city_country, self.city_name, self.city_state,
            ], env)
        finally:
            # 5. compose down (always, even on failure). Log failures rather than
            # silently swallowing so we know if the volume is stuck.
            self._run_cmd([
                "docker", "compose",
                "-f", str(self.compose_file),
                "-p", self.compose_project,
                "down",
            ], env, check=False, log_failure=True)

        return self._resolve_results_path()

    def _run_cmd(
        self,
        cmd: list[str],
        env: dict[str, str],
        check: bool = True,
        log_failure: bool = False,
    ) -> None:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            msg = (
                f"command failed (exit {result.returncode}): {' '.join(cmd)}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            if check:
                raise BrokenspokeRunFailed(msg)
            if log_failure:
                logger.warning("brokenspoke teardown failure: %s", msg)

    def _resolve_results_path(self) -> Path:
        """Find the deepest version subdir under results_dir/<country>/<state>/<city>/."""
        base = (
            self.results_dir
            / self._slug(self.city_country)
            / self._slug(self.city_state)
            / self._slug(self.city_name)
        )
        if not base.exists():
            raise BrokenspokeRunFailed(f"expected results dir not found: {base}")
        version_dirs = [p for p in base.iterdir() if p.is_dir()]
        if not version_dirs:
            raise BrokenspokeRunFailed(f"no version subdirs under {base}")
        if len(version_dirs) > 1:
            raise BrokenspokeRunFailed(
                f"expected exactly one version subdir under {base}, "
                f"found {len(version_dirs)}: {sorted(p.name for p in version_dirs)}. "
                f"Clear results_dir before rerunning."
            )
        return version_dirs[0]

    @staticmethod
    def _slug(name: str) -> str:
        return name.lower().replace(" ", "-")
