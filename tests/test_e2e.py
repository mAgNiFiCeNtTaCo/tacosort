"""End to end: the REAL classifier, engine, store and CLI against two in-memory mailboxes.

A Gmail-like and a Yahoo-like account, each with a realistic Inbox (receipts, bank mail, trips,
newsletters, sales, social alerts, app notices, friends, replies, security codes, a starred sale),
a Sent folder that tells MailTidy who the user's people are, and (Gmail) the user's own "Kids School"
folder. Everything goes through `mailtidy.cli.main`, exactly as a person would type it. Offline:
the settings file and memory folder live in pytest's tmp_path, and no keychain or network is used.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest

from mailtidy import cli
from mailtidy.config import save_config
from mailtidy.models import AccountConfig, AppConfig
from mailtidy.store import Store
from mailtidy.testing import FakeBackend, make_message

ME_GMAIL = "sam.rivera@example.com"
ME_YAHOO = "sam_rivera@example.net"
CATEGORY_FOLDERS = {"Receipts", "Finance", "Travel", "Newsletters", "Promotions", "Social", "Notifications"}

UNSUB = {"List-Unsubscribe": "<https://example.com/unsubscribe>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
FLAGGED = frozenset({"\\Flagged"})

# (sender, display name, subject, extra headers, flags, where it should end up)
INBOX_MAIL = [
    # receipts
    ("shipment-tracking@amazon.com", "Amazon.com", "Your Amazon.com order #112-4456781-2234 has shipped", {}, (), "Receipts"),
    ("no_reply@email.apple.com", "Apple", "Your receipt from Apple.", {}, (), "Receipts"),
    ("transaction@etsy.com", "Etsy", "Your Etsy Purchase from WoodcraftShop (2911044)", {}, (), "Receipts"),
    ("mcinfo@ups.com", "UPS", "UPS Update: Package Scheduled for Delivery Today", {}, (), "Receipts"),
    ("orders@target.com", "Target", "Thanks for your order! Order #9021334", {}, (), "Receipts"),
    # finance
    ("no.reply.alerts@chase.com", "Chase", "Your credit card statement is ready", {}, (), "Finance"),
    ("service@paypal.com", "PayPal", "Your monthly account statement is available", {}, (), "Finance"),
    ("alerts@notify.wellsfargo.com", "Wells Fargo", "Your bill payment is scheduled", {}, (), "Finance"),
    ("noreply@vanguard.com", "Vanguard", "Your quarterly investment statement", {}, (), "Finance"),
    # travel
    ("deltaairlines@t.delta.com", "Delta Air Lines", "Your trip confirmation: ATL to DEN, Dec 12", {}, (), "Travel"),
    ("noreply@booking.com", "Booking.com", "Your booking is confirmed at Hotel Maple", {}, (), "Travel"),
    ("automated@airbnb.com", "Airbnb", "Reservation confirmed - your stay in Denver", {}, (), "Travel"),
    ("hertz@emails.hertz.com", "Hertz", "Your Hertz car rental reservation", {}, (), "Travel"),
    # newsletters
    ("weeklybyte@substack.com", "The Weekly Byte", "The Weekly Byte #42: chips and dips",
     {**UNSUB, "List-Id": "The Weekly Byte <weeklybyte.substack.com>"}, (), "Newsletters"),
    ("crew@morningbrew.com", "Morning Brew", "Markets rally on Tuesday",
     {**UNSUB, "List-Id": "<daily.morningbrew.com>"}, (), "Newsletters"),
    ("nytdirect@nytimes.com", "The New York Times", "The Morning: Your Tuesday briefing", UNSUB, (), "Newsletters"),
    # promotions
    ("deals@oldnavy.com", "Old Navy", "40% off everything this weekend only!", {**UNSUB, "Precedence": "bulk"}, (),
     "Promotions"),
    ("bestbuyinfo@emailinfo.bestbuy.com", "Best Buy", "Last chance: doorbuster deals end tonight", UNSUB, (),
     "Promotions"),
    ("hello@glossier.com", "Glossier", "Use code SAVE20 for 20% off this week", UNSUB, (), "Promotions"),
    # social
    ("notification@facebookmail.com", "Facebook", "Jamie Lee commented on your post", {}, (), "Social"),
    ("invitations@linkedin.com", "LinkedIn", "You have 3 new invitations", {}, (), "Social"),
    ("noreply@redditmail.com", "Reddit", "u/hiker42 replied to your comment", {}, (), "Social"),
    # notifications
    ("notifications@github.com", "GitHub", "[sam/garden] Pull request #12 merged", {"X-GitHub-Reason": "author"}, (),
     "Notifications"),
    ("no-reply@dropbox.com", "Dropbox", "Your Dropbox is almost full", {}, (), "Notifications"),
    ("no-reply@zoom.us", "Zoom", "Your cloud recording is now available", {}, (), "Notifications"),
    # stays in the Inbox: people, contacts, replies, security, starred
    ("jordan.park@example.org", "Jordan Park", "Dinner on Saturday?", {}, (), "INBOX"),
    ("rose.rivera@example.org", "Rose Rivera", "Photos from the weekend", {}, (), "INBOX"),
    ("pat.builder@example.org", "Pat Builder", "Re: Kitchen quote",
     {"In-Reply-To": "<quote-1@example.org>", "References": "<quote-1@example.org>"}, (), "INBOX"),
    ("maria.lopez@example.org", "Maria Lopez", "Question about the bike you listed", {}, (), "INBOX"),
    ("no-reply@accounts.google.com", "Google", "Security alert: new sign-in on Windows", {}, (), "INBOX"),
    ("account-security-noreply@accountprotection.microsoft.com", "Microsoft account",
     "Your single-use code is 482913", {}, (), "INBOX"),
    ("no-reply@spotify.com", "Spotify", "Reset your password", {}, (), "INBOX"),
    ("deals@oldnavy.com", "Old Navy", "Flash sale: 60% off jeans (starred)", {**UNSUB, "Precedence": "bulk"}, FLAGGED,
     "INBOX"),
]

SCHOOL = "lincoln-elementary.example.org"
SCHOOL_INBOX = [   # Gmail only: the user already files this school's mail in "Kids School"
    (f"office@{SCHOOL}", "Lincoln Elementary Office", "Picture day is Friday", {}, (), "Kids School"),
    (f"ms.chen@{SCHOOL}", "Ms. Chen", "Field trip permission slip", {}, (), "Kids School"),
]
SCHOOL_FILED = [
    (f"office@{SCHOOL}", "Lincoln Elementary Office", "Half day on Wednesday"),
    (f"office@{SCHOOL}", "Lincoln Elementary Office", "Book fair next week"),
    (f"office@{SCHOOL}", "Lincoln Elementary Office", "Lunch menu for October"),
    (f"ms.chen@{SCHOOL}", "Ms. Chen", "Spelling words for this week"),
    (f"ms.chen@{SCHOOL}", "Ms. Chen", "Class newsletter: pumpkins!"),
]
SENT_TO = ["jordan.park@example.org", "rose.rivera@example.org", "coach.dave@example.org"]


class GmailLike(FakeBackend):
    """Gmail's folder layout: [Gmail]/Sent Mail, [Gmail]/All Mail, [Gmail]/Trash, [Gmail]/Spam."""

    def __init__(self, account_id: str):
        super().__init__(account_id)
        for name in ("Sent", "Trash", "Spam"):
            for table in (self._folders, self._flags, self._uidnext, self._uidvalidity):
                del table[name]
        self._create("[Gmail]", frozenset({"\\Noselect", "\\HasChildren"}))
        for name, flag in (("[Gmail]/Sent Mail", "\\Sent"), ("[Gmail]/All Mail", "\\All"),
                           ("[Gmail]/Trash", "\\Trash"), ("[Gmail]/Spam", "\\Junk"), ("[Gmail]/Drafts", "\\Drafts")):
            self._create(name, frozenset({flag}))


class YahooLike(FakeBackend):
    """Yahoo's layout: Sent, Draft, Trash and Bulk (its spam folder)."""

    def __init__(self, account_id: str):
        super().__init__(account_id)
        for table in (self._folders, self._flags, self._uidnext, self._uidvalidity):
            del table["Spam"]
        self._create("Bulk", frozenset({"\\Junk"}))
        self._create("Draft", frozenset({"\\Drafts"}))


_ids = Counter()


def mail(acct: str, me: str, sender: str, name: str, subject: str, headers=None, flags=()):
    _ids[acct] += 1
    return make_message(sender, subject, name=name, to=me, message_id=f"<{acct}-{_ids[acct]}@mail.example.com>",
                        headers=headers or {}, flags=frozenset(flags))


def where(backend: FakeBackend, subject: str) -> list[str]:
    return [f for f in backend._folders if subject in backend.subjects(f)]


def snapshot(backend: FakeBackend) -> dict[str, list[str]]:
    return {f: sorted(backend.subjects(f)) for f in backend._folders}


def moves_made(backend: FakeBackend) -> int:
    return sum(1 for c in backend.calls if c[0] in ("move", "ensure_folder"))


@pytest.fixture
def world(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config" / "config.yaml"
    data = tmp_path / "data"
    monkeypatch.setenv("MAILTIDY_CONFIG", str(cfg_path))
    monkeypatch.setenv("MAILTIDY_DATA", str(data))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    save_config(AppConfig(accounts=[
        AccountConfig(id="gmail", email=ME_GMAIL, provider="gmail"),
        AccountConfig(id="yahoo", email=ME_YAHOO, provider="yahoo"),
    ]), cfg_path)

    gmail, yahoo = GmailLike("gmail"), YahooLike("yahoo")
    for backend, me, extra in ((gmail, ME_GMAIL, SCHOOL_INBOX), (yahoo, ME_YAHOO, [])):
        for sender, name, subject, headers, flags, _ in INBOX_MAIL + extra:
            backend.add("INBOX", mail(backend.account_id, me, sender, name, subject, headers, flags))
        sent = "[Gmail]/Sent Mail" if backend is gmail else "Sent"
        for to in SENT_TO:
            backend.add(sent, mail(backend.account_id, to, me, "Sam Rivera", f"Hi from Sam to {to}"))
    for sender, name, subject in SCHOOL_FILED:
        gmail.add("Kids School", mail("gmail", ME_GMAIL, sender, name, subject))

    fakes = {"gmail": gmail, "yahoo": yahoo}
    monkeypatch.setattr(cli, "make_backend", lambda account: fakes[account.id])

    class NoSchedule:   # never touch the real Task Scheduler / launchd / systemd
        @staticmethod
        def status() -> str:
            return "off"

    monkeypatch.setattr(cli, "_schedule", lambda: NoSchedule)

    def store() -> Store:
        return Store(data / "state.sqlite3")

    return dataclasses.make_dataclass("World", ["gmail", "yahoo", "data", "tmp", "store"])(
        gmail, yahoo, data, tmp_path, store)


def mailtidy(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def check_sorted(backend: FakeBackend, expected: list[tuple]) -> list[str]:
    """Every message is in exactly the folder it should be in; returns a readable list of misses."""
    misses = []
    for sender, _, subject, *_, folder in expected:
        found = where(backend, subject)
        if found != [folder]:
            misses.append(f"{backend.account_id}: {subject!r} from {sender} is in {found}, expected {folder}")
    return misses


def test_full_journey(world, capsys):
    gmail, yahoo = world.gmail, world.yahoo
    expected_gmail, expected_yahoo = INBOX_MAIL + SCHOOL_INBOX, INBOX_MAIL

    # 1. Before any preview, `run` refuses to move anything.
    code, out = mailtidy(capsys, "run")
    assert code == 1 and "preview" in out and moves_made(gmail) == moves_made(yahoo) == 0

    # 2. learn: the user's Kids School folder and the people in Sent.
    code, out = mailtidy(capsys, "learn")
    assert code == 0, out
    assert "Learned" in out
    with world.store() as st:
        assert set(SENT_TO) <= st.contacts()
        assert st.learned.lookup(f"office@{SCHOOL}", SCHOOL, "", "gmail").folder == "Kids School"

    # 3. preview shows the plan and changes nothing.
    before = {n: snapshot(b) for n, b in (("gmail", gmail), ("yahoo", yahoo))}
    code, out = mailtidy(capsys, "preview", "--limit", "200")
    assert code == 0, out
    assert "would be moved" in out and "Kids School" in out and "Receipts" in out
    assert {n: snapshot(b) for n, b in (("gmail", gmail), ("yahoo", yahoo))} == before
    assert moves_made(gmail) == moves_made(yahoo) == 0

    # 4. A plain `run` sorts only mail that arrives from now on; --backlog sorts what is already there.
    code, out = mailtidy(capsys, "run")
    assert code == 0, out
    assert "--backlog" in out and moves_made(gmail) == moves_made(yahoo) == 0
    code, out = mailtidy(capsys, "run", "--backlog", "--yes")
    assert code == 0, out
    misses = check_sorted(gmail, expected_gmail) + check_sorted(yahoo, expected_yahoo)
    assert not misses, "\n".join(misses)

    # Same folder scheme on both accounts, and no message lost or duplicated.
    def made(b: FakeBackend) -> set[str]:
        return {f for f in b._folders if b.messages(f) and f in CATEGORY_FOLDERS}

    assert made(gmail) == made(yahoo) == CATEGORY_FOLDERS
    for b, n in ((gmail, len(expected_gmail) + len(SCHOOL_FILED) + len(SENT_TO)),
                 (yahoo, len(expected_yahoo) + len(SENT_TO))):
        assert sum(len(b.messages(f)) for f in b._folders) == n
    # Special folders are never touched.
    for b, special in ((gmail, ["[Gmail]/All Mail", "[Gmail]/Trash", "[Gmail]/Spam", "[Gmail]/Drafts"]),
                       (yahoo, ["Trash", "Bulk", "Draft"])):
        assert all(not b.messages(f) for f in special)
        assert not any(c[0] == "move" and (c[1] in special or c[3] in special) for c in b.calls)

    # 5. A second run (plain or backlog) changes nothing.
    after = {n: snapshot(b) for n, b in (("gmail", gmail), ("yahoo", yahoo))}
    for argv in (["run"], ["run", "--backlog"]):
        code, out = mailtidy(capsys, *argv)
        assert code == 0, out
        assert {n: snapshot(b) for n, b in (("gmail", gmail), ("yahoo", yahoo))} == after, argv

    # 6. New mail is sorted.
    new = [
        ("shipment-tracking@amazon.com", "Amazon.com", "Your Amazon.com order #112-0000001-9999 has shipped", {}, (),
         "Receipts"),
        ("weeklybyte@substack.com", "The Weekly Byte", "The Weekly Byte #43: more chips",
         {**UNSUB, "List-Id": "The Weekly Byte <weeklybyte.substack.com>"}, (), "Newsletters"),
        ("jordan.park@example.org", "Jordan Park", "Running late, see you at 7", {}, (), "INBOX"),
    ]
    new_school = [(f"ms.chen@{SCHOOL}", "Ms. Chen", "Science fair projects", {}, (), "Kids School")]
    for b, me, extra in ((gmail, ME_GMAIL, new_school), (yahoo, ME_YAHOO, [])):
        for sender, name, subject, headers, flags, _ in new + extra:
            b.add("INBOX", mail(b.account_id, me, sender, name, subject, headers, flags))
    code, out = mailtidy(capsys, "run")
    assert code == 0, out
    misses = check_sorted(gmail, new + new_school) + check_sorted(yahoo, new)
    assert not misses, "\n".join(misses)

    # 7. The user corrects MailTidy in their mail app: one sale back to the Inbox, one newsletter
    #    into a folder of their own. Both stick, and future mail from those senders follows.
    gmail.user_move("Last chance: doorbuster deals end tonight", "Promotions", "INBOX")
    gmail.user_move("The Morning: Your Tuesday briefing", "Newsletters", "Reading")
    code, out = mailtidy(capsys, "run")
    assert code == 0, out
    assert "learned 2 corrections" in out
    assert where(gmail, "Last chance: doorbuster deals end tonight") == ["INBOX"]
    assert where(gmail, "The Morning: Your Tuesday briefing") == ["Reading"]
    later = [
        ("bestbuyinfo@emailinfo.bestbuy.com", "Best Buy", "Weekend sale: TVs from $199", UNSUB, (), "INBOX"),
        ("nytdirect@nytimes.com", "The New York Times", "The Morning: Your Friday briefing", UNSUB, (), "Reading"),
    ]
    for sender, name, subject, headers, flags, _ in later:
        gmail.add("INBOX", mail("gmail", ME_GMAIL, sender, name, subject, headers, flags))
    # "Keep this sender in my Inbox" applies to every account; a personal folder only to its own account.
    yahoo.add("INBOX", mail("yahoo", ME_YAHOO, *later[0][:4]))
    for _ in range(2):
        code, out = mailtidy(capsys, "run")
        assert code == 0, out
        misses = check_sorted(gmail, later) + check_sorted(yahoo, later[:1])
        assert not misses, "\n".join(misses)
        assert where(gmail, "Last chance: doorbuster deals end tonight") == ["INBOX"]
    assert "Reading" not in yahoo._folders

    # 8. undo puts the last run's moves back, and they are never moved again.
    oops = [
        ("orders@target.com", "Target", "Thanks for your order! Order #9021999", {}, (), "Receipts"),
        ("no-reply@dropbox.com", "Dropbox", "Someone joined your shared folder", {}, (), "Notifications"),
    ]
    for sender, name, subject, headers, flags, _ in oops:
        yahoo.add("INBOX", mail("yahoo", ME_YAHOO, sender, name, subject, headers, flags))
    code, out = mailtidy(capsys, "run")
    assert code == 0 and not check_sorted(yahoo, oops), out
    code, out = mailtidy(capsys, "undo")
    assert code == 0, out
    assert out.count("Put 2 message(s) back") == 1
    assert all(where(yahoo, s[2]) == ["INBOX"] for s in oops)
    for argv in (["run"], ["run", "--backlog"]):
        assert mailtidy(capsys, *argv)[0] == 0
        assert all(where(yahoo, s[2]) == ["INBOX"] for s in oops), argv

    # 9. report, status and rules list work on the real memory file.
    report_path = world.tmp / "report.html"
    code, out = mailtidy(capsys, "report", "--output", str(report_path))
    assert code == 0, out
    html = report_path.read_text(encoding="utf-8")
    assert "Receipts" in html and "amazon.com" in html and "Kids School" in html
    assert "<script src" not in html
    code, out = mailtidy(capsys, "status")
    assert code == 0 and "gmail" in out and "yahoo" in out and "not previewed" not in out, out
    code, out = mailtidy(capsys, "rules", "list")
    assert code == 0 and "Kids School" in out, out

    # 10. `undo --run` with an id read from the report: the big backlog run is put back.
    runs = re.findall(r'<code>(\d{8}-\d{6}-[0-9a-f]{4})</code></td><td class="num">(\d+)</td>', html)
    assert len(runs) == 4          # backlog, new mail, the Reading move, the undone run
    big_run = max(runs, key=lambda r: int(r[1]))[0]
    code, out = mailtidy(capsys, "undo", "--run", big_run)
    assert code == 0 and "won't move them again" in out, out
    for b in (gmail, yahoo):
        assert where(b, "Your receipt from Apple.") == ["INBOX"]
        assert where(b, "The Weekly Byte #42: chips and dips") == ["INBOX"]
    assert where(gmail, "Picture day is Friday") == ["INBOX"]
    assert where(gmail, "The Morning: Your Tuesday briefing") == ["Reading"]    # the user's own move stays


def test_background_schedule_command_line_works(world, capsys, monkeypatch):
    """The scheduled job runs `python -m mailtidy run --quiet --config PATH`: that exact line must work,
    stay silent when all is well, and sort new mail."""
    from mailtidy import schedule
    from mailtidy.config import default_config_path

    other = world.tmp / "elsewhere" / "my settings.yaml"
    other.parent.mkdir()
    other.write_text(default_config_path().read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("MAILTIDY_CONFIG", str(world.tmp / "not-this-one.yaml"))
    args = schedule.run_arguments(other)
    assert args[:3] == ["-m", "mailtidy", "run"]

    assert mailtidy(capsys, "preview", "--config", str(other))[0] == 0
    code, out = mailtidy(capsys, *args[2:])
    assert (code, out) == (0, "")
    assert where(world.yahoo, "Your receipt from Apple.") == ["INBOX"]       # old mail: only with --backlog
    world.yahoo.add("INBOX", mail("yahoo", ME_YAHOO, "no_reply@email.apple.com", "Apple", "Your receipt from Apple (2)"))
    code, out = mailtidy(capsys, *args[2:])
    assert (code, out) == (0, "")
    assert where(world.yahoo, "Your receipt from Apple (2)") == ["Receipts"]


def test_rules_test_uses_what_was_learned(world, capsys):
    assert mailtidy(capsys, "learn")[0] == 0
    code, out = mailtidy(capsys, "rules", "test", "--from", f"office@{SCHOOL}", "--subject", "Snow day",
                         "--account", "gmail")
    assert code == 0 and "Kids School" in out, out
    code, out = mailtidy(capsys, "rules", "test", "--from", "jordan.park@example.org", "--subject", "Hi")
    assert code == 0 and "stays in the Inbox" in out, out


def test_user_rule_wins_and_folder_prefix_applies_to_both_accounts(world, capsys, tmp_path):
    from mailtidy.config import default_config_path, load_config
    from mailtidy.models import Rule

    path = default_config_path()
    cfg = load_config(path)
    cfg.folder_prefix = "MailTidy"
    cfg.rules = [Rule(name="github stays", from_=["*@github.com"], action="inbox")]
    save_config(cfg, path)
    assert mailtidy(capsys, "preview")[0] == 0
    code, out = mailtidy(capsys, "run", "--backlog")
    assert code == 0, out
    for b in (world.gmail, world.yahoo):
        assert where(b, "[sam/garden] Pull request #12 merged") == ["INBOX"]
        assert where(b, "Your receipt from Apple.") == ["MailTidy/Receipts"]
        assert "Receipts" not in b._folders
