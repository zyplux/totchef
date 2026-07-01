"""User stories §12 — Installing agent skills. One test per §12 criterion on the real chef in-process; system boundaries (bash, home) are faked."""


def _write_skills(home, *entries):
    """An effect simulating `skills add` writing the skills CLI's own lockfile — `entries` is the full (name, source, updatedAt) state it holds after this call, exactly as the story's next assertion expects to find it."""
    skills = ",".join('"' + name + '": {"source": "' + source + '", "updatedAt": "' + updated_at + '"}' for name, source, updated_at in entries)

    def write() -> None:
        lock_dir = home / ".agents"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / ".skill-lock.json").write_text('{"version": 3, "skills": {' + skills + "}}")

    return write


def _drop_skill_files(home, name: str, bin_path: str):
    """An effect simulating `skills add` copying a cli-kind skill's own files (package.json + its bin script) into `~/.agents/skills/<name>` — the script arrives non-executable, since git doesn't preserve that bit."""

    def write() -> None:
        skill_dir = home / ".agents" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "package.json").write_text('{"bin": "' + bin_path + '"}')
        script = skill_dir / bin_path
        script.write_text("#!/usr/bin/env python3\n")
        script.chmod(0o644)

    return write


def test_12_1_1_skills_installs_each_declared_repo_via_the_skills_cli(recipe, terminal, totchef, system, home):
    """`[skills] repos = [...]` installs each repo globally for Claude Code via `bunx skills add`."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx", "bun")
    terminal.arrange("skills add zyplux/zyp-skills", effect=_write_skills(home, ("totchef", "zyplux/zyp-skills", "2026-01-01T00:00:00Z")))

    report = totchef.up()

    report.assert_shows("skills.zyplux/zyp-skills", "installed")
    terminal.expect_ran("bunx skills add zyplux/zyp-skills -g --agent claude-code --skill '*' -y")


def test_12_1_2_skills_requires_bun_and_bunx_and_fails_hard_pointing_at_url_bun(recipe, totchef):
    """If bun or bunx is missing the run fails hard, telling the operator the [url] bun install must run first."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])

    report = totchef.up()

    report.assert_hard_failed()
    report.assert_logged("[url.bun]")


def test_12_1_3_a_repo_report_row_shows_the_most_recent_skill_timestamp(recipe, terminal, totchef, system, home):
    """A repo's report row shows the most recent `updatedAt` among its skills as a human-readable timestamp, not an opaque hash."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx", "bun")
    terminal.arrange(
        "skills add zyplux/zyp-skills",
        effect=_write_skills(
            home,
            ("totchef", "zyplux/zyp-skills", "2026-01-01T00:00:00Z"),
            ("mermaid", "zyplux/zyp-skills", "2026-01-03T00:00:00Z"),
        ),
    )

    report = totchef.up()

    assert 'skills.zyplux/zyp-skills,(none),"2026-01-03T00:00:00Z",—,installed' in report.full_table  # the later of the two skills' timestamps


def test_12_1_4_an_installed_repo_reports_unchanged_when_no_skill_timestamp_moved(recipe, terminal, totchef, system, home):
    """An already-installed repo is re-synced every run, but one whose skills' timestamps are all unchanged reports back as unchanged."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx", "bun")
    terminal.arrange("skills add zyplux/zyp-skills", effect=_write_skills(home, ("totchef", "zyplux/zyp-skills", "2026-01-01T00:00:00Z")))

    totchef.up().assert_shows("skills.zyplux/zyp-skills", "installed")
    report = totchef.up()

    report.assert_shows("skills.zyplux/zyp-skills", "unchanged")
    assert terminal.count("skills add zyplux/zyp-skills") == 2  # re-synced both times, despite reporting unchanged


def test_12_1_5_an_installed_repo_reports_upgraded_when_a_skill_timestamp_moved(recipe, terminal, totchef, system, home):
    """When any skill under a repo picks up a newer `updatedAt`, the repo is reported as upgraded."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx", "bun")
    terminal.arrange("skills add zyplux/zyp-skills", effect=_write_skills(home, ("totchef", "zyplux/zyp-skills", "2026-01-01T00:00:00Z")))
    totchef.up().assert_shows("skills.zyplux/zyp-skills", "installed")

    terminal.arrange("skills add zyplux/zyp-skills", effect=_write_skills(home, ("totchef", "zyplux/zyp-skills", "2026-01-02T00:00:00Z")))
    report = totchef.up()

    report.assert_shows("skills.zyplux/zyp-skills", "upgraded")


def test_12_1_6_the_run_log_breaks_down_which_skills_were_new_updated_or_unchanged(recipe, terminal, totchef, system, home):
    """The report row is per repo, but the run log carries per-skill detail: new, updated, and unchanged skills, read from the lockfile before and after that repo's `skills add` ran."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx", "bun")
    terminal.arrange(
        "skills add zyplux/zyp-skills",
        effect=_write_skills(
            home,
            ("totchef", "zyplux/zyp-skills", "2026-01-01T00:00:00Z"),
            ("peek", "zyplux/zyp-skills", "2026-01-01T00:00:00Z"),
        ),
    )
    totchef.up()

    terminal.arrange(
        "skills add zyplux/zyp-skills",
        effect=_write_skills(
            home,
            ("totchef", "zyplux/zyp-skills", "2026-01-02T00:00:00Z"),  # timestamp moved -> updated
            ("peek", "zyplux/zyp-skills", "2026-01-01T00:00:00Z"),  # untouched -> unchanged
            ("mermaid", "zyplux/zyp-skills", "2026-01-02T00:00:00Z"),  # new key -> new
        ),
    )
    report = totchef.up()

    report.assert_logged("new: mermaid")
    report.assert_logged("updated: totchef")
    report.assert_logged("unchanged: peek")


def test_12_1_7_a_failed_repo_reports_hard_naming_the_failed_repo(recipe, terminal, totchef, system):
    """If `skills add` fails for a repo, the run reports a hard failure naming it."""
    recipe.declares("skills", repos=["realSergiy/does-not-exist"])
    system.has("bunx", "bun")
    terminal.arrange("skills add realSergiy/does-not-exist", exit_code=1)

    report = totchef.up()

    report.assert_hard_failed()
    report.assert_logged("realSergiy/does-not-exist")


def test_12_1_8_multiple_repos_install_concurrently(recipe, terminal, totchef, system):
    """Multiple declared repos install concurrently, each via its own `skills add` invocation."""
    recipe.declares("skills", repos=["zyplux/zyp-skills", "vercel-labs/agent-skills"])
    system.has("bunx", "bun")
    terminal.expect_concurrent("skills add zyplux/zyp-skills", "skills add vercel-labs/agent-skills", parties=2)

    report = totchef.up()

    report.assert_succeeded()
    assert terminal.max_concurrent_commands == 2


def test_12_1_9_a_cli_kind_skill_binary_is_chmod_and_linked_onto_path(recipe, terminal, totchef, system, home):
    """A cli-kind skill's package.json `bin` script is chmod'd executable and `bun link`ed from its own directory, so it resolves on PATH."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx", "bun")
    lockfile = _write_skills(home, ("peek", "zyplux/zyp-skills", "2026-01-01T00:00:00Z"))
    files = _drop_skill_files(home, "peek", "peek.py")
    terminal.arrange("skills add zyplux/zyp-skills", effect=lambda: (lockfile(), files()))
    terminal.arrange("bun link")

    skill_dir = home / ".agents" / "skills" / "peek"

    totchef.up().assert_succeeded()  # installed; the binary is chmod'd and linked alongside
    terminal.expect_ran("bun link")
    assert terminal.cwd_for("bun link") == skill_dir
    assert (skill_dir / "peek.py").stat().st_mode & 0o111  # git doesn't preserve the executable bit; the cook restores it

    (skill_dir / "peek.py").chmod(0o644)  # bit dropped out of band
    totchef.up().assert_succeeded()  # converged: nothing to install, yet the binary link is restored
    assert (skill_dir / "peek.py").stat().st_mode & 0o111
