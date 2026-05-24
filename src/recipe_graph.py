"""Recipe -> scheduling graph: turn recipe.toml's parsed config into a validated
DAG of Nodes, and resolve each section to its cook class.

A section with named subtables ([url.*], [file.*], [bash.*], [apt_repo.*])
expands to one Node per entry (`url.rustup`); a section with plain data
([apt_pkg]) or none ([desktop]) is a single Node. `depends_on` is read per entry
(falling back to the section default) and stripped before the slice reaches the
cook; it resolves to node ids — a name that is itself a node id maps to itself,
a name that is only a section fans out to all of that section's entry nodes.

`needs_root` follows the same per-entry/section precedence, but its ultimate
fallback is the **cook class** (`CookBase.needs_root`, default False; an
always-root cook sets True and is named `<section>_root_cook.py`). So recipe.toml
only needs `needs_root` to mark a generic cook's entry as root (e.g. file/bash
writing under /etc); intrinsically-root cooks declare it once on the class.

Chef stays ignorant of concrete cooks — `load_cook_class` hands them back only
as the VersionedCook / StateCook interface.
"""

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter

from cook_base import CookBase

# Reserved per-section/entry keys chef reads, then strips before handing the
# slice to the cook. `needs_root` may appear in recipe.toml to override a generic
# cook's class default; `depends_on` carries ordering.
META_KEYS = ("needs_root", "depends_on")


def strip_meta(slice_: dict) -> dict:
    return {k: v for k, v in slice_.items() if k not in META_KEYS}


def load_cook_class(section: str) -> type[CookBase]:
    """Resolve a section to its cook class generically: a section maps to
    `cooks/<section>_root_cook.py` (always-root) or `cooks/<section>_cook.py`
    (generic), whichever exists, and chef returns the single CookBase subclass
    defined there — seeing it only through the VersionedCook / StateCook
    interface, never the concrete type."""
    candidates = [
        f"cooks.{section}{suffix}"
        for suffix in ("_root_cook", "_cook")
        if importlib.util.find_spec(f"cooks.{section}{suffix}") is not None
    ]
    if not candidates:
        sys.exit(
            f"ERROR: [{section}] -> no cooks/{section}_cook.py "
            f"or cooks/{section}_root_cook.py."
        )
    if len(candidates) > 1:
        sys.exit(
            f"ERROR: [{section}] -> both {' and '.join(candidates)} exist; "
            "keep exactly one."
        )
    module = importlib.import_module(candidates[0])
    classes = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, CookBase)
        and obj.__module__ == module.__name__
    ]
    if len(classes) != 1:
        sys.exit(
            f"ERROR: {candidates[0]} must define exactly one cook class, "
            f"found {len(classes)}: {[c.__name__ for c in classes]}."
        )
    return classes[0]


@dataclass(frozen=True)
class Node:
    """One unit of work chef schedules. A section with named subtables expands
    to one node per entry (`url.rustup`, `file.write_if_changed`); a section with
    plain data (or none) is a single node (`apt_pkg`, `desktop`). `needs_root`
    resolves from the entry, then the section, then the cook class default;
    `depends_on` from the entry, falling back to the section."""

    id: str
    section: str
    entry: str | None
    needs_root: bool
    depends_on: tuple[str, ...]


def build_nodes(config: dict) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for section, data in config.items():
        sec_root = data.get("needs_root", load_cook_class(section).needs_root)
        sec_deps = data.get("depends_on", [])
        children = {
            k: v for k, v in data.items() if k not in META_KEYS and isinstance(v, dict)
        }
        if children:
            for entry, entry_data in children.items():
                node_id = f"{section}.{entry}"
                nodes[node_id] = Node(
                    node_id,
                    section,
                    entry,
                    entry_data.get("needs_root", sec_root),
                    tuple(entry_data.get("depends_on", sec_deps)),
                )
        else:
            nodes[section] = Node(section, section, None, sec_root, tuple(sec_deps))
    return nodes


def node_graph(nodes: dict[str, Node]) -> dict[str, set[str]]:
    """Resolve each node's `depends_on` to node ids: a name that is a node id
    (`url.rustup`, `apt_pkg`) maps to itself; a name that is only a section
    (`bash`) fans out to all of that section's entry nodes."""
    sections = {node.section for node in nodes.values()}
    graph: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        deps: set[str] = set()
        for dep in node.depends_on:
            if dep in nodes:
                deps.add(dep)
            elif dep in sections:
                deps.update(nid for nid, n in nodes.items() if n.section == dep)
            else:
                sys.exit(
                    f"ERROR: [{node_id}] depends_on unknown section/entry '{dep}'."
                )
        deps.discard(node_id)
        graph[node_id] = deps
    return graph


def validate(config: dict) -> None:
    nodes = build_nodes(config)
    for section in {node.section for node in nodes.values()}:
        load_cook_class(section)
    try:
        list(TopologicalSorter(node_graph(nodes)).static_order())
    except CycleError as exc:
        sys.exit(f"ERROR: dependency cycle in recipe.toml: {' -> '.join(exc.args[1])}")
