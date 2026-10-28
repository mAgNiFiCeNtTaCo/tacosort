from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from mailtidy.models import INBOX, MoveRecord, RunReport
from mailtidy.store import ANY_ACCOUNT, SCHEMA_VERSION, Store, StoreError, normalize_list_id

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def move(run_id="r1", account="a", mid="<1@example.com>", to="Receipts", at=NOW, **kw) -> MoveRecord:
    return MoveRecord(run_id=run_id, account=account, message_id=mid, sender="shop@example.com",
                      subject="Your order", from_folder="INBOX", to_folder=to, category="receipts",
                      source="heuristic", reason="receipt words", at=at, **kw)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "data" / "state.sqlite3") as s:
        yield s


def test_creates_folder_schema_and_wal(tmp_path):
    path = tmp_path / "new" / "deeper" / "state.sqlite3"
    s = Store(path)
    assert path.exists() and s.schema_version == SCHEMA_VERSION
    assert s.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    s.close()
    s.close()                                   # safe twice
    with pytest.raises(StoreError):
        s.contacts()


def test_reopen_keeps_data_and_newer_file_is_refused(tmp_path):
    path = tmp_path / "state.sqlite3"
    with Store(path) as s:
        s.add_contacts(["Friend@Example.org"])
    with Store(path) as s:
        assert s.contacts() == {"friend@example.org"}
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = 999")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError, match="newer version"):
        Store(path)


def test_memory_store_works():
    with Store(":memory:") as s:
        s.set_meta("k", "v")
        assert s.get_meta("k") == "v" and s.get_meta("missing", "d") == "d"
        assert s.path is None


def test_lookup_order_address_then_list_then_domain(store):
    L = store.learned
    L.learn_from_folder(ANY_ACCOUNT, "domain", "example.com", category="promotions", weight=0.8, evidence=5)
    L.learn_from_folder(ANY_ACCOUNT, "list_id", "Deals <deals.example.com>", category="newsletters",
                        weight=0.85, evidence=3)
    assert L.lookup("a@example.com", "example.com", "", "acc").category == "promotions"
    assert L.lookup("a@example.com", "example.com", "x <DEALS.example.com>", "acc").category == "newsletters"
    L.learn_from_folder(ANY_ACCOUNT, "address", "A@example.com", category="receipts", weight=0.9, evidence=2)
    d = L.lookup("a@example.com", "example.com", "deals.example.com", "acc")
    assert (d.category, d.source, d.confidence) == ("receipts", "learned", 0.9)
    assert "You keep mail" in d.reason
    assert L.lookup("b@other.example", "other.example", "", "acc") is None


def test_account_rule_beats_everyone_rule(store):
    L = store.learned
    L.learn_from_folder(ANY_ACCOUNT, "address", "x@example.com", category="receipts", weight=0.9, evidence=3)
    L.learn_from_folder("work", "address", "x@example.com", category="folder", folder="Vendors",
                        weight=0.9, evidence=3)
    assert L.lookup("x@example.com", "example.com", "", "work").folder == "Vendors"
    home = L.lookup("x@example.com", "example.com", "", "home")
    assert home.category == "receipts" and home.folder is None


def test_domain_needs_two_pieces_of_evidence_unless_user_said_so(store):
    L = store.learned
    L.learn_from_folder(ANY_ACCOUNT, "domain", "shop.example.com", category="receipts", weight=0.9, evidence=1)
    assert L.lookup("a@shop.example.com", "shop.example.com", "", "acc") is None
    L.learn_correction(ANY_ACCOUNT, "domain", "shop.example.com", category="finance")
    assert L.lookup("a@shop.example.com", "shop.example.com", "", "acc").category == "finance"


def test_folder_learning_never_overwrites_a_correction(store):
    L = store.learned
    L.learn_correction("acc", "address", "x@example.com", category=INBOX)
    assert not L.learn_from_folder("acc", "address", "x@example.com", category="receipts", weight=0.9,
                                   evidence=9)
    d = L.lookup("x@example.com", "example.com", "", "acc")
    assert d.keeps and "back to the Inbox" in d.reason


def test_correction_evidence_adds_up_or_is_replaced(store):
    L = store.learned
    L.learn_correction("acc", "address", "x@example.com", category=INBOX)
    L.learn_correction("acc", "address", "x@example.com", category=INBOX)
    assert L.all()[0].evidence == 2
    L.learn_correction("acc", "address", "x@example.com", category="folder", folder="Bills")
    rule = L.all()[0]
    assert (rule.evidence, rule.folder, rule.source) == (1, "Bills", "correction")
    assert L.count() == 1 and L.count(source="folders") == 0
    assert store.learned_rules()[0]["folder"] == "Bills"
    assert L.forget("acc", "address", "X@example.com") and L.count() == 0


def test_normalize_list_id():
    assert normalize_list_id("Weekly News <Weekly.News.example.com>") == "weekly.news.example.com"
    assert normalize_list_id(" plain.example.com ") == "plain.example.com"
    assert normalize_list_id("") == ""


def test_runs_and_last_run(store):
    assert store.last_run() is None and not store.has_previewed()
    store.start_run("r0", NOW, dry_run=True)
    store.finish_run(RunReport(run_id="r0", dry_run=True, started=NOW, finished=NOW, moved=4))
    store.start_run("r1", NOW + timedelta(hours=1), dry_run=False)
    store.finish_run(RunReport(run_id="r1", dry_run=False, started=NOW + timedelta(hours=1), scanned=9,
                               moved=3, kept=6, errors=["b: login failed"]))
    last = store.last_run()
    assert last["run_id"] == "r1" and last["errors"] == ["b: login failed"] and last["scanned"] == 9
    assert store.last_run(dry_run=True)["moved"] == 4
    assert store.last_run(dry_run=None)["run_id"] == "r1"
    assert store.has_previewed()
    assert [r.run_id for r in store.runs()] == ["r1", "r0"]


def test_moves_queries(store):
    store.record_moves([move(mid="<1@example.com>"), move(mid="<2@example.com>", to="Newsletters"),
                        move(run_id="r2", account="b", mid="<3@example.com>", at=NOW + timedelta(days=1))])
    assert [m.message_id for m in store.recent_moves()] == ["<3@example.com>", "<2@example.com>", "<1@example.com>"]
    assert len(store.recent_moves(account="a")) == 2
    assert store.move_counts() == {("a", "Receipts"): 1, ("a", "Newsletters"): 1, ("b", "Receipts"): 1}
    assert store.moves_since(NOW + timedelta(hours=1)) == 1
    assert store.last_run_with_moves() == "r2"
    ids = [m.id for m in store.moves_for_run("r1")]
    store.mark_undone(ids[:1])
    assert len(store.moves_for_run("r1")) == 1 and len(store.moves_for_run("r1", include_undone=True)) == 2
    assert store.moved_by_us("a", "<2@example.com>") and not store.moved_by_us("a", "<9@example.com>")
    assert not store.moved_by_us("a", "")


def test_moves_to_check_and_corrections(store):
    store.record_moves([move(mid="<1@example.com>"), move(mid="", to="X"),
                        move(mid="<old@example.com>", at=NOW - timedelta(days=40))])
    todo = store.moves_to_check("a", since=NOW - timedelta(days=30))
    assert [m.message_id for m in todo] == ["<1@example.com>"]
    store.mark_move_checked(todo[0].id, "corrected:INBOX")
    assert store.moves_to_check("a", since=NOW - timedelta(days=30)) == []
    c = store.corrections()[0]
    assert (c["from_folder"], c["to_folder"], c["sender"]) == ("Receipts", "INBOX", "shop@example.com")
    assert store.summary()["corrections"] == 1


def test_watermarks(store):
    assert store.get_watermark("a", "INBOX") is None
    store.set_watermark("a", "INBOX", 77, 10)
    store.set_watermark("a", "INBOX", 77, 12)
    assert store.get_watermark("a", "INBOX") == (77, 12)
    store.clear_watermarks("a")
    assert store.get_watermark("a", "INBOX") is None


def test_contacts(store):
    assert store.add_contacts(["A@example.com", "a@example.com", "not-an-address", ""]) == 1
    assert store.add_contacts(["a@example.com", "b@example.com"]) == 1
    assert store.contacts() == {"a@example.com", "b@example.com"}


def test_seen_override_is_sticky(store):
    assert store.seen("a", "") is None
    store.mark_seen("a", "<1@example.com>", "moved:Receipts")
    assert not store.seen("a", "<1@example.com>").override
    store.mark_seen("a", "<1@example.com>", INBOX, override=True)
    store.mark_seen("a", "<1@example.com>", "moved:Receipts")      # later write can't clear the override
    entry = store.seen("a", "<1@example.com>")
    assert entry.override and entry.decision == "moved:Receipts"
    assert store.seen("b", "<1@example.com>") is None


def test_failed_transaction_rolls_back(store):
    with pytest.raises(RuntimeError), store._tx() as c:
        c.execute("INSERT INTO contacts (address, account, added) VALUES ('x@example.com', '', 'now')")
        raise RuntimeError("stop")
    assert store.contacts() == set()
