"""StateCook for [bash.<name>] entries — a generic idempotent shell executor.

This is the home for system-config one-offs that read more honestly as shell
than as Python (apt prerequisites, debconf, the trusted.gpg.d hardening, the
Ubuntu pin).

Phase 2 moves the run/skip decision into chef: each entry declares a read-only
`check_installed` probe and the `desired` token that means "already done". Chef
compares them and only calls apply_one (which runs `install_or_update`) when
they differ. `pre_hook` / `post_hook` are uniform chef-owned lifecycle hooks,
run around the action and only when an action is taken (`pre_hook` is a guard: a
non-zero exit skips the entry; `post_hook` non-zero is a soft failure).

Field semantics (per [bash.<name>] block):
  install_or_update  required. bash snippet (may be multi-line). Run only when
                     check_installed != desired. Non-zero exit -> hard failure.
  check_installed    required. read-only bash snippet; its stdout (stripped) is
                     the current state token.
  desired            required. the check_installed output that means "in the
                     desired state" (so chef skips the entry).
  pre_hook / post_hook
                     optional. bash snippets run by chef around install_or_update.

Idempotent file writes: snippets use the `write-if-changed` command (installed
to /usr/local/bin by the [file.write_if_changed] entry, which ubuntu_pin
depends_on) — pipe content to it with a destination path (and optional octal mode):
  `cat <<EOF | write-if-changed /etc/apt/.../foo.pref`.

Privilege-agnostic: a shell snippet is not inherently root, so this cook defaults
to needs_root = False (CookBase) — least privilege. The [bash] section in
recipe.toml marks needs_root = true because its current entries write under /etc
and drive apt; a user-scope snippet would simply omit it.
"""

import subprocess

from loguru import logger

from cook_base import ItemOutcome, StateCook, debug_main
from harness import stream_subprocess


class BashCook(StateCook):
    manager = "bash"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.entries: dict[str, dict] = section

    def items(self) -> list[str]:
        return list(self.entries)

    def current(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, block in self.entries.items():
            check = block.get("check_installed")
            if not check:
                out[name] = "(no check)"
                continue
            completed = subprocess.run(
                ["bash", "-c", check], capture_output=True, text=True
            )
            out[name] = completed.stdout.strip() or "(empty)"
        return out

    def desired(self) -> dict[str, str]:
        return {name: block.get("desired", "") for name, block in self.entries.items()}

    def hooks(self, name: str) -> tuple[str | None, str | None]:
        block = self.entries[name]
        return (block.get("pre_hook"), block.get("post_hook"))

    def apply_one(self, name: str) -> ItemOutcome:
        block = self.entries[name]
        tag = f"[{name}]"
        if "install_or_update" not in block:
            return ItemOutcome(
                changed=False,
                status="hard_fail",
                message=f"{tag} missing required `install_or_update` snippet",
            )
        try:
            stream_subprocess(
                ["bash", "-c", block["install_or_update"]],
                tag,
                note="install_or_update",
            )
        except subprocess.CalledProcessError as exc:
            return ItemOutcome(
                changed=False,
                status="hard_fail",
                message=f"{tag} install_or_update failed: {exc}",
            )
        logger.info(f"{tag} applied")
        return ItemOutcome(changed=True)


if __name__ == "__main__":
    debug_main(BashCook)
