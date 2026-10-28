"""Engine tests: FakeBackend mailboxes + a tiny stub classifier (the real one is tested elsewhere)."""

from __future__ import annotations

import pytest

from mailtidy import engine
from mailtidy.models import INBOX, AccountConfig, AppConfig, Decision
from mailtidy.store import Store
from mailtidy.testing import FakeBackend, make_message

DOMAINS = {
    "shop.example.com": "receipts",
    "news.example.com": "newsletters",
    "bank.example.com": "finance",
    "deals.example.com": "promotions",
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


def cfg(*ids, **kw) -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id=i, email=f"me@{i}.example.com") for i in (ids or ("a",))], **kw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


@pytest.fixture
def fb():
    b = FakeBackend("a")
    b.add("INBOX", make_message("order@shop.example.com", "Your order #1"))
    b.add("INBOX", make_message("order@shop.example.com", "Your order #2"))
    b.add("INBOX", make_message("weekly@news.example.com", "This week"))
    b.add("INBOX", make_message("friend@example.org", "Lunch?"))
    b.add("INBOX", make_message("order@shop.example.com", "Starred order", flags=frozenset({"\\Flagged"})))
    return b


def run(config, store, backends, **kw):
    kw.setdefault("dry_run", False)
    return engine.run_once(config, store, backends, log=lambda m: None, classifier=StubClassifier, **kw)


def test_first_run_moves_the_right_mail(store, fb):
    report = run(cfg(), store, {"a": fb})
    assert report.errors == []
    assert sorted(fb.subjects("Receipts")) == ["Your order #1", "Your order #2"]
    assert fb.subjects("Newsletters") == ["This week"]
    assert sorted(fb.subjects("INBOX")) == ["Lunch?", "Starred order"]
    assert (report.scanned, report.moved, report.kept) == (5, 3, 2)
    stored = store.recent_moves()
    assert len(stored) == 3 and {m.to_folder for m in stored} == {"Receipts", "Newsletters"}
    assert all(m.new_uid for m in stored)
    assert store.get_watermark("a", "INBOX")[1] == 5
    assert store.last_run()["moved"] == 3


def test_second_run_does_nothing_new(store, fb):
    run(cfg(), store, {"a": fb})
    fb.calls.clear()
    report = run(cfg(), store, {"a": fb})
    assert (report.scanned, report.moved) == (0, 0)
    assert not [c for c in fb.calls if c[0] == "move"]


def test_only_new_mail_is_processed(store, fb):
    run(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    report = run(cfg(), store, {"a": fb})
    assert (report.scanned, report.moved) == (1, 1)
    assert fb.subjects("Finance") == ["Your statement"]


def test_dry_run_changes_nothing(store, fb):
    before = {f: sorted(fb.subjects(f)) for f in ("INBOX",)}
    report = run(cfg(), store, {"a": fb}, dry_run=True)
    assert report.moved == 3 and len(report.moves) == 3
    assert {m.to_folder for m in report.moves} == {"Receipts", "Newsletters"}
    assert sorted(fb.subjects("INBOX")) == before["INBOX"]
    assert not [c for c in fb.calls if c[0] in ("move", "ensure_folder")]
    assert "Receipts" not in fb._folders
    assert store.get_watermark("a", "INBOX") is None
    assert store.recent_moves() == []
    assert store.seen("a", fb.messages("INBOX")[0].message_id) is None
    assert store.has_previewed()
    # The real run afterwards still does the work.
    assert run(cfg(), store, {"a": fb}).moved == 3


def test_message_put_back_in_inbox_is_never_moved_again_and_sender_is_learned(store, fb):
    run(cfg(), store, {"a": fb})
    fb.user_move("Your order #1", "Receipts", "INBOX")
    report = run(cfg(), store, {"a": fb})
    assert report.learned == 1
    assert "Your order #1" in fb.subjects("INBOX")
    d = store.learned.lookup("order@shop.example.com", "shop.example.com", "", "a")
    assert d.category == INBOX and d.source == "learned"
    # New mail from that sender now stays in the Inbox, and later runs never touch the message.
    fb.add("INBOX", make_message("order@shop.example.com", "Your order #3"))
    run(cfg(), store, {"a": fb})
    run(cfg(), store, {"a": fb}, backlog=True)
    assert {"Your order #1", "Your order #3"} <= set(fb.subjects("INBOX"))
    assert store.corrections()[0]["to_folder"] == "INBOX"


def test_put_back_is_respected_even_without_correction_learning(store, fb):
    config = cfg(learn_from_corrections=False)
    run(config, store, {"a": fb})
    fb.user_move("Your order #1", "Receipts", "INBOX")
    report = run(config, store, {"a": fb})
    assert report.learned == 0 and report.moved == 0
    run(config, store, {"a": fb}, backlog=True)
    assert "Your order #1" in fb.subjects("INBOX")
    assert store.seen("a", next(m for m in fb.messages("INBOX") if m.subject == "Your order #1").message_id).override


def test_user_moves_sorted_mail_to_another_folder(store, fb):
    run(cfg(), store, {"a": fb})
    fb.user_move("Your order #2", "Receipts", "Shopping")
    assert run(cfg(), store, {"a": fb}).learned == 1
    fb.add("INBOX", make_message("order@shop.example.com", "Your order #9"))
    run(cfg(), store, {"a": fb})
    assert "Your order #9" in fb.subjects("Shopping")
    assert "Your order #2" in fb.subjects("Shopping")      # the user's choice is left alone


def test_user_moves_to_another_category_folder_applies_to_all_accounts(store, fb):
    other = FakeBackend("b")
    run(cfg("a", "b"), store, {"a": fb, "b": other})
    fb.user_move("This week", "Newsletters", "Promotions")
    run(cfg("a", "b"), store, {"a": fb, "b": other})
    other.add("INBOX", make_message("weekly@news.example.com", "Next week"))
    run(cfg("a", "b"), store, {"a": fb, "b": other})
    assert other.subjects("Promotions") == ["Next week"]


def test_deleted_message_teaches_nothing(store, fb):
    run(cfg(), store, {"a": fb})
    fb.user_move("This week", "Newsletters", "Trash")
    assert run(cfg(), store, {"a": fb}).learned == 0
    assert store.learned.lookup("weekly@news.example.com", "news.example.com", "", "a") is None


def test_uidvalidity_reset_does_not_double_move(store, fb):
    run(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("order@shop.example.com", "Your order #4"))
    fb.reset_uidvalidity("INBOX")
    fb.calls.clear()
    report = run(cfg(), store, {"a": fb})
    assert report.moved == 1 and report.scanned == 1
    assert sorted(fb.subjects("Receipts")) == ["Your order #1", "Your order #2", "Your order #4"]
    assert store.get_watermark("a", "INBOX") == (fb._uidvalidity["INBOX"], 3)
    assert run(cfg(), store, {"a": fb}).scanned == 0


def test_undo_last_run_restores_and_is_not_resorted(store, fb):
    report = run(cfg(), store, {"a": fb})
    n = engine.undo(cfg(), store, {"a": fb}, log=lambda m: None)
    assert n == 3
    assert len(fb.messages("INBOX")) == 5 and fb.messages("Receipts") == []
    assert all(m.undone for m in store.moves_for_run(report.run_id, include_undone=True))
    assert run(cfg(), store, {"a": fb}, backlog=True).moved == 0
    assert engine.undo(cfg(), store, {"a": fb}, log=lambda m: None) == 0


def test_undo_last_n_and_by_run_id(store, fb):
    first = run(cfg(), store, {"a": fb})
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    run(cfg(), store, {"a": fb})
    assert engine.undo(cfg(), store, {"a": fb}, last=1, log=lambda m: None) == 1
    assert "Your statement" in fb.subjects("INBOX")
    assert engine.undo(cfg(), store, {"a": fb}, run_id=first.run_id, log=lambda m: None) == 3
    assert len(fb.messages("INBOX")) == 6


def test_undo_skips_message_the_user_already_moved(store, fb):
    run(cfg(), store, {"a": fb})
    fb.user_move("This week", "Newsletters", "Reading")
    assert engine.undo(cfg(), store, {"a": fb}, log=lambda m: None) == 2
    assert fb.subjects("Reading") == ["This week"]


def test_one_broken_account_does_not_stop_the_others(store, fb):
    broken = FakeBackend("b", fail_connect="The password for b was not accepted.")
    report = run(cfg("b", "a"), store, {"a": fb, "b": broken})
    assert report.moved == 3
    assert report.errors == ["b: The password for b was not accepted."]


def test_unexpected_crash_in_one_account_is_reported_in_plain_english(store, fb):
    class Exploding(FakeBackend):
        def folder_state(self, folder):
            raise ValueError("boom")

    report = run(cfg("b", "a"), store, {"a": fb, "b": Exploding("b")})
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
    assert run(config, store, {"a": b}).moved == 2
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
        report = run(cfg(), store, {"a": fb})
        assert report.run_id == "" and report.moved == 0
        assert fb.calls == []
        with pytest.raises(engine.EngineBusy), engine.RunLock(tmp_path):
            pass
    finally:
        lock.release()
    assert not engine.is_running(cfg(), store)
    assert run(cfg(), store, {"a": fb}).moved == 3


def test_backlog_resorts_kept_mail_after_learning(store, fb):
    run(cfg(), store, {"a": fb})
    store.learned.learn_correction("a", "address", "friend@example.org", category="social")
    assert run(cfg(), store, {"a": fb}).moved == 0                  # normal run: old mail is done
    assert run(cfg(), store, {"a": fb}, backlog=True).moved == 1    # backlog: sort everything again
    assert fb.subjects("Social") == ["Lunch?"]


def test_special_and_missing_watch_folders_are_skipped(store, fb):
    config = cfg()
    config.accounts[0].watch_folders = ["Sent", "Nope", "INBOX"]
    report = run(config, store, {"a": fb})
    assert report.moved == 3
    assert len(report.errors) == 2 and "special folder" in report.errors[0] and "does not exist" in report.errors[1]


def test_never_moves_into_trash_or_low_confidence(store):
    b = FakeBackend("a")
    b.add("INBOX", make_message("x@spam.example.com", "Bad"))
    b.add("INBOX", make_message("y@unsure.example.com", "Meh"))

    class Weird:
        def classify(self, msg):
            if msg.subject == "Bad":
                return Decision("folder", 1.0, "user-rule", "rule", folder="Trash")
            return Decision("promotions", 0.3, "heuristic", "weak")

    report = engine.run_once(cfg(), store, {"a": b}, dry_run=False, log=lambda m: None, classifier=Weird())
    assert report.moved == 0 and report.kept == 2
    assert sorted(b.subjects("INBOX")) == ["Bad", "Meh"]


def test_classify_many_is_used_when_available(store, fb):
    calls = []

    class Batch(StubClassifier):
        def classify_many(self, msgs):
            calls.append(len(msgs))
            return [self.classify(m) for m in msgs]

    run_report = engine.run_once(cfg(), store, {"a": fb}, dry_run=False, log=lambda m: None, classifier=Batch)
    assert calls == [5] and run_report.moved == 3


def test_log_file_is_written_without_subjects(store, fb, tmp_path):
    run(cfg(), store, {"a": fb})
    for h in engine.logger.handlers:
        h.flush()
    text = (tmp_path / "mailtidy.log").read_text(encoding="utf-8")
    assert "moved 2 message(s) from INBOX to Receipts" in text
    assert "Your order" not in text


def test_watch_runs_and_stops(store, fb):
    sleeps = []
    lines = []
    fb.add("INBOX", make_message("statements@bank.example.com", "Your statement"))
    engine.watch(cfg(), store, lambda: {"a": fb}, log=lines.append, classifier=StubClassifier, max_cycles=2,
                 sleep=sleeps.append, idle_seconds=1)
    assert fb.subjects("Finance") == ["Your statement"]
    assert sleeps == [engine.MIN_CYCLE_SECONDS, engine.MIN_CYCLE_SECONDS]   # FakeBackend.idle returns at once
    assert lines[-1] == "Stopped watching."


def test_watch_backs_off_when_offline_and_recovers(store, fb):
    sleeps = []
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) <= 4:
            return {"a": FakeBackend("a", fail_connect="The server can't be reached.")}
        return {"a": fb}

    engine.watch(cfg(), store, factory, log=lambda m: None, classifier=StubClassifier, max_cycles=3,
                 sleep=sleeps.append, idle_seconds=1)
    assert sleeps[:2] == [30, 60]
    assert sorted(fb.subjects("Receipts")) == ["Your order #1", "Your order #2"]


def test_watch_stops_on_ctrl_c(store, fb):
    def factory():
        raise KeyboardInterrupt

    lines = []
    engine.watch(cfg(), store, factory, log=lines.append, classifier=StubClassifier)
    assert lines[-1] == "Stopped watching."


def test_learn_from_folders_wrapper_uses_lock(store, tmp_path, monkeypatch):
    b = FakeBackend("a")
    for s in ("Fees", "Trip", "Concert"):
        b.add("Kids School", make_message("office@school.example.org", s))
    report = engine.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    assert report.rules_learned >= 1
    lock = engine.RunLock(tmp_path)
    assert lock.acquire()
    orig = engine.RunLock.acquire
    monkeypatch.setattr(engine.RunLock, "acquire", lambda self, wait=0: orig(self, 0))   # don't wait 2 minutes
    try:
        with pytest.raises(engine.EngineBusy):
            engine.learn_from_folders(cfg(), store, {"a": b}, log=lambda m: None)
    finally:
        lock.release()


def _fake_pipeline(monkeypatch):
    """Stand-ins for the real Classifier and AIClassifier: record what the engine hands them."""
    import types

    made = {}

    class Recorder(StubClassifier):
        def __init__(self, config, learned, contacts, ai=None):
            super().__init__(config, learned, contacts)
            made["ai"] = ai

    class FakeAI:
        def __init__(self, ai_config, key):
            made["key"] = key

    monkeypatch.setitem(__import__("sys").modules, "mailtidy.classify.pipeline",
                        types.SimpleNamespace(Classifier=Recorder))
    monkeypatch.setitem(__import__("sys").modules, "mailtidy.classify.ai", types.SimpleNamespace(AIClassifier=FakeAI))
    return made, FakeAI


def test_ai_is_wired_in_when_enabled_and_a_key_exists(store, fb, monkeypatch):
    made, fake_ai = _fake_pipeline(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    config = cfg()
    config.ai.enabled = True
    report = engine.run_once(config, store, {"a": fb}, dry_run=True, log=lambda m: None)
    assert report.moved == 3
    assert isinstance(made["ai"], fake_ai) and made["key"] == "test-key-not-real"


def test_ai_without_key_is_skipped_with_a_plain_message(store, fb, monkeypatch):
    made, _ = _fake_pipeline(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(engine, "_anthropic_key", lambda config: None)
    config = cfg()
    config.ai.enabled = True
    lines = []
    engine.run_once(config, store, {"a": fb}, dry_run=True, log=lines.append)
    assert made["ai"] is None
    assert any("no API key was found in ANTHROPIC_API_KEY" in line for line in lines)
    config.ai.enabled = False
    lines.clear()
    engine.run_once(config, store, {"a": fb}, dry_run=True, log=lines.append)
    assert made["ai"] is None and not any("AI" in line for line in lines)


def test_start_from_now_sorts_only_mail_that_arrives_later(store, fb):
    lines = []
    assert engine.start_from_now(cfg(), store, {"a": fb}, log=lines.append) == ["a"]
    assert any("--backlog" in line for line in lines)
    assert run(cfg(), store, {"a": fb}).moved == 0                 # the old mail stays put
    fb.add("INBOX", make_message("order@shop.example.com", "Your order #3"))
    assert run(cfg(), store, {"a": fb}).moved == 1
    assert "Your order #3" in fb.subjects("Receipts") and "Your order #1" in fb.subjects("INBOX")
    # Already started: a second call changes nothing, and --backlog still sorts the rest.
    assert engine.start_from_now(cfg(), store, {"a": fb}, log=lines.append) == []
    assert run(cfg(), store, {"a": fb}, backlog=True).moved == 3


def test_busy_run_is_marked_skipped(store, fb, tmp_path):
    lock = engine.RunLock(tmp_path)
    assert lock.acquire()
    try:
        report = run(cfg(), store, {"a": fb})
    finally:
        lock.release()
    assert report.skipped and report.run_id == "" and fb.subjects("Receipts") == []
