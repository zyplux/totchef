"""
Idempotent uv-tool installer/updater driven by the [uv] section of recipe.toml.

For each entry in [uv].packages:
  not installed -> `uv tool install <pkg>`
  installed     -> `uv tool upgrade <pkg>`

Installed tools are detected from a single up-front `uv tool list`, which
announces each tool on a column-0 line as `<name> v<version>` with its
executables indented (`- <bin>`) below.

Packages are processed concurrently via a thread pool; uv serializes
conflicting filesystem work internally with its own locks.

Requires uv to be installed first — url_cook.py must run first.

Runs as the invoking user — uv writes into ~/.local/share/uv and ~/.local/bin,
so the script refuses to run as root.
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from harness import (
    SOFT_FAIL_EXIT,
    CookBase,
    Result,
    VersionInfo,
    find_binary,
    load_section,
    start_log_tee,
    stream_subprocess,
)

SCRIPT = Path(__file__).resolve()


class UvCook(CookBase):
    def __init__(self, section: dict, uv: Path) -> None:
        self.packages: list[str] = section.get("packages", [])
        self.uv = uv

    def install_or_update(self) -> Result:
        if not self.packages:
            return Result("ok", "No [uv].packages entries; nothing to do", False)

        installed = self._list_installed()
        tag_width = max(len(name) for name in self.packages)
        failures: list[tuple[str, Exception]] = []

        with ThreadPoolExecutor(max_workers=len(self.packages)) as pool:
            pending = {
                pool.submit(
                    self._process_one,
                    name,
                    installed,
                    f"[{name:>{tag_width}}]",
                ): name
                for name in self.packages
            }
            for future in as_completed(pending):
                name = pending[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append((name, exc))
                    logger.error(f"{name} failed: {exc}")

        if failures:
            names = ", ".join(n for n, _ in failures)
            return Result(
                "hard_fail",
                f"{len(failures)}/{len(self.packages)} uv tool(s) failed: {names}",
                True,
            )
        return Result("ok", f"{len(self.packages)} uv tool(s) processed", True)

    def show_version(self) -> list[VersionInfo]:
        installed = self._list_installed()
        return [
            VersionInfo(
                name=name,
                installed_version=installed.get(name, ""),
                available_version="",
                source="pypi",
                status="installed" if name in installed else "missing",
                cook="uv_cook",
                manager="uv",
            )
            for name in self.packages
        ]

    def _list_installed(self) -> dict[str, str]:
        completed = subprocess.run(
            [str(self.uv), "tool", "list"],
            capture_output=True,
            text=True,
            check=True,
        )
        result: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if line and not line[0].isspace() and not line.startswith("-"):
                parts = line.split()
                result[parts[0]] = parts[1] if len(parts) > 1 else ""
        return result

    def _process_one(self, name: str, installed: dict[str, str], tag: str) -> None:
        action, verb = (
            ("Upgrading", "upgrade") if name in installed else ("Installing", "install")
        )
        stream_subprocess([str(self.uv), "tool", verb, name], tag, note=action)


def main() -> None:
    if os.geteuid() == 0:
        sys.exit(
            "ERROR: run as the invoking user (not root) — uv writes into "
            "~/.local and would land under /root if run as root."
        )

    uv = find_binary("uv")
    if not uv:
        sys.exit("ERROR: uv must be installed first; [url] must run before [uv].")

    section = load_section()
    start_log_tee()

    cook = UvCook(section, uv)

    if not cook.packages:
        logger.info("No [uv].packages entries in recipe.toml; nothing to do")
        return

    logger.info(f"Running {len(cook.packages)} uv tool(s) in parallel")
    result = cook.install_or_update()

    if result.status == "hard_fail":
        sys.exit(result.message)
    if result.status == "soft_fail":
        logger.warning(result.message)
        sys.exit(SOFT_FAIL_EXIT)

    logger.info("Done.")


if __name__ == "__main__":
    main()
