"""The mailbox interface. `mailtidy.imap.client.ImapBackend` talks to real servers;
`mailtidy.testing.FakeBackend` is an in-memory copy for tests. The engine only uses this.

Owned by the orchestrator: propose changes instead of editing (see SPEC.md).
"""

from __future__ import annotations

from typing import Protocol

from .models import FolderInfo, FolderState, Message


class BackendError(Exception):
    """A mailbox problem worth showing to the user in plain English (login failed, folder missing...)."""


class MailBackend(Protocol):
    account_id: str

    def connect(self) -> None:
        """Log in. Raises BackendError with a plain-English message on failure."""

    def close(self) -> None:
        """Log out; safe to call twice."""

    def list_folders(self) -> list[FolderInfo]:
        """Every folder with its LIST flags (special-use flags tell Sent/Trash/Junk/All apart)."""

    def ensure_folder(self, name: str) -> str:
        """Create `name` if missing (and its parents; "/" in `name` is converted to the server's
        delimiter). An existing folder whose name differs only in capitals is reused, never
        duplicated. Returns the server's full folder name."""

    def folder_state(self, folder: str) -> FolderState:
        """UIDVALIDITY, UIDNEXT and message count, without changing anything."""

    def search_uids(self, folder: str, *, min_uid: int | None = None, limit: int | None = None) -> list[int]:
        """UIDs in `folder`, ascending, skipping messages flagged \\Deleted. `min_uid` keeps UIDs >= it.
        `limit` keeps the NEWEST n."""

    def fetch(self, folder: str, uids: list[int], *, with_snippet: bool = False) -> list[Message]:
        """Envelopes + selected headers for `uids`, read with BODY.PEEK (never sets \\Seen).
        Missing UIDs are skipped silently."""

    def move(self, folder: str, uids: list[int], dest: str) -> dict[int, int | None]:
        """Move `uids` from `folder` to `dest` (MOVE, or COPY + \\Deleted + UID EXPUNGE of only those
        UIDs). Returns old uid -> new uid in `dest` when the server reports it (UIDPLUS), else None.
        A UID missing from the result was not moved, or its outcome is unknown. After a dropped
        connection the move is NOT blindly repeated: the backend reconnects and checks `dest` (by
        Message-ID) so a message is never copied twice and a finished move is still reported.
        On Gmail, moving out of INBOX removes the Inbox label and adds the destination label."""

    def find_message_id(self, folder: str, message_id: str) -> list[int]:
        """UIDs in `folder` whose Message-ID header equals `message_id`, skipping \\Deleted ones."""

    def idle(self, folder: str, timeout: int) -> bool:
        """Wait up to `timeout` seconds for new mail in `folder` (IMAP IDLE, or a poll if unsupported).
        True when something changed."""
