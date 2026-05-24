"""Shared contract for sys-conf-py cooks. A cook is a thin manager that probes
and acts but holds no diff logic — chef owns the diff. Two shapes share
`CookBase`: VersionedCook (requested/list_installed/latest_available/sync) for
versioned packages, and StateCook (items/current/desired/hooks/apply_one) for
desired-state resources. `debug_main` inspects one cook in isolation. The full
contract is in CLAUDE.md.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict

from harness import load_section
from logs import log_toon, start_logging

Status = Literal["ok", "soft_fail", "hard_fail"]


class EntrySpec(BaseModel):
    """Base for every cook's recipe-entry schema. `extra='forbid'` rejects any key
    the cook doesn't declare, so a typo'd recipe key fails the run with a clear
    error instead of being silently ignored. A field without a default is a
    required key; the annotation is the type contract.

    `pre_hook`/`post_hook` live here so every state-cook entry accepts them: chef
    runs `pre_hook` as a guard (non-zero skips the item) and `post_hook` after a
    change. A cook with an intrinsic hook composes it with these (see chain_hooks)."""

    model_config = ConfigDict(extra="forbid")

    pre_hook: str | None = None
    post_hook: str | None = None


def chain_hooks(*commands: str | None) -> str | None:
    """Join the present shell commands with `&&` (all must succeed), or None when
    there are none — lets a cook's intrinsic hook compose with a recipe-declared
    one. For a `pre_hook` guard the `&&` is the right semantics: any non-zero link
    short-circuits and chef skips the item."""
    present = [command for command in commands if command]
    return " && ".join(present) if present else None


class PackagesConfig(EntrySpec):
    """Schema shared by the plain package-list sections (cargo, uv, apt_pkg, snap)."""

    packages: list[str] = []


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
    entry_model: ClassVar[type[EntrySpec] | None] = None

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
    start_logging()
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
