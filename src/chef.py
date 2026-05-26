#!/usr/bin/env -S uv run
"""Orchestrator for `just up`: re-exec as root, parse recipe.toml into a graph, run the cooks, report. Exit codes: 0 ok, 75 soft fail, 1 hard fail (aborts)."""

import os
import sys
import time
import tomllib

import typer
from loguru import logger

from cook_base import CookResult
from cook_runner import format_duration, run_recipe
from harness import RECIPE_TOML, SOFT_FAIL_EXIT
from logs import SHARED_LOG_ENV, drain_logs, set_terminal_echo, start_logging
from schema_lint import validate
from terminal import show_table


def ensure_root() -> None:
    """Re-exec under sudo if not root, preserving argv and the shared log path (sudo sets SUDO_USER, which become_user drops back to)."""
    if os.geteuid() == 0:
        return
    os.execvp(
        "sudo",
        ["sudo", f"--preserve-env={SHARED_LOG_ENV}", sys.executable, *sys.argv],
    )


def cook_node(node_id: str, name: str) -> str:
    """The report's identity column: the owning cook (recipe section) dotted with the entry, matching the `section.entry` ids in the logs (e.g. `apt_pkg.code`, `url.rustup`)."""
    return f"{node_id.split('.', 1)[0]}.{name}"


def summary_rows(unchanged: int, elapsed: float | None) -> list[dict]:
    """The report's footer rows (under a divider): how many resources were left untouched and the total wall-clock — empty when there's nothing to total."""
    if not unchanged and elapsed is None:
        return []
    return [
        {
            "cook-node": f"{unchanged} unchanged" if unchanged else "elapsed",
            "current": "",
            "latest": "",
            "action": format_duration(elapsed) if elapsed is not None else "",
        }
    ]


def print_report(results: dict[str, CookResult], dry_run: bool, title: str = "Report", elapsed: float | None = None) -> None:
    rows = [(result.cook, row) for result in results.values() for row in result.rows]
    shown = rows if dry_run else [(node_id, row) for node_id, row in rows if row.changed or row.status != "ok"]

    if shown:
        show_table(
            [
                {
                    "cook-node": cook_node(node_id, row.name),
                    "current": row.installed,
                    "latest": row.latest,
                    "action": row.action,
                }
                for node_id, row in shown
            ],
            title=title,
            summary=summary_rows(len(rows) - len(shown), elapsed),
        )
    else:
        suffix = f" ({format_duration(elapsed)})" if elapsed is not None else ""
        logger.info(f"=== {title}: nothing changed{suffix} ===")


def preview_plan(config: dict) -> None:
    """Before a real run, print the plan table to the terminal from a probe-only pass; the probe's cook logs go to the file only, so the terminal shows just the table."""
    set_terminal_echo(False)
    results = run_recipe(config, dry_run=True)
    drain_logs()
    set_terminal_echo(True)
    print_report(results, dry_run=True, title="Plan")


def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Probe only; print the report without acting."),
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

    if not dry_run:
        ensure_root()
    start_logging(echo_to_terminal=not dry_run)
    start = time.monotonic()

    with RECIPE_TOML.open("rb") as f:
        config = tomllib.load(f)
    validate(config)

    if not dry_run:
        preview_plan(config)

    results = run_recipe(config, dry_run)
    drain_logs()
    set_terminal_echo(True)
    print_report(results, dry_run, elapsed=time.monotonic() - start)

    hard = [r.cook for r in results.values() if r.status == "hard_fail"]
    soft = [r.cook for r in results.values() if r.status == "soft_fail"]
    for result in results.values():
        if result.status == "hard_fail" and result.message:
            logger.error(f"[{result.cook}] {result.message}")
    if hard:
        logger.error(f"=== Hard failures: {', '.join(hard)} — `just up` aborted ===")
        drain_logs()
        raise typer.Exit(1)
    if soft:
        logger.warning(f"=== Soft failures: {', '.join(soft)} (scroll back) ===")
        drain_logs()
        raise typer.Exit(SOFT_FAIL_EXIT)
    drain_logs()


if __name__ == "__main__":
    typer.run(main)
