# Xiaomi Bootloader Unlock Quota Helper – Cross-Platform GUI and CLI

[![Stars](https://img.shields.io/github/stars/haseebno1/xiaomi-unlock-helper?style=flat)](https://github.com/haseebno1/xiaomi-unlock-helper/stargazers)
[![License](https://img.shields.io/github/license/haseebno1/xiaomi-unlock-helper)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue)
![Interface](https://img.shields.io/badge/interface-GUI%20%2B%20CLI-green)

A **cross-platform GUI and CLI** helper for the official Xiaomi bootloader
unlock request on **Xiaomi, Redmi and POCO** phones (HyperOS / MIUI). It
manages your Xiaomi Community accounts, checks tokens, syncs to Beijing time,
and sends your request at the moment the daily quota resets (00:00 Beijing).

Helps with:
- "Quota limit reached" in the Xiaomi Community app
- "Couldn't unlock. Try again after X days"
- "Apply for unlocking permission" failing or timing out

![Xiaomi Bootloader Unlock Quota Helper dashboard showing Beijing-time countdown, account status and run settings](/dashboard.png)

## Features
- **Dashboard:** live countdown to Beijing midnight, overview of accounts, ready, approved and needs-attention
- **Account manager:** add multiple accounts, including Firefox sessions and imported Chrome sessions
- **Token checker:** verify tokens before the run so you don't waste your window
- **Timing monitor:** Beijing clock sync and per-account latency offset measurement
- **Configurable run settings:** retry interval (ms), retry duration (s), 1–6 parallel senders per account
- **Options:** optionally send for blocked or new accounts
- **GUI and CLI:** point-and-click app, or scriptable command line
- **Cross-platform:** Windows, Linux, macOS

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


## Android app (APK)

A native Android app (Kivy) reuses the same `core.py` engine. It has Dashboard / Accounts / Log tabs and **Log in (browser)**: an in-app web login that reads your token straight from Android's cookie store, so you never copy cookies by hand.

### Get the APK
- **From GitHub:** open the repo's **Actions** tab, choose *Build Android APK*, press **Run workflow**, and download the `xiaomi-quota-helper-apk` artifact (about 10 to 25 minutes on the first build). Pushing a tag like `v1.0.0` also attaches the APK to a GitHub Release.
- **Locally (Linux/WSL):** `pip install buildozer "cython<3"`, then `cp core.py android/ && cd android && buildozer android debug`. The APK lands in `android/bin/`.

### Install and use
1. Copy the APK to your phone and open it. Allow *Install unknown apps* when asked. Play Protect may warn because it is an unsigned debug build.
2. **Accounts** tab, then **Log in (browser)**, sign in to the Xiaomi community, press **I'm logged in - get token**. (Or use **Add account** and paste a token.)
3. **Dashboard**: *Check tokens*, *Measure offsets*, then **Start** before midnight Beijing time.
4. **Keep the app open with the screen on** until *Run finished*. While a run is active the app keeps the screen awake and holds a wake lock. Tap **Battery settings** and exempt the app from battery optimization, or Android may pause it before midnight.

Phone networks have more jitter than a wired PC, so expect less precise timing. Accounts are stored in the app's private storage (not shared with the desktop `config.json`).
