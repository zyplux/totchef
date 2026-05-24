"""StateCook for [bash.<name>] entries — a generic idempotent shell executor,
home for system-config one-offs that read more honestly as shell than Python
(apt prerequisites, debconf, the trusted.gpg.d hardening, the Ubuntu pin).

Each entry's read-only `check_installed` yields the current token and `desired`
the target; chef runs `install_or_update` only when they differ, with `pre_hook`
/ `post_hook` around it. Snippets pipe file writes through the `write-if-changed`
command rather than writing directly. Fields: see recipe.toml's header.

Privilege-agnostic: a shell snippet isn't inherently root (needs_root = False by
default); recipe.toml grants root per entry.
"""

import subprocess

from loguru import logger

from cook_base import EntrySpec, ItemOutcome, StateCook, debug_main
from harness import stream_subprocess


class BashEntry(EntrySpec):
    install_or_update: str
    check_installed: str | None = None
    desired: str = ""


class BashCook(StateCook):
    manager = "bash"
    entry_model = BashEntry

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.entries = {
            name: BashEntry.model_validate(raw) for name, raw in section.items()
        }

    def items(self) -> list[str]:
        return list(self.entries)

    def current(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, entry in self.entries.items():
            if not entry.check_installed:
                out[name] = "(no check)"
                continue
            completed = subprocess.run(
                ["bash", "-c", entry.check_installed], capture_output=True, text=True
            )
            out[name] = completed.stdout.strip() or "(empty)"
        return out

    def desired(self) -> dict[str, str]:
        return {name: entry.desired for name, entry in self.entries.items()}

    def hooks(self, name: str) -> tuple[str | None, str | None]:
        entry = self.entries[name]
        return (entry.pre_hook, entry.post_hook)

    def apply_one(self, name: str) -> ItemOutcome:
        entry = self.entries[name]
        tag = f"[{name}]"
        try:
            stream_subprocess(
                ["bash", "-c", entry.install_or_update],
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
