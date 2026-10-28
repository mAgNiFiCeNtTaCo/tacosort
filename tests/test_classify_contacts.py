from __future__ import annotations

from tidyinbox.classify.contacts import contacts_from_sent, is_robot_address
from tidyinbox.models import Address
from tidyinbox.testing import make_message


def sent(to, cc=(), sender="me@example.com"):
    m = make_message(sender, "hi", to=to)
    m.cc = [Address(c.lower()) for c in cc]
    return m


def test_contacts_from_sent():
    msgs = [
        sent("Friend@Example.org", cc=["boss@work.example.com", "me@example.com"]),
        sent("noreply@service.example.com"),
        sent("me2@example.net", sender="me2@example.net"),
        sent("other@example.org", cc=["bounces+123@list.example.com"]),
    ]
    assert contacts_from_sent(msgs, own=["ME@example.com"]) == {
        "friend@example.org", "boss@work.example.com", "other@example.org"}


def test_own_addresses_from_senders_are_excluded():
    msgs = [sent("alias@example.com", sender="alias@example.com"), sent("alias@example.com")]
    assert contacts_from_sent(msgs) == set()


def test_robot_addresses():
    for a in ("noreply@x.example", "no-reply@x.example", "do-not-reply@x.example", "mailer-daemon@x.example",
              "notifications@x.example", "bounces+abc@x.example", "info-noreply@x.example"):
        assert is_robot_address(a), a
    for a in ("alex@x.example", "support@x.example", "norah@x.example"):
        assert not is_robot_address(a), a
