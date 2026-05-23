"""Shared scaffolding for sys-conf-py cooks: sudo re-exec, log teeing,
streamed subprocess wrapping, idempotent file writes, binary discovery.
"""

import json
import os
import pwd
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.request import Request, urlopen

from loguru import logger


@dataclass(frozen=True)
class Result:
    status: Literal["ok", "soft_fail", "hard_fail"]
    message: str
    changed: bool


@dataclass(frozen=True)
class VersionInfo:
    name: str
    installed_version: str
    available_version: str
    source: str
    status: Literal["installed", "needs_update", "missing", "unknown"]
    cook: str
    manager: str


SRC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SRC_DIR.parent
LOG_DIR = REPO_ROOT / "logs"
RECIPE_TOML = SRC_DIR / "recipe.toml"

LOG_FORMAT = "[{time:YYYY-MM-DD HH:mm:ss}] {extra[runner]: <22} {level: <7} {message}"

SHARED_LOG_ENV = "SYS_CONF_PY_LOG_FILE"
SECTION_ENV = "SYS_CONF_PY_SECTION_JSON"

# sysexits.h EX_TEMPFAIL: cook -> chef.py signal for recoverable failure.
SOFT_FAIL_EXIT = 75

# Configured at import so pre-sudo messages get timestamped too.
logger.remove()
logger.configure(extra={"runner": Path(sys.argv[0]).stem})
logger.add(sys.stderr, format=LOG_FORMAT, level="INFO", colorize=False)


def reexec_under_sudo(script: Path) -> None:
    if os.geteuid() != 0:
        logger.info("Re-running under sudo")
        os.execvp(
            "sudo",
            [
                "sudo",
                f"--preserve-env={SHARED_LOG_ENV},{SECTION_ENV}",
                sys.executable,
                str(script),
                *sys.argv[1:],
            ],
        )


def load_section() -> dict:
    """Read the recipe.toml slice chef.py passed us via SECTION_ENV."""
    payload = os.environ.get(SECTION_ENV)
    if payload is None:
        sys.exit(
            f"ERROR: {SECTION_ENV} not set; run via `just up`, not this cook directly."
        )
    return json.loads(payload)


def start_log_tee() -> Path:
    """Tee stdout/stderr into logs/<run>.log. Honors SHARED_LOG_ENV if set,
    else creates a timestamped file and exports it. Pre-chowns to SUDO_USER
    so root-written lines keep the original owner."""
    LOG_DIR.mkdir(exist_ok=True)
    if existing := os.environ.get(SHARED_LOG_ENV):
        log_file = Path(existing)
    else:
        log_file = LOG_DIR / f"sys-conf-py-{datetime.now():%Y%m%d-%H%M%S}.log"
        os.environ[SHARED_LOG_ENV] = str(log_file)
    log_file.touch(exist_ok=True)
    if sudo_user := os.environ.get("SUDO_USER"):
        pw = pwd.getpwnam(sudo_user)
        os.chown(LOG_DIR, pw.pw_uid, pw.pw_gid)
        os.chown(log_file, pw.pw_uid, pw.pw_gid)
    tee = subprocess.Popen(["tee", "-a", str(log_file)], stdin=subprocess.PIPE)
    assert tee.stdin is not None
    os.dup2(tee.stdin.fileno(), 1)
    os.dup2(tee.stdin.fileno(), 2)
    tee.stdin.close()
    return log_file


def get_invoking_user() -> tuple[str, int, int, Path]:
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        sys.exit("ERROR: SUDO_USER not set; run via sudo, not as root directly.")
    pw = pwd.getpwnam(sudo_user)
    return sudo_user, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)


def run(
    *cmd: str, note: str = "", check: bool = True, **kwargs
) -> subprocess.CompletedProcess:
    if note:
        logger.info(note)
    return subprocess.run(list(cmd), check=check, **kwargs)


def stream_subprocess(
    cmd: list[str],
    tag: str = "",
    *,
    note: str = "",
    stdin: bytes | None = None,
    check: bool = True,
) -> None:
    """Run `cmd`, stream merged stdout/stderr line-by-line through logger.info,
    optionally tagged per line. Raises CalledProcessError on non-zero unless
    check=False. TERM=dumb + NO_COLOR + start_new_session suppress ANSI and
    block /dev/tty bypass; CR-splits become separate log lines."""
    prefix = f"{tag} " if tag else ""
    if note:
        logger.info(f"{prefix}{note}")
    proc_env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=proc_env,
        start_new_session=True,
    )
    proc_stdout = proc.stdout
    assert proc_stdout is not None
    writer: threading.Thread | None = None
    if stdin is not None:
        proc_stdin = proc.stdin
        assert proc_stdin is not None

        def feed_stdin() -> None:
            try:
                proc_stdin.write(stdin)
            finally:
                proc_stdin.close()

        writer = threading.Thread(target=feed_stdin, daemon=True)
        writer.start()
    for raw in proc_stdout:
        decoded = raw.decode("utf-8", errors="replace").rstrip("\n")
        for segment in decoded.split("\r"):
            segment = segment.rstrip()
            if segment:
                logger.info(f"{prefix}{segment}")
    if writer is not None:
        writer.join()
    rc = proc.wait()
    if check and rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)


def write_if_changed(
    path: Path, content: bytes | str, mode: int = 0o644, note: str = ""
) -> bool:
    if isinstance(content, str):
        content = content.encode()
    if path.exists() and path.read_bytes() == content:
        logger.info(f"Unchanged: {path}")
        return False
    logger.info(f"Writing  : {path}" + (f"  ({note})" if note else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)
    return True


BOOTSTRAP_BIN_DIRS = (
    Path.home() / ".cargo/bin",
    Path.home() / ".bun/bin",
    Path.home() / ".local/bin",
    Path.home() / ".claude/local",
)


def find_binary(name: str) -> Path | None:
    """PATH first, then BOOTSTRAP_BIN_DIRS (rustup/bun/uv land here pre-PATH).
    Don't call after sudo re-exec — Path.home() was resolved at import."""
    if found := shutil.which(name):
        return Path(found)
    for d in BOOTSTRAP_BIN_DIRS:
        candidate = d / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


USER_AGENT = "sys-conf-py"


def fetch_url(url: str) -> bytes:
    """HTTP GET. Custom UA — Signal/herdr CDNs 403 the urllib default."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request) as response:
        return response.read()
