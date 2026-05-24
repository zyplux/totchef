"""Shared scaffolding for sys-conf-py cooks: log teeing, streamed subprocess
wrapping, idempotent file writes, TOON logging, binary discovery.
"""

import json
import os
import pwd
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from loguru import logger
from toon_format import encode

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


def become_user() -> None:
    """The one privilege-drop chokepoint, called by each forked user-scope cook
    before it works. Drops gid first (root can't set gid after dropping uid),
    reconstructs supplementary groups, then drops uid, and repoints HOME / USER /
    PATH at the invoking user so toolchains write into $HOME, not /root."""
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        sys.exit("ERROR: SUDO_USER not set; chef must be launched via sudo.")
    pw = pwd.getpwnam(sudo_user)
    os.setgid(pw.pw_gid)
    os.initgroups(sudo_user, pw.pw_gid)
    os.setuid(pw.pw_uid)
    home = pw.pw_dir
    os.environ["HOME"] = home
    os.environ["USER"] = sudo_user
    os.environ["LOGNAME"] = sudo_user
    os.environ["XDG_CACHE_HOME"] = f"{home}/.cache"
    # Toolchains install into these before they are on PATH; prepend so a fresh
    # bootstrap can find what an earlier cook just dropped here.
    bootstrap = ":".join(str(d) for d in bootstrap_bin_dirs())
    os.environ["PATH"] = f"{bootstrap}:{os.environ.get('PATH', '')}"


def log_toon(rows: list[dict], note: str = "") -> None:
    """Log a list of flat dicts as a TOON table, one logger line per row line."""
    if note:
        logger.info(note)
    for line in encode(rows).splitlines():
        logger.info(line)


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


def bootstrap_bin_dirs() -> tuple[Path, ...]:
    """Dirs rustup/bun/uv install into before they are on PATH. Resolved from
    the current $HOME at call time (not import time) so it follows become_user's
    privilege drop in a forked child."""
    home = Path.home()
    return (
        home / ".cargo/bin",
        home / ".bun/bin",
        home / ".local/bin",
        home / ".claude/local",
    )


def find_binary(name: str) -> Path | None:
    """PATH first, then the bootstrap dirs (rustup/bun/uv land there pre-PATH).
    Only call from user-scope context — the bootstrap dirs follow $HOME, which
    become_user repoints in the child; calling as root would probe /root."""
    if found := shutil.which(name):
        return Path(found)
    for d in bootstrap_bin_dirs():
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
