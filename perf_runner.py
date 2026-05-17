#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["loguru>=0.7", "toon-format>=0.9.0b1"]
# ///
"""
perf_runner.py — idempotent GPU performance config from perf.toml.

Stages: egpu-prime boot service, Brave .desktop launcher override, Brave Local
State sync. Driver packages live in apt.toml; run apt_runner.py first.
"""

import json
import os
import pwd
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from loguru import logger
from toon_format import encode

SCRIPT = Path(__file__).resolve()
PERF_TOML = SCRIPT.parent / "perf.toml"
FILES_DIR = SCRIPT.parent / "files"
LOG_DIR = SCRIPT.parent / "logs"

EGPU_SWITCH_SRC = FILES_DIR / "egpu-prime-switch"
EGPU_SERVICE_SRC = FILES_DIR / "egpu-prime.service"
EGPU_SWITCH_DST = Path("/usr/local/sbin/egpu-prime-switch")
EGPU_SERVICE_DST = Path("/etc/systemd/system/egpu-prime.service")

SYSTEM_BRAVE_DESKTOP = Path("/usr/share/applications/brave-browser.desktop")
BRAVE_LOCAL_STATE_REL = Path(".config/BraveSoftware/Brave-Browser/Local State")

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


def get_invoking_user() -> tuple[str, int, int, Path]:
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        sys.exit("ERROR: SUDO_USER not set; run via sudo, not as root directly.")
    pw = pwd.getpwnam(sudo_user)
    return sudo_user, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)


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


def rewrite_exec_line(
    exec_value: str,
    env: dict[str, str],
    features: list[str],
    switches: list[str],
) -> str:
    """Idempotent rewrite of a .desktop Exec= value with env prefix, --<switch>s, and
    --enable-features. New args insert before trailing field codes (%U, %u, %F, %f)."""
    tokens = exec_value.split()

    if tokens and tokens[0] == "env":
        i = 1
        while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
            i += 1
        tokens = tokens[i:]

    managed_switches = {f"--{s}" for s in switches}
    tokens = [t for t in tokens
              if not t.startswith("--enable-features=") and t not in managed_switches]

    insert_at = next(
        (i for i, t in enumerate(tokens) if len(t) == 2 and t.startswith("%")),
        len(tokens),
    )
    for sw in switches:
        tokens.insert(insert_at, f"--{sw}")
        insert_at += 1
    if features:
        tokens.insert(insert_at, f"--enable-features={','.join(features)}")

    if env:
        tokens = ["env", *(f"{k}={v}" for k, v in env.items()), *tokens]

    return " ".join(tokens)


def write_brave_desktop(brave_cfg: dict, uid: int, gid: int, home: Path) -> None:
    if not SYSTEM_BRAVE_DESKTOP.exists():
        logger.warning(f"{SYSTEM_BRAVE_DESKTOP} not found; skipping Brave .desktop "
                       "(install brave-browser via apt_runner.py first)")
        return

    env = brave_cfg.get("env", {})
    features = [f for group in brave_cfg.get("features", {}).values() for f in group]
    switches = brave_cfg.get("switches", [])

    new_lines = []
    rewritten = 0
    for line in SYSTEM_BRAVE_DESKTOP.read_text().splitlines():
        if line.startswith("Exec="):
            new_lines.append("Exec=" + rewrite_exec_line(line[5:], env, features, switches))
            rewritten += 1
        else:
            new_lines.append(line)
    new_text = "\n".join(new_lines) + "\n"

    dst_dir = home / ".local/share/applications"
    dst = dst_dir / SYSTEM_BRAVE_DESKTOP.name
    dst_dir.mkdir(parents=True, exist_ok=True)
    os.chown(dst_dir, uid, gid)

    if dst.exists() and dst.read_text() == new_text:
        logger.info(f"Unchanged: {dst}")
    else:
        logger.info(f"Writing  : {dst}  ({rewritten} Exec= line(s) rewritten)")
        dst.write_text(new_text)
        os.chown(dst, uid, gid)
        dst.chmod(0o644)


def patch_brave_local_state(brave_cfg: dict, uid: int, gid: int, home: Path) -> None:
    flags = brave_cfg.get("local_state_flags", [])
    if not flags:
        return

    local_state = home / BRAVE_LOCAL_STATE_REL
    if not local_state.exists():
        logger.warning(f"{local_state} not found; skipping Local State patch "
                       "(launch Brave once, then re-run)")
        return

    if subprocess.run(["pgrep", "-x", "brave"], capture_output=True).returncode == 0:
        logger.warning("Brave is running; skipping Local State patch (would race the write).")
        logger.warning("Quit Brave and re-run perf_runner.py to sync brave://flags UI state.")
        return

    data = json.loads(local_state.read_text())
    experiments = data.setdefault("browser", {}).setdefault("enabled_labs_experiments", [])
    before = set(experiments)
    after = before | set(flags)
    if after == before:
        logger.info(f"Local State already has all {len(flags)} brave://flags entries")
        return

    data["browser"]["enabled_labs_experiments"] = sorted(after)
    local_state.write_text(json.dumps(data, indent=2))
    os.chown(local_state, uid, gid)
    logger.info(f"Local State: added {sorted(after - before)}")


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


def main() -> None:
    with PERF_TOML.open("rb") as f:
        config = tomllib.load(f)

    if os.geteuid() != 0:
        logger.info("Re-running under sudo")
        os.execvp("sudo", ["sudo", sys.executable, __file__, *sys.argv[1:]])

    log_file = setup_log_tee()
    logger.info(f"Logging this run to {log_file}")
    logger.info(f"Loaded config from {PERF_TOML}")

    sudo_user, uid, gid, home = get_invoking_user()
    logger.info(f"Acting on behalf of {sudo_user}  (uid={uid}, home={home})")

    install_egpu_prime()

    brave_cfg = config.get("brave", {})
    if brave_cfg:
        write_brave_desktop(brave_cfg, uid, gid, home)
        patch_brave_local_state(brave_cfg, uid, gid, home)
    else:
        logger.info("No [brave] section in perf.toml; skipping Brave config")

    logger.info("Current GPU state:")
    print(encode([gpu_state_row()]))

    logger.info("Done.")
    logger.info("Reboot to let egpu-prime.service pick the PRIME mode before SDDM starts.")
    logger.info("After Brave restart, verify at brave://gpu (Graphics Feature Status + "
                "Video Acceleration Information).")


if __name__ == "__main__":
    main()
