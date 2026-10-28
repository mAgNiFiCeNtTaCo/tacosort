"""In-memory MailBackend for tests and demos.

By default it behaves like a well-behaved IMAP server with UIDPLUS. Options make it act like real,
messier servers, so the engine can be tested against them:

- `gmail=True`: Gmail's model. Folders are labels: one message can sit in INBOX and in "Family" at the
  same time (each copy has its own UID and the same `gmail_labels`), "[Gmail]/All Mail" holds every
  message, and moving out of INBOX removes the Inbox label and adds the destination label.
- `uidplus=False`: a server without UIDPLUS. A move copies the message and leaves the original in the
  source flagged \\Deleted (search_uids / find_message_id skip it), and the new UID is not reported.
- `move_filter=callable(uid) -> bool`: only UIDs it approves are moved; the rest are left in place and
  missing from the result (a partial move).

Owned by the orchestrator: propose changes instead of editing (see SPEC.md).
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Callable
from datetime import datetime, timezone

from .backend import BackendError
from .models import Address, FolderInfo, FolderState, Message

GMAIL_ALL = "[Gmail]/All Mail"
GMAIL_SENT = "[Gmail]/Sent Mail"


def make_message(
    sender: str,
    subject: str,
    *,
    name: str = "",
    to: str = "me@example.com",
    message_id: str | None = None,
    headers: dict[str, str] | None = None,
    flags: frozenset[str] = frozenset(),
    snippet: str = "",
    date: datetime | None = None,
) -> Message:
    """Build a Message for a test. account/folder/uid are filled in by FakeBackend.add()."""
    return Message(
        account="",
        folder="",
        uid=0,
        message_id=message_id if message_id is not None else f"<{abs(hash((sender, subject)))}@example.com>",
        subject=subject,
        from_=Address(sender.lower(), name),
        to=[Address(to.lower())],
        date=date or datetime(2026, 1, 1, tzinfo=timezone.utc),
        headers={k.lower(): v for k, v in (headers or {}).items()},
        flags=flags,
        snippet=snippet,
    )


def _label(folder: str) -> str:
    """Gmail label name as X-GM-LABELS shows it."""
    return "\\Inbox" if folder == "INBOX" else folder


class FakeBackend:
    """Folders hold {uid: Message}. Special folders: INBOX, Sent (\\Sent), Trash (\\Trash), Spam (\\Junk);
    with gmail=True: INBOX, [Gmail]/Sent Mail, All Mail, Trash, Spam, Drafts."""

    def __init__(self, account_id: str = "test", *, delimiter: str = "/", uidplus: bool = True,
                 fail_connect: str | None = None, gmail: bool = False,
                 move_filter: Callable[[int], bool] | None = None):
        self.account_id = account_id
        self.delimiter = delimiter
        self.uidplus = uidplus
        self.fail_connect = fail_connect
        self.gmail = gmail
        self.move_filter = move_filter
        self.connected = False
        self.calls: list[tuple] = []          # (verb, args...) log for assertions
        self._folders: dict[str, dict[int, Message]] = {}
        self._flags: dict[str, frozenset[str]] = {}
        self._uidnext: dict[str, int] = {}
        self._uidvalidity: dict[str, int] = {}
        self._gid: dict[tuple[str, int], int] = {}     # (folder, uid) -> Gmail message id
        self._next_gid = itertools.count(1)
        if gmail:
            self._create("INBOX")
            self._create("[Gmail]", frozenset({"\\Noselect", "\\HasChildren"}))
            for name, flag in ((GMAIL_SENT, "\\Sent"), (GMAIL_ALL, "\\All"), ("[Gmail]/Trash", "\\Trash"),
                               ("[Gmail]/Spam", "\\Junk"), ("[Gmail]/Drafts", "\\Drafts")):
                self._create(name, frozenset({flag}))
        else:
            for name, flags in (("INBOX", ()), ("Sent", ("\\Sent",)), ("Trash", ("\\Trash",)),
                                ("Spam", ("\\Junk",))):
                self._create(name, frozenset(flags))

    # ---- test helpers -------------------------------------------------------------------------
    @property
    def sent_folder(self) -> str:
        return GMAIL_SENT if self.gmail else "Sent"

    def _create(self, name: str, flags: frozenset[str] = frozenset()) -> None:
        if name not in self._folders:
            self._folders[name] = {}
            self._flags[name] = flags
            self._uidnext[name] = 1
            self._uidvalidity[name] = 1000 + len(self._folders)

    def _resolve(self, folder: str) -> str:
        """INBOX is case-insensitive on every IMAP server; other names are exact."""
        return "INBOX" if folder.upper() == "INBOX" else folder

    def _put(self, folder: str, msg: Message, gid: int | None) -> int:
        self._create(folder)
        uid = self._uidnext[folder]
        self._uidnext[folder] += 1
        self._folders[folder][uid] = dataclasses.replace(msg, account=self.account_id, folder=folder, uid=uid)
        if gid is not None:
            self._gid[(folder, uid)] = gid
        return uid

    def _copies(self, gid: int) -> list[tuple[str, int]]:
        return [key for key, g in self._gid.items() if g == gid and key[1] in self._folders.get(key[0], {})]

    def _relabel(self, gid: int) -> None:
        """Make every copy of a Gmail message show the same X-GM-LABELS."""
        copies = self._copies(gid)
        labels = frozenset(_label(f) for f, _ in copies if f != GMAIL_ALL)
        for f, u in copies:
            self._folders[f][u] = dataclasses.replace(self._folders[f][u], gmail_labels=labels)

    def add(self, folder: str, msg: Message) -> int:
        """Put a copy of `msg` into `folder` (created if needed); returns its UID.
        In Gmail mode it is also added to All Mail and gets its label."""
        folder = self._resolve(folder)
        if not self.gmail:
            return self._put(folder, msg, None)
        return self.add_labeled(msg, [folder])[folder]

    def add_labeled(self, msg: Message, folders: list[str]) -> dict[str, int]:
        """Gmail mode: one message carrying several labels, e.g. ["INBOX", "Family"].
        Returns folder -> UID (All Mail included)."""
        if not self.gmail:
            raise ValueError("add_labeled needs FakeBackend(gmail=True)")
        gid = next(self._next_gid)
        out = {}
        for f in dict.fromkeys([self._resolve(f) for f in folders] + [GMAIL_ALL]):
            out[f] = self._put(f, msg, gid)
        self._relabel(gid)
        return out

    def messages(self, folder: str, *, include_deleted: bool = False) -> list[Message]:
        """What a mail app would show in `folder` (\\Deleted originals hidden unless asked)."""
        box = self._folders.get(self._resolve(folder), {})
        return [m for m in box.values() if include_deleted or "\\Deleted" not in m.flags]

    def subjects(self, folder: str) -> list[str]:
        return [m.subject for m in self.messages(folder)]

    def user_move(self, subject: str, src: str, dest: str) -> None:
        """Simulate the user dragging a message (by subject) from src to dest in their mail app."""
        src = self._resolve(src)
        for uid, m in list(self._folders[src].items()):
            if m.subject == subject and "\\Deleted" not in m.flags:
                self._move_one(src, uid, self._resolve(dest), leave_original=False)
                return
        raise KeyError(subject)

    def reset_uidvalidity(self, folder: str) -> None:
        """Simulate a server renumbering a folder (all UIDs change)."""
        folder = self._resolve(folder)
        old = list(self._folders[folder].items())
        self._folders[folder] = {}
        self._uidvalidity[folder] += 1
        self._uidnext[folder] = 1
        for uid, m in old:
            gid = self._gid.pop((folder, uid), None)
            self._put(folder, m, gid)

    def _move_one(self, src: str, uid: int, dest: str, *, leave_original: bool) -> int | None:
        if self.gmail:
            gid = self._gid[(src, uid)]
            msg = self._folders[src][uid]
            if src != GMAIL_ALL:                          # moving out of All Mail only adds a label
                del self._folders[src][uid]
                del self._gid[(src, uid)]
            existing = [u for f, u in self._copies(gid) if f == dest]
            new = existing[0] if existing else self._put(dest, msg, gid)
            self._relabel(gid)
            return new
        if leave_original:                                # no UIDPLUS: copy, original stays \Deleted
            msg = self._folders[src][uid]
            self._folders[src][uid] = dataclasses.replace(msg, flags=msg.flags | {"\\Deleted"})
            return self._put(dest, msg, None)
        msg = self._folders[src].pop(uid)
        return self._put(dest, msg, None)

    def _need(self, folder: str) -> dict[int, Message]:
        if not self.connected:
            raise BackendError("not connected")
        folder = self._resolve(folder)
        if folder not in self._folders:
            raise BackendError(f"folder {folder!r} does not exist")
        return self._folders[folder]

    # ---- MailBackend ----------------------------------------------------------------------------
    def connect(self) -> None:
        self.calls.append(("connect",))
        if self.fail_connect:
            raise BackendError(self.fail_connect)
        self.connected = True

    def close(self) -> None:
        self.calls.append(("close",))
        self.connected = False

    def list_folders(self) -> list[FolderInfo]:
        if not self.connected:
            raise BackendError("not connected")
        return [FolderInfo(n, self.delimiter, self._flags[n]) for n in self._folders]

    def ensure_folder(self, name: str) -> str:
        if not self.connected:
            raise BackendError("not connected")
        server_name = self._resolve(name.replace("/", self.delimiter))
        for existing in self._folders:                     # same name in other capitals: reuse it
            if existing.lower() == server_name.lower():
                self.calls.append(("ensure_folder", existing))
                return existing
        parts = server_name.split(self.delimiter)
        for i in range(1, len(parts) + 1):
            prefix = self.delimiter.join(parts[:i])
            if not any(e.lower() == prefix.lower() for e in self._folders):
                self._create(prefix)
        self.calls.append(("ensure_folder", server_name))
        return server_name

    def folder_state(self, folder: str) -> FolderState:
        box = self._need(folder)
        folder = self._resolve(folder)
        return FolderState(self._uidvalidity[folder], self._uidnext[folder], len(box))

    def search_uids(self, folder: str, *, min_uid: int | None = None, limit: int | None = None) -> list[int]:
        box = self._need(folder)
        uids = sorted(u for u, m in box.items()
                      if "\\Deleted" not in m.flags and (min_uid is None or u >= min_uid))
        return uids[-limit:] if limit else uids

    def fetch(self, folder: str, uids: list[int], *, with_snippet: bool = False) -> list[Message]:
        box = self._need(folder)
        out = []
        for u in uids:
            if u in box:
                m = box[u]
                out.append(m if with_snippet else dataclasses.replace(m, snippet=""))
        return out

    def move(self, folder: str, uids: list[int], dest: str) -> dict[int, int | None]:
        box = self._need(folder)
        self._need(dest)
        folder, dest = self._resolve(folder), self._resolve(dest)
        self.calls.append(("move", folder, tuple(uids), dest))
        result: dict[int, int | None] = {}
        for u in uids:
            if u not in box or "\\Deleted" in box[u].flags:
                continue
            if self.move_filter is not None and not self.move_filter(u):
                continue
            new = self._move_one(folder, u, dest, leave_original=not self.uidplus and not self.gmail)
            result[u] = new if self.uidplus else None
        return result

    def find_message_id(self, folder: str, message_id: str) -> list[int]:
        return sorted(u for u, m in self._need(folder).items()
                      if message_id and m.message_id == message_id and "\\Deleted" not in m.flags)

    def idle(self, folder: str, timeout: int) -> bool:
        self._need(folder)
        return False
