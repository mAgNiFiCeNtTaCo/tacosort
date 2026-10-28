"""The `mailtidy` command. Every message here is written for people who don't program.

The parts that do the real work (imap, classify, engine, schedule) are imported only inside the
commands that need them, so `mailtidy --help` stays fast and each part can be tested on its own.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import getpass
import importlib
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .backend import BackendError
from .config import (
    RESERVED_IDS,
    ConfigError,
    account_id_problem,
    config_problems,
    data_dir,
    default_config_path,
    default_data_dir,
    load_config,
    make_private,
    save_config,
    state_path,
)
from .models import AccountConfig, Address, AppConfig, Message

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2
PREVIEW_FILE = "previewed.json"
MAX_INTERVAL = 1439          # the scheduler's limit: just under a day
REINSTALL_HINT = "Try installing MailTidy again (the Install steps are in the README)."


# ---- lazily loaded parts (tests replace these) --------------------------------------------------------

def _module(name: str) -> Any:
    return importlib.import_module(f"mailtidy.{name}")


def _engine() -> Any:
    return _module("engine")


def _schedule() -> Any:
    return _module("schedule")


def _providers() -> Any:
    return _module("imap.providers")


def _auth() -> Any:
    return _module("imap.auth")


def make_backend(account: AccountConfig, secret_getter: Callable[[str], str | None] | None = None) -> Any:
    """A real IMAP connection for `account` (not connected yet). `secret_getter` overrides the keychain,
    so a new password can be tested before it is saved."""
    cls = _module("imap.client").ImapBackend
    return cls(account) if secret_getter is None else cls(account, secret_getter=secret_getter)


def open_store(cfg: AppConfig) -> Any:
    """MailTidy's memory (SQLite) for this config."""
    path = state_path(cfg)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return _module("store").Store(path)


def make_classifier(cfg: AppConfig, learned: Any, contacts: set[str]) -> Any:
    return _module("classify.pipeline").Classifier(cfg, learned, contacts)


# ---- small helpers ----------------------------------------------------------------------------------

def say(*parts: Any) -> None:
    print(*parts)


def warn(text: str) -> None:
    print(text, file=sys.stderr)


def ask(prompt: str, default: str = "") -> str:
    """Ask a question; Enter gives `default`."""
    try:
        answer = input(prompt)
    except EOFError:
        answer = ""
    return answer.strip() or default


def ask_yes(prompt: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = ask(f"{prompt} {hint} ").lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        say("Please answer y (yes) or n (no).")


def _short(text: Any, width: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[: max(width - 3, 1)] + "..."


def print_table(headers: list[str], rows: list[list[Any]], widths: list[int]) -> None:
    cells = [[_short(c, w) for c, w in zip(row, widths)] for row in rows]
    cols = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    say("  ".join(h.ljust(cols[i]) for i, h in enumerate(headers)).rstrip())
    say("  ".join("-" * c for c in cols))
    for r in cells:
        say("  ".join(v.ljust(cols[i]) for i, v in enumerate(r)).rstrip())


def _close(obj: Any) -> None:
    fn = getattr(obj, "close", None)
    if callable(fn):
        with contextlib.suppress(Exception):   # closing must never hide the real problem
            fn()


@dataclasses.dataclass
class Ctx:
    """What every command needs: where the settings file is, and the settings."""

    path: Path
    quiet: bool = False
    _cfg: AppConfig | None = None

    @property
    def cfg(self) -> AppConfig:
        if self._cfg is None:
            self._cfg = load_config(self.path)
        return self._cfg

    def save(self) -> None:
        save_config(self.cfg, self.path)

    def log(self, *parts: Any) -> None:
        if not self.quiet:
            print(*parts)


class UserError(Exception):
    """Stop the command with a plain-English message (exit code 1)."""


def _need_accounts(ctx: Ctx) -> AppConfig:
    cfg = ctx.cfg
    if not cfg.accounts:
        raise UserError("No email accounts are set up yet. Run `mailtidy setup` to add one.")
    return cfg


def _pick_accounts(cfg: AppConfig, account_id: str | None) -> list[AccountConfig]:
    if account_id:
        for a in cfg.accounts:
            if a.id == account_id:
                return [a]
        ids = ", ".join(a.id for a in cfg.accounts) or "none"
        raise UserError(f"There is no account called '{account_id}'. Your accounts: {ids}.")
    accounts = [a for a in cfg.accounts if a.enabled]
    if not accounts:
        raise UserError("All your accounts are switched off (enabled: no) in the settings file.")
    return accounts


def _only(cfg: AppConfig, accounts: list[AccountConfig]) -> AppConfig:
    """A copy of the settings limited to these accounts."""
    return dataclasses.replace(cfg, accounts=list(accounts))


def _backends(accounts: list[AccountConfig]) -> dict[str, Any]:
    return {a.id: make_backend(a) for a in accounts}


# ---- "has this account been previewed?" (safety gate before the first real move) ------------------------

def _preview_path(cfg: AppConfig) -> Path:
    return data_dir(cfg) / PREVIEW_FILE


def previewed_accounts(cfg: AppConfig) -> set[str]:
    try:
        data = json.loads(_preview_path(cfg).read_text(encoding="utf-8"))
        return set(data.get("accounts", []))
    except (OSError, ValueError, AttributeError):
        return set()


def mark_previewed(cfg: AppConfig, ids: list[str], *, remove: bool = False) -> None:
    current = previewed_accounts(cfg)
    current = current - set(ids) if remove else current | set(ids)
    path = _preview_path(cfg)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps({"accounts": sorted(current)}, indent=2), encoding="utf-8")


def _gate(cfg: AppConfig, accounts: list[AccountConfig], yes: bool) -> list[AccountConfig]:
    """Drop accounts that were never previewed (unless --yes), explaining why."""
    if yes:
        return accounts
    done = previewed_accounts(cfg)
    missing = [a for a in accounts if a.id not in done]
    if missing:
        names = ", ".join(a.id for a in missing)
        warn(f"Not moving anything in {names} yet: MailTidy hasn't shown you a preview for "
             f"{'this account' if len(missing) == 1 else 'these accounts'}.\n"
             "Run `mailtidy preview` first to see what would happen (nothing is moved).\n"
             "When you're happy with it, run this again, or add --yes to skip the preview.")
    return [a for a in accounts if a.id in done]


# ---- the work: learn / preview / run (shared by commands and the setup wizard) --------------------------

def do_learn(ctx: Ctx, accounts: list[AccountConfig]) -> int:
    cfg = ctx.cfg
    store = open_store(cfg)
    try:
        ctx.log("Learning from the folders you already have (nothing is moved)...")
        rep = _engine().learn_from_folders(_only(cfg, accounts), store, _backends(accounts), log=ctx.log)
    finally:
        _close(store)
    ctx.log(f"Looked at {rep.messages_scanned} messages in {rep.folders_scanned} folders. "
            f"Learned {rep.rules_learned} sender rules and found {rep.contacts_found} people you write to.")
    for e in rep.errors:
        warn(f"Problem: {e}")
    return EXIT_ERROR if rep.errors else EXIT_OK


def do_preview(ctx: Ctx, accounts: list[AccountConfig], limit: int = 50) -> int:
    """Dry run over the Inbox backlog, one account at a time, and show the planned moves."""
    cfg = ctx.cfg
    store = open_store(cfg)
    moves: list[Any] = []
    scanned = kept = 0
    ok_ids: list[str] = []
    errors: list[str] = []
    try:
        for acct in accounts:
            say(f"Looking at {acct.email} (nothing will be moved)...")
            rep = _engine().run_once(_only(cfg, [acct]), store, _backends([acct]),
                                     dry_run=True, backlog=True, log=ctx.log)
            if not rep.run_id:
                errors.append("MailTidy is busy with another run right now; try the preview again in a minute.")
                continue
            moves.extend(rep.moves)
            scanned += rep.scanned
            kept += rep.kept
            if rep.errors:
                errors.extend(rep.errors)
            else:
                ok_ids.append(acct.id)
    finally:
        _close(store)
    say("")
    if moves:
        rows = [[m.account, m.sender, m.subject, f"-> {m.to_folder}", m.reason] for m in moves[:limit]]
        print_table(["Account", "From", "Subject", "Would go to", "Why"], rows, [16, 28, 40, 18, 34])
        if len(moves) > limit:
            say(f"...and {len(moves) - limit} more (show more with --limit {len(moves)}).")
        say("")
    say(f"Preview: {len(moves)} of {scanned} messages would be moved into folders; {kept} would stay in the Inbox.")
    say("Nothing was changed. Every move MailTidy makes can be undone with `mailtidy undo`.")
    for e in errors:
        warn(f"Problem: {e}")
    if ok_ids:
        mark_previewed(cfg, ok_ids)
        say("To do it for real: `mailtidy run --backlog` (sorts the mail already there) "
            "or `mailtidy run` (only new mail from now on).")
    return EXIT_ERROR if errors else EXIT_OK


def do_run(ctx: Ctx, accounts: list[AccountConfig], *, dry_run: bool, backlog: bool, yes: bool) -> int:
    cfg = ctx.cfg
    if not dry_run:
        allowed = _gate(cfg, accounts, yes)
        if not allowed:
            return EXIT_ERROR
        accounts = allowed
    store = open_store(cfg)
    try:
        if not dry_run and not backlog:
            # Plain `run` (and the schedule) sorts only mail that arrives from now on; --backlog sorts the rest.
            _engine().start_from_now(_only(cfg, accounts), store, _backends(accounts), log=ctx.log)
        rep = _engine().run_once(_only(cfg, accounts), store, _backends(accounts),
                                 dry_run=dry_run, backlog=backlog, log=ctx.log)
    finally:
        _close(store)
    if rep.skipped or not rep.run_id:   # another run (the schedule or `watch`) holds the lock: nothing to do
        ctx.log("Another MailTidy run is busy right now, so this one was skipped. That's fine.")
        return EXIT_OK
    if not dry_run and yes:
        mark_previewed(cfg, [a.id for a in accounts])
    # The engine already logs a one-line summary; add the details people ask about next.
    if rep.moves and not ctx.quiet:
        for m in rep.moves[:20]:
            ctx.log(f"  {_short(m.sender, 30)}: {_short(m.subject, 50)} -> {m.to_folder}")
        if len(rep.moves) > 20:
            ctx.log(f"  ...and {len(rep.moves) - 20} more (see `mailtidy report`).")
    if not dry_run and rep.moved:
        ctx.log("Changed your mind? `mailtidy undo` puts the last run's mail back.")
    for e in rep.errors:
        warn(f"Problem: {e}")
    return EXIT_ERROR if rep.errors else EXIT_OK


# ---- account set-up (wizard and `accounts add`) --------------------------------------------------------

def _suggest_id(cfg: AppConfig, provider_key: str, email: str) -> str:
    base = provider_key if provider_key != "custom" else email.rsplit("@", 1)[-1].split(".")[0]
    base = re.sub(r"[^A-Za-z0-9._-]", "-", base).strip("-.")[:30] or "mail"
    if base.lower() in RESERVED_IDS:   # e.g. sam@anthropic.com: that name belongs to the AI key
        base = f"{base}-mail"
    taken = {a.id for a in cfg.accounts}
    if base not in taken:
        return base
    local = re.sub(r"[^A-Za-z0-9._-]", "-", email.split("@")[0]).strip("-.")[:20]
    candidate = f"{base}-{local}"[:40].rstrip("-.") if local else base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _ask_email() -> str | None:
    while True:
        email = ask("Your email address (or press Enter to stop): ")
        if not email:
            return None
        local, _, domain = email.partition("@")
        if local and "." in domain and " " not in email:
            return email
        say("That doesn't look like an email address. It should look like name@example.com.")


def _test_login(acct: AccountConfig, password: str | None = None) -> str | None:
    """Try to log in and list folders. Returns None when it worked, else a plain-English problem.

    With `password`, that password is tried instead of the one in the keychain (nothing is saved)."""
    backend = make_backend(acct) if password is None else make_backend(acct, secret_getter=lambda _id: password)
    try:
        backend.connect()
        folders = backend.list_folders()
        say(f"Connected! {acct.email} has {len(folders)} folders.")
        return None
    except BackendError as e:
        return str(e) or "The login didn't work."
    except Exception as e:  # noqa: BLE001 - network trouble and the like: never show internals or secrets
        return f"Couldn't reach the mail server ({type(e).__name__})."
    finally:
        _close(backend)


def _app_password_steps(provider: Any) -> None:
    name = getattr(provider, "name", "your provider")
    notes = (getattr(provider, "notes", "") or "").strip()
    url = getattr(provider, "app_password_url", "") or ""
    say("")
    say(f"MailTidy needs an *app password* for {name}: a separate password just for MailTidy, which you can")
    say("cancel at any time without changing your normal password.")
    if notes:
        say("")
        for line in notes.splitlines():
            say(f"  {line}")
    if url:
        say("")
        say(f"  Make one here: {url}")
    say("")


def _password_login(acct: AccountConfig, provider: Any, *, existing: bool = False) -> bool:
    """Ask for the app password and test it; only a password that works is saved in the keychain.

    True when the account works. For a new account the person may also choose to save it without a
    password. For an `existing` account a failing password never replaces the saved one."""
    auth = _auth()
    _app_password_steps(provider)
    while True:
        secret = getpass.getpass("Paste the app password here (it stays hidden while you type): ").strip()
        if not secret:
            if not ask_yes("No password entered. Try again?", True):
                return False
            continue
        if acct.provider not in ("custom", "protonmail-bridge"):
            secret = "".join(secret.split())   # app passwords are often shown in groups of four
        problem = _test_login(acct, password=secret)
        if problem is None:
            try:
                auth.set_secret(acct.id, secret)
            except BackendError as e:
                say(f"The password works, but it couldn't be saved in your keychain: {e}")
                return False
            finally:
                del secret
            return True
        del secret
        say(f"That didn't work: {problem}")
        if not ask_yes("Try a different password?", True):
            if existing:
                say("The password MailTidy already had is kept.")
                return False
            return ask_yes("Save this account anyway, without a password? (add one later with "
                           "`mailtidy accounts add`)", False)


def _microsoft_login(acct: AccountConfig, provider: Any) -> bool:
    say("")
    say("Microsoft no longer lets mail programs sign in to Outlook.com / Hotmail with a password.")
    say("MailTidy signs in the Microsoft way instead: you approve it once in your browser.")
    say("For that it needs a free 'client ID', which you create once on Microsoft's website.")
    say("docs/outlook-oauth.md walks you through it (about 10 minutes, no payment needed).")
    notes = (getattr(provider, "notes", "") or "").strip()
    if notes:
        say("")
        for line in notes.splitlines():
            say(f"  {line}")
    say("")
    client_id = ask("Paste your client ID (or press Enter to skip this account for now): ")
    if not client_id:
        say("Skipped. Come back with `mailtidy accounts add` once you have a client ID.")
        return False
    acct.oauth_client_id = client_id
    auth = _auth()
    try:
        auth.microsoft_device_login(acct, log=say)
    except BackendError as e:
        say(f"The Microsoft sign-in didn't finish: {e}")
        _forget_secrets(acct.id)
        return False
    problem = _test_login(acct)
    if problem:
        say(f"Signed in, but reading mail didn't work: {problem}")
        if ask_yes("Save this account anyway?", False):
            return True
        _forget_secrets(acct.id)   # the sign-in saved a token; don't leave it behind for an unsaved account
        return False
    return True


def _forget_secrets(account_id: str) -> bool:
    """Delete every keychain entry of an account. False (after a plain warning) when that failed."""
    try:
        _auth().delete_account_secrets(account_id)
        return True
    except (BackendError, OSError) as e:   # auth.CredentialError is a BackendError
        warn(f"Couldn't remove the saved sign-in from your keychain ({e}); you can delete the "
             f"'mailtidy' entries for '{account_id}' by hand.")
        return False


def add_account(ctx: Ctx) -> AccountConfig | None:
    """Ask for one account, check it works, save it. Returns it, or None when skipped."""
    cfg = ctx.cfg
    email = _ask_email()
    if not email:
        return None
    existing = next((a for a in cfg.accounts if a.email.lower() == email.lower()), None)
    providers = _providers()
    if existing:
        say(f"{email} is already set up (as '{existing.id}').")
        provider = providers.PROVIDERS.get(existing.provider) or providers.detect_provider(email)
        if (existing.auth == "password" and ask_yes("Do you want to give it a new app password?", False)
                and _password_login(existing, provider, existing=True)):
            say("Password updated.")
        return None

    provider = providers.detect_provider(email)
    host, port, ssl = provider.host, provider.port, provider.ssl
    if provider.key == "custom":
        say("I don't recognise this email provider, so I need its IMAP server details.")
        say("Your provider's help pages list them (look for 'IMAP settings').")
        host = ask("IMAP server name (for example imap.example.com): ")
        while not host:
            host = ask("Please type the IMAP server name: ")
        port_text = ask("Port [993]: ", "993")
        while not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            port_text = ask("The port is a number, usually 993. Port [993]: ", "993")
        port = int(port_text)
        ssl = ask_yes("Does it use SSL/TLS on this port (almost always yes for 993)?", port == 993)
    else:
        say(f"This looks like a {provider.name} account.")

    suggested = _suggest_id(cfg, provider.key, email)
    while True:
        acct_id = ask(f"Short name for this account [{suggested}]: ", suggested)
        problem = account_id_problem(acct_id)
        if problem:
            say(problem)
        elif any(a.id == acct_id for a in cfg.accounts):
            say(f"'{acct_id}' is already used by another account. Pick another name.")
        else:
            break

    acct = AccountConfig(id=acct_id, email=email, provider=provider.key, host=host, port=port, ssl=ssl,
                         auth=provider.auth)
    if acct.auth == "oauth2-microsoft":
        works = _microsoft_login(acct, provider)
    elif acct.auth == "password":
        works = _password_login(acct, provider)
    else:
        say(f"{provider.name} needs a sign-in type ({acct.auth}) the wizard can't set up yet; "
            "see docs/providers.md.")
        works = False
    if not works:
        say("This account was not saved.")
        return None
    cfg.accounts.append(acct)
    ctx.save()
    say(f"Saved '{acct.id}' to {ctx.path}.")
    return acct


# ---- commands --------------------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace, ctx: Ctx) -> int:
    cfg = ctx.cfg
    say("Welcome to MailTidy!")
    say("MailTidy sorts automated mail (receipts, newsletters, promotions...) into folders, so your Inbox")
    say("keeps only mail from people. It never deletes anything, never marks mail as read, and every move")
    say("can be undone. Your password stays in your computer's keychain, never in a file.")
    say("")
    if cfg.accounts:
        say("Accounts already set up: " + ", ".join(f"{a.id} ({a.email})" for a in cfg.accounts))
        more = ask_yes("Add another account?", False)
    else:
        more = True
    added: list[AccountConfig] = []
    while more:
        acct = add_account(ctx)
        if acct:
            added.append(acct)
        say("")
        more = ask_yes("Add another account?", False)
    if not ctx.path.exists():
        ctx.save()   # an empty settings file, so `config edit` and friends have something to show
    accounts = [a for a in cfg.accounts if a.enabled]
    if not accounts:
        say("No accounts yet. Run `mailtidy setup` again when you're ready.")
        return EXIT_OK

    say("")
    if ask_yes("Learn from the folders you already have, so MailTidy follows your own system?", True):
        do_learn(ctx, accounts)
        say("")
    if ask_yes("See a preview of what MailTidy would do with the mail in your Inbox? (nothing is moved)", True):
        do_preview(ctx, accounts)
        say("")
        if ask_yes("Sort the mail in your Inbox like that now? (every move can be undone)", False):
            do_run(ctx, accounts, dry_run=False, backlog=True, yes=True)
            say("")
    minutes = cfg.interval_minutes
    if ask_yes(f"Keep your mail tidy automatically, every {minutes} minutes in the background?", True):
        if not previewed_accounts(cfg) & {a.id for a in accounts}:
            say("Note: background runs only move mail in accounts you've previewed. Run `mailtidy preview` soon.")
        try:
            say(_schedule().install(minutes, ctx.path.resolve()))
        except (RuntimeError, ValueError, OSError) as e:   # schedule.ScheduleError is a RuntimeError
            warn(f"Couldn't turn on the background schedule: {e}")
    if cfg.ai.enabled:
        say("")
        say("The AI helper is switched on in your settings. Save your Anthropic API key with")
        say("`mailtidy config ai-key`, so the background schedule can use it too.")
    say("")
    say("All set. Handy commands:")
    say("  mailtidy status    what MailTidy has been doing")
    say("  mailtidy preview   what it would do right now")
    say("  mailtidy undo      put the last run's mail back")
    say("  mailtidy report    a page listing every move")
    return EXIT_OK


def cmd_accounts(args: argparse.Namespace, ctx: Ctx) -> int:
    action = args.accounts_action
    if action == "add":
        return EXIT_OK if add_account(ctx) else EXIT_ERROR
    cfg = _need_accounts(ctx)
    if action == "list":
        rows = [[a.id, a.email, a.provider, a.auth, "yes" if a.enabled else "no", ", ".join(a.watch_folders)]
                for a in cfg.accounts]
        print_table(["Id", "Email", "Provider", "Sign-in", "On", "Watches"], rows, [20, 34, 18, 18, 3, 24])
        return EXIT_OK
    if action == "remove":
        acct = _pick_accounts(cfg, args.id)[0]
        say(f"This stops MailTidy looking after {acct.email}. Your mail and folders stay exactly as they are.")
        if not args.yes and not ask_yes(f"Remove '{acct.id}'?", False):
            say("Nothing changed.")
            return EXIT_OK
        cfg.accounts = [a for a in cfg.accounts if a.id != acct.id]
        ctx.save()
        mark_previewed(cfg, [acct.id], remove=True)
        if not args.keep_password and _forget_secrets(acct.id):
            say("Removed its password and sign-in from your keychain.")
        say(f"Removed '{acct.id}'.")
        return EXIT_OK
    if action == "test":
        failed = 0
        for acct in _pick_accounts(cfg, args.id):
            say(f"{acct.id} ({acct.email}):")
            problem = _test_login(acct)
            if problem:
                failed += 1
                say(f"  Problem: {problem}")
        return EXIT_ERROR if failed else EXIT_OK
    raise UserError("Choose list, add, remove or test (for example `mailtidy accounts list`).")


def cmd_run(args: argparse.Namespace, ctx: Ctx) -> int:
    cfg = _need_accounts(ctx)
    return do_run(ctx, _pick_accounts(cfg, args.account), dry_run=args.dry_run, backlog=args.backlog,
                  yes=args.yes)


def cmd_preview(args: argparse.Namespace, ctx: Ctx) -> int:
    cfg = _need_accounts(ctx)
    return do_preview(ctx, _pick_accounts(cfg, args.account), limit=args.limit)


def cmd_learn(args: argparse.Namespace, ctx: Ctx) -> int:
    cfg = _need_accounts(ctx)
    return do_learn(ctx, _pick_accounts(cfg, args.account))


def cmd_undo(args: argparse.Namespace, ctx: Ctx) -> int:
    cfg = _need_accounts(ctx)
    accounts = _pick_accounts(cfg, None)
    store = open_store(cfg)
    try:
        n = _engine().undo(_only(cfg, accounts), store, _backends(accounts), run_id=args.run, last=args.last,
                           log=ctx.log)
    finally:
        _close(store)
    if n:   # the engine already said how many were put back (or that there was nothing to undo)
        say("MailTidy won't move them again.")
    return EXIT_OK


def cmd_watch(args: argparse.Namespace, ctx: Ctx) -> int:
    cfg = _need_accounts(ctx)
    accounts = _gate(cfg, _pick_accounts(cfg, args.account), args.yes)
    if not accounts:
        return EXIT_ERROR
    store = open_store(cfg)
    say("Watching for new mail and sorting it as it arrives. Press Ctrl+C to stop.")
    try:
        _engine().start_from_now(_only(cfg, accounts), store, _backends(accounts), log=ctx.log)
        _engine().watch(_only(cfg, accounts), store, lambda: _backends(accounts), log=ctx.log)
    except KeyboardInterrupt:
        say("Stopped watching.")
    finally:
        _close(store)
    return EXIT_OK


def _store_if_exists(cfg: AppConfig) -> Any:
    """Open the memory file only if it's already there (read-only commands shouldn't create it)."""
    if not state_path(cfg).exists():
        return None
    try:
        return open_store(cfg)
    except Exception as e:  # noqa: BLE001 - a damaged memory file must not stop status/report
        warn(f"Couldn't read MailTidy's memory file ({e}); showing what I can.")
        return None


def cmd_status(args: argparse.Namespace, ctx: Ctx) -> int:
    from . import report

    cfg = ctx.cfg
    say(f"Settings file: {ctx.path}{'' if ctx.path.exists() else ' (not created yet)'}")
    if not cfg.accounts:
        say("Accounts: none yet. Run `mailtidy setup` to add one.")
    else:
        done = previewed_accounts(cfg)
        say(f"Accounts ({len(cfg.accounts)}):")
        for a in cfg.accounts:
            state = "on" if a.enabled else "off"
            note = "" if a.id in done else "  (not previewed yet: run `mailtidy preview`)"
            say(f"  {a.id:<20} {a.email:<34} {state}{note}")
    store = _store_if_exists(cfg)
    try:
        if store is None:
            say("Last run: never")
        else:
            run = report.last_run(store)
            if run:
                started = report._field(run, "started")
                when = started.strftime("%Y-%m-%d %H:%M") if isinstance(started, datetime) else str(started)
                kind = "preview" if report._field(run, "dry_run", False) else "run"
                say(f"Last {kind}: {when}, moved {report._field(run, 'moved', 0)} "
                    f"of {report._field(run, 'scanned', 0)} checked")
            else:
                say("Last run: never")
            today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            n = report.moves_since(store, today)
            say(f"Moved today: {n if n is not None else 'unknown'}")
            say(f"Learned rules: {len(report.learned_rules(store))}")
    finally:
        _close(store)
    try:
        sched = _schedule().status()
    except Exception as e:  # noqa: BLE001 - status must always finish
        sched = f"unknown ({e})"
    say(f"Background schedule: {sched}")
    say(f"Your rules: {len(cfg.rules)}   Memory folder: {data_dir(cfg)}")
    return EXIT_OK


def _rule_text(rule: Any) -> str:
    parts = []
    for label, values in (("from", rule.from_), ("to", rule.to), ("subject has", rule.subject),
                          ("list", rule.list_id)):
        if values:
            parts.append(f"{label} {' or '.join(values)}")
    for k, v in rule.header.items():
        parts.append(f"header {k} has {v}")
    if rule.accounts:
        parts.append(f"in {', '.join(rule.accounts)}")
    return " and ".join(parts) or "(no conditions)"


def _message_from_file(path: Path, account: str) -> Message:
    from email import policy
    from email.parser import BytesParser
    from email.utils import getaddresses, parseaddr, parsedate_to_datetime

    try:
        with path.open("rb") as f:
            msg = BytesParser(policy=policy.default).parse(f, headersonly=True)
    except OSError as e:
        raise UserError(f"Can't open {path}: {e.strerror or e}") from None

    def addr(value: str) -> Address:
        name, email = parseaddr(value or "")
        return Address(email.lower(), name)

    headers = {k.lower(): str(v) for k, v in msg.items()}
    date = None
    try:
        date = parsedate_to_datetime(headers["date"]) if headers.get("date") else None
    except (TypeError, ValueError):
        pass
    return Message(
        account=account, folder="INBOX", uid=0, message_id=headers.get("message-id", ""),
        subject=headers.get("subject", ""), from_=addr(headers.get("from", "")),
        to=[Address(e.lower(), n) for n, e in getaddresses([headers.get("to", "")]) if e],
        cc=[Address(e.lower(), n) for n, e in getaddresses([headers.get("cc", "")]) if e],
        reply_to=addr(headers["reply-to"]) if headers.get("reply-to") else None,
        date=date, headers=headers,
    )


def cmd_rules(args: argparse.Namespace, ctx: Ctx) -> int:
    from . import report

    cfg = ctx.cfg
    if args.rules_action == "list":
        if cfg.rules:
            say("Your rules (checked in this order, first match wins):")
            for i, r in enumerate(cfg.rules, 1):
                say(f"  {i}. {r.name}: {_rule_text(r)} -> {r.action}")
        else:
            say("You have no rules of your own yet. See docs/rules.md, then `mailtidy config edit`.")
        store = _store_if_exists(cfg)
        try:
            learned = report.learned_rules(store) if store is not None else []
        finally:
            _close(store)
        say(f"Learned rules: {len(learned)}")
        for item in learned[:20]:
            kind, key, target = (report._field(item, "kind"), report._field(item, "key"),
                                 report._field(item, "folder") or report._field(item, "category"))
            say(f"  {kind} {key} -> {target}")
        if len(learned) > 20:
            say(f"  ...and {len(learned) - 20} more (see `mailtidy report`).")
        return EXIT_OK

    # rules test
    account = args.account or (cfg.accounts[0].id if cfg.accounts else "")
    if args.file:
        msg = _message_from_file(Path(args.file), account)
    elif args.from_ or args.subject:
        headers = {}
        if args.list_id:
            headers["list-id"] = args.list_id
        msg = Message(account=account, folder="INBOX", uid=0, message_id="", subject=args.subject or "",
                      from_=Address((args.from_ or "").lower()), headers=headers)
    else:
        raise UserError("Give a saved email (.eml file) or --from and/or --subject. "
                        "Example: mailtidy rules test --from news@example.com --subject \"Weekly digest\"")
    store = _store_if_exists(cfg)
    try:
        learned = getattr(store, "learned", None) if store is not None else None
        known = report.contacts(store) if store is not None else set()
        decision = make_classifier(cfg, learned, known).classify(msg)
    finally:
        _close(store)
    target = decision.folder or cfg.folder_for(decision.category)
    say(f"From: {msg.from_.email or '(none)'}   Subject: {msg.subject or '(none)'}")
    if decision.keeps or not target:
        say("Result: it stays in the Inbox.")
    else:
        kind = "" if decision.folder else f" (category: {decision.category})"
        say(f"Result: it goes to {target}{kind}.")
    say(f"Why: {decision.reason}")
    say(f"Decided by: {decision.source.replace('-', ' ')} (confidence {decision.confidence:.2f}; "
        f"MailTidy only moves mail at {cfg.min_confidence:.2f} or more)")
    return EXIT_OK


def cmd_schedule(args: argparse.Namespace, ctx: Ctx) -> int:
    sched = _schedule()
    if args.schedule_action == "on":
        cfg = _need_accounts(ctx)
        minutes = cfg.interval_minutes if args.every is None else args.every
        if not 1 <= minutes <= MAX_INTERVAL:
            raise UserError(f"The schedule needs a number of minutes from 1 to {MAX_INTERVAL} "
                            "(15 is a good choice).")
        enabled = {a.id for a in cfg.accounts if a.enabled}
        if not args.yes and not previewed_accounts(cfg) & enabled:
            raise UserError("Run `mailtidy preview` first to see what would happen, then turn the schedule on. "
                            "(Or add --yes to skip the preview.)")
        try:
            say(sched.install(minutes, ctx.path.resolve()))
        except (RuntimeError, ValueError, OSError) as e:   # schedule.ScheduleError is a RuntimeError
            raise UserError(f"Couldn't turn on the background schedule: {e}") from None
        if minutes != cfg.interval_minutes:
            cfg.interval_minutes = minutes
            ctx.save()
        return EXIT_OK
    if args.schedule_action == "off":
        try:
            say(sched.uninstall())
        except (RuntimeError, OSError) as e:
            raise UserError(f"Couldn't turn off the background schedule: {e}") from None
        return EXIT_OK
    say(sched.status())
    return EXIT_OK


def cmd_report(args: argparse.Namespace, ctx: Ctx) -> int:
    from .report import build_report

    cfg = ctx.cfg
    out = Path(args.output) if args.output else data_dir(cfg) / "report.html"
    store = _store_if_exists(cfg)
    try:
        html = build_report(store, cfg)
    finally:
        _close(store)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    say(f"Report saved: {out}")
    if args.open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return EXIT_OK


def _editor_command(path: Path) -> list[str]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        return shlex.split(editor, posix=os.name != "nt") + [str(path)]
    if sys.platform.startswith("win"):
        return ["notepad.exe", str(path)]
    if sys.platform == "darwin":
        return ["open", "-W", "-t", str(path)]
    return ["xdg-open", str(path)]


def _check(ctx: Ctx) -> int:
    notes: list[str] = []
    cfg = load_config(ctx.path, warnings=notes)
    ctx._cfg = cfg
    problems = config_problems(cfg)
    try:
        extra = _module("classify.rules").validate_rules(cfg.rules, cfg)
        problems += [p for p in extra if p not in problems]
    except (ImportError, AttributeError, TypeError):   # the classifier's own checks are a bonus
        pass
    for n in notes:
        say(f"Note: {n}")
    if problems:
        say(f"Found {len(problems)} problem{'s' if len(problems) != 1 else ''} in {ctx.path}:")
        for p in problems:
            say(f"  - {p}")
        return EXIT_ERROR
    def plural(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    say(f"{ctx.path} looks good ({plural(len(cfg.accounts), 'account')}, {plural(len(cfg.rules), 'rule')}).")
    return EXIT_OK


def cmd_config(args: argparse.Namespace, ctx: Ctx) -> int:
    if args.config_action == "path":
        say(f"Settings file: {ctx.path}")
        say(f"Memory folder: {data_dir(ctx.cfg)}")
        return EXIT_OK
    if args.config_action == "edit":
        if not ctx.path.exists():
            ctx.save()
        cmd = _editor_command(ctx.path)
        say(f"Opening {ctx.path} ... save and close the editor when you're done.")
        try:
            subprocess.call(cmd)
        except OSError:
            raise UserError(f"Couldn't open an editor. Open this file yourself: {ctx.path}") from None
        return _check(ctx)
    if args.config_action == "ai-key":
        return _ai_key(args, ctx)
    return _check(ctx)


def _ai_key(args: argparse.Namespace, ctx: Ctx) -> int:
    """Save (or with --remove, delete) the Anthropic API key in the keychain, where the schedule finds it."""
    auth = _auth()
    try:
        if args.remove:
            auth.delete_ai_key()
            say("Removed your Anthropic API key from the keychain.")
            return EXIT_OK
        say("MailTidy keeps your Anthropic API key in your computer's keychain, so the background")
        say("schedule can use it too. Make a key at https://console.anthropic.com (API Keys).")
        key = "".join(getpass.getpass("Paste the API key here (it stays hidden while you type): ").split())
        if not key:
            say("No key entered; nothing changed.")
            return EXIT_ERROR
        auth.set_ai_key(key)
        del key
    except BackendError as e:   # auth.CredentialError: no usable keychain
        raise UserError(f"Couldn't reach your keychain: {e}") from None
    say("Saved. MailTidy will use this key when `ai: enabled: yes` is set in your settings.")
    if not ctx.cfg.ai.enabled:
        say("The AI helper is still off: run `mailtidy config edit` and set `enabled: yes` under `ai:`.")
    return EXIT_OK


# ---- argument parsing -----------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mailtidy",
        description="Keep every email account organized the same way, automatically. "
                    "Start with `mailtidy setup`.",
    )
    p.add_argument("--version", action="version", version=f"mailtidy {__version__}")
    p.add_argument("--config", metavar="PATH", help="use this settings file instead of the usual one")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    s = sub.add_parser("setup", help="step-by-step set-up: add accounts, preview, turn on the schedule")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("accounts", help="list, add, remove or test your email accounts")
    acc = s.add_subparsers(dest="accounts_action", metavar="ACTION", required=True)
    acc.add_parser("list", help="show your accounts")
    acc.add_parser("add", help="add an account (asks questions)")
    r = acc.add_parser("remove", help="stop looking after an account (mail is not touched)")
    r.add_argument("id", help="the account's short name (see `mailtidy accounts list`)")
    r.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    r.add_argument("--keep-password", action="store_true", help="leave its password in the keychain")
    t = acc.add_parser("test", help="check that MailTidy can log in")
    t.add_argument("id", nargs="?", help="only this account")
    s.set_defaults(func=cmd_accounts)

    s = sub.add_parser("run", help="sort new mail now")
    s.add_argument("--dry-run", action="store_true", help="only show what would happen; move nothing")
    s.add_argument("--backlog", action="store_true", help="also sort mail that was already in the Inbox")
    s.add_argument("--account", metavar="ID", help="only this account")
    s.add_argument("--quiet", action="store_true", help="print only problems (used by the background schedule)")
    s.add_argument("--yes", action="store_true", help="move mail even if you haven't seen a preview")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("preview", help="show what would be moved from your Inbox (nothing is moved)")
    s.add_argument("--account", metavar="ID", help="only this account")
    s.add_argument("--limit", type=int, default=50, metavar="N", help="show at most N moves (default 50)")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("learn", help="learn from the folders you already use (nothing is moved)")
    s.add_argument("--account", metavar="ID", help="only this account")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("undo", help="put mail back where it was")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--last", type=int, metavar="N", help="undo the last N moves")
    g.add_argument("--run", metavar="ID", help="undo one run (ids are in `mailtidy report`)")
    s.set_defaults(func=cmd_undo)

    s = sub.add_parser("watch", help="stay open and sort mail the moment it arrives")
    s.add_argument("--account", metavar="ID", help="only this account")
    s.add_argument("--yes", action="store_true", help="move mail even if you haven't seen a preview")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("status", help="what MailTidy has been doing")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("rules", help="show your rules, or test where a message would go")
    ru = s.add_subparsers(dest="rules_action", metavar="ACTION", required=True)
    ru.add_parser("list", help="show your rules and what MailTidy learned")
    t = ru.add_parser("test", help="where would this message go?")
    t.add_argument("file", nargs="?", help="a saved email (.eml file)")
    t.add_argument("--from", dest="from_", metavar="ADDRESS", help="sender address")
    t.add_argument("--subject", help="subject line")
    t.add_argument("--list-id", help="List-Id header, for mailing lists")
    t.add_argument("--account", metavar="ID", help="test as if it arrived in this account")
    s.set_defaults(func=cmd_rules)

    s = sub.add_parser("schedule", help="turn the automatic background run on or off")
    sc = s.add_subparsers(dest="schedule_action", metavar="ACTION", required=True)
    on = sc.add_parser("on", help="run automatically in the background")
    on.add_argument("--every", type=int, metavar="N", help="every N minutes (default: interval_minutes, 15)")
    on.add_argument("--yes", action="store_true", help="turn it on even if you haven't seen a preview")
    sc.add_parser("off", help="stop the background run")
    sc.add_parser("status", help="is it on?")
    s.set_defaults(func=cmd_schedule)

    s = sub.add_parser("report", help="make a web page listing everything MailTidy did")
    s.add_argument("--open", action="store_true", help="open it in your browser")
    s.add_argument("--output", metavar="FILE", help="save it here instead of the memory folder")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("config", help="where the settings file is, edit it, or check it for mistakes")
    co = s.add_subparsers(dest="config_action", metavar="ACTION", required=True)
    co.add_parser("path", help="show where the settings file is")
    co.add_parser("edit", help="open the settings file in an editor, then check it")
    co.add_parser("check", help="look for mistakes in the settings file")
    k = co.add_parser("ai-key", help="save your Anthropic API key in the keychain (for the optional AI)")
    k.add_argument("--remove", action="store_true", help="delete the saved key instead")
    s.set_defaults(func=cmd_config)
    _accept_config_everywhere(p)
    return p


def _accept_config_everywhere(parser: argparse.ArgumentParser) -> None:
    """Let `--config PATH` also come after the command (`mailtidy run --config PATH`): the background
    schedule runs it that way, and people type it that way too."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                child.add_argument("--config", metavar="PATH", default=argparse.SUPPRESS,
                                   help="use this settings file instead of the usual one")
                _accept_config_everywhere(child)


def _safe_console() -> None:
    """Never crash on an emoji in a subject when the console can't show it."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure: Callable | None = getattr(stream, "reconfigure", None)
        if reconfigure:
            with contextlib.suppress(Exception):
                reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the exit code: 0 ok, 1 error, 2 bad usage."""
    _safe_console()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:   # argparse: --help/--version exit 0, mistakes exit 2
        return int(e.code or 0) if not isinstance(e.code, str) else EXIT_USAGE
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    path = Path(args.config).expanduser() if args.config else default_config_path()
    ctx = Ctx(path=path, quiet=bool(getattr(args, "quiet", False)))
    # Linux/macOS: everything MailTidy creates (memory, log, report, settings) is readable by this user only.
    old_umask = os.umask(0o077) if os.name == "posix" else None
    try:
        _private_folders(ctx)
        return args.func(args, ctx)
    except UserError as e:
        warn(str(e))
    except ConfigError as e:
        warn(f"There's a problem with your settings: {e}")
    except BackendError as e:
        warn(f"Problem with your mailbox: {e}")
    except KeyboardInterrupt:
        warn("Stopped.")
    except ImportError as e:
        if os.environ.get("MAILTIDY_DEBUG"):
            raise
        warn(f"Part of MailTidy is missing ({e.name or e}). {REINSTALL_HINT}")
    except Exception as e:
        if os.environ.get("MAILTIDY_DEBUG"):
            raise
        warn(f"Something went wrong: {type(e).__name__}: {e}\n"
             "Set MAILTIDY_DEBUG=1 and run it again to see details for a bug report "
             "(remove any email addresses before sharing).")
    finally:
        if old_umask is not None:
            os.umask(old_umask)
    return EXIT_ERROR


def _private_folders(ctx: Ctx) -> None:
    """Tighten MailTidy's own settings and memory folders made by older versions (Linux/macOS).

    Only the usual folders (or the ones named by $MAILTIDY_CONFIG / $MAILTIDY_DATA) are changed, never a
    folder someone pointed `--config` or `data_dir:` at, such as Documents. New files and folders are
    private anyway because of the umask set in main()."""
    if os.name != "posix":
        return
    if ctx.path == default_config_path():
        make_private(ctx.path.parent)
    with contextlib.suppress(ConfigError):   # a broken settings file is reported by the command itself
        folder = data_dir(load_config(ctx.path, warnings=[]))
        if folder == default_data_dir():
            make_private(folder)
