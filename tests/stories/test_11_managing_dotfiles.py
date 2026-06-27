"""User stories §11 — Managing dotfiles with chezmoi. One test per §11 criterion on the real chef in-process; only system boundaries (bash, network, host, home) are faked."""

# 11.1 Provision dotfiles from a git repo


def test_11_1_1_chezmoi_clones_the_repo_and_applies_it(recipe, system, terminal, totchef):
    """`[chezmoi]` with a repo clones it into the source dir (`chezmoi init`) then writes the managed files into $HOME (`chezmoi apply`)."""
    system.has("chezmoi")
    recipe.declares("chezmoi", repo="https://github.test/operator/dotfiles.git")

    totchef.up().assert_shows("chezmoi.dotfiles", "applied")

    terminal.expect_ran("chezmoi init")
    terminal.expect_ran("chezmoi apply")


def test_11_1_2_chezmoi_is_idempotent_once_applied(recipe, system, terminal, totchef, home):
    """A re-run is a no-op once the source is cloned and `chezmoi verify` reports the destination already matches: unchanged, no init or apply."""
    system.has("chezmoi")
    (home / ".local/share/chezmoi/.git").mkdir(parents=True)
    terminal.arrange("chezmoi verify", "", exit_code=0)
    recipe.declares("chezmoi", repo="https://github.test/operator/dotfiles.git")

    totchef.up().assert_shows("chezmoi.dotfiles", "applied")

    terminal.reset()
    terminal.arrange("chezmoi verify", "", exit_code=0)

    report = totchef.up()

    report.assert_shows("chezmoi.dotfiles", "unchanged")
    terminal.expect_not_ran("chezmoi init")
    terminal.expect_not_ran("chezmoi apply")


# 11.2 Choose where dotfiles live and whether to apply


def test_11_2_1_source_dir_is_configurable_and_written_to_chezmoi_config(recipe, system, terminal, totchef, home):
    """`source_dir` is passed to chezmoi (`--source`) and persisted as `sourceDir` in ~/.config/chezmoi/chezmoi.toml so bare chezmoi commands use it too."""
    system.has("chezmoi")
    recipe.declares("chezmoi", repo="https://github.test/operator/dotfiles.git", source_dir="~/dotfiles")

    totchef.up().assert_succeeded()

    terminal.expect_ran(f"chezmoi init --source {home}/dotfiles")
    config = home / ".config/chezmoi/chezmoi.toml"
    assert 'sourceDir = "~/dotfiles"' in config.read_text()


def test_11_2_2_apply_can_be_disabled_to_clone_and_configure_only(recipe, system, terminal, totchef):
    """`apply = false` clones and configures but never runs `chezmoi apply`, leaving $HOME untouched."""
    system.has("chezmoi")
    recipe.declares("chezmoi", repo="https://github.test/operator/dotfiles.git", apply=False)

    totchef.up().assert_shows("chezmoi.dotfiles", "applied")

    terminal.expect_ran("chezmoi init")
    terminal.expect_not_ran("chezmoi apply")


# 11.3 Run as the operator with the binary in place


def test_11_3_1_chezmoi_is_user_scoped_not_root(cli):
    """`[chezmoi]` manages the operator's $HOME, so it lists with user scope and never escalates to root."""
    cli.run("--list-cooks").assert_lists("chezmoi", scope="user")


def test_11_3_2_chezmoi_without_the_binary_fails_clearly(recipe, totchef):
    """With no chezmoi binary on PATH (the [url.chezmoi] installer hasn't run), the resource hard-fails naming the section that must run first."""
    recipe.declares("chezmoi", repo="https://github.test/operator/dotfiles.git")

    report = totchef.up()

    report.assert_hard_failed()
    report.assert_logged("url.chezmoi")
