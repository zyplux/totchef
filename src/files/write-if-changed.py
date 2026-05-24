#!/usr/bin/env python3
"""Idempotent file writer for [bash.*] snippets: read stdin, write it to the
destination path only if the bytes differ, and log Unchanged:/Writing  : to
match harness.write_if_changed.

Installed verbatim to /usr/local/bin by the [file.write_if_changed] entry, so it
is on $PATH before any bash snippet runs. Standalone /usr/bin/python3 + stdlib only
(no harness/loguru import): it must work the same when invoked by a root bash
snippet that has no access to the project venv.

Usage:  <producer> | write-if-changed <dest-path> [octal-mode]
"""

import sys
from pathlib import Path

dest = Path(sys.argv[1])
mode = int(sys.argv[2], 8) if len(sys.argv) > 2 else 0o644
content = sys.stdin.buffer.read()

if dest.exists() and dest.read_bytes() == content:
    print(f"Unchanged: {dest}")
    sys.exit(0)

print(f"Writing  : {dest}")
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_bytes(content)
dest.chmod(mode)
