up:
    ./src/chef.py

plan:
    ./src/chef.py --dry-run

deadcode:
    uvx vulture

lint: deadcode
    ruff check --fix
    ruff format
    rumdl check --fix
    ./src/chef.py --lint

tc: lint
    uvx pyright

test: tc
    uv run pytest

# Shallow-clone a repo (owner/name or URL) into reference_clones/; optional ref keeps history back to but excluding that commit/tag (e.g. just clone microsoft/vscode 1.121.0)
clone repo ref="":
    scripts/clone_reference.py {{repo}} {{ref}}

