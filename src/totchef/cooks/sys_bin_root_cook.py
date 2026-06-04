"""StateCook for [sys_bin.<name>] — install a bundled command into /usr/local/bin (system-wide, on sudo's secure_path), named after its source stem; always root."""

from totchef.cooks.bin_cook_base import BinCommandCook


class SysBinCook(BinCommandCook):
    needs_root = True
    bin_dir = "/usr/local/bin"
