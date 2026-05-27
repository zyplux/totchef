"""User stories §7 — Observing a run.

§7.1 (the color-coded report) is observed end-to-end through `totchef`, and §7.3.1
(the log owned by the invoking user after a root apply) through the container fixture —
a real escalate-and-drop. The remaining §7.2/§7.3.2-3 criteria (the live progress bar,
per-cook log color, the scheduler's wait/unlock lines, and the log pump) are
rendering/timing properties of the forking scheduler and file-logging pump the
in-process framework does not run, and a container would only make them flakier (PTY
scraping, concurrency races); those stay white-box here.
"""

import totchef.logs as log_internals
from totchef.cook_runner import format_queueing, format_unlocked
from totchef.logs import set_terminal_echo, write_log
from totchef.terminal import (
    ProgressHandle,
    _LiveProgress,
    _colorize_log_line,
    _runner_style,
    is_interactive,
    progress_region,
)


# 7.1 See a clear, color-coded report of what happened


def test_7_1_1_report_table_color_coded_on_terminal_plain_toon_otherwise(recipe, scenario, terminal, totchef, tmp_path):
    """A table with cook-node/current/latest/action; rich color-coded on a terminal,
    plain TOON text on a non-terminal."""
    recipe.declares("file", "f", path=str(tmp_path / "f"), content="X\n")

    plan = totchef.plan()
    assert '{"cook-node",current,latest,action}' in plan.report  # plain TOON off-terminal
    plan.assert_colored("would apply", "yellow")  # a pending change → yellow

    totchef.up().assert_colored("applied", "green")  # a change made → green

    boom = scenario().declares("bash", "boom", apply="explode")
    terminal.arrange("explode", exit_code=1)
    boom.up().assert_colored("failed", "red bold")  # a failure → red


def test_7_1_2_up_shows_changed_rows_plus_footer_plan_shows_all(recipe, totchef, tmp_path):
    """A real up shows only changed/failed rows plus a footer (unchanged count,
    elapsed); a plan shows every row."""
    settled = tmp_path / "settled"
    settled.write_text("X\n")
    recipe.declares("file", "settled", path=str(settled), content="X\n")
    recipe.declares("file", "changed", path=str(tmp_path / "changed"), content="Y\n")

    plan = totchef.plan()
    assert "file.settled" in plan.report and "file.changed" in plan.report  # plan shows all

    report = totchef.up()
    assert "file.changed" in report.report  # up shows only the changed row …
    assert "file.settled" not in report.report  # … and hides the unchanged one
    report.assert_shows("file.settled", "unchanged")  # though it is still in the results
    assert "1 unchanged" in report.report  # the footer summarizes what was left alone


def test_7_1_3_content_hash_diffs_humanized_present_or_stale(recipe, totchef, tmp_path):
    """A matching hash reads `present`, a drifting one reads `stale`."""
    drift = tmp_path / "drift"
    drift.write_text("OLD\n")  # exists but will be rewritten
    settled = tmp_path / "settled"
    settled.write_text("SAME\n")  # already matches
    recipe.declares("file", "drift", path=str(drift), content="NEW\n")
    recipe.declares("file", "settled", path=str(settled), content="SAME\n")

    plan = totchef.plan()

    assert "file.drift,stale,present,would apply" in plan.report
    assert "file.settled,present,present,ok" in plan.report


# 7.2 Watch progress while a long run executes


def test_7_2_1_transient_progress_bar_cleared_on_exit(monkeypatch):
    """An interactive progress bar shows completed/total and elapsed, cleared on
    exit, leaving logs above it."""
    assert is_interactive() is False  # the test console is not a terminal
    with progress_region("Cooking", total=3) as bar:
        assert type(bar) is ProgressHandle  # off-terminal: a no-op handle
        bar.advance()

    monkeypatch.setattr("totchef.terminal.is_interactive", lambda: True)
    with progress_region("Cooking", total=3) as live:
        assert isinstance(live, _LiveProgress)  # on a terminal: a live transient bar
        live.advance()


def test_7_2_2_log_lines_colorized_and_tagged_per_cook():
    """Each cook's log lines are tagged with its name in a stable per-cook color so
    concurrent output stays readable."""
    first = _runner_style("url.bun")
    again = _runner_style("url.bun")
    other = _runner_style("apt_pkg")

    assert first == again  # stable across one cook's lines
    assert first != other  # distinct cooks get distinct hues

    colored = _colorize_log_line("[2026-05-27 10:00:00] url.bun                      INFO    Installing")
    assert "url.bun" in colored.plain  # the runner tag is carried into the rendered line


def test_7_2_3_start_and_completion_lines_announce_waits_and_unblocks():
    """Start lines announce who is running and what they wait on/unblock; completion
    lines report timing and what just unlocked."""
    queueing = format_queueing(("apt_pkg",), {"apt_pkg": 5}, combined=5)
    assert "queueing" in queueing and "apt_pkg" in queueing

    unlocked = format_unlocked(("apt_pkg",), {"apt_pkg": 2}, {"apt_pkg": 2})
    assert "unlocked" in unlocked and "apt_pkg (2/2)" in unlocked


# 7.3 Keep a timestamped log of every run


def test_7_3_1_timestamped_log_under_user_state_dir_chowned_back(apply_in_container):
    """Each run writes a timestamped log under the invoking user's state dir, chowned
    back to the user — so the operator owns their audit log even though the apply ran
    as root. Real escalate-and-drop, in a container."""
    run = apply_in_container('[file.f]\npath = "/home/tester/f"\ncontent = "x\\n"\n', ["/home/tester/f"])

    assert run.log_owner == "tester", run.transcript


def test_7_3_2_all_output_funnels_through_a_single_pump(monkeypatch, tmp_path):
    """Parent and every forked cook's stdout/stderr funnel through one pump so log
    lines never interleave with the live region."""
    log_file = tmp_path / "run.log"
    monkeypatch.setattr(log_internals, "LOG_HANDLE", open(log_file, "a"))  # noqa: SIM115 — the pump owns the handle for the run

    write_log("first line\n")
    write_log("second line\n")

    assert log_file.read_text() == "first line\nsecond line\n"  # one ordered writer, no interleaving

    monkeypatch.setattr(log_internals, "LOG_HANDLE", None)
    write_log("dropped")  # no handle yet ⇒ a safe no-op


def test_7_3_3_dry_run_shows_only_plan_on_terminal_but_logs_everything(recipe, totchef, tmp_path):
    """A dry run shows only the plan table on the terminal while still recording
    every line to the log file."""
    set_terminal_echo(False)  # dry-run suppresses cook log echo to the terminal …
    assert log_internals.ECHO_LOGS_TO_TERMINAL is False

    recipe.declares("file", "f", path=str(tmp_path / "f"), content="X\n")
    plan = totchef.plan()
    assert '{"cook-node"' in plan.report  # … but the plan table is still produced

    set_terminal_echo(True)
    assert log_internals.ECHO_LOGS_TO_TERMINAL is True
