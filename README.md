# Xiaomi Bootloader Unlock Quota Helper

A small open-source tool that manages your Xiaomi community accounts, checks whether their tokens work, measures network delay, and sends the unlock-permission request at **Beijing midnight (00:00 UTC+8)** using NTP-synced timing.

It comes in two forms that share the same engine (`core.py`) and the same `config.json`:

| | File | Runs on |
|---|---|---|
| **GUI** (responsive window) | `xiaomi_quota_helper.py` | Windows, macOS, Linux |
| **CLI** (no window) | `xiaomi_quota_cli.py` | Windows, macOS, Linux, **Android via Termux**, servers/VPS |

> **Disclaimer.** This is an unofficial tool, not affiliated with Xiaomi. It uses undocumented endpoints that can change at any time, and automated requests may be against Xiaomi's terms. You use it at your own risk. Your token gives full access to your Xiaomi community account: never share it or commit it. This tool only requests *permission*; it does not unlock anything by itself.

## Features
- Multiple accounts with per-account millisecond offsets and on/off switches
- Token checker and one-click offset measurement (median round-trip / 2)
- NTP sync from 7 servers, automatic re-sync 15 min and 1 min before the target
- Parallel senders per account, retry interval and time window
- Dashboard with countdown, status tiles and activity log (GUI)
- Import from `token.txt`, Firefox cookies or a Chrome login (GUI)

## Install

Requires **Python 3.9+**.

```bash
git clone https://github.com/haseebno1/xiaomi-unlock-helper.git
cd xiaomi-unlock-helper
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-optional.txt   # optional: browser imports (GUI)
```

Per platform:
- **Windows:** install Python from python.org (tick *Add to PATH*). Nothing else needed.
- **macOS:** use Python from python.org or `brew install python-tk`. Apple's built-in Python has an outdated Tk.
- **Linux:** install Tk first: `sudo apt install python3-tk` (Debian/Ubuntu) or `sudo dnf install python3-tkinter` (Fedora).
- **Android (Termux):** GUI is not supported, use the CLI:
  ```bash
  pkg install python git
  git clone https://github.com/haseebno1/xiaomi-unlock-helper.git && cd xiaomi-unlock-helper
  pip install urllib3 ntplib
  ```
  Keep the screen on and disable battery optimization for Termux, otherwise Android may pause it before midnight.

## Get your token
1. Log in at <https://new.c.mi.com/global> in a desktop browser.
2. Open DevTools, then *Application* (Chrome) or *Storage* (Firefox), then *Cookies*.
3. Copy the value of `new_bbs_serviceToken`.

## Use the GUI
```bash
python xiaomi_quota_helper.py
```
1. **Accounts** tab: *Add account* (or *Import*), paste the token.
2. *Check tokens* should show **Ready**. *Measure offsets* fills in each account's offset; adjust by hand if you like.
3. **Dashboard** tab: review the run settings, then press **Start** before midnight Beijing time. The app syncs the clock, waits, and sends on time.
4. Keep the app open and the computer awake until it logs *Run finished*.

## Use the CLI
```bash
python xiaomi_quota_cli.py add Main --offset 66     # prompts for the token (hidden)
python xiaomi_quota_cli.py list
python xiaomi_quota_cli.py check
python xiaomi_quota_cli.py measure
python xiaomi_quota_cli.py run                      # waits for Beijing midnight
python xiaomi_quota_cli.py run --only Main --senders 3 --interval 80 --window 30
python xiaomi_quota_cli.py enable all | disable Main | remove Main
```
Use `--config path/to/config.json` to keep accounts elsewhere. `run` also works under `tmux`/`screen` on a server.

## How it works
1. NTP gives the exact offset of your clock; a monotonic timer keeps time between syncs.
2. The target is the next 00:00:00 in UTC+8. Each account sends at `target - offset`, so the request *arrives* at midnight.
3. Senders warm up their connection 3 s early, then send every *interval* ms for *window* seconds until approved, limited, blocked, or the cookie expires.
4. After approval, sign in with the same account in the official Mi Unlock Tool.

## Troubleshooting
- **Cookie expired:** log in again and replace the token.
- **NTP servers fail:** check that UDP port 123 is not blocked; the app needs at least one server.
- **`externally-managed-environment` on Linux/macOS:** use the virtual environment shown above.
- **Fonts look different:** the GUI picks Segoe UI / Helvetica Neue / DejaVu Sans per OS. Edit `FONTS` in the GUI file to change it.

## Files
`core.py` engine • `xiaomi_quota_helper.py` GUI • `xiaomi_quota_cli.py` CLI • `config.example.json` sample config (real `config.json` is git-ignored)

## Contributing & license
Issues and pull requests are welcome. Licensed under the [MIT License](LICENSE). Author: [Abdul Haseeb](https://github.com/haseebno1).
