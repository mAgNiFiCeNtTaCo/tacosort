from __future__ import annotations

import sys
from datetime import timedelta

import pytest

from tidyinbox import learner
from tidyinbox.backend import BackendError
from tidyinbox.models import INBOX, AccountConfig, Address, AppConfig, FolderInfo, MoveRecord
from tidyinbox.store import ANY_ACCOUNT, Store, utcnow
from tidyinbox.testing import FakeBackend, make_message


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


LIST = {"List-Id": "School news <news.school.example.org>", "List-Unsubscribe": "<mailto:u@example.org>"}


def bulk(sender: str, subject: str, **kw):
    """A machine-sent message (mailing-list headers)."""
    return make_message(sender, subject, headers=LIST, **kw)


@pytest.fixture
def fb():
    b = FakeBackend("a")
    for s in ("Fees", "Trip", "Concert"):
        b.add("Kids School", bulk("office@school.example.org", s))
    for s in ("Practice", "Games"):
        b.add("Kids School", bulk("coach@school.example.org", s))
    b.add("Receipts", make_message("orders@shop.example.com", "Order 1"))
    b.add("Receipts", make_message("orders@shop.example.com", "Order 2"))
    # A friend split between two folders: no clear habit, no rule.
    b.add("Family", bulk("pal@gmail.com", "Photos"))
    b.add("Family", bulk("pal@gmail.com", "BBQ"))
    b.add("Work", bulk("pal@gmail.com", "Job lead"))
    # A person writing by hand: filed in Family, but people belong in the Inbox, so no rule.
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
    monkeypatch.setattr(learner, "contacts_from_sent", learner._contacts_fallback)   # the built-in fallback
    report = learner.learn_from_folders(cfg(), store, {"a": fb}, log=lambda m: None)
    got = rules(store)
    assert got[("a", "address", "office@school.example.org")] == ("folder", "Kids School")
    assert got[("a", "domain", "school.example.org")] == ("folder", "Kids School")    # 5 from two senders
    assert got[(ANY_ACCOUNT, "address", "orders@shop.example.com")] == ("receipts", "")  # scheme folder
    assert not any(k[2] == "aunt@gmail.com" for k in got)                           # a person: stays in Inbox
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

    mod = types.ModuleType("tidyinbox.classify.contacts")
    mod.contacts_from_sent = lambda msgs: {"Helper@Example.org", "me@example.com"}
    monkeypatch.setitem(sys.modules, "tidyinbox.classify.contacts", mod)
    learner.learn_from_folders(cfg(), store, {"a": fb}, log=lambda m: None)
    assert store.contacts() == {"helper@example.org"}           # own address removed


def test_learning_skips_mail_tidyinbox_filed_itself(store):
    b = FakeBackend("a")
    for s in ("1", "2"):
        b.add("Receipts", make_message("orders@shop.example.com", s, message_id=f"<{s}@example.com>"))
        store.record_moves([MoveRecord("r", "a", f"<{s}@example.com>", "orders@shop.example.com", s, "INBOX",
                                       "Receipts", "receipts", "heuristic", "", utcnow())])
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    assert store.learned.count() == 0


def test_contacts_fallback_without_the_classifier(monkeypatch):
    monkeypatch.setitem(sys.modules, "tidyinbox.classify.contacts", None)
    msgs = [sent(["friend@example.org", "Me@example.com", "noreply@service.example.com"], "Hi")]
    assert learner.contacts_from_sent(msgs, {"me@example.com"}) == {"friend@example.org"}


def test_prefix_scheme_means_existing_folder_is_custom(store, fb):
    learner.learn_from_folders(cfg(folder_prefix="TidyInbox"), store, {"a": fb}, log=lambda m: None)
    assert rules(store)[("a", "address", "orders@shop.example.com")] == ("folder", "Receipts")


def folder_rules(store):
    return {r.key: (r.category, r.folder) for r in store.learned.all()}


def test_gmail_label_on_inbox_mail_is_not_learned_as_a_move(store):
    """Reviewer case (T-B1/C-B2): a Gmail filter labels the school's mail "Family" and keeps it in the
    Inbox. Over IMAP it shows up in INBOX and in Family; that must not become "move it to Family"."""
    b = FakeBackend("a", gmail=True)
    for s in ("Snow day", "Concert", "Trip", "Fees"):
        b.add_labeled(bulk("office@school.example.org", s), ["INBOX", "Family"])
    # Archived with the label only (no longer in the Inbox): that IS a filing habit.
    for s in ("Order 1", "Order 2", "Order 3"):
        b.add_labeled(bulk("orders@shop.example.com", s), ["Shopping"])
    report = learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    got = folder_rules(store)
    assert "office@school.example.org" not in got and "school.example.org" not in got
    assert got["orders@shop.example.com"] == ("folder", "Shopping")
    assert report.errors == []
    assert "[Gmail]/All Mail" not in {r.folder for r in store.learned.all()}


def test_inbox_label_without_inbox_sample_still_counts_as_inbox(store):
    """A labelled message older than the Inbox sample still carries \\Inbox: not a filing habit."""
    b = FakeBackend("a", gmail=True)
    for s in ("1", "2", "3"):
        b.add_labeled(bulk("office@school.example.org", s), ["INBOX", "Family"])
    for s in ("new 1", "new 2", "new 3"):
        b.add("INBOX", make_message(f"x{s[-1]}@example.org", s))
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None, sample=3)
    assert "office@school.example.org" not in folder_rules(store)


def test_inbox_share_counts(store):
    """The folder share is taken over Inbox + folders: half kept in the Inbox -> no rule."""
    b = FakeBackend("a")
    for s in ("1", "2"):
        b.add("Deals", bulk("deals@store.example.com", f"filed {s}"))
        b.add("INBOX", bulk("deals@store.example.com", f"kept {s}"))
    for s in ("1", "2", "3", "4"):
        b.add("Updates", bulk("news@app.example.com", f"filed {s}"))
    b.add("INBOX", bulk("news@app.example.com", "kept"))                # 4 of 5 filed = 80%: still a rule
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    got = folder_rules(store)
    assert "deals@store.example.com" not in got
    assert got["news@app.example.com"] == ("folder", "Updates")


def test_same_message_in_inbox_and_folder_is_skipped(store):
    """Non-Gmail servers: a copy of an Inbox message in a folder (same Message-ID) is not "filed"."""
    b = FakeBackend("a")
    for s in ("1", "2", "3"):
        msg = bulk("club@sports.example.org", s, message_id=f"<club{s}@example.org>")
        b.add("INBOX", msg)
        b.add("Club", msg)
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    assert store.learned.count() == 0


def test_jane_in_projects_is_not_learned(store):
    """Reviewer case (C-B2): the person filed two emails from their contractor Jane in "Projects".
    Jane's next "URGENT: can you call me today?" must stay in the Inbox."""
    from tidyinbox.classify.pipeline import Classifier

    b = FakeBackend("a")
    b.add("Projects", make_message("jane@acme-builders.com", "Site visit photos"))
    b.add("Projects", make_message("jane@acme-builders.com", "Updated quote"))
    for i in range(3):
        b.add("INBOX", make_message(f"other{i}@example.org", f"hi {i}"))
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    assert store.learned.lookup("jane@acme-builders.com", "acme-builders.com", "", "a") is None
    urgent = make_message("jane@acme-builders.com", "URGENT: can you call me today?")
    assert Classifier(cfg(), store.learned, store.contacts()).classify(urgent).category == INBOX


def test_domain_rules_need_clear_evidence(store):
    b = FakeBackend("a")
    # Two colleagues filed in Work: written by people, never a domain rule (all of acme would skip the Inbox).
    for s in ("1", "2", "3"):
        b.add("Work", make_message("ann@acme.example.com", f"ann {s}"))
        b.add("Work", make_message("bob@acme.example.com", f"bob {s}"))
    # Four machine-sent messages from two addresses: not enough for a domain rule.
    for s in ("1", "2"):
        b.add("Bank", bulk("alerts@bank.example.com", f"a {s}"))
        b.add("Bank", bulk("statements@bank.example.com", f"s {s}"))
    # Five machine-sent messages, but all from one address: not a domain rule either.
    for s in ("1", "2", "3", "4", "5"):
        b.add("Games", bulk("news@games.example.com", f"g {s}"))
    # Five from two addresses: a domain rule.
    for s in ("1", "2", "3"):
        b.add("Travel stuff", bulk("deals@air.example.com", f"d {s}"))
    for s in ("1", "2"):
        b.add("Travel stuff", bulk("trips@air.example.com", f"t {s}"))
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    domains = {r.key: r.folder for r in store.learned.all() if r.kind == "domain"}
    assert domains == {"air.example.com": "Travel stuff"}
    assert "ann@acme.example.com" not in folder_rules(store)


def test_domain_rule_blocked_by_inbox_mail_from_the_domain(store):
    b = FakeBackend("a")
    for s in ("1", "2", "3"):
        b.add("Travel stuff", bulk("deals@air.example.com", f"d {s}"))
        b.add("Travel stuff", bulk("trips@air.example.com", f"t {s}"))
    for s in ("1", "2"):
        b.add("INBOX", make_message("pilot@air.example.com", f"hello {s}"))  # a person there writes to you
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    assert not any(r.kind == "domain" for r in store.learned.all())               # 6 of 8 = 75%


def test_without_the_classifier_the_counts_alone_decide(store, monkeypatch):
    monkeypatch.setitem(sys.modules, "tidyinbox.classify.heuristics", None)
    b = FakeBackend("a")
    b.add("Projects", make_message("jane@acme-builders.com", "Site visit photos"))
    b.add("Projects", make_message("jane@acme-builders.com", "Updated quote"))
    b.add("INBOX", make_message("jane@acme-builders.com", "Hi"))
    b.add("Club", make_message("club@sports.example.org", "1"))
    b.add("Club", make_message("club@sports.example.org", "2"))
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    assert folder_rules(store) == {"club@sports.example.org": ("folder", "Club")}   # Jane: 2 of 3 < 80%


def test_unreadable_inbox_means_no_folder_rules(store):
    b = FakeBackend("a")
    b.add("Club", bulk("club@sports.example.org", "1"))
    b.add("Club", bulk("club@sports.example.org", "2"))
    b.connect()
    orig = b.search_uids

    def search(folder, **kw):
        if folder == "INBOX":
            raise BackendError("the server hung up")
        return orig(folder, **kw)

    b.search_uids = search
    report = learner.learn_account(cfg(), store, b, cfg().accounts[0], log=lambda m: None)
    assert store.learned.count() == 0
    assert report.errors and "did not learn from your folders" in report.errors[0]


def test_watched_folders_count_as_inbox(store):
    """A folder TidyInbox watches (here "archive", in other capitals) is part of the "Inbox" side: a copy
    of its mail in another folder is not a filing habit."""
    config = AppConfig(accounts=[AccountConfig(id="a", email="me@example.com", watch_folders=["INBOX", "archive"])])
    b = FakeBackend("a")
    for s in ("1", "2", "3"):
        msg = bulk("club@sports.example.org", s, message_id=f"<club{s}@example.org>")
        b.add("Archive", msg)
        b.add("Club", msg)
    learner.learn_from_folders(config, store, {"a": b}, log=lambda m: None)
    assert "club@sports.example.org" not in folder_rules(store)
    learner.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)      # not watched: a habit
    assert folder_rules(store)["club@sports.example.org"] == ("folder", "Club")


def test_namespace_prefix_matches_scheme_folders(store):
    """Servers with a personal namespace show "INBOX.Receipts": that is the scheme's Receipts (C-N5)."""
    c = cfg()
    assert learner.category_for_folder(c, "INBOX.Receipts", ".") == "receipts"
    assert learner.category_for_folder(c, "INBOX/Receipts") == "receipts"
    assert learner.category_for_folder(c, "INBOX") is None
    assert learner.category_for_folder(cfg(folder_prefix="TidyInbox"), "INBOX.TidyInbox.Receipts", ".") == "receipts"
    b = FakeBackend("a", delimiter=".")
    b.add("INBOX.Receipts", make_message("orders@shop.example.com", "Order 1"))
    b.add("INBOX.Receipts", make_message("orders@shop.example.com", "Order 2"))
    learner.learn_from_folders(c, store, {"a": b}, log=lambda m: None)
    assert rules(store) == {(ANY_ACCOUNT, "address", "orders@shop.example.com"): ("receipts", "")}


def test_namespace_correction_is_a_category_rule(store):
    b = FakeBackend("a", delimiter=".")
    b._create("INBOX.Finance")
    _moved(store, b, "bill", folder="INBOX.Receipts", sender="bills@shop.example.com")
    b.user_move("bill", "INBOX.Receipts", "INBOX.Finance")
    b.connect()
    assert learner.detect_corrections(store, b, "a", config=cfg()) == 1
    assert store.learned.lookup("bills@shop.example.com", "", "", "other").category == "finance"


def test_refresh_contacts(store):
    b = FakeBackend("a")
    b.add("Sent", sent(["new.friend@example.org", "me@example.com"], "Hi"))
    b.connect()
    account = cfg().accounts[0]
    t0 = utcnow()
    assert learner.refresh_contacts(store, b, account, now=t0) == 1
    assert store.contacts() == {"new.friend@example.org"}
    b.add("Sent", sent(["later@example.org"], "Hello"))
    # Asked again within a day: nothing is read.
    calls = []
    orig = b.search_uids
    b.search_uids = lambda f, **kw: calls.append(f) or orig(f, **kw)
    assert learner.refresh_contacts(store, b, account, max_age=timedelta(days=1),
                                    now=t0 + timedelta(hours=5)) == 0
    assert calls == []
    assert learner.refresh_contacts(store, b, account, max_age=timedelta(days=1),
                                    now=t0 + timedelta(days=1, minutes=1)) == 1
    assert calls == ["Sent"]
    assert "later@example.org" in store.contacts()


def test_refresh_contacts_reads_only_the_newest_sent_mail(store):
    b = FakeBackend("a", gmail=True)
    for i in range(5):
        b.add(b.sent_folder, sent([f"p{i}@example.org"], f"m{i}"))
    b.connect()
    assert learner.refresh_contacts(store, b, "a", limit=2) == 2
    assert store.contacts() == {"p3@example.org", "p4@example.org"}
    # No Sent folder at all: nothing to learn, no error.
    b.list_folders = lambda: [FolderInfo("INBOX")]
    assert learner.refresh_contacts(store, b, "a") == 0


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


def _sort_like_the_engine(store, b, subject, sender="orders@shop.example.com"):
    """Put a message in the Inbox and move it to Receipts the way a run does, logging the move."""
    mid = f"<{subject}@example.com>"
    uid = b.add("INBOX", make_message(sender, subject, message_id=mid))
    b.ensure_folder("Receipts")
    b.move("INBOX", [uid], "Receipts")
    store.record_moves([MoveRecord("r", "a", mid, sender, subject, "INBOX", "Receipts", "receipts", "heuristic",
                                   "", utcnow())])
    return mid


class OldServer(FakeBackend):
    """A server whose Message-ID search still returns messages flagged \\Deleted."""

    def find_message_id(self, folder, message_id):
        return sorted(u for u, m in self._need(folder).items() if m.message_id == message_id)


@pytest.mark.parametrize("backend_class", [FakeBackend, OldServer])
def test_deleted_original_without_uidplus_is_not_a_put_back(store, backend_class):
    """C-S5: without UIDPLUS the original stays in the Inbox flagged \\Deleted. When the person then
    deletes the sorted copy, that leftover must not count as "you moved it back to the Inbox"."""
    b = backend_class("a", uidplus=False)
    b.connect()
    mid = _sort_like_the_engine(store, b, "Order 1")
    assert any("\\Deleted" in m.flags for m in b.messages("INBOX", include_deleted=True))
    b.user_move("Order 1", "Receipts", "Trash")
    assert learner.detect_corrections(store, b, "a", config=cfg()) == 0
    assert store.learned.lookup("orders@shop.example.com", "", "", "a") is None
    assert {m.message_id: m.check_state for m in store.recent_moves()} == {mid: "gone"}
    assert store.seen("a", mid) is None or not store.seen("a", mid).override


def test_real_put_back_without_uidplus_is_still_found(store):
    b = OldServer("a", uidplus=False)
    b.connect()
    mid = _sort_like_the_engine(store, b, "Order 2")
    b.user_move("Order 2", "Receipts", "INBOX")          # a new, undeleted copy next to the leftover
    assert learner.detect_corrections(store, b, "a", config=cfg()) == 1
    assert store.learned.lookup("orders@shop.example.com", "", "", "a").category == INBOX
    assert store.seen("a", mid).override
