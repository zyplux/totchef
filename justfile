set shell := ["bash", "-euo", "pipefail", "-c"]

alias i := install
alias k := knip
alias tc := typecheck
alias l := lint
alias t := test
alias c := check

# Dev recipe applied by `just up`/`plan`/`lint`; override with `just up recipe=path`.
recipe := "examples/totchef_recipe.toml"

# List available recipes.
default:
    @just --list

# Install Python dependencies (all groups).
install:
    uv sync --all-groups

# Build the standalone binary, then apply the recipe (so [file.totchef] installs the freshly-built totchef).
up: build
    uv run totchef up --recipe {{ recipe }}

# Dry-run: show what `up` would change without applying.
plan:
    uv run totchef plan --recipe {{ recipe }}

# List available cooks.
cooks:
    uv run totchef --list-cooks

# Find dead code via vulture.
knip:
    uv run --group lint vulture

# Type-check via pyrefly.
typecheck:
    uv run --group typecheck pyrefly check

# Lint with autofix: ruff (check --fix + format), story links, rumdl, recipe lint via totchef.
lint:
    uv run --group lint ruff check --fix
    uv run --group lint ruff format
    uv run python tests/project/sync_story_links.py
    uv run --group lint rumdl check --fix
    uv run totchef lint --recipe {{ recipe }}

# Build the standalone single-file totchef binary into the recipe's totchef_files/ (installed by [file.totchef] so `totchef up` runs from anywhere). Re-run after code changes.
build:
    uv run --with pyinstaller pyinstaller --onefile --name totchef --collect-submodules totchef.cooks --copy-metadata totchef --distpath examples/totchef_files --workpath build/pyinstaller --specpath build/pyinstaller src/totchef/__main__.py

# Run tests via pytest.
test:
    uv run pytest

# Full gate: install, knip, typecheck, lint, test — autofix throughout.
check: install knip typecheck lint test

# Shallow-clone a repo (owner/name or URL) into reference_clones/; optional ref keeps history back to but excluding that commit/tag (e.g. just clone microsoft/vscode 1.121.0)
clone repo ref="":
    scripts/clone_reference.py {{ repo }} {{ ref }}
