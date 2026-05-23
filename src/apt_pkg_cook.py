"""Cook for the [apt_pkg] section — package install/upgrade via nala.

Drives nala (parallel downloads + `nala history undo` for rollback):
  nala update         refresh the cache (third-party repos are already in
                      place — the [apt_repo] cook ran first)
  policy check        fail fast before full-upgrade if any requested package
                      has apt-cache priority 0 (not available in any repo)
  nala full-upgrade   bring the whole system current
  nala install        install/upgrade the requested packages
  nala autoremove     drop now-unused dependencies

Cross-repo safety, the trusted.gpg.d immutable bit, the DPkg pre/post unlock
hook, debconf, and prerequisites are all set up upstream in [bash.*] entries;
this cook only does the package transaction. It logs the trusted.gpg.d
attributes after full-upgrade as a check that the hardening survived the run.

Runs as root; chef spawns it under sudo. Depends on [bash] (prereqs +
hardening + pin + debconf) and [apt_repo] (third-party repos).
"""

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from cook_base import CookBase, Result, VersionInfo, VersionStatus, main_for
from harness import log_toon, stream_subprocess

TRUSTED_GPGD = Path("/etc/apt/trusted.gpg.d")


def nala(*args: str, note: str = "", check: bool = True) -> None:
    stream_subprocess(["nala", *args], note=note, check=check)


def build_policy_row(package: str) -> dict:
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
    # priority stays int so TOON emits it unquoted; 0 means "no match found".
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


class AptPkgCook(CookBase):
    needs_root = True
    manager = "apt"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.packages: list[str] = section.get("packages", [])

    def install_or_update(self) -> Result:
        nala("update", note="Refreshing apt cache")
        # `nala list --upgradable` exits 1 when nothing matches (grep convention).
        nala("list", "--upgradable", note="Upgradable packages:", check=False)

        rows = [build_policy_row(p) for p in self.packages]
        log_toon(
            rows,
            note="Verification — installed/candidate versions and effective pin priorities:",
        )
        # Fail fast before full-upgrade: priority 0 = package not found in any configured repo.
        # Cheaper than letting nala discover it half a minute into the install transaction.
        if missing := [r["package"] for r in rows if r["priority"] == 0]:
            return Result(
                "hard_fail",
                f"package(s) not available in any configured repo: {', '.join(missing)}\n"
                "  - Check release-specific naming (e.g. libva-nvidia-driver -> nvidia-vaapi-driver on Ubuntu 26.04+).\n"
                "  - Confirm the package's component is enabled (main / universe / multiverse / restricted).\n"
                "  - For a third-party package, confirm its [apt_repo.<name>] subtable is in recipe.toml.",
            )

        nala("full-upgrade", "-y", note="Running nala full-upgrade")

        stream_subprocess(
            ["lsattr", "-d", str(TRUSTED_GPGD)],
            note=f"{TRUSTED_GPGD} attributes (expect 'i' set):",
        )

        if self.packages:
            nala(
                "install",
                "-y",
                *self.packages,
                note=f"Installing packages: {' '.join(self.packages)}",
            )

        nala("autoremove", "-y", note="Removing unused packages with nala autoremove")
        logger.info(f"Done. Installed/upgraded {len(self.packages)} package(s).")
        return Result("ok", changed=True)

    def show_version(self) -> list[VersionInfo]:
        rows: list[VersionInfo] = []
        for package in self.packages:
            policy = build_policy_row(package)
            installed = policy["installed"]
            candidate = policy["candidate"]
            status: VersionStatus
            if installed == "(none)":
                status = "missing"
            elif installed != candidate:
                status = "needs_update"
            else:
                status = "installed"
            rows.append(
                VersionInfo(
                    name=package,
                    installed_version=installed,
                    available_version=candidate,
                    source=policy["source"],
                    status=status,
                    cook=self.cook_name,
                    manager=self.manager,
                )
            )
        return rows


if __name__ == "__main__":
    main_for(AptPkgCook)
