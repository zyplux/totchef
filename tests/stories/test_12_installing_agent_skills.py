"""User stories §12 — Installing agent skills. One test per §12 criterion on the real chef in-process; system boundaries (bash, home) are faked."""

import json
import threading
from pathlib import Path

_LOCK_WRITE_GUARD = threading.Lock()


def _skill_lock_entry(home: Path, source: str, skill_folder_hash: str):
    """An effect simulating `skills add` recording one skill's source repo and content hash into the CLI's own lockfile — merged, not overwritten, so two repos installing concurrently don't clobber each other's entry."""

    def write() -> None:
        lock_path = home / ".agents" / ".skill-lock.json"
        with _LOCK_WRITE_GUARD:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.loads(lock_path.read_text()) if lock_path.exists() else {"version": 3, "skills": {}}
            payload["skills"][source] = {"source": source, "skillFolderHash": skill_folder_hash}
            lock_path.write_text(json.dumps(payload))

    return write


def test_12_1_1_skills_installs_each_declared_repo_via_the_skills_cli(recipe, terminal, totchef, system, home):
    """`[skills] repos = [...]` installs each repo globally for Claude Code via `bunx skills add`."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx")
    terminal.arrange("skills add zyplux/zyp-skills", effect=_skill_lock_entry(home, "zyplux/zyp-skills", "abc123"))

    report = totchef.up()

    report.assert_shows("skills.zyplux/zyp-skills", "installed")
    terminal.expect_ran("bunx skills add zyplux/zyp-skills -g --agent claude-code --skill '*' -y")


def test_12_1_2_skills_requires_bunx_and_fails_hard_pointing_at_url_bun(recipe, totchef):
    """If bunx is missing the run fails hard, telling the operator the [url] bun install must run first."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])

    report = totchef.up()

    report.assert_hard_failed()
    report.assert_logged("[url.bun]")


def test_12_1_3_an_installed_repo_reports_unchanged_when_its_content_hash_is_stable(recipe, terminal, totchef, system, home):
    """An already-installed repo is re-synced every run, but a stable content hash reports back as unchanged."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx")
    terminal.arrange("skills add zyplux/zyp-skills", effect=_skill_lock_entry(home, "zyplux/zyp-skills", "abc123"))

    totchef.up().assert_shows("skills.zyplux/zyp-skills", "installed")
    report = totchef.up()

    report.assert_shows("skills.zyplux/zyp-skills", "unchanged")
    assert terminal.count("skills add zyplux/zyp-skills") == 2  # re-synced both times, despite reporting unchanged


def test_12_1_4_an_installed_repo_reports_upgraded_when_its_content_hash_changed(recipe, terminal, totchef, system, home):
    """When the lockfile's hash for a repo changed since the last run, the repo is reported as upgraded."""
    recipe.declares("skills", repos=["zyplux/zyp-skills"])
    system.has("bunx")
    terminal.arrange("skills add zyplux/zyp-skills", effect=_skill_lock_entry(home, "zyplux/zyp-skills", "abc123"))
    totchef.up().assert_shows("skills.zyplux/zyp-skills", "installed")

    terminal.arrange("skills add zyplux/zyp-skills", effect=_skill_lock_entry(home, "zyplux/zyp-skills", "def456"))
    report = totchef.up()

    report.assert_shows("skills.zyplux/zyp-skills", "upgraded")


def test_12_1_5_a_failed_repo_reports_hard_naming_the_failed_repo(recipe, terminal, totchef, system):
    """If `skills add` fails for a repo, the run reports a hard failure naming it."""
    recipe.declares("skills", repos=["realSergiy/does-not-exist"])
    system.has("bunx")
    terminal.arrange("skills add realSergiy/does-not-exist", exit_code=1)

    report = totchef.up()

    report.assert_hard_failed()
    report.assert_logged("realSergiy/does-not-exist")


def test_12_1_6_multiple_repos_install_concurrently(recipe, terminal, totchef, system, home):
    """Multiple declared repos install concurrently, each via its own `skills add` invocation."""
    recipe.declares("skills", repos=["zyplux/zyp-skills", "vercel-labs/agent-skills"])
    system.has("bunx")
    terminal.arrange("skills add zyplux/zyp-skills", effect=_skill_lock_entry(home, "zyplux/zyp-skills", "abc123"))
    terminal.arrange("skills add vercel-labs/agent-skills", effect=_skill_lock_entry(home, "vercel-labs/agent-skills", "def456"))
    terminal.expect_concurrent("skills add zyplux/zyp-skills", "skills add vercel-labs/agent-skills", parties=2)

    report = totchef.up()

    report.assert_succeeded()
    assert terminal.max_concurrent_commands == 2
