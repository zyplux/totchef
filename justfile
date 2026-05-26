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
    uvx pyright src

test: tc
    uv run pytest

clone repo ref="":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{repo}}" in
        http*://* | git@*) url="{{repo}}" ;;
        *) url="https://github.com/{{repo}}.git" ;;
    esac
    name="$(basename "{{repo}}" .git)"
    dest="reference_clones/$name"
    [ -e "$dest" ] && { echo "$dest already exists — remove it first: rm -rf $dest" >&2; exit 1; }
    if [ -z "{{ref}}" ]; then
        git clone --depth 1 --single-branch "$url" "$dest"
        echo "Cloned $url -> $dest (shallow, default branch tip). Delete with: rm -rf $dest"
    else
        git clone --shallow-exclude="{{ref}}" --single-branch "$url" "$dest"
        echo "Cloned $url -> $dest (default branch tip, history back to but excluding {{ref}}). Delete with: rm -rf $dest"
    fi
