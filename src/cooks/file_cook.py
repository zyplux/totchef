"""StateCook for [file.<name>] entries — install a file with exact content.

Desired state is the hash of the intended bytes (inline `content` or a `source`
under src/files/); current is the hash on disk. Chef calls apply_one only when
they differ, so a `post_hook` (daemon-reload, update-initramfs, …) fires only
when the file changed. Fields: see recipe.toml's header.

Privilege-agnostic: writing a file isn't inherently root (needs_root = False by
default); recipe.toml grants root per entry where it writes under /usr/local or
/etc.
"""

import hashlib
from pathlib import Path

from pydantic import model_validator

from cook_base import EntrySpec, ItemOutcome, StateCook, debug_main
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


class FileCook(StateCook):
    manager = "file"
    entry_model = FileEntry

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.entries = {
            name: FileEntry.model_validate(raw) for name, raw in section.items()
        }

    def _content(self, name: str) -> bytes:
        block = self.entries[name]
        if block.source is not None:
            return (FILES_DIR / block.source).read_bytes()
        return (block.content or "").encode()

    def _path(self, name: str) -> Path:
        return Path(self.entries[name].path)

    def _mode(self, name: str) -> int:
        return int(self.entries[name].mode, 8)

    def items(self) -> list[str]:
        return list(self.entries)

    def current(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self.entries:
            path = self._path(name)
            out[name] = _digest(path.read_bytes()) if path.exists() else "absent"
        return out

    def desired(self) -> dict[str, str]:
        return {name: _digest(self._content(name)) for name in self.entries}

    def hooks(self, name: str) -> tuple[str | None, str | None]:
        block = self.entries[name]
        return (block.pre_hook, block.post_hook)

    def apply_one(self, name: str) -> ItemOutcome:
        changed = write_if_changed(
            self._path(name), self._content(name), self._mode(name), note=name
        )
        return ItemOutcome(changed=changed)


if __name__ == "__main__":
    debug_main(FileCook)
