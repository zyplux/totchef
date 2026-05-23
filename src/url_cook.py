"""Cook for [url.<name>] entries — vendor `curl | bash` bootstrappers.

Field semantics (per recipe.toml block):
  url            installer URL, piped into bash
  bin            binary to probe for idempotency (default: subtable name)
  args           args appended after `bash -s --`
  update_action  list -> `<bin> <update_action...>`; "rerun-installer" ->
                 re-pipe url; absent -> skip update
  pre_update     bash one-liner run via `bash -c` before update_action;
                 `<bin>`'s dir is prepended to PATH. Non-zero exit aborts
                 the update as a soft failure

Failure contract with chef.py: install errors exit 1 (hard — downstream
sections may depend on the tool). Update errors exit SOFT_FAIL_EXIT=75
(soft — tool stays usable, run continues). Refuses to run as root: these
installers write into $HOME.
"""

import os
import shlex
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
    fetch_url,
    find_binary,
    load_section,
    start_log_tee,
    stream_subprocess,
)

SCRIPT = Path(__file__).resolve()

RERUN_INSTALLER = "rerun-installer"


class UpdateError(Exception):
    """Update step failed; the tool itself remains installed and usable."""


class UrlCook(CookBase):
    def __init__(self, section: dict) -> None:
        self.entries = {k: v for k, v in section.items() if isinstance(v, dict)}

    def install_or_update(self) -> Result:
        if not self.entries:
            return Result("ok", "No [url.*] entries; nothing to do", False)

        tag_width = max(len(name) for name in self.entries)
        install_failures: list[tuple[str, Exception]] = []
        update_failures: list[tuple[str, UpdateError]] = []

        with ThreadPoolExecutor(max_workers=len(self.entries)) as pool:
            pending = {
                pool.submit(
                    _install_from_url,
                    block["url"],
                    block.get("bin", name),
                    block.get("args", []),
                    block.get("update_action"),
                    block.get("pre_update"),
                    f"[{name:>{tag_width}}]",
                ): name
                for name, block in self.entries.items()
            }
            for future in as_completed(pending):
                name = pending[future]
                tag = f"[{name:>{tag_width}}]"
                try:
                    future.result()
                except UpdateError as exc:
                    update_failures.append((name, exc))
                    logger.warning(
                        f"{tag} UPDATE FAILED — {name} still installed "
                        f"but NOT updated. See output above. Error: {exc}"
                    )
                except Exception as exc:
                    install_failures.append((name, exc))
                    logger.error(
                        f"{tag} INSTALL FAILED — {name} NOT on box. "
                        f"See output above. Error: {exc}"
                    )

        if install_failures:
            names = ", ".join(n for n, _ in install_failures)
            logger.error(
                f"{len(install_failures)}/{len(self.entries)} install(s) failed: "
                f"{names}. Aborting."
            )
            return Result(
                "hard_fail",
                f"{len(install_failures)}/{len(self.entries)} install(s) failed: {names}",
                True,
            )

        if update_failures:
            names = ", ".join(n for n, _ in update_failures)
            logger.warning(
                f"{len(update_failures)}/{len(self.entries)} update(s) failed: "
                f"{names}. Exit {SOFT_FAIL_EXIT}."
            )
            return Result(
                "soft_fail",
                f"{len(update_failures)}/{len(self.entries)} update(s) failed: {names}",
                True,
            )

        return Result("ok", f"{len(self.entries)} url installer(s) processed", True)

    def show_version(self) -> list[VersionInfo]:
        return [
            VersionInfo(
                name=name,
                installed_version="",
                available_version="",
                source=block.get("url", ""),
                status="installed"
                if find_binary(block.get("bin", name))
                else "missing",
                cook="url_cook",
                manager="curl|bash",
            )
            for name, block in self.entries.items()
        ]


def _run_installer(url: str, args: list[str], tag: str, note: str) -> None:
    stream_subprocess(
        ["bash", "-s", "--", *args],
        tag,
        note=note,
        stdin=fetch_url(url),
    )


def _update_existing(
    url: str,
    bin_path: Path,
    args: list[str],
    update_action: list[str] | str | None,
    pre_update: str | None,
    tag: str,
) -> None:
    if update_action is None:
        logger.info(f"{tag} No update_action configured; leaving {bin_path} as-is")
        return

    if pre_update:
        shell_cmd = f"PATH={shlex.quote(str(bin_path.parent))}:$PATH; {pre_update}"
        stream_subprocess(
            ["bash", "-c", shell_cmd],
            tag,
            note=f"Pre-update hook (bash -c): {pre_update}",
        )

    if isinstance(update_action, list) and update_action:
        stream_subprocess(
            [str(bin_path), *update_action],
            tag,
            note=f"Updating via `{bin_path.name} {' '.join(update_action)}`",
        )
    elif update_action == RERUN_INSTALLER:
        _run_installer(
            url, args, tag, note=f"Updating by re-running installer from {url}"
        )
    else:
        raise ValueError(
            f"unrecognized update_action for {tag}: {update_action!r} "
            f"(expected a list of args, the string {RERUN_INSTALLER!r}, or absent)"
        )


def _install_from_url(
    url: str,
    bin_name: str,
    args: list[str],
    update_action: list[str] | str | None,
    pre_update: str | None,
    tag: str,
) -> None:
    if existing := find_binary(bin_name):
        try:
            _update_existing(url, existing, args, update_action, pre_update, tag)
        except subprocess.CalledProcessError as exc:
            raise UpdateError(str(exc)) from exc
        return

    _run_installer(url, args, tag, note=f"Installing from {url}")

    if found := find_binary(bin_name):
        logger.info(f"{tag} Installed: {found}")
    else:
        logger.warning(f"{tag} {bin_name} not found after install — non-standard path?")


def main() -> None:
    if os.geteuid() == 0:
        sys.exit("ERROR: run as invoking user, not root — installers write into $HOME.")

    section = load_section()
    start_log_tee()

    cook = UrlCook(section)

    if not cook.entries:
        logger.info("No [url.*] entries in recipe.toml; nothing to do")
        return

    logger.info(f"Running {len(cook.entries)} install(s) in parallel")
    result = cook.install_or_update()

    if result.status == "hard_fail":
        sys.exit(1)
    if result.status == "soft_fail":
        sys.exit(SOFT_FAIL_EXIT)

    logger.info("Done.")


if __name__ == "__main__":
    main()
