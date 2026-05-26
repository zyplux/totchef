"""Find the recipe to cook: an explicit `--recipe`/$TOTCHEF_RECIPE wins, else walk up from the cwd (project-local), else fall back to the per-user then system config locations."""

import os
import sys
from pathlib import Path

RECIPE_NAME = "recipe.toml"
RECIPE_ENV = "TOTCHEF_RECIPE"


def config_home() -> Path:
    """The per-user config root, honoring XDG_CONFIG_HOME (default ~/.config)."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _cwd_chain() -> list[Path]:
    start = Path.cwd()
    return [parent / RECIPE_NAME for parent in (start, *start.parents)]


def search_paths() -> list[Path]:
    """Every candidate recipe path in precedence order — used both to resolve and to explain a miss."""
    return [*_cwd_chain(), config_home() / "totchef" / RECIPE_NAME, Path("/etc/totchef") / RECIPE_NAME]


def find_recipe(explicit: Path | None = None) -> Path:
    """Resolve the recipe path: explicit flag, else $TOTCHEF_RECIPE, else the first existing candidate; exit listing where it looked when none is found."""
    if explicit is not None:
        if not explicit.is_file():
            sys.exit(f"ERROR: --recipe {explicit} does not exist.")
        return explicit.resolve()
    if env := os.environ.get(RECIPE_ENV):
        path = Path(env)
        if not path.is_file():
            sys.exit(f"ERROR: {RECIPE_ENV}={env} does not exist.")
        return path.resolve()
    for candidate in search_paths():
        if candidate.is_file():
            return candidate.resolve()
    looked = "\n".join(f"  - {path}" for path in search_paths())
    sys.exit(f"ERROR: no {RECIPE_NAME} found. Looked in:\n{looked}\nWrite one (see the README) or pass --recipe PATH.")
