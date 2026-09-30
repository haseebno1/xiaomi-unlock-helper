
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
