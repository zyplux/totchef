up:
    #!/usr/bin/env bash
    set -e
    export SYS_CONF_PY_LOG_FILE="${SYS_CONF_PY_LOG_FILE:-{{justfile_directory()}}/logs/sys-conf-py-$(date +%Y%m%d-%H%M%S).log}"
    just _prime-sudo
    just urls
    just cargo
    just uv
    just apt
    just gpu
    just apps

_prime-sudo:
    sudo -v

urls:
    ./src/install_from_urls.py

cargo:
    ./src/install_cargo_packages.py

uv:
    ./src/install_uv_packages.py

apt:
    ./src/configure_with_apt.py

gpu:
    ./src/configure_gpu.py

apps:
    ./src/configure_apps.py

lint:
    ruff check --fix
    ruff format

tc: lint
    uvx pyright src
