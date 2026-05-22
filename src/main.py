#!/usr/bin/env -S uv run
"""Orchestrator for `just up`.

Spawns src/<section>.py per top-level [section] in install.toml (file order
= execution order). Each loader gets its slice via SYS_CONF_PY_SECTION_JSON
env var; sudo re-exec preserves it. Subprocesses (not imports) so root-
elevated loaders can execvp into sudo without taking down the orchestrator.

Exit-code contract: 0 success, 75 soft fail (continue), other hard fail
(abort). Soft-failed sections are listed in a final stderr banner so they
can't get buried in scrollback; main.py also exits 75 if any soft-failed.

After install.toml sections, STANDALONE_PLAYBOOKS run unconditionally.
"""

import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from harness import INSTALL_TOML, LOG_DIR, SECTION_ENV, SHARED_LOG_ENV, SOFT_FAIL_EXIT

SRC_DIR = Path(__file__).resolve().parent

STANDALONE_PLAYBOOKS = [
    "configure_gpu.py",
    "configure_apps.py",
]


def run_loader(loader: Path, env: dict[str, str], label: str) -> int:
    result = subprocess.run([sys.executable, str(loader)], env=env)
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

    with INSTALL_TOML.open("rb") as f:
        config = tomllib.load(f)

    soft_failed_sections: list[str] = []

    for section_name, section_data in config.items():
        loader = SRC_DIR / f"{section_name}.py"
        if not loader.exists():
            sys.exit(f"ERROR: no loader for [{section_name}] (expected {loader}).")
        env = {**os.environ, SECTION_ENV: json.dumps(section_data)}
        if run_loader(loader, env, section_name) == SOFT_FAIL_EXIT:
            soft_failed_sections.append(section_name)

    for playbook in STANDALONE_PLAYBOOKS:
        loader = SRC_DIR / playbook
        if run_loader(loader, os.environ.copy(), playbook) == SOFT_FAIL_EXIT:
            soft_failed_sections.append(playbook)

    if soft_failed_sections:
        sys.stderr.write(
            f"\n=== Soft failures in: {', '.join(soft_failed_sections)} "
            "(scroll back for details) ===\n"
        )
        sys.exit(SOFT_FAIL_EXIT)


if __name__ == "__main__":
    main()
