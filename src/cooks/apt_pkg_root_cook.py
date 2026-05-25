"""VersionedCook for the [apt_pkg] section — package install/upgrade via nala.

Unlike the other versioned cooks, apt has a *cheap* latest: `apt-cache policy`
yields both the installed and candidate version in one call, so this cook fills
the report's "latest" column from the candidate.

apt is also the one cook that ignores chef's install/upgrade split and always
runs its full transaction, because `nala full-upgrade` is system-wide
maintenance (it moves packages chef never asked about) and `nala install` is the
single idempotent verb that both installs and upgrades the requested set:
  nala update         refresh the cache (third-party repos already in place)
  policy check        fail fast before full-upgrade if any requested package has
                      apt-cache priority 0 (not in any configured repo)
  nala full-upgrade   bring the whole system current
  nala install        install/upgrade the requested packages
  nala autoremove     drop now-unused dependencies
Chef still derives accurate per-package changes by re-probing installed versions
after the transaction.

Cross-repo safety, the trusted.gpg.d immutable bit, the DPkg unlock hook,
debconf, and prerequisites are set up upstream in [bash.*]; this cook only does
the package transaction. Runs as root; depends on [bash] and [apt_repo].
"""

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from cook_base import PackagesConfig, SyncOutcome, VersionedCook
from harness import stream_subprocess
from logs import log_toon

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


class AptPkgCook(VersionedCook):
    needs_root = True
    manager = "apt"
    entry_model = PackagesConfig

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.packages = PackagesConfig.model_validate(section).packages
        self._policy_cache: dict[str, dict] = {}

    def requested(self) -> list[str]:
        return self.packages

    def _policy(self, package: str) -> dict:
        # Cache within one probe pass so list_installed + latest_available share
        # a single apt-cache call per package.
        if package not in self._policy_cache:
            self._policy_cache[package] = build_policy_row(package)
        return self._policy_cache[package]

    def _fresh_policy(self, package: str) -> dict:
        self._policy_cache.pop(package, None)
        return self._policy(package)

    def list_installed(self) -> dict[str, str]:
        # Bust the cache so a probe after sync sees post-transaction versions.
        self._policy_cache.clear()
        return {
            p: row["installed"]
            for p in self.packages
            if (row := self._policy(p))["installed"] != "(none)"
        }

    def latest_available(self, names: list[str]) -> dict[str, str | None]:
        return {
            p: (None if (c := self._policy(p)["candidate"]) == "(none)" else c)
            for p in names
        }

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> SyncOutcome:
        nala("update", note="Refreshing apt cache")
        # `nala list --upgradable` exits 1 when nothing matches (grep convention).
        nala("list", "--upgradable", note="Upgradable packages:", check=False)

        rows = [self._fresh_policy(p) for p in self.packages]
        log_toon(
            rows,
            note="Verification — installed/candidate versions and effective pin priorities:",
        )
        # Fail fast before full-upgrade: priority 0 = not found in any configured repo.
        if missing := [r["package"] for r in rows if r["priority"] == 0]:
            return SyncOutcome(
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
        return SyncOutcome("ok")
