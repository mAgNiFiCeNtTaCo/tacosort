"""Regression tests for problems the re-check review found after the fix wave (2026-10-28).
Each test asserts the safe behaviour."""

import dataclasses

from tacosort import engine
from tacosort.classify.pipeline import Classifier
from tacosort.models import AccountConfig, AppConfig
from tacosort.store import Store
from tacosort.testing import FakeBackend, make_message
from tests.test_imap_move import MIDS, MailServer, connect, inbox_with


def quiet(_m):
    pass


# A. MOVE carried out, reply lost, and the destination search does not find the messages yet
#    (search index lag, e.g. Gmail/Exchange right after a move). Is the move still reported?
def test_move_done_then_drop_and_dest_search_lags():
    s = MailServer()
    uids = inbox_with(s, *MIDS)
    s.faults = [("move", "after")]
    b, _logs = connect(s)
    b._find_ids = lambda client, folder, mid: []          # the destination's index hasn't caught up
    result = b.move("INBOX", uids, "Receipts")
    gone = s.visible("INBOX") == [] and s.visible("Receipts") == list(MIDS)
    assert not gone or result, (
        f"both messages left the Inbox and are in Receipts, but move() returned {result}: "
        "the engine records nothing (no undo, no report)")


# B. A folder-learned rule for a robot sender vs. a security code / verify message from that sender.
def test_learned_robot_rule_does_not_file_away_a_security_code(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    b = FakeBackend("a")
    for i in range(8):
        b.add("Notifications", make_message("noreply@accounts.example.com", f"Weekly summary {i}",
                                            headers={"List-Unsubscribe": "<mailto:u@accounts.example.com>"}))
    for i in range(3):
        b.add("INBOX", make_message(f"friend{i}@example.org", f"hi {i}"))
    config = AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")])
    engine.learn_from_folders(config, store, {"a": b}, log=quiet)
    clf = Classifier(config, store.learned, set())
    out = {}
    for subject in ("Your verification code is 123456", "Password reset requested",
                    "Confirm your email address", "New sign-in to your account"):
        d = clf.classify(make_message("noreply@accounts.example.com", subject))
        out[subject] = (d.keeps or d.confidence < config.min_confidence, d.category, d.source, d.reason)
    moved = {k: v for k, v in out.items() if not v[0]}
    assert not moved, f"security mail filed away by a learned rule: {moved}"


# C. Two runs drawing the same run id (CI on Linux, 2026-10-28): the second overwrote the first
#    run's record, so its moves vanished from the report's run list.
def test_repeated_run_id_never_overwrites_an_earlier_run(tmp_path, monkeypatch):
    ids = iter(["20261028-080753-1acb0000", "20261028-080753-1acb0000", "20261028-080753-2bcd0000"])
    monkeypatch.setattr(engine, "_new_run_id", lambda: next(ids))
    store = Store(tmp_path / "s.sqlite3")
    b = FakeBackend("a")
    config = AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], data_dir=str(tmp_path))
    first = engine.run_once(config, store, {"a": b}, dry_run=False, log=quiet)
    second = engine.run_once(config, store, {"a": b}, dry_run=False, log=quiet)
    assert first.run_id != second.run_id
    assert {r.run_id for r in store.runs(10)} == {first.run_id, second.run_id}


# D. First real Gmail preview (2026-10-28): GitHub's "a third-party OAuth application has been added"
#    alert went to Notifications, and Gmail's tab (never in X-GM-LABELS) was missing as a clue.
def test_new_app_access_alerts_stay_in_the_inbox():
    config = AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")])
    clf = Classifier(config, None, set())
    for sender, subject in (
        ("noreply@github.com", "[GitHub] A third-party OAuth application has been added to your account"),
        ("noreply@github.com", "[GitHub] A new SSH key was added to your account"),
        ("noreply-accounts@google.com", "You shared some Google Account data with GitHub"),
        ("no-reply@accounts.nintendo.com", "[796751] Nintendo Account: E-mail address verification"),
    ):
        msg = dataclasses.replace(make_message(sender, subject), gmail_labels=frozenset({"CATEGORY_UPDATES"}))
        d = clf.classify(msg)
        assert d.keeps, (subject, d)


def test_gmail_tab_is_a_strong_clue():
    config = AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")])
    clf = Classifier(config, None, set())
    msg = make_message("hello@mail.example-anime.com", "How's it gonna end?!",
                       headers={"List-Unsubscribe": "<mailto:u@mail.example-anime.com>"})
    msg = dataclasses.replace(msg, gmail_labels=frozenset({"\\Inbox", "CATEGORY_PROMOTIONS"}))
    d = clf.classify(msg)
    assert d.category == "promotions" and d.confidence >= config.min_confidence
