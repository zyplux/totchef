"""Cook for [uv] — install/upgrade Python CLI tools via `uv tool`.

Driven by [uv].packages in recipe.toml. Each package is either installed
(not yet in `uv tool list`) or upgraded. Operations run in a thread pool;
uv serializes conflicting filesystem work with its own locks.

Requires uv pre-installed (bash_cook / [bash.uv] runs first).
Refuses root: uv writes into ~/.local and ~/.local/share/uv.
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from harness import (
    SOFT_FAIL_EXIT,
    Result,
    VersionInfo,
    find_binary,
    load_section,
    start_log_tee,
    stream_subprocess,
)


class UvCook:
    def __init__(self, packages: list[str], uv: Path) -> None:
        self._packages = packages
        self._uv = uv

    def install_or_update(self) -> Result:
        if not self._packages:
            logger.info("No [uv].packages entries in recipe.toml; nothing to do")
            return Result(status="ok", message="No packages configured", changed=False)

        logger.info(f"Running {len(self._packages)} uv tool(s) in parallel")
        installed = self._list_installed()

        tag_width = max(len(name) for name in self._packages)
        failures: list[tuple[str, Exception]] = []
        with ThreadPoolExecutor(max_workers=len(self._packages)) as pool:
            pending = {
                pool.submit(
                    self._install_or_upgrade_one,
                    name,
                    installed,
                    f"[{name:>{tag_width}}]",
                ): name
                for name in self._packages
            }
            for future in as_completed(pending):
                name = pending[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append((name, exc))
                    logger.error(f"{name} failed: {exc}")

        if failures:
            msg = (
                f"{len(failures)} of {len(self._packages)} uv tool(s) failed: "
                + ", ".join(name for name, _ in failures)
            )
            return Result(status="hard_fail", message=msg, changed=True)

        logger.info("Done.")
        return Result(status="ok", message="Done.", changed=True)

    def show_version(self) -> list[VersionInfo]:
        installed = self._list_installed()
        return [
            VersionInfo(
                name=pkg,
                installed_version=installed.get(pkg, ""),
                available_version="",
                source="uv",
                status="installed" if pkg in installed else "missing",
                cook="uv_cook",
                manager="uv",
            )
            for pkg in self._packages
        ]

    def _list_installed(self) -> dict[str, str]:
        completed = subprocess.run(
            [str(self._uv), "tool", "list"],
            capture_output=True,
            text=True,
            check=True,
        )
        result: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if line and not line[0].isspace() and not line.startswith("-"):
                parts = line.split()
                if len(parts) >= 2:
                    result[parts[0]] = parts[1]
        return result

    def _install_or_upgrade_one(
        self, name: str, installed: dict[str, str], tag: str
    ) -> None:
        action, verb = (
            ("Upgrading", "upgrade") if name in installed else ("Installing", "install")
        )
        stream_subprocess([str(self._uv), "tool", verb, name], tag, note=action)


def main() -> None:
    if os.geteuid() == 0:
        sys.exit(
            "ERROR: run as the invoking user (not root) — uv writes into "
            "~/.local and would land under /root if run as root."
        )

    uv = find_binary("uv")
    if not uv:
        sys.exit("ERROR: uv must be installed first; [bash] must run before [uv].")

    section = load_section()
    start_log_tee()

    cook = UvCook(packages=section.get("packages", []), uv=uv)
    result = cook.install_or_update()

    if result.status == "ok":
        sys.exit(0)
    elif result.status == "soft_fail":
        logger.error(result.message)
        sys.exit(SOFT_FAIL_EXIT)
    else:
        logger.error(result.message)
        sys.exit(1)


if __name__ == "__main__":
    main()
