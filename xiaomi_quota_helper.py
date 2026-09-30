"""
Xiaomi Bootloader Unlock Quota Helper
Author: Abdul Haseeb  -  github.com/haseebno1

One window for everything: manage accounts, check tokens, measure offsets,
and send the request at Beijing midnight with NTP-synced timing.
"""
import json, os, platform, queue, random, statistics, sys, threading, time, webbrowser
from datetime import datetime, timedelta, timezone

from core import *
from core import ensure_package

ctk = ensure_package("customtkinter")
import tkinter as tk
from tkinter import messagebox

# --------------------------------------------------------------------------- #
# Design tokens
# --------------------------------------------------------------------------- #
C = {
    "bg": "#0b0e1a", "panel": "#121729", "card": "#182038", "input": "#0f1424",
    "border": "#252e4a", "text": "#e8ebf7", "muted": "#8088a6",
    "accent": "#7b8cff", "accent_hover": "#97a4ff", "time": "#ffb454",
    "danger": "#ff6b81", "danger_hover": "#ff8597", "ok": "#5ee6a8",
}

BADGES = {  # (background, text)
    "ok": ("#0f3326", "#5ee6a8"),
    "warn": ("#3b2c12", "#ffb454"),
    "bad": ("#3d1822", "#ff6b81"),
    "info": ("#1d2550", "#9aa8ff"),
    "idle": ("#1b2240", "#8088a6"),
}

WIDE_AT = 980      # dashboard switches to two columns at this width
CARD_WIDE_AT = 720  # account card switches to a single row at this width


_OS = sys.platform
FONTS = {  # (ui, mono, display) per platform; Tk falls back to its default if a font is missing
    "win32": ("Segoe UI", "Consolas", "Bahnschrift"),
    "darwin": ("Helvetica Neue", "Menlo", "Helvetica Neue"),
}.get(_OS, ("DejaVu Sans", "DejaVu Sans Mono", "DejaVu Sans"))


def F(size=13, bold=False, mono=False, display=False, underline=False):
    family = FONTS[1] if mono else (FONTS[2] if display else FONTS[0])
    return ctk.CTkFont(family=family, size=size, weight="bold" if bold else "normal", underline=underline)


def status_kind(text):
    t = (text or "").lower()
    if not t:
        return "idle"
    if any(w in t for w in ("expired", "error", "network")):
        return "bad"
    if any(w in t for w in ("blocked", "limit", "under 30", "finished", "skipped", "unknown")):
        return "warn"
    if any(w in t for w in ("approved", "ready")):
        return "ok"
    return "info"


def classify_log(msg):
    m = msg.lower()
    if any(w in m for w in ("[!]", "error", "failed", "expired", "could not")):
        return "bad"
    if "approved" in m or "token added" in m or "synced" in m or m.startswith("found "):
        return "ok"
    if any(w in m for w in ("rejected", "skipped", "limit", "blocked", "stopp", "not approved", "already in")):
        return "warn"
    return "plain"


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #
def make_button(master, text, command, kind="secondary", width=None, height=34):
    styles = {
        "primary": dict(fg_color=C["accent"], hover_color=C["accent_hover"], text_color="#0b0e1a"),
        "secondary": dict(fg_color=C["card"], hover_color=C["border"], text_color=C["text"],
                          border_width=1, border_color=C["border"]),
        "ghost": dict(fg_color="transparent", hover_color=C["card"], text_color=C["muted"]),
        "danger": dict(fg_color=C["danger"], hover_color=C["danger_hover"], text_color="#0b0e1a"),
    }
    kw = dict(text=text, command=command, height=height, corner_radius=10,
              font=F(13, bold=(kind in ("primary", "danger"))))
    if width:
        kw["width"] = width
    kw.update(styles[kind])
    return ctk.CTkButton(master, **kw)


class Flow(ctk.CTkFrame):
    """Children wrap onto new rows when the available width shrinks."""

    def __init__(self, master, gap=8):
        super().__init__(master, fg_color="transparent")
        self.items, self.gap, self.cols = [], gap, 0
        self.bind("<Configure>", self._arrange)

    def add(self, widget):
        self.items.append(widget)
        self.cols = 0
        self.after(10, self._arrange)
        return widget

    def _arrange(self, _e=None):
        w = self.winfo_width()
        if w <= 1 or not self.items:
            return
        cell = max(i.winfo_reqwidth() for i in self.items) + self.gap
        cols = max(1, min(len(self.items), w // cell))
        if cols == self.cols:
            return
        self.cols = cols
        for i, it in enumerate(self.items):
            it.grid(row=i // cols, column=i % cols, padx=(0, self.gap), pady=(0, 6), sticky="w")


class AccountCard(ctk.CTkFrame):
    def __init__(self, master, app, acc):
        super().__init__(master, fg_color=C["card"], corner_radius=12, border_width=1, border_color=C["border"])
        self.app, self.acc, self.mode, self.maxlen = app, acc, None, 24

        self.switch = ctk.CTkSwitch(self, text="", width=46, command=self._toggle,
                                    progress_color=C["accent"], button_color=C["text"],
                                    button_hover_color="#ffffff", fg_color=C["border"])
        if acc.get("enabled", True):
            self.switch.select()

        self.info = ctk.CTkFrame(self, fg_color="transparent")
        self.lbl_name = ctk.CTkLabel(self.info, text=acc["name"], font=F(15, bold=True),
                                     text_color=C["text"], anchor="w")
        self.lbl_name.pack(anchor="w")
        self.lbl_token = ctk.CTkLabel(self.info, text=mask(acc["token"]), font=F(12, mono=True),
                                      text_color=C["muted"], anchor="w")
        self.lbl_token.pack(anchor="w")

        self.off = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.off, text="Offset", font=F(12), text_color=C["muted"]).pack(side="left", padx=(0, 6))
        self.ent = ctk.CTkEntry(self.off, width=62, height=30, justify="center", fg_color=C["input"],
                                border_color=C["border"], text_color=C["text"], font=F(13))
        self.ent.insert(0, f'{acc.get("offset_ms", 0):g}')
        self.ent.pack(side="left")
        self.ent.bind("<Return>", lambda e: self._commit_offset())
        self.ent.bind("<FocusOut>", lambda e: self._commit_offset())
        ctk.CTkLabel(self.off, text="ms", font=F(12), text_color=C["muted"]).pack(side="left", padx=(6, 0))

        self.badge = ctk.CTkLabel(self, text="", width=168, height=28, corner_radius=8, font=F(12, bold=True))

        self.btns = ctk.CTkFrame(self, fg_color="transparent")
        for text, cmd, w in (("Check", lambda: app.check_one(acc), 52), ("Copy", lambda: app.copy_token(acc), 50),
                             ("Edit", lambda: app.edit_account(acc), 44), ("Remove", lambda: app.remove_account(acc), 62)):
            make_button(self.btns, text, cmd, "ghost", width=w, height=28).pack(side="left", padx=1)

        self.lbl_name.bind("<Double-1>", lambda e: app.edit_account(acc))
        self.bind("<Configure>", self._on_cfg)
        self.layout("wide")

    def _on_cfg(self, e):
        mode = "wide" if e.width >= CARD_WIDE_AT else "narrow"
        if mode != self.mode:
            self.layout(mode)

    def layout(self, mode):
        self.mode = mode
        for w in (self.switch, self.info, self.off, self.badge, self.btns):
            w.grid_forget()
        for c in range(6):
            self.grid_columnconfigure(c, weight=0)
        self.grid_columnconfigure(1, weight=1)
        if mode == "wide":
            self.maxlen = 24
            self.badge.configure(width=168)
            self.switch.grid(row=0, column=0, padx=(14, 4), pady=12)
            self.info.grid(row=0, column=1, sticky="w", padx=6, pady=10)
            self.off.grid(row=0, column=2, padx=8)
            self.badge.grid(row=0, column=3, padx=8)
            self.btns.grid(row=0, column=4, padx=(0, 8))
        else:
            self.maxlen = 18
            self.badge.configure(width=130)
            self.switch.grid(row=0, column=0, padx=(14, 4), pady=(12, 4))
            self.info.grid(row=0, column=1, sticky="w", padx=6, pady=(10, 0))
            self.badge.grid(row=0, column=2, padx=(4, 12), pady=(10, 0))
            self.off.grid(row=1, column=0, columnspan=3, sticky="w", padx=14, pady=(6, 0))
            self.btns.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 10))
        self.set_status(self.acc.get("status", ""))

    def _toggle(self):
        self.acc["enabled"] = bool(self.switch.get())
        self.app.save_config()
        self.app.update_overview()

    def _commit_offset(self):
        try:
            value = float(self.ent.get().strip())
        except ValueError:
            value = self.acc.get("offset_ms", 0)
        if value != self.acc.get("offset_ms"):
            self.acc["offset_ms"] = value
            self.app.save_config()
        self.set_offset(value)

    def set_offset(self, value):
        self.ent.delete(0, "end")
        self.ent.insert(0, f"{value:g}")

    def set_status(self, text):
        bg, fg = BADGES[status_kind(text)]
        label = text or "Not checked"
        if len(label) > self.maxlen:
            label = label[:self.maxlen - 1] + "…"
        self.badge.configure(text=label, fg_color=bg, text_color=fg)


class Dialog(ctk.CTkToplevel):
    """Base for dialogs: centred on the parent and clamped to the screen."""

    def __init__(self, parent, title, w, h):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=C["panel"])
        w = min(w, max(340, parent.winfo_width() - 30))
        h = min(h, max(300, self.winfo_screenheight() - 120))
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
        self.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")
        self.transient(parent)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass


class ImportDialog(Dialog):
    def __init__(self, parent, options):
        super().__init__(parent, "Import account", 420, 150 + 74 * len(options))
        self.parent = parent
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22, pady=20)
        ctk.CTkLabel(body, text="Import account", font=F(18, bold=True), text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(body, text="Choose where to read the token from.", font=F(12),
                     text_color=C["muted"]).pack(anchor="w", pady=(2, 12))
        for title, sub, fn in options:
            ctk.CTkButton(body, text=f"{title}\n{sub}", height=60, corner_radius=10, anchor="w",
                          font=F(13, bold=True), fg_color=C["card"], hover_color=C["border"],
                          text_color=C["text"], border_width=1, border_color=C["border"],
                          command=lambda f=fn: self._pick(f)).pack(fill="x", pady=(0, 8))
        make_button(body, "Cancel", self.destroy, "ghost", height=32).pack(anchor="e", pady=(4, 0))

    def _pick(self, fn):
        self.destroy()
        self.parent.after(60, fn)


class AccountDialog(Dialog):
    def __init__(self, parent, title, acc=None):
        super().__init__(parent, title, 520, 480)
        self.result = None
        acc = acc or {}
        self.var_show = tk.BooleanVar(value=False)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=22)
        ctk.CTkLabel(body, text=title, font=F(18, bold=True), text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(body, text="Paste the new_bbs_serviceToken cookie value from your browser\n"
                                "(DevTools, Application, Cookies).",
                     font=F(12), text_color=C["muted"], justify="left").pack(anchor="w", pady=(2, 14))

        def field(label, value="", show=None):
            ctk.CTkLabel(body, text=label, font=F(12), text_color=C["muted"]).pack(anchor="w")
            e = ctk.CTkEntry(body, height=36, fg_color=C["input"], border_color=C["border"],
                             text_color=C["text"], font=F(13), show=show)
            e.insert(0, value)
            e.pack(fill="x", pady=(2, 10))
            return e

        self.e_name = field("Name", acc.get("name", ""))
        self.e_token = field("Token", acc.get("token", ""), show="•")
        ctk.CTkCheckBox(body, text="Show token", variable=self.var_show, command=self._toggle_show,
                        font=F(12), text_color=C["muted"], fg_color=C["accent"], hover_color=C["accent_hover"],
                        checkbox_width=18, checkbox_height=18).pack(anchor="w", pady=(0, 10))
        self.e_offset = field("Offset in milliseconds", f'{acc.get("offset_ms", 0):g}')

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", pady=(6, 0), side="bottom")
        make_button(btns, "Save", self._ok, "primary", width=110, height=36).pack(side="right")
        make_button(btns, "Cancel", self.destroy, "secondary", width=90, height=36).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._ok())
        self.after(200, self.e_name.focus_force)
        self.wait_window()

    def _toggle_show(self):
        self.e_token.configure(show="" if self.var_show.get() else "•")

    def _ok(self):
        token = self.e_token.get().strip().strip('"').strip()
        if not token:
            messagebox.showwarning("Token needed", "Paste the new_bbs_serviceToken value to continue.", parent=self)
            return
        try:
            offset = float(self.e_offset.get().strip() or 0)
        except ValueError:
            messagebox.showwarning("Offset must be a number", "Enter the offset in milliseconds, for example 67.", parent=self)
            return
        self.result = {"name": self.e_name.get().strip() or "Account", "token": token, "offset_ms": offset}
        self.destroy()


# --------------------------------------------------------------------------- #
# Main app
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root):
        self.root = root
        root.title(f"Xiaomi Bootloader Unlock Quota Helper - by {AUTHOR_NAME}")
        root.geometry("1100x820")
        root.minsize(520, 560)
        root.configure(fg_color=C["bg"])

        self.ui_queue = queue.Queue()
        self.accounts = []
        self.cards = {}
        self.mini = {}
        self.settings = dict(DEFAULT_SETTINGS)
        self.stop_event = threading.Event()
        self.workers = []
        self.phase = "idle"
        self.clock = Clock()
        self.target = None
        self.run_cfg = dict(DEFAULT_SETTINGS)
        self._label_cache = {}
        self.dash_mode = None
        self._resize_job = None
        self.check_btns, self.measure_btns = [], []

        self.load_config()
        self.var_allow = tk.BooleanVar(value=bool(self.settings["allow_blocked"]))
        self.var_interval = tk.StringVar(value=str(self.settings["interval_ms"]))
        self.var_window = tk.StringVar(value=str(self.settings["window_s"]))
        self.var_search = tk.StringVar()

        self.build_ui()
        self.refresh_accounts()
        self.show_page("Dashboard")
        self.log("Welcome to Xiaomi Bootloader Unlock Quota Helper. Add an account, check its token, then press Start.")
        root.bind("<Control-n>", lambda e: self.add_account())
        root.bind("<Configure>", self._on_root_cfg)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.pump)

    # ---------------- config ----------------
    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.accounts = data.get("accounts", [])
            self.settings.update(data.get("settings", {}))
            for a in self.accounts:
                a.setdefault("id", f"{int(time.time() * 1000)}{random.randint(0, 9999)}")
                a.setdefault("enabled", True)
                a.setdefault("offset_ms", 0)
                a.pop("status", None)
        except Exception as e:
            messagebox.showerror("Could not read config.json", str(e))

    def save_config(self):
        try:
            clean = [{k: v for k, v in a.items() if k != "status"} for a in self.accounts]
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"accounts": clean, "settings": self.settings}, f, indent=2)
        except Exception as e:
            self.log(f"[!] Could not save config: {e}")

    # ---------------- layout ----------------
    def card(self, master, radius=12):
        return ctk.CTkFrame(master, fg_color=C["panel"], corner_radius=radius,
                            border_width=1, border_color=C["border"])

    def build_ui(self):
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(root, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="X", width=44, height=44, corner_radius=12, fg_color=C["accent"],
                     text_color="#0b0e1a", font=F(22, bold=True, display=True)).grid(row=0, column=0)
        titles = ctk.CTkFrame(hdr, fg_color="transparent")
        titles.grid(row=0, column=1, sticky="w", padx=12)
        self.lbl_title = ctk.CTkLabel(titles, text="Xiaomi Bootloader Unlock Quota Helper", font=F(20, bold=True),
                                      text_color=C["text"], anchor="w", justify="left")
        self.lbl_title.pack(anchor="w")
        self.lbl_sub = ctk.CTkLabel(titles, text="Account manager • token checker • timing monitor • request dashboard",
                                    font=F(12), text_color=C["muted"], anchor="w", justify="left")
        self.lbl_sub.pack(anchor="w")

        nav = ctk.CTkFrame(root, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.nav = ctk.CTkSegmentedButton(
            nav, values=["Dashboard", "Accounts", "Developer"], command=self.show_page, height=34,
            selected_color=C["accent"], selected_hover_color=C["accent_hover"],
            unselected_color=C["panel"], unselected_hover_color=C["border"], fg_color=C["panel"],
            text_color=C["text"], font=F(13, bold=True))
        self.nav.pack(fill="x")

        self.host = ctk.CTkFrame(root, fg_color="transparent")
        self.host.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 14))
        self.host.grid_columnconfigure(0, weight=1)
        self.host.grid_rowconfigure(0, weight=1)

        self.pages = {}
        self.build_dashboard()
        self.build_accounts()
        self.build_developer()

    def show_page(self, name):
        for n, p in self.pages.items():
            if n == name:
                p.grid(row=0, column=0, sticky="nsew")
            else:
                p.grid_remove()
        self.nav.set(name)

    # ---------- dashboard ----------
    def build_dashboard(self):
        self.dash = ctk.CTkScrollableFrame(self.host, fg_color="transparent", corner_radius=0,
                                           scrollbar_button_color=C["border"],
                                           scrollbar_button_hover_color=C["muted"])
        self.pages["Dashboard"] = self.dash
        d = self.dash

        # hero: countdown
        self.b_hero = self.card(d, radius=18)
        cd = self.b_hero
        self.lbl_phase = ctk.CTkLabel(cd, text="Idle", height=26, corner_radius=8, width=150, font=F(12, bold=True))
        self.lbl_phase.pack(pady=(18, 0))
        self.lbl_count = ctk.CTkLabel(cd, text="--:--:--", font=F(54, bold=True, display=True), text_color=C["time"])
        self.lbl_count.pack(pady=(6, 0))
        self.lbl_target = ctk.CTkLabel(cd, text="", font=F(12), text_color=C["muted"], wraplength=320)
        self.lbl_target.pack()
        self.lbl_clock = ctk.CTkLabel(cd, text="", font=F(12), text_color=C["muted"], wraplength=320)
        self.lbl_clock.pack(pady=(2, 12))
        self.btn_run = ctk.CTkButton(cd, text="Start", height=48, corner_radius=12, font=F(16, bold=True),
                                     fg_color=C["accent"], hover_color=C["accent_hover"],
                                     text_color="#0b0e1a", command=self.toggle_run)
        self.btn_run.pack(fill="x", padx=20, pady=(0, 20))

        # stats + quick actions
        self.b_stats = self.card(d)
        st = self.b_stats
        ctk.CTkLabel(st, text="Overview", font=F(15, bold=True), text_color=C["text"]).pack(anchor="w", padx=18, pady=(14, 8))
        tiles = ctk.CTkFrame(st, fg_color="transparent")
        tiles.pack(fill="x", padx=12)
        self.tiles = {}
        for i, (key, cap, color) in enumerate((("total", "Accounts", C["text"]), ("ready", "Ready", C["ok"]),
                                               ("approved", "Approved", C["accent"]), ("attention", "Attention", C["time"]))):
            tiles.grid_columnconfigure(i, weight=1, uniform="t")
            t = ctk.CTkFrame(tiles, fg_color=C["card"], corner_radius=10)
            t.grid(row=0, column=i, sticky="ew", padx=4)
            v = ctk.CTkLabel(t, text="0", font=F(22, bold=True, display=True), text_color=color)
            v.pack(pady=(8, 0))
            ctk.CTkLabel(t, text=cap, font=F(11), text_color=C["muted"]).pack(pady=(0, 8))
            self.tiles[key] = v
        self.lbl_next = ctk.CTkLabel(st, text="", font=F(12), text_color=C["muted"], anchor="w",
                                     justify="left", wraplength=360)
        self.lbl_next.pack(anchor="w", padx=18, pady=(10, 4))
        act = Flow(st)
        act.pack(fill="x", padx=18, pady=(0, 14))
        self.check_btns.append(act.add(make_button(act, "Check tokens", self.check_tokens, "secondary", width=112)))
        self.measure_btns.append(act.add(make_button(act, "Measure offsets", self.measure_offsets, "secondary", width=130)))
        act.add(make_button(act, "Add account", self.add_account, "primary", width=112))

        # settings
        self.b_set = self.card(d)
        sc = self.b_set
        sc.grid_columnconfigure((0, 1), weight=1, uniform="s")
        ctk.CTkLabel(sc, text="Run settings", font=F(15, bold=True), text_color=C["text"]).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 6))
        ctk.CTkSwitch(sc, text="Also send for blocked or new accounts", variable=self.var_allow,
                      onvalue=True, offvalue=False, progress_color=C["accent"], button_color=C["text"],
                      fg_color=C["border"], font=F(12), text_color=C["text"]).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=18, pady=4)
        for col, (label, var) in enumerate([("Retry every (ms)", self.var_interval),
                                            ("Keep trying for (s)", self.var_window)]):
            ctk.CTkLabel(sc, text=label, font=F(12), text_color=C["muted"]).grid(
                row=2, column=col, sticky="w", padx=(18 if col == 0 else 8, 8), pady=(8, 0))
            ctk.CTkEntry(sc, textvariable=var, height=32, fg_color=C["input"], border_color=C["border"],
                         text_color=C["text"], font=F(13)).grid(
                row=3, column=col, sticky="ew", padx=(18 if col == 0 else 8, 18 if col == 1 else 8))
        ctk.CTkLabel(sc, text="Parallel senders per account", font=F(12), text_color=C["muted"]).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=18, pady=(10, 0))
        self.seg_senders = ctk.CTkSegmentedButton(
            sc, values=["1", "2", "3", "4", "5", "6"], selected_color=C["accent"],
            selected_hover_color=C["accent_hover"], unselected_color=C["input"],
            unselected_hover_color=C["border"], fg_color=C["input"], text_color=C["text"], font=F(12, bold=True))
        self.seg_senders.set(str(min(6, max(1, int(self.settings["senders"])))))
        self.seg_senders.grid(row=5, column=0, columnspan=2, sticky="ew", padx=18, pady=(4, 16))

        # account overview
        self.b_over = self.card(d)
        head = ctk.CTkFrame(self.b_over, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(head, text="Account status", font=F(15, bold=True), text_color=C["text"]).pack(side="left")
        make_button(head, "Manage", lambda: self.show_page("Accounts"), "ghost", width=70, height=26).pack(side="right")
        self.ov_frame = ctk.CTkFrame(self.b_over, fg_color="transparent")
        self.ov_frame.pack(fill="x", padx=12, pady=(0, 12))
        self.ov_frame.grid_columnconfigure(0, weight=1)

        # activity log
        self.b_log = self.card(d, radius=18)
        box = self.b_log
        box.grid_columnconfigure(0, weight=1)
        lh = ctk.CTkFrame(box, fg_color="transparent")
        lh.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 4))
        ctk.CTkLabel(lh, text="Activity", font=F(15, bold=True), text_color=C["text"]).pack(side="left")
        make_button(lh, "Clear", self.clear_log, "ghost", width=54, height=28).pack(side="right")
        make_button(lh, "Copy", self.copy_log, "ghost", width=54, height=28).pack(side="right")
        self.txt = ctk.CTkTextbox(box, height=200, fg_color=C["input"], text_color=C["text"], font=F(12, mono=True),
                                  corner_radius=10, border_width=1, border_color=C["border"], wrap="word")
        self.txt.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        for tag, col in (("ts", C["muted"]), ("name", C["accent"]), ("plain", C["text"]),
                         ("ok", C["ok"]), ("warn", C["time"]), ("bad", C["danger"])):
            self.txt.tag_config(tag, foreground=col)
        self.txt.configure(state="disabled")

    def layout_dashboard(self, mode):
        self.dash_mode = mode
        blocks = (self.b_hero, self.b_stats, self.b_set, self.b_over, self.b_log)
        for b in blocks:
            b.grid_forget()
        d = self.dash
        if mode == "wide":
            d.grid_columnconfigure(0, weight=1)
            d.grid_columnconfigure(1, weight=1)
            self.b_hero.grid(row=0, column=0, sticky="new", padx=(0, 8), pady=(0, 12))
            self.b_set.grid(row=1, column=0, sticky="new", padx=(0, 8), pady=(0, 12))
            self.b_stats.grid(row=0, column=1, sticky="new", padx=(8, 0), pady=(0, 12))
            self.b_over.grid(row=1, column=1, sticky="new", padx=(8, 0), pady=(0, 12))
            self.b_log.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        else:
            d.grid_columnconfigure(0, weight=1)
            d.grid_columnconfigure(1, weight=0)
            for i, b in enumerate(blocks):
                b.grid(row=i, column=0, sticky="ew", pady=(0, 12))

    def _on_root_cfg(self, e):
        if e.widget is not self.root:
            return
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(60, self._apply_resize)

    def _apply_resize(self):
        self._resize_job = None
        w = self.root.winfo_width()
        mode = "wide" if w >= WIDE_AT else "narrow"
        if mode != self.dash_mode:
            self.layout_dashboard(mode)
        wrap = max(200, w - 140)
        self.lbl_title.configure(wraplength=wrap)
        self.lbl_sub.configure(wraplength=wrap)
        inner = max(220, (w // 2 - 80) if mode == "wide" else w - 100)
        for lbl in (self.lbl_target, self.lbl_clock, self.lbl_next):
            lbl.configure(wraplength=inner)

    # ---------- accounts page ----------
    def build_accounts(self):
        page = self.card(self.host, radius=18)
        self.pages["Accounts"] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(3, weight=1)

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))
        top.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(top, text="Accounts", font=F(17, bold=True), text_color=C["text"]).grid(row=0, column=0)
        self.lbl_acc_count = ctk.CTkLabel(top, text="0", width=28, height=22, corner_radius=8,
                                          fg_color=C["card"], text_color=C["muted"], font=F(12, bold=True))
        self.lbl_acc_count.grid(row=0, column=1, padx=10)
        self.lbl_acc_sum = ctk.CTkLabel(top, text="", font=F(12), text_color=C["muted"], anchor="e")
        self.lbl_acc_sum.grid(row=0, column=2, sticky="e")

        bar = Flow(page)
        bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 4))
        bar.add(make_button(bar, "Add account", self.add_account, "primary", width=116))
        self.btn_import = bar.add(make_button(bar, "Import", self.show_import_menu, "secondary", width=90))
        self.check_btns.append(bar.add(make_button(bar, "Check tokens", self.check_tokens, "secondary", width=116)))
        self.measure_btns.append(bar.add(make_button(bar, "Measure offsets", self.measure_offsets, "secondary", width=130)))
        bar.add(make_button(bar, "Enable all", lambda: self.set_all_enabled(True), "secondary", width=96))
        bar.add(make_button(bar, "Disable all", lambda: self.set_all_enabled(False), "secondary", width=96))
        bar.add(make_button(bar, "Remove expired", self.remove_expired, "secondary", width=128))

        srch = ctk.CTkEntry(page, textvariable=self.var_search, height=34, fg_color=C["input"],
                            border_color=C["border"], text_color=C["text"], font=F(13),
                            placeholder_text="Search accounts by name")
        srch.grid(row=2, column=0, sticky="ew", padx=18, pady=(2, 10))
        self.var_search.trace_add("write", lambda *a: self.refresh_accounts())

        self.acc_frame = ctk.CTkScrollableFrame(page, fg_color="transparent", corner_radius=0,
                                                scrollbar_button_color=C["border"],
                                                scrollbar_button_hover_color=C["muted"])
        self.acc_frame.grid(row=3, column=0, sticky="nsew", padx=(14, 8), pady=(0, 14))
        self.acc_frame.grid_columnconfigure(0, weight=1)

    # ---------- developer page ----------
    def dev_lines(self):
        return [
            "APPLICATION", "  Xiaomi Bootloader Unlock Quota Helper",
            f"  Author      : {AUTHOR_NAME}", f"  GitHub      : {AUTHOR_GITHUB}", "",
            "RUNTIME", f"  Python      : {sys.version.split()[0]}",
            f"  OS          : {platform.system()} {platform.release()}",
            f"  Machine     : {platform.machine()}", f"  PID         : {os.getpid()}", "",
            "NETWORK", f"  Status API  : {STATUS_URL}", f"  Apply API   : {APPLY_URL}",
            "  Timezone    : Beijing / UTC+08:00", "",
            "NTP SERVERS", *[f"  • {s}" for s in NTP_SERVERS],
        ]

    def build_developer(self):
        page = self.card(self.host, radius=18)
        self.pages["Developer"] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(page, text="Developer", font=F(20, bold=True), text_color=C["text"]).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 0))
        ctk.CTkLabel(page, text="Diagnostics and technical information", font=F(12),
                     text_color=C["muted"]).grid(row=1, column=0, sticky="nw", padx=20, pady=(2, 10))
        info = ctk.CTkTextbox(page, fg_color=C["input"], border_width=1, border_color=C["border"],
                              text_color=C["text"], font=F(12, mono=True), corner_radius=10, wrap="none")
        info.grid(row=2, column=0, sticky="nsew", padx=18)
        info.insert("1.0", "\n".join(self.dev_lines()))
        info.configure(state="disabled")
        btns = Flow(page)
        btns.grid(row=3, column=0, sticky="ew", padx=18, pady=(12, 16))
        btns.add(make_button(btns, "Copy diagnostics", self.copy_diagnostics, "primary", width=140, height=36))
        btns.add(make_button(btns, "Open GitHub", lambda: webbrowser.open(AUTHOR_URL), "secondary", width=120, height=36))

    def copy_diagnostics(self):
        lines = [
            "Xiaomi Bootloader Unlock Quota Helper", f"Author: {AUTHOR_NAME}", f"GitHub: {AUTHOR_GITHUB}",
            f"Python: {sys.version.split()[0]}", f"OS: {platform.system()} {platform.release()}",
            f"Machine: {platform.machine()}", f"Accounts: {len(self.accounts)}",
            f"Enabled: {sum(1 for a in self.accounts if a.get('enabled', True))}",
            f"Clock synced: {self.clock.synced}", f"Clock offset: {self.clock.offset * 1000:+.1f} ms",
        ]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.log("Developer diagnostics copied to the clipboard.")

    # ---------------- accounts view ----------------
    def refresh_accounts(self):
        for w in self.acc_frame.winfo_children():
            w.destroy()
        self.cards = {}
        self.lbl_acc_count.configure(text=str(len(self.accounts)))
        q = self.var_search.get().strip().lower()
        shown = [a for a in self.accounts if q in a["name"].lower()]
        if not shown:
            empty = ctk.CTkFrame(self.acc_frame, fg_color="transparent")
            empty.grid(row=0, column=0, pady=40)
            msg = "No accounts yet" if not self.accounts else "No accounts match your search"
            ctk.CTkLabel(empty, text=msg, font=F(17, bold=True), text_color=C["text"]).pack()
            if not self.accounts:
                ctk.CTkLabel(empty, text="Paste a token, import token.txt, or read one from your browser.",
                             font=F(12), text_color=C["muted"], wraplength=320).pack(pady=(4, 14))
                make_button(empty, "Add account", self.add_account, "primary", width=140, height=38).pack()
        for i, a in enumerate(shown):
            card = AccountCard(self.acc_frame, self, a)
            card.grid(row=i, column=0, sticky="ew", pady=(0, 10))
            self.cards[a["id"]] = card
        self.build_mini()
        self.update_overview()

    def build_mini(self):
        for w in self.ov_frame.winfo_children():
            w.destroy()
        self.mini = {}
        if not self.accounts:
            ctk.CTkLabel(self.ov_frame, text="No accounts yet. Add one to get started.", font=F(12),
                         text_color=C["muted"]).grid(row=0, column=0, padx=6, pady=8, sticky="w")
            return
        for i, a in enumerate(self.accounts):
            row = ctk.CTkFrame(self.ov_frame, fg_color=C["card"], corner_radius=8)
            row.grid(row=i, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(0, weight=1)
            dim = "" if a.get("enabled", True) else "  (off)"
            ctk.CTkLabel(row, text=a["name"] + dim, font=F(13, bold=True), text_color=C["text"],
                         anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=8)
            b = ctk.CTkLabel(row, text="", width=150, height=24, corner_radius=8, font=F(11, bold=True))
            b.grid(row=0, column=1, padx=10)
            self.mini[a["id"]] = b
            self._paint_mini(a)

    def _paint_mini(self, acc):
        b = self.mini.get(acc["id"])
        if b:
            text = acc.get("status", "") or "Not checked"
            bg, fg = BADGES[status_kind(acc.get("status", ""))]
            b.configure(text=text if len(text) <= 22 else text[:21] + "…", fg_color=bg, text_color=fg)

    def update_overview(self):
        total = len(self.accounts)
        ready = appr = attn = 0
        for a in self.accounts:
            s = (a.get("status") or "").lower()
            if not s:
                continue
            if "not approved" in s:
                attn += 1
            elif "approved" in s:
                appr += 1
            elif s.startswith("ready"):
                ready += 1
            elif status_kind(s) in ("bad", "warn"):
                attn += 1
        for k, v in (("total", total), ("ready", ready), ("approved", appr), ("attention", attn)):
            self.tiles[k].configure(text=str(v))
        enabled = sum(1 for a in self.accounts if a.get("enabled", True))
        self.lbl_acc_sum.configure(text=f"{enabled} enabled • {ready} ready • {attn} need attention")

    def set_status(self, acc_id, text):
        for a in self.accounts:
            if a["id"] == acc_id:
                a["status"] = text
                self._paint_mini(a)
        card = self.cards.get(acc_id)
        if card:
            card.set_status(text)
        self.update_overview()

    def set_all_enabled(self, flag):
        for a in self.accounts:
            a["enabled"] = flag
        self.save_config()
        self.refresh_accounts()

    def remove_expired(self):
        dead = [a for a in self.accounts if "expired" in (a.get("status") or "").lower()]
        if not dead:
            messagebox.showinfo("Nothing to remove", "No account is marked as expired. Run Check tokens first.")
            return
        if messagebox.askyesno("Remove expired", f"Remove {len(dead)} account(s) with an expired cookie?"):
            self.accounts = [a for a in self.accounts if a not in dead]
            self.save_config()
            self.refresh_accounts()

    def copy_token(self, acc):
        self.root.clipboard_clear()
        self.root.clipboard_append(acc["token"])
        self.log("Token copied to the clipboard.", acc["name"])

    def show_import_menu(self):
        ImportDialog(self.root, [
            ("From token.txt and timeshift.txt", "Reads both files placed next to this script", self.import_token_txt),
            ("Read Firefox cookie", "Uses your logged-in Firefox profile", self.import_firefox),
            ("Log in with Chrome", "Opens a Chrome window to sign in", self.import_chrome),
        ])

    # ---------------- thread-safe UI plumbing ----------------
    def ui(self, fn):
        self.ui_queue.put(("call", fn))

    def log(self, msg, name=None):
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.ui_queue.put(("log", (stamp, name, msg)))

    def _append_log(self, stamp, name, msg):
        t = self.txt
        t.configure(state="normal")
        t.insert("end", stamp + "  ", "ts")
        if name:
            t.insert("end", f"{name}  ", "name")
        t.insert("end", msg + "\n", classify_log(msg))
        if int(t.index("end-1c").split(".")[0]) > 1500:
            t.delete("1.0", "301.0")
        t.see("end")
        t.configure(state="disabled")

    def clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.txt.get("1.0", "end"))
        self.log("Activity log copied to the clipboard.")

    def pump(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(*payload)
                else:
                    payload()
        except queue.Empty:
            pass
        self.update_countdown()
        self.root.after(100, self.pump)

    def _set(self, key, widget, **kw):
        sig = tuple(sorted(kw.items()))
        if self._label_cache.get(key) != sig:
            self._label_cache[key] = sig
            widget.configure(**kw)

    def update_countdown(self):
        phase = self.phase
        if phase == "idle" or not self.target:
            now = time.time()
            target, remaining = next_midnight_epoch(now), next_midnight_epoch(now) - now
        else:
            target, remaining = self.target, self.target - self.clock.now()

        if remaining > 0:
            h, rem = divmod(int(remaining), 3600)
            m, s = divmod(rem, 60)
            text = f"{h:02d}:{m:02d}:{remaining % 60:04.1f}" if remaining < 10 else f"{h:02d}:{m:02d}:{s:02d}"
        else:
            text = "00:00:00"
        self._set("count", self.lbl_count, text=text)

        if phase == "idle":
            chip, kind = "Idle", "idle"
        elif phase == "syncing":
            chip, kind = "Syncing clock", "warn"
        elif remaining > 0:
            chip, kind = "Waiting for target", "info"
        else:
            chip, kind = "Sending requests", "ok"
        bg, fg = BADGES[kind]
        self._set("phase", self.lbl_phase, text=chip, fg_color=bg, text_color=fg)

        local = datetime.fromtimestamp(target)
        self._set("target", self.lbl_target,
                  text=f"Beijing {datetime.fromtimestamp(target, BJ):%a %d %b} 00:00:00, your PC time {local:%H:%M}")
        if self.clock.synced and phase != "idle":
            side = "behind" if self.clock.offset > 0 else "ahead"
            clock_text = f"Clock synced, your PC is {abs(self.clock.offset) * 1000:.0f} ms {side}"
        elif phase == "idle":
            clock_text = "Clock not synced yet. It syncs when you press Start."
        else:
            clock_text = "Syncing with NTP servers…"
        self._set("clock", self.lbl_clock, text=clock_text)

        en = [a for a in self.accounts if a.get("enabled", True)]
        if en:
            big = max(en, key=lambda a: a.get("offset_ms", 0))
            first = target - big.get("offset_ms", 0) / 1000.0
            nxt = (f"{len(en)} of {len(self.accounts)} accounts will send. Earliest request fires at "
                   f"{fmt_bj(first)[:-3]} Beijing ({big['name']}, offset {big.get('offset_ms', 0):g} ms).")
        else:
            nxt = "No accounts enabled. Switch one on in the Accounts tab."
        self._set("next", self.lbl_next, text=nxt)

        if phase == "running" and self.workers and not any(t.is_alive() for t in self.workers):
            self.finish_run()

    # ---------------- account management ----------------
    def add_account(self):
        dlg = AccountDialog(self.root, "Add account")
        if dlg.result:
            self.add_account_data(dlg.result)

    def add_account_data(self, data):
        if any(a["token"] == data["token"] for a in self.accounts):
            self.log(f"Token for '{data['name']}' is already in the list, skipped.")
            return False
        data.update({"id": f"{int(time.time() * 1000)}{random.randint(0, 9999)}", "enabled": True, "status": ""})
        self.accounts.append(data)
        self.save_config()
        self.refresh_accounts()
        return True

    def edit_account(self, acc):
        dlg = AccountDialog(self.root, "Edit account", acc)
        if dlg.result:
            acc.update(dlg.result)
            self.save_config()
            self.refresh_accounts()

    def remove_account(self, acc):
        if messagebox.askyesno("Remove account", f"Remove '{acc['name']}' from the list?"):
            self.accounts.remove(acc)
            self.save_config()
            self.refresh_accounts()

    # ---------------- token sources ----------------
    def import_token_txt(self):
        path = os.path.join(BASE_DIR, "token.txt")
        if not os.path.exists(path):
            messagebox.showinfo("token.txt not found", "Put token.txt next to this script and try again.")
            return
        with open(path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.read().splitlines()]
        shifts = []
        shift_path = os.path.join(BASE_DIR, "timeshift.txt")
        if os.path.exists(shift_path):
            with open(shift_path, "r", encoding="utf-8") as f:
                shifts = [l.strip() for l in f.read().splitlines()]

        def offset_for(i):
            try:
                return float(shifts[i])
            except (IndexError, ValueError):
                return 0.0

        added = 0
        for i, label in [(0, "Firefox (imported)"), (1, "Chrome (imported)")]:
            if i < len(lines) and lines[i] and lines[i].upper() != "N/A":
                added += bool(self.add_account_data(
                    {"name": label, "token": lines[i], "offset_ms": offset_for(i)}))
        self.log(f"Imported {added} token(s) from token.txt.")

    def import_firefox(self):
        self.log("Reading Firefox cookies…")

        def job():
            try:
                bc3 = ensure_package("browser_cookie3", "browser-cookie3")
                cj = bc3.firefox(domain_name="mi.com")
                token = next((c.value for c in cj if "new_bbs_serviceToken" in c.name), None)
            except Exception as e:
                self.log(f"[!] Firefox read failed: {e}. Close Firefox and retry, or paste the token with Add account.")
                return
            if not token:
                self.log("[!] No token found in Firefox. Log in at c.mi.com/global first.")
                return
            self.ui(lambda: self.add_account_data({"name": "Firefox", "token": token, "offset_ms": 0})
                    and self.log("Firefox token added."))

        threading.Thread(target=job, daemon=True).start()

    def import_chrome(self):
        self.log("Opening Chrome…")

        def job():
            try:
                ensure_package("selenium")
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options
                opts = Options()
                opts.add_argument("--start-maximized")
                driver = webdriver.Chrome(options=opts)
                driver.get(CHROME_LINK)
            except Exception as e:
                self.log(f"[!] Could not start Chrome: {e}")
                return
            self.ui(lambda: self._chrome_finish(driver))

        threading.Thread(target=job, daemon=True).start()

    def _chrome_finish(self, driver):
        messagebox.showinfo(
            "Log in with Chrome",
            "Log in on c.mi.com/global in the Chrome window. Wait until the page shows you as "
            "logged in, then press OK here.")
        token = None
        while True:
            token = self._find_chrome_token(driver)
            if token:
                break
            if not messagebox.askretrycancel(
                    "Token not found",
                    "Could not find the new_bbs_serviceToken cookie yet.\n\n"
                    "Make sure you are fully logged in, then press Retry."):
                break
        try:
            driver.quit()
        except Exception:
            pass
        if token:
            if self.add_account_data({"name": "Chrome", "token": token, "offset_ms": 0}):
                self.log("Chrome token added.")
        else:
            self.log("[!] Chrome token not found. Use Add account and paste the "
                     "new_bbs_serviceToken value from DevTools, Application, Cookies.")

    def _find_chrome_token(self, driver):
        try:
            for c in driver.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", []):
                if c.get("name") == "new_bbs_serviceToken" and c.get("value"):
                    self.log("Found new_bbs_serviceToken via DevTools protocol.")
                    return c["value"]
        except Exception as e:
            self.log(f"DevTools cookie read failed: {e}")
        try:
            for c in driver.get_cookies():
                if c.get("name") == "new_bbs_serviceToken" and c.get("value"):
                    self.log("Found new_bbs_serviceToken via Selenium cookies.")
                    return c["value"]
        except Exception:
            pass
        try:
            tok = driver.execute_script(
                "var m=document.cookie.match(/popRunToken=([^;]+)/); return m?m[1]:null;")
            if tok:
                self.log("Only popRunToken was found. Run Check tokens to verify it works.")
            return tok
        except Exception:
            return None

    # ---------------- check / measure ----------------
    def run_bg(self, btns, busy_text, fn):
        saved = [(b, b.cget("text")) for b in btns]
        for b, _ in saved:
            b.configure(state="disabled", text=busy_text)

        def job():
            try:
                fn()
            finally:
                def restore():
                    for b, t in saved:
                        b.configure(state="normal", text=t)
                self.ui(restore)

        threading.Thread(target=job, daemon=True).start()

    def _check_one(self, a):
        self.ui(lambda: self.set_status(a["id"], "Checking…"))
        st = Api(a["token"]).status()
        text = STATE_TEXT.get(st["state"], st["state"])
        if st.get("deadline") and st["state"] in ("blocked", "approved"):
            text += f" until {st['deadline']}"
        self.ui(lambda: self.set_status(a["id"], text))
        self.log(f"Status: {text}", a["name"])

    def check_tokens(self):
        accs = list(self.accounts)
        if not accs:
            self.log("Add an account first.")
            return
        self.run_bg(self.check_btns, "Checking…", lambda: [self._check_one(a) for a in accs])

    def check_one(self, acc):
        threading.Thread(target=self._check_one, args=(acc,), daemon=True).start()

    def measure_offsets(self):
        accs = list(self.accounts)
        if not accs:
            self.log("Add an account first.")
            return
        self.log("Measuring round-trip time (5 requests per account)…")

        def job():
            for a in accs:
                api = Api(a["token"])
                first = api.status()
                if first["state"] in ("error", "expired"):
                    self.log(f"Skipped: {STATE_TEXT[first['state']]}", a["name"])
                    continue
                rtts = []
                for _ in range(5):
                    st = api.status()
                    if "rtt" in st:
                        rtts.append(st["rtt"])
                    time.sleep(0.2)
                if not rtts:
                    self.log("No successful samples.", a["name"])
                    continue
                rtt = statistics.median(rtts)
                offset = round(rtt / 2)
                a["offset_ms"] = offset
                self.log(f"Median round trip {rtt:.0f} ms, offset set to {offset} ms. Adjust by hand if needed.", a["name"])
                self.ui(lambda a=a: self._offset_updated(a))

        self.run_bg(self.measure_btns, "Measuring…", job)

    def _offset_updated(self, acc):
        card = self.cards.get(acc["id"])
        if card:
            card.set_offset(acc["offset_ms"])
        self.save_config()

    # ---------------- run ----------------
    def set_run_button(self, mode):
        if mode == "start":
            self.btn_run.configure(text="Start", state="normal", fg_color=C["accent"], hover_color=C["accent_hover"])
        elif mode == "stop":
            self.btn_run.configure(text="Stop", state="normal", fg_color=C["danger"], hover_color=C["danger_hover"])
        else:
            self.btn_run.configure(text="Stopping…", state="disabled")

    def toggle_run(self):
        if self.phase == "idle":
            self.start()
        else:
            self.stop()

    def start(self):
        accs = [a for a in self.accounts if a.get("enabled", True)]
        if not accs:
            messagebox.showwarning("Nothing to run", "Add an account and switch it on, then press Start.")
            return
        try:
            self.settings.update({
                "allow_blocked": bool(self.var_allow.get()),
                "interval_ms": max(20, int(float(self.var_interval.get()))),
                "window_s": max(1, int(float(self.var_window.get()))),
                "senders": int(self.seg_senders.get()),
            })
        except ValueError:
            messagebox.showwarning("Check run settings", "Retry interval and duration must be numbers.")
            return
        self.save_config()
        self.run_cfg = dict(self.settings)
        self.stop_event = threading.Event()
        self.workers = []
        self.target = None
        self.phase = "syncing"
        self.set_run_button("stop")
        threading.Thread(target=self._start_thread, args=(accs,), daemon=True).start()

    def _start_thread(self, accs):
        clock = Clock()
        if not clock.sync(self.log):
            self.log("[!] Could not reach any NTP server. Check your internet connection and press Start again.")
            self.phase = "idle"
            self.ui(self.finish_run)
            return
        self.clock = clock
        self.target = next_midnight_epoch(clock.now())
        self.log(f"Target is Beijing midnight, {datetime.fromtimestamp(self.target, BJ):%Y-%m-%d %H:%M:%S}.")
        threads = [threading.Thread(target=self._worker, args=(a,), daemon=True) for a in accs]
        for t in threads:
            t.start()
        self.workers = threads
        self.phase = "running"
        threading.Thread(target=self._resync_thread, daemon=True).start()

    def _resync_thread(self):
        for lead in (900, 60):
            if self.target - self.clock.now() <= lead:
                continue
            if not self.wait_until(self.target - lead):
                return
            self.clock.sync(self.log, label="Re-sync")

    def stop(self):
        self.stop_event.set()
        self.set_run_button("stopping")
        self.log("Stopping…")

    def finish_run(self):
        self.phase = "idle"
        self.workers = []
        self.set_run_button("start")
        self.log("Run finished.")

    def wait_until(self, epoch):
        """Sleep coarsely, then busy-wait for the last ~50 ms. False if stopped."""
        while not self.stop_event.is_set():
            remaining = epoch - self.clock.now()
            if remaining <= 0:
                return True
            if remaining > 0.05:
                self.stop_event.wait(min(remaining - 0.05, 0.25))
        return False

    def _worker(self, acc):
        name, cfg = acc["name"], self.run_cfg
        set_status = lambda t: self.ui(lambda: self.set_status(acc["id"], t))
        try:
            api = Api(acc["token"])
            st = api.status()
            state = st["state"]
            set_status(STATE_TEXT.get(state, state))
            self.log(f"Status: {STATE_TEXT.get(state, state)}", name)
            if state in ("expired", "approved", "unknown", "error"):
                return
            if state in ("blocked", "new_account") and not cfg["allow_blocked"]:
                self.log("Skipped. Switch on 'Also send for blocked or new accounts' to override.", name)
                return

            send_at = self.target - acc.get("offset_ms", 0) / 1000.0
            n = cfg["senders"]
            self.log(f"Offset {acc.get('offset_ms', 0):g} ms, {n} parallel sender(s), "
                     f"sending at {fmt_bj(send_at)} (UTC+8)", name)

            done = threading.Event()
            senders = [threading.Thread(target=self._sender, args=(acc, i + 1, n, send_at, done), daemon=True)
                       for i in range(n)]
            for t in senders:
                t.start()
            for t in senders:
                t.join()
            if not done.is_set():
                set_status("Finished (not approved)")
                self.log("Stopped or time window ended without approval.", name)
        except Exception as e:
            set_status("Error")
            self.log(f"Unexpected error: {e}", name)

    def _sender(self, acc, idx, total, send_at, done):
        cfg = self.run_cfg
        name = acc["name"] if total == 1 else f'{acc["name"]}#{idx}'
        set_status = lambda t: self.ui(lambda: self.set_status(acc["id"], t))

        def finish(status_text, message):
            if not done.is_set():
                done.set()
                set_status(status_text)
            self.log(message, name)

        try:
            api = Api(acc["token"])
            if not self.wait_until(send_at - 3) or done.is_set():
                return
            warm = api.status()
            self.log(f"Warm-up check: {STATE_TEXT.get(warm['state'], warm['state'])}", name)

            if not self.wait_until(send_at):
                return
            if idx == 1:
                set_status("Sending…")
            end = self.clock.now() + cfg["window_s"]
            interval = cfg["interval_ms"] / 1000.0

            while not self.stop_event.is_set() and not done.is_set() and self.clock.now() < end:
                self.log(f"Sending request at {fmt_bj(self.clock.now())}", name)
                resp = api.apply()
                self.log(f"Answer at {fmt_bj(self.clock.now())}", name)

                if "_error" in resp:
                    self.log(f"Network error: {resp['_error']}", name)
                else:
                    code, data = resp.get("code"), resp.get("data", {}) or {}
                    if code == 0:
                        result = data.get("apply_result")
                        deadline = data.get("deadline_format", "not specified")
                        if result == 1:
                            self.log("Request approved, verifying...", name)
                            final = api.status()
                            text = STATE_TEXT.get(final["state"], final["state"])
                            finish("Approved" if final["state"] == "approved" else text,
                                   f"Final status: {text}")
                            return
                        if result == 3:
                            finish(f"Limit reached until {deadline}",
                                   f"Limit reached, try again after {deadline} (Month/Day).")
                            return
                        if result == 4:
                            finish(f"Blocked until {deadline}", f"Blocked until {deadline} (Month/Day).")
                            return
                        self.log(f"Unknown apply_result: {resp}", name)
                    elif code == 100001:
                        self.log("Request rejected, retrying...", name)
                    elif code == 100003:
                        self.log("May have been approved, checking...", name)
                        final = api.status()
                        if final["state"] == "approved":
                            finish("Approved", "Approved!")
                            return
                    elif code == 100004:
                        finish("Cookie expired", "Cookie expired, need a new token.")
                        return
                    else:
                        self.log(f"Unexpected answer: {resp}", name)

                self.stop_event.wait(interval)
        except Exception as e:
            self.log(f"Unexpected error: {e}", name)

    def on_close(self):
        self.stop_event.set()
        self.root.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    App(root)
    root.mainloop()
