"""In-memory MailBackend for tests and demos. Behaves like a well-behaved IMAP server with UIDPLUS.

Owned by the orchestrator: propose changes instead of editing (see SPEC.md).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from .backend import BackendError
from .models import Address, FolderInfo, FolderState, Message


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


class FakeBackend:
    """Folders hold {uid: Message}. Special folders: INBOX, Sent (\\Sent), Trash (\\Trash), Spam (\\Junk)."""

    def __init__(self, account_id: str = "test", *, delimiter: str = "/", uidplus: bool = True,
                 fail_connect: str | None = None):
        self.account_id = account_id
        self.delimiter = delimiter
        self.uidplus = uidplus
        self.fail_connect = fail_connect
        self.connected = False
        self.calls: list[tuple] = []          # (verb, args...) log for assertions
        self._folders: dict[str, dict[int, Message]] = {}
        self._flags: dict[str, frozenset[str]] = {}
        self._uidnext: dict[str, int] = {}
        self._uidvalidity: dict[str, int] = {}
        for name, flags in (("INBOX", ()), ("Sent", ("\\Sent",)), ("Trash", ("\\Trash",)), ("Spam", ("\\Junk",))):
            self._create(name, frozenset(flags))

    # ---- test helpers -------------------------------------------------------------------------
    def _create(self, name: str, flags: frozenset[str] = frozenset()) -> None:
        if name not in self._folders:
            self._folders[name] = {}
            self._flags[name] = flags
            self._uidnext[name] = 1
            self._uidvalidity[name] = 1000 + len(self._folders)

    def add(self, folder: str, msg: Message) -> int:
        """Put a copy of `msg` into `folder` (created if needed); returns its UID."""
        self._create(folder)
        uid = self._uidnext[folder]
        self._uidnext[folder] += 1
        self._folders[folder][uid] = dataclasses.replace(msg, account=self.account_id, folder=folder, uid=uid)
        return uid

    def messages(self, folder: str) -> list[Message]:
        return list(self._folders.get(folder, {}).values())

    def subjects(self, folder: str) -> list[str]:
        return [m.subject for m in self.messages(folder)]

    def user_move(self, subject: str, src: str, dest: str) -> None:
        """Simulate the user dragging a message (by subject) from src to dest in their mail app."""
        for uid, m in list(self._folders[src].items()):
            if m.subject == subject:
                self._move_one(src, uid, dest)
                return
        raise KeyError(subject)

    def reset_uidvalidity(self, folder: str) -> None:
        """Simulate a server renumbering a folder (all UIDs change)."""
        msgs = list(self._folders[folder].values())
        self._folders[folder] = {}
        self._uidvalidity[folder] += 1
        self._uidnext[folder] = 1
        for m in msgs:
            self.add(folder, m)

    def _move_one(self, src: str, uid: int, dest: str) -> int:
        msg = self._folders[src].pop(uid)
        return self.add(dest, msg)

    def _need(self, folder: str) -> dict[int, Message]:
        if not self.connected:
            raise BackendError("not connected")
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
        server_name = name.replace("/", self.delimiter)
        parts = server_name.split(self.delimiter)
        for i in range(1, len(parts) + 1):
            self._create(self.delimiter.join(parts[:i]))
        self.calls.append(("ensure_folder", server_name))
        return server_name

    def folder_state(self, folder: str) -> FolderState:
        box = self._need(folder)
        return FolderState(self._uidvalidity[folder], self._uidnext[folder], len(box))

    def search_uids(self, folder: str, *, min_uid: int | None = None, limit: int | None = None) -> list[int]:
        uids = sorted(u for u in self._need(folder) if min_uid is None or u >= min_uid)
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
        self.calls.append(("move", folder, tuple(uids), dest))
        result: dict[int, int | None] = {}
        for u in uids:
            if u in box:
                new = self._move_one(folder, u, dest)
                result[u] = new if self.uidplus else None
        return result

    def find_message_id(self, folder: str, message_id: str) -> list[int]:
        return sorted(u for u, m in self._need(folder).items() if message_id and m.message_id == message_id)

    def idle(self, folder: str, timeout: int) -> bool:
        self._need(folder)
        return False
