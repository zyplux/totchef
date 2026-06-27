"""StateCook for [chezmoi] — provision a dotfiles repo with chezmoi: clone it into source_dir, persist sourceDir to chezmoi's config, and (by default) apply it into $HOME; idempotent and user-scoped, gated on [url.chezmoi] for the binary."""

import os
import subprocess
from pathlib import Path

from totchef import harness, shell
from totchef.cook_base import CookBase, EntrySpec, StateChangeOutcome, StateCook

RESOURCE = "dotfiles"
# chezmoi's own default source directory; overridable per recipe.
DEFAULT_SOURCE_DIR = "~/.local/share/chezmoi"


class ChezmoiEntry(EntrySpec):
    repo: str
    source_dir: str = DEFAULT_SOURCE_DIR
    apply: bool = True


class ChezmoiCook(StateCook[ChezmoiEntry]):
    """The single flat [chezmoi] section is one resource, so it validates the slice directly into one synthetic `dotfiles` entry rather than the subtable map StateCook assumes."""

    entry_model = ChezmoiEntry
    entry_keyed = False

    def __init__(self, section: dict) -> None:
        CookBase.__init__(self, section)
        self.entries = {RESOURCE: ChezmoiEntry.model_validate(section)}

    @property
    def spec(self) -> ChezmoiEntry:
        return self.entries[RESOURCE]

    def _source_path(self) -> Path:
        return Path(self.spec.source_dir).expanduser()

    def _config_path(self) -> Path:
        """chezmoi reads its config from $XDG_CONFIG_HOME/chezmoi (else ~/.config/chezmoi); write sourceDir there so the operator's bare chezmoi commands find the same source."""
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(base) / "chezmoi" / "chezmoi.toml"

    def _config_bytes(self) -> bytes:
        return f'sourceDir = "{self.spec.source_dir}"\n'.encode()

    def _is_cloned(self) -> bool:
        return (self._source_path() / ".git").is_dir()

    def _config_current(self) -> bool:
        path = self._config_path()
        return path.exists() and path.read_bytes() == self._config_bytes()

    def _destination_matches(self, chezmoi: Path) -> bool:
        """`chezmoi verify` exits 0 only when $HOME already matches the source's target state; it mutates nothing, so it is a safe probe."""
        completed = shell.run(str(chezmoi), "verify", "--source", str(self._source_path()))
        return completed.returncode == 0

    def get_desired_state(self) -> dict[str, str]:
        return {RESOURCE: "applied" if self.spec.apply else "initialized"}

    def get_current_state(self) -> dict[str, str]:
        chezmoi = harness.find_binary("chezmoi")
        if chezmoi is None:
            return {RESOURCE: "chezmoi-missing"}
        if not self._is_cloned():
            return {RESOURCE: "uncloned"}
        if not self._config_current():
            return {RESOURCE: "unconfigured"}
        if self.spec.apply and not self._destination_matches(chezmoi):
            return {RESOURCE: "unapplied"}
        return self.get_desired_state()

    def apply_resource(self, name: str) -> StateChangeOutcome:
        spec = self.entries[name]
        chezmoi = harness.find_binary("chezmoi")
        if chezmoi is None:
            return StateChangeOutcome(changed=False, status="hard_fail", message="chezmoi not found — the [url.chezmoi] section must run before [chezmoi].")
        source = self._source_path()
        harness.write_if_changed(self._config_path(), self._config_bytes(), note="chezmoi sourceDir")
        try:
            if not self._is_cloned():
                shell.stream([str(chezmoi), "init", "--source", str(source), spec.repo], note="chezmoi init")
            if spec.apply:
                shell.stream([str(chezmoi), "apply", "--source", str(source)], note="chezmoi apply")
        except subprocess.CalledProcessError as exc:
            return StateChangeOutcome(changed=False, status="hard_fail", message=f"chezmoi failed: {exc}")
        return StateChangeOutcome(changed=True)
