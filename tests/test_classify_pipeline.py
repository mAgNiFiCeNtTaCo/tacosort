from __future__ import annotations

from mailtidy.classify import Classifier
from mailtidy.models import INBOX, AccountConfig, AIConfig, AppConfig, Decision, Rule
from mailtidy.testing import make_message

UNSUB = {"List-Unsubscribe": "<https://example.com/u>"}


def cfg(**kw) -> AppConfig:
    c = AppConfig(accounts=[AccountConfig(id="personal", email="me@example.com")])
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def receipt(**kw):
    m = make_message("auto-confirm@amazon.com", "Your order #123 has shipped", **kw)
    m.account = "personal"
    return m


class FakeLearned:
    def __init__(self, decision=None, raises=False):
        self.decision = decision
        self.raises = raises
        self.calls = []

    def lookup(self, sender, domain, list_id, account):
        self.calls.append((sender, domain, list_id, account))
        if self.raises:
            raise RuntimeError("db locked")
        return self.decision


class FakeAI:
    def __init__(self, answer):
        self.answer = answer
        self.seen = []

    def classify_batch(self, msgs, categories=None):
        self.seen.append((list(msgs), list(categories or [])))
        return [self.answer(m) if callable(self.answer) else self.answer for m in msgs]


def test_heuristic_moves_a_receipt():
    d = Classifier(cfg()).classify(receipt())
    assert d.category == "receipts" and d.source == "heuristic" and d.reason


def test_guard_beats_rule_beats_learned_beats_heuristic():
    m = receipt(flags=frozenset({"\\Flagged"}))
    rules = [Rule(name="promo", action="promotions", from_=["*@amazon.com"])]
    learned = FakeLearned(Decision("travel", 0.99, "learned", "you moved it"))
    c = Classifier(cfg(rules=rules), learned=learned)
    assert c.classify(m).source == "guard"
    d = c.classify(receipt())
    assert (d.source, d.category) == ("user-rule", "promotions")
    c = Classifier(cfg(), learned=learned)
    d = c.classify(receipt())
    assert (d.source, d.category) == ("learned", "travel")
    assert learned.calls[-1] == ("auto-confirm@amazon.com", "amazon.com", "", "personal")
    d = Classifier(cfg(), learned=FakeLearned(None)).classify(receipt())
    assert d.source == "heuristic"


def test_keep_flagged_can_be_turned_off():
    m = receipt(flags=frozenset({"\\Flagged"}))
    assert Classifier(cfg(keep_flagged=False)).classify(m).category == "receipts"


def test_contacts_guard():
    m = receipt()
    assert Classifier(cfg(), contacts={"Auto-Confirm@amazon.com"}).classify(m).source == "guard"
    assert Classifier(cfg(keep_contacts=False), contacts={"auto-confirm@amazon.com"}).classify(m).source \
        == "heuristic"


def test_vip_addresses_and_domains():
    for entry in ("auto-confirm@amazon.com", "amazon.com", "*@amazon.com", "@amazon.com", "*.amazon.com"):
        d = Classifier(cfg(vip=[entry])).classify(receipt())
        assert d.source == "guard" and d.keeps, entry
    assert Classifier(cfg(vip=["ebay.com"])).classify(receipt()).source == "heuristic"


def test_reply_guard_needs_no_bulk_headers_and_to_you():
    reply = {"In-Reply-To": "<a@example.com>"}
    m = make_message("agent@help.example.com", "Re: ticket 55 - order refund", headers=reply)
    assert Classifier(cfg()).classify(m).source == "guard"
    m2 = make_message("agent@help.example.com", "Re: ticket", headers=reply, to="someone@example.org")
    assert Classifier(cfg()).classify(m2).source != "guard"
    m3 = make_message("notifications@github.com", "Re: [org/repo] bug",
                      headers={**reply, "List-Id": "<repo.github.com>"})
    assert Classifier(cfg()).classify(m3).source != "guard"


def test_min_confidence_turns_weak_guess_into_inbox():
    learned = FakeLearned(Decision("travel", 0.7, "learned", "weak"))
    d = Classifier(cfg(), learned=learned).classify(make_message("noreply@unknown.example.com", "Hello"))
    assert d.keeps and d.source == "default" and "Travel" in d.reason
    # raising the bar makes the heuristic receipt unsure too
    d = Classifier(cfg(min_confidence=0.999)).classify(receipt())
    assert d.keeps and d.source == "default" and "Receipts" in d.reason


def test_weak_learned_falls_through_to_heuristic():
    learned = FakeLearned(Decision("travel", 0.5, "learned", "weak"))
    assert Classifier(cfg(), learned=learned).classify(receipt()).category == "receipts"


def test_disabled_category_stays_in_inbox():
    c = cfg()
    c.categories["receipts"] = ""
    d = Classifier(c).classify(receipt())
    assert d.keeps and "turned off" in d.reason


def test_unknown_rule_category_stays_in_inbox():
    rules = [Rule(name="oops", action="recipes", from_=["*@amazon.com"])]
    d = Classifier(cfg(rules=rules)).classify(receipt())
    assert d.keeps and "not a category" in d.reason


def test_folder_rule_and_prefix():
    rules = [Rule(name="Shop", action="folder:Shopping/Amazon", from_=["*@amazon.com"])]
    d = Classifier(cfg(rules=rules, folder_prefix="MailTidy")).classify(receipt())
    assert d.folder == "Shopping/Amazon" and not d.keeps


def test_custom_category_from_rule():
    c = cfg(rules=[Rule(name="Kids", action="school", from_=["*@school.example.org"])])
    c.categories["school"] = "School"
    m = make_message("office@school.example.org", "Field trip form")
    assert Classifier(c).classify(m).category == "school"


def test_broken_learned_store_is_ignored():
    d = Classifier(cfg(), learned=FakeLearned(raises=True)).classify(receipt())
    assert d.category == "receipts"


def test_security_mail_stays_even_from_shop():
    m = make_message("no-reply@amazon.com", "Your Amazon verification code", headers=UNSUB)
    d = Classifier(cfg()).classify(m)
    assert d.keeps and d.source == "heuristic"


def test_default_when_nothing_matches():
    d = Classifier(cfg()).classify(make_message("noreply@unknown.example.com", "Hello"))
    assert d.keeps and d.source == "default" and d.reason


def test_ai_used_only_for_unsure_messages_and_in_one_batch():
    ai = FakeAI(Decision("promotions", 0.9, "ai", "AI: marketing"))
    c = cfg(ai=AIConfig(enabled=True))
    c.categories["social"] = ""
    unsure1 = make_message("noreply@unknown.example.com", "Hello")
    unsure2 = make_message("team@app.example.com", "Something new")
    out = Classifier(c, ai=ai).classify_many([receipt(), unsure1, unsure2])
    assert [d.source for d in out] == ["heuristic", "ai", "ai"]
    assert len(ai.seen) == 1 and len(ai.seen[0][0]) == 2
    cats = ai.seen[0][1]
    assert "receipts" in cats and "social" not in cats and INBOX not in cats


def test_ai_low_confidence_and_none_mean_inbox():
    c = cfg(ai=AIConfig(enabled=True))
    m = make_message("noreply@unknown.example.com", "Hello")
    d = Classifier(c, ai=FakeAI(Decision("promotions", 0.4, "ai", "AI: maybe"))).classify(m)
    assert d.keeps and d.source == "default"
    d = Classifier(c, ai=FakeAI(None)).classify(m)
    assert d.keeps


def test_ai_not_used_when_disabled_or_for_confident_inbox():
    ai = FakeAI(Decision("promotions", 0.9, "ai", "x"))
    Classifier(cfg(), ai=ai).classify(make_message("noreply@unknown.example.com", "Hello"))
    assert ai.seen == []
    Classifier(cfg(ai=AIConfig(enabled=True)), ai=ai).classify(
        make_message("test.friend.mailtidy@gmail.com", "Lunch?"))
    assert ai.seen == []


def test_ai_answer_for_disabled_category_stays():
    c = cfg(ai=AIConfig(enabled=True))
    c.categories["promotions"] = ""
    d = Classifier(c, ai=FakeAI(Decision("promotions", 0.9, "ai", "x"))).classify(
        make_message("noreply@unknown.example.com", "Hello"))
    assert d.keeps


def test_ai_crash_does_not_break_classification():
    class Boom:
        def classify_batch(self, msgs, categories=None):
            raise RuntimeError("boom")
    d = Classifier(cfg(ai=AIConfig(enabled=True)), ai=Boom()).classify(
        make_message("noreply@unknown.example.com", "Hello"))
    assert d.keeps


def test_classify_many_keeps_order():
    msgs = [receipt(), make_message("test.friend.mailtidy@gmail.com", "Hi"), receipt()]
    out = Classifier(cfg()).classify_many(msgs)
    assert [d.category for d in out] == ["receipts", INBOX, "receipts"]
