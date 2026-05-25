"""StateCook for [file.<name>] entries — install a file with exact content.

Desired state is the hash of the intended bytes (inline `content` or a `source`
under src/files/); current is the hash on disk. Chef calls apply_resource only when
they differ, so a `post_hook` (daemon-reload, update-initramfs, …) fires only
when the file changed. Fields: see recipe.toml's header.

Privilege-agnostic: writing a file isn't inherently root (needs_root = False by
default); recipe.toml grants root per entry where it writes under /usr/local or
/etc.
"""

import hashlib
from pathlib import Path

from pydantic import model_validator

from cook_base import EntrySpec, StateChangeOutcome, StateCook
from harness import SRC_DIR, write_if_changed

FILES_DIR = SRC_DIR / "files"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileEntry(EntrySpec):
    path: str
    source: str | None = None
    content: str | None = None
    mode: str = "0644"

    @model_validator(mode="after")
    def _exactly_one_body(self) -> "FileEntry":
        if (self.source is None) == (self.content is None):
            raise ValueError("set exactly one of `source` or `content`")
        return self


class FileCook(StateCook[FileEntry]):
    manager = "file"
    entry_model = FileEntry

    def _load_content(self, name: str) -> bytes:
        entry = self.entries[name]
        if entry.source is not None:
            return (FILES_DIR / entry.source).read_bytes()
        return (entry.content or "").encode()

    def _target_path(self, name: str) -> Path:
        return Path(self.entries[name].path)

    def _parse_mode(self, name: str) -> int:
        return int(self.entries[name].mode, 8)

    def get_current_state(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name in self.entries:
            path = self._target_path(name)
            states[name] = _digest(path.read_bytes()) if path.exists() else "absent"
        return states

    def get_desired_state(self) -> dict[str, str]:
        return {name: _digest(self._load_content(name)) for name in self.entries}

    def apply_resource(self, name: str) -> StateChangeOutcome:
        changed = write_if_changed(
            self._target_path(name),
            self._load_content(name),
            self._parse_mode(name),
            note=name,
        )
        return StateChangeOutcome(changed=changed)
