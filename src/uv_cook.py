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

Requires uv to be installed first — the [url] section installs it, so [uv]
declares `depends_on = ["url"]`.

Runs as the invoking user — uv writes into ~/.local/share/uv and ~/.local/bin,
so the cook refuses to run as root.
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from cook_base import CookBase, Result, VersionInfo, main_for
from harness import find_binary, stream_subprocess


def parse_tool_versions(uv: Path) -> dict[str, str]:
    """Map tool name -> version from `uv tool list`. Each tool is announced by
    a column-0 line `<name> v<version>`; indented `- <bin>` lines are skipped."""
    completed = subprocess.run(
        [str(uv), "tool", "list"],
        capture_output=True,
        text=True,
        check=True,
    )
    versions: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line or line[0].isspace() or line.startswith("-"):
            continue
        tokens = line.split()
        versions[tokens[0]] = tokens[1].lstrip("v") if len(tokens) > 1 else "unknown"
    return versions


class UvCook(CookBase):
    needs_root = False
    manager = "uv"
    user_only_reason = "uv writes into ~/.local/share/uv and ~/.local/bin"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.requested: list[str] = section.get("packages", [])

    def _find_uv(self) -> Path | None:
        return find_binary("uv")

    def install_or_update(self) -> Result:
        if not self.requested:
            return Result(
                "ok", "No [uv].packages entries in recipe.toml; nothing to do"
            )

        uv = self._find_uv()
        if not uv:
            return Result(
                "hard_fail",
                "uv must be installed first; the [url] section must run before [uv].",
            )

        logger.info(f"Running {len(self.requested)} uv tool(s) in parallel")
        installed = set(parse_tool_versions(uv))
        tag_width = max(len(name) for name in self.requested)

        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=len(self.requested)) as pool:
            pending = {
                pool.submit(self._install_one, uv, name, installed, tag_width): name
                for name in self.requested
            }
            for future in as_completed(pending):
                name = pending[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append(name)
                    logger.error(f"{name} failed: {exc}")

        if failures:
            return Result(
                "hard_fail",
                f"{len(failures)} of {len(self.requested)} uv tool(s) failed: "
                + ", ".join(failures),
            )
        logger.info("Done.")
        return Result("ok", changed=True)

    @staticmethod
    def _install_one(uv: Path, name: str, installed: set[str], tag_width: int) -> None:
        action, verb = (
            ("Upgrading", "upgrade") if name in installed else ("Installing", "install")
        )
        stream_subprocess(
            [str(uv), "tool", verb, name], f"[{name:>{tag_width}}]", note=action
        )

    def show_version(self) -> list[VersionInfo]:
        uv = self._find_uv()
        versions = parse_tool_versions(uv) if uv else {}
        rows: list[VersionInfo] = []
        for name in self.requested:
            installed = versions.get(name)
            rows.append(
                VersionInfo(
                    name=name,
                    installed_version=installed or "(none)",
                    available_version="unknown",
                    source="uv",
                    status="installed" if installed else "missing",
                    cook=self.cook_name,
                    manager=self.manager,
                )
            )
        return rows


if __name__ == "__main__":
    main_for(UvCook)
