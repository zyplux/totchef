"""StateCook for [file.<name>] entries — install a file with exact content.

A FileStateCook: it supplies `_target_path` (the install path) and `_render` (the
intended bytes — inline `content`, or a `source` under src/files/), and the base
diffs by content hash so a `post_hook` (daemon-reload, update-initramfs, …) fires
only when the file changed. Fields: see recipe.toml's header.

Privilege-agnostic: writing a file isn't inherently root (needs_root = False by
default); recipe.toml grants root per entry where it writes under /usr/local or
/etc.
"""

from pathlib import Path

from pydantic import model_validator

from cook_base import FileStateCook, StateChangeOutcome, StateEntrySpec
from harness import SRC_DIR, write_if_changed

FILES_DIR = SRC_DIR / "files"


class FileEntry(StateEntrySpec):
    path: str
    source: str | None = None
    content: str | None = None
    mode: str = "0644"

    @model_validator(mode="after")
    def _exactly_one_body(self) -> "FileEntry":
        if (self.source is None) == (self.content is None):
            raise ValueError("set exactly one of `source` or `content`")
        return self


class FileCook(FileStateCook[FileEntry]):
    manager = "file"
    entry_model = FileEntry

    def _target_path(self, name: str) -> Path:
        return Path(self.entries[name].path)

    def _render(self, name: str) -> bytes:
        entry = self.entries[name]
        if entry.source is not None:
            return (FILES_DIR / entry.source).read_bytes()
        return (entry.content or "").encode()

    def _parse_mode(self, name: str) -> int:
        return int(self.entries[name].mode, 8)

    def apply_resource(self, name: str) -> StateChangeOutcome:
        changed = write_if_changed(
            self._target_path(name),
            self._render(name),
            self._parse_mode(name),
            note=name,
        )
        return StateChangeOutcome(changed=changed)
