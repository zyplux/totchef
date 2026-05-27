"""Meta-test: a story test asserts user-facing behavior through fixtures only.

The prose tests under `tests/stories/` are black-box — they drive totchef the way an
operator (or, for §8, a cook author writing a drop-in) does, and read back only what is
user-visible. So a story test file imports nothing: every seam it touches arrives as a
fixture, and reaching for a production symbol means it has stopped testing the story.

The §7.2/§7.3.2-3 tests (the live progress bar, per-cook log color, the scheduler's
wait/unlock lines, and the log pump) are the one acknowledged exception: they are driven
by the forking scheduler and the real file-logging the in-process story framework
deliberately does not run, and they assert rendering/timing properties a container would
only make flakier (PTY scraping, concurrency races) — so they stay white-box. The OS-state
half of the same boundary — who owns the files a cook wrote (§6.3.2) and the run's log
(§7.3.1) — is deterministic and genuinely unobservable in-process, so it runs a real
`totchef up` in a container via the `apply_in_container` fixture: still zero-import, since
the seam arrives as a fixture like every other. This exception is pinned below so the set
cannot quietly grow.
"""

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
