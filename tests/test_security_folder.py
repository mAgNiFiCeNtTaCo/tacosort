"""security_keep_days: codes, sign-in alerts and "verify your email" stay in the Inbox while they matter,
then move to the Security folder."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tacosort import engine
from tacosort.classify import Classifier
from tacosort.config import config_problems
from tacosort.models import AccountConfig, AppConfig
from tacosort.store import Store
from tacosort.testing import FakeBackend, make_message

NOW = datetime.now(timezone.utc)


def cfg(**kw) -> AppConfig:
    kw.setdefault("security_keep_days", 3)
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], **kw)


def run(config, store, b, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("backlog", True)
    return engine.run_once(config, store, {"a": b}, log=lambda _m: None, classifier=Classifier, **kw)


def code(subject, days_old, **kw):
    m = make_message("no-reply@accounts.example.com", subject, **kw)
    return dataclasses.replace(m, received=NOW - timedelta(days=days_old))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


def test_old_security_mail_moves_new_stays(store):
    b = FakeBackend("a")
    b.add("INBOX", code("Your verification code is 123456", 5))
    b.add("INBOX", code("New sign-in from Chrome on Windows", 1))
    b.add("INBOX", code("Verify your email address", 4))
    rep = run(cfg(), store, b)
    assert b.subjects("INBOX") == ["New sign-in from Chrome on Windows"]
    assert sorted(b.subjects("Security")) == ["Verify your email address", "Your verification code is 123456"]
    assert all("older than 3 days" in m.reason for m in rep.moves)


def test_daily_recheck_moves_it_once_old_enough(store, monkeypatch):
    b = FakeBackend("a")
    run(cfg(), store, b, backlog=False)                      # first look: start point
    b.add("INBOX", code("New login to your account", 0))
    run(cfg(), store, b, backlog=False)
    assert len(b.messages("INBOX")) == 1
    later = NOW + timedelta(days=3, hours=1)
    monkeypatch.setattr(engine, "utcnow", lambda: later)
    run(cfg(), store, b, backlog=False)                      # the daily re-check
    assert b.subjects("Security") == ["New login to your account"]


def test_off_by_default_starred_stays_and_prefix_applies(store):
    b = FakeBackend("a")
    b.add("INBOX", code("Your verification code is 123456", 9))
    b.add("INBOX", code("Security alert", 9, flags=frozenset({"\\Flagged"})))
    run(cfg(security_keep_days=0), store, b)
    assert len(b.messages("INBOX")) == 2
    run(cfg(folder_prefix="TacoSort"), store, b)
    assert b.subjects("TacoSort/Security") == ["Your verification code is 123456"]
    assert b.subjects("INBOX") == ["Security alert"]


def test_moving_it_back_is_respected(store):
    b = FakeBackend("a")
    b.add("INBOX", code("Your verification code is 123456", 9))
    run(cfg(), store, b)
    b.user_move("Your verification code is 123456", "Security", "INBOX")
    run(cfg(), store, b)
    run(cfg(), store, b)
    assert b.subjects("INBOX") == ["Your verification code is 123456"]


def test_settings_are_checked():
    assert any("security_keep_days" in p for p in config_problems(cfg(security_keep_days=-1)))
    assert any("security_folder" in p for p in config_problems(cfg(security_folder="")))
    assert not any("security" in p for p in config_problems(cfg()))
