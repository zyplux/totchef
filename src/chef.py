#!/usr/bin/env -S uv run
"""Orchestrator for `just up`.

Reads recipe.toml, builds a dependency DAG from each section's `depends_on`
field, and walks it via graphlib.TopologicalSorter. For each section:
  - needs_root=true  → spawns cook via sudo (chef owns elevation)
  - needs_root=false → spawns cook directly; refuses if chef is running as root

`needs_root` and `depends_on` are stripped from the section data before
serialising to SYS_CONF_PY_SECTION_JSON so cooks never see orchestration
metadata.

After recipe sections, STANDALONE_PLAYBOOKS run unconditionally (configure_gpu
and configure_apps manage their own sudo internally; they move to recipe.toml
in Phase 1.5).

Exit-code contract: 0 success, 75 soft fail (continue), other hard fail
(abort). Soft-failed sections are listed in a final stderr banner.
"""

import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime
from graphlib import TopologicalSorter
from pathlib import Path

from harness import LOG_DIR, RECIPE_TOML, SECTION_ENV, SHARED_LOG_ENV, SOFT_FAIL_EXIT

SRC_DIR = Path(__file__).resolve().parent

STANDALONE_PLAYBOOKS = [
    "configure_gpu.py",
    "configure_apps.py",
]

_ORCHESTRATION_KEYS = frozenset({"needs_root", "depends_on"})


def run_cook(cook: Path, env: dict[str, str], label: str, needs_root: bool) -> int:
    is_root = os.geteuid() == 0
    if needs_root:
        cmd = (
            [sys.executable, str(cook)]
            if is_root
            else [
                "sudo",
                f"--preserve-env={SHARED_LOG_ENV},{SECTION_ENV}",
                sys.executable,
                str(cook),
            ]
        )
    else:
        if is_root:
            sys.exit(
                f"ERROR: chef is running as root but [{label}] has needs_root=false. "
                "Refusing to run a user-scope cook as root."
            )
        cmd = [sys.executable, str(cook)]

    result = subprocess.run(cmd, env=env)
    if result.returncode in (0, SOFT_FAIL_EXIT):
        return result.returncode
    sys.exit(f"\n[{label}] FAILED (exit {result.returncode}). Aborting `just up`.")


def run_standalone(cook: Path, label: str) -> int:
    result = subprocess.run([sys.executable, str(cook)], env=os.environ.copy())
    if result.returncode in (0, SOFT_FAIL_EXIT):
        return result.returncode
    sys.exit(f"\n[{label}] FAILED (exit {result.returncode}). Aborting `just up`.")


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    os.environ.setdefault(
        SHARED_LOG_ENV,
        str(LOG_DIR / f"sys-conf-py-{datetime.now():%Y%m%d-%H%M%S}.log"),
    )
    subprocess.run(["sudo", "-v"], check=True)

    with RECIPE_TOML.open("rb") as f:
        config = tomllib.load(f)

    graph: dict[str, set[str]] = {
        name: set(data.get("depends_on", [])) for name, data in config.items()
    }
    ts = TopologicalSorter(graph)
    ts.prepare()

    soft_failed_sections: list[str] = []

    while ts.is_active():
        for section_name in ts.get_ready():
            section_data = config[section_name]
            needs_root = section_data.get("needs_root", False)
            cook = SRC_DIR / f"{section_name}_cook.py"
            if not cook.exists():
                sys.exit(f"ERROR: no cook for [{section_name}] (expected {cook}).")
            cook_data = {
                k: v for k, v in section_data.items() if k not in _ORCHESTRATION_KEYS
            }
            env = {**os.environ, SECTION_ENV: json.dumps(cook_data)}
            if run_cook(cook, env, section_name, needs_root) == SOFT_FAIL_EXIT:
                soft_failed_sections.append(section_name)
            ts.done(section_name)

    for playbook in STANDALONE_PLAYBOOKS:
        cook = SRC_DIR / playbook
        if run_standalone(cook, playbook) == SOFT_FAIL_EXIT:
            soft_failed_sections.append(playbook)

    if soft_failed_sections:
        sys.stderr.write(
            f"\n=== Soft failures in: {', '.join(soft_failed_sections)} "
            "(scroll back for details) ===\n"
        )
        sys.exit(SOFT_FAIL_EXIT)


if __name__ == "__main__":
    main()
