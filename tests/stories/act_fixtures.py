"""Act half of the prose framework: drive the chef the way a user does (`plan`/`up`/`lint`) and hand back a RunReport to assert against. Runs the real chef in-process; the only things faked are the system boundaries arranged in arrange_fixtures."""

import traceback
from collections.abc import Callable
from graphlib import TopologicalSorter

import pytest
from loguru import logger
from rich.console import Console
from typer.testing import CliRunner

from arrange_fixtures import FakeTerminal, RecipeBuilder
from assert_fixtures import CliResult, RecipeRejected, RunReport
from totchef import terminal
from totchef.cli import app, print_report
from totchef.cook_base import CookResult
from totchef.cook_runner import run_cook
from totchef.harness import SOFT_FAIL_EXIT
from totchef.recipe_graph import build_node_graph, build_nodes
from totchef.schema_lint import validate


class Totchef:
    """The user action. `plan`/`up`/`lint` drive the real chef against the current recipe, in-process: topo-sort the DAG and run each node directly (no fork, no privilege drop — the bash boundary is mocked, so nothing escalates)."""

    def __init__(self, recipe: RecipeBuilder, terminal: FakeTerminal) -> None:
        self.recipe = recipe
        self.terminal = terminal

    def declares(self, section: str, name: str | None = None, **fields) -> "Totchef":
        """Add a section/entry to this run's recipe (delegates to the recipe builder) and return self, so an independent scenario reads as one chained statement."""
        self.recipe.declares(section, name, **fields)
        return self

    def plan(self) -> RunReport:
        return self._run(dry_run=True)

    def up(self) -> RunReport:
        return self._run(dry_run=False)

    def lint(self) -> None:
        try:
            validate(self.recipe.config)
        except SystemExit as exit:
            raise RecipeRejected(str(exit.code)) from exit

    def assert_lint_rejects(self, snippet: str = "") -> None:
        """Assert the operator's recipe is refused at lint, optionally carrying `snippet` in the message that tells them how to fix it."""
        try:
            self.lint()
        except RecipeRejected as rejection:
            assert snippet in str(rejection), f"recipe was rejected, but the message {str(rejection)!r} did not mention {snippet!r}"
            return
        raise AssertionError("expected the recipe to be rejected at lint, but it validated")

    def _run(self, dry_run: bool) -> RunReport:
        config = self.recipe.config
        validate(config)
        nodes = build_nodes(config)
        order = list(TopologicalSorter(build_node_graph(nodes)).static_order())

        lines: list[str] = []
        sink = logger.add(lambda message: lines.append(message.record["message"]), format="{message}", level="INFO")
        results: dict[str, CookResult] = {}
        try:
            for node_id in order:
                try:
                    result = run_cook(nodes[node_id], config, dry_run)
                except Exception:
                    result = CookResult(node_id, "hard_fail", [], traceback.format_exc())
                results[node_id] = result
                if result.status == "hard_fail":
                    break  # chef aborts the apply on a hard failure
        finally:
            logger.remove(sink)

        for result in results.values():  # the failure lines `cli.apply` prints after the run
            if result.status in ("hard_fail", "soft_fail") and result.message:
                lines.append(f"[{result.cook}] {result.message}")

        title = "Plan" if dry_run else "Report"
        report = _capture_report(results, dry_run, title)
        terminal_report = _capture_colored_report(results, dry_run, title)
        return RunReport(results, _exit_code(results), report=report, logs="\n".join(lines), terminal_report=terminal_report)


def _capture_report(results: dict[str, CookResult], dry_run: bool, title: str) -> str:
    """Run the real `print_report` and capture the plain TOON it logs off-terminal, by attaching a temporary loguru sink around it — so the snapshot is exactly what an operator sees, not a reconstruction."""
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(message.record["message"]), format="{message}", level="INFO")
    try:
        print_report(results, dry_run, title=title)
    finally:
        logger.remove(sink)
    return "\n".join(lines)


def _capture_colored_report(results: dict[str, CookResult], dry_run: bool, title: str) -> str:
    """Render the same report as it appears on an interactive terminal — the rich, color-coded table — by forcing `terminal`'s console to a capturing, color-emitting one for the duration."""
    console = Console(force_terminal=True, color_system="standard", width=120)
    original_console, original_interactive = terminal.console, terminal.is_interactive
    terminal.console, terminal.is_interactive = (lambda: console), (lambda: True)
    try:
        with console.capture() as captured:
            print_report(results, dry_run, title=title)
        return captured.get()
    finally:
        terminal.console, terminal.is_interactive = original_console, original_interactive


def _exit_code(results: dict[str, CookResult]) -> int:
    if any(result.status == "hard_fail" for result in results.values()):
        return 1
    if any(result.status == "soft_fail" for result in results.values()):
        return SOFT_FAIL_EXIT
    return 0


@pytest.fixture
def totchef(recipe: RecipeBuilder, terminal: FakeTerminal) -> Totchef:
    return Totchef(recipe, terminal)


class Cli:
    """The operator's command line: invoke a real `totchef <command>` (the informational ones — `where`, `lint`, `--version`, `--list-cooks`) and capture what scrolled past plus the exit code. The recipe-discovery commands read the arranged filesystem/env, so a test sets those up and observes the path the CLI resolves."""

    def __init__(self) -> None:
        self._runner = CliRunner()

    def run(self, *args: str) -> CliResult:
        outcome = self._runner.invoke(app, list(args))
        stderr = outcome.stderr if outcome.stderr_bytes is not None else ""
        return CliResult(outcome.stdout + stderr, outcome.exit_code)


@pytest.fixture
def cli() -> Cli:
    return Cli()


@pytest.fixture
def scenario(terminal: FakeTerminal) -> Callable[[], Totchef]:
    """Build an independent run with its own fresh recipe — for a test that exercises several distinct recipes (e.g. a few ways a dependency can be malformed) against the same mocked system."""

    def build() -> Totchef:
        return Totchef(RecipeBuilder(), terminal)

    return build
