"""HTML report: content, and that untrusted email text can never inject HTML."""

from __future__ import annotations

from datetime import datetime, timezone

from mailtidy.models import AccountConfig, AppConfig, MoveRecord
from mailtidy.report import build_report, moves_since

EVIL_SUBJECT = '<script>alert("hi")</script> & <img src=x onerror=alert(1)>'
EVIL_SENDER = '"><b>bold</b>@example.com'


def move(subject: str, sender: str, folder: str, account: str = "gmail", undone: bool = False) -> MoveRecord:
    return MoveRecord(run_id="r1", account=account, message_id="<1@example.com>", sender=sender,
                      subject=subject, from_folder="INBOX", to_folder=folder, category="receipts",
                      source="heuristic", reason="Looks like a <receipt>", undone=undone,
                      at=datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc))


class FakeStore:
    def __init__(self) -> None:
        self.moves = [
            move(EVIL_SUBJECT, EVIL_SENDER, "Receipts"),
            move("Weekly digest", "news@example.com", "Newsletters", account="yahoo"),
            move("Old one", "shop@example.com", "Receipts", undone=True),
        ]

    def recent_moves(self, limit=200, *, account=None, include_undone=True):
        return self.moves[:limit]

    def last_run(self, *, dry_run=False):
        return {"run_id": "r1", "started": datetime(2026, 10, 1, 9, 30), "dry_run": False, "moved": 2,
                "scanned": 10}

    def move_counts(self):
        return {("gmail", "Receipts"): 1, ("yahoo", "Newsletters"): 1}

    def learned_rules(self):
        return [{"account": "*", "kind": "address", "key": "<evil>@example.com", "category": "folder",
                 "folder": "Kids <School>", "weight": 3, "updated": datetime(2026, 10, 2)}]

    def corrections(self, limit=100):
        return [{"at": datetime(2026, 10, 3), "account": "gmail", "sender": "x&y@example.com",
                 "from_folder": "Promotions", "to_folder": "INBOX"}]


def config() -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id="gmail", email="alice@example.com"),
                               AccountConfig(id="yahoo", email="alice@yahoo.example.com")])


def test_escapes_everything_from_email() -> None:
    html = build_report(FakeStore(), config())
    assert "<script>" not in html and "<img" not in html and "<b>bold" not in html
    assert "&lt;script&gt;alert(&quot;hi&quot;)&lt;/script&gt; &amp; &lt;img" in html
    assert "&quot;&gt;&lt;b&gt;bold&lt;/b&gt;@example.com" in html
    assert "Looks like a &lt;receipt&gt;" in html
    assert "&lt;evil&gt;@example.com" in html and "Kids &lt;School&gt;" in html
    assert "x&amp;y@example.com" in html


def test_content() -> None:
    html = build_report(FakeStore(), config())
    assert html.startswith("<!doctype html>")
    assert "prefers-color-scheme: dark" in html
    assert "Newsletters" in html and "alice@example.com" in html
    assert "(undone)" in html
    assert "Last run: 2026-10-01 09:30, moved 2." in html


def test_self_contained() -> None:
    html = build_report(FakeStore(), config()).lower()
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html and "<link" not in html
    assert "default-src 'none'" in html


def test_empty_store_and_no_store() -> None:
    for store in (None, object()):
        html = build_report(store, AppConfig())
        assert "hasn&#x27;t run yet" in html or "hasn't run yet" in html
        assert "Nothing learned yet" in html


def test_moves_since_fallback_counts_from_moves() -> None:
    since = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert moves_since(FakeStore(), since) == 2      # the undone one doesn't count
