"""StateCook for [file.<name>] entries — install a file with exact content.

Each entry is one managed file. Its desired state is the hash of the intended
bytes (inline `content` or a `source` under src/files/); current is the hash of
what is on disk. Chef compares them and calls apply_one only when they differ —
so the `post_hook` (daemon-reload, enable, update-initramfs, …) fires only when
the file actually changed.

Field semantics (per [file.<name>] block):
  path     required. absolute destination path.
  content  inline file content (string). Mutually exclusive with `source`.
  source   filename under src/files/ to copy verbatim. Mutually exclusive with
           `content`.
  mode     optional octal string (default "0644"), e.g. "0755".
  pre_hook / post_hook
           optional bash snippets run by chef around the write, fired only when
           the file changed.

Privilege-agnostic: the cook just writes a file, so it defaults to needs_root =
False (CookBase). The [file] section in recipe.toml marks needs_root = true
because its current entries write under /usr/local/sbin and /etc; a user-scope
file entry would simply omit it.
"""

import hashlib
from pathlib import Path

from cook_base import ItemOutcome, StateCook, debug_main
from harness import SRC_DIR, write_if_changed

FILES_DIR = SRC_DIR / "files"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileCook(StateCook):
    manager = "file"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.entries: dict[str, dict] = section

    def _content(self, name: str) -> bytes:
        block = self.entries[name]
        if "source" in block and "content" in block:
            raise ValueError(f"[file.{name}] sets both `source` and `content`")
        if source := block.get("source"):
            return (FILES_DIR / source).read_bytes()
        if (content := block.get("content")) is not None:
            return content.encode()
        raise ValueError(f"[file.{name}] needs either `source` or `content`")

    def _path(self, name: str) -> Path:
        return Path(self.entries[name]["path"])

    def _mode(self, name: str) -> int:
        return int(self.entries[name].get("mode", "0644"), 8)

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
        return (block.get("pre_hook"), block.get("post_hook"))

    def apply_one(self, name: str) -> ItemOutcome:
        changed = write_if_changed(
            self._path(name), self._content(name), self._mode(name), note=name
        )
        return ItemOutcome(changed=changed)


if __name__ == "__main__":
    debug_main(FileCook)
