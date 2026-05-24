"""StateCook for [apt_repo.<name>] entries — third-party apt repositories.

Each repo is one resource. Its desired state is "configured": a signing key
under /usr/share/keyrings/<name>.gpg (outside trusted.gpg.d, modern layout) and
a `.sources` file with `Signed-By:` pointing at that key, so each key only
authorises its own repo. Chef compares current vs desired and only calls
apply_one for repos that aren't fully in place — so a re-run does no key fetch.

Field semantics (per [apt_repo.<name>] block):
  key_url        required. URL of the signing key (ASCII-armored or binary).
  uris           required. repo base URI.
  suites         optional (default "stable"). `{release}` is interpolated.
  components     optional (default "main").
  architectures  optional. omit to let apt use the host dpkg arch.
  keyring        optional. default /usr/share/keyrings/<name>.gpg.
  source_path    optional. default /etc/apt/sources.list.d/<name>.sources.

Runs as root (writes under /usr/share/keyrings and /etc/apt). Depends on the
[bash] prerequisites (gnupg for key dearmor).
"""

import platform
import sys
from pathlib import Path

from loguru import logger

from cook_base import ItemOutcome, StateCook, debug_main
from harness import fetch_url, run, write_if_changed


def detect_release() -> str:
    osr = platform.freedesktop_os_release()
    release = osr.get("VERSION_CODENAME") or osr.get("UBUNTU_CODENAME")
    if not release:
        sys.exit("ERROR: could not determine release codename")
    return release


def keyring_path(name: str, repo: dict) -> Path:
    return Path(repo.get("keyring", f"/usr/share/keyrings/{name}.gpg"))


def source_path(name: str, repo: dict) -> Path:
    return Path(repo.get("source_path", f"/etc/apt/sources.list.d/{name}.sources"))


def install_repo_key(name: str, key_url: str, keyring: Path) -> bool:
    data = fetch_url(key_url)
    # ASCII-armored keys start with the RFC 4880 §6.2 header; binary OpenPGP
    # packets start with a high-bit-set tag byte and never match.
    if data.lstrip().startswith(b"-----BEGIN PGP"):
        data = run("gpg", "--dearmor", input=data, capture_output=True).stdout
    return write_if_changed(keyring, data, note=f"{name} GPG key")


def configure_repo(name: str, repo: dict, release: str) -> bool:
    keyring = keyring_path(name, repo)
    changed = install_repo_key(name, repo["key_url"], keyring)
    lines = [
        "Types: deb",
        f"URIs: {repo['uris']}",
        f"Suites: {repo.get('suites', 'stable').format(release=release)}",
        f"Components: {repo.get('components', 'main')}",
    ]
    # Omitting Architectures: lets apt use the host's dpkg arch (plus any added
    # via `dpkg --add-architecture`); only pin for repos shipping a strict subset.
    if archs := repo.get("architectures"):
        lines.append(f"Architectures: {archs}")
    lines.append(f"Signed-By: {keyring}")
    changed |= write_if_changed(source_path(name, repo), "\n".join(lines) + "\n")
    return changed


class AptRepoCook(StateCook):
    needs_root = True
    manager = "apt-repo"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.repos: dict[str, dict] = section

    def items(self) -> list[str]:
        return list(self.repos)

    def current(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, repo in self.repos.items():
            present = (
                keyring_path(name, repo).exists() and source_path(name, repo).exists()
            )
            out[name] = "configured" if present else "absent"
        return out

    def desired(self) -> dict[str, str]:
        return dict.fromkeys(self.repos, "configured")

    def apply_one(self, name: str) -> ItemOutcome:
        release = detect_release()
        logger.info(f"Configuring repo {name} (release codename: {release})")
        changed = configure_repo(name, self.repos[name], release)
        return ItemOutcome(changed=changed)


if __name__ == "__main__":
    debug_main(AptRepoCook)
