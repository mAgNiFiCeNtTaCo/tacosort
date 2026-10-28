"""Known contacts: the people the user has written to. Their mail always stays in the Inbox."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Message

# Local parts that are never people (replying to them doesn't make them a contact).
_NOT_PEOPLE = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "mailer-daemon", "postmaster", "bounce", "bounces", "notifications", "notification",
)


def is_robot_address(email: str) -> bool:
    """True for addresses like noreply@..., mailer-daemon@..., bounces+123@..."""
    local = email.lower().rsplit("@", 1)[0]
    local = local.split("+", 1)[0]
    return any(local == word or local.startswith((word + "-", word + "."))
               for word in _NOT_PEOPLE) or "noreply" in local or "no-reply" in local


def contacts_from_sent(messages: list[Message], own: Iterable[str] = ()) -> set[str]:
    """Every To/Cc address of mail the user sent (lower-case), minus the user's own addresses
    (the senders of these messages plus `own`) and robot addresses like noreply@."""
    mine = {e.lower() for e in own} | {m.from_.email.lower() for m in messages if m.from_.email}
    found: set[str] = set()
    for m in messages:
        for addr in m.to + m.cc:
            email = addr.email.strip().lower()
            if "@" not in email or email in mine or is_robot_address(email):
                continue
            found.add(email)
    return found
