#!/usr/bin/env -S uv run
"""Orchestrator for `just up`: re-exec as root, parse recipe.toml into a graph
(recipe_graph), run the cooks (cook_runner), and report. Chef owns every
diff/idempotency decision; cooks only probe and act.

Exit codes: 0 success, 75 soft fail (named in a banner), 1 hard fail (aborts).
"""

import os
import sys
import tomllib
from datetime import datetime

import typer
from loguru import logger

from cook_base import CookResult
from cook_runner import execute
from harness import (
    LOG_DIR,
    RECIPE_TOML,
    SHARED_LOG_ENV,
    SOFT_FAIL_EXIT,
    log_toon,
    start_log_tee,
)
from recipe_graph import validate


def ensure_root() -> None:
    """Re-exec under sudo if not root, preserving argv and the shared log path.
    sudo sets SUDO_USER, which become_user() drops back to."""
    if os.geteuid() == 0:
        return
    os.execvp(
        "sudo",
        ["sudo", f"--preserve-env={SHARED_LOG_ENV}", sys.executable, *sys.argv],
    )


def print_report(results: dict[str, CookResult], dry_run: bool) -> None:
    all_rows = [row for result in results.values() for row in result.items]
    changed_rows = [r for r in all_rows if r.changed or r.status != "ok"]
    shown = all_rows if dry_run else changed_rows

    logger.info("")
    if shown:
        log_toon(
            [
                {
                    "name": r.name,
                    "mgr": r.manager,
                    "installed": r.installed,
                    "latest": r.latest,
                    "action": r.action,
                }
                for r in shown
            ],
            note="=== Report ===",
        )
    else:
        logger.info("=== Report: nothing changed ===")

    if not dry_run:
        unchanged = len(all_rows) - len(changed_rows)
        if unchanged:
            logger.info(
                f"{unchanged} item(s) unchanged. Run with --dry-run for the full inventory."
            )


def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Probe only; print the report without acting."
    ),
    lint: bool = typer.Option(
        False,
        "--lint",
        help="Validate recipe.toml against the cook schemas and exit; no root, no changes.",
    ),
) -> None:
    if lint:
        with RECIPE_TOML.open("rb") as f:
            validate(tomllib.load(f))
        logger.info(f"{RECIPE_TOML.name}: valid")
        return

    ensure_root()
    LOG_DIR.mkdir(exist_ok=True)
    os.environ.setdefault(
        SHARED_LOG_ENV,
        str(LOG_DIR / f"sys-conf-py-{datetime.now():%Y%m%d-%H%M%S}.log"),
    )
    start_log_tee()

    with RECIPE_TOML.open("rb") as f:
        config = tomllib.load(f)
    validate(config)

    results = execute(config, dry_run)
    print_report(results, dry_run)

    hard = [r.cook for r in results.values() if r.status == "hard_fail"]
    soft = [r.cook for r in results.values() if r.status == "soft_fail"]
    for result in results.values():
        if result.status == "hard_fail" and result.message:
            logger.error(f"[{result.cook}] {result.message}")
    if hard:
        logger.error(f"=== Hard failures: {', '.join(hard)} — `just up` aborted ===")
        raise typer.Exit(1)
    if soft:
        logger.warning(f"=== Soft failures: {', '.join(soft)} (scroll back) ===")
        raise typer.Exit(SOFT_FAIL_EXIT)


if __name__ == "__main__":
    typer.run(main)
