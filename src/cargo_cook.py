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

cargo-binstall is invoked by absolute path to sidestep the bootstrap PATH
problem — see logs/sys-conf-py-*.log for context.

Bootstraps cargo-binstall via `cargo install cargo-binstall` if it isn't
already on disk. That's a slow source compile, but only happens once per
fresh system; thereafter cargo-binstall is in [cargo].packages and updates
itself in the same batch as everything else (version-aware, ~1s). Requires
cargo (from rustup) — url_cook.py must run first.

Runs as the invoking user — cargo writes into ~/.cargo, so the script refuses
to run as root (toolchains would land under /root otherwise).
"""

import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

from harness import (
    CookBase,
    Result,
    VersionInfo,
    find_binary,
    load_section,
    start_log_tee,
    stream_subprocess,
)

SCRIPT = Path(__file__).resolve()

_CRATES_TOML = Path.home() / ".cargo" / ".crates.toml"


class CargoCook(CookBase):
    def __init__(self, section: dict, binstall: Path) -> None:
        self.packages: list[str] = section.get("packages", [])
        self.binstall = binstall

    def install_or_update(self) -> Result:
        if not self.packages:
            return Result("ok", "No [cargo].packages entries; nothing to do", False)

        logger.info(
            f"Installing/upgrading {len(self.packages)} crate(s): "
            + ", ".join(self.packages)
        )
        try:
            stream_subprocess([str(self.binstall), "--no-confirm", *self.packages])
        except subprocess.CalledProcessError as exc:
            return Result("hard_fail", f"cargo-binstall failed: {exc}", True)

        return Result("ok", f"{len(self.packages)} crate(s) processed", True)

    def show_version(self) -> list[VersionInfo]:
        installed = self._read_installed()
        return [
            VersionInfo(
                name=pkg,
                installed_version=installed.get(pkg, ""),
                available_version="",
                source="crates.io",
                status="installed" if pkg in installed else "missing",
                cook="cargo_cook",
                manager="cargo-binstall",
            )
            for pkg in self.packages
        ]

    def _read_installed(self) -> dict[str, str]:
        if not _CRATES_TOML.exists():
            return {}
        import tomllib

        with _CRATES_TOML.open("rb") as f:
            data = tomllib.load(f)
        result: dict[str, str] = {}
        for key in data.get("v1", {}):
            name = key.split(" ")[0]
            version = key.split(" ")[1] if " " in key else ""
            result[name] = version
        return result


def _bootstrap_binstall() -> Path:
    binstall = find_binary("cargo-binstall")
    if binstall:
        return binstall
    cargo = find_binary("cargo")
    if not cargo:
        sys.exit(
            "ERROR: cargo not found — [url] must run first (rustup provides cargo)."
        )
    logger.info(
        "cargo-binstall missing — bootstrapping via `cargo install` "
        "(slow source compile; happens once per fresh system)"
    )
    stream_subprocess([str(cargo), "install", "cargo-binstall"])
    binstall = find_binary("cargo-binstall")
    if not binstall:
        sys.exit(
            "ERROR: `cargo install cargo-binstall` succeeded but the binary "
            "is not on PATH or in ~/.cargo/bin. Check cargo's install root."
        )
    return binstall


def main() -> None:
    if os.geteuid() == 0:
        sys.exit(
            "ERROR: run as the invoking user (not root) — cargo writes into "
            "~/.cargo and would land under /root if run as root."
        )

    section = load_section()
    start_log_tee()

    binstall = _bootstrap_binstall()
    cook = CargoCook(section, binstall)

    if not cook.packages:
        logger.info("No [cargo].packages entries in recipe.toml; nothing to do")
        return

    result = cook.install_or_update()

    if result.status == "hard_fail":
        sys.exit(result.message)

    logger.info("Done.")


if __name__ == "__main__":
    main()
