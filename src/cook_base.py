"""Shared contract for sys-conf-py cooks. A cook is a thin manager that probes
and acts but holds no diff logic — chef owns the diff. Two shapes share
`CookBase`: VersionedCook (list_requested/list_installed/find_latest/sync) for
versioned packages, and StateCook (list_resources/get_current_state/get_desired_state/get_hooks/apply_resource)
for desired-state resources. The full contract is in CLAUDE.md.
"""

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

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
class SyncOutcome:
    """Outcome of a VersionedCook.sync (or any cook-level act). Expected
    failures land here as a status; only bugs raise."""

    status: Status = "ok"
    message: str = ""


@dataclass(frozen=True)
class StateChangeOutcome:
    """Outcome of a StateCook.apply_resource for one resource."""

    changed: bool
    status: Status = "ok"
    message: str = ""


@dataclass(frozen=True)
class ReportRow:
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
    """Everything chef needs from one cook: an aggregate status, the report rows,
    and an optional cook-level message. Travels back from forked
    user-cook children to the root parent over a pipe (so it must stay
    picklable — plain dataclasses only)."""

    cook: str
    status: Status
    rows: list[ReportRow] = field(default_factory=list)
    message: str = ""


class CookBase:
    """Base for every cook. Subclasses set `manager`; an always-root cook also
    sets `needs_root = True` and is named `<section>_root_cook.py`.

    `needs_root` is the cook's privilege default — False here, i.e. least
    privilege. Chef reads it as the final fallback when recipe.toml grants no
    per-entry needs_root."""

    needs_root: bool = False
    manager: str = ""
    entry_model: ClassVar[type[EntrySpec] | None] = None

    def __init__(self, section: dict) -> None:
        self.section = section


class VersionedCook(CookBase):
    kind = "versioned"

    def list_requested(self) -> list[str]:
        raise NotImplementedError

    def list_installed(self) -> dict[str, str]:
        raise NotImplementedError

    def find_latest(self, names: list[str]) -> dict[str, str | None]:
        raise NotImplementedError

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> SyncOutcome:
        raise NotImplementedError


class StateCook(CookBase):
    kind = "state"

    def list_resources(self) -> list[str]:
        raise NotImplementedError

    def get_current_state(self) -> dict[str, str]:
        raise NotImplementedError

    def get_desired_state(self) -> dict[str, str]:
        raise NotImplementedError

    def get_hooks(self, name: str) -> tuple[str | None, str | None]:
        return (None, None)

    def apply_resource(self, name: str) -> StateChangeOutcome:
        raise NotImplementedError
