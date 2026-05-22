"""Loader for [bash.<name>] entries — vendor `curl | bash` bootstrappers.

Field semantics (per install.toml block):
  url            installer URL, piped into bash
  bin            binary to probe for idempotency (default: subtable name)
  args           args appended after `bash -s --`
  update_action  list -> `<bin> <update_action...>`; "rerun-installer" ->
                 re-pipe url; absent -> skip update
  pre_update     bash one-liner run via `bash -c` before update_action;
                 non-zero exit aborts the update as a soft failure

Failure contract with main.py: install errors exit 1 (hard — downstream
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


def run_installer(url: str, args: list[str], tag: str, note: str) -> None:
    stream_subprocess(
        ["bash", "-s", "--", *args],
        tag,
        note=note,
        stdin=fetch_url(url),
    )


def update_existing(
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
        run_installer(
            url, args, tag, note=f"Updating by re-running installer from {url}"
        )
    else:
        raise ValueError(
            f"unrecognized update_action for {tag}: {update_action!r} "
            f"(expected a list of args, the string {RERUN_INSTALLER!r}, or absent)"
        )


def install_from_url(
    url: str,
    bin_name: str,
    args: list[str],
    update_action: list[str] | str | None,
    pre_update: str | None,
    tag: str,
) -> None:
    if existing := find_binary(bin_name):
        try:
            update_existing(url, existing, args, update_action, pre_update, tag)
        except subprocess.CalledProcessError as exc:
            raise UpdateError(str(exc)) from exc
        return

    run_installer(url, args, tag, note=f"Installing from {url}")

    if found := find_binary(bin_name):
        logger.info(f"{tag} Installed: {found}")
    else:
        logger.warning(f"{tag} {bin_name} not found after install — non-standard path?")


def main() -> None:
    if os.geteuid() == 0:
        sys.exit("ERROR: run as invoking user, not root — installers write into $HOME.")

    installs = load_section()
    if not installs:
        logger.info("No [bash.*] entries in install.toml; nothing to do")
        return

    start_log_tee()
    logger.info(f"Running {len(installs)} install(s) in parallel")

    tag_width = max(len(name) for name in installs)
    install_failures: list[tuple[str, Exception]] = []
    update_failures: list[tuple[str, UpdateError]] = []
    with ThreadPoolExecutor(max_workers=len(installs)) as pool:
        pending = {
            pool.submit(
                install_from_url,
                block["url"],
                block.get("bin", name),
                block.get("args", []),
                block.get("update_action"),
                block.get("pre_update"),
                f"[{name:>{tag_width}}]",
            ): name
            for name, block in installs.items()
        }
        for future in as_completed(pending):
            name = pending[future]
            try:
                future.result()
            except UpdateError as exc:
                update_failures.append((name, exc))
                logger.warning(
                    f"[{name:>{tag_width}}] UPDATE FAILED — {name} still installed "
                    f"but NOT updated. See output above. Error: {exc}"
                )
            except Exception as exc:
                install_failures.append((name, exc))
                logger.error(
                    f"[{name:>{tag_width}}] INSTALL FAILED — {name} NOT on box. "
                    f"See output above. Error: {exc}"
                )

    if install_failures:
        names = ", ".join(name for name, _ in install_failures)
        logger.error(
            f"{len(install_failures)}/{len(installs)} install(s) failed: {names}. Aborting."
        )
        sys.exit(1)

    if update_failures:
        names = ", ".join(name for name, _ in update_failures)
        logger.warning(
            f"{len(update_failures)}/{len(installs)} update(s) failed: {names}. Exit {SOFT_FAIL_EXIT}."
        )
        sys.exit(SOFT_FAIL_EXIT)

    logger.info("Done.")


if __name__ == "__main__":
    main()
