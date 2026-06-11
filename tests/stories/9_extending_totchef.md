# 9. Extending totchef (cook authors)

## 9.1 [Add a new configuration domain as a plugin](test_9_extending_totchef.py)

> As a cook author, I want to add a new recipe section backed by my own cook, so
> that totchef can manage a domain it doesn't ship with.

### 9.1.1 cook registered under entry point group serves its section

A cook is a `CookBase` subclass registered under the `totchef.cooks`
entry-point group; the section name it serves is the entry-point name. Built-in and
third-party cooks register the same way, and `totchef cooks` shows the origin.

## 9.2 [Prototype a cook without packaging it](test_9_extending_totchef.py)

> As a cook author, I want to drop a single Python file into my config dir and have
> totchef pick it up, so that I can prototype a domain without building a package.

### 9.2.1 local cook file is picked up and shadows a builtin

A loose `~/.config/totchef/cooks/<section>_cook.py` (containing exactly one
`CookBase` subclass; the `_cook`/`_root_cook` suffix is stripped to derive the
section) is loaded as a local cook and **shadows** a built-in of the same name — an
escape hatch for overriding or prototyping.

## 9.3 [Choose the right cook shape for my domain](test_9_extending_totchef.py)

> As a cook author, I want base classes that match common patterns, so that I only
> implement the domain-specific probe/act logic and inherit diffing, scheduling,
> and reporting.

### 9.3.1 versioned cook implements requested installed latest sync

**VersionedCook** for versioned packages: implement
`list_requested`/`list_installed`/`find_latest`/`sync`. `PackageListCook` covers
plain `packages = [...]` sections.

### 9.3.2 state cook implements current desired apply filestate diffs

**StateCook** for desired-state resources: implement
`get_current_state`/`get_desired_state`/`apply_resource` (+ hooks). `FileStateCook`
already diffs by sha256 of rendered bytes vs the on-disk file — a subclass just
supplies the target path and the rendered content.

### 9.3.3 cook only probes and acts orchestrator owns the diff

The cook only *probes* and *acts*; the orchestrator owns every diff and
idempotency decision, so a cook holds no diff logic.

## 9.4 [Get a typo'd recipe rejected against my schema](test_9_extending_totchef.py)

> As a cook author, I want each cook to define a strict schema for its recipe
> entries, so that an operator's typo fails the lint instead of being silently
> ignored.

### 9.4.1 cook entry model lints recipe slice reporting violations

A cook declares an `entry_model` (a pydantic model with `extra='forbid'`).
`totchef lint` validates each node's recipe slice against it and reports every
violation as a precise `[node] location: message` line.
