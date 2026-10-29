"""Company sub-folders ("Promotions/Crunchyroll"): naming, the threshold, tidying mail filed earlier,
undo after a tidy, previews, and decisions that must keep their exact folder."""

from __future__ import annotations

import pytest

from tacosort import engine
from tacosort.companies import clean_name, company_key, folder_safe, pick_name
from tacosort.models import INBOX, AccountConfig, AppConfig, Decision
from tacosort.store import Store
from tacosort.testing import FakeBackend, make_message

CATEGORY_OF = {"crunchyroll.com": "promotions", "google.com": "receipts", "anthropic.com": "receipts"}


class ByCompany:
    """Sorts by the sender's company (a tiny stand-in for the real classifier)."""

    def __init__(self, config, learned, contacts):
        self.config = config

    def classify(self, msg):
        cat = CATEGORY_OF.get(company_key(msg.from_.email))
        if cat:
            return Decision(cat, 0.9, "heuristic", f"known sender {msg.from_.domain}")
        return Decision(INBOX, 0.5, "default", "not sure")


def cfg(**kw) -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com")], **kw)


def quiet(_m):
    pass


def has(b, name):
    return any(f.lower() == name.lower() for f in b._folders)


def run(config, store, b, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("backlog", True)
    kw.setdefault("classifier", ByCompany)
    return engine.run_once(config, store, {"a": b}, log=quiet, **kw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


def anime(n):
    return make_message("hello@mail.crunchyroll.com", f"Anime pick {n}", name="Crunchyroll")


def play(n):
    return make_message("googleplay-noreply@google.com", f"Your Google Play Order Receipt #{n}",
                        name="Google Play")


# ---- naming ------------------------------------------------------------------------------------
@pytest.mark.parametrize("email,key", [
    ("hello@mail.crunchyroll.com", "crunchyroll.com"),
    ("a@shop.example.co.uk", "example.co.uk"),
    ("x@github.com", "github.com"),
    ("nobody", ""),
    ("me@localhost", ""),
])
def test_company_key_is_the_registrable_domain(email, key):
    assert company_key(email) == key


@pytest.mark.parametrize("display,key,name", [
    ("Google Play", "google.com", "Google Play"),
    ("Anthropic, PBC", "anthropic.com", "Anthropic"),
    ("GitHub Notifications", "github.com", "GitHub"),
    ("Samsung US", "samsungusa.com", "Samsung US"),
    ("Kilian (TCGplayer)", "tcgplayer.com", "TCGplayer"),            # a person at the company
    ("Kilian (Support)", "tcgplayer.com", None),
    ("Ruben's Big Deals", "playfame.com", None),          # not clearly the company: domain name instead
    ("noreply@playfame.com", "playfame.com", None),
    ("", "playfame.com", None),
])
def test_display_names_must_belong_to_the_domain(display, key, name):
    assert clean_name(display, key) == name


def test_pick_name_falls_back_to_the_domain_and_is_folder_safe():
    assert pick_name(["Ruben's Big Deals", ""], "playfame.com") == "Playfame"
    assert pick_name(["mAgNiFiCeNtTaCo"], "github.com") == "GitHub"     # GitHub mail carries a person's name
    assert pick_name(["Google Play", "Google Play", "Google"], "google.com") == "Google Play"
    assert folder_safe("Booking.com") == "Booking"
    assert "/" not in folder_safe("A/B Shop") and "." not in folder_safe("Shop.Inc.")


# ---- the engine --------------------------------------------------------------------------------
def test_below_the_threshold_mail_goes_to_the_plain_folder(store):
    b = FakeBackend("a")
    for i in range(2):
        b.add("INBOX", anime(i))
    run(cfg(), store, b)
    assert len(b.messages("Promotions")) == 2
    assert not has(b, "Promotions/Crunchyroll")


def test_three_from_one_company_get_their_own_folder(store):
    b = FakeBackend("a")
    for i in range(3):
        b.add("INBOX", anime(i))
    b.add("INBOX", make_message("billing@mail.anthropic.com", "Your receipt", name="Anthropic, PBC"))
    run(cfg(), store, b)
    assert len(b.messages("Promotions/Crunchyroll")) == 3
    assert b.messages("Promotions") == []
    assert b.subjects("Receipts") == ["Your receipt"]             # only one from Anthropic: no folder
    # New mail from the company goes straight there.
    b.add("INBOX", anime(9))
    run(cfg(), store, b, backlog=False)
    assert "Anime pick 9" in b.subjects("Promotions/Crunchyroll")


def test_mail_filed_earlier_is_tidied_and_undo_still_brings_it_back(store):
    b = FakeBackend("a")
    for i in range(2):
        b.add("INBOX", play(i))
    first = run(cfg(), store, b)
    assert len(b.messages("Receipts")) == 2
    b.add("INBOX", play(2))
    run(cfg(), store, b, backlog=False)
    assert sorted(b.subjects("Receipts/Google Play")) == [f"Your Google Play Order Receipt #{i}" for i in range(3)]
    assert b.messages("Receipts") == []
    # Undoing the first run puts its two messages back in the Inbox from their new folder.
    assert engine.undo(cfg(), store, {"a": b}, run_id=first.run_id, log=quiet) == 2
    assert len(b.messages("INBOX")) == 2 and len(b.messages("Receipts/Google Play")) == 1


def test_mail_already_filed_by_an_older_version_is_tidied(store):
    # The first real run happened before company folders existed (company_folders off).
    b = FakeBackend("a")
    for i in range(4):
        b.add("INBOX", play(i))
    run(cfg(company_folders=False), store, b)
    assert len(b.messages("Receipts")) == 4
    preview = run(cfg(), store, b, dry_run=True)
    assert len(b.messages("Receipts")) == 4                       # a preview moves nothing
    assert [m.to_folder for m in preview.moves] == ["Receipts/Google Play"] * 4
    run(cfg(), store, b, backlog=False)
    assert len(b.messages("Receipts/Google Play")) == 4 and b.messages("Receipts") == []


def test_the_folder_name_never_changes(store):
    b = FakeBackend("a")
    for i in range(3):
        b.add("INBOX", play(i))
    run(cfg(), store, b)
    b.add("INBOX", make_message("pay-noreply@google.com", "Your receipt", name="Google Pay"))
    b.add("INBOX", make_message("pay-noreply@google.com", "Your receipt 2", name="Google Pay"))
    run(cfg(), store, b, backlog=False)
    assert len(b.messages("Receipts/Google Play")) == 5
    assert {k: v[0] for k, v in store.company_folders("a").items()} == {("receipts", "google.com"): "Receipts/Google Play"}


def test_rules_and_corrections_keep_their_exact_folder(store):
    class Taught(ByCompany):
        def classify(self, msg):
            d = super().classify(msg)
            return Decision(d.category, 1.0, "user-rule", "your rule", explicit=True) if d.category != INBOX else d

    b = FakeBackend("a")
    for i in range(4):
        b.add("INBOX", anime(i))
    run(cfg(), store, b, classifier=Taught)
    assert len(b.messages("Promotions")) == 4
    assert not has(b, "Promotions/Crunchyroll")


def test_user_moving_mail_out_of_a_company_folder_is_respected(store):
    b = FakeBackend("a")
    for i in range(3):
        b.add("INBOX", anime(i))
    run(cfg(), store, b)
    b.user_move("Anime pick 0", "Promotions/Crunchyroll", "Promotions")   # wanted it in the plain folder
    run(cfg(), store, b, backlog=False)
    run(cfg(), store, b, backlog=False)
    assert b.subjects("Promotions") == ["Anime pick 0"]


def test_turned_off(store):
    b = FakeBackend("a")
    for i in range(3):
        b.add("INBOX", anime(i))
    run(cfg(company_folders=False), store, b)
    assert len(b.messages("Promotions")) == 3 and not has(b, "Promotions/Crunchyroll")


def test_gmail_nested_labels(store):
    b = FakeBackend("a", gmail=True)
    for i in range(3):
        b.add("INBOX", anime(i))
    run(cfg(), store, b)
    assert len(b.messages("Promotions/Crunchyroll")) == 3 and b.messages("INBOX") == []


# ---- mail TacoSort isn't sure about ------------------------------------------------------------
class Unsure(ByCompany):
    """Knows the kind only roughly: every non-Inbox answer comes back as an unsure guess."""
    sure = 0.6

    def classify(self, msg):
        d = super().classify(msg)
        if d.category == INBOX:
            return d
        return Decision(INBOX, self.sure, "default", "Not sure enough to move it",
                        guess=Decision(d.category, self.sure, "heuristic", "a rough guess"))


def test_unsure_mail_joins_its_company_folder(store):
    b = FakeBackend("a")
    for i in range(3):
        b.add("INBOX", anime(i))
    for i in range(2):
        b.add("INBOX", play(i))
    rep = run(cfg(), store, b, classifier=Unsure)
    assert len(b.messages("Promotions/Crunchyroll")) == 3
    assert len(b.messages("INBOX")) == 2                         # only two from Google Play: they stay
    assert not has(b, "Promotions") or b.messages("Promotions") == []
    assert all("company's own folder" in m.reason for m in rep.moves)


def test_unsure_mail_joins_a_folder_the_company_already_has(store):
    b = FakeBackend("a")
    for i in range(3):
        b.add("INBOX", anime(i))
    run(cfg(), store, b)                                         # sure: makes Promotions/Crunchyroll
    b.add("INBOX", anime(7))
    run(cfg(), store, b, classifier=Unsure, backlog=False)
    assert "Anime pick 7" in b.subjects("Promotions/Crunchyroll")


@pytest.mark.parametrize("kw,sure", [({"unsure_to_company": False}, 0.6), ({}, 0.4)])
def test_unsure_mail_stays_when_turned_off_or_too_unsure(store, kw, sure):
    class VeryUnsure(Unsure):
        pass
    VeryUnsure.sure = sure
    b = FakeBackend("a")
    for i in range(4):
        b.add("INBOX", anime(i))
    run(cfg(**kw), store, b, classifier=VeryUnsure)
    assert len(b.messages("INBOX")) == 4


def test_a_company_s_unsure_mail_stays_together(store):
    class Mixed(ByCompany):
        """Guesses differ for the same kind of mail (as real Gmail tabs do)."""
        def classify(self, msg):
            if company_key(msg.from_.email) != "crunchyroll.com":
                return super().classify(msg)
            cat = "notifications" if msg.subject.endswith(("0", "1")) else "promotions"
            return Decision(INBOX, 0.6, "default", "Not sure", guess=Decision(cat, 0.6, "heuristic", "guess"))

    b = FakeBackend("a")
    for i in range(5):
        b.add("INBOX", anime(i))
    run(cfg(), store, b, classifier=Mixed)
    assert len(b.messages("Promotions/Crunchyroll")) == 5 and not has(b, "Notifications/Crunchyroll")
    b.add("INBOX", anime(10))                                    # a "notifications" guess, later on
    run(cfg(), store, b, classifier=Mixed, backlog=False)
    assert "Anime pick 10" in b.subjects("Promotions/Crunchyroll")
