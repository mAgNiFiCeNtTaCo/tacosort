"""ImapBackend.move() when the connection drops or the server refuses half-way (no network).

`MailServer` keeps real folders that survive a reconnect, so each test can check what is really on
the server afterwards: never a second copy, and every message that did move is reported.
"""

import types

import pytest
from imapclient import exceptions as imap_errors

from tacosort.backend import BackendError
from tacosort.imap.client import ImapBackend
from tacosort.models import AccountConfig


class MailServer:
    """Folders of {uid: {"mid": Message-ID, "flags": set}}. `faults` are (command, what) pairs used
    once, in order: "before" drops the line before the command runs, "after" runs it and then drops
    the line (the reply is lost), "refuse" answers NO."""

    def __init__(self, caps=("IMAP4REV1", "MOVE", "UIDPLUS"), folders=("INBOX", "Receipts")):
        self.caps = {c.upper() for c in caps}
        self.boxes = {f: {} for f in folders}
        self.uidnext = {f: 1 for f in folders}
        self.faults: list[tuple[str, str]] = []
        self.done: list[str] = []          # commands the server really carried out

    def add(self, folder, mid, flags=()):
        uid = self.uidnext[folder]
        self.uidnext[folder] += 1
        self.boxes[folder][uid] = {"mid": mid, "flags": set(flags)}
        return uid

    def put(self, folder, msg):
        uid = self.uidnext[folder]
        self.uidnext[folder] += 1
        self.boxes[folder][uid] = {"mid": msg["mid"], "flags": set()}
        return uid

    def visible(self, folder):
        """Message-IDs a mail app would show (\\Deleted hidden)."""
        return sorted(m["mid"] for m in self.boxes[folder].values() if "\\Deleted" not in m["flags"])

    def count(self, command):
        return self.done.count(command)


class Conn:
    """One connection to MailServer, shaped like IMAPClient."""

    def __init__(self, server):
        self.s = server
        self.dead = False
        self.box = None
        self._imap = types.SimpleNamespace(untagged_responses={})

    def _do(self, name, action):
        if self.dead:
            raise imap_errors.IMAPClientAbortError("socket error: EOF")
        fault = next((f for f in self.s.faults if f[0] == name), None)
        if fault:
            self.s.faults.remove(fault)
            if fault[1] == "before":
                self.dead = True
                raise imap_errors.IMAPClientAbortError("socket error: EOF")
            if fault[1] == "refuse":
                raise imap_errors.IMAPClientError(f"{name.upper()} failed: permission denied")
        result = action()
        self.s.done.append(name)
        if fault and fault[1] == "after":
            self.dead = True
            raise imap_errors.IMAPClientAbortError("socket error: EOF")
        return result

    def login(self, user, password):
        self._do("login", lambda: None)

    def logout(self):
        self.dead = True

    def has_capability(self, cap):
        return cap.upper() in self.s.caps

    def select_folder(self, folder, readonly=False):
        def run():
            self.box = folder
            return {}
        return self._do("select", run)

    def fetch(self, uids, items):
        def run():
            box = self.s.boxes[self.box]
            out = {}
            for u in uids:
                if u in box:
                    m = box[u]
                    header = f"Message-ID: {m['mid']}\r\n\r\n" if m["mid"] else "\r\n"
                    out[u] = {b"FLAGS": tuple(f.encode() for f in sorted(m["flags"])),
                              b"BODY[HEADER.FIELDS (MESSAGE-ID)]": header.encode()}
            return out
        return self._do("fetch", run)

    def search(self, criteria="ALL", charset=None):
        def run():
            box = self.s.boxes[self.box]
            uids = sorted(box)
            if "UNDELETED" in criteria:
                uids = [u for u in uids if "\\Deleted" not in box[u]["flags"]]
            if criteria != "ALL" and criteria[0] == "HEADER":
                uids = [u for u in uids if criteria[2] in box[u]["mid"]]
            return uids
        return self._do("search", run)

    def _transfer(self, uids, dest, remove):
        box = self.s.boxes[self.box]
        old, new = [], []
        for u in uids:
            if u in box:
                msg = box.pop(u) if remove else box[u]
                old.append(u)
                new.append(self.s.put(dest, msg))
        return f"1 {','.join(map(str, old))} {','.join(map(str, new))}".encode()

    def move(self, uids, dest):
        def run():
            self._imap.untagged_responses["COPYUID"] = [self._transfer(uids, dest, remove=True)]
        return self._do("move", run)

    def copy(self, uids, dest):
        return self._do("copy", lambda: b"[COPYUID " + self._transfer(uids, dest, remove=False) + b"] Done")

    def add_flags(self, uids, flags, silent=False):
        def run():
            for u in uids:
                if u in self.s.boxes[self.box]:
                    self.s.boxes[self.box][u]["flags"].add("\\Deleted")
        return self._do("add_flags", run)

    def uid_expunge(self, uids):
        def run():
            box = self.s.boxes[self.box]
            for u in uids:
                if u in box and "\\Deleted" in box[u]["flags"]:
                    del box[u]
        return self._do("uid_expunge", run)


def connect(server):
    logs = []
    acct = AccountConfig(id="acct", email="me@example.com", host="imap.example.com")
    b = ImapBackend(acct, secret_getter=lambda _id: "pw", client_factory=lambda host, **kw: Conn(server),
                    sleep=lambda s: None, log=logs.append)
    b.connect()
    return b, logs


def inbox_with(server, *mids):
    return [server.add("INBOX", mid) for mid in mids]


MIDS = ("<a@example.com>", "<b@example.com>")
NO_MOVE = ("IMAP4REV1", "UIDPLUS")


# ---- MOVE ---------------------------------------------------------------------------------------

def test_move_done_then_drop_is_still_reported():
    s = MailServer()
    uids = inbox_with(s, *MIDS)
    s.faults = [("move", "after")]           # the server moved them; the OK never arrived
    b, _ = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("move") == 1
    assert s.visible("INBOX") == [] and s.visible("Receipts") == list(MIDS)


def test_drop_before_move_is_tried_once_more():
    s = MailServer()
    uids = inbox_with(s, *MIDS)
    s.faults = [("move", "before")]          # e.g. the connection went stale while waiting
    b, _ = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("move") == 1
    assert s.visible("Receipts") == list(MIDS)


def test_move_drops_twice_gives_up_quietly():
    s = MailServer()
    uids = inbox_with(s, *MIDS)
    s.faults = [("move", "before"), ("move", "before")]
    b, logs = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {}
    assert s.visible("INBOX") == list(MIDS) and s.visible("Receipts") == []
    assert any("dropped twice" in line for line in logs)


# ---- COPY fallback (no MOVE) --------------------------------------------------------------------

def test_drop_after_copy_does_not_copy_twice():
    s = MailServer(caps=NO_MOVE)
    uids = inbox_with(s, *MIDS)
    s.faults = [("add_flags", "before")]     # COPY worked, the line dropped before \Deleted
    b, _ = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("copy") == 1
    assert s.visible("INBOX") == [] and s.visible("Receipts") == list(MIDS)
    assert s.boxes["INBOX"] == {}            # the delete step was finished after reconnecting


def test_copy_done_but_reply_lost_only_finishes_the_delete_step():
    s = MailServer(caps=NO_MOVE)
    uids = inbox_with(s, *MIDS)
    s.faults = [("copy", "after")]
    b, _ = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}   # new uids found by Message-ID
    assert s.count("copy") == 1
    assert s.visible("INBOX") == [] and s.visible("Receipts") == list(MIDS)


def test_copy_that_never_happened_is_tried_once_more():
    s = MailServer(caps=NO_MOVE)
    uids = inbox_with(s, *MIDS)
    s.faults = [("copy", "before")]
    b, _ = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("copy") == 1
    assert s.visible("Receipts") == list(MIDS)


def test_unclear_copy_of_message_without_message_id_is_never_repeated():
    s = MailServer(caps=NO_MOVE)
    uids = inbox_with(s, "", "")
    s.faults = [("copy", "after")]
    b, _ = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {}            # can't be checked: outcome unknown
    assert s.count("copy") == 1


def test_delete_step_refused_still_reports_the_move_and_says_so():
    s = MailServer(caps=NO_MOVE)
    uids = inbox_with(s, *MIDS)
    s.faults = [("add_flags", "refuse")]     # e.g. a shared mailbox without the delete right
    b, logs = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("copy") == 1
    assert s.visible("Receipts") == list(MIDS)
    assert any("could not remove the originals" in line for line in logs)


def test_drop_then_refusal_after_reconnect_still_reports_the_move():
    s = MailServer(caps=NO_MOVE)
    uids = inbox_with(s, *MIDS)
    s.faults = [("add_flags", "before"), ("add_flags", "refuse")]
    b, logs = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("copy") == 1
    assert any("could not remove the originals" in line for line in logs)


def test_unclear_copy_then_refused_delete_still_reports_the_move():
    s = MailServer(caps=NO_MOVE)
    uids = inbox_with(s, *MIDS)
    s.faults = [("copy", "after"), ("add_flags", "refuse")]
    b, logs = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("copy") == 1
    assert any("could not remove the originals" in line for line in logs)


def test_without_uidplus_drop_after_copy_flags_originals_and_never_expunges():
    s = MailServer(caps=("IMAP4REV1",))
    uids = inbox_with(s, *MIDS)
    s.faults = [("copy", "after")]
    b, _ = connect(s)
    assert b.move("INBOX", uids, "Receipts") == {1: 1, 2: 2}
    assert s.count("copy") == 1 and s.count("uid_expunge") == 0
    assert s.visible("INBOX") == [] and len(s.boxes["INBOX"]) == 2      # left flagged \Deleted


# ---- other ------------------------------------------------------------------------------------

def test_original_already_flagged_deleted_is_not_moved_again():
    s = MailServer(caps=("IMAP4REV1",))
    s.add("INBOX", MIDS[0], flags=("\\Deleted",))
    b, _ = connect(s)
    assert b.move("INBOX", [1], "Receipts") == {}
    assert s.count("copy") == 0 and s.count("move") == 0


def test_moves_of_earlier_batches_are_kept_when_a_later_batch_fails():
    s = MailServer()
    uids = inbox_with(s, *(f"<m{i}@example.com>" for i in range(201)))
    s.faults = [("move", "ok"), ("move", "refuse")]          # first batch fine, second refused
    b, logs = connect(s)
    result = b.move("INBOX", uids, "Receipts")
    assert len(result) == 200 and set(result) == set(uids[:200])
    assert any("then stopped" in line for line in logs)


def test_refused_move_with_nothing_moved_is_an_error():
    s = MailServer()
    uids = inbox_with(s, *MIDS)
    s.faults = [("move", "refuse")]
    b, _ = connect(s)
    with pytest.raises(BackendError, match="said no"):
        b.move("INBOX", uids, "Receipts")
