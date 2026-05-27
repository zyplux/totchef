"""Meta-test: story tests assert behavior through fixtures only, zero imports; §7.2/§7.3.2-3 white-box and §6.3.2/§7.3.1 container are pinned exceptions."""

import ast

from project_paths import STORIES_DIR

CONTAINER_BOUND = {"test_7_observing_a_run.py"}  # §7.2/§7.3.2-3: rendering/timing white-box (the ownership stories are fixture-driven, zero-import)


def _import_bearing_story_tests() -> set[str]:
    bearing = set()
    for path in STORIES_DIR.glob("test_*.py"):
        tree = ast.parse(path.read_text())
        if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)):
            bearing.add(path.name)
    return bearing


def test_story_tests_use_fixtures_only():
    bearing = _import_bearing_story_tests()
    unexpected = bearing - CONTAINER_BOUND
    resolved = CONTAINER_BOUND - bearing
    assert bearing == CONTAINER_BOUND, (
        f"story tests must reach production code only through fixtures (zero imports).\n"
        f"  unexpected import-bearing files: {sorted(unexpected)}\n"
        f"  exceptions now import-free (drop from CONTAINER_BOUND): {sorted(resolved)}"
    )
