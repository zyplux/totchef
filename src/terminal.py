"""Terminal presentation layer: minimalist TOON in the log, pretty tables and
progress bars on the terminal.

The terminal has a single writer — the rich Console here — because every log line
(parent + forked cooks) reaches the terminal through `logs`'s log pump, which
this module registers a sink on (`_emit_log_line`). Routing logs through the same
Console that draws tables/progress lets rich interleave them: logs scroll above, a
live region redraws below, never corrupting each other.

`show_table` and `progress_region` are TTY-gated off `logs.TERMINAL_FD`'s
`is_terminal`:
- interactive terminal -> rich table / live progress bar to the terminal, with the
  table's rows appended as TOON to the log file (rich output never hits the file);
- non-terminal stdout (piped / consumed programmatically) -> plain TOON via the
  loguru path, and progress bars degrade to no-ops (the per-step log lines already
  convey progress).

Drive these from the root/in-process context only (chef, root cooks). A forked
user cook inherits the fds but must not draw to the terminal — it emits line logs
(the pump serialises those) and the parent renders after collecting results.
"""

import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from functools import cache

from rich.box import ROUNDED
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from toon_format import encode

import logs
from logs import log_toon

ACTION_STYLES = {
    "installed": "green",
    "upgraded": "green",
    "changed": "green",
    "up-to-date": "dim",
    "unchanged": "dim",
    "ok": "dim",
    "would install": "yellow",
    "would sync": "yellow",
    "would upgrade": "yellow",
    "would apply": "yellow",
    "skipped": "dim",
    "missing": "red",
    "failed": "red bold",
    "post-failed": "red",
}
QUIET_ACTIONS = {"up-to-date", "unchanged", "ok"}
RIGHT_ALIGNED = {"priority"}


@cache
def console() -> Console:
    """rich Console on the saved real-stdout fd, so its is_terminal reflects the
    actual terminal (or pipe) rather than the log pipe. Duped so the Console owns
    its handle independently of TERMINAL_FD."""
    if logs.TERMINAL_FD is None:
        return Console()
    return Console(file=os.fdopen(os.dup(logs.TERMINAL_FD), "w"))


def is_interactive() -> bool:
    return console().is_terminal


def _emit_log_line(line: str) -> None:
    """The pump's terminal sink: print a pumped log line through the Console so it
    coordinates with any live table/progress region. out() never wraps or parses
    markup, so arbitrary subprocess output passes through verbatim."""
    console().out(line.rstrip("\n"), highlight=False)


logs.LINE_SINK = _emit_log_line


def show_table(rows: list[dict], title: str = "") -> None:
    """Render rows as a rich table on an interactive terminal and TOON in the log
    file; on a non-terminal stdout, emit plain TOON to both via the loguru path."""
    if not rows or not is_interactive():
        log_toon(rows, note=title)
        return
    _render_table(rows, title)
    _append_toon(rows, title)


def _render_table(rows: list[dict], title: str) -> None:
    columns = list(rows[0])
    table = Table(
        title=title or None,
        box=ROUNDED,
        title_style="bold",
        header_style="bold cyan",
    )
    for column in columns:
        table.add_column(column, justify="right" if column in RIGHT_ALIGNED else "left")
    for row in rows:
        cells = [
            Text(str(row[column]), style=ACTION_STYLES.get(str(row[column]), ""))
            if column == "action"
            else Text(str(row[column]))
            for column in columns
        ]
        quiet = str(row.get("action", "")) in QUIET_ACTIONS
        table.add_row(*cells, style="dim" if quiet else "")
    console().print(table)


def _append_toon(rows: list[dict], title: str) -> None:
    """Append the rows as a TOON block to the log file, keeping the file minimalist
    while the terminal got rich. Shares logs.write_log (one locked file writer)."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = f"[{stamp}] {title}\n" if title else ""
    logs.write_log(head + encode(rows) + "\n")


class ProgressHandle:
    """No-op progress handle (the non-interactive yield). The live subclass drives a
    rich bar; callers advance/update through this interface regardless of TTY."""

    def advance(self, amount: int = 1) -> None: ...

    def update(self, description: str) -> None: ...


class _LiveProgress(ProgressHandle):
    def __init__(self, progress: Progress, task: TaskID) -> None:
        self._progress = progress
        self._task = task

    def advance(self, amount: int = 1) -> None:
        self._progress.advance(self._task, amount)

    def update(self, description: str) -> None:
        self._progress.update(self._task, description=description)


@contextmanager
def progress_region(description: str, total: int) -> Generator[ProgressHandle]:
    """A live progress bar on an interactive terminal (transient — cleared on exit,
    leaving only the logs that scrolled above it); a no-op handle otherwise."""
    if not is_interactive():
        yield ProgressHandle()
        return
    columns = (
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
    with Progress(*columns, console=console(), transient=True) as progress:
        task = progress.add_task(description, total=total)
        yield _LiveProgress(progress, task)
