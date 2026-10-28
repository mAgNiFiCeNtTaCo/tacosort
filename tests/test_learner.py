from __future__ import annotations

import sys

import pytest

from mailtidy import learner
from mailtidy.models import INBOX, AccountConfig, Address, AppConfig, FolderInfo, MoveRecord
from mailtidy.store import ANY_ACCOUNT, Store, utcnow
from mailtidy.testing import FakeBackend, make_message


def cfg(**kw) -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], **kw)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "state.sqlite3") as s:
        yield s


def sent(to: list[str], subject: str):
    m = make_message("me@example.com", subject)
    m.to = [Address(t) for t in to]
    return m


@pytest.fixture
def fb():
    b = FakeBackend("a")
    for s in ("Fees", "Trip", "Concert"):
        b.add("Kids School", make_message("office@school.example.org", s))
    b.add("Kids School", make_message("coach@school.example.org", "Practice"))
    b.add("Receipts", make_message("orders@shop.example.com", "Order 1"))
    b.add("Receipts", make_message("orders@shop.example.com", "Order 2"))
    # A friend split between two folders: no clear habit, no rule.
    b.add("Family", make_message("pal@gmail.com", "Photos"))
    b.add("Family", make_message("pal@gmail.com", "BBQ"))
    b.add("Work", make_message("pal@gmail.com", "Job lead"))
    b.add("Family", make_message("aunt@gmail.com", "Hello"))
    b.add("Family", make_message("aunt@gmail.com", "Hello again"))
    # Seen only once: not enough.
    b.add("Work", make_message("boss@corp.example.net", "Plan"))
    # System folders must be ignored.
    b._create("[Gmail]/All Mail", frozenset({"\\All"}))
    b._create("[Gmail]/Starred")
    for i in range(3):
        b.add("[Gmail]/All Mail", make_message("x@all.example.com", f"all {i}"))
        b.add("[Gmail]/Starred", make_message("y@star.example.com", f"star {i}"))
        b.add("Trash", make_message("z@trash.example.com", f"trash {i}"))
        b.add("INBOX", make_message("w@inbox.example.com", f"inbox {i}"))
    b.add("Sent", sent(["friend@example.org", "Me@example.com"], "Hi"))
    b.add("Sent", sent(["noreply@service.example.com", "aunt@gmail.com"], "Re: Hello"))
    return b


def rules(store):
    return {(r.account, r.kind, r.key): (r.category, r.folder) for r in store.learned.all()}


def test_learns_the_users_own_scheme_and_contacts(store, fb, monkeypatch):
    monkeypatch.setitem(sys.modules, "mailtidy.classify.contacts", None)     # force the built-in fallback
    report = learner.learn_from_folders(cfg(), store, {"a": fb}, log=lambda m: None)
    got = rules(store)
    assert got[("a", "address", "office@school.example.org")] == ("folder", "Kids School")
    assert got[("a", "domain", "school.example.org")] == ("folder", "Kids School")    # two senders agree
    assert got[(ANY_ACCOUNT, "address", "orders@shop.example.com")] == ("receipts", "")  # scheme folder
    assert got[("a", "address", "aunt@gmail.com")] == ("folder", "Family")
    assert not any(k[2] == "gmail.com" for k in got)                                # freemail: no domain rule
    assert not any(k[2] == "pal@gmail.com" for k in got)                            # 2/3 in one folder < 80%
    assert not any(k[2] == "boss@corp.example.net" for k in got)                    # seen once
    assert not any("example.com" in k[2] and k[2].split("@")[-1] in
                   ("all.example.com", "star.example.com", "trash.example.com", "inbox.example.com")
                   for k in got)
    assert store.contacts() == {"friend@example.org", "aunt@gmail.com"}
    assert report.contacts_found == 2 and report.errors == []
    assert report.folders_scanned == 4                     # Kids School, Receipts, Family, Work
    d = store.learned.lookup("office@school.example.org", "school.example.org", "", "a")
    assert d.folder == "Kids School" and d.confidence >= 0.75


def test_uses_classifier_contacts_helper_when_present(store, fb, monkeypatch):
    import types

    mod = types.ModuleType("mailtidy.classify.contacts")
    mod.contacts_from_sent = lambda msgs: {"Helper@Example.org", "me@example.com"}
    monkeypatch.setitem(sys.modules, "mailtidy.classify.contacts", mod)
    learner.learn_from_folders(cfg(), store, {"a": fb}, log=lambda m: None)
    assert store.contacts() == {"helper@example.org"}           # own address removed


def test_learning_skips_mail_mailtidy_filed_itself(store):
    b = FakeBackend("a")
    for s in ("1", "2"):
        b.add("Receipts", make_message("orders@shop.example.com", s, message_id=f"<{s}@example.com>"))
        store.record_moves([MoveRecord("r", "a", f"<{s}@example.com>", "orders@shop.example.com", s, "INBOX",
                                       "Receipts", "receipts", "heuristic", "", utcnow())])
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    assert store.learned.count() == 0


def test_prefix_scheme_means_existing_folder_is_custom(store, fb, monkeypatch):
    monkeypatch.setitem(sys.modules, "mailtidy.classify.contacts", None)
    learner.learn_from_folders(cfg(folder_prefix="MailTidy"), store, {"a": fb}, log=lambda m: None)
    assert rules(store)[("a", "address", "orders@shop.example.com")] == ("folder", "Receipts")


def test_folder_filters():
    assert learner.is_learnable_folder(FolderInfo("Kids School"))
    assert learner.is_learnable_folder(FolderInfo("INBOX.Clients", "."))
    for f in (FolderInfo("INBOX"), FolderInfo("Sent", flags=frozenset({"\\Sent"})), FolderInfo("[Gmail]/Spam"),
              FolderInfo("Parent", flags=frozenset({"\\Noselect"})), FolderInfo("Deleted Items"),
              FolderInfo("INBOX.Trash", ".")):
        assert not learner.is_learnable_folder(f), f
    assert learner.find_sent_folder([FolderInfo("INBOX"), FolderInfo("Sent Items")]) == "Sent Items"
    assert learner.find_sent_folder([FolderInfo("x", flags=frozenset({"\\Sent"}))]) == "x"


def test_broken_account_is_reported(store):
    report = learner.learn_from_folders(cfg(), store, {"a": FakeBackend("a", fail_connect="Login failed.")},
                                        log=lambda m: None)
    assert report.errors == ["a: Login failed."]
    report = learner.learn_from_folders(cfg(), store, {}, log=lambda m: None)
    assert report.errors == ["a: no connection was set up for this account."]


def _moved(store, b, subject, folder="Receipts", sender="orders@shop.example.com"):
    mid = f"<{subject}@example.com>"
    b.add(folder, make_message(sender, subject, message_id=mid))
    store.record_moves([MoveRecord("r", "a", mid, sender, subject, "INBOX", folder, "receipts", "heuristic", "",
                                   utcnow())])
    return mid


def test_detect_corrections_cases(store):
    b = FakeBackend("a")
    stay = _moved(store, b, "stays")
    back = _moved(store, b, "back", sender="person@shop.example.com")
    other = _moved(store, b, "other", sender="bills@shop.example.com")
    gone = _moved(store, b, "gone", sender="gone@shop.example.com")
    b.user_move("back", "Receipts", "INBOX")
    b.user_move("other", "Receipts", "Finance")
    b.user_move("gone", "Receipts", "Trash")
    b.connect()
    n = learner.detect_corrections(store, b, "a", config=cfg())
    assert n == 2
    assert store.learned.lookup("person@shop.example.com", "", "", "zzz").category == INBOX   # all accounts
    assert store.seen("a", back).override
    assert store.learned.lookup("bills@shop.example.com", "", "", "a").category == "finance"
    assert store.learned.lookup("gone@shop.example.com", "", "", "a") is None
    assert store.learned.lookup("orders@shop.example.com", "", "", "a") is None
    states = {m.message_id: m.check_state for m in store.recent_moves()}
    assert states == {stay: "", back: "corrected:INBOX", other: "corrected:Finance", gone: "gone"}
    # Already handled corrections are not counted twice.
    assert learner.detect_corrections(store, b, "a", config=cfg()) == 0


def test_detect_corrections_is_bounded(store):
    b = FakeBackend("a")
    for i in range(10):
        _moved(store, b, f"m{i}")
    b.connect()
    calls = []
    orig = b.find_message_id
    b.find_message_id = lambda f, mid: calls.append(f) or orig(f, mid)
    learner.detect_corrections(store, b, "a", config=cfg(), max_lookups=4)
    assert len(calls) == 4
    # The next check starts with the ones not looked at yet.
    calls.clear()
    learner.detect_corrections(store, b, "a", config=cfg(), max_lookups=4)
    assert len(calls) == 4
    checked = [m for m in store.recent_moves() if m.id and m.check_state == ""]
    assert len(checked) == 10
