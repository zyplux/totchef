"""StateCook for [chromium_flags.<app>] — GPU-flag injection for Chromium apps.

Two delivery mechanisms, picked per app by which marker it carries:
- `local_state`  Chromium `Local State` JSON: union brave://flags ids into
                 browser.enabled_labs_experiments. Guarded by a `pre_hook` that
                 skips the write while the browser is running (it would race the
                 browser's own write); a skip is benign, not a failure.
- `argv_json`    Electron argv.json: merge allowlisted flags + a synthesized
                 enable-features built from the entry's `features`.

Diffable: desired = hash of the rendered JSON, current = hash of what's on disk,
so unchanged apps are skipped. Runs as the invoking user, writing into $HOME.
"""

import hashlib
import json
from pathlib import Path

from pydantic import model_validator

from cook_base import EntrySpec, StateChangeOutcome, StateCook, chain_hooks
from harness import logger, write_if_changed


def _strip_json_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))


class ChromiumFlagsEntry(EntrySpec):
    local_state: str | None = None
    local_state_flags: list[str] = []
    argv_json: str | None = None
    argv: dict[str, str | bool] = {}
    features: list[str] = []
    process_name: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "ChromiumFlagsEntry":
        if (self.local_state is None) == (self.argv_json is None):
            raise ValueError("set exactly one of `local_state` or `argv_json`")
        return self


class ChromiumFlagsCook(StateCook[ChromiumFlagsEntry]):
    manager = "chromium-flags"
    entry_model = ChromiumFlagsEntry

    def _target_path(self, name: str) -> Path:
        app = self.entries[name]
        return Path.home() / (app.local_state or app.argv_json or "")

    def _render(self, name: str) -> bytes | None:
        """Desired file bytes, or None when there's nothing to do / no base file
        to patch. Returns the on-disk bytes verbatim when no flag would change,
        so desired == current and chef skips the entry."""
        app = self.entries[name]
        target = self._target_path(name)
        if app.local_state is not None:
            flags = app.local_state_flags
            if not target.exists():
                return None
            raw = target.read_bytes()
            if not flags:
                return raw
            data = json.loads(raw)
            experiments = data.setdefault("browser", {}).setdefault(
                "enabled_labs_experiments", []
            )
            merged = sorted(set(experiments) | set(flags))
            if set(merged) == set(experiments):
                return raw
            data["browser"]["enabled_labs_experiments"] = merged
            return json.dumps(data, indent=2).encode()

        # argv_json (Electron)
        argv: dict[str, str | bool] = dict(app.argv)
        features = app.features
        if features:
            argv["enable-features"] = ",".join(features)
        existing: dict = {}
        if target.exists():
            stripped = _strip_json_comments(target.read_text())
            if stripped.strip():
                existing = json.loads(stripped)
        merged = {**existing, **argv}
        return (json.dumps(merged, indent=2) + "\n").encode()

    def get_current_state(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name in self.entries:
            target = self._target_path(name)
            states[name] = (
                hashlib.sha256(target.read_bytes()).hexdigest()
                if target.exists()
                else "absent"
            )
        return states

    def get_desired_state(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name in self.entries:
            content = self._render(name)
            states[name] = (
                hashlib.sha256(content).hexdigest() if content else "(no base file)"
            )
        return states

    def get_hooks(self, name: str) -> tuple[str | None, str | None]:
        app = self.entries[name]
        # Skip the Local State write while the browser runs (it would race the
        # browser's own write); `! pgrep` exits non-zero when found, so chef skips.
        guard = (
            f"! pgrep -x {app.process_name or name} >/dev/null"
            if app.local_state is not None
            else None
        )
        return (chain_hooks(guard, app.pre_hook), app.post_hook)

    def apply_resource(self, name: str) -> StateChangeOutcome:
        content = self._render(name)
        if content is None:
            return StateChangeOutcome(
                changed=False,
                message=f"{self._target_path(name)} not found; launch the app once, then re-run.",
            )
        changed = write_if_changed(self._target_path(name), content, note=name)
        if changed:
            logger.info(f"{name}: restart the app to apply the new flags.")
        return StateChangeOutcome(changed=changed)
