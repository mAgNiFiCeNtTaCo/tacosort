"""Engine tests: FakeBackend mailboxes + a tiny stub classifier (the real one is tested elsewhere).

The core promises (first sort, second run changes nothing, put back, undo, start from now) run against
three kinds of fake server: a well-behaved one, a Gmail-like one (labels, All Mail) and one without
UIDPLUS (originals stay behind flagged \\Deleted, no new UIDs reported).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from tacosort import engine, learner
from tacosort.models import INBOX, AccountConfig, AppConfig, Decision, MoveRecord
from tacosort.store import Store, utcnow
from tacosort.testing import FakeBackend, make_message

DOMAINS = {
    "shop.example.com": "receipts",
    "news.example.com": "newsletters",
    "bank.example.com": "finance",
    "deals.example.com": "promotions",
}
MODES = {
    "plain": {},
    "gmail": {"gmail": True},
    "no-uidplus": {"uidplus": False},
}


class StubClassifier:
    """Guards (flagged, contacts) -> learned rules -> a domain table -> inbox. Same order as the spec."""

    def __init__(self, config, learned, contacts):
        self.config, self.learned, self.contacts = config, learned, contacts

    def classify(self, msg):
        if msg.is_flagged or msg.from_.email in self.contacts:
            return Decision(INBOX, 1.0, "guard", "contact or flagged")
        d = self.learned.lookup(msg.from_.email, msg.from_.domain, msg.headers.get("list-id", ""), msg.account)
        if d is not None:
            return d
        cat = DOMAINS.get(msg.from_.domain)
        if cat:
            return Decision(cat, 0.9, "heuristic", f"known sender {msg.from_.domain}")
        return Decision(INBOX, 0.5, "default", "not sure")


class Always:
    """A classifier that sends everything to one category with full confidence."""

    def __init__(self, category="receipts", folder=None):
        self.decision = Decision(category, 1.0, "heuristic", "always", folder=folder)

    def classify(self, msg):
        return self.decision


def cfg(*ids, **kw) -> AppConfig:
    kw.setdefault("company_folders", False)     # covered by test_companies.py
    return AppConfig(accounts=[AccountConfig(id=i, email=f"me@{i}.example.com") for i in (ids or ("a",))], **kw)


def fill(b: FakeBackend) -> FakeBackend:
    b.add("INBOX", make_message("order@shop.example.com", "Your order #1"))
    b.add("INBOX", make_message("order@shop.example.com", "Your order #2"))
    b.add("INBOX", make_message("weekly@news.example.com", "This week"))
    b.add("INBOX", make_message("friend@example.org", "Lunch?"))
    b.add("INBOX", make_message("order@shop.example.com", "Starred order", flags=frozenset({"\\Flagged"})))
    return b


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


@pytest.fixture
def fb():
    return fill(FakeBackend("a"))


@pytest.fixture(params=list(MODES), ids=list(MODES))
def mfb(request):
    """The same mailbox on each kind of fake server."""
    return fill(FakeBackend("a", **MODES[request.param]))


def quiet(_msg):
    pass


def run(config, store, backends, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("classifier", StubClassifier)
    return engine.run_once(config, store, backends, log=quiet, **kw)


def sort_all(config, store, backends, **kw):
    """`tacosort run --backlog`: sort the mail that is already there too."""
    return run(config, store, backends, backlog=True, **kw)


def start(config, store, backends):
    return engine.start_from_now(config, store, backends, log=quiet)


def tables(store) -> dict[str, list[tuple]]:
    """Everything the store remembers except the run log itself."""
    out = {}
    for name in ("moves", "watermarks", "learned", "contacts", "seen", "meta"):
        out[name] = sorted(tuple(r) for r in store.conn.execute(f"SELECT * FROM {name}").fetchall())
    return out


# ---- the core promises, on every kind of server ------------------------------------------------------
def test_first_sort_moves_the_right_mail(store, mfb):
    report = sort_all(cfg(), store, {"a": mfb})
    assert report.errors == []
    assert sorted(mfb.subjects("Receipts")) == ["Your order #1", "Your order #2"]
    assert mfb.subjects("Newsletters") == ["This week"]
    assert sorted(mfb.subjects("INBOX")) == ["Lunch?", "Starred order"]
    assert (report.scanned, report.moved, report.kept) == (5, 3, 2)
    stored = store.recent_moves()
    assert len(stored) == 3 and {m.to_folder for m in stored} == {"Receipts", "Newsletters"}
    assert all(bool(m.new_uid) == mfb.uidplus for m in stored)
    assert store.get_watermark("a", "INBOX")[1] == 5
    assert store.last_run()["moved"] == 3


def test_second_run_does_nothing_new(store, mfb):
    sort_all(cfg(), store, {"a": mfb})
    mfb.calls.clear()
    for kw in ({}, {"backlog": True}):
        report = run(cfg(), store, {"a": mfb}, **kw)
        assert report.moved == 0, kw
    assert not [c for c in mfb.calls if c[0] == "move"]


def test_message_put_back_in_inbox_is_never_moved_again_and_sender_is_learned(store, mfb):
    sort_all(cfg(), store, {"a": mfb})
    mfb.user_move("Your order #1", "Receipts", "INBOX")
    report = run(cfg(), store, {"a": mfb})
    assert report.learned == 1
    assert "Your order #1" in mfb.subjects("INBOX")
    d = store.learned.lookup("order@shop.example.com", "shop.example.com", "", "a")
    assert d.category == INBOX and d.source == "learned" and d.explicit
    # New mail from that sender now stays in the Inbox, and later runs never touch the message.
    mfb.add("INBOX", make_message("order@shop.example.com", "Your order #3"))
    run(cfg(), store, {"a": mfb})
    sort_all(cfg(), store, {"a": mfb})
    assert {"Your order #1", "Your order #3"} <= set(mfb.subjects("INBOX"))
    assert store.corrections()[0]["to_folder"] == "INBOX"


def test_undo_last_run_restores_and_is_not_resorted(store, mfb):
    report = sort_all(cfg(), store, {"a": mfb})
    n = engine.undo(cfg(), store, {"a": mfb}, log=quiet)
    assert n == 3
    assert len(mfb.messages("INBOX")) == 5 and mfb.messages("Receipts") == []
    assert all(m.undone for m in store.moves_for_run(report.run_id, include_undone=True))
    assert sort_all(cfg(), store, {"a": mfb}).moved == 0
    assert engine.undo(cfg(), store, {"a": mfb}, log=quiet) == 0


def test_start_from_now_sorts_only_mail_that_arrives_later(store, mfb):
    lines = []
    assert engine.start_from_now(cfg(), store, {"a": mfb}, log=lines.append) == ["a"]
    assert any("--backlog" in line for line in lines)
    assert run(cfg(), store, {"a": mfb}).moved == 0                 # the old mail stays put
    mfb.add("INBOX", make_message("order@shop.example.com", "Your order #3"))
    assert run(cfg(), store, {"a": mfb}).moved == 1
    assert "Your order #3" in mfb.subjects("Receipts") and "Your order #1" in mfb.subjects("INBOX")
    # Already started: a second call changes nothing, and --backlog still sorts the rest.
    assert engine.start_from_now(cfg(), store, {"a": mfb}, log=lines.append) == []
    assert sort_all(cfg(), store, {"a": mfb}).moved == 3


def test_start_from_now_then_server_renumbers_inbox_moves_nothing(store, mfb):
    start(cfg(), store, {"a": mfb})
    mfb.reset_uidvalidity("INBOX")
    lines = []
    report = engine.run_once(cfg(), store, {"a": mfb}, dry_run=False, log=lines.append, classifier=StubClassifier)
    assert report.moved == 0 and report.scanned == 0
    assert len(mfb.messages("INBOX")) == 5
    assert any("renumbered" in line for line in lines)
    assert store.get_watermark("a", "INBOX")[0] == mfb._uidvalidity["INBOX"]
    # Mail that arrives after that is sorted as usual.
    mfb.add("INBOX", make_message("order@shop.example.com", "Your order #3"))
    assert run(cfg(), store, {"a": mfb}).moved == 1
    assert mfb.subjects("Receipts") == ["Your order #3"]


# ---- C-B1: no start point is never "sort everything" -------------------------------------------------
def test_plain_run_on_a_never_sorted_folder_sorts_nothing_and_starts_from_now(store, fb):
    lines = []
    report = engine.run_once(cfg(), store, {"a": fb}, dry_run=False, log=lines.append, classifier=StubClassifier)
    assert report.moved == 0 and len(fb.messages("INBOX")) == 5
    assert store.get_watermark("a", "INBOX") == (fb._uidvalidity["INBOX"], 5)
    assert any("--backlog" in line for line in lines)
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    assert run(cfg(), store, {"a": fb}).moved == 1
    assert fb.subjects("Finance") == ["Your statement"]


def test_start_from_now_that_could_not_connect_still_sorts_no_old_mail(store, fb):
    class FailsOnce(FakeBackend):
        tries = 0

        def connect(self):
            self.tries += 1
            if self.tries == 1:
                raise engine.BackendError("The server can't be reached.")
            super().connect()

    b = fill(FailsOnce("a"))
    assert start(cfg(), store, {"a": b}) == []
    assert store.get_watermark("a", "INBOX") is None
    assert run(cfg(), store, {"a": b}).moved == 0
    assert len(b.messages("INBOX")) == 5


def test_uidvalidity_reset_after_a_full_sort(store, fb):
    sort_all(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("friend@example.org", "Another lunch"))
    fb.reset_uidvalidity("INBOX")
    fb.calls.clear()
    report = run(cfg(), store, {"a": fb})                         # plain: nothing is re-sorted
    assert report.moved == 0 and not [c for c in fb.calls if c[0] == "move"]
    assert store.get_watermark("a", "INBOX") == (fb._uidvalidity["INBOX"], 3)
    assert run(cfg(), store, {"a": fb}).scanned == 0
    # --backlog after a renumbering does not move anything twice either.
    fb.add("INBOX", make_message("order@shop.example.com", "Your order #4"))
    fb.reset_uidvalidity("INBOX")
    assert sort_all(cfg(), store, {"a": fb}).moved == 1
    assert sorted(fb.subjects("Receipts")) == ["Your order #1", "Your order #2", "Your order #4"]


def test_start_from_now_takes_the_run_lock(store, fb, tmp_path):
    lock = engine.RunLock(tmp_path)
    assert lock.acquire()
    try:
        assert start(cfg(), store, {"a": fb}) == []
        assert fb.calls == []
    finally:
        lock.release()
    assert start(cfg(), store, {"a": fb}) == ["a"]
    assert engine.is_running(cfg(), store) is False


# ---- C-S1: watermarks under the server's folder name -------------------------------------------------
def test_watch_folder_spelled_inbox_uses_the_server_name(store, fb):
    config = cfg()
    config.accounts[0].watch_folders = ["Inbox"]
    start(config, store, {"a": fb})
    assert store.get_watermark("a", "INBOX") is not None and store.get_watermark("a", "Inbox") is None
    assert run(config, store, {"a": fb}).moved == 0
    fb.add("INBOX", make_message("order@shop.example.com", "Your order #3"))
    assert run(config, store, {"a": fb}).moved == 1
    assert fb.subjects("Receipts") == ["Your order #3"]


def test_old_watermark_saved_under_the_typed_name_is_adopted(store, fb):
    config = cfg()
    config.accounts[0].watch_folders = ["Inbox"]
    store.set_watermark("a", "Inbox", fb._uidvalidity["INBOX"], 5)      # what an older TacoSort saved
    assert start(config, store, {"a": fb}) == []
    assert run(config, store, {"a": fb}).moved == 0
    assert store.get_watermark("a", "INBOX") == (fb._uidvalidity["INBOX"], 5)


# ---- C-S2: no Message-ID, never moved ------------------------------------------------------------------
def test_message_without_message_id_is_never_moved(store):
    b = FakeBackend("a")
    b.add("INBOX", make_message("order@shop.example.com", "No id", message_id=""))
    b.add("INBOX", make_message("order@shop.example.com", "Has id"))
    report = sort_all(cfg(), store, {"a": b}, classifier=Always())
    assert b.subjects("INBOX") == ["No id"] and b.subjects("Receipts") == ["Has id"]
    assert (report.scanned, report.kept, report.moved) == (2, 1, 1)
    assert sort_all(cfg(), store, {"a": b}, classifier=Always()).moved == 0
    assert engine.UNTRACKABLE.keeps and "can't track" in engine.UNTRACKABLE.reason


# ---- T-S4: an existing folder with other capitals is used as it is --------------------------------------
def test_existing_folder_with_other_capitals_is_reused(store, fb):
    fb.add("receipts", make_message("old@shop.example.com", "Old receipt"))
    sort_all(cfg(), store, {"a": fb})
    assert "Receipts" not in fb._folders
    assert sorted(fb.subjects("receipts")) == ["Old receipt", "Your order #1", "Your order #2"]
    assert ("ensure_folder", "receipts") not in fb.calls and ("ensure_folder", "Receipts") not in fb.calls
    assert {m.to_folder for m in store.recent_moves() if m.category == "receipts"} == {"receipts"}


# ---- C-S3: only what the server confirms is recorded ---------------------------------------------------
def test_partial_move_records_only_moved_mail_and_retries_the_rest(store):
    b = FakeBackend("a")
    for i in (1, 2, 3):
        b.add("INBOX", make_message("old@example.org", f"Old {i}"))
    start(cfg(), store, {"a": b})
    b.add("INBOX", make_message("order@shop.example.com", "New 4"))      # uid 4
    b.add("INBOX", make_message("order@shop.example.com", "New 5"))      # uid 5
    b.move_filter = lambda uid: uid != 4
    lines = []
    report = engine.run_once(cfg(), store, {"a": b}, dry_run=False, log=lines.append, classifier=StubClassifier)
    assert report.moved == 1 and [m.subject for m in store.recent_moves()] == ["New 5"]
    assert any("did not confirm 1 move" in line for line in lines)
    assert store.get_watermark("a", "INBOX")[1] == 3                     # must not pass uid 4
    b.move_filter = None
    assert run(cfg(), store, {"a": b}).moved == 1
    assert sorted(b.subjects("Receipts")) == ["New 4", "New 5"]
    assert store.get_watermark("a", "INBOX")[1] == 4
    assert len(store.recent_moves()) == 2


# ---- T-S8: the engine's own "never moves flagged mail" guard -------------------------------------------
def test_flagged_mail_stays_even_when_the_classifier_says_move(store):
    b = FakeBackend("a")
    b.add("INBOX", make_message("order@shop.example.com", "Starred", flags=frozenset({"\\Flagged"})))
    assert sort_all(cfg(), store, {"a": b}, classifier=Always()).moved == 0
    assert b.subjects("INBOX") == ["Starred"]
    b2 = FakeBackend("a")
    b2.add("INBOX", make_message("order@shop.example.com", "Starred", flags=frozenset({"\\Flagged"})))
    with Store(":memory:") as other:
        assert sort_all(cfg(keep_flagged=False), other, {"a": b2}, classifier=Always()).moved == 1
    assert b2.subjects("Receipts") == ["Starred"]


# ---- T-N4: already where it belongs ----------------------------------------------------------------------
def test_message_already_in_its_destination_is_not_moved(store, fb):
    report = sort_all(cfg(), store, {"a": fb}, classifier=Always(category="folder", folder="inbox"))
    assert report.moved == 0 and report.kept == 5
    assert not [c for c in fb.calls if c[0] in ("move", "ensure_folder")]


# ---- T-N2: two different messages, one Message-ID ----------------------------------------------------------
def test_two_different_messages_with_same_message_id(store):
    b = FakeBackend("a")
    b.add("INBOX", make_message("order@shop.example.com", "Order A", message_id="<same@example.com>"))
    sort_all(cfg(), store, {"a": b})
    b.add("INBOX", make_message("statements@bank.example.com", "Statement", message_id="<same@example.com>"))
    run(cfg(), store, {"a": b})
    assert b.subjects("Finance") == ["Statement"]
    # The real put-back (same sender and subject) is still respected.
    b.user_move("Order A", "Receipts", "INBOX")
    sort_all(cfg(), store, {"a": b}, classifier=Always())
    assert b.subjects("INBOX") == ["Order A"]


# ---- T-N1: a preview changes nothing at all ------------------------------------------------------------------
def test_dry_run_changes_nothing(store, fb):
    sort_all(cfg(), store, {"a": fb})
    fb.user_move("Your order #1", "Receipts", "INBOX")                  # a put-back the preview will notice
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    fb.add("Work", make_message("boss@example.org", "Hi"))
    config = cfg()
    config.accounts[0].watch_folders = ["INBOX", "Work"]                 # Work has no start point yet
    before = tables(store)
    folders_before = {f: sorted(fb.subjects(f)) for f in fb._folders}
    fb.calls.clear()
    for kw in ({}, {"backlog": True}):
        report = run(config, store, {"a": fb}, dry_run=True, **kw)
        assert tables(store) == before, kw
    assert report.moved == 1 and [m.to_folder for m in report.moves] == ["Finance"]
    assert {f: sorted(fb.subjects(f)) for f in fb._folders} == folders_before
    assert not [c for c in fb.calls if c[0] in ("move", "ensure_folder")]
    assert store.has_previewed()


def test_preview_of_a_new_mailbox_creates_nothing(store, fb):
    report = sort_all(cfg(), store, {"a": fb}, dry_run=True)
    assert report.moved == 3 and {m.to_folder for m in report.moves} == {"Receipts", "Newsletters"}
    assert "Receipts" not in fb._folders and len(fb.messages("INBOX")) == 5
    assert store.get_watermark("a", "INBOX") is None and store.recent_moves() == []
    assert sort_all(cfg(), store, {"a": fb}).moved == 3


# ---- undo ------------------------------------------------------------------------------------------------------
def test_undo_last_n_and_by_run_id(store, fb):
    first = sort_all(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    run(cfg(), store, {"a": fb})
    assert engine.undo(cfg(), store, {"a": fb}, last=1, log=quiet) == 1
    assert "Your statement" in fb.subjects("INBOX")
    assert engine.undo(cfg(), store, {"a": fb}, run_id=first.run_id, log=quiet) == 3
    assert len(fb.messages("INBOX")) == 6


def test_undo_skips_message_the_user_already_moved(store, fb):
    sort_all(cfg(), store, {"a": fb})
    fb.user_move("This week", "Newsletters", "Reading")
    assert engine.undo(cfg(), store, {"a": fb}, log=quiet) == 2
    assert fb.subjects("Reading") == ["This week"]


def test_undo_two_records_with_one_message_id(store):
    b = FakeBackend("a")
    for _ in range(2):                                                    # the same message twice (a duplicate)
        b.add("INBOX", make_message("order@shop.example.com", "Twice", message_id="<twice@example.com>"))
    sort_all(cfg(), store, {"a": b})
    assert len(b.messages("Receipts")) == 2 and len(store.recent_moves()) == 2
    assert engine.undo(cfg(), store, {"a": b}, log=quiet) == 2
    assert len(b.messages("INBOX")) == 2 and b.messages("Receipts") == []


def test_undo_with_one_copy_left_restores_one(store):
    b = FakeBackend("a")
    for _ in range(2):
        b.add("INBOX", make_message("order@shop.example.com", "Twice", message_id="<twice@example.com>"))
    report = sort_all(cfg(), store, {"a": b})
    b.user_move("Twice", "Receipts", "Trash")                            # the user deleted one copy
    lines = []
    assert engine.undo(cfg(), store, {"a": b}, log=lines.append) == 1
    assert len(b.messages("INBOX")) == 1 and b.messages("Receipts") == []
    assert any("no longer in Receipts" in line for line in lines)
    assert all(m.undone for m in store.moves_for_run(report.run_id, include_undone=True))


def _record(store, mid, new_uid, sender="order@shop.example.com", subject="Receipt"):
    store.record_moves([MoveRecord("old-run", "a", mid, sender, subject, "INBOX", "Receipts", "receipts", "heuristic",
                                   "", utcnow(), new_uid=new_uid)])


def test_undo_without_message_id_checks_sender_and_subject(store):
    b = FakeBackend("a")
    b.connect()
    b.ensure_folder("Receipts")
    uid = b.add("Receipts", make_message("order@shop.example.com", "Receipt", message_id=""))
    other = b.add("Receipts", make_message("someone@example.org", "Not it", message_id=""))
    _record(store, "", uid)
    _record(store, "", other)                                           # same number, different message now
    assert engine.undo(cfg(), store, {"a": b}, run_id="old-run", log=quiet) == 1
    assert b.subjects("INBOX") == ["Receipt"] and b.subjects("Receipts") == ["Not it"]


def test_undo_without_message_id_or_number_says_it_cannot_track_it(store):
    b = FakeBackend("a")
    b.connect()
    b.ensure_folder("Receipts")
    b.add("Receipts", make_message("order@shop.example.com", "Receipt", message_id=""))
    _record(store, "", None)
    lines = []
    assert engine.undo(cfg(), store, {"a": b}, run_id="old-run", log=lines.append) == 0
    assert any("can't track" in line for line in lines)
    assert not any("you moved or deleted it" in line for line in lines)
    assert store.moves_for_run("old-run") == []                          # not offered again


# ---- learning and corrections --------------------------------------------------------------------------
def test_put_back_is_respected_even_without_correction_learning(store, fb):
    config = cfg(learn_from_corrections=False)
    sort_all(config, store, {"a": fb})
    fb.user_move("Your order #1", "Receipts", "INBOX")
    report = run(config, store, {"a": fb})
    assert report.learned == 0 and report.moved == 0
    sort_all(config, store, {"a": fb})
    assert "Your order #1" in fb.subjects("INBOX")
    assert store.seen("a", next(m for m in fb.messages("INBOX") if m.subject == "Your order #1").message_id).override


def test_user_moves_sorted_mail_to_another_folder(store, fb):
    sort_all(cfg(), store, {"a": fb})
    fb.user_move("Your order #2", "Receipts", "Shopping")
    assert run(cfg(), store, {"a": fb}).learned == 1
    fb.add("INBOX", make_message("order@shop.example.com", "Your order #9"))
    run(cfg(), store, {"a": fb})
    assert "Your order #9" in fb.subjects("Shopping")
    assert "Your order #2" in fb.subjects("Shopping")      # the user's choice is left alone


def test_user_moves_to_another_category_folder_applies_to_all_accounts(store, fb):
    other = FakeBackend("b")
    sort_all(cfg("a", "b"), store, {"a": fb, "b": other})
    fb.user_move("This week", "Newsletters", "Promotions")
    run(cfg("a", "b"), store, {"a": fb, "b": other})
    other.add("INBOX", make_message("weekly@news.example.com", "Next week"))
    run(cfg("a", "b"), store, {"a": fb, "b": other})
    assert other.subjects("Promotions") == ["Next week"]


def test_deleted_message_teaches_nothing(store, fb):
    sort_all(cfg(), store, {"a": fb})
    fb.user_move("This week", "Newsletters", "Trash")
    assert run(cfg(), store, {"a": fb}).learned == 0
    assert store.learned.lookup("weekly@news.example.com", "news.example.com", "", "a") is None


def test_correction_checks_get_rarer_over_time(store, fb):
    sort_all(cfg(), store, {"a": fb})
    lookups = []
    orig = fb.find_message_id
    fb.find_message_id = lambda f, mid: lookups.append(f) or orig(f, mid)
    run(cfg(), store, {"a": fb})
    assert len(lookups) == 3                                             # first check of each move
    lookups.clear()
    run(cfg(), store, {"a": fb})
    assert lookups == []                                                 # not due again for an hour


def test_contacts_are_refreshed_once_a_day(store, monkeypatch):
    calls = []

    def refresh(st, backend, account):
        calls.append(account.id)
        return st.add_contacts(["order@shop.example.com"], account.id)

    monkeypatch.setattr(learner, "refresh_contacts", refresh, raising=False)
    b = FakeBackend("a")
    start(cfg(), store, {"a": b})
    b.add("INBOX", make_message("order@shop.example.com", "From someone I now write to"))
    assert run(cfg(), store, {"a": b}).moved == 0                       # protected in the same run
    assert calls == ["a"]
    run(cfg(), store, {"a": b})
    assert calls == ["a"]                                                # not again the same day
    store.set_meta("contacts_refreshed:a", (utcnow() - timedelta(days=2)).isoformat())
    run(cfg(), store, {"a": b})
    assert calls == ["a", "a"]
    run(cfg(), store, {"a": b}, dry_run=True)
    assert calls == ["a", "a"]                                           # previews read nothing extra


def test_contacts_refresh_missing_or_failing_does_not_stop_sorting(store, fb, monkeypatch):
    monkeypatch.delattr(learner, "refresh_contacts", raising=False)
    assert sort_all(cfg(), store, {"a": fb}).moved == 3

    def broken(*_):
        raise engine.BackendError("Sent folder unreadable")

    monkeypatch.setattr(learner, "refresh_contacts", broken, raising=False)
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    report = run(cfg(), store, {"a": fb})
    assert report.moved == 1 and report.errors == []
    assert store.get_meta("contacts_refreshed:a") is None


# ---- accounts, errors, limits ---------------------------------------------------------------------------
def test_only_new_mail_is_processed(store, fb):
    sort_all(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    report = run(cfg(), store, {"a": fb})
    assert (report.scanned, report.moved) == (1, 1)
    assert fb.subjects("Finance") == ["Your statement"]


def test_one_broken_account_does_not_stop_the_others(store, fb):
    broken = FakeBackend("b", fail_connect="The password for b was not accepted.")
    report = sort_all(cfg("b", "a"), store, {"a": fb, "b": broken})
    assert report.moved == 3
    assert report.errors == ["b: The password for b was not accepted."]


def test_unexpected_crash_in_one_account_is_reported_in_plain_english(store, fb):
    class Exploding(FakeBackend):
        def folder_state(self, folder):
            raise ValueError("boom")

    report = sort_all(cfg("b", "a"), store, {"a": fb, "b": Exploding("b")})
    assert report.moved == 3
    assert report.errors[0].startswith("b: something unexpected went wrong")


def test_missing_backend_and_disabled_account(store, fb):
    config = cfg("a", "c", "d")
    config.accounts[2].enabled = False
    report = run(config, store, {"a": fb})
    assert report.errors == ["c: no connection was set up for this account."]


def test_max_moves_per_run(store):
    b = FakeBackend("a")
    for i in range(5):
        b.add("INBOX", make_message("order@shop.example.com", f"Order {i}"))
    b.add("INBOX", make_message("friend@example.org", "Hi"))
    config = cfg(max_moves_per_run=2)
    assert sort_all(config, store, {"a": b}).moved == 2
    assert sorted(b.subjects("Receipts")) == ["Order 3", "Order 4"]           # newest first
    assert run(config, store, {"a": b}).moved == 2
    assert run(config, store, {"a": b}).moved == 1
    assert run(config, store, {"a": b}).scanned == 0
    assert b.subjects("INBOX") == ["Hi"]
    assert len(store.recent_moves()) == 5


def test_lock_prevents_concurrent_runs(store, fb, tmp_path):
    lock = engine.RunLock(tmp_path)
    assert lock.acquire()
    try:
        assert engine.is_running(cfg(), store)
        report = sort_all(cfg(), store, {"a": fb})
        assert report.run_id == "" and report.moved == 0
        assert fb.calls == []
        with pytest.raises(engine.EngineBusy), engine.RunLock(tmp_path):
            pass
    finally:
        lock.release()
    assert not engine.is_running(cfg(), store)
    assert sort_all(cfg(), store, {"a": fb}).moved == 3


def test_busy_run_is_marked_skipped(store, fb, tmp_path):
    lock = engine.RunLock(tmp_path)
    assert lock.acquire()
    try:
        report = run(cfg(), store, {"a": fb})
    finally:
        lock.release()
    assert report.skipped and report.run_id == "" and fb.subjects("Receipts") == []


def test_backlog_resorts_kept_mail_after_learning(store, fb):
    sort_all(cfg(), store, {"a": fb})
    store.learned.learn_correction("a", "address", "friend@example.org", category="social")
    assert run(cfg(), store, {"a": fb}).moved == 0                  # normal run: old mail is done
    assert sort_all(cfg(), store, {"a": fb}).moved == 1             # backlog: sort everything again
    assert fb.subjects("Social") == ["Lunch?"]


def test_special_and_missing_watch_folders_are_skipped(store, fb):
    config = cfg()
    config.accounts[0].watch_folders = ["Sent", "Nope", "INBOX"]
    report = sort_all(config, store, {"a": fb})
    assert report.moved == 3
    assert len(report.errors) == 2 and "special folder" in report.errors[0] and "does not exist" in report.errors[1]


def test_never_moves_into_trash_or_low_confidence(store):
    b = FakeBackend("a")
    b.add("INBOX", make_message("x@spam.example.com", "Bad"))
    b.add("INBOX", make_message("y@unsure.example.com", "Meh"))

    class Weird:
        def classify(self, msg):
            if msg.subject == "Bad":
                return Decision("folder", 1.0, "user-rule", "rule", folder="trash")
            return Decision("promotions", 0.3, "heuristic", "weak")

    report = sort_all(cfg(), store, {"a": b}, classifier=Weird())
    assert report.moved == 0 and report.kept == 2
    assert sorted(b.subjects("INBOX")) == ["Bad", "Meh"]


def test_classify_many_is_used_when_available(store, fb):
    calls = []

    class Batch(StubClassifier):
        def classify_many(self, msgs):
            calls.append(len(msgs))
            return [self.classify(m) for m in msgs]

    assert sort_all(cfg(), store, {"a": fb}, classifier=Batch).moved == 3
    assert calls == [5]


def test_log_file_is_written_without_subjects(store, fb, tmp_path):
    sort_all(cfg(), store, {"a": fb})
    for h in engine.logger.handlers:
        h.flush()
    text = (tmp_path / "tacosort.log").read_text(encoding="utf-8")
    assert "moved 2 message(s) from INBOX to Receipts" in text
    assert "Your order" not in text


# ---- watch -------------------------------------------------------------------------------------------------
def test_watch_runs_and_stops(store, fb):
    sleeps = []
    lines = []
    start(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    engine.watch(cfg(), store, lambda: {"a": fb}, log=lines.append, classifier=StubClassifier, max_cycles=2,
                 sleep=sleeps.append, idle_seconds=1)
    assert fb.subjects("Finance") == ["Your statement"]
    assert sleeps == [engine.MIN_CYCLE_SECONDS, engine.MIN_CYCLE_SECONDS]   # FakeBackend.idle returns at once
    assert lines[-1] == "Stopped watching."


def test_watch_backs_off_when_offline_and_recovers(store, fb):
    sleeps = []
    attempts = []
    start(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))

    def factory():
        attempts.append(1)
        if len(attempts) <= 4:
            return {"a": FakeBackend("a", fail_connect="The server can't be reached.")}
        return {"a": fb}

    engine.watch(cfg(), store, factory, log=quiet, classifier=StubClassifier, max_cycles=3,
                 sleep=sleeps.append, idle_seconds=1)
    assert sleeps[:2] == [30, 60]
    assert fb.subjects("Finance") == ["Your statement"]


def test_watch_stops_on_ctrl_c(store, fb):
    def factory():
        raise KeyboardInterrupt

    lines = []
    engine.watch(cfg(), store, factory, log=lines.append, classifier=StubClassifier)
    assert lines[-1] == "Stopped watching."


def test_learn_from_folders_wrapper_uses_lock(store, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(learner, "learn_from_folders", lambda *a, **kw: seen.append(1) or learner.LearnReport())
    b = FakeBackend("a")
    engine.learn_from_folders(cfg(), store, {"a": b}, log=quiet)
    assert seen == [1]
    lock = engine.RunLock(tmp_path)
    assert lock.acquire()
    orig = engine.RunLock.acquire
    monkeypatch.setattr(engine.RunLock, "acquire", lambda self, wait=0: orig(self, 0))   # don't wait 2 minutes
    try:
        with pytest.raises(engine.EngineBusy):
            engine.learn_from_folders(cfg(), store, {"a": b}, log=quiet)
    finally:
        lock.release()
    assert seen == [1]


# ---- AI ------------------------------------------------------------------------------------------------------
def _fake_pipeline(monkeypatch):
    """Stand-ins for the real Classifier and AIClassifier: record what the engine hands them."""
    import sys
    import types

    made = {}

    class Recorder(StubClassifier):
        def __init__(self, config, learned, contacts, ai=None):
            super().__init__(config, learned, contacts)
            made["ai"] = ai

    class FakeAI:
        def __init__(self, ai_config, key):
            made["key"] = key

    monkeypatch.setitem(sys.modules, "tacosort.classify.pipeline", types.SimpleNamespace(Classifier=Recorder))
    monkeypatch.setitem(sys.modules, "tacosort.classify.ai", types.SimpleNamespace(AIClassifier=FakeAI))
    return made, FakeAI


@pytest.fixture
def keychain(monkeypatch):
    """A fake keychain: the AI key under its own name, and a mail password under the bare 'anthropic'."""
    from tacosort.imap import auth

    saved = {"ai": None}
    monkeypatch.setattr(auth, "get_ai_key", lambda: saved["ai"], raising=False)
    monkeypatch.setattr(auth, "get_secret", lambda account_id: "MAIL-APP-PASSWORD-not-real")
    return saved


def test_ai_is_wired_in_when_enabled_and_a_key_exists(store, fb, monkeypatch, keychain):
    made, fake_ai = _fake_pipeline(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    config = cfg()
    config.ai.enabled = True
    report = engine.run_once(config, store, {"a": fb}, dry_run=True, backlog=True, log=quiet)
    assert report.moved == 3
    assert isinstance(made["ai"], fake_ai) and made["key"] == "test-key-not-real"


def test_ai_key_comes_from_the_named_variable_then_the_ai_keychain_entry(monkeypatch, keychain):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = cfg()
    config.ai.api_key_env = "MY_CLAUDE_KEY"
    monkeypatch.setenv("MY_CLAUDE_KEY", "env-key-not-real")
    assert engine._anthropic_key(config) == "env-key-not-real"
    monkeypatch.delenv("MY_CLAUDE_KEY")
    assert engine._anthropic_key(config) is None                       # never the mail password
    keychain["ai"] = "keychain-key-not-real"
    assert engine._anthropic_key(config) == "keychain-key-not-real"


def test_ai_without_key_is_skipped_with_a_plain_message(store, fb, monkeypatch, keychain):
    made, _ = _fake_pipeline(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = cfg()
    config.ai.enabled = True
    lines = []
    engine.run_once(config, store, {"a": fb}, dry_run=True, log=lines.append)
    assert made["ai"] is None
    note = [line for line in lines if "no API key was found" in line]
    assert len(note) == 1 and "ANTHROPIC_API_KEY" in note[0] and "tacosort config ai-key" in note[0]
    config.ai.enabled = False
    lines.clear()
    engine.run_once(config, store, {"a": fb}, dry_run=True, log=lines.append)
    assert made["ai"] is None and not any("AI" in line for line in lines)


# ---- the store file of an older TacoSort -------------------------------------------------------------------
def test_engine_works_on_a_state_file_from_the_first_version(tmp_path, fb):
    from tacosort.store import MIGRATIONS

    path = tmp_path / "state.sqlite3"
    conn = sqlite3.connect(path)
    for statement in MIGRATIONS[1]:
        conn.execute(statement)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (1)")
    conn.commit()
    conn.close()
    with Store(path) as st:
        assert sort_all(cfg(), st, {"a": fb}).moved == 3
        fb.user_move("Your order #1", "Receipts", "INBOX")
        assert run(cfg(), st, {"a": fb}).learned == 1
