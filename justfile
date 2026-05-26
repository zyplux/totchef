# Dev recipe applied by `just up`/`plan`/`lint`; override with `just up recipe=path`.
recipe := "examples/recipe.toml"

up:
    uv run totchef up --recipe {{recipe}}

plan:
    uv run totchef plan --recipe {{recipe}}

cooks:
    uv run totchef cooks

deadcode:
    uvx vulture

lint: deadcode
    ruff check --fix
    ruff format
    rumdl check --fix
    uv run totchef lint --recipe {{recipe}}

tc: lint
    uvx pyright

test: tc
    uv run pytest

# Shallow-clone a repo (owner/name or URL) into reference_clones/; optional ref keeps history back to but excluding that commit/tag (e.g. just clone microsoft/vscode 1.121.0)
clone repo ref="":
    scripts/clone_reference.py {{repo}} {{ref}}
