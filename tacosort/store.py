"""TacoSort's memory: one SQLite file (`state.sqlite3` in the data folder).

It remembers every run, every move (so it can be undone), how far each folder has been read
(UID watermarks), what it learned from the user (their folders and their corrections), the people the
user writes to (contacts), and every message it has already decided about ("seen"), so a message the
user put back is never moved again.

Usage:
    with Store(path) as store:
        store.learned.lookup("shop@example.com", "example.com", "", "gmail")
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import INBOX, Decision, MoveRecord, RunReport

# Bump when MIGRATIONS gets a new entry. Each entry upgrades the file from the previous version.
SCHEMA_VERSION = 4

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            started TEXT NOT NULL,
            finished TEXT,
            dry_run INTEGER NOT NULL DEFAULT 0,
            scanned INTEGER NOT NULL DEFAULT 0,
            moved INTEGER NOT NULL DEFAULT 0,
            kept INTEGER NOT NULL DEFAULT 0,
            learned INTEGER NOT NULL DEFAULT 0,
            errors TEXT NOT NULL DEFAULT '[]'
        )""",
        """CREATE TABLE moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            account TEXT NOT NULL,
            message_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            from_folder TEXT NOT NULL,
            to_folder TEXT NOT NULL,
            category TEXT NOT NULL,
            source TEXT NOT NULL,
            reason TEXT NOT NULL,
            at TEXT NOT NULL,
            new_uid INTEGER,
            undone INTEGER NOT NULL DEFAULT 0,
            check_state TEXT NOT NULL DEFAULT '',
            last_checked TEXT
        )""",
        "CREATE INDEX moves_run ON moves(run_id)",
        "CREATE INDEX moves_account_msg ON moves(account, message_id)",
        "CREATE INDEX moves_at ON moves(at)",
        """CREATE TABLE watermarks (
            account TEXT NOT NULL,
            folder TEXT NOT NULL,
            uidvalidity INTEGER NOT NULL,
            last_uid INTEGER NOT NULL,
            updated TEXT NOT NULL,
            PRIMARY KEY (account, folder)
        )""",
        """CREATE TABLE learned (
            account TEXT NOT NULL,
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            category TEXT NOT NULL,
            folder TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL,
            evidence INTEGER NOT NULL,
            source TEXT NOT NULL,
            updated TEXT NOT NULL,
            PRIMARY KEY (account, kind, key)
        )""",
        """CREATE TABLE contacts (
            address TEXT PRIMARY KEY,
            account TEXT NOT NULL DEFAULT '',
            added TEXT NOT NULL
        )""",
        """CREATE TABLE seen (
            account TEXT NOT NULL,
            message_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            override INTEGER NOT NULL DEFAULT 0,
            updated TEXT NOT NULL,
            PRIMARY KEY (account, message_id)
        )""",
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ],
    2: [
        # When a move is due for its next correction check (NULL = at the next run).
        "ALTER TABLE moves ADD COLUMN next_check TEXT",
    ],
    3: [
        # A company's own sub-folder for one category ("Promotions/Crunchyroll"). `tidied` = the mail
        # TacoSort had already put in the category folder has been moved into it.
        """CREATE TABLE company_folders (
            account TEXT NOT NULL,
            category TEXT NOT NULL,
            company TEXT NOT NULL,
            folder TEXT NOT NULL,
            created TEXT NOT NULL,
            tidied INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (account, category, company)
        )""",
    ],
    4: [
        # Each message TacoSort has read, by sender and subject template, to spot look-alike emails
        # ("Your TCGplayer.com items of ... have sold" over and over: a robot, whatever its headers say).
        """CREATE TABLE look_alikes (
            account TEXT NOT NULL,
            message_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            template TEXT NOT NULL,
            added TEXT NOT NULL,
            PRIMARY KEY (account, message_id)
        )""",
        "CREATE INDEX look_alikes_by_template ON look_alikes (account, sender, template)",
    ],
}

# How often a move is re-checked for user corrections, by its age: young moves often, old ones rarely.
RECHECK_EVERY = (
    (timedelta(days=1), timedelta(hours=1)),
    (timedelta(days=7), timedelta(days=1)),
    (timedelta(days=30), timedelta(days=7)),
)
RECHECK_LATE = timedelta(days=30)

# Learned-rule kinds, strongest first (this is the lookup order).
KINDS = ("address", "list_id", "domain")
# Where a learned rule came from.
FROM_FOLDERS = "folders"        # the user's own existing folders (bootstrap)
FROM_CORRECTION = "correction"  # the user moved something we sorted
# Category stored for a learned rule that names a folder outside the category scheme.
FOLDER_CATEGORY = "folder"
ANY_ACCOUNT = "*"


class StoreError(Exception):
    """The state file can't be used (for example it was made by a newer TacoSort)."""


@dataclass
class StoredMove(MoveRecord):
    """A MoveRecord read back from the store, with its row id and correction-check state."""

    id: int = 0
    check_state: str = ""        # "" = still watched, "gone", or "corrected:<folder>"


@dataclass
class LearnedRule:
    account: str
    kind: str
    key: str
    category: str
    folder: str
    weight: float
    evidence: int
    source: str
    updated: datetime


@dataclass
class SeenEntry:
    decision: str                # "inbox" or "moved:<folder>"
    override: bool               # True once the user overrode us: never move this message again
    updated: datetime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def normalize_list_id(value: str) -> str:
    """'Weekly News <weekly.news.example.com>' -> 'weekly.news.example.com'."""
    value = (value or "").strip()
    if "<" in value and ">" in value:
        value = value[value.rfind("<") + 1 : value.rfind(">")]
    return value.strip().lower()


def _normalize_key(kind: str, key: str) -> str:
    return normalize_list_id(key) if kind == "list_id" else (key or "").strip().lower()


class LearnedStore:
    """Learned sender rules. Implements `tacosort.models.LearnedRules` for the classifier."""

    def __init__(self, store: Store):
        self._store = store

    # ---- the classifier's view ------------------------------------------------------------------
    def lookup(self, sender: str, domain: str, list_id: str, account: str) -> Decision | None:
        """Best learned decision: sender address, then List-Id, then domain. A rule for this account
        beats one for every account ("*"). Domain rules need 2+ pieces of evidence, except when the
        user taught us directly (a correction)."""
        for kind, key in (("address", sender), ("list_id", list_id), ("domain", domain)):
            key = _normalize_key(kind, key)
            if not key:
                continue
            rows = self._store._query(
                "SELECT * FROM learned WHERE kind = ? AND key = ? AND account IN (?, ?) "
                "ORDER BY CASE WHEN account = ? THEN 0 ELSE 1 END",
                (kind, key, account, ANY_ACCOUNT, account),
            )
            for row in rows:
                rule = _learned(row)
                needed = 1 if rule.source == FROM_CORRECTION or kind != "domain" else 2
                if rule.evidence >= needed:
                    return _decision(rule)
        return None

    # ---- writing --------------------------------------------------------------------------------
    def learn_from_folder(self, account: str, kind: str, key: str, *, category: str, folder: str = "",
                          weight: float, evidence: int) -> bool:
        """Remember that the user keeps this sender in a folder. Never overwrites a correction (the
        user's latest action wins). Returns True when the rule was stored."""
        key = _normalize_key(kind, key)
        if not key:
            return False
        with self._store._tx() as c:
            row = c.execute("SELECT source FROM learned WHERE account = ? AND kind = ? AND key = ?",
                            (account, kind, key)).fetchone()
            if row and row["source"] == FROM_CORRECTION:
                return False
            c.execute(
                "INSERT OR REPLACE INTO learned (account, kind, key, category, folder, weight, evidence, "
                "source, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account, kind, key, category, folder or "", weight, evidence, FROM_FOLDERS, _ts(utcnow())),
            )
        return True

    def learn_correction(self, account: str, kind: str, key: str, *, category: str, folder: str = "",
                         weight: float = 0.99) -> None:
        """Remember what the user did by hand. The same answer again adds evidence; a different
        answer replaces the old one."""
        key = _normalize_key(kind, key)
        if not key:
            return
        with self._store._tx() as c:
            row = c.execute(
                "SELECT category, folder, evidence, source FROM learned WHERE account = ? AND kind = ? "
                "AND key = ?", (account, kind, key)).fetchone()
            same = (row is not None and row["source"] == FROM_CORRECTION and row["category"] == category
                    and row["folder"] == (folder or ""))
            evidence = row["evidence"] + 1 if same else 1
            c.execute(
                "INSERT OR REPLACE INTO learned (account, kind, key, category, folder, weight, evidence, "
                "source, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account, kind, key, category, folder or "", weight, evidence, FROM_CORRECTION,
                 _ts(utcnow())),
            )

    def forget(self, account: str, kind: str, key: str) -> bool:
        """Remove one learned rule. Returns True when something was removed."""
        with self._store._tx() as c:
            cur = c.execute("DELETE FROM learned WHERE account = ? AND kind = ? AND key = ?",
                            (account, kind, _normalize_key(kind, key)))
            return cur.rowcount > 0

    # ---- reading --------------------------------------------------------------------------------
    def all(self, *, source: str | None = None) -> list[LearnedRule]:
        sql, args = "SELECT * FROM learned", ()
        if source:
            sql, args = sql + " WHERE source = ?", (source,)
        return [_learned(r) for r in self._store._query(sql + " ORDER BY updated DESC", args)]

    def count(self, *, source: str | None = None) -> int:
        if source:
            return self._store._scalar("SELECT COUNT(*) FROM learned WHERE source = ?", (source,))
        return self._store._scalar("SELECT COUNT(*) FROM learned")


def _learned(row: sqlite3.Row) -> LearnedRule:
    return LearnedRule(row["account"], row["kind"], row["key"], row["category"], row["folder"],
                       row["weight"], row["evidence"], row["source"], _dt(row["updated"]))


def _decision(rule: LearnedRule) -> Decision:
    where = rule.folder or rule.category
    what = {"address": "this sender", "list_id": "this mailing list", "domain": "this sender's domain"}[rule.kind]
    if rule.source == FROM_CORRECTION:
        if rule.category == INBOX and not rule.folder:
            reason = f"You moved mail from {what} back to the Inbox"
        else:
            reason = f"You moved mail from {what} to {where}"
    else:
        reason = f"You keep mail from {what} in {where}"
    return Decision(
        category=rule.category,
        confidence=rule.weight,
        source="learned",
        reason=reason,
        folder=rule.folder or None,
        explicit=rule.source == FROM_CORRECTION,
    )


def _move(row: sqlite3.Row) -> StoredMove:
    return StoredMove(
        run_id=row["run_id"], account=row["account"], message_id=row["message_id"], sender=row["sender"],
        subject=row["subject"], from_folder=row["from_folder"], to_folder=row["to_folder"],
        category=row["category"], source=row["source"], reason=row["reason"], at=_dt(row["at"]),
        new_uid=row["new_uid"], undone=bool(row["undone"]), id=row["id"], check_state=row["check_state"],
    )


class Store:
    """The SQLite state file. One connection, safe to use as a context manager."""

    def __init__(self, path: str | Path):
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            str(self.path) if self.path else ":memory:", isolation_level=None, check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000")
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        self.learned = LearnedStore(self)

    # ---- plumbing -------------------------------------------------------------------------------
    def __enter__(self) -> Store:  # noqa: PYI034 - typing.Self needs Python 3.11
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Close the file; safe to call twice."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("The TacoSort state file is already closed.")
        return self._conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            c = self.conn
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def _scalar(self, sql: str, args: tuple = ()):
        rows = self._query(sql, args)
        return rows[0][0] if rows else None

    def _migrate(self) -> None:
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = c.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
        if current > SCHEMA_VERSION:
            raise StoreError(
                f"The state file {self.path} was made by a newer version of TacoSort. "
                "Please update TacoSort (pip install --upgrade tacosort)."
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            with self._tx() as tx:
                for statement in MIGRATIONS[version]:
                    tx.execute(statement)
                tx.execute("DELETE FROM schema_version")
                tx.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    @property
    def schema_version(self) -> int:
        return self._scalar("SELECT version FROM schema_version") or 0

    # ---- meta (small key/value settings) --------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        value = self._scalar("SELECT value FROM meta WHERE key = ?", (key,))
        return default if value is None else value

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    # ---- runs -----------------------------------------------------------------------------------
    def start_run(self, run_id: str, started: datetime, dry_run: bool) -> bool:
        """Record a new run. False (and nothing changed) when `run_id` is already taken."""
        with self._tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO runs (id, started, dry_run) VALUES (?, ?, ?)",
                            (run_id, _ts(started), int(dry_run)))
            return cur.rowcount == 1

    def finish_run(self, report: RunReport) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs (id, started, finished, dry_run, scanned, moved, kept, learned, "
                "errors) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report.run_id, _ts(report.started), _ts(report.finished or utcnow()), int(report.dry_run),
                 report.scanned, report.moved, report.kept, report.learned, json.dumps(report.errors)),
            )

    def runs(self, limit: int = 20, *, dry_run: bool | None = None) -> list[RunReport]:
        """Most recent runs first (without their move lists)."""
        sql, args = "SELECT * FROM runs", ()
        if dry_run is not None:
            sql, args = sql + " WHERE dry_run = ?", (int(dry_run),)
        rows = self._query(sql + " ORDER BY started DESC, rowid DESC LIMIT ?", args + (limit,))
        return [
            RunReport(run_id=r["id"], dry_run=bool(r["dry_run"]), started=_dt(r["started"]),
                      finished=_dt(r["finished"]), scanned=r["scanned"], moved=r["moved"], kept=r["kept"],
                      learned=r["learned"], errors=json.loads(r["errors"] or "[]"))
            for r in rows
        ]

    def last_run(self, *, dry_run: bool | None = False) -> dict | None:
        """The latest real run (dry_run=False), preview (True) or either (None), as a dict with keys
        run_id, started, finished, dry_run, scanned, moved, kept, learned, errors (a list)."""
        runs = self.runs(1, dry_run=dry_run)
        if not runs:
            return None
        r = runs[0]
        return {"run_id": r.run_id, "started": r.started, "finished": r.finished, "dry_run": r.dry_run,
                "scanned": r.scanned, "moved": r.moved, "kept": r.kept, "learned": r.learned,
                "errors": r.errors}

    def has_previewed(self) -> bool:
        """True once a dry run (preview) has finished at least once."""
        return bool(self._scalar("SELECT COUNT(*) FROM runs WHERE dry_run = 1 AND finished IS NOT NULL"))

    # ---- moves ----------------------------------------------------------------------------------
    def record_moves(self, records: Iterable[MoveRecord]) -> None:
        with self._tx() as c:
            c.executemany(
                "INSERT INTO moves (run_id, account, message_id, sender, subject, from_folder, to_folder, "
                "category, source, reason, at, new_uid, undone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(m.run_id, m.account, m.message_id, m.sender, m.subject, m.from_folder, m.to_folder,
                  m.category, m.source, m.reason, _ts(m.at), m.new_uid, int(m.undone)) for m in records],
            )

    def moves_for_run(self, run_id: str, *, include_undone: bool = False) -> list[StoredMove]:
        sql = "SELECT * FROM moves WHERE run_id = ?" + ("" if include_undone else " AND undone = 0")
        return [_move(r) for r in self._query(sql + " ORDER BY id", (run_id,))]

    def recent_moves(self, limit: int = 200, *, account: str | None = None,
                     include_undone: bool = True) -> list[StoredMove]:
        """Newest moves first."""
        where, args = [], []
        if account:
            where.append("account = ?")
            args.append(account)
        if not include_undone:
            where.append("undone = 0")
        sql = "SELECT * FROM moves" + (" WHERE " + " AND ".join(where) if where else "")
        return [_move(r) for r in self._query(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def last_run_with_moves(self) -> str | None:
        """Id of the latest run that still has moves that were not undone."""
        return self._scalar("SELECT run_id FROM moves WHERE undone = 0 ORDER BY id DESC LIMIT 1")

    def mark_undone(self, move_ids: Iterable[int]) -> None:
        with self._tx() as c:
            c.executemany("UPDATE moves SET undone = 1 WHERE id = ?", [(i,) for i in move_ids])

    def moves_since(self, since: datetime, *, account: str | None = None) -> int:
        """How many moves (not undone) happened since `since`."""
        sql, args = "SELECT COUNT(*) FROM moves WHERE undone = 0 AND at >= ?", (_ts(since),)
        if account:
            sql, args = sql + " AND account = ?", args + (account,)
        return self._scalar(sql, args)

    def move_counts(self) -> dict[tuple[str, str], int]:
        """(account, to_folder) -> number of moves there that were not undone."""
        rows = self._query("SELECT account, to_folder, COUNT(*) AS n FROM moves WHERE undone = 0 "
                           "GROUP BY account, to_folder")
        return {(r["account"], r["to_folder"]): r["n"] for r in rows}

    def filed_senders(self, account: str) -> list[StoredMove]:
        """Moves into a category folder that are still in place (not undone, not corrected or gone):
        what company folders count and tidy."""
        return [_move(r) for r in self._query(
            "SELECT * FROM moves WHERE account = ? AND undone = 0 AND check_state = '' AND message_id != '' "
            "AND category NOT IN (?, 'folder') ORDER BY id", (account, INBOX))]

    def refile_move(self, move_id: int, to_folder: str, new_uid: int | None) -> None:
        """TacoSort moved this message on into a company folder: the move now ends there, so undo and
        the correction check look for it in the right place."""
        with self._tx() as c:
            c.execute("UPDATE moves SET to_folder = ?, new_uid = ?, next_check = NULL WHERE id = ?",
                      (to_folder, new_uid, move_id))

    # ---- company folders ------------------------------------------------------------------------
    def company_folders(self, account: str) -> dict[tuple[str, str], tuple[str, bool]]:
        """(category, company) -> (folder, tidied) for this account."""
        rows = self._query("SELECT category, company, folder, tidied FROM company_folders WHERE account = ?",
                           (account,))
        return {(r["category"], r["company"]): (r["folder"], bool(r["tidied"])) for r in rows}

    def add_company_folder(self, account: str, category: str, company: str, folder: str) -> None:
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO company_folders (account, category, company, folder, created) "
                      "VALUES (?, ?, ?, ?, ?)", (account, category, company, folder, _ts(utcnow())))

    def note_look_alikes(self, account: str, rows: list[tuple[str, str, str]]) -> None:
        """Remember (message_id, sender, subject template) of messages read; repeats are ignored."""
        if not rows:
            return
        now = _ts(utcnow())
        with self._tx() as c:
            c.executemany("INSERT OR IGNORE INTO look_alikes (account, message_id, sender, template, added) "
                          "VALUES (?, ?, ?, ?, ?)", [(account, mid, s, t, now) for mid, s, t in rows])

    def look_alike_counts(self, account: str, keys: set[tuple[str, str]]) -> dict[tuple[str, str], int]:
        """How many messages TacoSort has read for each (sender, subject template)."""
        out: dict[tuple[str, str], int] = {}
        for sender, template in keys:
            out[(sender, template)] = int(self._scalar(
                "SELECT COUNT(*) FROM look_alikes WHERE account = ? AND sender = ? AND template = ?",
                (account, sender, template)) or 0)
        return out

    def look_alike_sender_counts(self, account: str, senders: set[str]) -> dict[str, int]:
        """How many messages TacoSort has read from each address, whatever their subject."""
        return {s: int(self._scalar("SELECT COUNT(*) FROM look_alikes WHERE account = ? AND sender = ?",
                                    (account, s)) or 0) for s in senders}

    def mark_company_tidied(self, account: str, category: str, company: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE company_folders SET tidied = 1 WHERE account = ? AND category = ? AND company = ?",
                      (account, category, company))

    def moved_by_us(self, account: str, message_id: str) -> bool:
        if not message_id:
            return False
        return bool(self._scalar("SELECT 1 FROM moves WHERE account = ? AND message_id = ? LIMIT 1",
                                 (account, message_id)))

    def moves_for_message(self, account: str, message_id: str) -> list[StoredMove]:
        """Every move of this Message-ID in this account, oldest first."""
        if not message_id:
            return []
        return [_move(r) for r in self._query("SELECT * FROM moves WHERE account = ? AND message_id = ? "
                                              "ORDER BY id", (account, message_id))]

    def moves_to_check(self, account: str, since: datetime, limit: int = 200, *,
                       now: datetime | None = None) -> list[StoredMove]:
        """Recent, not undone moves whose message we still watch for user corrections and that are due
        for a check (never checked yet, or their next check time has come, see RECHECK_EVERY). Never
        checked and longest overdue first, so a bounded check eventually covers all of them."""
        rows = self._query(
            "SELECT * FROM moves WHERE account = ? AND undone = 0 AND check_state = '' AND message_id != '' "
            "AND at >= ? AND (next_check IS NULL OR next_check <= ?) "
            "ORDER BY next_check IS NOT NULL, next_check, id DESC LIMIT ?",
            (account, _ts(since), _ts(now or utcnow()), limit),
        )
        return [_move(r) for r in rows]

    def mark_move_checked(self, move_id: int, state: str = "", *, now: datetime | None = None) -> None:
        """Record a correction check: state "" = still in place, "gone", or "corrected:<folder>".
        A move still in place is checked again later: hourly in its first day, daily in its first week,
        weekly in its first month."""
        now = now or utcnow()
        with self._tx() as c:
            row = c.execute("SELECT at FROM moves WHERE id = ?", (move_id,)).fetchone()
            age = now - _dt(row["at"]) if row else RECHECK_LATE
            every = next((gap for limit, gap in RECHECK_EVERY if age < limit), RECHECK_LATE)
            c.execute("UPDATE moves SET last_checked = ?, check_state = ?, next_check = ? WHERE id = ?",
                      (_ts(now), state, _ts(now + every), move_id))

    def corrections(self, limit: int = 100) -> list[dict]:
        """Moves the user changed by hand, newest first: dicts with at (when we noticed), account,
        sender, subject, from_folder (where we had put it) and to_folder (where the user put it)."""
        rows = self._query("SELECT * FROM moves WHERE check_state LIKE 'corrected:%' ORDER BY last_checked "
                           "DESC, id DESC LIMIT ?", (limit,))
        return [{"at": _dt(r["last_checked"]), "account": r["account"], "sender": r["sender"],
                 "subject": r["subject"], "from_folder": r["to_folder"],
                 "to_folder": r["check_state"].split(":", 1)[1]} for r in rows]

    def learned_rules(self) -> list[dict]:
        """Every learned rule as a dict (account, kind, key, category, folder, weight, evidence, source,
        updated), newest first."""
        return [dict(vars(rule)) for rule in self.learned.all()]

    # ---- watermarks -----------------------------------------------------------------------------
    def get_watermark(self, account: str, folder: str) -> tuple[int, int] | None:
        """(uidvalidity, last_uid) for a folder, or None when it was never read."""
        rows = self._query("SELECT uidvalidity, last_uid FROM watermarks WHERE account = ? AND folder = ?",
                           (account, folder))
        return (rows[0]["uidvalidity"], rows[0]["last_uid"]) if rows else None

    def set_watermark(self, account: str, folder: str, uidvalidity: int, last_uid: int) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO watermarks (account, folder, uidvalidity, last_uid, updated) "
                "VALUES (?, ?, ?, ?, ?)", (account, folder, uidvalidity, last_uid, _ts(utcnow())))

    def clear_watermarks(self, account: str | None = None) -> None:
        with self._tx() as c:
            if account:
                c.execute("DELETE FROM watermarks WHERE account = ?", (account,))
            else:
                c.execute("DELETE FROM watermarks")

    # ---- contacts -------------------------------------------------------------------------------
    def add_contacts(self, addresses: Iterable[str], account: str = "") -> int:
        """Add people the user writes to. Returns how many were new."""
        clean = sorted({a.strip().lower() for a in addresses if a and "@" in a})
        before = self.contact_count()
        with self._tx() as c:
            c.executemany("INSERT OR IGNORE INTO contacts (address, account, added) VALUES (?, ?, ?)",
                          [(a, account, _ts(utcnow())) for a in clean])
        return self.contact_count() - before

    def contacts(self) -> set[str]:
        return {r["address"] for r in self._query("SELECT address FROM contacts")}

    def contact_count(self) -> int:
        return self._scalar("SELECT COUNT(*) FROM contacts")

    # ---- seen -----------------------------------------------------------------------------------
    def seen(self, account: str, message_id: str) -> SeenEntry | None:
        if not message_id:
            return None
        rows = self._query("SELECT decision, override, updated FROM seen WHERE account = ? AND message_id = ?",
                           (account, message_id))
        if not rows:
            return None
        return SeenEntry(rows[0]["decision"], bool(rows[0]["override"]), _dt(rows[0]["updated"]))

    def mark_seen(self, account: str, message_id: str, decision: str, *, override: bool = False) -> None:
        """Remember our last decision for a message. Once overridden by the user, it stays overridden."""
        self.mark_seen_many(account, [(message_id, decision)], override=override)

    def mark_seen_many(self, account: str, items: Iterable[tuple[str, str]], *, override: bool = False) -> None:
        rows = [(account, mid, decision, int(override), _ts(utcnow())) for mid, decision in items if mid]
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO seen (account, message_id, decision, override, updated) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(account, message_id) DO UPDATE SET decision = excluded.decision, "
                "override = MAX(seen.override, excluded.override), updated = excluded.updated",
                rows,
            )

    # ---- summary for `tacosort status` ----------------------------------------------------------
    def summary(self) -> dict:
        """Counts for a status screen."""
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        last = self.last_run(dry_run=False)
        return {
            "last_run": last,
            "moves_total": self._scalar("SELECT COUNT(*) FROM moves WHERE undone = 0"),
            "moves_today": self.moves_since(today),
            "learned_rules": self.learned.count(),
            "corrections": self._scalar("SELECT COUNT(*) FROM moves WHERE check_state LIKE 'corrected:%'"),
            "contacts": self.contact_count(),
        }
