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

# Shallow-clone a repo (owner/name or URL) into reference_clones/; optional ref keeps history back to but excluding that commit/tag (e.g. just clone microsoft/vscode 1.121.0)
clone repo ref="":
    #!/usr/bin/env -S uv run --script
    # /// script
    # requires-python = ">=3.14"
    # ///
    import shutil, subprocess, sys
    from pathlib import Path

    repo, ref = "{{repo}}", "{{ref}}"
    url = repo if "://" in repo or repo[:4] == "git@" else f"https://github.com/{repo}.git"
    dest = Path("reference_clones") / Path(repo).name.removesuffix(".git")
    if dest.exists():
        input(f"{dest} exists — rm -rf and re-clone? [enter to continue, ^C to abort] ")
        shutil.rmtree(dest)
    opts = ["--shallow-exclude", ref] if ref else ["--depth", "1"]
    subprocess.run(["git", "clone", *opts, "--single-branch", url, dest], check=True)

