"""Cook for the [snap] section — snap install/refresh via snapd.

For each entry in [snap].packages:
  not installed -> `snap install <name>`
  installed     -> `snap refresh <name>`  (no-op + exit 0 when already current)

Installed snaps are detected from a single up-front `snap list`, whose first
line is a header and whose every other line announces a snap in column 0.

Some browsers on Ubuntu (firefox, chromium) ship *only* as snaps — the apt
packages of the same name are transitional debs that just pull the snap. This
cook installs them as snaps openly rather than laundering them through apt.

Snaps run confined: a host-side VA-API driver (nvidia-vaapi-driver) is not
guaranteed to be visible inside the sandbox, so hardware video decode in a snap
browser is not something this cook can configure — it only installs.

Only strictly-confined snaps are supported; classic snaps (which need
`snap install --classic`) are out of scope until a recipe needs one.

Install failure is hard (a requested browser is missing); refresh failure is
soft (the snap stays usable at its current revision). Snapd serializes all
operations behind a global lock, so entries run sequentially in file order.

Runs as root; chef spawns it under sudo (`snap install` requires root).
"""

import shutil
import subprocess

from loguru import logger

from cook_base import CookBase, Result, VersionInfo, VersionStatus, main_for
from harness import stream_subprocess


def parse_installed_snaps() -> dict[str, str]:
    """Map snap name -> version from `snap list`. The first line is a header
    (`Name  Version  Rev  …`); every other line carries the name in column 0
    and the version in column 1."""
    completed = subprocess.run(
        ["snap", "list"], capture_output=True, text=True, check=True
    )
    versions: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line or line.startswith("Name"):
            continue
        tokens = line.split()
        versions[tokens[0]] = tokens[1] if len(tokens) > 1 else "unknown"
    return versions


class SnapCook(CookBase):
    needs_root = True
    manager = "snap"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.packages: list[str] = section.get("packages", [])

    def install_or_update(self) -> Result:
        if not self.packages:
            return Result(
                "ok", "No [snap].packages entries in recipe.toml; nothing to do"
            )
        if shutil.which("snap") is None:
            return Result("hard_fail", "snapd is not installed; cannot manage snaps.")

        installed = parse_installed_snaps()
        tag_width = max(len(name) for name in self.packages)

        install_failures: list[str] = []
        refresh_failures: list[str] = []
        for name in self.packages:
            present = name in installed
            verb = "refresh" if present else "install"
            try:
                stream_subprocess(
                    ["snap", verb, name],
                    f"[{name:>{tag_width}}]",
                    note="Refreshing" if present else "Installing",
                )
            except subprocess.CalledProcessError:
                (refresh_failures if present else install_failures).append(name)

        if install_failures:
            return Result(
                "hard_fail",
                f"snap install failed: {', '.join(install_failures)}",
            )
        if refresh_failures:
            return Result(
                "soft_fail",
                f"snap refresh failed (snap stays usable): {', '.join(refresh_failures)}",
            )
        logger.info(f"Done. Installed/refreshed {len(self.packages)} snap(s).")
        return Result("ok", changed=True)

    def show_version(self) -> list[VersionInfo]:
        installed = parse_installed_snaps() if shutil.which("snap") else {}
        rows: list[VersionInfo] = []
        for name in self.packages:
            version = installed.get(name)
            status: VersionStatus = "installed" if version else "missing"
            rows.append(
                VersionInfo(
                    name=name,
                    installed_version=version or "(none)",
                    available_version="unknown",
                    source="snap",
                    status=status,
                    cook=self.cook_name,
                    manager=self.manager,
                )
            )
        return rows


if __name__ == "__main__":
    main_for(SnapCook)
