"""Xiaomi Bootloader Unlock Quota Helper - Android (KivyMD) app.

Material Design 3 UI (KivyMD 1.2.0) with light/dark themes. Reuses core.py
(copied in at build time) for the NTP clock and the Xiaomi API client.
"""
import json, os, random, re, statistics, sys, threading, time
from datetime import datetime

from kivy.clock import Clock as KClock, mainthread
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.utils import platform

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton, MDIconButton, MDRaisedButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDLabel
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.selectioncontrol import MDCheckbox, MDSwitch
from kivymd.uix.snackbar import MDSnackbar
from kivymd.uix.tab import MDTabsBase
from kivymd.uix.textfield import MDTextField

from core import (APPLY_URL, AUTHOR_GITHUB, AUTHOR_NAME, AUTHOR_URL, BJ, DEFAULT_SETTINGS, NTP_SERVERS, STATE_TEXT,
                  STATUS_URL, Api, Clock as Ntp, fmt_bj, mask, next_midnight_epoch)

ANDROID = platform == "android"
if ANDROID:
    from android.runnable import run_on_ui_thread
    from jnius import PythonJavaClass, autoclass, cast, java_method
    PA = autoclass("org.kivy.android.PythonActivity")
else:
    run_on_ui_thread = lambda f: f

if ANDROID:
    _JString = autoclass("java.lang.String")

    def jstr(s):
        """Python str -> Java CharSequence (pyjnius cannot pass a bare str where
        Java expects CharSequence, e.g. TextView/Button.setText)."""
        return cast("java.lang.CharSequence", _JString(s))
else:
    def jstr(s):
        return s

LOGIN_URL = "https://new.c.mi.com/global"

# Fixed status colors (readable on both light and dark surfaces).
OK, WARN, BAD = "#5ee6a8", "#ffb454", "#ff6b81"


def kind_color(text):
    t = (text or "").lower()
    if any(w in t for w in ("expired", "error")):
        return BAD
    if any(w in t for w in ("blocked", "limit", "under 30", "finished", "unknown")):
        return WARN
    if any(w in t for w in ("approved", "ready")):
        return OK
    return None  # None = theme default


def kind_icon(text):
    t = (text or "").lower()
    if any(w in t for w in ("expired", "error")):
        return "alert-circle-outline"
    if any(w in t for w in ("blocked", "limit", "under 30", "finished", "unknown")):
        return "clock-outline"
    if any(w in t for w in ("approved", "ready")):
        return "check-decagram"
    return "information"


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
            Intent, Uri = autoclass("android.content.Intent"), autoclass("android.net.Uri")
            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            if chooser:
                intent = Intent.createChooser(intent, jstr("Open login page with"))
            PA.mActivity.startActivity(intent)
        else:
            import webbrowser
            webbrowser.open(url)
        return True
    except Exception:
        return False


def md_field(hint="", text="", **kw):
    return MDTextField(hint_text=hint, text=text, size_hint_y=None, height=dp(56), **kw)


def snack(text):
    """MD3 snackbar feedback (silently no-ops before the UI is up)."""
    try:
        app = MDApp.get_running_app()
        if app is None:
            return
        MDSnackbar(MDLabel(text=text, theme_text_color="Custom",
                           text_color=app.theme_cls.text_color)).open()
    except Exception:
        pass


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

    class _PageLoadClient(PythonJavaClass):
        """WebViewClient that inspects Android's cookie store after every page load,
        so the new_bbs_serviceToken is picked up automatically once the user logs in."""
        __javaclass__ = "android/webkit/WebViewClient"
        __javacontext__ = "app"

        def __init__(self, on_page):
            super().__init__()
            self.on_page = on_page

        @java_method("(Landroid/webkit/WebView;Ljava/lang/String;)V")
        def onPageFinished(self, view, url):
            try:
                self.on_page(url)
            except Exception:
                pass


class WebLogin:
    """In-app browser. Shows a WebView over the app; after logging in, reads new_bbs_serviceToken from Android's
    cookie store (HttpOnly cookies included). Every step reports problems through on_error instead of failing silently."""
    UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/124.0.0.0 Mobile Safari/537.36")

    def __init__(self, on_token, on_error):
        self.on_token, self.on_error, self.layout, self.wv = on_token, on_error, None, None
        self._clicks, self._got = [], False

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
            self._client = _PageLoadClient(self._on_page)
            self.wv.setWebViewClient(self._client)
            cm = CM.getInstance()
            cm.setAcceptCookie(True)
            cm.setAcceptThirdPartyCookies(self.wv, True)
            self.wv.loadUrl(LOGIN_URL)

            bar = LL(act)
            bar.setOrientation(0)
            for text, cb in (("Get token", self.finish), ("Close", self.close)):
                b = Button_(act)
                b.setText(jstr(text))
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

    def _on_page(self, _url):
        """Called by onPageFinished (on the UI thread) after each page load:
        capture the token automatically as soon as login produces it."""
        if self.layout is None or self._got:
            return
        try:
            if self._read_token():
                self._got = True
                self.finish()
        except Exception:
            pass

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
class TabPage(MDScrollView, MDTabsBase):
    """Scrollable content container for one MDTabs page. Must be defined in
    Python: the KV '<X@A+B>' mixin syntax needs both names in the Kivy Factory,
    and MDTabsBase is a plain mixin, not a Factory-registered widget."""
    pass


KV = """
MDBoxLayout:
    orientation: "vertical"
    md_bg_color: app.theme_cls.bg_normal

    MDTopAppBar:
        title: "Xiaomi Quota Helper"
        elevation: 0
        left_action_items: [["shield-key-outline", lambda *a: None]]
        right_action_items: app.bar_actions

    MDTabs:
        id: tabs
        tab_bar_height: dp(56)
        anim_duration: 120
        background_color: app.theme_cls.bg_normal

        TabPage:
            title: "Home"

        TabPage:
            title: "Accounts"

        TabPage:
            title: "Log"

        TabPage:
            title: "About"
"""


class QuotaApp(MDApp):
    title = "Xiaomi Quota Helper"

    # ---------- theme ----------
    def tb(self, widget, attr, getter):
        """Theme-bind a custom attribute so it follows the light/dark toggle."""
        setattr(widget, attr, getter())
        self._theme_bound.append((widget, attr, getter))

    def _apply_theme(self, *_a):
        for w, attr, getter in self._theme_bound:
            try:
                setattr(w, attr, getter())
            except Exception:
                pass  # widget may have been removed by refresh()

    def flip_theme(self, *_a):
        self.set_theme("Light" if self.theme_cls.theme_style == "Dark" else "Dark")

    def set_theme(self, style):
        self.theme_cls.theme_style = style
        self.cfg.setdefault("settings", {})["theme"] = style.lower()
        self.save()

    # ---------- shared widgets ----------
    def headline(self, text):
        lbl = MDLabel(text=text, font_style="H6", bold=True, adaptive_height=True, theme_text_color="Custom")
        self.tb(lbl, "text_color", lambda: self.theme_cls.primary_color)
        return lbl

    def muted(self, text, style="Caption"):
        lbl = MDLabel(text=text, font_style=style, adaptive_height=True, theme_text_color="Custom")
        self.tb(lbl, "text_color", lambda: self.theme_cls.disabled_primary_color)
        return lbl

    def section(self, title, *children, spacing=dp(8)):
        card = MDCard(style="filled", radius=[dp(16)], adaptive_height=True,
                      padding=dp(12), orientation="vertical", spacing=spacing)
        self.tb(card, "md_bg_color", lambda: self.theme_cls.bg_light)
        card.add_widget(self.muted(title.upper(), style="Overline"))
        for w in children:
            card.add_widget(w)
        return card

    def raised(self, text, cb, icon=None, h=48):
        b = MDRaisedButton(text=text, size_hint=(1, None), height=dp(h))
        if icon:
            b.icon = icon
        b.bind(on_release=lambda *_: cb())
        return b

    def flat(self, text, cb, icon=None):
        b = MDFlatButton(text=text, size_hint=(1, None), height=dp(44))
        if icon:
            b.icon = icon
        b.bind(on_release=lambda *_: cb())
        return b

    # ---------- lifecycle ----------
    def build(self):
        self._theme_bound = []
        self._open_dialog = self._set_dialog = self._help_dialog = None
        self.web = None
        self.cfg_path = os.path.join(self.user_data_dir, "config.json")
        self.cfg = {}
        if os.path.exists(self.cfg_path):
            try:
                self.cfg.update(json.load(open(self.cfg_path, encoding="utf-8")))
            except Exception:
                pass
        self.cfg.setdefault("accounts", [])
        settings = {**DEFAULT_SETTINGS, **self.cfg.get("settings", {})}
        self.cfg["settings"] = settings
        for a in self.cfg["accounts"]:
            a.setdefault("id", str(random.randint(10**8, 10**9)))
            a.setdefault("enabled", True)
            a.setdefault("offset_ms", 0)

        self.theme_cls.primary_palette = "Indigo"
        self.theme_cls.accent_palette = "Amber"
        self.theme_cls.theme_style = "Dark" if settings.get("theme", "dark") == "dark" else "Light"
        self.theme_cls.fbind("theme_style", self._apply_theme)

        # Must exist before KV is parsed (the app bar references it).
        self.bar_actions = [["theme-light-dark", self.flip_theme], ["cog", lambda *a: self.show_settings()]]
        self.running, self.phase, self.target, self.clock = False, "Idle", None, Ntp()
        self.stop_ev, self.lines, self.status_icons = threading.Event(), [], {}

        root = Builder.load_string(KV)
        # Content goes into the carousel slides (tab_list holds the labels, not the slides).
        pages = (self.build_dash, self.build_accounts, self.build_log, self.build_about)
        for slide, builder in zip(root.ids.tabs.get_slides(), pages):
            slide.add_widget(builder())

        KClock.schedule_interval(self.tick, 0.1)
        Window.bind(on_keyboard=self._on_key)
        self.log("Welcome. Add an account, check its token, then press Start.")
        return root

    def _on_key(self, _w, key, *_a):
        # Android hardware back: close the web login first, then any open dialog, else leave the app.
        if key == 27:
            if self.web is not None and self.web.layout is not None:
                self.web.close()
                return True
            for d in (self._open_dialog, self._set_dialog, self._help_dialog):
                if d is not None:
                    try:
                        d.dismiss()
                    except Exception:
                        pass
                    return True
        return False

    def on_pause(self):
        return True  # keep running in the background

    def save(self):
        clean = [{k: v for k, v in a.items() if k != "status"} for a in self.cfg["accounts"]]
        try:
            with open(self.cfg_path, "w", encoding="utf-8") as f:
                json.dump({"accounts": clean, "settings": self.cfg["settings"]}, f, indent=2)
        except Exception as e:
            self.log(f"[!] Could not save config: {e}")

    # ---------- home ----------
    def build_dash(self):
        col = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=dp(10), spacing=dp(10))
        col.add_widget(self.headline("Dashboard"))

        self.l_count = MDLabel(text="--:--:--", font_style="H3", bold=True, adaptive_height=True,
                               halign="center", theme_text_color="Custom")
        self.l_target = MDLabel(text="", font_style="Caption", adaptive_height=True, halign="center",
                                theme_text_color="Custom")
        self.tb(self.l_count, "text_color", lambda: self.theme_cls.primary_color)
        self.tb(self.l_target, "text_color", lambda: self.theme_cls.disabled_primary_color)
        col.add_widget(self.section("Countdown", self.l_count, self.l_target, spacing=dp(2)))

        self.l_phase = MDLabel(text="Idle", font_style="Body2", adaptive_height=True, theme_text_color="Custom")
        self.tb(self.l_phase, "text_color", lambda: self.theme_cls.disabled_primary_color)
        self.l_stats = MDLabel(text="", font_style="Body2", adaptive_height=True)
        self.b_run = self.raised("Start", self.toggle_run, icon="play", h=52)
        col.add_widget(self.section("Run", self.l_phase, self.l_stats, self.b_run, spacing=dp(12)))

        s = self.cfg["settings"]
        self.chk = MDCheckbox(active=bool(s["allow_blocked"]), size_hint=(None, None), size=(dp(28), dp(28)))
        chk_row = MDBoxLayout(size_hint_y=None, height=dp(34), spacing=dp(12))
        chk_row.add_widget(self.chk)
        chk_row.add_widget(MDLabel(text="Also send for blocked / new accounts", font_style="Body2",
                                   adaptive_height=True))
        col.add_widget(self.section("Options", chk_row))

        self.in_int = md_field("Retry every (ms)", str(s["interval_ms"]), input_filter="int")
        self.in_win = md_field("Keep trying for (s)", str(s["window_s"]), input_filter="int")
        self.in_snd = md_field("Parallel senders (1-6)", str(s["senders"]), input_filter="int")
        col.add_widget(self.section("Retry settings", self.in_int, self.in_win, self.in_snd))

        self.b_check = self.flat("Check tokens", self.check_tokens, icon="check-decagram")
        self.b_meas = self.flat("Measure offsets", self.measure, icon="chart-line")
        self.b_batt = self.flat("Battery settings (allow background)", open_battery_settings, icon="battery-charging")
        col.add_widget(self.section("Maintenance", self.b_check, self.b_meas, self.b_batt, spacing=dp(4)))

        col.add_widget(self.muted("Keep the app open and the screen on until the run finishes."))
        return col

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
        accs = self.cfg["accounts"]
        self.l_count.text = f"{h:02d}:{m:02d}:{r % 60:04.1f}" if 0 < r < 10 else f"{h:02d}:{m:02d}:{s:02d}"
        self.l_target.text = f"Beijing {datetime.fromtimestamp(target, BJ):%a %d %b} 00:00:00"
        self.l_phase.text = self.phase if self.running else "Idle"
        self.l_stats.text = f"{sum(1 for a in accs if a.get('enabled', True))} of {len(accs)} accounts enabled"

    # ---------- accounts ----------
    def build_accounts(self):
        col = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=dp(10), spacing=dp(10))
        col.add_widget(self.headline("Accounts"))
        col.add_widget(self.raised("Add account", self.add_dialog, icon="plus", h=44))
        col.add_widget(self.flat("Log in (in-app browser)", self.web_login, icon="login"))
        col.add_widget(self.flat("Firefox / Kiwi + Cookie-Editor", self.token_help, icon="open-in-new"))
        col.add_widget(self.flat("Paste token from clipboard", self.paste_token, icon="clipboard-check-outline"))
        self.acc_col = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8))
        col.add_widget(self.acc_col)
        self.refresh()
        return col

    def refresh(self):
        if not hasattr(self, "acc_col"):
            return
        self.acc_col.clear_widgets()
        self.status_icons = {}
        if not self.cfg["accounts"]:
            self.acc_col.add_widget(self.muted("No accounts yet.\nTap 'Add account' or 'Log in (in-app browser)'."))
        for a in self.cfg["accounts"]:
            color = kind_color(a.get("status", "")) or self.theme_cls.primary_color
            ic = MDIconButton(icon=kind_icon(a.get("status", "")), size_hint=(None, None), size=(dp(40), dp(40)),
                              theme_icon_color="Custom", icon_color=color)
            self.status_icons[a["id"]] = ic

            sw = MDSwitch()
            sw.active = bool(a.get("enabled", True))  # set after init: ids.thumb only exists once the KV rule is applied
            sw.bind(active=lambda _w, v, a=a: self._set_enabled(a, v))
            top = MDBoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
            top.add_widget(sw)
            top.add_widget(MDLabel(text=f"{a['name']}  ·  {mask(a['token'])}", font_style="Body1",
                                   adaptive_height=True, shorten=True, shorten_from="right"))
            top.add_widget(ic)

            off = md_field("Offset ms", f"{a['offset_ms']:g}", input_filter="float",
                           size_hint_x=None, width=dp(150))
            off.bind(focus=lambda w, f, a=a: None if f else self._set_offset(a, w.text))
            ic_check = MDIconButton(icon="refresh", size_hint=(None, None), size=(dp(44), dp(44)))
            ic_check.bind(on_release=lambda *_a, a=a: self.check_one(a))
            ic_del = MDIconButton(icon="delete-outline", size_hint=(None, None), size=(dp(44), dp(44)),
                                  theme_icon_color="Custom", icon_color=BAD)
            ic_del.bind(on_release=lambda *_a, a=a: self.remove(a))
            row = MDBoxLayout(size_hint_y=None, height=dp(56), spacing=dp(4))
            row.add_widget(off)
            row.add_widget(MDBoxLayout())  # spacer
            row.add_widget(ic_check)
            row.add_widget(ic_del)

            card = MDCard(style="filled", radius=[dp(16)], adaptive_height=True,
                          orientation="vertical", padding=dp(10), spacing=dp(4), ripple_behavior=True)
            self.tb(card, "md_bg_color", lambda: self.theme_cls.bg_light)
            card.add_widget(top)
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
        self.cfg["accounts"].remove(a)
        self.save()
        self.refresh()
        snack(f"Removed '{a['name']}'.")

    def add_dialog(self, token=""):
        name = md_field("Name", "Account")
        tok = md_field("new_bbs_serviceToken", token)
        off = md_field("Offset ms", "66", input_filter="float")
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(4), padding=dp(4))
        for w in (name, tok, off):
            content.add_widget(w)

        def save(*_a):
            t = extract_token(tok.text) or tok.text.strip().strip('"')
            if t:
                self.add_account(name.text.strip() or "Account", t, float(off.text or 0))
            else:
                snack("Paste a token first.")
            self._open_dialog.dismiss()
            self._open_dialog = None

        self._open_dialog = MDDialog(title="Add account", type="custom", content_cls=content,
                                     buttons=[MDFlatButton(text="SAVE"), MDFlatButton(text="CLOSE",
                                            on_release=lambda *_: self._close_dialog("open"))])
        self._open_dialog.buttons[0].bind(on_release=save)
        self._open_dialog.open()

    def _close_dialog(self, which):
        d = getattr(self, f"_{which}_dialog", None)
        if d is not None:
            try:
                d.dismiss()
            except Exception:
                pass
        setattr(self, f"_{which}_dialog", None)

    def add_account(self, name, token, offset=0.0):
        if any(a["token"] == token for a in self.cfg["accounts"]):
            return self.log("That token is already saved.")
        self.cfg["accounts"].append({"id": str(random.randint(10**8, 10**9)), "name": name, "token": token,
                                     "offset_ms": offset, "enabled": True})
        self.save()
        self.refresh()
        self.log(f"Token added for '{name}'.")
        snack(f"Token added for '{name}'.")

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
        self.add_account(f"Account {len(self.cfg['accounts']) + 1}", token, 66.0)

    def paste_token(self):
        token = extract_token(Clipboard.paste())
        if not token:
            return snack("No token in the clipboard. Copy new_bbs_serviceToken first.")
        self.add_account(f"Account {len(self.cfg['accounts']) + 1}", token, 66.0)

    def token_help(self):
        steps = ("1. Install Firefox (or Kiwi Browser) and add the 'Cookie-Editor' extension.\n\n"
                 "2. Tap 'Open login page', choose Firefox or Kiwi, and sign in to the Xiaomi Community.\n\n"
                 "3. Open Cookie-Editor, tap the cookie new_bbs_serviceToken and copy its value "
                 "(Export also works).\n\n"
                 "4. Come back here and tap 'Paste token from clipboard'.")
        content = MDBoxLayout(adaptive_height=True, padding=dp(4))
        content.add_widget(MDLabel(text=steps, font_style="Body2", adaptive_height=True))
        self._help_dialog = MDDialog(title="Get the token with Firefox / Kiwi", type="custom", content_cls=content,
                                     buttons=[MDFlatButton(text="OPEN LOGIN PAGE",
                                            on_release=lambda *_: open_url(LOGIN_URL)),
                                              MDFlatButton(text="CLOSE",
                                            on_release=lambda *_: self._close_dialog("help"))])
        self._help_dialog.open()

    # ---------- log ----------
    def build_log(self):
        col = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=dp(10), spacing=dp(10))
        col.add_widget(self.headline("Log"))
        card = MDCard(style="filled", radius=[dp(16)], padding=dp(10))
        self.tb(card, "md_bg_color", lambda: self.theme_cls.bg_light)
        self.log_lbl = MDLabel(text="", font_style="Caption", size_hint_y=None, halign="left", valign="top")
        self.log_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w - dp(12), None)),
                          texture_size=lambda i, s: setattr(i, "height", s[1]))
        card.add_widget(self.log_lbl)
        col.add_widget(card)
        return col

    @mainthread
    def log(self, msg, name=None):
        self.lines.append(f"{datetime.now():%H:%M:%S.%f}"[:-3] + (f" {name}" if name else "") + f"  {msg}")
        self.lines = self.lines[-300:]
        if getattr(self, "log_lbl", None):
            self.log_lbl.text = "\n".join(self.lines)

    @mainthread
    def set_status(self, acc_id, text):
        for a in self.cfg["accounts"]:
            if a["id"] == acc_id:
                a["status"] = text
        icon = self.status_icons.get(acc_id)
        if icon:
            icon.icon = kind_icon(text)
            icon.icon_color = kind_color(text) or self.theme_cls.primary_color

    # ---------- about / settings ----------
    def build_about(self):
        col = MDBoxLayout(orientation="vertical", adaptive_height=True, padding=dp(10), spacing=dp(10))
        col.add_widget(self.headline("About"))
        dev = MDLabel(text=self.dev_text(), font_style="Caption", size_hint_y=None, halign="left")
        dev.bind(width=lambda i, w: setattr(i, "text_size", (w - dp(8), None)),
                 texture_size=lambda i, s: setattr(i, "height", s[1]))
        col.add_widget(self.section("Diagnostics", dev, spacing=dp(6)))
        col.add_widget(self.raised("Open GitHub", lambda: open_url(AUTHOR_URL, chooser=False), icon="open-in-new", h=44))
        col.add_widget(self.flat("Copy diagnostics", self.copy_dev, icon="content-copy"))
        col.add_widget(self.muted(f"Author: {AUTHOR_NAME} · {AUTHOR_GITHUB}"))
        return col

    def dev_text(self):
        try:
            Build, VER = autoclass("android.os.Build"), autoclass("android.os.Build$VERSION")
            android = f"{VER.RELEASE} (API {VER.SDK_INT})"
            device = f"{Build.MANUFACTURER} {Build.MODEL}"
        except Exception:
            android = device = "n/a (not running on Android)"
        accs = self.cfg["accounts"]
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

    def copy_dev(self):
        Clipboard.copy(self.dev_text())
        snack("Diagnostics copied to the clipboard.")

    def show_settings(self):
        """Dark-theme switch, from the top app bar gear icon."""
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8), padding=dp(4))
        row = MDBoxLayout(size_hint_y=None, height=dp(40), spacing=dp(12))
        sw = MDSwitch()
        sw.active = self.theme_cls.theme_style == "Dark"  # set after init: ids.thumb only exists once the KV rule is applied
        sw.bind(active=lambda _w, v: self.set_theme("Dark" if v else "Light"))
        row.add_widget(sw)
        row.add_widget(MDLabel(text="Dark theme", font_style="Body1", adaptive_height=True))
        box.add_widget(row)
        self._set_dialog = MDDialog(title="Settings", type="custom", content_cls=box,
                                    buttons=[MDFlatButton(text="CLOSE",
                                            on_release=lambda *_: self._close_dialog("set"))])
        self._set_dialog.open()

    # ---------- check / measure ----------
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
        accs = list(self.cfg["accounts"])
        threading.Thread(target=lambda: [self._check(a) for a in accs], daemon=True).start()

    def measure(self):
        accs = list(self.cfg["accounts"])
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

    # ---------- run ----------
    def start(self):
        accs = [a for a in self.cfg["accounts"] if a.get("enabled", True)]
        if not accs:
            return snack("No enabled accounts.")
        try:
            cfg = {"allow_blocked": bool(self.chk.active), "interval_ms": max(20, int(float(self.in_int.text))),
                   "window_s": max(1, int(float(self.in_win.text))),
                   "senders": min(6, max(1, int(float(self.in_snd.text))))}
        except ValueError:
            return snack("Settings must be numbers.")
        self.cfg["settings"].update(cfg)
        self.save()
        self.stop_ev, self.running, self.target, self.phase = threading.Event(), True, None, "Syncing clock"
        self.b_run.text = "Stop"
        self.b_run.icon = "stop"
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
        self.b_run.icon = "play"
        keep_screen_on(False)
        wake_lock(False)
        self.log("Run finished.")
        snack("Run finished.")

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
