"""Fixtures for the prose-style tests, by role (arrange/act/assert); only system boundaries (bash, network, `$HOME`, host) are mocked."""

from act_fixtures import cli, scenario, totchef
from arrange_fixtures import fresh_registry, home, http, recipe, system, terminal
from assert_fixtures import read_json
from container_fixtures import apply_in_container, container_image

__all__ = ["apply_in_container", "cli", "container_image", "fresh_registry", "home", "http", "read_json", "recipe", "scenario", "system", "terminal", "totchef"]
