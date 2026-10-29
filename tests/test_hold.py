"""hold_hours: new mail stays in the Inbox until it is old enough, then a later run sorts it."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tacosort import engine
from tacosort.config import config_problems
from tacosort.models import INBOX, AccountConfig, AppConfig, Decision
from tacosort.store import Store
from tacosort.testing import FakeBackend, make_message


class AllPromotions:
    def __init__(self, config, learned, contacts):
        pass

    def classify(self, msg):
        if msg.from_.email.endswith("@shop.example.com"):
            return Decision("promotions", 0.9, "heuristic", "a shop")
        return Decision(INBOX, 0.5, "default", "not sure")


def cfg(**kw) -> AppConfig:
    kw.setdefault("company_folders", False)
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], **kw)


def run(config, store, b, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("backlog", False)
    return engine.run_once(config, store, {"a": b}, log=lambda _m: None, classifier=AllPromotions, **kw)


def ad(n, received):
    m = make_message("deals@shop.example.com", f"Sale {n}")
    return dataclasses.replace(m, received=received)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


def test_new_mail_waits_then_gets_sorted(store, monkeypatch):
    b = FakeBackend("a")
    run(cfg(hold_hours=24), store, b)                      # first look: sets the start point
    now = datetime.now(timezone.utc)
    b.add("INBOX", ad(1, now - timedelta(hours=30)))
    b.add("INBOX", ad(2, now - timedelta(hours=2)))
    b.add("INBOX", ad(3, now - timedelta(hours=40)))
    rep = run(cfg(hold_hours=24), store, b)
    assert b.subjects("INBOX") == ["Sale 2"] and rep.held == 1 and rep.moved == 2
    rep = run(cfg(hold_hours=24), store, b)                # still too new; nothing else looked at twice
    assert rep.held == 1 and rep.scanned == 0 and b.subjects("INBOX") == ["Sale 2"]
    monkeypatch.setattr(engine, "utcnow", lambda: now + timedelta(hours=23))
    rep = run(cfg(hold_hours=24), store, b)
    assert b.messages("INBOX") == [] and rep.held == 0
    assert len(b.messages("Promotions")) == 3


def test_backlog_and_preview_wait_too(store):
    b = FakeBackend("a")
    now = datetime.now(timezone.utc)
    b.add("INBOX", ad(1, now - timedelta(hours=1)))
    b.add("INBOX", ad(2, now - timedelta(days=3)))
    preview = run(cfg(hold_hours=24), store, b, dry_run=True, backlog=True)
    assert [m.subject for m in preview.moves] == ["Sale 2"] and preview.held == 1
    run(cfg(hold_hours=24), store, b, backlog=True)
    assert b.subjects("INBOX") == ["Sale 1"]


def test_no_hold_by_default_and_naive_times_are_local(store):
    b = FakeBackend("a")
    b.add("INBOX", ad(1, datetime.now(timezone.utc)))
    b.add("INBOX", ad(2, datetime.now()))                  # no zone: the computer's local time
    run(cfg(), store, b, backlog=True)
    assert b.messages("INBOX") == []
    b.add("INBOX", ad(3, datetime.now()))
    rep = run(cfg(hold_hours=1), store, b, backlog=True)
    assert rep.held == 1 and b.subjects("INBOX") == ["Sale 3"]


def test_no_receive_time_falls_back_to_the_date_header(store):
    b = FakeBackend("a")
    b.add("INBOX", make_message("deals@shop.example.com", "Old sale"))   # dated 2026-01-01, no receive time
    b.add("INBOX", make_message("deals@shop.example.com", "New sale", date=datetime.now(timezone.utc)))
    run(cfg(hold_hours=24), store, b, backlog=True)
    assert b.subjects("INBOX") == ["New sale"]


def test_hold_hours_is_checked():
    assert any("hold_hours" in p for p in config_problems(cfg(hold_hours=-1)))
    assert any("hold_hours" in p for p in config_problems(cfg(hold_hours=1000)))
    assert not any("hold_hours" in p for p in config_problems(cfg(hold_hours=24)))
