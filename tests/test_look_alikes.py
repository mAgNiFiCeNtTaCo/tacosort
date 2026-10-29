"""Look-alike emails: one address sending the same kind of email again and again is a robot, even when
its headers look personal, so its mail may join its company's folder. Plus the daily re-check of mail
TacoSort left in the Inbox."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tacosort import engine
from tacosort.classify import Classifier
from tacosort.classify.heuristics import subject_template
from tacosort.config import config_problems
from tacosort.models import AccountConfig, AppConfig
from tacosort.store import Store
from tacosort.testing import FakeBackend, make_message


def cfg(**kw) -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], **kw)


def run(config, store, b, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("backlog", True)
    return engine.run_once(config, store, {"a": b}, log=lambda _m: None, classifier=Classifier, **kw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


def sold(card):
    # Plain headers, no robot markers: on its own this looks like it could be from a person.
    return make_message("sales@cardshop.com", f"Your CardShop.com items of {card} have sold",
                        name="CardShop")


@pytest.mark.parametrize("subject,template", [
    ("Your TCGplayer.com items of D.D. Crow have sold", "your tcgplayer com items of"),
    ("TCGplayer: Your Daily Summary for 10/24/2026", "tcgplayer your daily summary for"),
    ("Re: Order 12345 shipped", "order # shipped"),
    ("Was $2,750.99 now $2,613.44 - come and save", "was # now # come"),
    ("Was $19.95 now $18.95-come and save", "was # now # come"),
    ("Hi", ""),
])
def test_subject_template(subject, template):
    assert subject_template(subject) == template


def test_look_alike_mail_gets_its_company_folder(store):
    b = FakeBackend("a")
    for i in range(6):
        b.add("INBOX", sold(f"Card {i}"))
    run(cfg(), store, b)
    assert len(b.messages("Notifications/CardShop")) == 6 and b.messages("INBOX") == []


def test_a_few_look_alikes_are_not_enough(store):
    b = FakeBackend("a")
    for i in range(4):
        b.add("INBOX", sold(f"Card {i}"))
    run(cfg(), store, b)
    assert len(b.messages("INBOX")) == 4
    b.add("INBOX", sold("Card 9"))                             # the 5th one: now it's a pattern
    run(cfg(), store, b)
    assert b.messages("INBOX") == []


def test_people_and_buyers_stay_however_often_they_write(store):
    b = FakeBackend("a")
    store.add_contacts(["pen.pal@example.org"], "a")
    for i in range(6):
        b.add("INBOX", make_message("pen.pal@example.org", f"Our trip plans part {i}"))
        b.add("INBOX", make_message("sales@cardshop.com", f"Message from Buyer for Order {i}"))
    run(cfg(), store, b)
    assert len(b.messages("INBOX")) == 12


def test_turned_off(store):
    b = FakeBackend("a")
    for i in range(6):
        b.add("INBOX", sold(f"Card {i}"))
    run(cfg(look_alike_min=0), store, b)
    assert len(b.messages("INBOX")) == 6


def test_daily_recheck_sorts_mail_left_earlier(store, monkeypatch):
    b = FakeBackend("a")
    b.add("INBOX", make_message("old@before.example.com", "There before TacoSort"))
    run(cfg(), store, b, backlog=False)                          # first look: start point
    for i in range(4):
        b.add("INBOX", sold(f"Card {i}"))
    run(cfg(), store, b, backlog=False)
    assert len(b.messages("INBOX")) == 5                         # 4 look-alikes: not yet a pattern
    b.add("INBOX", sold("Card 4"))
    run(cfg(), store, b, backlog=False)
    assert len(b.messages("INBOX")) == 6                         # a pattern now, but one new email alone
    later = engine.utcnow() + timedelta(days=1, minutes=1)
    monkeypatch.setattr(engine, "utcnow", lambda: later)
    run(cfg(), store, b, backlog=False)                          # the daily re-check
    assert b.subjects("INBOX") == ["There before TacoSort"]      # never looked at: only --backlog sorts it
    assert len(b.messages("Notifications/CardShop")) == 5


def test_recheck_can_be_turned_off(store, monkeypatch):
    b = FakeBackend("a")
    run(cfg(recheck_inbox=False), store, b, backlog=False)
    for i in range(4):
        b.add("INBOX", sold(f"Card {i}"))
    run(cfg(recheck_inbox=False), store, b, backlog=False)
    b.add("INBOX", sold("Card 4"))
    later = engine.utcnow() + timedelta(days=2)
    monkeypatch.setattr(engine, "utcnow", lambda: later)
    run(cfg(recheck_inbox=False), store, b, backlog=False)
    assert len(b.messages("INBOX")) == 5                        # without a re-check they all stay


def test_look_alike_min_is_checked():
    assert any("look_alike_min" in p for p in config_problems(cfg(look_alike_min=1)))
    assert not any("look_alike_min" in p for p in config_problems(cfg(look_alike_min=5)))


def test_an_address_that_sends_lots_of_mail_is_a_robot(store):
    b = FakeBackend("a")
    for i in range(10):
        b.add("INBOX", make_message("sales@cardshop.com", f"{['Payout', 'Level up', 'Tips'][i % 3]} news {i}",
                                    name="CardShop"))
    run(cfg(), store, b)
    assert b.messages("INBOX") == [] and len(b.messages("Notifications/CardShop")) == 10


def test_unsure_to_folder_files_robot_one_offs_by_their_best_guess(store):
    survey = make_message("surveys@poll.com", "Your opinion counts", headers={"List-Unsubscribe": "<u>"})
    person = make_message("someone@mail.org", "Quick question")
    for config, inbox in ((cfg(), 2), (cfg(unsure_to_folder=True), 1)):
        b = FakeBackend("a")
        b.add("INBOX", survey)
        b.add("INBOX", person)
        with Store(store.path.parent / f"s{inbox}.sqlite3") as s:
            run(config, s, b)
        assert len(b.messages("INBOX")) == inbox
        assert "Quick question" in b.subjects("INBOX")


@pytest.mark.parametrize("subject", ["Confirm your TCGplayer account", "Verify your Carrd account"])
def test_confirm_your_brand_account_stays(subject):
    m = make_message("sales@cardshop.com", subject, headers={"List-Unsubscribe": "<u>"})
    d = Classifier(cfg()).classify(m)
    assert d.keeps and d.source == "guard"
