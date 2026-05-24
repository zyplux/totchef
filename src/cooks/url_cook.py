"""Cook for [url.<name>] entries — vendor `curl | bash` bootstrappers.

VersionedCook with version semantics degraded to presence: there is no version
number to compare, so `latest_available` is always None ("—") and chef's diff
falls back to install-if-missing / upgrade-if-present. Chef computes that split;
this cook batches the work in parallel (vendor installers are independent and
network-bound).

Field semantics (per [url.<name>] block):
  url            installer URL, piped into bash
  bin            binary to probe for idempotency (default: subtable name)
  args           args appended after `bash -s --`
  update_action  list -> `<bin> <update_action...>`; "rerun-installer" ->
                 re-pipe url; absent -> skip update
  update_guard   bash one-liner run via `bash -c` before the update action;
                 must be an idempotent guard (it also runs on first install, so
                 e.g. herdr's `server stop` first checks the binary exists).
                 Non-zero exit aborts the update as a soft failure. Distinct from
                 chef's StateCook pre_hook: this is run by url_cook itself, only
                 around update_action.

Failure contract: install errors are hard (downstream sections may depend on
the tool). Update errors are soft (tool stays usable, run continues). Runs as
the invoking user (chef forks + drops privilege): these installers write into
$HOME.
"""

import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from cook_base import Result, VersionedCook, debug_main
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
    update_guard: str | None,
    tag: str,
) -> None:
    if update_action is None:
        logger.info(f"{tag} No update_action configured; leaving {bin_path} as-is")
        return

    if update_guard:
        shell_cmd = f"PATH={shlex.quote(str(bin_path.parent))}:$PATH; {update_guard}"
        stream_subprocess(
            ["bash", "-c", shell_cmd],
            tag,
            note=f"Update guard (bash -c): {update_guard}",
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


class UrlCook(VersionedCook):
    manager = "curl|bash"
    user_only_reason = "these installers write into $HOME"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.installs: dict[str, dict] = section

    def requested(self) -> list[str]:
        return list(self.installs)

    def list_installed(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for name, block in self.installs.items():
            if path := find_binary(block.get("bin", name)):
                found[name] = str(path)
        return found

    def latest_available(self, names: list[str]) -> dict[str, str | None]:
        return dict.fromkeys(names)

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> Result:
        work = [("install", n) for n in to_install] + [
            ("upgrade", n) for n in to_upgrade
        ]
        if not work:
            return Result("ok")

        logger.info(f"Running {len(work)} url action(s) in parallel")
        tag_width = max(len(name) for _, name in work)
        install_failures: list[str] = []
        update_failures: list[str] = []
        with ThreadPoolExecutor(max_workers=len(work)) as pool:
            pending = {
                pool.submit(self._run_one, kind, name, tag_width): (kind, name)
                for kind, name in work
            }
            for future in as_completed(pending):
                kind, name = pending[future]
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
                f"{len(install_failures)} install(s) failed: "
                + ", ".join(install_failures)
                + ". Aborting.",
            )
        if update_failures:
            return Result(
                "soft_fail",
                f"{len(update_failures)} update(s) failed: "
                + ", ".join(update_failures)
                + ".",
            )
        return Result("ok")

    def _run_one(self, kind: str, name: str, tag_width: int) -> None:
        block = self.installs[name]
        tag = f"[{name:>{tag_width}}]"
        url = block["url"]
        args = block.get("args", [])
        bin_name = block.get("bin", name)

        if kind == "install":
            run_installer(url, args, tag, note=f"Installing from {url}")
            if found := find_binary(bin_name):
                logger.info(f"{tag} Installed: {found}")
            else:
                logger.warning(
                    f"{tag} {bin_name} not found after install — non-standard path?"
                )
            return

        existing = find_binary(bin_name)
        if not existing:
            run_installer(url, args, tag, note=f"Re-installing from {url}")
            return
        try:
            update_existing(
                url,
                existing,
                args,
                block.get("update_action"),
                block.get("update_guard"),
                tag,
            )
        except subprocess.CalledProcessError as exc:
            raise UpdateError(str(exc)) from exc


if __name__ == "__main__":
    debug_main(UrlCook)
