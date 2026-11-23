"""The TacoSort app: the same TacoSort in a window, for people who'd rather not type commands.

A side bar with four pages:
  Home      what TacoSort is doing: background sorting, the last sort, your accounts, the phone page
  Sort      learn from your folders, preview (moves nothing), sort now, undo, see every move
  Accounts  add an email account (with the steps to get an app password), test it, switch it off
  Settings  a switch for every setting, the folders to use, the background schedule, the phone page

The look (colours, cards, switches) comes from widgets.py and theme.py; the side bar's coloured dots
pick a theme. Mail work runs on a helper thread so the window never freezes; only the main thread
touches the window (results come back through a queue). The settings file, the keychain, the memory and
the log are the same ones the `tacosort` command uses, so both can be used side by side.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import queue
import re
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from . import __version__, cli, options, theme
from . import phone as phone_page
from .actions import (  # noqa: F401 - the mail work lives in actions.py; these names stay importable here
    MAX_SORT_ROUNDS,
    SAFETY_LIMIT_TEXT,
    enabled_accounts,
    learn,
    preview,
    schedule_is_on,
    sort_now,
    try_login,
    undo_last,
    write_report,
)
from .actions import status as sort_status
from .backend import BackendError
from .config import (
    ConfigError,
    account_id_problem,
    default_config_path,
    load_config,
    save_config,
)
from .models import AccountConfig, AppConfig
from .widgets import Look, ScrollFrame, Tip, scroll_when_needed

ASSETS = Path(__file__).parent / "assets"
APP_ID = "TacoSort.App"
PAD = 8
PAGES = (("home", "Home"), ("sort", "Sort"), ("accounts", "Accounts"), ("settings", "Settings"), ("help", "Help"))
NAV_TIPS = {"home": "See what TacoSort is doing.", "sort": "Tidy your Inbox now, or look first.",
            "accounts": "The email addresses TacoSort tidies.", "settings": "Choose how TacoSort tidies.",
            "help": "Short answers to common questions."}
INTRO = ("TacoSort tidies your email. Mail from shops, apps and companies goes into folders. Mail from real "
         "people stays in your Inbox. It never throws mail away and never marks it as read, and you can always "
         "put mail back. Your passwords stay in your computer's own password safe.")
# The Help page: a question and its answer, in the words someone new would use.
HELP = (
    ("What does TacoSort do?",
     ("It tidies your email. Mail sent by shops, apps and companies goes into folders such as Receipts or "
     "Promotions. Mail from real people stays in your Inbox, so you see it first.")),
    ("Will it delete my email?",
     ("No. TacoSort never deletes an email and never marks one as read. It only moves mail into folders, and "
     "every move can be undone.")),
    ("Where did my email go?",
     ("Into a folder in your mail app, next to your Inbox. For the full list, open the Sort page and press Show "
     "every move. It names each email, the folder it went to, and why.")),
    ("How do I get an email back?",
     ("Open the Sort page and press Undo last sort. Or drag the email back to your Inbox in your mail app. With "
     "Learning switched on in Settings, TacoSort notices and leaves that sender's mail in your Inbox from then "
     "on.")),
    ("What stays in my Inbox?",
     "Mail from people, replies, starred mail and fresh security codes. You can change the details in Settings."),
    ("What is a preview?",
     ("A practice run. It shows where each email would go and moves nothing, so you can check before anything "
     "happens.")),
    ("What is automatic sorting?",
     ("With its switch on (Home page), TacoSort checks for new mail every few minutes and files it for you, even "
     "when this window is closed. With it off, mail only moves when you press Sort my Inbox now.")),
    ("Is my password safe?",
     ("TacoSort keeps it in your computer's own password safe (the keychain), never in a file. It uses an app "
     "password: a second password made just for TacoSort, which you can cancel at any time.")),
    ("What do the coloured dots do?",
     "They change the colours of this window. Pick the one you like. It changes nothing about your mail."),
)


def phone_state(path: Path) -> tuple[bool, str, str]:
    """(on, one plain line, the address to open on the phone) for the phone page (slow: helper thread)."""
    try:
        on, text = phone_page.describe(path)
        return on, text, phone_page.address(load_config(path)) if on else ""
    except Exception as e:  # noqa: BLE001 - the window still works without knowing
        return False, f"Couldn't check the phone page ({type(e).__name__}).", ""


def switch_phone(path: Path, on: bool) -> str:
    try:
        return phone_page.turn_on(path) if on else phone_page.turn_off(path)
    except phone_page.PhoneError as e:
        raise cli.UserError(str(e)) from None


# ---- window helpers ---------------------------------------------------------------------------------
class Jobs:
    """Run mail work on a helper thread, one job at a time; hand results back to the window."""

    def __init__(self, root: tk.Misc, on_busy: Callable[[bool], None]):
        self.root = root
        self.on_busy = on_busy
        self.busy = False
        self._q: queue.Queue[Callable[[], None]] = queue.Queue()
        self._poll()

    def _poll(self) -> None:
        with contextlib.suppress(queue.Empty):
            while True:
                self._q.get_nowait()()
        self.root.after(100, self._poll)

    def later(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the window's thread soon (safe to call from the helper thread)."""
        self._q.put(fn)

    def start(self, work: Callable[[], Any], done: Callable[[Any, BaseException | None], None]) -> bool:
        if self.busy:
            messagebox.showinfo("TacoSort", "TacoSort is still busy with the last job. Please wait a moment.")
            return False
        self.busy = True
        self.on_busy(True)

        def run() -> None:
            try:
                result, error = work(), None
            except BaseException as e:  # noqa: BLE001 - shown to the person in plain words
                result, error = None, e

            def finish() -> None:
                self.busy = False
                self.on_busy(False)
                done(result, error)

            self.later(finish)

        threading.Thread(target=run, daemon=True).start()
        return True


_SETUP_HINT = re.compile(r"\b([Rr])un ['`]tacosort (?:setup|accounts add)['`](?: again)?")


def for_app(text: str) -> str:
    """Messages written for the command ("run 'tacosort setup'") pointed at the window instead."""
    return _SETUP_HINT.sub(lambda m: ("U" if m.group(1) == "R" else "u") + "se Add account... on the Accounts "
                           "page (with the same email address)", text)


def plain_error(e: BaseException) -> str:
    if isinstance(e, (BackendError, ConfigError, cli.UserError)):
        return for_app(str(e))
    if isinstance(e, ImportError):
        return f"Part of TacoSort is missing ({e.name or e}). {cli.REINSTALL_HINT}"
    return f"Something went wrong: {type(e).__name__}: {e}"


def when_text(at: datetime | None) -> tuple[str, str]:
    """("2:30 PM", "Today") for a time today, ("Nov 20", "Nov 20, 2:30 PM") for an older one."""
    if at is None:
        return "Not yet", ""
    local = at.astimezone() if at.tzinfo else at
    clock = local.strftime("%I:%M %p").lstrip("0")
    days = (datetime.now().date() - local.date()).days
    if days == 0:
        return clock, "Today"
    day = f"{local.strftime('%b')} {local.day}"
    return ("Yesterday" if days == 1 else day), f"{day}, {clock}"


# ---- the window -------------------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk, config_path: Path):
        self.root = root
        self.path = config_path
        self.cfg = AppConfig()
        self.action_buttons: list[ttk.Widget] = []
        root.title("TacoSort")
        self.look = Look(root, self._saved_theme())
        px = self.look.px
        wide = min(px(1120), root.winfo_screenwidth() - px(60))          # fits a small or zoomed screen
        tall = min(px(780), root.winfo_screenheight() - px(110))
        root.geometry(f"{wide}x{tall}")
        root.minsize(min(px(920), wide), min(px(600), tall))
        _set_icon(root)
        self.jobs = Jobs(root, self._set_busy)

        self._build_side()
        ttk.Frame(root, style="Line.TFrame", width=max(1, px(1))).pack(side="left", fill="y")
        main = ttk.Frame(root)
        main.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(main, padding=(px(PAD * 3), px(PAD * 3), px(PAD * 3), px(PAD)))
        top.pack(fill="x")
        self.title = tk.StringVar()
        ttk.Label(top, textvariable=self.title, style="H1.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(top, mode="indeterminate", length=px(160))
        self.status = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.status, style="Small.TLabel",
                  padding=(px(PAD * 3), px(PAD), px(PAD * 3), px(PAD))).pack(side="bottom", fill="x")
        holder = ttk.Frame(main)
        holder.pack(fill="both", expand=True)
        self.pages = {name: ttk.Frame(holder) for name, _ in PAGES}
        self.page = ""

        self._build_home()
        self._build_sort()
        self._build_accounts()
        self._build_options()
        self._build_help()
        self.look.on_change(self._paint)
        self._paint()
        self._wide = 0
        root.bind("<Configure>", self._resized, add="+")
        self.ok = self.reload()        # False: the settings file is broken, so the window must not save over it
        root.protocol("WM_DELETE_WINDOW", self._close)
        self.show("home" if self.cfg.accounts else "accounts")

    def _saved_theme(self) -> str:
        try:
            return load_config(self.path).theme
        except ConfigError:
            return theme.DEFAULT           # reload() explains the problem

    # ---- side bar -------------------------------------------------------------------------------
    def _build_side(self) -> None:
        px = self.look.px
        self.side = side = ttk.Frame(self.root, style="Side.TFrame", padding=(px(PAD * 1.5), px(PAD * 2)))
        side.pack(side="left", fill="y")
        brand = ttk.Frame(side, style="Side.TFrame")
        brand.pack(fill="x", pady=(0, px(PAD * 2.5)), padx=px(PAD / 2))
        with contextlib.suppress(tk.TclError, OSError):
            big = tk.PhotoImage(master=self.root, file=str(ASSETS / "tacosort.png"))
            logo = big.subsample(max(1, big.width() // px(34)))
            self.look.kept.append(logo)
            ttk.Label(brand, image=logo, style="Side.TLabel").pack(side="left", padx=(0, px(PAD + 2)))
        ttk.Label(brand, text="TacoSort", style="Side.Brand.TLabel").pack(side="left")

        self.nav: dict[str, ttk.Button] = {}
        for name, title in PAGES:
            b = ttk.Button(side, text=f"  {title}", compound="left", width=14, cursor="hand2",
                           style="Side.Nav.TButton", command=lambda n=name: self.show(n))
            b.pack(fill="x", pady=px(2))
            self.look.follow(b, f"icon-{name}")
            Tip(b, self.look, NAV_TIPS[name])
            self.nav[name] = b

        ttk.Label(side, text=f"Version {__version__}", style="Side.Small.TLabel").pack(side="bottom", anchor="w",
                                                                                      padx=px(PAD / 2))
        self.look_name = tk.StringVar()
        dots = ttk.Frame(side, style="Side.TFrame")
        dots.pack(side="bottom", anchor="w", pady=(px(4), px(PAD * 2)))
        ttk.Label(side, textvariable=self.look_name, style="Side.Small.TLabel").pack(side="bottom", anchor="w",
                                                                                    padx=px(PAD / 2))
        self.swatches: dict[str, ttk.Button] = {}
        for i, name in enumerate(theme.NAMES):
            b = ttk.Button(dots, style="Side.Swatch.TButton", cursor="hand2", takefocus=True,
                           command=lambda n=name: self.pick_theme(n))
            b.grid(row=i // 4, column=i % 4, padx=px(1), pady=px(1))
            b.bind("<Enter>", lambda _e, n=name: self.look_name.set(f"Look: {theme.label(n)}"))
            b.bind("<Leave>", lambda _e: self._show_look())
            Tip(b, self.look, f"{theme.label(name)}: changes the colours of this window, nothing else.")
            self.swatches[name] = b
        self._show_look()

    def _show_look(self) -> None:
        self.look_name.set(f"Look: {theme.label(self.look.name)}")
        for name, b in self.swatches.items():
            self.look.follow(b, f"swatch-{name}{'-on' if name == self.look.name else ''}")

    def pick_theme(self, name: str) -> None:
        """Repaint in the picked look and remember it (the phone page uses the same setting)."""
        self.look.use(name)
        self._show_look()
        if self.ok and self.cfg.theme != name:
            self.cfg = dataclasses.replace(self.cfg, theme=name)
            save_config(self.cfg, self.path)

    def _resized(self, event: tk.Event) -> None:
        if event.widget is self.root and event.width != self._wide:
            self._wide = event.width
            # (the side bar's asked-for width: its real one is still 1 when the window first appears)
            self.look.rewrap(event.width - self.side.winfo_reqwidth() - self.look.px(PAD * 6 + 2))

    def _paint(self) -> None:
        """Colours for the few widgets ttk styles don't reach."""
        c = self.look.c
        self.log_box.configure(background=c["surface"], foreground=c["text"], insertbackground=c["text"],
                               selectbackground=theme.mix(c["surface"], c["accent"], 0.35),
                               selectforeground=c["text"], font=self.look.fonts["small"])
        for tree in (self.acct_list, self.moves):
            tree.tag_configure("odd", background=self.look.stripe)

    def show(self, name: str) -> None:
        """Show one page ("home", "sort", "accounts", "settings" or "help")."""
        if self.page:
            self.pages[self.page].pack_forget()
        self.page = name
        self.pages[name].pack(fill="both", expand=True)
        self.title.set(dict(PAGES)[name])
        for key, b in self.nav.items():
            b.configure(style="Side.NavOn.TButton" if key == name else "Side.Nav.TButton")
            self.look.follow(b, f"icon-{key}-on" if key == name else f"icon-{key}")
        if name == "home":
            self._fill_home()

    # ---- shared ---------------------------------------------------------------------------------
    def reload(self) -> bool:
        try:
            self.cfg = load_config(self.path)
        except ConfigError as e:
            messagebox.showerror("TacoSort", f"There's a problem with your settings file ({self.path}):\n\n{e}")
            return False
        if self.cfg.theme != self.look.name:
            self.look.use(self.cfg.theme)
            self._show_look()
        self._fill_accounts()
        self._fill_options()
        self._refresh_status()
        return True

    def _set_busy(self, busy: bool) -> None:
        for b in self.action_buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        if busy:
            self.progress.pack(side="right")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self._refresh_status()

    def _button(self, parent: tk.Misc, text: str, command: Callable[[], None], kind: str = "Plain",
                **kw: Any) -> ttk.Button:
        b = ttk.Button(parent, text=text, command=command, cursor="hand2",
                       style=self.look.style_for(parent, f"{kind}.TButton"), **kw)
        self.action_buttons.append(b)
        return b

    def _card(self, parent: tk.Misc, title: str = "", pad: float = PAD * 2) -> ttk.Frame:
        """A rounded panel; pack or grid it yourself."""
        card = ttk.Frame(parent, style="Panel.TFrame", padding=self.look.px(pad))
        self.look.within(card, parent, pad * 2)
        if title:
            ttk.Label(card, text=title, style="Card.H2.TLabel").pack(anchor="w", pady=(0, self.look.px(PAD)))
        return card

    def _actions(self, parent: tk.Misc, rows: tuple[tuple[str, Callable[[], None], str, str], ...]) -> ttk.Frame:
        """A card of buttons, each with a plain line beside it saying what pressing it does."""
        px = self.look.px
        card = self._card(parent, pad=PAD * 1.5)
        card.columnconfigure(1, weight=1)
        for i, (text, command, kind, about) in enumerate(rows):
            self._button(card, text, command, kind, width=22).grid(row=i, column=0, sticky="ew", pady=px(3),
                                                                    padx=(0, px(PAD * 1.5)))
            words = ttk.Frame(card, style="Card.TFrame")
            words.grid(row=i, column=1, sticky="ew")
            self.look.column(words, 1.0, 250)          # the page, less the card's edges and the button
            self.look.wrapped(words, about, "Muted.TLabel").pack(fill="x")
        return card

    def _switch_row(self, box: tk.Misc, text: str, variable: tk.BooleanVar, help_text: str = "",
                    number: tk.StringVar | None = None, unit: str = "", low: int = 0, high: int = 0) -> None:
        """One setting: a switch, what it does, an optional number and a line of help."""
        px = self.look.px
        row = ttk.Frame(box, style="Card.TFrame")
        row.pack(fill="x", pady=px(5))
        row.columnconfigure(1, weight=1)
        switch = ttk.Checkbutton(row, variable=variable, style="Card.Switch.TCheckbutton", cursor="hand2")
        switch.grid(row=0, column=0, sticky="nw", padx=(0, px(PAD + 4)))
        words = ttk.Frame(row, style="Card.TFrame")
        words.grid(row=0, column=1, sticky="ew")
        self.look.within(words, box, 40 + PAD + 4)
        label = self.look.wrapped(words, text)
        label.pack(fill="x")
        label.bind("<Button-1>", lambda _e: switch.invoke())
        if number is not None:
            line = ttk.Frame(words, style="Card.TFrame")
            line.pack(fill="x", pady=(px(4), 0))
            ttk.Spinbox(line, from_=low, to=high, textvariable=number, width=6).pack(side="left")
            ttk.Label(line, text=unit, style="Card.Muted.TLabel").pack(side="left", padx=(px(6), 0))
        if help_text:
            self.look.wrapped(words, help_text, "Small.TLabel").pack(fill="x", pady=(px(2), 0))

    def _table(self, parent: tk.Misc, columns: tuple[str, ...], titles: tuple[str, ...], widths: tuple[int, ...],
               height: int, **kw: Any) -> ttk.Treeview:
        """A list inside a rounded card (the card is packed here; the list is returned)."""
        px = self.look.px
        card = self._card(parent, pad=PAD * 0.75)
        card.pack(fill="both", expand=True)
        tree = ttk.Treeview(card, columns=columns, show="headings", height=height, **kw)
        for col, title, width in zip(columns, titles, widths):
            tree.heading(col, text=title, anchor="w")
            tree.column(col, width=px(width), anchor="w")
        bar = ttk.Scrollbar(card, orient="vertical", command=tree.yview, style="Card.Vertical.TScrollbar")
        tree.pack(side="left", fill="both", expand=True)
        scroll_when_needed(tree, bar)
        return tree

    def _page_body(self, name: str) -> ttk.Frame:
        px = self.look.px
        body = ttk.Frame(self.pages[name], padding=(px(PAD * 3), 0, px(PAD * 3), px(PAD)))
        body.pack(fill="both", expand=True)
        return body

    def _log(self, line: str) -> None:
        """Safe from the helper thread: the line is added to the Sort page's log on the window's thread."""
        self.jobs.later(lambda: self._append_log(line))

    def _append_log(self, line: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", for_app(line).rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.log_lines += 1
        if line.startswith("Problem"):
            self._show_log(True)
        else:
            self._show_log(self.log_open)

    def _refresh_status(self) -> None:
        n = len(self.cfg.accounts)
        on, _text = schedule_is_on() if n else (False, "")
        self.sched_on = on
        if not n:
            self.status.set("No email accounts yet: start with Add an email account.")
        else:
            auto = "Sorting automatically in the background." if on else "Automatic sorting is off."
            self.status.set(f"{n} account{'' if n == 1 else 's'}. {auto}")
        self._fill_home()

    def _close(self) -> None:
        if self.jobs.busy:
            messagebox.showinfo("TacoSort", "TacoSort is still working. Please wait until it finishes, so no "
                                            "move is left half-done.")
            return
        self.root.destroy()

    # ---- Home page -------------------------------------------------------------------------------
    def _build_home(self) -> None:
        px = self.look.px
        scroll = ScrollFrame(self.pages["home"], self.look)
        scroll.pack(fill="both", expand=True)
        body = ttk.Frame(scroll.inner, padding=(px(PAD * 3), 0, px(PAD * 3), px(PAD)))
        body.pack(fill="both", expand=True)
        self.look.column(body, 1.0, PAD * 2 + 6)         # room for the scroll bar when the page is long
        self.look.wrapped(body, INTRO, "Muted.TLabel").pack(fill="x", pady=(0, px(PAD * 2)))

        # The start guide: shown until all three steps are done, each with a dot that lights up.
        self.welcome = self._card(body, "Start here: three small steps")
        self.previewed = False
        self.steps: dict[str, tuple[ttk.Label, tk.StringVar, str]] = {}
        for key, title, about, button, command in (
                ("account", "1. Add your email", ("Tell TacoSort which email address to tidy. It checks your "
                 "password works before it saves anything."), "Add my email", self.add_account),
                ("preview", "2. Look first", "A preview shows where each email would go. Nothing is moved.",
                 "Show me", lambda: (self.show("sort"), self.preview())),
                ("auto", "3. Let it sort by itself", ("Happy with the preview? Flip the switch under \"Automatic "
                 "sorting\" just below. TacoSort then files new mail for you."), "", None)):
            row = ttk.Frame(self.welcome, style="Card.TFrame")
            row.pack(fill="x", pady=px(4))
            row.columnconfigure(1, weight=1)
            dot = self.look.picture(row, "dot-off")
            dot.grid(row=0, column=0, sticky="nw", padx=(px(2), px(PAD + 2)), pady=px(5))
            words = ttk.Frame(row, style="Card.TFrame")
            words.grid(row=0, column=1, sticky="ew")
            self.look.within(words, self.welcome, 220)
            shown = tk.StringVar(value=title)
            ttk.Label(words, textvariable=shown, style="Card.Bold.TLabel").pack(anchor="w")
            self.look.wrapped(words, about, "Small.TLabel").pack(fill="x")
            if command is not None:
                self._button(row, button, command, "Primary" if key == "account" else "Plain", width=14).grid(
                    row=0, column=2, sticky="e", padx=(px(PAD), 0))
            self.steps[key] = (dot, shown, title)

        self.tiles = ttk.Frame(body)
        self.tiles.pack(fill="x")
        self.home: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        names = (("schedule", "Automatic sorting"), ("last", "Last sort"), ("today", "Moved today"),
                 ("phone", "On your phone"))
        for i, (key, title) in enumerate(names):
            self.tiles.columnconfigure(i, weight=1, uniform="tile")
            self.look.column(self.tiles, 0.25, PAD)
            tile = self._card(self.tiles)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(PAD), 0))
            ttk.Label(tile, text=title, style="Card.Small.TLabel").pack(anchor="w")
            value, sub = tk.StringVar(value="..."), tk.StringVar()
            ttk.Label(tile, textvariable=value, style="Card.Stat.TLabel").pack(anchor="w", pady=(px(2), 0))
            self.look.wrapped(tile, "", "Small.TLabel", textvariable=sub).pack(fill="x")
            self.home[key] = (value, sub)
            if key == "schedule":
                self.home_sched = tk.BooleanVar()
                switch = ttk.Checkbutton(tile, variable=self.home_sched, style="Card.Switch.TCheckbutton",
                                         cursor="hand2", command=self.toggle_schedule)
                switch.pack(anchor="w", pady=(px(PAD), 0))
                Tip(switch, self.look, "On: TacoSort files new mail by itself. Off: mail only moves when you "
                                       "press Sort my Inbox now.")
                self.action_buttons.append(switch)
        lower = ttk.Frame(body)
        lower.pack(fill="x", pady=(px(PAD), 0))
        lower.columnconfigure(0, weight=3, uniform="low")
        lower.columnconfigure(1, weight=2, uniform="low")
        accounts = self._card(lower)
        accounts.grid(row=0, column=0, sticky="nsew")
        self.look.column(accounts, 0.6, PAD * 5)
        head = ttk.Frame(accounts, style="Card.TFrame")
        head.pack(fill="x", pady=(0, px(PAD / 2)))
        ttk.Label(head, text="Your email accounts", style="Card.H2.TLabel").pack(side="left")
        change = ttk.Button(head, text="Change", style="Card.Quiet.TButton", cursor="hand2",
                            command=lambda: self.show("accounts"))
        change.pack(side="right")
        Tip(change, self.look, "Opens the Accounts page, where you add or remove an email address.")
        self.home_accounts = ttk.Frame(accounts, style="Card.TFrame")
        self.home_accounts.pack(fill="x")
        self.look.wrapped(accounts, "A coloured dot means TacoSort tidies that account. A grey dot means it is "
                          "switched off and left alone.", "Small.TLabel").pack(fill="x", pady=(px(PAD / 2), 0))

        self.look.column(lower, 0.4, PAD)
        quick = self._card(lower, "Tidy now")
        quick.grid(row=0, column=1, sticky="nsew", padx=(px(PAD), 0))
        self.look.wrapped(quick, "Preview only looks: nothing is moved. Sort moves the mail into folders. You can "
                          "always undo it on the Sort page.", "Muted.TLabel").pack(fill="x", pady=(0, px(PAD)))
        self._button(quick, "Preview", lambda: (self.show("sort"), self.preview()), "Primary").pack(fill="x")
        self._button(quick, "Sort my Inbox now", lambda: (self.show("sort"), self.sort_now())).pack(
            fill="x", pady=(px(PAD), 0))
        self.home_note = tk.StringVar()
        self.look.wrapped(body, "", "Muted.TLabel", textvariable=self.home_note).pack(fill="x", pady=(px(PAD), 0))

    def _fill_home(self) -> None:
        px = self.look.px
        cfg = self.cfg
        on = getattr(self, "sched_on", False)
        self.home_sched.set(on)
        self.home["schedule"][0].set("On" if on else "Off")
        self.home["schedule"][1].set(f"TacoSort files new mail every {cfg.interval_minutes} minutes." if on
                                     else "Flip the switch and TacoSort files new mail for you.")
        try:
            run, today = sort_status(cfg)
        except Exception:  # noqa: BLE001 - the Home page still opens when the memory file can't be read
            run, today = None, 0
        done = {"account": bool(cfg.accounts), "preview": bool(run) or self.previewed, "auto": on}
        for key, (dot, shown, title) in self.steps.items():
            self.look.follow(dot, "dot-on" if done[key] else "dot-off")
            shown.set(f"{title} (done)" if done[key] else title)
        if all(done.values()):
            self.welcome.pack_forget()
        else:
            self.welcome.pack(fill="x", pady=(0, px(PAD)), before=self.tiles)
        value, day = when_text(run["started"] if run else None)
        self.home["last"][0].set(value)
        if not run:
            self.home["last"][1].set("TacoSort hasn't sorted anything yet.")
        elif not run["scanned"]:
            self.home["last"][1].set(f"{day}: no new mail to sort.")
        else:
            self.home["last"][1].set(f"{day}: moved {run['moved']} of {run['scanned']} checked.")
        self.home["today"][0].set(str(today))
        self.home["today"][1].set("email moved into a folder." if today == 1 else "emails moved into folders.")

        for child in self.home_accounts.winfo_children():
            child.destroy()
        providers = cli._providers().PROVIDERS
        for a in cfg.accounts:
            row = ttk.Frame(self.home_accounts, style="Card.TFrame")
            row.pack(fill="x", pady=px(4))
            self.look.picture(row, "dot-on" if a.enabled else "dot-off").pack(side="left", padx=(px(2), px(PAD + 2)))
            ttk.Label(row, text=a.email, style="Card.TLabel").pack(side="left")
            name = providers[a.provider].name if a.provider in providers else a.provider
            ttk.Label(row, text=name if a.enabled else f"{name}, off", style="Card.Small.TLabel").pack(side="right")
        if not cfg.accounts:
            ttk.Label(self.home_accounts, text="None yet. Step 1 above adds one.",
                      style="Card.Muted.TLabel").pack(anchor="w")

    def toggle_schedule(self) -> None:
        """The Home page's switch: background sorting on or off, with the saved number of minutes."""
        want_on = self.home_sched.get()
        if want_on and not enabled_accounts(self.cfg):
            self.home_sched.set(False)
            self.home_note.set("Add an account before turning on automatic sorting.")
            return
        if not want_on and not messagebox.askyesno("Automatic sorting", "Turn off automatic sorting?\n\nNew "
                                                   "mail stays in the Inbox until you turn it on again."):
            self.home_sched.set(True)
            return
        sched = cli._schedule()
        minutes, path = self.cfg.interval_minutes, self.path.resolve()

        def done(result: Any, error: BaseException | None) -> None:
            self.home_note.set(str(result) if error is None else "Automatic sorting couldn't be changed: "
                               f"{plain_error(error)}")
            self.sched_var.set(schedule_is_on()[0])

        if not self.jobs.start(lambda: sched.install(minutes, path) if want_on else sched.uninstall(), done):
            self.home_sched.set(not want_on)

    # ---- Accounts page ---------------------------------------------------------------------------
    def _build_accounts(self) -> None:
        px = self.look.px
        tab = self._page_body("accounts")
        self.look.wrapped(tab, "These are the email addresses TacoSort tidies. To change one, click it in the list, "
                          "then press a button underneath.", "Muted.TLabel").pack(fill="x", pady=(0, px(PAD * 1.5)))
        self.acct_list = self._table(tab, ("name", "email", "provider", "status"),
                                     ("Nickname", "Email address", "Mail service", "Tidied?"), (160, 300, 160, 100),
                                     6, selectmode="browse")
        self.acct_note = tk.StringVar()
        self.look.wrapped(tab, "", textvariable=self.acct_note).pack(fill="x", pady=(px(PAD), 0))
        self._actions(tab, (
            ("Add an email account", self.add_account, "Primary",
             "Connect another email address. TacoSort checks the password works before it saves anything."),
            ("Check it works", self.test_account, "Plain",
             "Click an account in the list first. TacoSort tries to sign in and tells you if it worked."),
            ("Turn on / off", self.toggle_account, "Plain",
             "Click an account first. Off means TacoSort leaves that account alone. Nothing is deleted."),
            ("Remove", self.remove_account, "Danger",
             "Click an account first. TacoSort forgets it. Your mail and your folders stay as they are."),
        )).pack(fill="x", pady=(px(PAD), 0))

    def _fill_accounts(self) -> None:
        self.acct_list.delete(*self.acct_list.get_children())
        providers = cli._providers().PROVIDERS
        for i, a in enumerate(self.cfg.accounts):
            name = providers[a.provider].name if a.provider in providers else a.provider
            self.acct_list.insert("", "end", iid=a.id, values=(a.id, a.email, name, "On" if a.enabled else "Off"),
                                  tags=("odd",) if i % 2 else ())
        if not self.cfg.accounts:
            self.acct_note.set("No accounts yet. Press Add an email account to start.")

    def _selected(self) -> AccountConfig | None:
        sel = self.acct_list.selection()
        acct = next((a for a in self.cfg.accounts if sel and a.id == sel[0]), None)
        if acct is None:
            messagebox.showinfo("TacoSort", "Click an account in the list first, so TacoSort knows which one "
                                "you mean.")
        return acct

    def add_account(self) -> None:
        AddAccount(self)

    def test_account(self) -> None:
        acct = self._selected()
        if acct is None:
            return
        self.acct_note.set(f"Logging in to {acct.email}...")

        def done(result: Any, error: BaseException | None) -> None:
            problem, folders = result if error is None else (plain_error(error), 0)
            self.acct_note.set(f"{acct.email}: {for_app(problem)}" if problem
                               else f"Connected! {acct.email} has {folders} folders.")

        self.jobs.start(lambda: try_login(acct), done)

    def toggle_account(self) -> None:
        acct = self._selected()
        if acct is None:
            return
        acct.enabled = not acct.enabled
        save_config(self.cfg, self.path)
        self.acct_note.set(f"{acct.email} is now {'on' if acct.enabled else 'off (TacoSort leaves it alone)'}.")
        self.reload()

    def remove_account(self) -> None:
        acct = self._selected()
        if acct is None:
            return
        if not messagebox.askyesno("Remove account", f"Stop sorting {acct.email}?\n\nYour mail and folders "
                                   "stay exactly as they are; TacoSort only forgets the account and its saved "
                                   "password."):
            return
        self.cfg.accounts = [a for a in self.cfg.accounts if a.id != acct.id]
        save_config(self.cfg, self.path)
        try:
            cli._auth().delete_account_secrets(acct.id)
            self.acct_note.set(f"Removed {acct.email}.")
        except (BackendError, OSError) as e:
            self.acct_note.set(f"Removed {acct.email}, but its saved password couldn't be deleted from the "
                               f"keychain ({e}); you can delete the 'tacosort' entries for '{acct.id}' by hand.")
        self.reload()

    # ---- Settings page ---------------------------------------------------------------------------
    def _build_options(self) -> None:
        px = self.look.px
        page = self.pages["settings"]
        bar = ttk.Frame(page, padding=(px(PAD * 3), px(PAD), px(PAD * 3), 0))
        bar.pack(side="bottom", fill="x")
        ttk.Frame(page, style="Line.TFrame", height=max(1, px(1))).pack(side="bottom", fill="x")
        save = self._button(bar, "Save settings", self.save_options, "Primary")
        save.pack(side="left")
        Tip(save, self.look, "Keeps the switches the way they are now. Nothing changes until you press this.")
        back = self._button(bar, "Undo changes", self.reload)
        back.pack(side="left", padx=px(PAD))
        Tip(back, self.look, "Puts every switch back to how it was the last time you saved.")
        self.opt_note = tk.StringVar()
        self.look.column(bar, 1.0, 300)
        self.look.wrapped(bar, "", textvariable=self.opt_note).pack(side="left", fill="x", expand=True)

        head = ttk.Frame(page, padding=(px(PAD * 3), 0, px(PAD * 3), px(PAD)))
        head.pack(fill="x")
        self.look.wrapped(head, "Each switch is one choice: coloured means on, grey means off. Change what you like, "
                          "then press Save settings at the bottom. Nothing changes until you do.",
                          "Muted.TLabel").pack(fill="x")
        scroll = ScrollFrame(page, self.look)
        scroll.pack(fill="both", expand=True)
        body = ttk.Frame(scroll.inner, padding=(px(PAD * 3), 0, px(PAD * 3), px(PAD)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        self.look.column(body, 0.5, PAD + 6)        # two columns, less the gap and the scroll bar
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(PAD)))
        right.grid(row=0, column=1, sticky="nsew", padx=(px(PAD), 0))

        self.opt_vars: dict[str, tuple[tk.BooleanVar, tk.StringVar | None]] = {}
        for title, group in options.GROUPS:
            box = self._card(left, title)
            box.pack(fill="x", pady=(0, px(PAD * 1.5)))
            for o in group:
                ticked = tk.BooleanVar()
                number = tk.StringVar() if o.unit else None
                self._switch_row(box, o.label, ticked, o.help, number, o.unit, o.low, o.high)
                self.opt_vars[o.field] = (ticked, number)

        cats = self._card(right, "Folders")
        cats.pack(fill="x", pady=(0, px(PAD * 1.5)))
        self.look.wrapped(cats, "Each switch is one folder. On: that kind of mail goes into it. Off: that kind of "
                          "mail stays in your Inbox.", "Small.TLabel").pack(
            fill="x", pady=(0, px(4)))
        self.cats_box = ttk.Frame(cats, style="Card.TFrame")
        self.cats_box.pack(fill="x")
        self.cat_vars: dict[str, tk.BooleanVar] = {}

        bg = self._card(right, "Automatic sorting")
        bg.pack(fill="x", pady=(0, px(PAD * 1.5)))
        self.sched_var = tk.BooleanVar()
        self.interval_var = tk.StringVar()
        self._switch_row(bg, "Sort new mail by itself, every", self.sched_var,
                         "TacoSort checks for new mail this often and files it. It works while you are signed in "
                         "to this computer, even with this window closed.",
                         self.interval_var, "minutes", 5, 1439)

        ph = self._card(right, "On your phone")
        ph.pack(fill="x", pady=(0, px(PAD * 1.5)))
        self.look.wrapped(ph, "Open TacoSort on your phone to see what it did, sort, or undo. You need the free "
                    "Tailscale app on this computer and on your phone, signed in as you. Nobody else can open "
                    "it.", "Small.TLabel").pack(fill="x")
        self.phone_note = tk.StringVar(value="Checking...")
        self.look.wrapped(ph, "", textvariable=self.phone_note).pack(fill="x", pady=(px(PAD), 0))
        row = ttk.Frame(ph, style="Card.TFrame")
        row.pack(fill="x", pady=(px(PAD), 0))
        self.phone_button = self._button(row, "Turn on", self.toggle_phone)
        self.phone_button.pack(side="left")
        Tip(self.phone_button, self.look, "Turns the phone page on or off. Your mail is not touched.")
        copy = self._button(row, "Copy address", self.copy_phone_address, "Quiet")
        copy.pack(side="left", padx=px(PAD))
        Tip(copy, self.look, "Copies the web address of the phone page, so you can send it to your phone and open "
                             "it there.")
        self.phone_on = False
        self.phone_address = ""

    def _fill_options(self) -> None:
        values = options.read(self.cfg)
        for name, (ticked, number) in self.opt_vars.items():
            on, n = values.options[name]
            ticked.set(on)
            if number is not None:
                number.set(str(n))
        for child in self.cats_box.winfo_children():
            child.destroy()
        self.cat_vars = {}
        for key, folder, about in options.categories(self.cfg):
            var = tk.BooleanVar(value=values.categories.get(key, False))
            self._switch_row(self.cats_box, folder, var, about)
            self.cat_vars[key] = var
        self.interval_var.set(str(values.interval_minutes))
        self.sched_var.set(schedule_is_on()[0])
        self.opt_note.set("")
        self.refresh_phone()

    # ---- phone page ------------------------------------------------------------------------------
    def refresh_phone(self) -> None:
        """Ask on a helper thread (Tailscale can take a second) and show the answer here."""
        def ask() -> None:
            state = phone_state(self.path)
            self.jobs.later(lambda: self._show_phone(*state))

        threading.Thread(target=ask, daemon=True).start()

    def _show_phone(self, on: bool, text: str, address: str) -> None:
        self.phone_on, self.phone_address = on, address
        self.phone_note.set(text)
        self.phone_button.configure(text="Turn off" if on else "Turn on")
        self.home["phone"][0].set("On" if on else "Off")
        self.home["phone"][1].set("You can open TacoSort on your phone." if on
                                  else "Turn it on in Settings to use TacoSort from your phone.")

    def toggle_phone(self) -> None:
        want_on = not self.phone_on
        self.phone_note.set("Turning the phone page on..." if want_on else "Turning the phone page off...")

        def done(result: Any, error: BaseException | None) -> None:
            self.phone_note.set(result if error is None else f"That didn't work: {plain_error(error)}")
            if error is None:
                self.phone_on = want_on
                self.phone_button.configure(text="Turn off" if want_on else "Turn on")
                self.refresh_phone()

        self.jobs.start(lambda: switch_phone(self.path, want_on), done)

    def copy_phone_address(self) -> None:
        if not self.phone_address:
            self.phone_note.set("Turn the phone page on first; then its address can be copied.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.phone_address)
        self.phone_note.set(f"Copied {self.phone_address}: open it in Safari or Chrome on your phone.")

    def save_options(self) -> None:
        values = options.Values(
            options={name: (t.get(), n.get() if n is not None else 0) for name, (t, n) in self.opt_vars.items()},
            categories={k: v.get() for k, v in self.cat_vars.items()},
            interval_minutes=self.interval_var.get(),
        )
        new, problems = options.apply(self.cfg, values)
        if problems:
            self.opt_note.set("Not saved: " + " ".join(problems))
            return
        save_config(new, self.path)
        self.cfg = new
        want_on = self.sched_var.get()
        was_on = schedule_is_on()[0]
        if not want_on and not was_on:
            self.opt_note.set("Saved. The next sort uses these settings.")
            return
        # Switched on: set the task up again every time, so it uses this interval and this copy of the app
        # (after the app file was moved, opening it and saving once fixes the schedule).
        if want_on and not enabled_accounts(new):
            self.opt_note.set("Saved. Add an account before turning on automatic sorting.")
            self.sched_var.set(False)
            return
        sched = cli._schedule()

        def work() -> str:
            if want_on:
                return sched.install(new.interval_minutes, self.path.resolve())
            return sched.uninstall()

        def done(result: Any, error: BaseException | None) -> None:
            if error is None:
                self.opt_note.set(f"Saved. {result}")
            else:
                self.opt_note.set(f"Saved, but automatic sorting couldn't be changed: {plain_error(error)}")
            self.sched_var.set(schedule_is_on()[0])

        self.jobs.start(work, done)

    # ---- Sort page -------------------------------------------------------------------------------
    def _build_sort(self) -> None:
        px = self.look.px
        tab = self._page_body("sort")
        self.look.wrapped(tab, "Not sure what will happen? Press Preview first. It only looks, and moves nothing.",
                          "Muted.TLabel").pack(fill="x", pady=(0, px(PAD * 1.5)))
        self._actions(tab, (
            ("Preview", self.preview, "Primary",
             "Look first. Shows where each email in your Inbox would go. Nothing is moved."),
            ("Sort my Inbox now", self.sort_now, "Plain",
             "Moves those emails into their folders. Mail from people stays in your Inbox."),
            ("Undo last sort", self.undo, "Plain",
             "Changed your mind? Puts the emails from the last sort back where they were."),
            ("Learn from my folders", self.learn, "Plain",
             "Looks at the folders you already made, so TacoSort files mail the way you do. Nothing is moved."),
            ("Show every move", self.report, "Plain",
             "Opens a page listing every email TacoSort has moved, and why."),
        )).pack(fill="x", pady=(0, px(PAD * 1.5)))

        self.summary = tk.StringVar(value="Nothing to show yet. Press Preview to see where your mail would go.")
        self.look.wrapped(tab, "", "H2.TLabel", textvariable=self.summary).pack(fill="x", pady=(0, px(PAD)))

        self.log_card = self._card(tab, pad=PAD)
        self.log_box = tk.Text(self.log_card, height=7, wrap="word", state="disabled", relief="flat",
                               borderwidth=0, highlightthickness=0, padx=px(6), pady=px(4))
        log_bar = ttk.Scrollbar(self.log_card, orient="vertical", command=self.log_box.yview,
                                style="Card.Vertical.TScrollbar")
        self.log_box.pack(side="left", fill="both", expand=True)
        scroll_when_needed(self.log_box, log_bar)
        self.log_open = False
        self.log_lines = 0
        self.log_toggle = ttk.Button(tab, style="Quiet.TButton", cursor="hand2",
                                     command=lambda: self._show_log(not self.log_open))
        self.log_toggle.pack(side="bottom", anchor="w", pady=(px(PAD / 2), 0))
        Tip(self.log_toggle, self.look, "The notes TacoSort wrote while it worked, step by step.")
        self.moves = self._table(tab, ("account", "sender", "subject", "folder", "why"),
                                 ("Account", "From", "Subject", "Goes to folder", "Why"), (80, 150, 230, 160, 200),
                                 6)
        self._show_log(False)

    def _build_help(self) -> None:
        px = self.look.px
        scroll = ScrollFrame(self.pages["help"], self.look)
        scroll.pack(fill="both", expand=True)
        body = ttk.Frame(scroll.inner, padding=(px(PAD * 3), 0, px(PAD * 3), px(PAD)))
        body.pack(fill="both", expand=True)
        self.look.column(body, 1.0, PAD * 2 + 6)
        self.look.wrapped(body, "Short answers to the things people ask most. Tip: rest the mouse on a button or "
                          "switch anywhere in TacoSort and a small note says what it does.",
                          "Muted.TLabel").pack(fill="x", pady=(0, px(PAD * 1.5)))
        for question, answer in HELP:
            card = self._card(body, question)
            card.pack(fill="x", pady=(0, px(PAD)))
            self.look.wrapped(card, answer, "Muted.TLabel").pack(fill="x")

    def _show_log(self, show: bool) -> None:
        """Fold the "what it did" lines open or shut, under the list of moves."""
        self.log_open = show
        count = f" ({self.log_lines} lines)" if self.log_lines else ""
        self.log_toggle.configure(text=("Hide what it did" if show else "Show what it did") + count)
        if show and not self.log_card.winfo_manager():
            self.log_card.pack(side="bottom", fill="x", pady=(self.look.px(PAD), 0), before=self.log_toggle)
            self.log_box.see("end")
        elif not show and self.log_card.winfo_manager():
            self.log_card.pack_forget()

    def _need_accounts(self) -> bool:
        if enabled_accounts(self.cfg):
            return True
        messagebox.showinfo("TacoSort", "Add an email account first (the Accounts page).")
        self.show("accounts")
        return False

    def _show_moves(self, moves: list[Any]) -> None:
        self.moves.delete(*self.moves.get_children())
        for i, m in enumerate(moves):
            self.moves.insert("", "end", values=(m.account, m.sender, m.subject, m.to_folder, m.reason),
                              tags=("odd",) if i % 2 else ())

    def learn(self) -> None:
        if not self._need_accounts():
            return
        cfg = self.cfg
        self._log("Learning from the folders you already have (nothing is moved)...")
        self.summary.set("Learning from your folders (nothing is moved)...")

        def done(rep: Any, error: BaseException | None) -> None:
            if error is not None:
                self.summary.set("Learning didn't finish.")
                self._append_log(f"Problem: {plain_error(error)}")
                return
            said = (f"Looked at {rep.messages_scanned} messages in {rep.folders_scanned} folders. "
                    f"Learned {rep.rules_learned} sender rules and found {rep.contacts_found} people "
                    "you write to.")
            self.summary.set(said)
            self._append_log(said)
            for e in rep.errors:
                self._append_log(f"Problem: {e}")

        self.jobs.start(lambda: learn(cfg, self._log), done)

    def preview(self) -> None:
        if not self._need_accounts():
            return
        cfg = self.cfg
        self.summary.set("Looking at your Inbox (nothing will be moved)...")

        def done(result: Any, error: BaseException | None) -> None:
            if error is not None:
                self.summary.set("The preview didn't finish.")
                self._append_log(f"Problem: {plain_error(error)}")
                return
            moves, scanned, kept, problems = result
            self._show_moves(moves)
            self.previewed = True
            self.summary.set(f"Preview: {len(moves)} of {scanned} emails would go into folders; {kept} would "
                             "stay in the Inbox. Nothing was moved.")
            for p in problems:
                self._append_log(f"Problem: {p}")

        self.jobs.start(lambda: preview(cfg, self._log), done)

    def sort_now(self) -> None:
        if not self._need_accounts():
            return
        if not messagebox.askyesno("Sort my Inbox now", "Move the emails in your Inbox into folders now?\n\n"
                                   "Mail from people, starred mail and security codes stay. Every move can be "
                                   "undone with Undo last sort."):
            return
        cfg = self.cfg
        self.summary.set("Sorting your Inbox...")

        def done(result: Any, error: BaseException | None) -> None:
            if error is not None:
                self.summary.set("Sorting stopped.")
                self._append_log(f"Problem: {plain_error(error)}")
                return
            moved, problems = result
            self.summary.set(f"Done: {moved} emails moved into folders." + (" Undo last sort puts them back."
                                                                             if moved else ""))
            self._show_moves([])
            for p in problems:
                self._append_log(f"Problem: {p}")

        self.jobs.start(lambda: sort_now(cfg, self._log), done)

    def undo(self) -> None:
        if not self.cfg.accounts:
            return
        if not messagebox.askyesno("Undo last sort", "Put the emails from the last sort back where they were?"):
            return
        cfg = self.cfg

        def done(result: Any, error: BaseException | None) -> None:
            if error is not None:
                self._append_log(f"Problem: {plain_error(error)}")
                return
            self.summary.set(f"Put {result} emails back." if result else "There was nothing to undo.")

        self.jobs.start(lambda: undo_last(cfg, self._log), done)

    def report(self) -> None:
        try:
            path = write_report(self.cfg)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("TacoSort", plain_error(e))
            return
        webbrowser.open(path.resolve().as_uri())


class AddAccount:
    """The Add account window: email -> what kind of account -> password (or Microsoft sign-in) -> test -> save."""

    def __init__(self, app: App):
        self.app = app
        self.px = px = app.look.px
        self.win = tk.Toplevel(app.root, background=app.look.c["bg"])
        self.win.title("Add an email account")
        self.win.transient(app.root)
        self.win.geometry(f"{px(640)}x{px(600)}")
        _set_icon(self.win)
        app.look.title_bar(self.win)
        self.body = ttk.Frame(self.win, padding=px(PAD * 3))
        self.body.pack(fill="both", expand=True)
        app.look.column(self.body, fixed=640 - PAD * 6)
        self.provider: Any = None
        self.email = tk.StringVar()
        self.short = tk.StringVar()
        self.password = tk.StringVar()
        self.client_id = tk.StringVar()
        self.host = tk.StringVar()
        self.port = tk.StringVar(value="993")
        self.ssl = tk.BooleanVar(value=True)
        self.note = tk.StringVar()
        self._step_email()
        self.win.grab_set()

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def _step_email(self) -> None:
        px = self.px
        self._clear()
        ttk.Label(self.body, text="Step 1 of 2: which email?", style="H2.TLabel").pack(anchor="w",
                                                                                   pady=(0, px(PAD)))
        self.app.look.wrapped(self.body, "Type the email address you want TacoSort to tidy, then press Next.",
                              "Muted.TLabel").pack(fill="x", pady=(0, px(PAD)))
        entry = ttk.Entry(self.body, textvariable=self.email, width=46)
        entry.pack(anchor="w", pady=(px(4), px(PAD * 1.5)))
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self._next())
        ttk.Button(self.body, text="Next", command=self._next, style="Primary.TButton", cursor="hand2").pack(
            anchor="w")
        self.app.look.wrapped(self.body, "", "Danger.TLabel", textvariable=self.note).pack(fill="x", pady=px(PAD))

    def _next(self) -> None:
        email = self.email.get().strip()
        local, _, domain = email.partition("@")
        if not (local and "." in domain and " " not in email):
            self.note.set("That doesn't look like an email address. It should look like name@example.com.")
            return
        cfg = self.app.cfg
        existing = next((a for a in cfg.accounts if a.email.lower() == email.lower()), None)
        providers = cli._providers()
        if existing is not None:
            self.provider = providers.PROVIDERS.get(existing.provider) or providers.detect_provider(email)
            self.existing: AccountConfig | None = existing
        else:
            self.provider = providers.detect_provider(email)
            self.existing = None
            self.short.set(cli._suggest_id(cfg, self.provider.key, email))
            self.host.set(self.provider.host)
        self.note.set("")
        self._step_details()

    def _step_details(self) -> None:
        px = self.px
        self._clear()
        p = self.provider
        b = self.body
        if self.existing is not None:
            self.app.look.wrapped(b, f"{self.existing.email} is already set up (as '{self.existing.id}'). You can give it a new "
                       "app password here.").pack(fill="x", pady=(0, px(PAD)))
        elif p.key == "custom":
            self.app.look.wrapped(b, "TacoSort doesn't recognise this email provider, so it needs the IMAP server details. "
                       "Your provider's help pages list them (look for 'IMAP settings').").pack(
                fill="x", pady=(0, px(PAD)))
            grid = ttk.Frame(b)
            grid.pack(anchor="w", pady=(0, px(PAD)))
            ttk.Label(grid, text="IMAP server", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Entry(grid, textvariable=self.host, width=32).grid(row=0, column=1, sticky="w", padx=px(8),
                                                                    pady=px(3))
            ttk.Label(grid, text="Port", style="Muted.TLabel").grid(row=1, column=0, sticky="w")
            ttk.Entry(grid, textvariable=self.port, width=8).grid(row=1, column=1, sticky="w", padx=px(8),
                                                                   pady=px(3))
            ttk.Checkbutton(grid, text="  Uses SSL/TLS (almost always yes for port 993)", variable=self.ssl,
                            style="Switch.TCheckbutton").grid(row=2, column=1, sticky="w", padx=px(8), pady=px(3))
        else:
            ttk.Label(b, text=f"Step 2 of 2: this looks like a {p.name} account.", style="H2.TLabel").pack(
                anchor="w", pady=(0, px(PAD * 1.5)))
        if self.existing is None:
            line = ttk.Frame(b)
            line.pack(anchor="w", pady=(0, px(PAD * 1.5)))
            ttk.Label(line, text="A nickname for this account", style="Muted.TLabel").pack(side="left")
            ttk.Entry(line, textvariable=self.short, width=24).pack(side="left", padx=px(8))

        row = ttk.Frame(b)               # packed below the explanation; its buttons are made inside it
        if p.auth == "password":
            self.app.look.wrapped(b, f"TacoSort needs an app password for {p.name}. That is a second password made just for "
                       "TacoSort. Your normal password stays secret, and you can cancel the app password at any "
                       "time. Make one, copy it, and paste it below.").pack(fill="x")
            notes = (getattr(p, "notes", "") or "").strip()
            if notes:
                self.app.look.wrapped(b, notes, "Muted.TLabel").pack(fill="x", pady=(px(6), 0))
            if p.app_password_url:
                ttk.Button(b, text="Open the page where you make one", cursor="hand2",
                           command=lambda: webbrowser.open(p.app_password_url)).pack(anchor="w", pady=px(PAD * 1.5))
            line = ttk.Frame(b)
            line.pack(anchor="w", pady=(0, px(PAD)))
            ttk.Label(line, text="Paste the app password here", style="Muted.TLabel").pack(side="left")
            ttk.Entry(line, textvariable=self.password, show="•", width=30).pack(side="left", padx=px(8))
            self.save_button = ttk.Button(row, text="Check it and save", command=self._save_password,
                                          style="Primary.TButton", cursor="hand2")
        elif p.auth == "oauth2-microsoft":
            self.app.look.wrapped(b, "Microsoft doesn't let mail programs sign in to Outlook.com / Hotmail with a password. "
                       "TacoSort signs in the Microsoft way: you approve it once in your browser. For that it "
                       "needs a free 'client ID' you create once on Microsoft's website (docs/outlook-oauth.md "
                       "in the TacoSort guide walks you through it, about 10 minutes).").pack(fill="x")
            line = ttk.Frame(b)
            line.pack(anchor="w", pady=px(PAD * 1.5))
            ttk.Label(line, text="Client ID", style="Muted.TLabel").pack(side="left")
            ttk.Entry(line, textvariable=self.client_id, width=40).pack(side="left", padx=px(8))
            self.save_button = ttk.Button(row, text="Sign in with Microsoft", command=self._save_microsoft,
                                          style="Primary.TButton", cursor="hand2")
        else:
            self.app.look.wrapped(b, f"{p.name} needs a sign-in type ({p.auth}) this window can't set up yet. Use "
                       "`tacosort accounts add` in a terminal (see docs/providers.md).").pack(fill="x")
            self.save_button = ttk.Button(row, text="Close", command=self.win.destroy, cursor="hand2")
        row.pack(anchor="w", pady=px(PAD))
        self.save_button.pack(side="left")
        ttk.Button(row, text="Back", command=self._step_email, cursor="hand2").pack(side="left", padx=px(PAD))
        self.app.look.wrapped(b, "", textvariable=self.note).pack(fill="x")

    def _account(self) -> AccountConfig | None:
        """The new account from the form, or None (with the reason shown) when something is missing."""
        if self.existing is not None:
            return self.existing
        p = self.provider
        acct_id = self.short.get().strip()
        problem = account_id_problem(acct_id)
        if problem:
            self.note.set(problem)
            return None
        if any(a.id == acct_id for a in self.app.cfg.accounts):
            self.note.set(f"'{acct_id}' is already used by another account. Pick another name.")
            return None
        host, port, ssl = p.host, p.port, p.ssl
        if p.key == "custom":
            host = self.host.get().strip()
            if not host:
                self.note.set("Please type the IMAP server name.")
                return None
            if not self.port.get().strip().isdigit() or not 1 <= int(self.port.get()) <= 65535:
                self.note.set("The port is a number, usually 993.")
                return None
            port, ssl = int(self.port.get()), self.ssl.get()
        return AccountConfig(id=acct_id, email=self.email.get().strip(), provider=p.key, host=host, port=port,
                             ssl=ssl, auth=p.auth)

    def _save_password(self) -> None:
        acct = self._account()
        if acct is None:
            return
        secret = self.password.get().strip()
        if not secret:
            self.note.set("Paste the app password first.")
            return
        if acct.provider not in ("custom", "protonmail-bridge"):
            secret = "".join(secret.split())   # app passwords are often shown in groups of four
        self.note.set("Logging in...")
        is_new = self.existing is None

        def work() -> tuple[str | None, int]:
            problem, folders = try_login(acct, password=secret)
            if problem is None:
                cli._auth().set_secret(acct.id, secret)
            return problem, folders

        def done(result: Any, error: BaseException | None) -> None:
            problem, folders = result if error is None else (plain_error(error), 0)
            if problem:
                self.note.set(f"That didn't work: {for_app(problem)}")
                return
            self.password.set("")
            if is_new:
                self.app.cfg.accounts.append(acct)
                save_config(self.app.cfg, self.app.path)
            self.app.reload()
            self.app.acct_note.set(f"Connected! {acct.email} has {folders} folders. "
                                   + ("Next: press Preview on the Sort page to see where your mail would go." if is_new
                                      else "The new password is saved."))
            self.win.destroy()

        self.app.jobs.start(work, done)

    def _save_microsoft(self) -> None:
        acct = self._account()
        if acct is None:
            return
        client_id = self.client_id.get().strip()
        if not client_id:
            self.note.set("Paste your client ID first.")
            return
        acct = dataclasses.replace(acct, oauth_client_id=client_id)
        self.note.set("Starting the Microsoft sign-in...")

        def show(line: str) -> None:
            self.app.jobs.later(lambda: self.note.set(line + "\n\nTacoSort waits here while you approve it."))
            for word in line.split():
                if word.startswith("https://"):
                    webbrowser.open(word.rstrip(".,"))
                    break

        def work() -> tuple[str | None, int]:
            cli._auth().microsoft_device_login(acct, log=show)
            problem, folders = try_login(acct)
            if problem:
                cli._auth().delete_account_secrets(acct.id)
            return problem, folders

        def done(result: Any, error: BaseException | None) -> None:
            problem, folders = result if error is None else (plain_error(error), 0)
            if problem:
                self.note.set(f"The Microsoft sign-in didn't finish: {for_app(problem)}")
                return
            self.app.cfg.accounts.append(acct)
            save_config(self.app.cfg, self.app.path)
            self.app.reload()
            self.app.acct_note.set(f"Connected! {acct.email} has {folders} folders.")
            self.win.destroy()

        self.app.jobs.start(work, done)


# ---- start ------------------------------------------------------------------------------------------
def _set_icon(win: tk.Misc) -> None:
    with contextlib.suppress(tk.TclError, OSError):
        if sys.platform.startswith("win") and (ASSETS / "tacosort.ico").exists():
            win.iconbitmap(str(ASSETS / "tacosort.ico"))     # .ico alone: adding iconphoto blanks it
        elif (ASSETS / "tacosort.png").exists():
            win.iconphoto(True, tk.PhotoImage(file=str(ASSETS / "tacosort.png")))


def _windows_setup() -> None:
    """Own taskbar button and icon, and sharp text on high-resolution screens."""
    if not sys.platform.startswith("win"):
        return
    import ctypes

    with contextlib.suppress(Exception):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)


def main(argv: list[str] | None = None) -> int:
    """Open the window. `--config PATH` uses another settings file."""
    args = list(sys.argv[1:] if argv is None else argv)
    path = default_config_path()
    if "--config" in args:
        i = args.index("--config")
        if i + 1 < len(args):
            path = Path(args[i + 1]).expanduser()
    old_umask = os.umask(0o077) if os.name == "posix" else None   # same privacy as the command
    try:
        _windows_setup()
        root = tk.Tk()
        app = App(root, path)
        if not app.ok:
            root.destroy()
            return 1
        root.mainloop()
    finally:
        if old_umask is not None:
            os.umask(old_umask)
    return 0


if __name__ == "__main__":
    sys.exit(main())
