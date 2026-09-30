# Xiaomi Bootloader Unlock Helper – Cross-Platform GUI and CLI

[![Stars](https://img.shields.io/github/stars/haseebno1/xiaomi-unlock-helper?style=flat)](https://github.com/haseebno1/xiaomi-unlock-helper/stargazers)
[![License](https://img.shields.io/github/license/haseebno1/xiaomi-unlock-helper)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue)
![Interface](https://img.shields.io/badge/interface-GUI%20%2B%20CLI-green)

**Cross-platform GUI and CLI** helper for unlocking the bootloader on
**Xiaomi, Redmi and POCO** phones running **HyperOS or MIUI**. Use the
graphical app if you want simplicity, or the command line for scripting and
headless setups. It helps fix:

- "Quota limit reached" in the Xiaomi Community app
- "Couldn't unlock. Try again after X days"
- "Apply for unlocking permission" failing or timing out
- Mi Unlock Tool stuck at 99% / "Couldn't verify device"

## Features
- **Cross-platform:** runs on Windows, Linux and macOS
- **GUI mode:** point-and-click interface, no terminal knowledge needed
- **CLI mode:** scriptable, works over SSH and on servers

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
