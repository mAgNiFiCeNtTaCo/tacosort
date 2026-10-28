"""The engine: reads new mail, asks the classifier where it belongs, moves it, and remembers.

Entry points (see SPEC.md "Contracts"):
- `run_once`: one pass over every enabled account.
- `learn_from_folders`: bootstrap from the user's existing folders and Sent mail.
- `undo`: put moved messages back where they came from.
- `watch`: keep running, waking up on new mail (IMAP IDLE).

Safety: never deletes, never marks as read, never moves flagged mail (unless the config says so), never
moves into or out of Sent/Drafts/Trash/Junk/All Mail, never moves a message the user put back, and
stops after `max_moves_per_run` moves per account. Only one run works at a time (a lock file).
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from . import learner
from .backend import BackendError, MailBackend
from .models import (
    INBOX,
    AccountConfig,
    AppConfig,
    Decision,
    FolderInfo,
    LearnReport,
    Message,
    MoveRecord,
    RunReport,
)
from .store import Store, utcnow

logger = logging.getLogger("tidyinbox")

Log = Callable[[str], None]

BATCH = 200                    # messages fetched and classified at a time
CONTACTS_EVERY = timedelta(days=1)   # how often the people you write to are re-read from Sent
IDLE_SECONDS = 25 * 60         # servers drop IDLE after ~29 minutes
MIN_CYCLE_SECONDS = 30         # never loop faster than this when nothing happened
BACKOFF = (30, 60, 120, 300, 600, 900)
NEVER_TOUCH = {"\\Sent", "\\Trash", "\\Junk", "\\All", "\\Drafts"}
LOCK_NAME = "tidyinbox.lock"
LOG_NAME = "tidyinbox.log"
# A message without a Message-ID can't be recognised again (put back, undone), so it is never moved.
UNTRACKABLE = Decision(INBOX, 1.0, "guard", "TidyInbox can't track this message safely, so it leaves it alone")


class EngineBusy(RuntimeError):
    """Another TidyInbox run is working right now."""


# ---- data folder, log file, lock ------------------------------------------------------------------
def data_dir_for(config: AppConfig, store: Store | None = None) -> Path:
    """Where the lock and log live: config.data_dir, else next to the state file, else the default."""
    if config.data_dir:
        return Path(config.data_dir).expanduser()
    if store is not None and store.path is not None:
        return store.path.parent
    try:
        from .config import default_data_dir
    except ImportError:
        return Path.home() / ".tidyinbox"
    return Path(default_data_dir())


_log_handler: logging.Handler | None = None


def setup_logging(data_dir: Path) -> logging.Logger:
    """Send TidyInbox's log to a rotating file `tidyinbox.log` in the data folder (1 MB x 3).
    The log holds counts, folder names and errors: never passwords, tokens or message text."""
    global _log_handler
    path = Path(data_dir) / LOG_NAME
    if _log_handler is not None and getattr(_log_handler, "baseFilename", None) == str(path.resolve()):
        return logger
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
                                                   delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    if _log_handler is not None:
        logger.removeHandler(_log_handler)
        _log_handler.close()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    _log_handler = handler
    return logger


class RunLock:
    """A lock file in the data folder so two runs (the schedule and `watch`) never work at once.
    The operating system releases it if TidyInbox crashes, so it can't get stuck."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / LOCK_NAME
        self._fh = None

    def acquire(self, wait: float = 0) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + wait
        while True:
            fh = open(self.path, "a+")  # noqa: SIM115 - stays open while the lock is held
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.5)
                continue
            self._fh = fh
            return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None

    def __enter__(self) -> RunLock:  # noqa: PYI034 - typing.Self needs Python 3.11
        if not self.acquire():
            raise EngineBusy("Another TidyInbox run is working right now.")
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def is_running(config: AppConfig, store: Store | None = None) -> bool:
    """True when some TidyInbox run holds the lock right now."""
    lock = RunLock(data_dir_for(config, store))
    if lock.acquire():
        lock.release()
        return False
    return True


def _say(log: Log, msg: str) -> None:
    logger.info(msg)
    log(msg)


def _new_run_id() -> str:
    return utcnow().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _close(backend: MailBackend) -> None:
    with contextlib.suppress(Exception):   # closing must never hide the real problem
        backend.close()


def _plain_error(account_id: str, err: Exception) -> str:
    if isinstance(err, BackendError):
        return f"{account_id}: {err}"
    return (f"{account_id}: something unexpected went wrong ({type(err).__name__}: {err}). "
            "The other accounts were still processed; details are in tidyinbox.log.")


# ---- classifier -----------------------------------------------------------------------------------
def _make_classifier(config: AppConfig, store: Store, classifier, log: Log = print):
    """`classifier` may be an object with .classify, a factory (config, learned, contacts) -> object,
    or None for the real pipeline (with AI when the config turns it on and a key is found)."""
    if classifier is not None and hasattr(classifier, "classify") and not isinstance(classifier, type):
        return classifier
    contacts = store.contacts()
    if classifier is not None:
        return classifier(config, store.learned, contacts)
    from .classify.pipeline import Classifier
    return Classifier(config, store.learned, contacts, ai=_make_ai(config, log))


def _anthropic_key(config: AppConfig) -> str | None:
    """The AI key: from the environment variable the settings name, else the one saved in the keychain
    with `tidyinbox config ai-key` (never any other keychain entry, such as a mail password)."""
    key = os.environ.get(config.ai.api_key_env or "ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        from .imap import auth
        get_ai_key = getattr(auth, "get_ai_key", None)
        return get_ai_key() if get_ai_key is not None else None
    except Exception:  # noqa: BLE001 - no keychain means no key, not a crash
        return None


def _make_ai(config: AppConfig, log: Log):
    """A fresh AI helper for this run (its per-run cap starts at zero), or None."""
    if not config.ai.enabled:
        return None
    key = _anthropic_key(config)
    if not key:
        _say(log, f"AI is turned on but no API key was found in {config.ai.api_key_env or 'ANTHROPIC_API_KEY'} "
                  "or saved with `tidyinbox config ai-key`, so AI was skipped this time.")
        return None
    try:
        from .classify.ai import AIClassifier
        return AIClassifier(config.ai, key)
    except Exception as e:  # noqa: BLE001 - e.g. the anthropic package is not installed
        _say(log, f"AI was skipped this time: {e}")
        return None


def _classify(clf, msgs: list[Message]) -> list[Decision]:
    if hasattr(clf, "classify_many"):
        return list(clf.classify_many(msgs))
    return [clf.classify(m) for m in msgs]


def _norm(name: str) -> str:
    return name.strip().lower()


def _find_folder(folders: dict[str, FolderInfo], name: str) -> FolderInfo | None:
    """The server's folder for a name from the settings: the exact name first, then ignoring capitals
    ("Inbox" finds "INBOX"). Watermarks are always kept under the server's name."""
    return folders.get(name) or next((f for n, f in folders.items() if _norm(n) == _norm(name)), None)


def _folder_key(info: FolderInfo) -> str:
    """Name for matching a destination like "TidyInbox/Receipts" whatever the server's delimiter."""
    return _norm(info.name.replace(info.delimiter, "/") if info.delimiter else info.name)


def _has_watermark(store: Store, account_id: str, folder: str) -> bool:
    """True when the folder (as written in the settings) already has a start point. Without the server
    list only INBOX's spelling is known for sure; run_once also finds other names stored as typed."""
    names = {folder, "INBOX"} if folder.upper() == "INBOX" else {folder}
    return any(store.get_watermark(account_id, n) is not None for n in names)


def _top_uid(backend: MailBackend, folder: str) -> int:
    """Highest UID in the folder now (0 when empty): everything up to it counts as "already there"."""
    return max(backend.search_uids(folder, limit=1), default=0)


def _refresh_contacts(store: Store, backend: MailBackend, account: AccountConfig, clf) -> None:
    """Re-read the people the user writes to from Sent, at most once a day per account, so people they
    started writing to after setup are protected too. Skipped quietly when the learner can't do it."""
    refresh = getattr(learner, "refresh_contacts", None)
    if refresh is None:
        return
    key = f"contacts_refreshed:{account.id}"
    last = store.get_meta(key)
    now = utcnow()
    with contextlib.suppress(ValueError):
        if last and now - datetime.fromisoformat(last) < CONTACTS_EVERY:
            return
    try:
        refresh(store, backend, account)
    except Exception as e:  # noqa: BLE001 - contacts are a bonus; sorting goes on without them
        logger.warning("%s: could not re-read contacts from Sent: %s", account.id, e)
        return
    store.set_meta(key, now.isoformat())
    known = getattr(clf, "contacts", None)
    if isinstance(known, set):                     # let this run already protect the new people
        known.update(store.contacts())


# ---- run_once -------------------------------------------------------------------------------------
def run_once(config: AppConfig, store: Store, backends: dict[str, MailBackend], *, dry_run: bool,
             backlog: bool = False, log: Log = print, classifier=None,
             accounts: list[str] | None = None) -> RunReport:
    """One pass over every enabled account (or only `accounts`). With `dry_run`, nothing changes on the
    server and no progress is saved: `report.moves` lists what would happen. With `backlog`, the
    whole watched folder is sorted (newest first), not just new mail.

    Without `backlog`, a folder with no watermark yet (or one the server renumbered) is never sorted
    completely: the mail there now counts as handled and only later mail is sorted. Messages without a
    Message-ID are never moved. Once a day per account, the people the user writes to are re-read from
    Sent. If another run is working, returns at once with `run_id == ""` and `skipped=True`, and does
    nothing."""
    data_dir = data_dir_for(config, store)
    setup_logging(data_dir)
    report = RunReport(run_id="", dry_run=dry_run, started=utcnow())
    lock = RunLock(data_dir)
    if not lock.acquire():
        _say(log, "Another TidyInbox run is working right now, so this one did nothing.")
        report.skipped = True
        report.finished = utcnow()
        return report
    try:
        report.run_id = _new_run_id()
        store.start_run(report.run_id, report.started, dry_run)
        _say(log, f"{'Preview' if dry_run else 'Run'} {report.run_id} started.")
        clf = _make_classifier(config, store, classifier, log)
        for account in config.accounts:
            if not account.enabled or (accounts and account.id not in accounts):
                continue
            backend = backends.get(account.id)
            if backend is None:
                report.errors.append(f"{account.id}: no connection was set up for this account.")
                continue
            try:
                backend.connect()
                _run_account(config, store, backend, account, clf, report, dry_run=dry_run,
                             backlog=backlog, log=log)
            except Exception as e:
                logger.exception("account %s failed", account.id)
                report.errors.append(_plain_error(account.id, e))
            finally:
                _close(backend)
        report.finished = utcnow()
        store.finish_run(report)
        verb = "would move" if dry_run else "moved"
        _say(log, f"Done: looked at {report.scanned} messages, {verb} {report.moved}, left {report.kept} "
                  f"where they were, learned {report.learned} corrections."
                  + (f" {len(report.errors)} problem(s)." if report.errors else ""))
        for err in report.errors:
            logger.warning(err)
        return report
    finally:
        lock.release()


def _run_account(config: AppConfig, store: Store, backend: MailBackend, account: AccountConfig, clf,
                 report: RunReport, *, dry_run: bool, backlog: bool, log: Log) -> None:
    folders = {f.name: f for f in backend.list_folders()}
    if not dry_run:
        _refresh_contacts(store, backend, account, clf)
    if config.learn_from_corrections and not dry_run:
        found = learner.detect_corrections(store, backend, account, config=config, log=log)
        report.learned += found
    dest_cache: dict[str, str] = {}
    moves_left = [config.max_moves_per_run]
    for folder in account.watch_folders:
        info = _find_folder(folders, folder)
        if info is None:
            report.errors.append(f"{account.id}: the folder {folder!r} does not exist, so it was skipped.")
            continue
        if info.flags & NEVER_TOUCH:
            report.errors.append(f"{account.id}: {folder!r} is a special folder (Sent, Trash, Junk...). "
                                 "TidyInbox never sorts it, so it was skipped.")
            continue
        _run_folder(config, store, backend, account, info.name, folders, clf, report, dest_cache, moves_left,
                    dry_run=dry_run, backlog=backlog, log=log, config_name=folder)
        if moves_left[0] <= 0:
            break


def _destination(config: AppConfig, decision: Decision) -> str | None:
    if decision.folder:
        return decision.folder
    return config.folder_for(decision.category)


def _run_folder(config: AppConfig, store: Store, backend: MailBackend, account: AccountConfig, folder: str,
                folders: dict[str, FolderInfo], clf, report: RunReport, dest_cache: dict[str, str],
                moves_left: list[int], *, dry_run: bool, backlog: bool, log: Log,
                config_name: str = "") -> None:
    state = backend.folder_state(folder)
    mark = store.get_watermark(account.id, folder)
    legacy = False
    if mark is None and config_name and config_name != folder:
        # Saved by an older TidyInbox under the name as typed in the settings ("Inbox"): adopt it.
        mark = store.get_watermark(account.id, config_name)
        legacy = mark is not None
    reset = mark is not None and mark[0] != state.uidvalidity
    if not backlog and (mark is None or reset):
        # Without --backlog a folder is never sorted completely just because its start point is missing
        # or the server renumbered it: the mail there now counts as already handled.
        if not dry_run:
            store.set_watermark(account.id, folder, state.uidvalidity, _top_uid(backend, folder))
        if reset:
            _say(log, f"{account.id}: the server renumbered the messages in {folder}, so TidyInbox can't tell "
                      "old mail from new this time. It left them where they are and sorts new mail from now on.")
        else:
            _say(log, f"{account.id}: first look at {folder}. TidyInbox sorts mail that arrives from now on; "
                      "`tidyinbox run --backlog` sorts the mail already there.")
        return
    if backlog:
        candidates = backend.search_uids(folder)
    else:
        last_uid = mark[1]
        candidates = [u for u in backend.search_uids(folder, min_uid=last_uid + 1) if u > last_uid]
    candidates = sorted(set(candidates), reverse=True)        # newest first
    done: set[int] = set()
    by_name = {_folder_key(f): f for f in folders.values()}
    untracked = 0

    for i in range(0, len(candidates), BATCH):
        if moves_left[0] <= 0:
            break
        chunk = candidates[i : i + BATCH]
        fetched = {m.uid: m for m in backend.fetch(folder, chunk, with_snippet=config.ai.enabled
                                                   and config.ai.send_snippet)}
        done.update(u for u in chunk if u not in fetched)     # vanished meanwhile: nothing to do
        todo: list[Message] = []
        kept_ids: list[tuple[str, str]] = []
        for uid in chunk:
            m = fetched.get(uid)
            if m is None:
                continue
            if not m.message_id:
                # Nothing could recognise it later (put back by you, undo), so it is never moved.
                report.scanned += 1
                report.kept += 1
                untracked += 1
                done.add(uid)
                continue
            seen = store.seen(account.id, m.message_id)
            if seen is not None and seen.override:
                done.add(uid)
                continue
            if seen is not None and seen.decision.startswith("moved:"):
                if _same_message(store, account.id, m):
                    # We moved it before and it is back here: the user put it back. Never fight that.
                    if not dry_run:
                        store.mark_seen(account.id, m.message_id, seen.decision, override=True)
                    done.add(uid)
                    continue
                seen = None                # a different message reusing the Message-ID: sort it as new mail
            if seen is not None and not backlog:
                done.add(uid)
                continue
            todo.append(m)
        if not todo:
            continue
        decisions = _classify(clf, todo)
        groups: dict[str, list[tuple[Message, Decision]]] = {}
        for m, d in zip(todo, decisions):
            if moves_left[0] <= 0:
                break
            report.scanned += 1
            dest = None if d is None else _destination(config, d)
            target = by_name.get(_norm(dest)) if dest else None
            if target is not None:
                dest = target.name                 # the server's own spelling ("receipts" stays "receipts")
            if (dest is None or d.confidence < config.min_confidence or _norm(dest) == _norm(folder)
                    or (config.keep_flagged and m.is_flagged)
                    or (target is not None and target.flags & NEVER_TOUCH)):
                report.kept += 1
                kept_ids.append((m.message_id, INBOX))
                done.add(m.uid)
                continue
            groups.setdefault(dest, []).append((m, d))
            moves_left[0] -= 1
        if not dry_run:
            store.mark_seen_many(account.id, kept_ids)
        for dest, items in groups.items():
            records = [
                MoveRecord(run_id=report.run_id, account=account.id, message_id=m.message_id,
                           sender=m.from_.email, subject=m.subject, from_folder=folder, to_folder=dest,
                           category=d.category, source=d.source, reason=d.reason, at=utcnow())
                for m, d in items
            ]
            if dry_run:
                report.moves.extend(records)
                report.moved += len(records)
                done.update(m.uid for m, _ in items)
                continue
            server_dest = dest if dest in folders else dest_cache.get(dest)    # existing: no CREATE needed
            if server_dest is None:
                server_dest = backend.ensure_folder(dest)
                dest_cache[dest] = server_dest
            result = backend.move(folder, [m.uid for m, _ in items], server_dest)
            moved = []
            for (m, _), rec in zip(items, records):
                if m.uid in result:        # only what the server confirms; the rest is tried again next run
                    rec.to_folder = server_dest
                    rec.new_uid = result[m.uid]
                    moved.append(rec)
                    done.add(m.uid)
            store.record_moves(moved)
            store.mark_seen_many(account.id, [(r.message_id, f"moved:{server_dest}") for r in moved])
            report.moves.extend(moved)
            report.moved += len(moved)
            logger.info("%s: moved %d message(s) from %s to %s", account.id, len(moved), folder, server_dest)
            if len(moved) < len(items):
                _say(log, f"{account.id}: the server did not confirm {len(items) - len(moved)} move(s) to "
                          f"{server_dest}; TidyInbox will try those again next time.")

    if untracked:
        logger.info("%s: left %d message(s) in %s that have no Message-ID (%s)", account.id, untracked, folder,
                    UNTRACKABLE.reason)
    if moves_left[0] <= 0 and len(done) < len(candidates):
        _say(log, f"{account.id}: reached the safety limit of {config.max_moves_per_run} moves for one run; "
                  "the rest will be sorted next time.")
    if dry_run:
        return
    # The watermark covers every UID up to the highest one with nothing unhandled below it.
    remaining = [u for u in candidates if u not in done]
    if remaining:
        handled = [u for u in candidates if u < min(remaining)]
    else:
        handled = candidates
    old = mark[1] if mark and not reset else 0
    new_last = max([old, *handled]) if handled else old
    if reset or mark is None or legacy or new_last != old:
        store.set_watermark(account.id, folder, state.uidvalidity, new_last)


def _same_message(store: Store, account_id: str, msg: Message) -> bool:
    """True when the message we moved earlier with this Message-ID had the same sender and subject (so
    this is it, put back), False when a different message reuses the Message-ID."""
    moves = store.moves_for_message(account_id, msg.message_id)
    if not moves:
        return True
    return any(mv.sender == msg.from_.email and mv.subject == msg.subject for mv in moves)


# ---- start from now -------------------------------------------------------------------------------
def start_from_now(config: AppConfig, store: Store, backends: dict[str, MailBackend], *,
                   log: Log = print) -> list[str]:
    """For watched folders TidyInbox has never sorted, remember the newest message as "already handled",
    so the next run only sorts mail that arrives after this (`backlog=True` still sorts everything).
    Folders that already have a watermark are left alone. Returns the ids of accounts that were set.

    If this can't reach an account (or another run holds the lock), nothing is lost: a run without
    `backlog` sets the start point itself the first time it sees a folder and sorts nothing there."""
    data_dir = data_dir_for(config, store)
    setup_logging(data_dir)
    lock = RunLock(data_dir)
    if not lock.acquire():
        logger.info("start_from_now: another run is working; the next run sets the start point itself")
        return []
    started: list[str] = []
    try:
        for account in config.accounts:
            backend = backends.get(account.id)
            if not account.enabled or backend is None:
                continue
            missing = [f for f in account.watch_folders if not _has_watermark(store, account.id, f)]
            if not missing:
                continue
            try:
                backend.connect()
                folders = {f.name: f for f in backend.list_folders()}
                for folder in missing:
                    info = _find_folder(folders, folder)
                    if info is None or info.flags & NEVER_TOUCH:
                        continue
                    if store.get_watermark(account.id, info.name) is not None:
                        continue
                    state = backend.folder_state(info.name)
                    top = max(_top_uid(backend, info.name), state.uidnext - 1, 0)
                    store.set_watermark(account.id, info.name, state.uidvalidity, top)
                started.append(account.id)
            except Exception as e:  # noqa: BLE001 - run_once reports the account's problem properly
                logger.warning("start_from_now %s: %s", account.id, e)
            finally:
                _close(backend)
    finally:
        lock.release()
    if started:
        _say(log, f"{', '.join(started)}: from now on TidyInbox sorts new mail as it arrives. The mail already "
                  "in your Inbox stays put; `tidyinbox run --backlog` sorts that too.")
    return started


# ---- learn ----------------------------------------------------------------------------------------
def learn_from_folders(config: AppConfig, store: Store, backends: dict[str, MailBackend], *,
                       log: Log = print, sample: int | None = None) -> LearnReport:
    """Learn the user's own folder scheme and contacts (see `tidyinbox.learner`)."""
    data_dir = data_dir_for(config, store)
    setup_logging(data_dir)
    lock = RunLock(data_dir)
    if not lock.acquire(wait=120):
        raise EngineBusy("Another TidyInbox run is working right now. Try again in a minute.")
    try:
        report = learner.learn_from_folders(config, store, backends, log=lambda m: _say(log, m), sample=sample)
        for err in report.errors:
            logger.warning(err)
        return report
    finally:
        lock.release()


# ---- undo -----------------------------------------------------------------------------------------
def undo(config: AppConfig, store: Store, backends: dict[str, MailBackend], *, run_id: str | None = None,
         last: int | None = None, log: Log = print) -> int:
    """Move messages back where they came from: one run (`run_id`), the last `last` moves, or else the
    latest run. Undone messages are never sorted again. Returns how many were put back."""
    data_dir = data_dir_for(config, store)
    setup_logging(data_dir)
    if last:
        moves = store.recent_moves(last, include_undone=False)
    else:
        run_id = run_id or store.last_run_with_moves()
        moves = store.moves_for_run(run_id) if run_id else []
    if not moves:
        _say(log, "There is nothing to undo.")
        return 0
    lock = RunLock(data_dir)
    if not lock.acquire(wait=120):
        raise EngineBusy("Another TidyInbox run is working right now. Try again in a minute.")
    restored = 0
    try:
        by_account: dict[str, list] = {}
        for mv in moves:
            by_account.setdefault(mv.account, []).append(mv)
        for account_id, items in by_account.items():
            backend = backends.get(account_id)
            if backend is None:
                _say(log, f"{account_id}: no connection was set up for this account; its moves were not undone.")
                continue
            try:
                backend.connect()
                restored += _undo_account(store, backend, account_id, items, log)
            except Exception as e:
                logger.exception("undo failed for %s", account_id)
                _say(log, _plain_error(account_id, e))
            finally:
                _close(backend)
    finally:
        lock.release()
    _say(log, f"Put {restored} message(s) back.")
    return restored


def _undo_account(store: Store, backend: MailBackend, account_id: str, moves: list, log: Log) -> int:
    existing = {f.name for f in backend.list_folders()}
    groups: dict[tuple[str, str], list[tuple[int, object]]] = {}
    taken: dict[str, set[int]] = {}           # folder -> UIDs already picked (two moves, one Message-ID)
    for mv in moves:
        if mv.to_folder not in existing or mv.from_folder not in existing:
            _say(log, f"{account_id}: a folder for one move no longer exists; skipped it.")
            continue
        if not mv.message_id and not mv.new_uid:
            _say(log, f"{account_id}: TidyInbox can't track one message in {mv.to_folder} (it has no Message-ID "
                      "and the server gave no number for it), so it can't put it back; it is still there "
                      "unless you moved it.")
            store.mark_undone([mv.id])
            continue
        uids = backend.find_message_id(mv.to_folder, mv.message_id) if mv.message_id else []
        if not uids and not mv.message_id and mv.new_uid:
            # No Message-ID: fall back to the UID the server gave it, but only if it still matches.
            same = [m for m in backend.fetch(mv.to_folder, [mv.new_uid])
                    if m.from_.email == mv.sender and m.subject == mv.subject]
            uids = [m.uid for m in same]
        used = taken.setdefault(mv.to_folder, set())
        uids = [u for u in uids if u not in used]
        if not uids:
            _say(log, f"{account_id}: one message is no longer in {mv.to_folder} (you moved or deleted it); "
                      "left it alone.")
            store.mark_undone([mv.id])
            continue
        used.add(uids[0])
        groups.setdefault((mv.to_folder, mv.from_folder), []).append((uids[0], mv))
    restored = 0
    for (src, dest), items in groups.items():
        result = backend.move(src, [u for u, _ in items], dest)
        done = [mv for u, mv in items if u in result]
        store.mark_undone([mv.id for mv in done])
        store.mark_seen_many(account_id, [(mv.message_id, INBOX) for mv in done], override=True)
        restored += len(done)
    return restored


# ---- watch ----------------------------------------------------------------------------------------
def watch(config: AppConfig, store: Store, backends_factory: Callable[[], dict[str, MailBackend]], *,
          log: Log = print, classifier=None, idle_seconds: int = IDLE_SECONDS,
          max_cycles: int | None = None, sleep: Callable[[float], None] = time.sleep,
          stop: threading.Event | None = None) -> None:
    """Keep running: sort, then wait for new mail (IMAP IDLE, up to 25 minutes), sort again...
    Reconnects with growing pauses after errors. Stops cleanly on Ctrl+C (or when `stop` is set)."""
    setup_logging(data_dir_for(config, store))
    stop = stop or threading.Event()
    failures = 0
    cycles = 0
    _say(log, "Watching for new mail. Press Ctrl+C to stop.")
    try:
        while not stop.is_set() and (max_cycles is None or cycles < max_cycles):
            cycles += 1
            started = time.monotonic()
            try:
                run_once(config, store, backends_factory(), dry_run=False, log=log, classifier=classifier)
                changed = _idle_all(config, backends_factory, idle_seconds, stop)   # raises if all are down
                failures = 0
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - keep watching through network trouble
                pause = BACKOFF[min(failures, len(BACKOFF) - 1)]
                failures += 1
                logger.warning("watch: %s", e)
                _say(log, f"Lost the connection ({e}). Trying again in {pause} seconds.")
                _wait(stop, pause, sleep)
                continue
            if not changed and time.monotonic() - started < MIN_CYCLE_SECONDS:
                _wait(stop, MIN_CYCLE_SECONDS, sleep)
    except KeyboardInterrupt:
        pass
    _say(log, "Stopped watching.")


def _wait(stop: threading.Event, seconds: float, sleep: Callable[[float], None]) -> None:
    if not stop.is_set():
        sleep(seconds)


def _idle_all(config: AppConfig, backends_factory, idle_seconds: int, stop: threading.Event) -> bool:
    """IDLE on each account's first watched folder at the same time; return when any sees new mail,
    all time out, or `stop` is set. Raises when no account could IDLE at all."""
    backends = backends_factory()
    jobs = [(a, backends[a.id]) for a in config.accounts if a.enabled and a.id in backends]
    if not jobs:
        return False
    woke = threading.Event()
    changed = threading.Event()
    finished: list[bool] = []
    errors: list[Exception] = []
    guard = threading.Lock()

    def wait_on(account: AccountConfig, backend: MailBackend) -> None:
        try:
            backend.connect()
            if backend.idle((account.watch_folders or ["INBOX"])[0], idle_seconds):
                changed.set()
                woke.set()
        except Exception as e:  # noqa: BLE001
            with guard:
                errors.append(e)
        finally:
            with guard:
                finished.append(True)
                if len(finished) == len(jobs):
                    woke.set()

    threads = [threading.Thread(target=wait_on, args=job, daemon=True) for job in jobs]
    for t in threads:
        t.start()
    try:
        while not woke.wait(1.0):
            if stop.is_set():
                break
    finally:
        for _, b in jobs:
            _close(b)
    if not changed.is_set() and errors and len(errors) == len(jobs):
        raise errors[0]
    return changed.is_set()


__all__ = [
    "EngineBusy",
    "RunLock",
    "data_dir_for",
    "is_running",
    "learn_from_folders",
    "run_once",
    "setup_logging",
    "start_from_now",
    "undo",
    "watch",
]
