"""Cook for [apt_repo.<name>] entries — third-party apt repositories.

One subtable per repo. Each repo gets its signing key written outside
/etc/apt/trusted.gpg.d/ (modern layout) and a `.sources` file with
`Signed-By:` pointing at that key, so each key only authorises its own repo.

Field semantics (per [apt_repo.<name>] block):
  key_url        required. URL of the signing key (ASCII-armored or binary).
  uris           required. repo base URI.
  suites         optional (default "stable"). `{release}` is interpolated.
  components     optional (default "main").
  architectures  optional. omit to let apt use the host dpkg arch.
  keyring        optional. default /usr/share/keyrings/<name>.gpg.
  source_path    optional. default /etc/apt/sources.list.d/<name>.sources.

Runs as root (writes under /usr/share/keyrings and /etc/apt). Depends on the
[bash] prerequisites (gnupg for key dearmor); the actual `nala update` +
package work lives in the [apt_pkg] cook, which depends on this one.
"""

import platform
import sys
from pathlib import Path

from loguru import logger

from cook_base import CookBase, Result, VersionInfo, main_for
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


def install_repo_key(name: str, key_url: str, keyring: Path) -> None:
    data = fetch_url(key_url)
    # ASCII-armored keys start with the RFC 4880 §6.2 header; binary OpenPGP
    # packets start with a high-bit-set tag byte and never match.
    if data.lstrip().startswith(b"-----BEGIN PGP"):
        data = run("gpg", "--dearmor", input=data, capture_output=True).stdout
    write_if_changed(keyring, data, note=f"{name} GPG key")


def configure_repo(name: str, repo: dict, release: str) -> None:
    keyring = keyring_path(name, repo)
    install_repo_key(name, repo["key_url"], keyring)
    lines = [
        "Types: deb",
        f"URIs: {repo['uris']}",
        f"Suites: {repo.get('suites', 'stable').format(release=release)}",
        f"Components: {repo.get('components', 'main')}",
    ]
    # Omitting Architectures: lets apt use the host's dpkg arch (plus any
    # added via `dpkg --add-architecture`); only pin for repos that ship a
    # strict subset of arches the host supports.
    if archs := repo.get("architectures"):
        lines.append(f"Architectures: {archs}")
    lines.append(f"Signed-By: {keyring}")
    write_if_changed(source_path(name, repo), "\n".join(lines) + "\n")


class AptRepoCook(CookBase):
    needs_root = True
    manager = "apt-repo"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.repos: dict[str, dict] = section

    def install_or_update(self) -> Result:
        if not self.repos:
            return Result("ok", "No [apt_repo.*] entries in recipe.toml; nothing to do")

        release = detect_release()
        logger.info(f"Detected release codename: {release}")
        for name, repo in self.repos.items():
            configure_repo(name, repo, release)
        logger.info(f"Done. Configured {len(self.repos)} repo(s).")
        return Result("ok", changed=True)

    def show_version(self) -> list[VersionInfo]:
        rows: list[VersionInfo] = []
        for name, repo in self.repos.items():
            present = (
                keyring_path(name, repo).exists() and source_path(name, repo).exists()
            )
            rows.append(
                VersionInfo(
                    name=name,
                    installed_version="configured" if present else "(none)",
                    available_version="unknown",
                    source=repo.get("uris", ""),
                    status="installed" if present else "missing",
                    cook=self.cook_name,
                    manager=self.manager,
                )
            )
        return rows


if __name__ == "__main__":
    main_for(AptRepoCook)
