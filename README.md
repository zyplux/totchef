# sys-conf-py

Declarative Ubuntu/Kubuntu Wayland laptop config: apt repos + packages, eGPU
auto-PRIME at boot, and Brave + VS Code Insiders GPU-acceleration flags;
re-runnable;
same script for first-run bootstrap and ongoing upkeep.

## One-time setup

`just up` then reboot.

After reboot, the egpu-prime service runs before SDDM and picks
`prime-select nvidia` when the eGPU is on PCI, else `prime-select on-demand`.

## Config

| File | Purpose |
|---|---|
| `src/install.toml` | unified declarative config: `[bash.<cli>]` vendor URL installers, `[cargo]`/`[uv]` package lists, `[apt]` packages + repos + pinning + debconf. Each top-level section drives `src/<section>.py`. |
| `src/apps_config.toml` | Shared `[env]`; one section per app, dispatched on marker keys: `desktop` (launcher override + flags via `features`/`switches`), `local_state` (Chromium Local State flags), `argv_json` (Electron argv.json), `settings_json` (JSON settings file with `settings_env` block, e.g. Claude Code) |
| `src/files/` | static assets installed verbatim (apt hooks, prefs, egpu-prime sources) |
| `logs/` | timestamped per-run log (chowned to invoking user) |

Edit `install.toml` (or `apps_config.toml`), re-run `just up`. Loaders only
rewrite files whose contents would actually change, so idle re-runs are cheap.

## eGPU Rollback

```bash
# eGPU service
sudo systemctl disable --now egpu-prime.service
sudo rm /etc/systemd/system/egpu-prime.service /usr/local/sbin/egpu-prime-switch

# Brave / Code Insiders flag overrides (back to system defaults)
rm ~/.local/share/applications/brave-browser.desktop
rm ~/.local/share/applications/code-insiders.desktop
# argv.json: open and manually remove the keys you don't want; the file is JSON
```
