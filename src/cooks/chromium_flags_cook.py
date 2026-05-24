"""StateCook for [chromium_flags] — GPU-flag injection for Chromium-family apps.

Two delivery mechanisms, picked per app by which marker it carries:
- `local_state`  Chromium `Local State` JSON: union brave://flags ids into
                 browser.enabled_labs_experiments. Guarded by a `pre_hook` that
                 skips the write while the browser is running (it would race the
                 browser's own write); a skip is benign, not a failure.
- `argv_json`    Electron argv.json: merge allowlisted flags + a synthesized
                 enable-features built from the shared [chromium].features.

Diffable: desired = hash of the rendered JSON, current = hash of what's on disk,
so unchanged apps are skipped. Runs as the invoking user (needs_root = false ->
chef forks + drops privilege); writes into $HOME, no chown.
"""

import hashlib
import json
from pathlib import Path

from apps_config import apps_with, load
from cook_base import ItemOutcome, StateCook, debug_main
from harness import logger, write_if_changed


def _strip_json_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))


class ChromiumFlagsCook(StateCook):
    manager = "chromium-flags"
    user_only_reason = "it writes browser config under $HOME"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        config = load()
        self.chromium_features: list[str] = config.get("chromium", {}).get(
            "features", []
        )
        self.apps = apps_with(config, "local_state", "argv_json")

    def items(self) -> list[str]:
        return list(self.apps)

    def _target(self, name: str) -> Path:
        app = self.apps[name]
        return Path.home() / (app.get("local_state") or app["argv_json"])

    def _render(self, name: str) -> bytes | None:
        """Desired file bytes, or None when there's nothing to do / no base file
        to patch. Returns the on-disk bytes verbatim when no flag would change,
        so desired == current and chef skips the entry."""
        app = self.apps[name]
        target = self._target(name)
        if "local_state" in app:
            flags = app.get("local_state_flags", [])
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
        argv = dict(app.get("argv", {}))
        features = self.chromium_features + app.get("features", [])
        if features:
            argv["enable-features"] = ",".join(features)
        existing: dict = {}
        if target.exists():
            stripped = _strip_json_comments(target.read_text())
            if stripped.strip():
                existing = json.loads(stripped)
        merged = {**existing, **argv}
        return (json.dumps(merged, indent=2) + "\n").encode()

    def current(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self.apps:
            target = self._target(name)
            out[name] = (
                hashlib.sha256(target.read_bytes()).hexdigest()
                if target.exists()
                else "absent"
            )
        return out

    def desired(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self.apps:
            content = self._render(name)
            out[name] = (
                hashlib.sha256(content).hexdigest() if content else "(no base file)"
            )
        return out

    def hooks(self, name: str) -> tuple[str | None, str | None]:
        app = self.apps[name]
        if "local_state" in app:
            process = app.get("process_name", name)
            # Guard: skip the Local State write while the browser is running.
            return (f"pgrep -x {process} >/dev/null && exit 1 || exit 0", None)
        return (None, None)

    def apply_one(self, name: str) -> ItemOutcome:
        content = self._render(name)
        if content is None:
            return ItemOutcome(
                changed=False,
                message=f"{self._target(name)} not found; launch the app once, then re-run.",
            )
        changed = write_if_changed(self._target(name), content, note=name)
        if changed:
            logger.info(f"{name}: restart the app to apply the new flags.")
        return ItemOutcome(changed=changed)


if __name__ == "__main__":
    debug_main(ChromiumFlagsCook)
