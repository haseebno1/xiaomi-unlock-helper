"""Xiaomi Bootloader Unlock Quota Helper - Android (Kivy) app. Reuses core.py (copied in at build time)."""
import json, os, random, re, statistics, sys, threading, time
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

from core import (APPLY_URL, AUTHOR_GITHUB, AUTHOR_NAME, AUTHOR_URL, BJ, DEFAULT_SETTINGS, NTP_SERVERS, STATE_TEXT,
                  STATUS_URL, Api, Clock as Ntp, fmt_bj, mask, next_midnight_epoch)

ANDROID = platform == "android"
if ANDROID:
    from android.runnable import run_on_ui_thread
    from jnius import PythonJavaClass, autoclass, cast, java_method
    PA = autoclass("org.kivy.android.PythonActivity")
else:
    run_on_ui_thread = lambda f: f

BG, CARD, TEXT, MUTED, ACCENT, TIME = "#0b0e1a", "#182038", "#e8ebf7", "#8088a6", "#7b8cff", "#ffb454"
OK, WARN, BAD = "#5ee6a8", "#ffb454", "#ff6b81"
LOGIN_URL = "https://new.c.mi.com/global"



def extract_token(text):
    """Find a new_bbs_serviceToken in whatever the user copied: a bare value, 'name=value', or Cookie-Editor JSON."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and it.get("name") == "new_bbs_serviceToken" and it.get("value"):
                return str(it["value"]).strip()
    except ValueError:
        pass
    m = re.search(r"new_bbs_serviceToken[\"']?\s*[=:]\s*[\"']?([^;\"'\s,}]+)", text)
    if m:
        return m.group(1)
    if len(text) >= 20 and not re.search(r"[\s{}\[\]]", text):
        return text.strip('"')
    return None


def open_url(url, chooser=True):
    """Open a link in an external browser (lets the user pick Firefox / Kiwi)."""
    try:
        if ANDROID:
            Intent, Uri, JString = autoclass("android.content.Intent"), autoclass("android.net.Uri"), autoclass("java.lang.String")
            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            if chooser:
                intent = Intent.createChooser(intent, cast("java.lang.CharSequence", JString("Open login page with")))
            PA.mActivity.startActivity(intent)
        else:
            import webbrowser
            webbrowser.open(url)
        return True
    except Exception:
        return False


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
    """In-app browser. Shows a WebView over the app; after logging in, reads new_bbs_serviceToken from Android's
    cookie store (HttpOnly cookies included). Every step reports problems through on_error instead of failing silently."""
    UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/124.0.0.0 Mobile Safari/537.36")

    def __init__(self, on_token, on_error):
        self.on_token, self.on_error, self.layout, self.wv = on_token, on_error, None, None
        self._clicks = []

    @run_on_ui_thread
    def open(self):
        try:
            act = PA.mActivity
            WebView, WVC = autoclass("android.webkit.WebView"), autoclass("android.webkit.WebViewClient")
            CM, LL = autoclass("android.webkit.CookieManager"), autoclass("android.widget.LinearLayout")
            Button_, VLP = autoclass("android.widget.Button"), autoclass("android.view.ViewGroup$LayoutParams")
            LLP = autoclass("android.widget.LinearLayout$LayoutParams")
            self.wv = WebView(act)
            st = self.wv.getSettings()
            st.setJavaScriptEnabled(True)
            st.setDomStorageEnabled(True)
            st.setUserAgentString(self.UA)
            self.wv.setWebViewClient(WVC())
            cm = CM.getInstance()
            cm.setAcceptCookie(True)
            cm.setAcceptThirdPartyCookies(self.wv, True)
            self.wv.loadUrl(LOGIN_URL)

            bar = LL(act)
            bar.setOrientation(0)
            for text, cb in (("Get token", self.finish), ("Close", self.close)):
                b = Button_(act)
                b.setText(text)
                click = _Click(cb)
                self._clicks.append(click)  # keep a reference so Java can still call it
                b.setOnClickListener(click)
                bar.addView(b, LLP(0, -2, 1.0))
            self.layout = LL(act)
            self.layout.setOrientation(1)
            self.layout.setBackgroundColor(0xFFFFFFFF)
            self.layout.addView(bar, LLP(-1, -2))
            self.layout.addView(self.wv, LLP(-1, 0, 1.0))
            act.addContentView(self.layout, VLP(-1, -1))
        except Exception as e:
            self.layout = None
            self.on_error(f"open: {e!r}")

    def _read_token(self):
        CM = autoclass("android.webkit.CookieManager")
        cm = CM.getInstance()
        for url in (LOGIN_URL, "https://new.c.mi.com", "https://c.mi.com", "https://mi.com",
                    "https://sgp-api.buy.mi.com"):
            for part in (cm.getCookie(url) or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "new_bbs_serviceToken" and v:
                    return v.strip('"')
        return None

    @run_on_ui_thread
    def finish(self):
        try:
            token = self._read_token()
        except Exception as e:
            return self.on_error(f"read cookie: {e!r}")
        if token:
            self._remove()
            self.on_token(token)
        else:
            self.on_error("no token yet - finish logging in on the page first, then tap 'Get token' again")

    @run_on_ui_thread
    def close(self):
        self._remove()

    def _remove(self):
        try:
            if self.layout is not None:
                self.layout.getParent().removeView(self.layout)
                self.wv.destroy()
        except Exception as e:
            self.on_error(f"close: {e!r}")
        self.layout = None


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
        Window.bind(on_keyboard=self._on_key)
        self.web = None
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

        tp = TabbedPanel(do_default_tab=False, tab_width=dp(88), background_color=hexc(BG))
        for name, builder in (("Dashboard", self.build_dash), ("Accounts", self.build_accounts), ("Log", self.build_log),
                              ("Developer", self.build_dev)):
            item = TabbedPanelItem(text=name)
            item.add_widget(builder())
            item.bind(on_release=lambda *_: self.update_dev())
            tp.add_widget(item)
        tp.default_tab = tp.tab_list[-1]
        KClock.schedule_interval(self.tick, 0.1)
        self.refresh()
        self.log("Welcome. Add an account (Accounts tab), check its token, then press Start.")
        return tp

    def _on_key(self, _w, key, *_a):
        if key == 27 and self.web is not None and self.web.layout is not None:
            self.web.close()
            return True
        return False

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
        bar = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(94), spacing=dp(6))
        r1, r2 = BoxLayout(spacing=dp(6)), BoxLayout(spacing=dp(6))
        r1.add_widget(btn("Add account", self.add_dialog, primary=True))
        r1.add_widget(btn("Log in (in-app)", self.web_login))
        r2.add_widget(btn("Firefox / Kiwi", self.token_help))
        r2.add_widget(btn("Paste token", self.paste_token))
        bar.add_widget(r1)
        bar.add_widget(r2)
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
        body.add_widget(btn("Paste token from clipboard", lambda: setattr(tok, "text", extract_token(Clipboard.paste()) or (Clipboard.paste() or "").strip())))
        body.add_widget(off)
        pop = Popup(title="Add account", content=body, size_hint=(0.94, None), height=dp(400))

        def save():
            t = extract_token(tok.text) or tok.text.strip().strip('"')
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
            return self.log("[!] The in-app browser only works on Android. Use 'Paste token' instead.")
        self.log("Opening in-app browser... log in, then tap 'Get token' at the top.")
        self.web = WebLogin(self._got_token, self._web_error)
        self.web.open()

    @mainthread
    def _web_error(self, msg):
        self.log(f"[!] In-app browser: {msg}")
        self.log("Tip: use 'Firefox / Kiwi' (Cookie-Editor) and then 'Paste token'.")

    @mainthread
    def _got_token(self, token):
        self.add_account(f"Account {len(self.data['accounts']) + 1}", token, 66.0)

    def paste_token(self):
        token = extract_token(Clipboard.paste())
        if not token:
            return self.log("[!] No token in the clipboard. In Cookie-Editor, copy the value of new_bbs_serviceToken "
                            "(or the exported cookie text), then tap 'Paste token'.")
        self.add_account(f"Account {len(self.data['accounts']) + 1}", token, 66.0)

    def token_help(self):
        body = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        steps = ("1. Install Firefox (or Kiwi Browser) and add the 'Cookie-Editor' extension.\n\n"
                 "2. Tap 'Open login page', choose Firefox or Kiwi, and sign in to the Xiaomi Community.\n\n"
                 "3. Open Cookie-Editor, tap the cookie new_bbs_serviceToken and copy its value "
                 "(Export also works).\n\n"
                 "4. Come back here and tap 'Paste token'.")
        label = Label(text=steps, font_size=dp(13), color=hexc(TEXT), halign="left", valign="top")
        label.bind(size=lambda i, v: setattr(i, "text_size", v))
        body.add_widget(label)
        pop = Popup(title="Get the token with Firefox / Kiwi", content=body, size_hint=(0.94, None), height=dp(470))
        body.add_widget(btn("Open login page", lambda: open_url(LOGIN_URL), primary=True))
        body.add_widget(btn("Paste token", lambda: (pop.dismiss(), self.paste_token())))
        body.add_widget(btn("Close", pop.dismiss))
        pop.open()

    # ---- developer ----
    def build_dev(self):
        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        root.add_widget(lbl("Developer", 22, TEXT, size_hint_y=None, height=dp(34), halign="left", bold=True))
        root.add_widget(lbl(f"Author   : {AUTHOR_NAME}\nGitHub   : {AUTHOR_GITHUB}", 14, MUTED, size_hint_y=None,
                            height=dp(48), halign="left"))
        for w in root.children:
            w.bind(size=lambda i, v: setattr(i, "text_size", (v[0], None)))
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        row.add_widget(btn("Open GitHub", lambda: open_url(AUTHOR_URL, chooser=False), primary=True))
        row.add_widget(btn("Copy diagnostics", self.copy_dev))
        root.add_widget(row)
        sv = ScrollView()
        self.dev_lbl = Label(text="", font_name="RobotoMono-Regular", font_size=dp(11), color=hexc(TEXT),
                             size_hint_y=None, halign="left", valign="top")
        self.dev_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w - dp(8), None)),
                          texture_size=lambda i, v: setattr(i, "height", v[1] + dp(12)))
        sv.add_widget(self.dev_lbl)
        root.add_widget(sv)
        return root

    def dev_text(self):
        try:
            Build, VER = autoclass("android.os.Build"), autoclass("android.os.Build$VERSION")
            android = f"{VER.RELEASE} (API {VER.SDK_INT})"
            device = f"{Build.MANUFACTURER} {Build.MODEL}"
        except Exception:
            android = device = "n/a (not running on Android)"
        accs = self.data["accounts"]
        return "\n".join([
            "APPLICATION", "  Xiaomi Bootloader Unlock Quota Helper (Android)", "  Version     : 1.0.0",
            f"  Author      : {AUTHOR_NAME}", f"  GitHub      : {AUTHOR_GITHUB}", "",
            "RUNTIME", f"  Python      : {sys.version.split()[0]}", f"  Android     : {android}",
            f"  Device      : {device}", "",
            "APP STATE", f"  Accounts    : {len(accs)} ({sum(1 for a in accs if a.get('enabled', True))} enabled)",
            f"  Clock synced: {self.clock.synced}", f"  Clock offset: {self.clock.offset * 1000:+.1f} ms", "",
            "NETWORK", f"  Status API  : {STATUS_URL}", f"  Apply API   : {APPLY_URL}",
            "  Timezone    : Beijing / UTC+08:00", "",
            "NTP SERVERS", *[f"  - {x}" for x in NTP_SERVERS]])

    def update_dev(self):
        if getattr(self, "dev_lbl", None) is not None:
            self.dev_lbl.text = self.dev_text()

    def copy_dev(self):
        Clipboard.copy(self.dev_text())
        self.log("Developer diagnostics copied to the clipboard.")

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
        self.update_dev()
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
