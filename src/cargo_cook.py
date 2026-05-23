"""
Idempotent cargo installer/updater driven by the [cargo] section of recipe.toml.

Hands every requested crate to a single `cargo binstall --no-confirm pkg1 pkg2 ...`
call. cargo-binstall resolves each crate's latest release, compares against the
installed version recorded in ~/.cargo/.crates.toml, and installs / upgrades /
skips per crate. Idempotency is built in.

cargo-binstall parallelizes resolution and download across requested crates
inside one process, so a ThreadPool wrapper around N single-crate invocations
would just add process-startup overhead and per-process cache-lock contention
without buying parallelism.

cargo-binstall writes to cargo's own .crates.toml registry, so binstall'd and
source-built packages share one index.

cargo-binstall is invoked by absolute path to sidestep the bootstrap PATH
problem — see logs/sys-conf-py-*.log for context.

Bootstraps cargo-binstall via `cargo install cargo-binstall` if it isn't
already on disk. That's a slow source compile, but only happens once per
fresh system; thereafter cargo-binstall is in [cargo].packages and updates
itself in the same batch as everything else (version-aware, ~1s). Requires
cargo (from rustup) — the [url] section installs it, so [cargo] declares
`depends_on = ["url"]`.

Runs as the invoking user — cargo writes into ~/.cargo, so the cook refuses
to run as root (toolchains would land under /root otherwise).
"""

import subprocess
from pathlib import Path

from loguru import logger

from cook_base import CookBase, Result, VersionInfo, main_for
from harness import find_binary, stream_subprocess


def parse_installed_crates() -> dict[str, str]:
    """Map crate name -> version from `cargo install --list`. Each crate is
    announced by a column-0 line `<name> v<version>:`; binaries are indented."""
    cargo = find_binary("cargo")
    if not cargo:
        return {}
    completed = subprocess.run(
        [str(cargo), "install", "--list"], capture_output=True, text=True
    )
    versions: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if not line or line[0].isspace():
            continue
        tokens = line.rstrip(":").split()
        if len(tokens) >= 2 and tokens[1].startswith("v"):
            versions[tokens[0]] = tokens[1].lstrip("v")
    return versions


class CargoCook(CookBase):
    needs_root = False
    manager = "cargo-binstall"
    user_only_reason = "cargo writes into ~/.cargo"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.requested: list[str] = section.get("packages", [])

    def _ensure_binstall(self) -> Path | None:
        if binstall := find_binary("cargo-binstall"):
            return binstall
        cargo = find_binary("cargo")
        if not cargo:
            return None
        logger.info(
            "cargo-binstall missing — bootstrapping via `cargo install` "
            "(slow source compile; happens once per fresh system)"
        )
        stream_subprocess([str(cargo), "install", "cargo-binstall"])
        return find_binary("cargo-binstall")

    def install_or_update(self) -> Result:
        if not self.requested:
            return Result(
                "ok", "No [cargo].packages entries in recipe.toml; nothing to do"
            )

        if not find_binary("cargo"):
            return Result(
                "hard_fail",
                "cargo not found — the [url] section (rustup) must run before [cargo].",
            )

        binstall = self._ensure_binstall()
        if not binstall:
            return Result(
                "hard_fail",
                "cargo-binstall is not on PATH or in ~/.cargo/bin after bootstrap. "
                "Check cargo's install root.",
            )

        logger.info(
            f"Installing/upgrading {len(self.requested)} crate(s): "
            + ", ".join(self.requested)
        )
        stream_subprocess([str(binstall), "--no-confirm", *self.requested])
        logger.info("Done.")
        return Result("ok", changed=True)

    def show_version(self) -> list[VersionInfo]:
        versions = parse_installed_crates()
        rows: list[VersionInfo] = []
        for name in self.requested:
            installed = versions.get(name)
            rows.append(
                VersionInfo(
                    name=name,
                    installed_version=installed or "(none)",
                    available_version="unknown",
                    source="crates.io",
                    status="installed" if installed else "missing",
                    cook=self.cook_name,
                    manager=self.manager,
                )
            )
        return rows


if __name__ == "__main__":
    main_for(CargoCook)
