"""Shared engine: NTP clock, Xiaomi API client, constants. Used by the GUI and the CLI."""
import hashlib
import json
import os
import platform
import queue
import random
import statistics
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone

AUTHOR_NAME = "Abdul Haseeb"
AUTHOR_GITHUB = "github.com/haseebno1"
AUTHOR_URL = "https://github.com/haseebno1"


def ensure_package(module, pip_name=None):
    try:
        return __import__(module)
    except ImportError:
        print(f"[!] Installing missing package: {pip_name or module}...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name or module])
        except subprocess.CalledProcessError:
            sys.exit(f"Could not auto-install '{pip_name or module}'. Run: pip install -r requirements.txt "
                     "(inside a virtual environment on newer Linux/macOS).")
        return __import__(module)


urllib3 = ensure_package("urllib3")
ntplib = ensure_package("ntplib")


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
BJ = timezone(timedelta(hours=8))  # Beijing time (UTC+8)

STATUS_URL = "https://sgp-api.buy.mi.com/bbs/api/global/user/bl-switch/state"
APPLY_URL = "https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth"
CHROME_LINK = "https://new.c.mi.com/global"

NTP_SERVERS = [
    "ntp.aliyun.com", "ntp.tencent.com", "cn.pool.ntp.org", "edu.ntp.org.cn",
    "time.apple.com", "time.google.com", "pool.ntp.org",
]

DEFAULT_SETTINGS = {"allow_blocked": False, "interval_ms": 100, "window_s": 30, "senders": 2}

STATE_TEXT = {
    "ready": "Ready",
    "blocked": "Blocked (cooldown)",
    "new_account": "Account under 30 days",
    "approved": "Already approved",
    "expired": "Cookie expired",
    "unknown": "Unknown status",
    "error": "Network error",
}



# --------------------------------------------------------------------------- #
# Engine helpers
# --------------------------------------------------------------------------- #
class Clock:
    """Beijing-accurate clock: NTP offset + perf_counter, re-synced shortly before the target."""

    def __init__(self):
        self.synced = False
        self.offset = 0.0
        self._anchor = (time.perf_counter(), time.time())

    def sync(self, log, label="NTP"):
        client = ntplib.NTPClient()
        samples = []
        for server in NTP_SERVERS:
            try:
                r = client.request(server, version=3, timeout=2)
                samples.append((r.delay, r.offset))
                log(f"{label} {server}: offset {r.offset * 1000:+.1f} ms, delay {r.delay * 1000:.0f} ms")
            except Exception as e:
                log(f"{label} {server} failed: {e}")
        if not samples:
            return False
        best = sorted(samples)[:3]
        off = statistics.median(o for _, o in best)
        self._anchor = (time.perf_counter(), time.time() + off)
        self.offset = off
        self.synced = True
        log(f"{label}: clock synced using the {len(best)} lowest-delay samples, PC is {off * 1000:+.1f} ms off")
        return True

    def now(self):
        perf, epoch = self._anchor
        return epoch + (time.perf_counter() - perf)


def next_midnight_epoch(now_epoch):
    dt = datetime.fromtimestamp(now_epoch, BJ)
    nxt = (dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def fmt_bj(epoch):
    return datetime.fromtimestamp(epoch, BJ).strftime("%H:%M:%S.%f")


def mask(token):
    return token if len(token) <= 12 else f"{token[:6]}…{token[-4:]}"


class Api:
    def __init__(self, token):
        self.device_id = hashlib.sha1(f"{random.random()}-{time.time()}".encode()).hexdigest().upper()
        self.headers = {
            "Cookie": f"new_bbs_serviceToken={token};versionCode=500411;versionName=5.4.11;deviceId={self.device_id};",
            "Content-Type": "application/json; charset=utf-8",
        }
        self.http = urllib3.PoolManager(
            maxsize=4, retries=False, timeout=urllib3.Timeout(connect=2.0, read=10.0)
        )

    def status(self):
        try:
            t0 = time.perf_counter()
            r = self.http.request("GET", STATUS_URL, headers=self.headers)
            rtt = (time.perf_counter() - t0) * 1000
            js = json.loads(r.data.decode("utf-8"))
        except Exception as e:
            return {"state": "error", "error": str(e)}
        if js.get("code") == 100004:
            return {"state": "expired", "rtt": rtt}
        data = js.get("data", {}) or {}
        deadline = data.get("deadline_format", "")
        is_pass, button = data.get("is_pass"), data.get("button_state")
        if is_pass == 1:
            return {"state": "approved", "deadline": deadline, "rtt": rtt}
        if is_pass == 4:
            state = {1: "ready", 2: "blocked", 3: "new_account"}.get(button, "unknown")
            return {"state": state, "deadline": deadline, "rtt": rtt}
        return {"state": "unknown", "rtt": rtt}

    def apply(self):
        headers = dict(self.headers)
        headers.update({
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "okhttp/4.12.0",
            "Connection": "keep-alive",
        })
        try:
            r = self.http.request("POST", APPLY_URL, headers=headers, body=b'{"is_retry":true}')
            return json.loads(r.data.decode("utf-8"))
        except Exception as e:
            return {"_error": str(e)}
