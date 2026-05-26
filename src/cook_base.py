"""Shared contract for sys-conf-py cooks. A cook is a thin manager that probes
and acts but holds no diff logic — chef owns the diff. Two shapes share
`CookBase`: VersionedCook (list_requested/list_installed/find_latest/sync) for
versioned packages, and StateCook (list_resources/get_current_state/get_desired_state/get_hooks/apply_resource)
for desired-state resources. The full contract is in CLAUDE.md.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict

Status = Literal["ok", "soft_fail", "hard_fail"]


class EntrySpec(BaseModel):
    """Base for every cook's recipe-entry schema. `extra='forbid'` rejects any key
    the cook doesn't declare, so a typo'd recipe key fails the run with a clear
    error instead of being silently ignored. A field without a default is a
    required key; the annotation is the type contract."""

    model_config = ConfigDict(extra="forbid")


class StateEntrySpec(EntrySpec):
    """Base for a StateCook entry schema. Adds the hook pair every desired-state
    entry accepts: chef runs `pre_hook` as a guard (non-zero skips the item) and
    `post_hook` after a change. They live here, not on EntrySpec, because only
    StateCook honors hooks — a versioned section (cargo, uv, snap, apt_pkg, url)
    keeps the bare EntrySpec, so `extra='forbid'` rejects a hook there at lint time
    rather than accepting one the runtime would silently never run. A cook with an
    intrinsic hook composes it with these (see chain_hooks)."""

    pre_hook: str | None = None
    post_hook: str | None = None


def chain_hooks(*commands: str | None) -> str | None:
    """Join present shell commands with `&&` (None if none) so an intrinsic hook composes with a recipe-declared one; a non-zero link short-circuits a pre_hook guard."""
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

    @property
    def unit_count(self) -> int:
        """Discrete units of work this cook represents — one by default, weighting its scheduler pull; a versioned cook overrides with its package count."""
        return 1


class VersionedCook(CookBase):
    @property
    def unit_count(self) -> int:
        return len(self.list_requested())

    def list_requested(self) -> list[str]:
        raise NotImplementedError

    def list_installed(self) -> dict[str, str]:
        raise NotImplementedError

    def find_latest(self, names: list[str]) -> dict[str, str | None]:
        raise NotImplementedError

    def sync(self, to_install: list[str], to_upgrade: list[str]) -> SyncOutcome:
        raise NotImplementedError


class PackageListCook(VersionedCook):
    """VersionedCook over a plain `packages = [...]` section (cargo, uv, snap,
    apt_pkg). The base validates the list into `self.packages` and serves
    `list_requested` plus a no-op `find_latest` — most managers have no cheap
    "latest" probe, so chef derives the change from the installed version moving.
    A manager with a cheap candidate (apt) overrides `find_latest`; every cook
    still implements `list_installed` and `sync`."""

    entry_model = PackagesConfig

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.packages = PackagesConfig.model_validate(section).packages

    def list_requested(self) -> list[str]:
        return self.packages

    def find_latest(self, names: list[str]) -> dict[str, str | None]:
        return dict.fromkeys(names)


class StateCook[EntryModel: StateEntrySpec](CookBase):
    """Desired-state cook over a subtable section. The base validates every entry
    against `entry_model` into `self.entries` (name -> typed StateEntrySpec) and
    serves the two members every such cook shares: `list_resources` and the default
    `get_hooks` (pre_hook/post_hook straight off the entry). Subclasses implement
    the diff — `get_current_state` / `get_desired_state` / `apply_resource` — and
    override `get_hooks` only to compose an intrinsic hook (see chain_hooks)."""

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        model = self.entry_model
        assert model is not None, f"{type(self).__name__} must set entry_model"
        self.entries: dict[str, EntryModel] = {name: cast("EntryModel", model.model_validate(raw)) for name, raw in section.items()}

    def list_resources(self) -> list[str]:
        return list(self.entries)

    def get_current_state(self) -> dict[str, str]:
        raise NotImplementedError

    def get_desired_state(self) -> dict[str, str]:
        raise NotImplementedError

    def get_hooks(self, name: str) -> tuple[str | None, str | None]:
        entry = self.entries[name]
        return (entry.pre_hook, entry.post_hook)

    def apply_resource(self, name: str) -> StateChangeOutcome:
        raise NotImplementedError


class FileStateCook[EntryModel: StateEntrySpec](StateCook[EntryModel]):
    """A StateCook whose diff is a content hash: current state is the sha256 of the
    file on disk (or "absent"), desired is the sha256 of the rendered bytes (or
    `_unrendered_label`, when there is no base file present to render against).
    Subclasses provide `_target_path` (where the file lives) and `_render` (its
    desired bytes, or None when there is no base to patch); they keep their own
    `apply_resource`, since the write's mode and its user-facing messages differ
    per cook."""

    _unrendered_label = "absent"

    def _target_path(self, name: str) -> Path:
        raise NotImplementedError

    def _render(self, name: str) -> bytes | None:
        raise NotImplementedError

    def get_current_state(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name in self.entries:
            path = self._target_path(name)
            states[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"
        return states

    def get_desired_state(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name in self.entries:
            content = self._render(name)
            states[name] = hashlib.sha256(content).hexdigest() if content is not None else self._unrendered_label
        return states
