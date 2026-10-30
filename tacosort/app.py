"""The TacoSort app: the same TacoSort in a window, for people who'd rather not type commands.

Three tabs, in the order a new person needs them:
  Accounts  add an email account (with the steps to get an app password), test it, switch it off
  Options   tick boxes for every setting, the folders to use, and the background schedule
  Sort      learn from your folders, preview (moves nothing), sort now, undo, see every move

Mail work runs on a helper thread so the window never freezes; only the main thread touches the
window (results come back through a queue). The settings file, the keychain, the memory and the log
are the same ones the `tacosort` command uses, so both can be used side by side.
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
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from . import __version__, cli, options
from .backend import BackendError
from .config import (
    ConfigError,
    account_id_problem,
    data_dir,
    default_config_path,
    load_config,
    save_config,
)
from .models import AccountConfig, AppConfig

ASSETS = Path(__file__).parent / "assets"
APP_ID = "TacoSort.App"
PAD = 8
MAX_SORT_ROUNDS = 100            # "Sort my Inbox now" repeats runs of max_moves_per_run until done
SAFETY_LIMIT_TEXT = "reached the safety limit"

Log = Callable[[str], None]


# ---- mail work (no window code: these run on the helper thread) ------------------------------------
def try_login(acct: AccountConfig, password: str | None = None) -> tuple[str | None, int]:
    """(problem or None, number of folders). With `password`, that one is tried (nothing is saved)."""
    backend = (cli.make_backend(acct) if password is None
               else cli.make_backend(acct, secret_getter=lambda _id: password))
    try:
        backend.connect()
        return None, len(backend.list_folders())
    except BackendError as e:
        return str(e) or "The login didn't work.", 0
    except Exception as e:  # noqa: BLE001 - network trouble and the like: never show internals or secrets
        return f"Couldn't reach the mail server ({type(e).__name__}).", 0
    finally:
        cli._close(backend)


def enabled_accounts(cfg: AppConfig) -> list[AccountConfig]:
    return [a for a in cfg.accounts if a.enabled]


def learn(cfg: AppConfig, log: Log) -> Any:
    accounts = enabled_accounts(cfg)
    store = cli.open_store(cfg)
    try:
        return cli._engine().learn_from_folders(cli._only(cfg, accounts), store, cli._backends(accounts),
                                                log=log)
    finally:
        cli._close(store)


def preview(cfg: AppConfig, log: Log) -> tuple[list[Any], int, int, list[str]]:
    """(planned moves, messages looked at, kept, problems). Moves nothing."""
    moves: list[Any] = []
    scanned = kept = 0
    problems: list[str] = []
    ok: list[str] = []
    store = cli.open_store(cfg)
    try:
        for acct in enabled_accounts(cfg):
            log(f"Looking at {acct.email} (nothing will be moved)...")
            rep = cli._engine().run_once(cli._only(cfg, [acct]), store, cli._backends([acct]),
                                         dry_run=True, backlog=True, log=log)
            if not rep.run_id:
                problems.append("TacoSort is busy with another run right now; try again in a minute.")
                continue
            moves.extend(rep.moves)
            scanned += rep.scanned
            kept += rep.kept
            problems.extend(rep.errors)
            if not rep.errors:
                ok.append(acct.id)
    finally:
        cli._close(store)
    if ok:
        cli.mark_previewed(cfg, ok)
    return moves, scanned, kept, problems


def sort_now(cfg: AppConfig, log: Log) -> tuple[int, list[str]]:
    """Sort everything in the Inbox, in runs of max_moves_per_run until nothing is left. (moved, problems)"""
    accounts = enabled_accounts(cfg)
    moved = 0
    problems: list[str] = []
    store = cli.open_store(cfg)
    try:
        for _ in range(MAX_SORT_ROUNDS):
            limit_hit = []

            def tell(line: str, _hit: list = limit_hit) -> None:
                if SAFETY_LIMIT_TEXT in line:
                    _hit.append(line)
                log(line)

            rep = cli._engine().run_once(cli._only(cfg, accounts), store, cli._backends(accounts),
                                         dry_run=False, backlog=True, log=tell)
            if not rep.run_id:
                problems.append("TacoSort is busy with another run right now; try again in a minute.")
                break
            moved += rep.moved
            problems.extend(rep.errors)
            if not limit_hit or rep.errors:
                break
            log("More to sort: starting the next round...")
    finally:
        cli._close(store)
    cli.mark_previewed(cfg, [a.id for a in accounts])
    return moved, problems


def undo_last(cfg: AppConfig, log: Log) -> int:
    accounts = cfg.accounts
    store = cli.open_store(cfg)
    try:
        return cli._engine().undo(cfg, store, cli._backends(accounts), log=log)
    finally:
        cli._close(store)


def write_report(cfg: AppConfig) -> Path:
    from .report import build_report

    store = cli._store_if_exists(cfg)
    try:
        html = build_report(store, cfg)
    finally:
        cli._close(store)
    out = data_dir(cfg) / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def schedule_is_on() -> tuple[bool, str]:
    try:
        text = cli._schedule().status()
    except Exception as e:  # noqa: BLE001 - the window still works without knowing
        return False, f"Couldn't check the background schedule ({type(e).__name__})."
    return text.startswith("The background schedule is on"), text


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
                           "tab (with the same email address)", text)


def plain_error(e: BaseException) -> str:
    if isinstance(e, (BackendError, ConfigError, cli.UserError)):
        return for_app(str(e))
    if isinstance(e, ImportError):
        return f"Part of TacoSort is missing ({e.name or e}). {cli.REINSTALL_HINT}"
    return f"Something went wrong: {type(e).__name__}: {e}"


def wrapped(parent: tk.Misc, text: str, width: int = 520, **kw: Any) -> ttk.Label:
    return ttk.Label(parent, text=text, wraplength=width, justify="left", **kw)


class ScrollFrame(ttk.Frame):
    """A frame whose contents scroll with the mouse wheel when the window is too small."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, event: tk.Event) -> None:
        if self.winfo_ismapped() and str(event.widget).startswith(str(self)):
            self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")


# ---- the window -------------------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk, config_path: Path):
        self.root = root
        self.path = config_path
        self.cfg = AppConfig()
        self.action_buttons: list[ttk.Button] = []
        root.title("TacoSort")
        root.geometry("1000x780")
        root.minsize(760, 520)
        _set_icon(root)
        self.jobs = Jobs(root, self._set_busy)

        head = ttk.Frame(root, padding=(PAD * 2, PAD * 2, PAD * 2, 0))
        head.pack(fill="x")
        ttk.Label(head, text="TacoSort", font=("TkDefaultFont", 18, "bold")).pack(side="left")
        ttk.Label(head, text=f"  version {__version__}").pack(side="left", pady=(8, 0))
        self.progress = ttk.Progressbar(head, mode="indeterminate", length=160)
        wrapped(root, "Sorts robot mail (receipts, newsletters, promotions...) into folders, so your Inbox keeps "
                      "mail from people. It never deletes anything, never marks mail as read, and every move "
                      "can be undone. Passwords stay in your computer's keychain.", width=900,
                padding=(PAD * 2, 4, PAD * 2, PAD)).pack(fill="x")

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=PAD * 2, pady=(0, PAD))
        self.accounts_tab = ttk.Frame(self.tabs, padding=PAD * 2)
        self.options_tab = ttk.Frame(self.tabs)
        self.sort_tab = ttk.Frame(self.tabs, padding=PAD * 2)
        self.tabs.add(self.accounts_tab, text="  1. Accounts  ")
        self.tabs.add(self.options_tab, text="  2. Options  ")
        self.tabs.add(self.sort_tab, text="  3. Sort  ")

        self.status = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.status, padding=(PAD * 2, 0, PAD * 2, PAD)).pack(fill="x")

        self._build_accounts()
        self._build_options()
        self._build_sort()
        self.ok = self.reload()        # False: the settings file is broken, so the window must not save over it
        root.protocol("WM_DELETE_WINDOW", self._close)
        if not self.cfg.accounts:
            self.tabs.select(self.accounts_tab)

    # ---- shared ---------------------------------------------------------------------------------
    def reload(self) -> bool:
        try:
            self.cfg = load_config(self.path)
        except ConfigError as e:
            messagebox.showerror("TacoSort", f"There's a problem with your settings file ({self.path}):\n\n{e}")
            return False
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

    def _button(self, parent: tk.Misc, text: str, command: Callable[[], None], **kw: Any) -> ttk.Button:
        b = ttk.Button(parent, text=text, command=command, **kw)
        self.action_buttons.append(b)
        return b

    def _log(self, line: str) -> None:
        """Safe from the helper thread: the line is added to the Sort tab's log on the window's thread."""
        self.jobs.later(lambda: self._append_log(line))

    def _append_log(self, line: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", for_app(line).rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _refresh_status(self) -> None:
        n = len(self.cfg.accounts)
        if not n:
            self.status.set("No email accounts yet: start with Add account.")
            return
        on, _text = schedule_is_on()
        auto = "Sorting automatically in the background." if on else "Background sorting is off."
        self.status.set(f"{n} account(s). {auto}")

    def _close(self) -> None:
        if self.jobs.busy:
            messagebox.showinfo("TacoSort", "TacoSort is still working. Please wait until it finishes, so no "
                                            "move is left half-done.")
            return
        self.root.destroy()

    # ---- Accounts tab ----------------------------------------------------------------------------
    def _build_accounts(self) -> None:
        tab = self.accounts_tab
        wrapped(tab, "Add each email account you want sorted. TacoSort checks the login works before it saves "
                     "anything.", width=860).pack(anchor="w", pady=(0, PAD))
        cols = ("name", "email", "provider", "status")
        self.acct_list = ttk.Treeview(tab, columns=cols, show="headings", height=8, selectmode="browse")
        for col, title, width in zip(cols, ("Short name", "Email", "Provider", "Status"), (160, 320, 140, 120)):
            self.acct_list.heading(col, text=title, anchor="w")
            self.acct_list.column(col, width=width, anchor="w")
        self.acct_list.pack(fill="both", expand=True)
        row = ttk.Frame(tab, padding=(0, PAD, 0, 0))
        row.pack(fill="x")
        self._button(row, "Add account...", self.add_account).pack(side="left")
        self._button(row, "Test login", self.test_account).pack(side="left", padx=PAD)
        self._button(row, "Turn on / off", self.toggle_account).pack(side="left")
        self._button(row, "Remove", self.remove_account).pack(side="left", padx=PAD)
        self.acct_note = tk.StringVar()
        wrapped(tab, "", width=860, textvariable=self.acct_note).pack(anchor="w", pady=(PAD, 0))

    def _fill_accounts(self) -> None:
        self.acct_list.delete(*self.acct_list.get_children())
        providers = cli._providers().PROVIDERS
        for a in self.cfg.accounts:
            name = providers[a.provider].name if a.provider in providers else a.provider
            self.acct_list.insert("", "end", iid=a.id, values=(a.id, a.email, name, "On" if a.enabled else "Off"))
        if not self.cfg.accounts:
            self.acct_note.set("No accounts yet. Click Add account... to start.")

    def _selected(self) -> AccountConfig | None:
        sel = self.acct_list.selection()
        acct = next((a for a in self.cfg.accounts if sel and a.id == sel[0]), None)
        if acct is None:
            messagebox.showinfo("TacoSort", "Click an account in the list first.")
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

    # ---- Options tab -----------------------------------------------------------------------------
    def _build_options(self) -> None:
        scroll = ScrollFrame(self.options_tab)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        left = ttk.Frame(body, padding=PAD * 2)
        right = ttk.Frame(body, padding=PAD * 2)
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew")

        self.opt_vars: dict[str, tuple[tk.BooleanVar, tk.StringVar | None]] = {}
        for title, group in options.GROUPS:
            box = ttk.LabelFrame(left, text=title, padding=PAD)
            box.pack(fill="x", pady=(0, PAD))
            for o in group:
                ticked = tk.BooleanVar()
                number = tk.StringVar() if o.unit else None
                line = ttk.Frame(box)
                line.pack(fill="x", anchor="w")
                ttk.Checkbutton(line, text=o.label, variable=ticked).pack(side="left")
                if number is not None:
                    ttk.Spinbox(line, from_=o.low, to=o.high, textvariable=number, width=6).pack(
                        side="left", padx=(6, 4))
                    ttk.Label(line, text=o.unit).pack(side="left")
                if o.help:
                    wrapped(box, o.help, width=400, foreground="#555").pack(anchor="w", padx=(24, 0),
                                                                             pady=(0, 4))
                self.opt_vars[o.field] = (ticked, number)

        cats = ttk.LabelFrame(right, text="Folders to sort into", padding=PAD)
        cats.pack(fill="x", pady=(0, PAD))
        wrapped(cats, "Untick a folder and that kind of mail stays in the Inbox.", width=400,
                foreground="#555").pack(anchor="w", pady=(0, 4))
        self.cats_box = ttk.Frame(cats)
        self.cats_box.pack(fill="x")
        self.cat_vars: dict[str, tk.BooleanVar] = {}

        bg = ttk.LabelFrame(right, text="In the background", padding=PAD)
        bg.pack(fill="x", pady=(0, PAD))
        self.sched_var = tk.BooleanVar()
        self.interval_var = tk.StringVar()
        line = ttk.Frame(bg)
        line.pack(fill="x")
        ttk.Checkbutton(line, text="Sort new mail automatically every", variable=self.sched_var).pack(side="left")
        ttk.Spinbox(line, from_=5, to=1439, textvariable=self.interval_var, width=6).pack(side="left", padx=(6, 4))
        ttk.Label(line, text="minutes").pack(side="left")
        wrapped(bg, "Runs while you are logged in to this computer, even with this window closed.", width=400,
                foreground="#555").pack(anchor="w", padx=(24, 0))

        save_row = ttk.Frame(right)
        save_row.pack(fill="x", pady=(PAD, 0))
        self._button(save_row, "Save settings", self.save_options).pack(side="left")
        self._button(save_row, "Undo changes", self.reload).pack(side="left", padx=PAD)
        self.opt_note = tk.StringVar()
        wrapped(right, "", width=400, textvariable=self.opt_note).pack(anchor="w", pady=(PAD, 0))

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
            ttk.Checkbutton(self.cats_box, text=folder, variable=var).pack(anchor="w")
            if about:
                wrapped(self.cats_box, about, width=380, foreground="#555").pack(anchor="w", padx=(24, 0))
            self.cat_vars[key] = var
        self.interval_var.set(str(values.interval_minutes))
        self.sched_var.set(schedule_is_on()[0])
        self.opt_note.set("")

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
        # Ticked: set the task up again every time, so it uses this interval and this copy of the app
        # (after the app file was moved, opening it and saving once fixes the schedule).
        if want_on and not enabled_accounts(new):
            self.opt_note.set("Saved. Add an account before turning on background sorting.")
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
                self.opt_note.set(f"Saved, but the background schedule couldn't be changed: {plain_error(error)}")
            self.sched_var.set(schedule_is_on()[0])

        self.jobs.start(work, done)

    # ---- Sort tab --------------------------------------------------------------------------------
    def _build_sort(self) -> None:
        tab = self.sort_tab
        wrapped(tab, "Start with a preview: it shows where each email in your Inbox would go and moves nothing. "
                     "Happy with it? Sort my Inbox now moves them. Undo last sort puts them back.",
                width=900).pack(anchor="w", pady=(0, PAD))
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(0, PAD))
        self._button(row, "Learn from my folders", self.learn).pack(side="left")
        self._button(row, "Preview (moves nothing)", self.preview).pack(side="left", padx=PAD)
        self._button(row, "Sort my Inbox now", self.sort_now).pack(side="left")
        self._button(row, "Undo last sort", self.undo).pack(side="left", padx=PAD)
        self._button(row, "Show every move", self.report).pack(side="left")

        self.summary = tk.StringVar(value="No preview yet.")
        ttk.Label(tab, textvariable=self.summary, font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        panes = ttk.PanedWindow(tab, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(4, 0))
        table = ttk.Frame(panes)
        cols = ("account", "sender", "subject", "folder", "why")
        self.moves = ttk.Treeview(table, columns=cols, show="headings", height=10)
        for col, title, width in zip(cols, ("Account", "From", "Subject", "Goes to", "Why"),
                                     (100, 200, 280, 170, 260)):
            self.moves.heading(col, text=title, anchor="w")
            self.moves.column(col, width=width, anchor="w")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.moves.yview)
        self.moves.configure(yscrollcommand=bar.set)
        self.moves.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        panes.add(table, weight=3)
        logs = ttk.Frame(panes)
        self.log_box = tk.Text(logs, height=6, wrap="word", state="disabled", relief="flat",
                               background="#f6f6f6")
        log_bar = ttk.Scrollbar(logs, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=log_bar.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        log_bar.pack(side="right", fill="y")
        panes.add(logs, weight=1)

    def _need_accounts(self) -> bool:
        if enabled_accounts(self.cfg):
            return True
        messagebox.showinfo("TacoSort", "Add an email account first (the Accounts tab).")
        self.tabs.select(self.accounts_tab)
        return False

    def _show_moves(self, moves: list[Any]) -> None:
        self.moves.delete(*self.moves.get_children())
        for m in moves:
            self.moves.insert("", "end", values=(m.account, m.sender, m.subject, m.to_folder, m.reason))

    def learn(self) -> None:
        if not self._need_accounts():
            return
        cfg = self.cfg
        self._log("Learning from the folders you already have (nothing is moved)...")

        def done(rep: Any, error: BaseException | None) -> None:
            if error is not None:
                self._append_log(f"Problem: {plain_error(error)}")
                return
            self._append_log(f"Looked at {rep.messages_scanned} messages in {rep.folders_scanned} folders. "
                             f"Learned {rep.rules_learned} sender rules and found {rep.contacts_found} people "
                             "you write to.")
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
        self.win = tk.Toplevel(app.root)
        self.win.title("Add an email account")
        self.win.transient(app.root)
        self.win.geometry("620x560")
        _set_icon(self.win)
        self.body = ttk.Frame(self.win, padding=PAD * 2)
        self.body.pack(fill="both", expand=True)
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
        self._clear()
        ttk.Label(self.body, text="Your email address").pack(anchor="w")
        entry = ttk.Entry(self.body, textvariable=self.email, width=50)
        entry.pack(anchor="w", pady=(2, PAD))
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self._next())
        ttk.Button(self.body, text="Next", command=self._next).pack(anchor="w")
        wrapped(self.body, "", width=560, textvariable=self.note, foreground="#a00").pack(anchor="w", pady=PAD)

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
        self._clear()
        p = self.provider
        b = self.body
        if self.existing is not None:
            wrapped(b, f"{self.existing.email} is already set up (as '{self.existing.id}'). You can give it a new "
                       "app password here.", width=560).pack(anchor="w", pady=(0, PAD))
        elif p.key == "custom":
            wrapped(b, "TacoSort doesn't recognise this email provider, so it needs the IMAP server details. "
                       "Your provider's help pages list them (look for 'IMAP settings').", width=560).pack(
                anchor="w", pady=(0, PAD))
            grid = ttk.Frame(b)
            grid.pack(anchor="w", pady=(0, PAD))
            ttk.Label(grid, text="IMAP server").grid(row=0, column=0, sticky="w")
            ttk.Entry(grid, textvariable=self.host, width=32).grid(row=0, column=1, sticky="w", padx=6)
            ttk.Label(grid, text="Port").grid(row=1, column=0, sticky="w")
            ttk.Entry(grid, textvariable=self.port, width=8).grid(row=1, column=1, sticky="w", padx=6)
            ttk.Checkbutton(grid, text="Uses SSL/TLS (almost always yes for port 993)",
                            variable=self.ssl).grid(row=2, column=1, sticky="w", padx=6)
        else:
            ttk.Label(b, text=f"This looks like a {p.name} account.", font=("TkDefaultFont", 10, "bold")).pack(
                anchor="w", pady=(0, PAD))
        if self.existing is None:
            line = ttk.Frame(b)
            line.pack(anchor="w", pady=(0, PAD))
            ttk.Label(line, text="Short name for this account").pack(side="left")
            ttk.Entry(line, textvariable=self.short, width=24).pack(side="left", padx=6)

        row = ttk.Frame(b)               # packed below the explanation; its buttons are made inside it
        if p.auth == "password":
            wrapped(b, f"TacoSort needs an app password for {p.name}: a separate password just for TacoSort, "
                       "which you can cancel at any time without changing your normal password.",
                    width=560).pack(anchor="w")
            notes = (getattr(p, "notes", "") or "").strip()
            if notes:
                wrapped(b, notes, width=560, foreground="#444").pack(anchor="w", pady=(4, 0))
            if p.app_password_url:
                ttk.Button(b, text="Open the page where you make one",
                           command=lambda: webbrowser.open(p.app_password_url)).pack(anchor="w", pady=PAD)
            line = ttk.Frame(b)
            line.pack(anchor="w", pady=(0, PAD))
            ttk.Label(line, text="App password").pack(side="left")
            ttk.Entry(line, textvariable=self.password, show="•", width=30).pack(side="left", padx=6)
            self.save_button = ttk.Button(row, text="Test and save", command=self._save_password)
        elif p.auth == "oauth2-microsoft":
            wrapped(b, "Microsoft doesn't let mail programs sign in to Outlook.com / Hotmail with a password. "
                       "TacoSort signs in the Microsoft way: you approve it once in your browser. For that it "
                       "needs a free 'client ID' you create once on Microsoft's website (docs/outlook-oauth.md "
                       "in the TacoSort guide walks you through it, about 10 minutes).", width=560).pack(anchor="w")
            line = ttk.Frame(b)
            line.pack(anchor="w", pady=PAD)
            ttk.Label(line, text="Client ID").pack(side="left")
            ttk.Entry(line, textvariable=self.client_id, width=40).pack(side="left", padx=6)
            self.save_button = ttk.Button(row, text="Sign in with Microsoft", command=self._save_microsoft)
        else:
            wrapped(b, f"{p.name} needs a sign-in type ({p.auth}) this window can't set up yet. Use "
                       "`tacosort accounts add` in a terminal (see docs/providers.md).", width=560).pack(anchor="w")
            self.save_button = ttk.Button(row, text="Close", command=self.win.destroy)
        row.pack(anchor="w", pady=PAD)
        self.save_button.pack(side="left")
        ttk.Button(row, text="Back", command=self._step_email).pack(side="left", padx=PAD)
        wrapped(b, "", width=560, textvariable=self.note).pack(anchor="w")

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
                                   + ("Next: the Options tab, then Preview on the Sort tab." if is_new
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
