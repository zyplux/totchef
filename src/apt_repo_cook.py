"""Cook for [apt_repo.<name>] entries — idempotent third-party apt repo setup.

For each named entry, installs a GPG keyring under /usr/share/keyrings/ and
writes a .sources file pointing to it via Signed-By:. This keeps each key
scoped to its own repo (no bleed into trusted.gpg.d).

Depends on [bash] having run first (curl, gnupg must be present).
Runs as root (needs_root = true in recipe.toml).
"""

from pathlib import Path

from loguru import logger

from harness import (
    CookBase,
    Result,
    VersionInfo,
    detect_release,
    fetch_url,
    load_section,
    run,
    start_log_tee,
    write_if_changed,
)

SCRIPT = Path(__file__).resolve()


class AptRepoCook(CookBase):
    def __init__(self, section: dict, release: str) -> None:
        self.repos = {k: v for k, v in section.items() if isinstance(v, dict)}
        self.release = release

    def install_or_update(self) -> Result:
        for name, repo in self.repos.items():
            _configure_repo(name, repo, self.release)
        return Result("ok", f"Configured {len(self.repos)} repo(s)", bool(self.repos))

    def show_version(self) -> list[VersionInfo]:
        return [
            VersionInfo(
                name=name,
                installed_version="present"
                if _repo_is_configured(name, repo)
                else "absent",
                available_version="",
                source=repo.get("uris", ""),
                status="installed" if _repo_is_configured(name, repo) else "missing",
                cook="apt_repo_cook",
                manager="apt",
            )
            for name, repo in self.repos.items()
        ]


def _repo_is_configured(name: str, repo: dict) -> bool:
    keyring = Path(repo.get("keyring", f"/usr/share/keyrings/{name}.gpg"))
    source = Path(repo.get("source_path", f"/etc/apt/sources.list.d/{name}.sources"))
    return keyring.exists() and source.exists()


def _install_repo_key(name: str, key_url: str, keyring: Path) -> None:
    data = fetch_url(key_url)
    # ASCII-armored keys start with the RFC 4880 §6.2 header; binary OpenPGP
    # packets start with a high-bit-set tag byte and never match.
    if data.lstrip().startswith(b"-----BEGIN PGP"):
        data = run("gpg", "--dearmor", input=data, capture_output=True).stdout
    write_if_changed(keyring, data, note=f"{name} GPG key")


def _configure_repo(name: str, repo: dict, release: str) -> None:
    keyring = Path(repo.get("keyring", f"/usr/share/keyrings/{name}.gpg"))
    source = Path(repo.get("source_path", f"/etc/apt/sources.list.d/{name}.sources"))
    _install_repo_key(name, repo["key_url"], keyring)
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
    write_if_changed(source, "\n".join(lines) + "\n")


def main() -> None:
    section = load_section()
    start_log_tee()

    release = detect_release()
    logger.info(f"Detected release codename: {release}")

    cook = AptRepoCook(section, release)

    if not cook.repos:
        logger.info("No [apt_repo.*] entries in recipe.toml; nothing to do")
        return

    result = cook.install_or_update()
    logger.info(result.message)
    logger.info("Done.")


if __name__ == "__main__":
    main()
