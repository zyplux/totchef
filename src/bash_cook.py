"""Cook for [bash.<name>] entries — a generic shell executor.

Each entry is a small, idempotent shell program. This is the home for
system-config one-offs that read more honestly as shell than as Python
(apt prerequisites, debconf, the trusted.gpg.d hardening, the Ubuntu pin).

Field semantics (per [bash.<name>] block):
  install_or_update  required. bash snippet (may be multi-line). MUST be
                     idempotent: check-and-act, or act on something natively
                     idempotent. Non-zero exit is a hard failure (downstream
                     sections may depend on it).
  pre_update         optional. bash snippet run before install_or_update.
                     Non-zero exit aborts the entry as a soft failure.
  post_update        optional. bash snippet run after install_or_update.
                     Non-zero exit aborts the entry as a soft failure.
  check_installed    optional. read-only bash snippet; show_version() runs it
                     and wraps stdout into a VersionInfo. Loose contract — emit
                     a version string, or `present`/`absent`.

Idempotent file writes: every snippet has a `write-if-changed` command on
$PATH that wraps harness.write_if_changed — pipe content to it with a
destination path (and optional octal mode) so heredoc'd files stay quiet on
re-runs:  `cat <<EOF | write-if-changed /etc/apt/.../foo.pref`.

Entries run sequentially in recipe file order. The cook runs as root (its
entries write under /etc and drive apt); chef spawns it under sudo.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

from cook_base import CookBase, Result, VersionInfo, main_for
from harness import SRC_DIR, stream_subprocess

WRITE_IF_CHANGED_HELPER = """#!{python}
import sys
from pathlib import Path

sys.path.insert(0, {src_dir!r})

import harness  # noqa: E402
from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, format="{{message}}", level="INFO")

dest = Path(sys.argv[1])
mode = int(sys.argv[2], 8) if len(sys.argv) > 2 else 0o644
harness.write_if_changed(dest, sys.stdin.buffer.read(), mode)
"""


def install_helper_on_path() -> tempfile.TemporaryDirectory:
    """Drop a `write-if-changed` shim into a temp dir and prepend it to PATH so
    snippets can funnel file writes through harness.write_if_changed. The shim
    runs under this cook's interpreter (sys.executable) so loguru/harness import
    cleanly even under sudo."""
    helper_dir = tempfile.TemporaryDirectory(prefix="sys-conf-py-helper-")
    helper = Path(helper_dir.name) / "write-if-changed"
    helper.write_text(
        WRITE_IF_CHANGED_HELPER.format(python=sys.executable, src_dir=str(SRC_DIR))
    )
    helper.chmod(0o755)
    os.environ["PATH"] = f"{helper_dir.name}:{os.environ['PATH']}"
    return helper_dir


class BashCook(CookBase):
    # The only [bash.*] entries today are root apt operations; chef spawns this
    # cook under sudo. A future non-root bash section would split this attr off
    # per-section (Phase 4 per-entry granularity).
    needs_root = True
    manager = "bash"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.entries: dict[str, dict] = section

    def install_or_update(self) -> Result:
        if not self.entries:
            return Result("ok", "No [bash.*] entries in recipe.toml; nothing to do")

        helper_dir = install_helper_on_path()
        try:
            logger.info(f"Running {len(self.entries)} bash entry(ies) sequentially")
            hard_failures: list[str] = []
            soft_failures: list[str] = []
            for name, block in self.entries.items():
                outcome = self._run_entry(name, block)
                if outcome == "hard":
                    hard_failures.append(name)
                elif outcome == "soft":
                    soft_failures.append(name)
        finally:
            helper_dir.cleanup()

        if hard_failures:
            return Result(
                "hard_fail",
                f"{len(hard_failures)}/{len(self.entries)} bash entry(ies) failed: "
                + ", ".join(hard_failures)
                + ". Aborting.",
            )
        if soft_failures:
            return Result(
                "soft_fail",
                f"{len(soft_failures)}/{len(self.entries)} bash pre/post hook(s) "
                "failed: " + ", ".join(soft_failures) + ".",
            )
        logger.info("Done.")
        return Result("ok", changed=True)

    def _run_entry(self, name: str, block: dict) -> str:
        """Run one entry's pre_update / install_or_update / post_update. Returns
        "ok", "soft" (pre/post hook failed), or "hard" (main snippet failed)."""
        tag = f"[{name}]"
        if "install_or_update" not in block:
            logger.error(f"{tag} missing required `install_or_update` snippet")
            return "hard"

        if pre := block.get("pre_update"):
            try:
                stream_subprocess(["bash", "-c", pre], tag, note="pre_update")
            except subprocess.CalledProcessError as exc:
                logger.warning(f"{tag} pre_update failed: {exc}")
                return "soft"

        try:
            stream_subprocess(
                ["bash", "-c", block["install_or_update"]],
                tag,
                note="install_or_update",
            )
        except subprocess.CalledProcessError as exc:
            logger.error(f"{tag} install_or_update failed: {exc}")
            return "hard"

        if post := block.get("post_update"):
            try:
                stream_subprocess(["bash", "-c", post], tag, note="post_update")
            except subprocess.CalledProcessError as exc:
                logger.warning(f"{tag} post_update failed: {exc}")
                return "soft"

        return "ok"

    def show_version(self) -> list[VersionInfo]:
        rows: list[VersionInfo] = []
        for name, block in self.entries.items():
            check = block.get("check_installed")
            if not check:
                continue
            completed = subprocess.run(
                ["bash", "-c", check], capture_output=True, text=True
            )
            reported = completed.stdout.strip()
            rows.append(
                VersionInfo(
                    name=name,
                    installed_version=reported or "(none)",
                    available_version="unknown",
                    source="bash",
                    status="installed" if completed.returncode == 0 else "unknown",
                    cook=self.cook_name,
                    manager=self.manager,
                )
            )
        return rows


if __name__ == "__main__":
    main_for(BashCook)
