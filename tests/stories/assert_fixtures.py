"""Assert half of the prose framework: the run report a test inspects, plus the assertion mixins layered onto the system-boundary doubles (what bash ran, what was fetched)."""

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from totchef.cli import cook_node
from totchef.cook_base import CookResult
from totchef.harness import SOFT_FAIL_EXIT


class TerminalAssertions:
    """Assertion half of the bash double: verify what the system handed to the shell. The arrange half (arrange_fixtures.FakeTerminal) records each command into `commands`."""

    commands: list

    def expect_ran(self, match: str) -> None:
        assert any(match in command.line for command in self.commands), f"expected a command matching {match!r}, but only ran:\n{self._ran_lines()}"

    def expect_not_ran(self, match: str) -> None:
        offenders = [command.line for command in self.commands if match in command.line]
        assert not offenders, f"expected no command matching {match!r}, but ran:\n" + "\n".join(f"  {line}" for line in offenders)

    def count(self, match: str) -> int:
        """How many run/stream commands matched `match` — for asserting a step ran exactly once (a bootstrap) or fanned out per package."""
        return sum(match in command.line for command in self.commands)

    def stdin_for(self, match: str) -> bytes | str | None:
        """The stdin piped to the first command matching `match` — e.g. the installer script piped into `bash -s`, or the key bytes piped into `gpg --dearmor`."""
        return next((command.stdin for command in self.commands if match in command.line), None)

    def _ran_lines(self) -> str:
        return "\n".join(f"  {command.line}" for command in self.commands) or "  (nothing)"


class HttpAssertions:
    """Assertion half of the network double: verify what was fetched. The arrange half (arrange_fixtures.FakeHttp) records each URL into `requests`."""

    requests: list

    def expect_fetched(self, match: str) -> None:
        assert any(match in url for url in self.requests), f"expected a fetch matching {match!r}, but only fetched: {self.requests or '(nothing)'}"


class RecipeRejected(Exception):
    """Raised by `Totchef.lint` when validation rejects the recipe, carrying the message the operator would see."""


SGR_CODES = {
    "green": "32",
    "yellow": "33",
    "red": "31",
    "red bold": "1;31",
    "dim": "2",
}


@dataclass
class RunReport:
    """What `plan`/`up` produced: the chef's per-node results, plus assertion helpers phrased as the operator's expectations."""

    results: dict[str, CookResult]
    exit_code: int
    report: str = ""
    logs: str = ""
    terminal_report: str = ""
    rows: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for result in self.results.values():
            for row in result.rows:
                self.rows[cook_node(result.cook, row.name)] = row.action

    def assert_report(self, expected: str) -> None:
        """Assert the whole rendered report (the real `print_report` TOON, captured from the logs) matches `expected`, ignoring surrounding blank lines and uniform indentation so the snapshot can be written flush under the call."""
        actual = self.report.strip("\n")
        wanted = textwrap.dedent(expected).strip("\n")
        assert actual == wanted, f"report mismatch:\n--- expected ---\n{wanted}\n--- actual ---\n{actual}"

    def assert_shows(self, node: str, action: str) -> None:
        assert node in self.rows, f"no report row for {node!r}; saw {sorted(self.rows)}"
        assert self.rows[node] == action, f"expected {node!r} to show {action!r}, but it showed {self.rows[node]!r}"

    def assert_ran_before(self, earlier: str, later: str) -> None:
        """Assert the report lists `earlier` above `later` — the order totchef applied them, so a dependency is shown resolved before the resource that needs it."""
        order = list(self.rows)
        assert earlier in order, f"no report row for {earlier!r}; saw {order}"
        assert later in order, f"no report row for {later!r}; saw {order}"
        assert order.index(earlier) < order.index(later), f"expected {earlier!r} to run before {later!r}, but the report ordered them {order}"

    def assert_colored(self, text: str, color: str) -> None:
        """Assert that on a terminal the report renders `text` in `color` (the operator sees a color-coded table, not the plain TOON in `report`)."""
        needle = f"\x1b[{SGR_CODES[color]}m{text}"
        assert needle in self.terminal_report, f"expected {text!r} colored {color!r} in the terminal report, but got:\n{self.terminal_report!r}"

    def assert_logged(self, snippet: str) -> None:
        """Assert a line the operator would see scrolled past — a cook's guidance ("launch the app once"), a failure reason, a "Writing" notice — was logged during the run."""
        assert snippet in self.logs, f"expected a log line containing {snippet!r}, but the run logged:\n{self.logs or '(nothing)'}"

    def assert_succeeded(self) -> None:
        assert self.exit_code == 0, f"expected success (exit 0), got exit {self.exit_code}"

    def assert_soft_failed(self) -> None:
        assert self.exit_code == SOFT_FAIL_EXIT, f"expected soft failure (exit {SOFT_FAIL_EXIT}), got exit {self.exit_code}"

    def assert_hard_failed(self) -> None:
        assert self.exit_code == 1, f"expected hard failure (exit 1), got exit {self.exit_code}"


@dataclass
class CliResult:
    """What a `totchef <command>` invocation showed the operator: the text it printed (stdout and stderr together, as it would scroll past) and the exit code."""

    output: str
    exit_code: int

    def assert_succeeded(self) -> None:
        assert self.exit_code == 0, f"command exited {self.exit_code}:\n{self.output}"

    def assert_failed(self) -> None:
        assert self.exit_code != 0, f"expected the command to fail, but it exited 0:\n{self.output}"

    def assert_prints(self, snippet: str) -> None:
        assert snippet in self.output, f"expected the output to contain {snippet!r}, but it printed:\n{self.output or '(nothing)'}"

    def assert_output(self, expected: str) -> None:
        """Assert the whole printed output matches `expected` — a full snapshot, so a test reads as exactly what the command returns. Ignores surrounding blank lines and uniform indentation so the snapshot can sit flush under the call."""
        actual = self.output.strip("\n")
        wanted = textwrap.dedent(expected).strip("\n")
        assert actual == wanted, f"output mismatch:\n--- expected ---\n{wanted}\n--- actual ---\n{actual}"

    def assert_lists(self, section: str, *, scope: str = "", origin: str = "") -> None:
        """Assert the listing has a row for `section` with the given scope/origin — targeted, for a listing whose full text carries a run-varying value (e.g. a `local:<path>` origin) that a full snapshot can't pin."""
        line = next((line for line in self.output.splitlines() if section in line), None)
        assert line is not None, f"expected a row for {section!r}, but it listed:\n{self.output}"
        assert scope in line, f"expected {section!r} to list scope {scope!r}, but its row was {line!r}"
        assert origin in line, f"expected {section!r} to list origin {origin!r}, but its row was {line!r}"


@pytest.fixture
def read_json() -> Any:
    """Read a config file a cook produced and parse it as data, so a test asserts on the resulting values (not the exact formatting an implementation happens to emit)."""

    def read(path: Path) -> Any:
        return json.loads(Path(path).read_text())

    return read
