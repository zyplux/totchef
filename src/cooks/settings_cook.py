"""StateCook for [settings.<app>] — merge an env block into a JSON settings file.

For each [settings.<app>] entry, merges its
`settings_env` table into the top-level `env` key of that JSON file (e.g.
~/.claude/settings.json), preserving every other key the user has set. The TOML
declares the desired state, so same-named env entries are overridden. Diffable:
desired = hash of the merged JSON, current = hash on disk. Runs as the invoking
user, writing into $HOME.
"""

import json
from pathlib import Path

from cook_base import EntrySpec, FileStateCook, StateChangeOutcome
from harness import write_if_changed


class SettingsEntry(EntrySpec):
    settings_json: str
    settings_env: dict[str, str] = {}


class SettingsCook(FileStateCook[SettingsEntry]):
    manager = "settings"
    entry_model = SettingsEntry

    def _target_path(self, name: str) -> Path:
        return Path.home() / self.entries[name].settings_json

    def _render(self, name: str) -> bytes:
        target = self._target_path(name)
        env_overrides = self.entries[name].settings_env
        existing: dict = json.loads(target.read_text()) if target.exists() else {}
        merged = {**existing, "env": {**existing.get("env", {}), **env_overrides}}
        return (json.dumps(merged, indent=2) + "\n").encode()

    def apply_resource(self, name: str) -> StateChangeOutcome:
        changed = write_if_changed(
            self._target_path(name), self._render(name), note=name
        )
        return StateChangeOutcome(changed=changed)
