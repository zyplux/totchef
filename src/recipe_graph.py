"""Recipe -> scheduling graph: turn recipe.toml into a validated DAG of Nodes and
resolve each section to its cook class. A section with named subtables expands to
one Node per entry; a plain-data or empty section is a single Node. `depends_on`
and `needs_root` are read per entry (falling back to the section, then — for
needs_root — the cook class). See recipe.toml's header for the field semantics.
"""

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter

from pydantic import ValidationError

from cook_base import CookBase

# Keys chef reads off a slice, then strips before handing it to the cook.
META_KEYS = ("needs_root", "depends_on")


def strip_meta(slice_: dict) -> dict:
    return {k: v for k, v in slice_.items() if k not in META_KEYS}


def merge_section_defaults(section_data: dict, entry: str) -> dict:
    """Fold a subtable section's own (non-meta, scalar) keys into one entry's slice
    as defaults: lists union (section defaults the entry extends), everything else
    overrides (entry wins). Lets a shared list like `features` live once at the
    section header instead of being repeated in every entry."""
    defaults = {
        k: v
        for k, v in section_data.items()
        if k not in META_KEYS and not isinstance(v, dict)
    }
    entry_data = strip_meta(section_data[entry])
    merged = {**defaults, **entry_data}
    for key, shared in defaults.items():
        if not isinstance(shared, list):
            continue
        extra = entry_data.get(key)
        if isinstance(extra, list):
            merged[key] = list(dict.fromkeys([*shared, *extra]))
        elif key not in entry_data:
            merged[key] = list(shared)
    return merged


def load_cook_class(section: str) -> type[CookBase]:
    """Import cooks/<section>_root_cook.py (always-root) or <section>_cook.py
    (generic), whichever exists, and return its single CookBase subclass."""
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
    """One unit of work chef schedules — one entry of a subtable section, or a
    whole plain-data/empty section."""

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


def build_node_graph(nodes: dict[str, Node]) -> dict[str, set[str]]:
    """Resolve each node's `depends_on` to node ids. A dependency names either a
    node directly — an entry (`url.rustup`, `bash.apt_prereqs`) or a single-node
    section (`apt_pkg`) — or a whole section (`apt_repo`), which fans out to every
    node in that section. Name the smallest unit that matches the real need: a
    section only when you depend on all of it (`apt_pkg` needs every repo),
    individual entries when you need some (two of the `bash` steps, not all)."""
    section_nodes: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        section_nodes.setdefault(node.section, set()).add(node_id)

    graph: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        deps: set[str] = set()
        for dep in node.depends_on:
            if dep in nodes:
                deps.add(dep)
            elif dep in section_nodes:
                deps.update(section_nodes[dep])
            else:
                sys.exit(
                    f"ERROR: [{node_id}] depends_on '{dep}', which is neither a "
                    "node nor a section. Name an entry (e.g. 'bash.apt_prereqs'), "
                    "a single-node section (e.g. 'apt_pkg'), or a whole section to "
                    "fan out to all its entries (e.g. 'apt_repo')."
                )
        deps.discard(node_id)
        graph[node_id] = deps
    return graph


def node_slice(config: dict, node: "Node") -> dict:
    """The exact dict a node's cook receives: an entry node gets its merged slice
    (section defaults folded in), a single-node section gets the section itself."""
    if node.entry is not None:
        return merge_section_defaults(config[node.section], node.entry)
    return strip_meta(config[node.section])


def check_schema(config: dict, nodes: dict[str, "Node"]) -> list[str]:
    """Validate each node's slice against its cook's `entry_model`, collecting every
    Pydantic error as a readable `[node] loc: message` line (empty list == valid)."""
    problems: list[str] = []
    for node_id, node in nodes.items():
        model = load_cook_class(node.section).entry_model
        if model is None:
            continue
        try:
            model.model_validate(node_slice(config, node))
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(part) for part in err["loc"]) or "(entry)"
                problems.append(f"  [{node_id}] {loc}: {err['msg']}")
    return problems


def validate(config: dict) -> None:
    nodes = build_nodes(config)
    for section in {node.section for node in nodes.values()}:
        load_cook_class(section)
    try:
        list(TopologicalSorter(build_node_graph(nodes)).static_order())
    except CycleError as exc:
        sys.exit(f"ERROR: dependency cycle in recipe.toml: {' -> '.join(exc.args[1])}")
    if problems := check_schema(config, nodes):
        sys.exit("ERROR: recipe.toml schema validation failed:\n" + "\n".join(problems))
