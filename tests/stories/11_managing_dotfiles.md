# 11. [Managing dotfiles with chezmoi](test_11_managing_dotfiles.py)

## 11.1 Provision dotfiles from a git repo

> As an operator, I want to declare my dotfiles git repo and have totchef clone it
> with chezmoi and apply it into my `$HOME` — idempotently — so a fresh machine
> converges to my personalized environment from one recipe section.

### 11.1.1 chezmoi clones the repo and applies it

`[chezmoi]` with a `repo` clones it into the source directory (`chezmoi init`) and
then writes the managed files into `$HOME` (`chezmoi apply`), so the dotfiles land
on a fresh machine in one step.

```toml
[chezmoi]
repo = "https://github.com/operator/dotfiles.git"
```

### 11.1.2 chezmoi is idempotent once applied

A re-run is a no-op: once the source directory holds the clone and `chezmoi verify`
reports the destination already matches the target, the resource shows `unchanged`
and neither `init` nor `apply` runs again.

## 11.2 Choose where dotfiles live and whether to apply

> As an operator, I want the source directory and whether to apply to be recipe
> settings, so I can keep my dotfiles where I like and gate `$HOME` changes when I
> want to review them first.

### 11.2.1 source dir is configurable and written to chezmoi config

`source_dir` sets where chezmoi clones and reads the dotfiles (defaulting to
chezmoi's own `~/.local/share/chezmoi`). It is both passed to chezmoi and persisted
as `sourceDir` in `~/.config/chezmoi/chezmoi.toml`, so the operator's own bare
`chezmoi` commands use the same directory afterwards.

```toml
[chezmoi]
repo = "https://github.com/operator/dotfiles.git"
source_dir = "~/dotfiles"
```

### 11.2.2 apply can be disabled to clone and configure only

`apply = false` clones the repo and writes the config but never runs `chezmoi
apply`, leaving `$HOME` untouched until the operator applies it themselves (after a
`chezmoi diff`).

## 11.3 Run as the operator with the binary in place

> As an operator, I want chezmoi to manage my own `$HOME` (never root's) and to fail
> loudly when the chezmoi binary isn't installed yet, so the dependency on the
> installer is obvious.

### 11.3.1 chezmoi is user scoped not root

`[chezmoi]` is a user-scoped cook: it manages the operator's `$HOME`, so it lists
with `user` scope and never escalates to root.

### 11.3.2 chezmoi without the binary fails clearly

When the `chezmoi` binary isn't on the operator's PATH — the `[url.chezmoi]`
installer hasn't run — the resource hard-fails with a message naming the section
that must run first, instead of silently doing nothing.
