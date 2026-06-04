"""Fixtures for the prose-style tests, by role (arrange/act/assert); only system boundaries (bash, network, `$HOME`, host) are mocked."""

from act_fixtures import chef, cli, totchef
from arrange_fixtures import bundled_files, fresh_registry, fresh_runner_colors, home, http, recipe, register_plugin, scenario, sys_bin_dir, system, terminal
from assert_fixtures import read_json
from container_fixtures import apply_in_container, container_image

__all__ = [
    "apply_in_container",
    "bundled_files",
    "chef",
    "cli",
    "container_image",
    "fresh_registry",
    "fresh_runner_colors",
    "home",
    "http",
    "read_json",
    "recipe",
    "register_plugin",
    "scenario",
    "sys_bin_dir",
    "system",
    "terminal",
    "totchef",
]
