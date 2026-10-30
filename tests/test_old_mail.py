"""old_mail_days: read mail TacoSort leaves in the Inbox moves to one folder once it is old, so the
Inbox holds what is current."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tacosort import engine
from tacosort.classify import Classifier
from tacosort.config import config_problems
from tacosort.models import AccountConfig, AppConfig, Rule
from tacosort.store import Store
from tacosort.testing import FakeBackend, make_message

NOW = datetime.now(timezone.utc)
READ = frozenset({"\\Seen"})


def cfg(**kw) -> AppConfig:
    kw.setdefault("old_mail_days", 180)
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], **kw)


def run(config, store, b, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("backlog", True)
    return engine.run_once(config, store, {"a": b}, log=lambda _m: None, classifier=Classifier, **kw)


def mail(sender, subject, days_old, flags=READ, **kw):
    m = make_message(sender, subject, flags=flags, **kw)
    return dataclasses.replace(m, received=NOW - timedelta(days=days_old))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


def test_old_read_mail_moves_new_unread_and_starred_stay(store):
    b = FakeBackend("a")
    b.add("INBOX", mail("pat@friends.org", "Lunch next week?", 400))
    b.add("INBOX", mail("pat@friends.org", "Photos from the trip", 20))
    b.add("INBOX", mail("sam@friends.org", "Did you get my letter", 400, flags=frozenset()))
    b.add("INBOX", mail("kim@friends.org", "Keep this one", 400, flags=READ | {"\\Flagged"}))
    rep = run(cfg(), store, b)
    assert b.subjects("Old mail") == ["Lunch next week?"]
    assert sorted(b.subjects("INBOX")) == ["Did you get my letter", "Keep this one", "Photos from the trip"]
    [mv] = rep.moves
    assert mv.category == "old" and "older than 180 days" in mv.reason


def test_off_by_default_and_prefix_applies(store):
    b = FakeBackend("a")
    b.add("INBOX", mail("pat@friends.org", "Lunch next week?", 400))
    run(cfg(old_mail_days=0), store, b)
    assert b.subjects("INBOX") == ["Lunch next week?"]
    run(cfg(folder_prefix="TacoSort"), store, b)
    assert b.subjects("TacoSort/Old mail") == ["Lunch next week?"]


def test_vip_and_your_own_rules_stay(store):
    b = FakeBackend("a")
    b.add("INBOX", mail("mom@family.org", "Call me", 400))
    b.add("INBOX", mail("boss@work.org", "Review", 400))
    rules = [Rule(name="work", from_=["*@work.org"], action="inbox")]
    run(cfg(vip=["mom@family.org"], rules=rules), store, b)
    assert sorted(b.subjects("INBOX")) == ["Call me", "Review"]


def test_moving_it_back_is_respected(store):
    b = FakeBackend("a")
    b.add("INBOX", mail("pat@friends.org", "Lunch next week?", 400))
    run(cfg(), store, b)
    b.user_move("Lunch next week?", "Old mail", "INBOX")
    run(cfg(), store, b)
    run(cfg(), store, b)
    assert b.subjects("INBOX") == ["Lunch next week?"]


def test_daily_recheck_moves_it_once_old_enough(store, monkeypatch):
    b = FakeBackend("a")
    run(cfg(), store, b, backlog=False)                      # first look: start point
    b.add("INBOX", mail("pat@friends.org", "Lunch next week?", 0))
    run(cfg(), store, b, backlog=False)
    assert b.subjects("INBOX") == ["Lunch next week?"]
    later = NOW + timedelta(days=181)
    monkeypatch.setattr(engine, "utcnow", lambda: later)
    run(cfg(), store, b, backlog=False)                      # the daily re-check
    assert b.subjects("Old mail") == ["Lunch next week?"]


def test_settings_are_checked():
    assert any("old_mail_days" in p for p in config_problems(cfg(old_mail_days=-1)))
    assert any("old_mail_folder" in p for p in config_problems(cfg(old_mail_folder="")))
    assert not any("old_mail" in p for p in config_problems(cfg()))
