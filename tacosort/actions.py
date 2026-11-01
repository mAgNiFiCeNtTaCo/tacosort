"""What the window and the phone page can ask TacoSort to do: test a login, learn, preview, sort, undo.

No window or web code here: these run on a helper thread and report through `log`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import cli
from .backend import BackendError
from .config import data_dir
from .models import AccountConfig, AppConfig

MAX_SORT_ROUNDS = 100            # "Sort my Inbox now" repeats runs of max_moves_per_run until done
SAFETY_LIMIT_TEXT = "reached the safety limit"
BUSY_TEXT = "TacoSort is busy with another run right now; try again in a minute."

Log = Callable[[str], None]


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
                problems.append(BUSY_TEXT)
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
                problems.append(BUSY_TEXT)
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
