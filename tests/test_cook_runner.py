"""The diff engine — chef's idempotency guarantee. run_versioned and run_state
turn a cook's probes into the install/upgrade/unchanged/missing/failed verdict and
drive the pre/apply/post lifecycle. Fakes stand in for real cooks so the
classification logic is exercised without a package manager or the filesystem."""

import cook_runner
from cook_base import StateChangeOutcome, StateCook, SyncOutcome, VersionedCook
from cook_runner import (
    format_state,
    format_version,
    pick_worst_status,
    run_state,
    run_versioned,
)

HEX = "a" * 64
HEX2 = "b" * 64


# --- small formatters ---


def test_pick_worst_status_picks_highest_rank():
    assert pick_worst_status([]) == "ok"
    assert pick_worst_status(["ok", "soft_fail"]) == "soft_fail"
    assert pick_worst_status(["soft_fail", "hard_fail", "ok"]) == "hard_fail"


def test_format_version_dashes_when_empty():
    assert format_version(None) == "—"
    assert format_version("") == "—"
    assert format_version("1.2.3") == "1.2.3"


def test_format_state_hides_content_digest_as_present():
    assert format_state(HEX) == "present"
    assert format_state("absent") == "absent"
    assert format_state("configured") == "configured"


# --- run_versioned ---


class FakeVersioned(VersionedCook):
    manager = "fake"

    def __init__(self, requested, before, after, latest, outcome=SyncOutcome("ok")):
        self._requested = requested
        self._before = before
        self._after = after
        self._latest = latest
        self._outcome = outcome
        self._installed_calls = 0
        self.synced: tuple[list[str], list[str]] | None = None

    def list_requested(self):
        return self._requested

    def list_installed(self):
        self._installed_calls += 1
        return self._before if self._installed_calls == 1 else self._after

    def find_latest(self, names):
        return self._latest

    def sync(self, to_install, to_upgrade):
        self.synced = (to_install, to_upgrade)
        return self._outcome


def test_run_versioned_dry_run_classifies_each_package():
    cook = FakeVersioned(
        requested=["new", "stale", "current", "noprobe"],
        before={"stale": "1.0", "current": "1.0", "noprobe": "2.0"},
        after={},
        latest={"new": "1.0", "stale": "2.0", "current": "1.0", "noprobe": None},
    )
    rows = {r.name: r for r in run_versioned(cook, "sec", dry_run=True).rows}
    assert (rows["new"].action, rows["new"].changed) == ("would install", True)
    assert (rows["stale"].action, rows["stale"].changed) == ("would upgrade", True)
    assert (rows["current"].action, rows["current"].changed) == ("up-to-date", False)
    assert (rows["noprobe"].action, rows["noprobe"].changed) == ("would sync", True)
    assert cook.synced is None  # dry run never syncs


def test_run_versioned_splits_install_from_upgrade_and_reports_outcome():
    cook = FakeVersioned(
        requested=["new", "stale", "current", "gone"],
        before={"stale": "1.0", "current": "1.0", "gone": "9"},
        after={"new": "1.0", "stale": "2.0", "current": "1.0"},
        latest={"new": None, "stale": "2.0", "current": "1.0", "gone": None},
    )
    result = run_versioned(cook, "sec", dry_run=False)
    rows = {r.name: r for r in result.rows}
    # to_install = absent; to_upgrade = present with a moved-or-unknown latest
    # (current, whose latest equals installed, is excluded).
    assert cook.synced == (["new"], ["stale", "gone"])
    assert rows["new"].action == "installed"
    assert rows["stale"].action == "upgraded"
    assert rows["current"].action == "unchanged"
    assert rows["gone"].action == "missing"  # requested, still absent, run ok
    assert result.status == "ok"


def test_run_versioned_marks_absent_after_hard_fail_as_failed():
    cook = FakeVersioned(
        requested=["pkg"],
        before={},
        after={},
        latest={"pkg": None},
        outcome=SyncOutcome("hard_fail", "boom"),
    )
    result = run_versioned(cook, "sec", dry_run=False)
    assert result.status == "hard_fail"
    assert result.rows[0].action == "failed"
    assert result.message == "boom"


# --- run_state ---


class FakeState(StateCook):
    manager = "fake"

    def __init__(self, current, desired, outcomes=None, hooks=None):
        self._current = current
        self._desired = desired
        self._outcomes = outcomes or {}
        self._hooks = hooks or {}
        self.applied: list[str] = []

    def list_resources(self):
        return list(self._desired)

    def get_current_state(self):
        return self._current

    def get_desired_state(self):
        return self._desired

    def get_hooks(self, name):
        return self._hooks.get(name, (None, None))

    def apply_resource(self, name):
        self.applied.append(name)
        return self._outcomes.get(name, StateChangeOutcome(changed=True))


def test_run_state_dry_run_flags_changes_and_stale_digests():
    cook = FakeState(
        current={"add": "absent", "same": "present", "edit": HEX},
        desired={"add": "present", "same": "present", "edit": HEX2},
    )
    rows = {r.name: r for r in run_state(cook, "sec", dry_run=True).rows}
    assert (rows["add"].action, rows["add"].changed) == ("would apply", True)
    assert (rows["same"].action, rows["same"].changed) == ("ok", False)
    assert rows["edit"].action == "would apply"
    assert (
        rows["edit"].installed == "stale"
    )  # a changing content digest reads as 'stale'
    assert cook.applied == []  # dry run never applies


def test_run_state_applies_only_drifted_resources():
    cook = FakeState(
        current={"drift": "absent", "ok": "present"},
        desired={"drift": "present", "ok": "present"},
    )
    rows = {r.name: r for r in run_state(cook, "sec", dry_run=False).rows}
    assert cook.applied == ["drift"]
    assert rows["drift"].action == "changed"
    assert rows["ok"].action == "unchanged"


def test_run_state_skips_when_pre_hook_is_not_satisfied(monkeypatch):
    monkeypatch.setattr(cook_runner, "run_pre_hook", lambda snippet, tag: False)
    cook = FakeState(
        current={"x": "absent"},
        desired={"x": "present"},
        hooks={"x": ("guard-cmd", None)},
    )
    result = run_state(cook, "sec", dry_run=False)
    assert cook.applied == []
    assert result.rows[0].action == "skipped"
    assert result.status == "ok"


def test_run_state_post_hook_failure_downgrades_to_soft_fail(monkeypatch):
    monkeypatch.setattr(cook_runner, "run_post_hook", lambda snippet, tag: "soft_fail")
    cook = FakeState(
        current={"x": "absent"},
        desired={"x": "present"},
        outcomes={"x": StateChangeOutcome(changed=True)},
        hooks={"x": (None, "refresh-cmd")},
    )
    result = run_state(cook, "sec", dry_run=False)
    assert result.rows[0].action == "post-failed"
    assert result.status == "soft_fail"


def test_run_state_apply_hard_fail_is_reported():
    cook = FakeState(
        current={"x": "absent"},
        desired={"x": "present"},
        outcomes={
            "x": StateChangeOutcome(changed=False, status="hard_fail", message="nope")
        },
    )
    result = run_state(cook, "sec", dry_run=False)
    assert result.rows[0].action == "failed"
    assert result.status == "hard_fail"


def test_run_state_post_hook_is_skipped_when_apply_made_no_change(monkeypatch):
    ran_post = False

    def record(snippet, tag):
        nonlocal ran_post
        ran_post = True
        return "ok"

    monkeypatch.setattr(cook_runner, "run_post_hook", record)
    # Drift triggers apply, but apply reports changed=False, so post_hook must not run.
    cook = FakeState(
        current={"x": "absent"},
        desired={"x": "present"},
        outcomes={"x": StateChangeOutcome(changed=False)},
        hooks={"x": (None, "refresh-cmd")},
    )
    result = run_state(cook, "sec", dry_run=False)
    assert ran_post is False
    assert result.rows[0].action == "unchanged"
