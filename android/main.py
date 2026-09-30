"""Xiaomi Bootloader Unlock Quota Helper - Android (Kivy) app. Reuses core.py (copied in at build time)."""
import json, os, random, statistics, threading, time
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock as KClock, mainthread
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.switch import Switch
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex as hexc, platform

from core import BJ, DEFAULT_SETTINGS, STATE_TEXT, Api, Clock as Ntp, fmt_bj, mask, next_midnight_epoch

ANDROID = platform == "android"
if ANDROID:
    from android.runnable import run_on_ui_thread
    from jnius import PythonJavaClass, autoclass, java_method
    PA = autoclass("org.kivy.android.PythonActivity")
else:
    run_on_ui_thread = lambda f: f

BG, CARD, TEXT, MUTED, ACCENT, TIME = "#0b0e1a", "#182038", "#e8ebf7", "#8088a6", "#7b8cff", "#ffb454"
OK, WARN, BAD = "#5ee6a8", "#ffb454", "#ff6b81"
LOGIN_URL = "https://new.c.mi.com/global"


def kind_color(text):
    t = (text or "").lower()
    if any(w in t for w in ("expired", "error")):
        return BAD
    if any(w in t for w in ("blocked", "limit", "under 30", "finished", "unknown")):
        return WARN
    if any(w in t for w in ("approved", "ready")):
        return OK
    return TEXT if t else MUTED


def lbl(text="", size=14, color=TEXT, **kw):
    return Label(text=text, font_size=dp(size), color=hexc(color), **kw)


def btn(text, cb, primary=False, danger=False, h=44, **kw):
    bg = ACCENT if primary else ("#ff6b81" if danger else CARD)
    b = Button(text=text, size_hint_y=None, height=dp(h), background_normal="", background_color=hexc(bg),
               color=hexc("#0b0e1a" if primary or danger else TEXT), font_size=dp(14), **kw)
    b.bind(on_release=lambda *_: cb())
    return b


def field(text="", hint="", **kw):
    return TextInput(text=text, hint_text=hint, multiline=False, size_hint_y=None, height=dp(42),
                     background_color=hexc("#0f1424"), foreground_color=hexc(TEXT),
                     hint_text_color=hexc(MUTED), cursor_color=hexc(ACCENT), font_size=dp(14), **kw)


def scroll_col(spacing=8):
    sv = ScrollView()
    col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(spacing), padding=dp(8))
    col.bind(minimum_height=col.setter("height"))
    sv.add_widget(col)
    return sv, col


# ---------------- Android glue ----------------
if ANDROID:
    class _Click(PythonJavaClass):
        __javainterfaces__ = ["android/view/View$OnClickListener"]
        __javacontext__ = "app"

        def __init__(self, cb):
            super().__init__()
            self.cb = cb

        @java_method("(Landroid/view/View;)V")
        def onClick(self, view):
            self.cb()


class WebLogin:
    """Shows a WebView over the app. After logging in, reads new_bbs_serviceToken from Android's cookie store
    (this includes HttpOnly cookies, so nothing needs to be copied by hand)."""

    def __init__(self, on_token):
        self.on_token, self.layout, self.wv = on_token, None, None

    @run_on_ui_thread
    def open(self):
        act = PA.mActivity
        WebView, WVC = autoclass("android.webkit.WebView"), autoclass("android.webkit.WebViewClient")
        CM, LL = autoclass("android.webkit.CookieManager"), autoclass("android.widget.LinearLayout")
        Button_, VLP = autoclass("android.widget.Button"), autoclass("android.view.ViewGroup$LayoutParams")
        LLP = autoclass("android.widget.LinearLayout$LayoutParams")
        self.wv = WebView(act)
        s = self.wv.getSettings()
        s.setJavaScriptEnabled(True)
        s.setDomStorageEnabled(True)
        self.wv.setWebViewClient(WVC())
        cm = CM.getInstance()
        cm.setAcceptCookie(True)
        cm.setAcceptThirdPartyCookies(self.wv, True)
        self.wv.loadUrl(LOGIN_URL)
        b = Button_(act)
        b.setText("I'm logged in - get token")
        self._click = _Click(self.finish)
        b.setOnClickListener(self._click)
        self.layout = LL(act)
        self.layout.setOrientation(1)
        self.layout.setBackgroundColor(0xFF000000)
        self.layout.addView(b, LLP(-1, -2))
        self.layout.addView(self.wv, LLP(-1, 0, 1.0))
        act.addContentView(self.layout, VLP(-1, -1))

    @run_on_ui_thread
    def finish(self):
        CM = autoclass("android.webkit.CookieManager")
        token = None
        for url in (LOGIN_URL, "https://new.c.mi.com", "https://c.mi.com"):
            for part in (CM.getInstance().getCookie(url) or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "new_bbs_serviceToken" and v:
                    token = v.strip('"')
            if token:
                break
        self.layout.getParent().removeView(self.layout)
        self.wv.destroy()
        self.on_token(token)


@run_on_ui_thread
def keep_screen_on(on):
    w = PA.mActivity.getWindow()
    (w.addFlags if on else w.clearFlags)(128)  # FLAG_KEEP_SCREEN_ON


_wake = None


def wake_lock(on):
    global _wake
    if not ANDROID:
        return
    try:
        if on and _wake is None:
            _wake = PA.mActivity.getSystemService("power").newWakeLock(1, "xqh:run")
            _wake.acquire()
        elif not on and _wake is not None:
            _wake.release()
            _wake = None
    except Exception:
        pass


def open_battery_settings():
    if ANDROID:
        PA.mActivity.startActivity(autoclass("android.content.Intent")("android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS"))


# ---------------- App ----------------
class QuotaApp(App):
    title = "Xiaomi Quota Helper"

    def build(self):
        Window.clearcolor = hexc(BG)
        self.cfg_path = os.path.join(self.user_data_dir, "config.json")
        self.data = {"accounts": [], "settings": dict(DEFAULT_SETTINGS)}
        if os.path.exists(self.cfg_path):
            try:
                self.data.update(json.load(open(self.cfg_path, encoding="utf-8")))
            except Exception:
                pass
        self.data["settings"] = {**DEFAULT_SETTINGS, **self.data.get("settings", {})}
        for a in self.data["accounts"]:
            a.setdefault("id", str(random.randint(10**8, 10**9)))
            a.setdefault("enabled", True)
            a.setdefault("offset_ms", 0)
        self.running, self.phase, self.target, self.clock = False, "Idle", None, Ntp()
        self.stop_ev, self.lines, self.status_lbls = threading.Event(), [], {}

        tp = TabbedPanel(do_default_tab=False, tab_width=dp(110), background_color=hexc(BG))
        for name, builder in (("Dashboard", self.build_dash), ("Accounts", self.build_accounts), ("Log", self.build_log)):
            item = TabbedPanelItem(text=name)
            item.add_widget(builder())
            tp.add_widget(item)
        tp.default_tab = tp.tab_list[-1]
        KClock.schedule_interval(self.tick, 0.1)
        self.refresh()
        self.log("Welcome. Add an account (Accounts tab), check its token, then press Start.")
        return tp

    def on_pause(self):
        return True  # keep running in the background

    def save(self):
        clean = [{k: v for k, v in a.items() if k != "status"} for a in self.data["accounts"]]
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump({"accounts": clean, "settings": self.data["settings"]}, f, indent=2)

    # ---- dashboard ----
    def build_dash(self):
        sv, col = scroll_col(10)
        self.l_phase = lbl("Idle", 14, MUTED, size_hint_y=None, height=dp(24))
        self.l_count = lbl("--:--:--", 52, TIME, size_hint_y=None, height=dp(80), bold=True)
        self.l_target = lbl("", 12, MUTED, size_hint_y=None, height=dp(20))
        self.l_stats = lbl("", 13, TEXT, size_hint_y=None, height=dp(24))
        self.b_run = btn("Start", self.toggle_run, primary=True, h=56)
        for w in (self.l_phase, self.l_count, self.l_target, self.l_stats, self.b_run):
            col.add_widget(w)
        s = self.data["settings"]
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        row.add_widget(lbl("Also send for blocked / new accounts", 13, TEXT, halign="left"))
        self.chk = CheckBox(active=bool(s["allow_blocked"]), size_hint_x=None, width=dp(44))
        row.add_widget(self.chk)
        col.add_widget(row)
        self.in_int, self.in_win, self.in_snd = (field(str(s["interval_ms"])), field(str(s["window_s"])),
                                                 field(str(s["senders"])))
        for text, w in (("Retry every (ms)", self.in_int), ("Keep trying for (s)", self.in_win),
                        ("Parallel senders per account (1-6)", self.in_snd)):
            col.add_widget(lbl(text, 12, MUTED, size_hint_y=None, height=dp(20), halign="left"))
            col.add_widget(w)
        self.b_check = btn("Check tokens", self.check_tokens)
        self.b_meas = btn("Measure offsets", self.measure)
        col.add_widget(self.b_check)
        col.add_widget(self.b_meas)
        col.add_widget(btn("Battery settings (allow background)", open_battery_settings))
        col.add_widget(lbl("Keep the app open and the screen on until the run finishes.", 11, MUTED,
                           size_hint_y=None, height=dp(30)))
        return sv

    def toggle_run(self):
        self.stop() if self.running else self.start()

    def tick(self, _dt):
        now = time.time()
        if self.running and self.target:
            remaining, target = self.target - self.clock.now(), self.target
        else:
            target = next_midnight_epoch(now)
            remaining = target - now
        r = max(remaining, 0)
        h, rem = divmod(int(r), 3600)
        m, s = divmod(rem, 60)
        self.l_count.text = f"{h:02d}:{m:02d}:{r % 60:04.1f}" if 0 < r < 10 else f"{h:02d}:{m:02d}:{s:02d}"
        self.l_phase.text = self.phase if self.running else "Idle"
        self.l_target.text = f"Beijing {datetime.fromtimestamp(target, BJ):%a %d %b} 00:00:00"
        accs = self.data["accounts"]
        self.l_stats.text = f"{sum(1 for a in accs if a.get('enabled', True))} of {len(accs)} accounts enabled"

    # ---- accounts ----
    def build_accounts(self):
        root = BoxLayout(orientation="vertical", padding=dp(6), spacing=dp(6))
        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        bar.add_widget(btn("Add account", self.add_dialog, primary=True))
        bar.add_widget(btn("Log in (browser)", self.web_login))
        root.add_widget(bar)
        sv, self.acc_col = scroll_col(8)
        root.add_widget(sv)
        return root

    def refresh(self):
        self.acc_col.clear_widgets()
        self.status_lbls = {}
        if not self.data["accounts"]:
            self.acc_col.add_widget(lbl("No accounts yet.\nTap 'Log in (browser)' or 'Add account'.", 14, MUTED,
                                        size_hint_y=None, height=dp(80)))
        for a in self.data["accounts"]:
            card = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(130), padding=dp(8), spacing=dp(4))
            top = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
            sw = Switch(active=a.get("enabled", True), size_hint_x=None, width=dp(70))
            sw.bind(active=lambda _w, v, a=a: self._set_enabled(a, v))
            top.add_widget(sw)
            top.add_widget(lbl(f"{a['name']}  [{mask(a['token'])}]", 13, TEXT, halign="left"))
            card.add_widget(top)
            st = lbl(a.get("status", "") or "Not checked", 12, kind_color(a.get("status", "")),
                     size_hint_y=None, height=dp(20), halign="left")
            self.status_lbls[a["id"]] = st
            card.add_widget(st)
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
            row.add_widget(lbl("Offset ms", 12, MUTED, size_hint_x=None, width=dp(70)))
            off = field(f"{a['offset_ms']:g}", input_filter="float", size_hint_x=None, width=dp(70))
            off.bind(focus=lambda w, f, a=a: None if f else self._set_offset(a, w.text))
            row.add_widget(off)
            row.add_widget(btn("Check", lambda a=a: self.check_one(a), h=40))
            row.add_widget(btn("Remove", lambda a=a: self.remove(a), danger=True, h=40))
            card.add_widget(row)
            self.acc_col.add_widget(card)

    def _set_enabled(self, a, v):
        a["enabled"] = bool(v)
        self.save()

    def _set_offset(self, a, text):
        try:
            a["offset_ms"] = float(text)
            self.save()
        except ValueError:
            pass

    def remove(self, a):
        self.data["accounts"].remove(a)
        self.save()
        self.refresh()

    def add_dialog(self, token=""):
        body = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(6))
        name, tok, off = field("Account", "Name"), field(token, "new_bbs_serviceToken"), field("0", "Offset ms", input_filter="float")
        for w in (name, tok):
            body.add_widget(w)
        body.add_widget(btn("Paste token from clipboard", lambda: setattr(tok, "text", (Clipboard.paste() or "").strip())))
        body.add_widget(off)
        pop = Popup(title="Add account", content=body, size_hint=(0.94, None), height=dp(400))

        def save():
            t = tok.text.strip().strip('"')
            if t:
                self.add_account(name.text.strip() or "Account", t, float(off.text or 0))
            pop.dismiss()
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        row.add_widget(btn("Cancel", pop.dismiss))
        row.add_widget(btn("Save", save, primary=True))
        body.add_widget(row)
        pop.open()

    def add_account(self, name, token, offset=0.0):
        if any(a["token"] == token for a in self.data["accounts"]):
            return self.log("That token is already saved.")
        self.data["accounts"].append({"id": str(random.randint(10**8, 10**9)), "name": name, "token": token,
                                      "offset_ms": offset, "enabled": True})
        self.save()
        self.refresh()
        self.log(f"Token added for '{name}'.")

    def web_login(self):
        if not ANDROID:
            return self.log("[!] Browser login only works on Android. Use 'Add account' and paste the token.")
        WebLogin(self._got_token).open()

    @mainthread
    def _got_token(self, token):
        if token:
            self.add_account(f"Account {len(self.data['accounts']) + 1}", token, 66.0)
        else:
            self.log("[!] Token not found. Make sure you are fully logged in, then try again.")

    # ---- log ----
    def build_log(self):
        sv = ScrollView()
        self.log_lbl = Label(text="", font_size=dp(12), color=hexc(TEXT), size_hint_y=None, halign="left",
                             valign="top", markup=False)
        self.log_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w - dp(12), None)),
                          texture_size=lambda i, s: setattr(i, "height", s[1] + dp(12)))
        sv.add_widget(self.log_lbl)
        return sv

    @mainthread
    def log(self, msg, name=None):
        self.lines.append(f"{datetime.now():%H:%M:%S.%f}"[:-3] + (f" {name}" if name else "") + f"  {msg}")
        self.lines = self.lines[-300:]
        self.log_lbl.text = "\n".join(self.lines)

    @mainthread
    def set_status(self, acc_id, text):
        for a in self.data["accounts"]:
            if a["id"] == acc_id:
                a["status"] = text
        w = self.status_lbls.get(acc_id)
        if w:
            w.text, w.color = text, hexc(kind_color(text))

    # ---- check / measure ----
    def _check(self, a):
        self.set_status(a["id"], "Checking...")
        st = Api(a["token"]).status()
        text = STATE_TEXT.get(st["state"], st["state"])
        if st.get("deadline") and st["state"] in ("blocked", "approved"):
            text += f" until {st['deadline']}"
        self.set_status(a["id"], text)
        self.log(f"Status: {text}", a["name"])

    def check_one(self, a):
        threading.Thread(target=self._check, args=(a,), daemon=True).start()

    def check_tokens(self):
        accs = list(self.data["accounts"])
        threading.Thread(target=lambda: [self._check(a) for a in accs], daemon=True).start()

    def measure(self):
        accs = list(self.data["accounts"])
        self.log("Measuring round-trip time (5 requests per account)...")

        def job():
            for a in accs:
                api = Api(a["token"])
                if api.status()["state"] in ("error", "expired"):
                    self.log("Skipped: token problem", a["name"])
                    continue
                rtts = []
                for _ in range(5):
                    s = api.status()
                    rtts += [s["rtt"]] if "rtt" in s else []
                    time.sleep(0.2)
                if rtts:
                    rtt = statistics.median(rtts)
                    a["offset_ms"] = round(rtt / 2)
                    self.log(f"Median round trip {rtt:.0f} ms, offset set to {a['offset_ms']} ms", a["name"])
            self.save()
            KClock.schedule_once(lambda _dt: self.refresh())
        threading.Thread(target=job, daemon=True).start()

    # ---- run ----
    def start(self):
        accs = [a for a in self.data["accounts"] if a.get("enabled", True)]
        if not accs:
            return self.log("[!] No enabled accounts.")
        try:
            cfg = {"allow_blocked": bool(self.chk.active), "interval_ms": max(20, int(float(self.in_int.text))),
                   "window_s": max(1, int(float(self.in_win.text))),
                   "senders": min(6, max(1, int(float(self.in_snd.text))))}
        except ValueError:
            return self.log("[!] Settings must be numbers.")
        self.data["settings"] = cfg
        self.save()
        self.stop_ev, self.running, self.target, self.phase = threading.Event(), True, None, "Syncing clock"
        self.b_run.text = "Stop"
        keep_screen_on(True)
        wake_lock(True)
        threading.Thread(target=self._run, args=(accs, cfg), daemon=True).start()

    def stop(self):
        self.stop_ev.set()
        self.log("Stopping...")

    @mainthread
    def _finished(self):
        self.running, self.phase = False, "Idle"
        self.b_run.text = "Start"
        keep_screen_on(False)
        wake_lock(False)
        self.log("Run finished.")

    def _wait_until(self, epoch):
        while not self.stop_ev.is_set():
            left = epoch - self.clock.now()
            if left <= 0:
                return True
            if left > 0.05:
                self.stop_ev.wait(min(left - 0.05, 0.25))
        return False

    def _run(self, accs, cfg):
        clock = Ntp()
        if not clock.sync(self.log):
            self.log("[!] Could not reach any NTP server. Check your connection.")
            return self._finished()
        self.clock, self.target = clock, next_midnight_epoch(clock.now())
        self.phase = "Waiting for target"
        self.log(f"Target is Beijing midnight, {datetime.fromtimestamp(self.target, BJ):%Y-%m-%d %H:%M:%S}.")

        def resync():
            for lead in (900, 60):
                if self.target - self.clock.now() > lead and self._wait_until(self.target - lead):
                    self.clock.sync(self.log, label="Re-sync")
        threading.Thread(target=resync, daemon=True).start()
        ws = [threading.Thread(target=self._worker, args=(a, cfg), daemon=True) for a in accs]
        [t.start() for t in ws]
        [t.join() for t in ws]
        self._finished()

    def _worker(self, acc, cfg):
        st = Api(acc["token"]).status()["state"]
        self.set_status(acc["id"], STATE_TEXT.get(st, st))
        self.log(f"Status: {STATE_TEXT.get(st, st)}", acc["name"])
        if st in ("expired", "approved", "unknown", "error"):
            return
        if st in ("blocked", "new_account") and not cfg["allow_blocked"]:
            return self.log("Skipped. Enable 'Also send for blocked / new accounts' to override.", acc["name"])
        send_at, done = self.target - acc["offset_ms"] / 1000.0, threading.Event()
        self.log(f"Offset {acc['offset_ms']:g} ms, {cfg['senders']} sender(s), sending at {fmt_bj(send_at)} (UTC+8)", acc["name"])
        ts = [threading.Thread(target=self._sender, args=(acc, i + 1, cfg, send_at, done), daemon=True)
              for i in range(cfg["senders"])]
        [t.start() for t in ts]
        [t.join() for t in ts]
        if not done.is_set():
            self.set_status(acc["id"], "Finished (not approved)")

    def _sender(self, acc, idx, cfg, send_at, done):
        name = acc["name"] if cfg["senders"] == 1 else f"{acc['name']}#{idx}"
        api = Api(acc["token"])

        def fin(status, msg):
            if not done.is_set():
                done.set()
                self.set_status(acc["id"], status)
            self.log(msg, name)
        if not self._wait_until(send_at - 3) or done.is_set():
            return
        api.status()  # warm-up
        if not self._wait_until(send_at):
            return
        self.phase = "Sending requests"
        end, gap = self.clock.now() + cfg["window_s"], cfg["interval_ms"] / 1000.0
        while not self.stop_ev.is_set() and not done.is_set() and self.clock.now() < end:
            self.log(f"Sending request at {fmt_bj(self.clock.now())}", name)
            r = api.apply()
            if "_error" in r:
                self.log(f"Network error: {r['_error']}", name)
            else:
                code, data = r.get("code"), r.get("data", {}) or {}
                dl, res = data.get("deadline_format", "not specified"), data.get("apply_result")
                if code == 0 and res == 1:
                    s = STATE_TEXT.get(api.status()["state"], "?")
                    return fin("Approved" if s == "Already approved" else s, f"APPROVED. Final status: {s}")
                if code == 0 and res == 3:
                    return fin(f"Limit reached until {dl}", f"Limit reached until {dl} (Month/Day).")
                if code == 0 and res == 4:
                    return fin(f"Blocked until {dl}", f"Blocked until {dl} (Month/Day).")
                if code == 100003 and api.status()["state"] == "approved":
                    return fin("Approved", "APPROVED.")
                if code == 100004:
                    return fin("Cookie expired", "Cookie expired, need a new token.")
                self.log("Request rejected, retrying..." if code == 100001 else f"Answer: {r}", name)
            self.stop_ev.wait(gap)


if __name__ == "__main__":
    QuotaApp().run()
