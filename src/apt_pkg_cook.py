"""Cook for [apt_pkg] — idempotent package install and full system upgrade.

Drives nala (parallel downloads + `nala history undo` for rollback). Refreshes
the apt cache, runs full-upgrade, installs declared packages, and autoremoving
orphans. Verifies apt-cache policy priority for every declared package before
the install step — priority 0 means the package isn't available in any
configured repo, which fails fast with a clear error rather than letting nala
discover it mid-transaction.

Depends on [apt_repo] (repos must be configured before cache refresh).
Runs as root (needs_root = true in recipe.toml).
"""

import os
import subprocess
import sys
from urllib.parse import urlparse

from loguru import logger
from toon_format import encode

from harness import (
    CookBase,
    Result,
    VersionInfo,
    load_section,
    start_log_tee,
    stream_subprocess,
)


class AptPkgCook(CookBase):
    def __init__(self, section: dict) -> None:
        self.packages: list[str] = section.get("packages", [])

    def install_or_update(self) -> Result:
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"

        _nala("update", note="Refreshing apt cache with new repos")
        # `nala list --upgradable` exits 1 when nothing matches (grep convention).
        _nala("list", "--upgradable", note="Upgradable packages:", check=False)

        if self.packages:
            rows = [_build_policy_row(p) for p in self.packages]
            _log_toon(
                rows,
                note="Verification — installed/candidate versions and effective pin priorities:",
            )
            # Fail fast before full-upgrade: priority 0 = package not found in any
            # configured repo. Cheaper than letting nala discover it mid-transaction.
            if missing := [r["package"] for r in rows if r["priority"] == 0]:
                return Result(
                    "hard_fail",
                    "package(s) not available in any configured repo: "
                    + ", ".join(missing)
                    + "\n  - Check release-specific naming.\n"
                    "  - Confirm the package's component is enabled.\n"
                    "  - For a third-party package, confirm its [apt_repo.*] entry.",
                    False,
                )

        _nala("full-upgrade", "-y", note="Running nala full-upgrade")

        if self.packages:
            _nala(
                "install",
                "-y",
                *self.packages,
                note=f"Installing packages: {' '.join(self.packages)}",
            )

        _nala("autoremove", "-y", note="Removing unused packages with nala autoremove")

        return Result(
            "ok",
            f"Installed/upgraded {len(self.packages)} package(s)",
            True,
        )

    def show_version(self) -> list[VersionInfo]:
        result: list[VersionInfo] = []
        for pkg in self.packages:
            row = _build_policy_row(pkg)
            result.append(
                VersionInfo(
                    name=pkg,
                    installed_version=row.get("installed", ""),
                    available_version=row.get("candidate", ""),
                    source=row.get("source", ""),
                    status="unknown",
                    cook="apt_pkg_cook",
                    manager="nala",
                )
            )
        return result


def _nala(*args: str, note: str = "", check: bool = True) -> None:
    stream_subprocess(["nala", *args], note=note, check=check)


def _log_toon(rows: list[dict], note: str = "") -> None:
    if note:
        logger.info(note)
    for line in encode(rows).splitlines():
        logger.info(line)


def _build_policy_row(package: str) -> dict:
    """Parse `apt-cache policy <package>` into a flat row for the TOON summary."""
    lines = subprocess.run(
        ["apt-cache", "policy", package], capture_output=True, text=True
    ).stdout.splitlines()

    def field(name: str) -> str:
        prefix = f"{name}:"
        return next(
            (
                line.split(":", 1)[1].strip()
                for line in lines
                if line.strip().startswith(prefix)
            ),
            "(none)",
        )

    candidate = field("Candidate")
    priority, source = 0, ""
    inside_candidate_section, inside_version_table = False, False
    for line in lines:
        if line.strip() == "Version table:":
            inside_version_table = True
            continue
        if not inside_version_table:
            continue
        if line.startswith("        "):
            # Skip /var/lib/dpkg/status — apt's "installed on disk" bookkeeping, not a real repo.
            if inside_candidate_section and not source:
                tokens = line.split()
                if len(tokens) >= 2 and tokens[1] != "/var/lib/dpkg/status":
                    source = urlparse(tokens[1]).hostname or tokens[1]
        else:  # version line: " *** VERSION PRIO" or "     VERSION PRIO"
            tokens = line.replace("***", "").split()
            inside_candidate_section = len(tokens) >= 2 and tokens[0] == candidate
            if inside_candidate_section:
                priority = int(tokens[1])

    return {
        "package": package,
        "installed": field("Installed"),
        "candidate": candidate,
        "priority": priority,
        "source": source,
    }


def main() -> None:
    section = load_section()
    start_log_tee()

    cook = AptPkgCook(section)

    result = cook.install_or_update()

    if result.status == "hard_fail":
        sys.exit(result.message)

    logger.info(f"Done. {result.message}.")


if __name__ == "__main__":
    main()
