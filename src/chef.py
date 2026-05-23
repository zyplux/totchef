#!/usr/bin/env -S uv run
"""Orchestrator for `just up`.

Reads src/recipe.toml, builds a dependency graph from each section's
`depends_on`, and walks it in topological order (file order breaks ties).
For each section it spawns src/<section>_cook.py — or src/<section>.py for the
standalone playbooks — as a subprocess (not an import, so the boundary is
preserved for Phase 2). The section's TOML slice, minus the reserved
`needs_root` / `depends_on` keys, is handed over via SYS_CONF_PY_SECTION_JSON.

Chef owns sudo elevation: a `needs_root = true` section is spawned under sudo;
a `needs_root = false` section is spawned directly. Chef refuses to run a
non-root section as root (toolchains would land under /root).

Exit-code contract: 0 success, 75 soft fail (continue), other hard fail
(abort). Soft-failed sections are listed in a final stderr banner; chef.py
also exits 75 if any soft-failed.
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

META_KEYS = ("needs_root", "depends_on")


def resolve_cook(section: str) -> Path:
    """Map a section name to its script: <section>_cook.py, falling back to
    <section>.py for the standalone playbooks (configure_gpu, configure_apps)."""
    for candidate in (SRC_DIR / f"{section}_cook.py", SRC_DIR / f"{section}.py"):
        if candidate.exists():
            return candidate
    sys.exit(
        f"ERROR: no cook for [{section}] "
        f"(expected {section}_cook.py or {section}.py in {SRC_DIR})."
    )


def plan_order(config: dict) -> list[str]:
    """Topological order of sections honoring `depends_on`; file order is the
    tiebreaker because sections are added in file order and graphlib keeps that
    stable among ready nodes."""
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for section, data in config.items():
        sorter.add(section, *data.get("depends_on", []))
    return list(sorter.static_order())


def run_cook(section: str, config: dict) -> int:
    data = config[section]
    needs_root = data.get("needs_root", False)
    cook = resolve_cook(section)

    if not needs_root and os.geteuid() == 0:
        sys.exit(
            f"ERROR: [{section}] is needs_root=false but chef is running as root; "
            "refusing — its toolchain/files would land under /root. Run `just up` "
            "as your normal user."
        )

    section_slice = {k: v for k, v in data.items() if k not in META_KEYS}
    env = {**os.environ, SECTION_ENV: json.dumps(section_slice)}

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
    sys.exit(f"\n[{section}] FAILED (exit {result.returncode}). Aborting `just up`.")


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    os.environ.setdefault(
        SHARED_LOG_ENV,
        str(LOG_DIR / f"sys-conf-py-{datetime.now():%Y%m%d-%H%M%S}.log"),
    )

    with RECIPE_TOML.open("rb") as f:
        config = tomllib.load(f)

    order = plan_order(config)
    # Validate every cook exists before doing any work or prompting for sudo.
    for section in order:
        resolve_cook(section)

    if any(config[s].get("needs_root", False) for s in order):
        subprocess.run(["sudo", "-v"], check=True)

    soft_failed: list[str] = []
    for section in order:
        if run_cook(section, config) == SOFT_FAIL_EXIT:
            soft_failed.append(section)

    if soft_failed:
        sys.stderr.write(
            f"\n=== Soft failures in: {', '.join(soft_failed)} "
            "(scroll back for details) ===\n"
        )
        sys.exit(SOFT_FAIL_EXIT)


if __name__ == "__main__":
    main()
