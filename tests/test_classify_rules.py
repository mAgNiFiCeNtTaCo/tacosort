from __future__ import annotations

import pytest

from tidyinbox.classify.rules import (
    address_matches,
    first_matching_rule,
    rule_decision,
    rule_matches,
    validate_rules,
)
from tidyinbox.models import INBOX, AccountConfig, AppConfig, Rule
from tidyinbox.testing import make_message


def msg(sender="shop@store.example.com", subject="Your order", **kw):
    m = make_message(sender, subject, **kw)
    m.account = "personal"
    return m


@pytest.mark.parametrize("pattern,email,ok", [
    ("a@example.com", "a@example.com", True),
    ("A@Example.com", "a@example.com", True),
    ("a@example.com", "b@example.com", False),
    ("*@example.com", "x@example.com", True),
    ("*@example.com", "x@mail.example.com", False),
    ("@example.com", "x@example.com", True),
    ("example.com", "x@example.com", True),
    ("*.example.com", "x@mail.example.com", True),
    ("*.example.com", "x@example.com", True),
    ("*.example.com", "x@notexample.com", False),
    ("news*@example.com", "news-daily@example.com", True),
    ("*@*.example.org", "x@lists.example.org", True),
    ("", "x@example.com", False),
])
def test_address_patterns(pattern, email, ok):
    assert address_matches(pattern, email) is ok


def test_and_across_fields_or_within_list():
    rule = Rule(name="r", action="receipts", from_=["*@other.example.com", "*.example.com"],
                subject=["invoice", "order"])
    assert rule_matches(rule, msg())
    assert not rule_matches(rule, msg(subject="Hello there"))
    assert not rule_matches(rule, msg(sender="a@example.org"))


def test_to_matches_cc_too():
    m = msg()
    rule = Rule(name="r", action="inbox", to=["*@work.example.com"])
    assert not rule_matches(rule, m)
    from tidyinbox.models import Address
    m.cc = [Address("me@work.example.com")]
    assert rule_matches(rule, m)


def test_list_id_and_header_conditions():
    m = msg(headers={"List-Id": "Club <club.example.org>", "X-Custom": "Hello World"})
    assert rule_matches(Rule(name="r", action="newsletters", list_id=["CLUB.example"]), m)
    assert rule_matches(Rule(name="r", action="newsletters", header={"x-custom": "world"}), m)
    assert not rule_matches(Rule(name="r", action="newsletters", header={"X-Missing": ""}), m)


def test_accounts_filter():
    rule = Rule(name="r", action="receipts", subject=["order"], accounts=["work"])
    assert not rule_matches(rule, msg())
    rule.accounts = ["Personal"]
    assert rule_matches(rule, msg())


def test_rule_decisions():
    d = rule_decision(Rule(name="Shop", action="Receipts"))
    assert (d.category, d.confidence, d.source) == ("receipts", 1.0, "user-rule")
    assert "Shop" in d.reason
    d = rule_decision(Rule(name="Work", action="folder:Clients/Acme"))
    assert d.category == INBOX and d.folder == "Clients/Acme" and not d.keeps
    d = rule_decision(Rule(name="Keep", action="inbox"))
    assert d.keeps


def test_first_matching_rule_order():
    rules = [Rule(name="first", action="promotions", subject=["order"]),
             Rule(name="second", action="receipts", subject=["order"])]
    rule, d = first_matching_rule(rules, msg())
    assert rule.name == "first" and d.category == "promotions"
    assert first_matching_rule(rules, msg(subject="hi")) is None


def test_validate_rules():
    cfg = AppConfig(accounts=[AccountConfig(id="personal", email="me@example.com")])
    cfg.categories["social"] = ""          # turned off
    rules = [
        Rule(name="ok", action="receipts", from_=["*@shop.example.com"]),
        Rule(name="bad cat", action="recipes", subject=["x"]),
        Rule(name="off", action="social", subject=["x"]),
        Rule(name="", action="folder:", subject=["x"]),
        Rule(name="no cond", action="inbox"),
        Rule(name="space", action="inbox", from_=["a b@example.com"]),
        Rule(name="acct", action="inbox", subject=["x"], accounts=["nope"]),
        Rule(name="ok", action="folder:Work", subject=["y"]),
    ]
    problems = validate_rules(rules, cfg)
    text = "\n".join(problems)
    assert "recipes" in text and "not a category" in text
    assert "turned off" in text
    assert "no name" in text
    assert "folder name" in text
    assert "no conditions" in text
    assert "space" in text
    assert '"nope"' in text
    assert "already uses the name" in text
    assert not any("Rule 1 " in p for p in problems)


def test_validate_good_rules_is_empty():
    cfg = AppConfig()
    assert validate_rules([Rule(name="a", action="travel", subject=["flight"])], cfg) == []
