"""StateCook for [bash.<name>] entries — a generic idempotent shell executor,
home for system-config one-offs that read more honestly as shell than Python
(apt prerequisites, debconf, the trusted.gpg.d hardening, the Ubuntu pin).

The three snippet keys mirror the StateCook lifecycle one-to-one, so bash reads
as the contract written into TOML: `current_state` (a read-only probe that echoes
the current token -> get_current_state), `desired_state` (the target token ->
get_desired_state), and `apply` (run only when they differ -> apply_resource),
with `pre_hook` / `post_hook` around it. Snippets pipe file writes through the
`write-if-changed` command rather than writing directly. Fields: see
recipe.toml's header.

Privilege-agnostic: a shell snippet isn't inherently root (needs_root = False by
default); recipe.toml grants root per entry.
"""

import subprocess

from loguru import logger

from cook_base import StateChangeOutcome, StateCook, StateEntrySpec
from harness import stream_subprocess


class BashEntry(StateEntrySpec):
    current_state: str | None = None
    desired_state: str = ""
    apply: str


class BashCook(StateCook[BashEntry]):
    manager = "bash"
    entry_model = BashEntry

    def get_current_state(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name, entry in self.entries.items():
            if not entry.current_state:
                states[name] = "(no check)"
                continue
            completed = subprocess.run(
                ["bash", "-c", entry.current_state], capture_output=True, text=True
            )
            states[name] = completed.stdout.strip() or "(empty)"
        return states

    def get_desired_state(self) -> dict[str, str]:
        return {name: entry.desired_state for name, entry in self.entries.items()}

    def apply_resource(self, name: str) -> StateChangeOutcome:
        entry = self.entries[name]
        try:
            stream_subprocess(["bash", "-c", entry.apply], note="apply")
        except subprocess.CalledProcessError as exc:
            return StateChangeOutcome(
                changed=False,
                status="hard_fail",
                message=f"apply failed: {exc}",
            )
        logger.info("applied")
        return StateChangeOutcome(changed=True)
