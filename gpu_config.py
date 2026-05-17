#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["loguru>=0.7", "toon-format>=0.9.0b1"]
# ///
"""
gpu_config.py — idempotent egpu-prime boot service install + GPU state probe.

Installs /usr/local/sbin/egpu-prime-switch and /etc/systemd/system/
egpu-prime.service, then enables the service. The service runs once at
boot before SDDM and selects `prime-select nvidia` when the eGPU is on
PCI, else `prime-select on-demand`. Re-runnable; only rewrites files
whose contents would change.

NVIDIA driver packages live in apt.toml; run apt_runner.py first.
"""

import os
import pwd
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger
from toon_format import encode

SCRIPT = Path(__file__).resolve()
FILES_DIR = SCRIPT.parent / "files"
LOG_DIR = SCRIPT.parent / "logs"

EGPU_SWITCH_SRC = FILES_DIR / "egpu-prime-switch"
EGPU_SERVICE_SRC = FILES_DIR / "egpu-prime.service"
EGPU_SWITCH_DST = Path("/usr/local/sbin/egpu-prime-switch")
EGPU_SERVICE_DST = Path("/etc/systemd/system/egpu-prime.service")

LOG_FORMAT = "[{time:YYYY-MM-DD HH:mm:ss}] {level: <7} {message}"

logger.remove()
logger.add(sys.stderr, format=LOG_FORMAT, level="INFO", colorize=False)


def run(*cmd: str, note: str = "", check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    if note:
        logger.info(note)
    return subprocess.run(list(cmd), check=check, **kwargs)


def write_if_changed(path: Path, data: bytes, mode: int, note: str = "") -> bool:
    if path.exists() and path.read_bytes() == data:
        logger.info(f"Unchanged: {path}")
        return False
    logger.info(f"Writing  : {path}" + (f"  ({note})" if note else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return True


def install_egpu_prime() -> None:
    changed = False
    changed |= write_if_changed(EGPU_SWITCH_DST, EGPU_SWITCH_SRC.read_bytes(),
                                0o755, note="boot-time prime-select switch")
    changed |= write_if_changed(EGPU_SERVICE_DST, EGPU_SERVICE_SRC.read_bytes(),
                                0o644, note="systemd unit, Before=display-manager")
    if changed:
        run("systemctl", "daemon-reload", note="systemctl daemon-reload")

    enabled = subprocess.run(
        ["systemctl", "is-enabled", "egpu-prime.service"],
        capture_output=True, text=True,
    ).stdout.strip()
    if enabled == "enabled":
        logger.info("egpu-prime.service already enabled")
    else:
        run("systemctl", "enable", "egpu-prime.service",
            note="enabling egpu-prime.service")


def gpu_state_row() -> dict:
    def out(*cmd: str) -> str:
        r = subprocess.run(list(cmd), capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else "(error)"

    nvidia_pci = bool(out("lspci", "-nn", "-d", "10de:"))
    prime = out("prime-select", "query") or "(not installed)"
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if not session and (sudo_user := os.environ.get("SUDO_USER")):
        r = subprocess.run(
            ["sudo", "-u", sudo_user, "sh", "-c", "echo $XDG_SESSION_TYPE"],
            capture_output=True, text=True,
        )
        session = r.stdout.strip() or "(unknown)"
    return {
        "nvidia_on_pci": "yes" if nvidia_pci else "no",
        "prime_mode": prime,
        "session_type": session or "(unknown)",
    }


def setup_log_tee() -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{SCRIPT.stem}-{datetime.now():%Y%m%d-%H%M%S}.log"
    log_file.touch()
    if sudo_user := os.environ.get("SUDO_USER"):
        pw = pwd.getpwnam(sudo_user)
        for p in (LOG_DIR, *LOG_DIR.iterdir()):
            os.chown(p, pw.pw_uid, pw.pw_gid)
    tee = subprocess.Popen(["tee", "-a", str(log_file)], stdin=subprocess.PIPE)
    os.dup2(tee.stdin.fileno(), 1)
    os.dup2(tee.stdin.fileno(), 2)
    tee.stdin.close()
    return log_file


def main() -> None:
    if os.geteuid() != 0:
        logger.info("Re-running under sudo")
        os.execvp("sudo", ["sudo", sys.executable, __file__, *sys.argv[1:]])

    log_file = setup_log_tee()
    logger.info(f"Logging this run to {log_file}")

    install_egpu_prime()

    logger.info("Current GPU state:")
    print(encode([gpu_state_row()]))

    logger.info("Done.")
    logger.info("Reboot to let egpu-prime.service pick the PRIME mode before SDDM starts.")


if __name__ == "__main__":
    main()
