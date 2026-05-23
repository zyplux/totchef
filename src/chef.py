#!/usr/bin/env -S uv run
"""Orchestrator for `just up`.

Reads recipe.toml, builds a dependency DAG from each section's `depends_on`
field, and walks it in topological order (file order is the tiebreaker).
For each section it spawns src/<section>_cook.py as a subprocess — not an
import — so a cook's sudo-elevated work can't take down the orchestrator.

The section slice is passed via SYS_CONF_PY_SECTION_JSON. For sections with
`needs_root = true`, chef spawns the cook under sudo with --preserve-env so
the env var survives the privilege boundary.

Exit-code contract: 0 success, 75 soft fail (continue), other hard fail
(abort). Soft-failed sections are listed in a final stderr banner so they
can't get buried in scrollback; chef.py also exits 75 if any soft-failed.
"""

import graphlib
import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from harness import LOG_DIR, RECIPE_TOML, SECTION_ENV, SHARED_LOG_ENV, SOFT_FAIL_EXIT

SRC_DIR = Path(__file__).resolve().parent


def _run_section(section_name: str, section_data: dict, base_env: dict) -> int:
    cook = SRC_DIR / f"{section_name}_cook.py"
    if not cook.exists():
        sys.exit(f"ERROR: no cook for [{section_name}] (expected {cook}).")

    needs_root = section_data.get("needs_root", False)
    env = {**base_env, SECTION_ENV: json.dumps(section_data)}

    if needs_root:
        cmd = [
            "sudo",
            f"--preserve-env={SHARED_LOG_ENV},{SECTION_ENV}",
            sys.executable,
            str(cook),
        ]
    else:
        cmd = [sys.executable, str(cook)]

    result = subprocess.run(cmd, env=env)
    if result.returncode in (0, SOFT_FAIL_EXIT):
        return result.returncode
    sys.exit(
        f"\n[{section_name}] FAILED (exit {result.returncode}). Aborting `just up`."
    )


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    os.environ.setdefault(
        SHARED_LOG_ENV,
        str(LOG_DIR / f"sys-conf-py-{datetime.now():%Y%m%d-%H%M%S}.log"),
    )
    subprocess.run(["sudo", "-v"], check=True)

    with RECIPE_TOML.open("rb") as f:
        config = tomllib.load(f)

    sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    for name, data in config.items():
        sorter.add(name, *data.get("depends_on", []))
    sorter.prepare()

    soft_failed_sections: list[str] = []
    base_env = os.environ.copy()

    while sorter.is_active():
        for section_name in sorter.get_ready():
            rc = _run_section(section_name, config[section_name], base_env)
            if rc == SOFT_FAIL_EXIT:
                soft_failed_sections.append(section_name)
            sorter.done(section_name)

    if soft_failed_sections:
        sys.stderr.write(
            f"\n=== Soft failures in: {', '.join(soft_failed_sections)} "
            "(scroll back for details) ===\n"
        )
        sys.exit(SOFT_FAIL_EXIT)


if __name__ == "__main__":
    main()
