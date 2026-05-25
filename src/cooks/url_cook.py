"""Cook for [url.<name>] entries — vendor `curl | bash` bootstrappers.

VersionedCook degraded to presence: there is no version to compare, so
`latest_available` is None ("—") and chef's diff is install-if-missing /
upgrade-if-present. Each [url.<name>] is its own node, so chef runs the
installers concurrently and this cook only ever handles its one entry. Install
errors are hard (downstream may need the tool); update errors are soft (the tool
stays usable). Runs as the invoking user. Fields: see recipe.toml's header.
"""

import shlex
import subprocess
from pathlib import Path
from typing import Literal

from loguru import logger

from cook_base import EntrySpec, SyncOutcome, VersionedCook
from harness import fetch_url, find_binary, stream_subprocess

RERUN_INSTALLER = "rerun-installer"


class UrlEntry(EntrySpec):
    url: str
    bin: str | None = None
    args: list[str] = []
    update_action: list[str] | Literal["rerun-installer"] | None = None
    update_guard: str | None = None


def run_installer(url: str, args: list[str], tag: str, note: str) -> None:
    stream_subprocess(["bash", "-s", "--", *args], tag, note=note, stdin=fetch_url(url))


def update_existing(entry: UrlEntry, bin_path: Path, tag: str) -> None:
    action = entry.update_action
    if action is None:
        logger.info(f"{tag} No update_action; leaving {bin_path} as-is")
        return
    if guard := entry.update_guard:
        shell = f"PATH={shlex.quote(str(bin_path.parent))}:$PATH; {guard}"
        stream_subprocess(["bash", "-c", shell], tag, note=f"Update guard: {guard}")
    if action == RERUN_INSTALLER:
        run_installer(entry.url, entry.args, tag, note=f"Updating from {entry.url}")
    elif isinstance(action, list) and action:
        stream_subprocess(
            [str(bin_path), *action],
            tag,
            note=f"Updating via `{bin_path.name} {' '.join(action)}`",
        )
    else:
        raise ValueError(
            f"{tag} unrecognized update_action {action!r} "
            f"(expected an arg list, {RERUN_INSTALLER!r}, or absent)"
        )


class UrlCook(VersionedCook):
    manager = "curl|bash"
    entry_model = UrlEntry

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.installs = {
            name: UrlEntry.model_validate(raw) for name, raw in section.items()
        }

    def requested(self) -> list[str]:
        return list(self.installs)

    def list_installed(self) -> dict[str, str]:
        return {
            name: "present"
            for name, entry in self.installs.items()
            if find_binary(entry.bin or name)
        }

    def latest_available(self, names: list[str]) -> dict[str, str | None]:
        return dict.fromkeys(names)

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> SyncOutcome:
        if not (to_install or to_upgrade):
            return SyncOutcome("ok")

        [(name, entry)] = self.installs.items()
        tag = f"[{name}]"
        bin_name = entry.bin or name

        if (existing := find_binary(bin_name)) is None:
            try:
                run_installer(
                    entry.url, entry.args, tag, note=f"Installing {entry.url}"
                )
            except Exception as exc:
                return SyncOutcome("hard_fail", f"{name} install failed: {exc}")
            if found := find_binary(bin_name):
                logger.info(f"{tag} Installed: {found}")
            else:
                logger.warning(f"{tag} {bin_name} not found after install")
            return SyncOutcome("ok")

        try:
            update_existing(entry, existing, tag)
        except subprocess.CalledProcessError as exc:
            return SyncOutcome(
                "soft_fail", f"{name} update failed (still installed): {exc}"
            )
        return SyncOutcome("ok")
