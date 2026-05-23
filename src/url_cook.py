"""Cook for [url.<name>] entries — vendor `curl | bash` bootstrappers.

(Formerly bash_cook.py; renamed when [bash.*] became the generic shell
executor. This cook is the URL-driven half of that split.)

Field semantics (per [url.<name>] block):
  url            installer URL, piped into bash
  bin            binary to probe for idempotency (default: subtable name)
  args           args appended after `bash -s --`
  update_action  list -> `<bin> <update_action...>`; "rerun-installer" ->
                 re-pipe url; absent -> skip update
  pre_update     bash one-liner run via `bash -c` before update_action;
                 non-zero exit aborts the update as a soft failure

Failure contract: install errors are hard (downstream sections may depend on
the tool). Update errors are soft (tool stays usable, run continues). Refuses
to run as root: these installers write into $HOME.
"""

import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from cook_base import CookBase, Result, VersionInfo, main_for
from harness import fetch_url, find_binary, stream_subprocess

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


class UrlCook(CookBase):
    needs_root = False
    manager = "curl|bash"
    user_only_reason = "these installers write into $HOME"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.installs: dict[str, dict] = section

    def install_or_update(self) -> Result:
        if not self.installs:
            return Result("ok", "No [url.*] entries in recipe.toml; nothing to do")

        logger.info(f"Running {len(self.installs)} install(s) in parallel")
        tag_width = max(len(name) for name in self.installs)
        install_failures: list[str] = []
        update_failures: list[str] = []
        with ThreadPoolExecutor(max_workers=len(self.installs)) as pool:
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
                for name, block in self.installs.items()
            }
            for future in as_completed(pending):
                name = pending[future]
                try:
                    future.result()
                except UpdateError as exc:
                    update_failures.append(name)
                    logger.warning(
                        f"[{name:>{tag_width}}] UPDATE FAILED — {name} still installed "
                        f"but NOT updated. See output above. Error: {exc}"
                    )
                except Exception as exc:
                    install_failures.append(name)
                    logger.error(
                        f"[{name:>{tag_width}}] INSTALL FAILED — {name} NOT on box. "
                        f"See output above. Error: {exc}"
                    )

        if install_failures:
            return Result(
                "hard_fail",
                f"{len(install_failures)}/{len(self.installs)} install(s) failed: "
                + ", ".join(install_failures)
                + ". Aborting.",
            )
        if update_failures:
            return Result(
                "soft_fail",
                f"{len(update_failures)}/{len(self.installs)} update(s) failed: "
                + ", ".join(update_failures)
                + ".",
            )
        logger.info("Done.")
        return Result("ok", changed=True)

    def show_version(self) -> list[VersionInfo]:
        rows: list[VersionInfo] = []
        for name, block in self.installs.items():
            found = find_binary(block.get("bin", name))
            rows.append(
                VersionInfo(
                    name=name,
                    installed_version=str(found) if found else "(none)",
                    available_version="unknown",
                    source=block.get("url", ""),
                    status="installed" if found else "missing",
                    cook=self.cook_name,
                    manager=self.manager,
                )
            )
        return rows


if __name__ == "__main__":
    main_for(UrlCook)
