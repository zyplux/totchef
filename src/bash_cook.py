"""Cook for [bash.<name>] entries — generic idempotent shell operations.

Each entry declares bash one-liners executed by BashCook:
  install_or_update  (required) bash snippet; must be idempotent
  check_installed    (optional) snippet whose stdout becomes show_version output
  pre_update         (optional) snippet run before install_or_update; non-zero
                     exit aborts the entry as a soft failure
  post_update        (optional) snippet run after install_or_update; non-zero
                     exit is reported as a soft failure

Snippets run via `bash -c` through stream_subprocess. SRC_DIR is prepended to
$PATH so the `write-if-changed` helper is available for idempotent file writes:

    write-if-changed /etc/some/file <<'EOF'
    file content here
    EOF

Entries run sequentially in file order; the whole section runs as root
(needs_root = true in recipe.toml).
"""

import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

from harness import (
    SOFT_FAIL_EXIT,
    SRC_DIR,
    CookBase,
    Result,
    VersionInfo,
    load_section,
    start_log_tee,
    stream_subprocess,
)

SCRIPT = Path(__file__).resolve()


class BashCook(CookBase):
    def __init__(self, section: dict) -> None:
        self.entries = {k: v for k, v in section.items() if isinstance(v, dict)}

    def install_or_update(self) -> Result:
        if not self.entries:
            return Result("ok", "No [bash.*] entries; nothing to do", False)

        soft_failures: list[str] = []

        for name, block in self.entries.items():
            tag = f"[bash.{name}]"

            if pre := block.get("pre_update"):
                try:
                    stream_subprocess(["bash", "-c", pre], tag, note="pre_update")
                except subprocess.CalledProcessError as exc:
                    soft_failures.append(name)
                    logger.warning(f"{tag} pre_update failed ({exc}); skipping entry")
                    continue

            snippet = block.get("install_or_update", "")
            if snippet:
                try:
                    stream_subprocess(
                        ["bash", "-c", snippet], tag, note="install_or_update"
                    )
                except subprocess.CalledProcessError as exc:
                    soft_failures.append(name)
                    logger.error(f"{tag} install_or_update failed: {exc}")
                    continue

            if post := block.get("post_update"):
                try:
                    stream_subprocess(["bash", "-c", post], tag, note="post_update")
                except subprocess.CalledProcessError as exc:
                    soft_failures.append(name)
                    logger.warning(f"{tag} post_update failed: {exc}")

        if soft_failures:
            return Result(
                "soft_fail",
                f"bash entries failed: {', '.join(soft_failures)}",
                True,
            )
        return Result("ok", f"{len(self.entries)} bash entry/entries processed", True)

    def show_version(self) -> list[VersionInfo]:
        result: list[VersionInfo] = []
        for name, block in self.entries.items():
            check = block.get("check_installed", "")
            installed_version = ""
            if check:
                try:
                    completed = subprocess.run(
                        ["bash", "-c", check],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    installed_version = completed.stdout.strip()
                except Exception:
                    pass
            result.append(
                VersionInfo(
                    name=name,
                    installed_version=installed_version,
                    available_version="",
                    source="bash",
                    status="unknown",
                    cook="bash_cook",
                    manager="bash",
                )
            )
        return result


def main() -> None:
    section = load_section()
    start_log_tee()

    os.environ["PATH"] = f"{SRC_DIR}:{os.environ.get('PATH', '')}"

    cook = BashCook(section)

    if not cook.entries:
        logger.info("No [bash.*] entries in recipe.toml; nothing to do")
        return

    result = cook.install_or_update()

    if result.status == "hard_fail":
        sys.exit(result.message)
    if result.status == "soft_fail":
        logger.warning(result.message)
        sys.exit(SOFT_FAIL_EXIT)

    logger.info("Done.")


if __name__ == "__main__":
    main()
