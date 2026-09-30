#!/usr/bin/env python3
"""Command-line version for Linux, macOS, Windows and Termux (Android). Shares config.json with the GUI."""
import argparse, getpass, json, os, random, statistics, sys, threading, time
from datetime import datetime

from core import (BJ, CONFIG_FILE, DEFAULT_SETTINGS, STATE_TEXT, Api, Clock,
                  fmt_bj, mask, next_midnight_epoch)


def log(msg, name=None):
    print(f"{datetime.now():%H:%M:%S.%f}"[:-3] + (f"  {name}" if name else "") + f"  {msg}", flush=True)


def load(path):
    data = {"accounts": [], "settings": dict(DEFAULT_SETTINGS)}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data.update(json.load(f))
    data["settings"] = {**DEFAULT_SETTINGS, **data.get("settings", {})}
    for a in data["accounts"]:
        a.setdefault("id", f"{int(time.time() * 1000)}{random.randint(0, 9999)}")
        a.setdefault("enabled", True)
        a.setdefault("offset_ms", 0)
    return data


def save(path, data):
    clean = [{k: v for k, v in a.items() if k != "status"} for a in data["accounts"]]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"accounts": clean, "settings": data["settings"]}, f, indent=2)


def pick(data, names):
    accs = [a for a in data["accounts"] if a.get("enabled", True)]
    return [a for a in accs if not names or a["name"] in names]


def cmd_list(a, d):
    if not d["accounts"]:
        return print("No accounts. Add one with: add NAME --offset 66")
    for x in d["accounts"]:
        print(f'{x["name"]:<20} {mask(x["token"]):<14} offset {x["offset_ms"]:g} ms  {"on" if x["enabled"] else "off"}')


def cmd_add(a, d):
    token = (a.token or getpass.getpass("Paste new_bbs_serviceToken (hidden): ")).strip().strip('"')
    if any(x["token"] == token for x in d["accounts"]):
        return print("That token is already saved.")
    d["accounts"].append({"id": f"{int(time.time() * 1000)}", "name": a.name, "token": token,
                          "offset_ms": a.offset, "enabled": True})
    save(a.config, d)
    print(f"Added '{a.name}'.")


def cmd_remove(a, d):
    n = len(d["accounts"])
    d["accounts"] = [x for x in d["accounts"] if x["name"] != a.name]
    save(a.config, d)
    print("Removed." if len(d["accounts"]) < n else "No account with that name.")


def cmd_toggle(a, d):
    for x in d["accounts"]:
        if a.name in ("all", x["name"]):
            x["enabled"] = a.cmd == "enable"
    save(a.config, d)


def cmd_check(a, d):
    for x in d["accounts"]:
        st = Api(x["token"]).status()
        text = STATE_TEXT.get(st["state"], st["state"])
        if st.get("deadline") and st["state"] in ("blocked", "approved"):
            text += f" until {st['deadline']}"
        log(text, x["name"])


def cmd_measure(a, d):
    for x in d["accounts"]:
        api = Api(x["token"])
        first = api.status()
        if first["state"] in ("error", "expired"):
            log(f"Skipped: {STATE_TEXT[first['state']]}", x["name"])
            continue
        rtts = []
        for _ in range(5):
            s = api.status()
            if "rtt" in s:
                rtts.append(s["rtt"])
            time.sleep(0.2)
        if rtts:
            rtt = statistics.median(rtts)
            x["offset_ms"] = round(rtt / 2)
            log(f"Median round trip {rtt:.0f} ms, offset set to {x['offset_ms']} ms", x["name"])
    save(a.config, d)


def cmd_run(a, d):
    cfg = dict(d["settings"])
    for k, v in (("interval_ms", a.interval), ("window_s", a.window), ("senders", a.senders)):
        if v:
            cfg[k] = v
    cfg["allow_blocked"] = cfg["allow_blocked"] or a.allow_blocked
    accs = pick(d, a.only)
    if not accs:
        return print("No enabled accounts to run.")
    clock = Clock()
    if not clock.sync(log):
        sys.exit("Could not reach any NTP server.")
    target = next_midnight_epoch(clock.now())
    log(f"Target is Beijing midnight, {datetime.fromtimestamp(target, BJ):%Y-%m-%d %H:%M:%S}. Ctrl+C to stop.")
    stop = threading.Event()

    def wait_until(epoch):
        while not stop.is_set():
            left = epoch - clock.now()
            if left <= 0:
                return True
            if left > 0.05:
                stop.wait(min(left - 0.05, 0.25))
        return False

    def resync():
        for lead in (900, 60):
            if target - clock.now() > lead and wait_until(target - lead):
                clock.sync(log, label="Re-sync")

    def sender(acc, idx, n, send_at, done):
        name = acc["name"] if n == 1 else f'{acc["name"]}#{idx}'
        api = Api(acc["token"])
        if not wait_until(send_at - 3) or done.is_set():
            return
        api.status()  # warm-up
        if not wait_until(send_at):
            return
        end, gap = clock.now() + cfg["window_s"], cfg["interval_ms"] / 1000.0
        while not stop.is_set() and not done.is_set() and clock.now() < end:
            log(f"Sending request at {fmt_bj(clock.now())}", name)
            r = api.apply()
            if "_error" in r:
                log(f"Network error: {r['_error']}", name)
            else:
                code, data = r.get("code"), r.get("data", {}) or {}
                dl = data.get("deadline_format", "not specified")
                if code == 0 and data.get("apply_result") == 1:
                    s = STATE_TEXT.get(api.status()["state"])
                    done.set()
                    return log(f"APPROVED. Final status: {s}", name)
                if code == 0 and data.get("apply_result") in (3, 4):
                    done.set()
                    return log(f"Limit/blocked until {dl} (Month/Day).", name)
                if code == 100003 and api.status()["state"] == "approved":
                    done.set()
                    return log("APPROVED.", name)
                if code == 100004:
                    done.set()
                    return log("Cookie expired, need a new token.", name)
                log("Request rejected, retrying..." if code == 100001 else f"Answer: {r}", name)
            stop.wait(gap)

    def worker(acc):
        st = Api(acc["token"]).status()["state"]
        log(f"Status: {STATE_TEXT.get(st, st)}", acc["name"])
        if st in ("expired", "approved", "unknown", "error"):
            return
        if st in ("blocked", "new_account") and not cfg["allow_blocked"]:
            return log("Skipped. Use --allow-blocked to override.", acc["name"])
        send_at = target - acc["offset_ms"] / 1000.0
        done = threading.Event()
        ts = [threading.Thread(target=sender, args=(acc, i + 1, cfg["senders"], send_at, done), daemon=True)
              for i in range(cfg["senders"])]
        [t.start() for t in ts]
        [t.join() for t in ts]

    threading.Thread(target=resync, daemon=True).start()
    workers = [threading.Thread(target=worker, args=(x,), daemon=True) for x in accs]
    [t.start() for t in workers]
    try:
        while any(t.is_alive() for t in workers):
            left = target - clock.now()
            if left > 0:
                print(f"\r  time to target: {int(left // 3600):02d}:{int(left % 3600 // 60):02d}:{int(left % 60):02d}  ",
                      end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop.set()
        print("\nStopping...")
    print("\nRun finished.")


def main():
    p = argparse.ArgumentParser(description="Xiaomi Bootloader Unlock Quota Helper (CLI)")
    p.add_argument("--config", default=CONFIG_FILE, help="path to config.json (shared with the GUI)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("add"); s.add_argument("name"); s.add_argument("--token"); s.add_argument("--offset", type=float, default=0)
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("remove"); s.add_argument("name"); s.set_defaults(fn=cmd_remove)
    for c in ("enable", "disable"):
        s = sub.add_parser(c); s.add_argument("name", help="account name or 'all'"); s.set_defaults(fn=cmd_toggle)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("measure").set_defaults(fn=cmd_measure)
    s = sub.add_parser("run", help="wait for Beijing midnight and send")
    s.add_argument("--only", nargs="*", help="run only these account names")
    s.add_argument("--interval", type=int, help="retry every N ms")
    s.add_argument("--window", type=int, help="keep trying for N seconds")
    s.add_argument("--senders", type=int, choices=range(1, 7), help="parallel senders per account")
    s.add_argument("--allow-blocked", action="store_true")
    s.set_defaults(fn=cmd_run)
    a = p.parse_args()
    a.fn(a, load(a.config))


if __name__ == "__main__":
    main()
