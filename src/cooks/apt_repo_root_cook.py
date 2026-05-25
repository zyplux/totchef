"""StateCook for [apt_repo.<name>] entries — third-party apt repositories.

Each repo is one resource. Its desired state is "configured": a signing key
under /usr/share/keyrings/<name>.gpg (outside trusted.gpg.d, modern layout) and
a `.sources` file with `Signed-By:` pointing at that key, so each key only
authorises its own repo. Chef compares current vs desired and only calls
apply_one for repos that aren't fully in place — so a re-run does no key fetch.
Fields: see recipe.toml's header.

Runs as root (writes under /usr/share/keyrings and /etc/apt); depends on
bash.apt_prereqs (gnupg for key dearmor).
"""

import platform
import sys
from pathlib import Path

from loguru import logger

from cook_base import EntrySpec, ItemOutcome, StateCook
from harness import fetch_url, run, write_if_changed


class AptRepoEntry(EntrySpec):
    key_url: str
    uris: str
    suites: str = "stable"
    components: str = "main"
    architectures: str | None = None
    keyring: str | None = None
    source_path: str | None = None


def detect_release() -> str:
    os_release = platform.freedesktop_os_release()
    release = os_release.get("VERSION_CODENAME") or os_release.get("UBUNTU_CODENAME")
    if not release:
        sys.exit("ERROR: could not determine release codename")
    return release


def keyring_path(name: str, repo: AptRepoEntry) -> Path:
    return Path(repo.keyring or f"/usr/share/keyrings/{name}.gpg")


def source_path(name: str, repo: AptRepoEntry) -> Path:
    return Path(repo.source_path or f"/etc/apt/sources.list.d/{name}.sources")


def install_repo_key(name: str, key_url: str, keyring: Path) -> bool:
    data = fetch_url(key_url)
    # ASCII-armored keys start with the RFC 4880 §6.2 header; binary OpenPGP
    # packets start with a high-bit-set tag byte and never match.
    if data.lstrip().startswith(b"-----BEGIN PGP"):
        data = run("gpg", "--dearmor", input=data, capture_output=True).stdout
    return write_if_changed(keyring, data, note=f"{name} GPG key")


def configure_repo(name: str, repo: AptRepoEntry, release: str) -> bool:
    keyring = keyring_path(name, repo)
    changed = install_repo_key(name, repo.key_url, keyring)
    lines = [
        "Types: deb",
        f"URIs: {repo.uris}",
        f"Suites: {repo.suites.format(release=release)}",
        f"Components: {repo.components}",
    ]
    # Omitting Architectures: lets apt use the host's dpkg arch (plus any added
    # via `dpkg --add-architecture`); only pin for repos shipping a strict subset.
    if repo.architectures:
        lines.append(f"Architectures: {repo.architectures}")
    lines.append(f"Signed-By: {keyring}")
    changed |= write_if_changed(source_path(name, repo), "\n".join(lines) + "\n")
    return changed


class AptRepoCook(StateCook):
    needs_root = True
    manager = "apt-repo"
    entry_model = AptRepoEntry

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.repos = {
            name: AptRepoEntry.model_validate(raw) for name, raw in section.items()
        }

    def items(self) -> list[str]:
        return list(self.repos)

    def current(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for name, repo in self.repos.items():
            present = (
                keyring_path(name, repo).exists() and source_path(name, repo).exists()
            )
            states[name] = "configured" if present else "absent"
        return states

    def desired(self) -> dict[str, str]:
        return dict.fromkeys(self.repos, "configured")

    def hooks(self, name: str) -> tuple[str | None, str | None]:
        repo = self.repos[name]
        return (repo.pre_hook, repo.post_hook)

    def apply_one(self, name: str) -> ItemOutcome:
        release = detect_release()
        logger.info(f"Configuring repo {name} (release codename: {release})")
        changed = configure_repo(name, self.repos[name], release)
        return ItemOutcome(changed=changed)
