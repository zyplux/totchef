"""Shared contract for sys-conf-py cooks (Phase 2).

Cooks are thin "managers": they know how to probe what is installed and how to
act, but hold **no idempotency/diff logic of their own** — chef owns the diff.
Two shapes share `CookBase`:

  VersionedCook  packages with a version (apt, cargo, uv, snap, url):
      requested()        -> names this cook manages
      list_installed()   -> {name: installed_version}
      latest_available() -> {name: latest_version | None}   (None = "—", best effort)
      sync(to_install, to_upgrade) -> Result
    Chef computes the install/upgrade split, calls sync once (the cook batches
    as its package manager requires), then re-probes to derive per-item changes.

  StateCook      desired-state resources (apt_repo, bash, file, desktop, …):
      items()            -> resource names
      current()          -> {name: current_state_token}
      desired()          -> {name: desired_state_token}
      hooks(name)        -> (pre, post) shell snippets, run by chef around apply
      apply_one(name)    -> ItemOutcome
    Chef compares current vs desired, and for each differing item runs
    pre -> apply_one -> post, firing hooks only when an action is taken.

`debug_main(cls)` is the standalone entry point (`python -m cooks.<cook>` from
src/, with SYS_CONF_PY_SECTION_JSON set) — it enforces the privilege contract and
prints the cook's probe so a single cook can be inspected in isolation. In a real
run chef imports cooks directly; it does not spawn them.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Literal

from loguru import logger

from harness import load_section, log_toon, start_log_tee

Status = Literal["ok", "soft_fail", "hard_fail"]


@dataclass(frozen=True)
class Result:
    """Outcome of a VersionedCook.sync (or any cook-level act). Expected
    failures land here as a status; only bugs raise."""

    status: Status = "ok"
    message: str = ""


@dataclass(frozen=True)
class ItemOutcome:
    """Outcome of a StateCook.apply_one for one resource."""

    changed: bool
    status: Status = "ok"
    message: str = ""


@dataclass(frozen=True)
class ItemReport:
    """One row of the end-of-run report, assembled by chef."""

    name: str
    manager: str
    installed: str
    latest: str
    action: str
    changed: bool
    status: Status = "ok"


@dataclass
class CookResult:
    """Everything chef needs from one cook: an aggregate status, the per-item
    report rows, and an optional cook-level message. Travels back from forked
    user-cook children to the root parent over a pipe (so it must stay
    picklable — plain dataclasses only)."""

    cook: str
    status: Status
    items: list[ItemReport] = field(default_factory=list)
    message: str = ""


class CookBase:
    """Base for every cook. Subclasses set `manager`; an always-root cook also
    sets `needs_root = True` and is named `<section>_root_cook.py`.

    `needs_root` is the cook's privilege default — False here, i.e. least
    privilege. Chef reads it as the final fallback when recipe.toml grants no
    per-entry needs_root, and debug_main uses it to refuse the wrong euid."""

    needs_root: bool = False
    manager: str = ""
    user_only_reason: str = "it writes into the invoking user's $HOME"

    def __init__(self, section: dict) -> None:
        self.section = section

    @property
    def cook_name(self) -> str:
        return type(self).__name__


class VersionedCook(CookBase):
    kind = "versioned"

    def requested(self) -> list[str]:
        raise NotImplementedError

    def list_installed(self) -> dict[str, str]:
        raise NotImplementedError

    def latest_available(self, names: list[str]) -> dict[str, str | None]:
        raise NotImplementedError

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> Result:
        raise NotImplementedError


class StateCook(CookBase):
    kind = "state"

    def items(self) -> list[str]:
        raise NotImplementedError

    def current(self) -> dict[str, str]:
        raise NotImplementedError

    def desired(self) -> dict[str, str]:
        raise NotImplementedError

    def hooks(self, name: str) -> tuple[str | None, str | None]:
        return (None, None)

    def apply_one(self, name: str) -> ItemOutcome:
        raise NotImplementedError


def _enforce_privilege(cls: type[CookBase]) -> None:
    is_root = os.geteuid() == 0
    if cls.needs_root and not is_root:
        sys.exit(
            f"ERROR: {cls.__name__} needs root but is not running as root. "
            "Run via `just up`; chef runs as root and drops privilege per cook."
        )
    if not cls.needs_root and is_root:
        sys.exit(
            f"ERROR: {cls.__name__} must run as the invoking user, not root — "
            f"{cls.user_only_reason} and would land under /root."
        )


def debug_main(cls: type[CookBase]) -> None:
    """Standalone probe for one cook (debugging). Loads the section chef would
    have passed via env, enforces the euid contract, and prints the cook's
    installed/current state as a TOON table. Does not act."""
    section = load_section()
    _enforce_privilege(cls)
    start_log_tee()
    cook = cls(section)

    if isinstance(cook, VersionedCook):
        names = cook.requested()
        installed = cook.list_installed()
        latest = cook.latest_available(names)
        rows = [
            {
                "name": n,
                "installed": installed.get(n, "(none)"),
                "latest": latest.get(n) or "—",
            }
            for n in names
        ]
    elif isinstance(cook, StateCook):
        current = cook.current()
        desired = cook.desired()
        rows = [
            {
                "name": n,
                "current": current.get(n, "?"),
                "desired": desired.get(n, "?"),
            }
            for n in cook.items()
        ]
    else:
        logger.info("Unknown cook kind; nothing to probe.")
        return

    if rows:
        log_toon(rows, note=f"{cook.cook_name} probe:")
    else:
        logger.info("Nothing to probe.")
