"""VersionedCook for the [snap] section — snap install/refresh via snapd.

Chef decides the split from a single up-front `snap list` parse; this cook runs:
  to_install -> `snap install <name>`
  to_upgrade -> `snap refresh <name>`  (no-op + exit 0 when already current)
sequentially, because snapd serializes everything behind a global lock anyway.

`latest_available` is "—": snap has no latest-version probe without an extra
network round-trip (the plan's best-effort column). Chef derives the actual
change from the installed version moving across the refresh.

Some browsers on Ubuntu (firefox, chromium) ship *only* as snaps — the apt
packages of the same name are transitional debs that just pull the snap. Snaps
run confined, so a host-side VA-API driver may not be visible inside the
sandbox; this cook only installs, it cannot make a snap browser HW-decode video.
Strictly-confined snaps only (no --classic).

Install failure is hard (a requested browser is missing); refresh failure is
soft (the snap stays usable). Runs as root (chef runs root cooks in-process).
"""

import shutil
import subprocess

from cook_base import PackageListCook, SyncOutcome
from harness import stream_subprocess


def parse_snap_list(output: str) -> dict[str, str]:
    """Map snap name -> version from `snap list` output. The first line is a header
    (`Name  Version  Rev  …`); every other line carries the name in column 0
    and the version in column 1."""
    versions: dict[str, str] = {}
    for line in output.splitlines():
        if not line or line.startswith("Name"):
            continue
        tokens = line.split()
        versions[tokens[0]] = tokens[1] if len(tokens) > 1 else "unknown"
    return versions


def parse_installed_snaps() -> dict[str, str]:
    completed = subprocess.run(
        ["snap", "list"], capture_output=True, text=True, check=True
    )
    return parse_snap_list(completed.stdout)


class SnapCook(PackageListCook):
    needs_root = True
    manager = "snap"

    def list_installed(self) -> dict[str, str]:
        return parse_installed_snaps() if shutil.which("snap") else {}

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> SyncOutcome:
        work = [("install", n) for n in to_install] + [
            ("refresh", n) for n in to_upgrade
        ]
        if not work:
            return SyncOutcome("ok")
        if shutil.which("snap") is None:
            return SyncOutcome(
                "hard_fail", "snapd is not installed; cannot manage snaps."
            )

        tag_width = max(len(name) for _, name in work)
        install_failures: list[str] = []
        refresh_failures: list[str] = []
        for verb, name in work:
            try:
                stream_subprocess(
                    ["snap", verb, name],
                    f"[{name:>{tag_width}}]",
                    note="Installing" if verb == "install" else "Refreshing",
                )
            except subprocess.CalledProcessError:
                (install_failures if verb == "install" else refresh_failures).append(
                    name
                )

        if install_failures:
            return SyncOutcome(
                "hard_fail", f"snap install failed: {', '.join(install_failures)}"
            )
        if refresh_failures:
            return SyncOutcome(
                "soft_fail",
                f"snap refresh failed (snap stays usable): {', '.join(refresh_failures)}",
            )
        return SyncOutcome("ok")
