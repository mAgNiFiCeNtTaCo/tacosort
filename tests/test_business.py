"""The Business folder: business paperwork from robots goes there; from people (a reply about your tax
exemption) it waits in the Inbox for business_keep_days first. Also: writing to a shop's service@
address once doesn't keep its newsletters in the Inbox."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tacosort import engine
from tacosort.classify import Classifier
from tacosort.models import AccountConfig, AppConfig
from tacosort.store import Store
from tacosort.testing import FakeBackend, make_message

NOW = datetime.now(timezone.utc)
UNSUB = {"List-Unsubscribe": "<https://shop.example/u>"}


def cfg(**kw) -> AppConfig:
    kw.setdefault("business_keep_days", 3)
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], **kw)


def run(config, store, b):
    return engine.run_once(config, store, {"a": b}, log=lambda _m: None, classifier=Classifier,
                           dry_run=False, backlog=True)


def aged(m, days):
    return dataclasses.replace(m, received=NOW - timedelta(days=days))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


def test_robot_business_mail_goes_to_business(store):
    b = FakeBackend("a")
    b.add("INBOX", make_message("idverification@market.com", "Your seller verification is complete!", headers=UNSUB))
    b.add("INBOX", make_message("sales@cards.com", "Your Funding Source has been Verified", headers=UNSUB))
    run(cfg(), store, b)
    assert sorted(b.subjects("Business")) == ["Your Funding Source has been Verified",
                                              "Your seller verification is complete!"]


def test_people_s_business_mail_waits_then_moves(store):
    b = FakeBackend("a")
    store.add_contacts(["service@cards.com"], "a")
    b.add("INBOX", aged(make_message("service@cards.com", "Re: Tax Exemption"), 5))
    b.add("INBOX", aged(make_message("service@cards.com", "Re: Tax Exemption form"), 1))
    b.add("INBOX", aged(make_message("banker@credit.org", "FOLLOW UP: BUSINESS ACCOUNTS/APPLICATION 537437"), 4))
    b.add("INBOX", aged(make_message("service@cards.com", "Re: where is my order"), 9))
    rep = run(cfg(), store, b)
    assert sorted(b.subjects("Business")) == ["FOLLOW UP: BUSINESS ACCOUNTS/APPLICATION 537437",
                                              "Re: Tax Exemption"]
    assert sorted(b.subjects("INBOX")) == ["Re: Tax Exemption form", "Re: where is my order"]
    assert all("older than 3 days" in m.reason for m in rep.moves)


def test_people_s_business_mail_stays_by_default(store):
    b = FakeBackend("a")
    b.add("INBOX", aged(make_message("banker@credit.org", "Your business account application"), 30))
    run(cfg(business_keep_days=0), store, b)
    assert len(b.messages("INBOX")) == 1


def test_a_security_email_about_business_ends_up_in_business(store):
    b = FakeBackend("a")
    b.add("INBOX", aged(make_message("no.reply@market.com", "We need more information regarding your tax "
                                     "exemption request", headers=UNSUB), 5))
    run(cfg(security_keep_days=3), store, b)
    assert len(b.messages("Business")) == 1


def test_a_shop_you_wrote_to_is_not_a_person_when_it_mails_in_bulk():
    c = Classifier(cfg(), contacts={"service@cards.com"})
    promo = c.classify(make_message("service@cards.com", "Psst - Here's $10 off to complete your order!",
                                    headers=UNSUB))
    reply = c.classify(make_message("service@cards.com", "Re: Contact form submission"))
    assert promo.source != "guard" and promo.guess is not None
    assert reply.source == "guard" and "written to" in reply.reason
