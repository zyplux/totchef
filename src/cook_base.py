"""Shared contract for sys-conf-py cooks.

Every cook is a class subclassing `CookBase`, owning one recipe.toml section.
The public surface is two synchronous methods:

  install_or_update() -> Result          do the work; never raise for expected
                                          failures (return soft_fail/hard_fail)
  show_version()      -> list[VersionInfo]  read-only probe; always a list

`main_for(cls)` is the entry point each cook calls from `__main__`: it loads the
section chef.py passed, enforces the privilege contract (root cooks must be
root, user cooks must not), tees the log, runs `install_or_update`, and maps the
returned `Result.status` onto the chef exit-code contract (0 / 75 / 1). Passing
`--show-version` instead prints the `show_version` probe as a TOON table — handy
when debugging a single cook in isolation.

Concurrency is the cook's own business in Phase 1 (uv/url keep their thread
pools); chef walks sections sequentially in topological order.
"""

import os
import sys
from dataclasses import dataclass
from typing import Literal

from loguru import logger
from toon_format import encode

from harness import SOFT_FAIL_EXIT, load_section, start_log_tee

Status = Literal["ok", "soft_fail", "hard_fail"]
VersionStatus = Literal["installed", "needs_update", "missing", "unknown"]


@dataclass(frozen=True)
class Result:
    """Outcome of `install_or_update`. `changed` lets chef report what moved
    this run (wired in Phase 2). Expected failures land here as a status, not
    an exception; only bugs propagate."""

    status: Status
    message: str = ""
    changed: bool = False


@dataclass(frozen=True)
class VersionInfo:
    """One row of `show_version` output. The contract is loose in Phase 1 —
    nothing branches on it yet — so cooks emit a best-effort snapshot."""

    name: str
    installed_version: str
    available_version: str
    source: str
    status: VersionStatus
    cook: str
    manager: str


class CookBase:
    """Base for every cook. Subclasses set `needs_root` / `manager` and
    implement the two public methods.

    `needs_root` must agree with the section's `needs_root` in recipe.toml:
    it is the cook's own copy of the privilege contract chef enforces from the
    other side, so a cook run by hand still refuses the wrong euid."""

    needs_root: bool = False
    manager: str = ""
    user_only_reason: str = "it writes into the invoking user's $HOME"

    def __init__(self, section: dict) -> None:
        self.section = section

    @property
    def cook_name(self) -> str:
        return type(self).__name__

    def install_or_update(self) -> Result:
        raise NotImplementedError

    def show_version(self) -> list[VersionInfo]:
        raise NotImplementedError


def _enforce_privilege(cls: type[CookBase]) -> None:
    is_root = os.geteuid() == 0
    if cls.needs_root and not is_root:
        sys.exit(
            f"ERROR: {cls.__name__} needs root but is not running as root. "
            "Run via `just up`; chef spawns root cooks under sudo."
        )
    if not cls.needs_root and is_root:
        sys.exit(
            f"ERROR: {cls.__name__} must run as the invoking user, not root — "
            f"{cls.user_only_reason} and would land under /root."
        )


def _print_versions(cook: CookBase) -> None:
    rows = [vars(info) for info in cook.show_version()]
    if not rows:
        logger.info("No version information to report.")
        return
    for line in encode(rows).splitlines():
        logger.info(line)


def main_for(cls: type[CookBase]) -> None:
    """Standard cook entry point: load section, enforce privilege, tee the log,
    run, and translate `Result.status` into the chef exit-code contract."""
    section = load_section()
    _enforce_privilege(cls)
    cook = cls(section)
    start_log_tee()

    if "--show-version" in sys.argv[1:]:
        _print_versions(cook)
        return

    result = cook.install_or_update()
    if result.message:
        log = logger.error if result.status == "hard_fail" else logger.info
        log(result.message)
    if result.status == "soft_fail":
        sys.exit(SOFT_FAIL_EXIT)
    if result.status == "hard_fail":
        sys.exit(1)
