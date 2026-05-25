"""Cook execution engine: chef diffs each cook (VersionedCook by install/upgrade
split, StateCook by current vs desired) and acts. `run_recipe` walks the graph with
graphlib.TopologicalSorter, running ready nodes concurrently — a root node
in-process, a user node in a forked child that drops privilege via
harness.become_user() and pipes its CookResult back.
"""

import os
import pickle
import re
import traceback
from graphlib import TopologicalSorter

from loguru import logger

from cook_base import CookResult, ReportRow, StateCook, Status, VersionedCook
from harness import become_user, stream_subprocess
from recipe_graph import (
    Node,
    build_node_graph,
    build_nodes,
    load_cook_class,
    node_slice,
)
from terminal import progress_region

STATUS_RANK: dict[Status, int] = {"ok": 0, "soft_fail": 1, "hard_fail": 2}


def pick_worst_status(statuses: list[Status]) -> Status:
    if not statuses:
        return "ok"
    return max(statuses, key=lambda s: STATUS_RANK[s])


def format_version(version: str | None) -> str:
    return version if version else "—"


CONTENT_DIGEST = re.compile(r"[0-9a-f]{64}")


def format_state(token: str) -> str:
    """Render a state cook's diff token for the report. A 64-char sha256 content
    digest carries no human meaning, so show presence; readable state tokens
    (absent / present / configured / set) pass through unchanged."""
    return "present" if CONTENT_DIGEST.fullmatch(token) else token


def run_pre_hook(snippet: str, tag: str) -> bool:
    """A `pre_hook` is a guard: zero exit -> proceed; non-zero -> skip this item
    (a benign skip, e.g. "browser is running", not a failure)."""
    try:
        stream_subprocess(["bash", "-c", snippet], tag, note=f"pre_hook: {snippet}")
        return True
    except Exception:
        logger.info(f"{tag} pre_hook not satisfied; skipping")
        return False


def run_post_hook(snippet: str, tag: str) -> Status:
    """A `post_hook` runs after a successful change; non-zero -> soft failure."""
    try:
        stream_subprocess(["bash", "-c", snippet], tag, note=f"post_hook: {snippet}")
        return "ok"
    except Exception as exc:
        logger.warning(f"{tag} post_hook failed: {exc}")
        return "soft_fail"


def run_versioned(cook: VersionedCook, section: str, dry_run: bool) -> CookResult:
    requested = cook.list_requested()
    installed_before = cook.list_installed()
    latest = cook.find_latest(requested)

    if dry_run:
        rows: list[ReportRow] = []
        for name in requested:
            installed = installed_before.get(name)
            available = latest.get(name)
            if installed is None:
                action, changed = "would install", True
            elif available is None:
                action, changed = "would sync", True
            elif available != installed:
                action, changed = "would upgrade", True
            else:
                action, changed = "up-to-date", False
            rows.append(
                ReportRow(
                    name,
                    cook.manager,
                    installed or "(none)",
                    format_version(available),
                    action,
                    changed,
                )
            )
        return CookResult(section, "ok", rows)

    to_install = [n for n in requested if n not in installed_before]
    to_upgrade = [
        n
        for n in requested
        if n in installed_before
        and (latest.get(n) is None or latest[n] != installed_before[n])
    ]
    result = cook.sync(to_install, to_upgrade)
    if result.message:
        (logger.error if result.status == "hard_fail" else logger.info)(result.message)

    installed_after = cook.list_installed()
    rows = []
    for name in requested:
        before = installed_before.get(name)
        after = installed_after.get(name)
        if before is None and after is not None:
            action, changed = "installed", True
        elif before is not None and after is not None and before != after:
            action, changed = "upgraded", True
        elif after is None:
            action = "failed" if result.status == "hard_fail" else "missing"
            changed = False
        else:
            action, changed = "unchanged", False
        rows.append(
            ReportRow(
                name,
                cook.manager,
                before or "(none)",
                format_version(latest.get(name)),
                action,
                changed,
            )
        )
    return CookResult(section, result.status, rows, result.message)


def run_state(cook: StateCook, section: str, dry_run: bool) -> CookResult:
    resources = cook.list_resources()
    current = cook.get_current_state()
    desired = cook.get_desired_state()
    to_apply = [n for n in resources if current.get(n) != desired.get(n)]

    rows: list[ReportRow] = []
    if dry_run:
        for name in resources:
            will = name in to_apply
            current_token = current.get(name, "?")
            current_label = (
                "stale"
                if will and CONTENT_DIGEST.fullmatch(current_token)
                else format_state(current_token)
            )
            rows.append(
                ReportRow(
                    name,
                    cook.manager,
                    current_label,
                    format_state(desired.get(name, "?")),
                    "would apply" if will else "ok",
                    will,
                )
            )
        return CookResult(section, "ok", rows)

    statuses: list[Status] = []
    for name in resources:
        before = format_state(current.get(name, "?"))
        if name not in to_apply:
            rows.append(ReportRow(name, cook.manager, before, "—", "unchanged", False))
            continue

        tag = f"[{section}:{name}]"
        pre_hook, post_hook = cook.get_hooks(name)
        if pre_hook and not run_pre_hook(pre_hook, tag):
            rows.append(ReportRow(name, cook.manager, before, "—", "skipped", False))
            continue

        outcome = cook.apply_resource(name)
        if outcome.message:
            (logger.error if outcome.status == "hard_fail" else logger.info)(
                f"{tag} {outcome.message}"
            )
        status: Status = outcome.status
        if outcome.status == "ok" and outcome.changed and post_hook:
            if run_post_hook(post_hook, tag) == "soft_fail":
                status = "soft_fail"

        if status == "hard_fail":
            action = "failed"
        elif status == "soft_fail":
            action = "post-failed"
        elif outcome.changed:
            action = "changed"
        else:
            action = "unchanged"
        statuses.append(status)
        rows.append(
            ReportRow(name, cook.manager, before, "—", action, outcome.changed, status)
        )

    return CookResult(section, pick_worst_status(statuses), rows)


def run_cook(node: Node, config: dict, dry_run: bool) -> CookResult:
    slice_ = node_slice(config, node)
    section_slice = {node.entry: slice_} if node.entry is not None else slice_
    cook = load_cook_class(node.section)(section_slice)
    if isinstance(cook, VersionedCook):
        return run_versioned(cook, node.id, dry_run)
    if isinstance(cook, StateCook):
        return run_state(cook, node.id, dry_run)
    return CookResult(node.id, "hard_fail", [], f"{node.id}: unknown cook kind")


def run_cook_guarded(node: Node, config: dict, dry_run: bool) -> CookResult:
    try:
        return run_cook(node, config, dry_run)
    except Exception:
        return CookResult(node.id, "hard_fail", [], traceback.format_exc())


def fork_user_cook(node: Node, config: dict, dry_run: bool) -> tuple[int, int]:
    """Fork a child, drop to the invoking user via become_user(), run the cook,
    and pickle its CookResult back over a pipe. Forking only from the main
    thread keeps loguru's locks safe."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            become_user()
            result = run_cook_guarded(node, config, dry_run)
        except Exception:
            result = CookResult(node.id, "hard_fail", [], traceback.format_exc())
        with os.fdopen(write_fd, "wb") as out:
            out.write(pickle.dumps(result))
        os._exit(0)
    os.close(write_fd)
    return pid, read_fd


def read_child_result(read_fd: int, exit_status: int, node_id: str) -> CookResult:
    with os.fdopen(read_fd, "rb") as src:
        payload = src.read()
    if not payload:
        return CookResult(
            node_id,
            "hard_fail",
            [],
            f"{node_id} produced no result (status {exit_status}).",
        )
    try:
        return pickle.loads(payload)
    except Exception as exc:
        return CookResult(
            node_id, "hard_fail", [], f"{node_id} result unreadable: {exc}"
        )


def run_recipe(config: dict, dry_run: bool) -> dict[str, CookResult]:
    nodes = build_nodes(config)
    sorter: TopologicalSorter[str] = TopologicalSorter(build_node_graph(nodes))
    sorter.prepare()
    results: dict[str, CookResult] = {}
    running: dict[int, tuple[str, int]] = {}
    abort = False

    with progress_region("Cooking", total=len(nodes)) as bar:
        while sorter.is_active() and not abort:
            for node_id in sorter.get_ready():
                node = nodes[node_id]
                if node.needs_root:
                    result = run_cook_guarded(node, config, dry_run)
                    results[node_id] = result
                    sorter.done(node_id)
                    bar.advance()
                    if result.status == "hard_fail":
                        abort = True
                        break
                else:
                    pid, read_fd = fork_user_cook(node, config, dry_run)
                    running[pid] = (node_id, read_fd)
            if abort:
                break
            if running:
                pid, exit_status = os.waitpid(-1, 0)
                node_id, read_fd = running.pop(pid)
                result = read_child_result(read_fd, exit_status, node_id)
                results[node_id] = result
                sorter.done(node_id)
                bar.advance()
                if result.status == "hard_fail":
                    abort = True

        while running:
            pid, exit_status = os.waitpid(-1, 0)
            node_id, read_fd = running.pop(pid)
            results[node_id] = read_child_result(read_fd, exit_status, node_id)
            bar.advance()

    return results
