"""VersionedCook for [skills] — Claude Code skills fetched from GitHub repos via the `skills` CLI (skills.sh), run through `bunx`. Each requested repo is (re)installed with `skills add`; the CLI's own ~/.agents/.skill-lock.json tracks an updatedAt timestamp per installed skill, so an unchanged repo reports back as unchanged even though `add` reruns every pass — same install-if-missing/refresh-if-present shape as [url]. The report row is per repo (the CLI has no per-skill semver to show), but each sync logs a per-skill new/updated/unchanged breakdown read from the lockfile before and after. A "cli"-kind skill (e.g. peek) ships its own package.json `bin`; the skills CLI installs its files but never chmods or links that binary onto PATH, so this cook does — chmod +x plus `bun link` from the skill's own directory, best-effort and idempotent like bun_cook's node shim. Runs as the invoking user; depends on [url] (bun)."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from totchef import shell
from totchef.cook_base import EntrySpec, SyncOutcome, VersionedCook
from totchef.harness import find_binary

AGENT = "claude-code"


class SkillsConfig(EntrySpec):
    repos: list[str] = []


def lockfile_path() -> Path:
    """The `skills` CLI's own global lockfile; resolved at call time so it follows become_user's $HOME drop in a forked child."""
    return Path.home() / ".agents" / ".skill-lock.json"


def skills_dir() -> Path:
    """Where the `skills` CLI installs each skill's own files; resolved at call time, same reasoning as lockfile_path."""
    return Path.home() / ".agents" / "skills"


def lockfile_skills() -> dict[str, dict]:
    try:
        payload = json.loads(lockfile_path().read_text())
    except OSError, json.JSONDecodeError:
        return {}
    return payload.get("skills", {})


def read_repo_timestamps() -> dict[str, str]:
    """Map each installed skill's declared source repo -> the most recent updatedAt among its skills, straight from the lockfile the `skills` CLI already maintains — a change there is exactly what a rerun should report as an upgrade."""
    latest_by_source: dict[str, str] = {}
    for info in lockfile_skills().values():
        source, updated = info["source"], info["updatedAt"]
        if updated > latest_by_source.get(source, ""):
            latest_by_source[source] = updated
    return latest_by_source


def skills_for_source(skills: dict[str, dict], source: str) -> dict[str, str]:
    return {name: info["updatedAt"] for name, info in skills.items() if info.get("source") == source}


def describe_skill_changes(before: dict[str, str], after: dict[str, str]) -> str:
    """A per-skill new/updated/unchanged breakdown for one repo's sync, read from the lockfile snapshot taken before and after `skills add` ran."""
    new = sorted(set(after) - set(before))
    updated = sorted(name for name in after.keys() & before.keys() if after[name] != before[name])
    unchanged = sorted(name for name in after.keys() & before.keys() if after[name] == before[name])
    parts = [f"{label}: {', '.join(names)}" for label, names in (("new", new), ("updated", updated), ("unchanged", unchanged)) if names]
    return "; ".join(parts) if parts else "no skills found"


def bin_paths(package_json: Path) -> list[str]:
    """The script path(s) a skill's package.json declares as `bin` — a dict of {name: path} for one-or-many named binaries, or a bare string for a single binary named after the package itself."""
    try:
        bin_field = json.loads(package_json.read_text()).get("bin")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"could not read {package_json}: {exc}")
        return []
    if isinstance(bin_field, dict):
        return list(bin_field.values())
    return [bin_field] if bin_field else []


def link_cli_binary(bun: Path, name: str) -> None:
    """A "cli"-kind skill ships its own package.json `bin`; the skills CLI installs the files but never chmods or links the binary onto PATH. Mirror zyp-skills' skillman.py: chmod the script executable (git doesn't preserve the bit) and `bun link` from within the skill's own directory. Best-effort and idempotent, like bun_cook's node shim — runs on every sync, so a converged re-run restores the link if it was removed."""
    skill_dir = skills_dir() / name
    package_json = skill_dir / "package.json"
    if not package_json.exists():
        return
    for bin_path in bin_paths(package_json):
        script = skill_dir / bin_path
        if script.exists():
            script.chmod(script.stat().st_mode | 0o111)
    try:
        shell.stream([str(bun), "link"], note=f"Linking {name} CLI binary", cwd=skill_dir)
    except subprocess.CalledProcessError as exc:
        logger.warning(f"{name}: could not link CLI binary: {exc}")


class SkillsCook(VersionedCook):
    entry_model = SkillsConfig

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        config = SkillsConfig.model_validate(section)
        self.repos = config.repos
        self.hooks = (config.pre_hook, config.post_hook)

    def list_requested(self) -> list[str]:
        return self.repos

    def get_hooks(self) -> tuple[str | None, str | None]:
        return self.hooks

    def list_installed(self) -> dict[str, str]:
        return read_repo_timestamps()

    def find_latest(self, names: list[str]) -> dict[str, str | None]:
        return dict.fromkeys(names)

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> SyncOutcome:
        targets = to_install + to_upgrade
        if not targets:
            return SyncOutcome("ok")

        bunx = find_binary("bunx")
        bun = find_binary("bun")
        if not bunx or not bun:
            return SyncOutcome("hard_fail", "bun/bunx not found — the [url.bun] section must run before [skills].")

        logger.info(f"Installing/refreshing skills from {len(targets)} repo(s): " + ", ".join(targets))
        tag_width = max(len(repo) for repo in targets)
        failures: list[str] = []
        changes: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            pending = {pool.submit(self._add_one, bunx, bun, repo, tag_width): repo for repo in targets}
            for future in as_completed(pending):
                repo = pending[future]
                try:
                    changes[repo] = future.result()
                except Exception as exc:
                    failures.append(repo)
                    logger.error(f"{repo} failed: {exc}")

        if failures:
            return SyncOutcome("hard_fail", f"{len(failures)} skill repo(s) failed: " + ", ".join(failures))
        return SyncOutcome("ok", "; ".join(f"{repo} ({change})" for repo, change in changes.items()))

    @staticmethod
    def _add_one(bunx: Path, bun: Path, repo: str, tag_width: int) -> str:
        before = skills_for_source(lockfile_skills(), repo)
        shell.stream(
            [str(bunx), "skills", "add", repo, "-g", "--agent", AGENT, "--skill", "*", "-y"],
            f"[{repo:>{tag_width}}]",
            note="Installing skills",
        )
        after = skills_for_source(lockfile_skills(), repo)
        for name in after:
            link_cli_binary(bun, name)
        return describe_skill_changes(before, after)
