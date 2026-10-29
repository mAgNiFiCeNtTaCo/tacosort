"""ImapBackend against a fake IMAPClient (no network)."""

import ssl
import types
from datetime import datetime, timezone

import pytest
from imapclient import exceptions as imap_errors

from tacosort.backend import BackendError
from tacosort.imap import client as client_module
from tacosort.imap.client import (
    ImapBackend,
    decode_header_value,
    make_snippet,
    parse_addresses,
    parse_copyuid,
)
from tacosort.models import AccountConfig

SECRET = "sup3r-s3cret-app-pass"


def header_block(**fields) -> bytes:
    lines = []
    for k, v in fields.items():
        name = k.replace("_", "-")
        lines.append(name.encode() + b": " + (v if isinstance(v, bytes) else v.encode()))
    return b"\r\n".join(lines) + b"\r\n\r\n"


class FakeIMAP:
    """Just enough of IMAPClient. Records every call in `calls`."""

    def __init__(self, caps=("IMAP4REV1", "MOVE", "UIDPLUS", "NAMESPACE", "IDLE"), *, delimiter="/",
                 prefix="", folders=None, messages=None, login_error=None, tabs=None):
        self.caps = {c.upper() for c in caps}
        self.delimiter = delimiter
        self.prefix = prefix
        self.folders = dict(folders or {"INBOX": (b"\\HasNoChildren",)})
        self.messages = dict(messages or {})      # uid -> fetch row
        self.tabs = dict(tabs or {})              # Gmail tab ("promotions") -> uids, for X-GM-RAW search
        self.login_error = login_error
        self.calls = []
        self.fail_next = []                       # exceptions to raise on the next calls
        self.idle_responses = []
        self.status = {b"UIDVALIDITY": 7, b"UIDNEXT": 50, b"MESSAGES": 3}
        self._imap = types.SimpleNamespace(untagged_responses={})
        self.copy_response = b"[COPYUID 77 10:11 500:501] Done"
        self.move_copyuid = b"77 10:11 900:901"

    def _rec(self, *call):
        self.calls.append(call)
        if self.fail_next:
            exc = self.fail_next.pop(0)
            if exc is not None:
                raise exc

    def names(self):
        return [c[0] for c in self.calls]

    # connection
    def starttls(self, ctx=None):
        self.starttls_context = ctx
        self._rec("starttls")

    def login(self, user, password):
        self._rec("login", user, password)
        if self.login_error:
            raise imap_errors.LoginError(self.login_error)

    def oauth2_login(self, user, token):
        self._rec("oauth2_login", user, token)

    def logout(self):
        self.calls.append(("logout",))

    def has_capability(self, cap):
        return cap.upper() in self.caps

    def noop(self):
        self._rec("noop")

    # folders
    def list_folders(self, directory="", pattern="*"):
        self._rec("list_folders", directory, pattern)
        if pattern == "":
            return [((b"\\Noselect",), self.delimiter.encode(), "")]
        return [(flags, self.delimiter.encode(), name) for name, flags in self.folders.items()]

    def namespace(self):
        self._rec("namespace")
        return types.SimpleNamespace(personal=((self.prefix, self.delimiter),))

    def create_folder(self, name):
        self._rec("create_folder", name)
        self.folders[name] = (b"\\HasNoChildren",)

    def subscribe_folder(self, name):
        self._rec("subscribe_folder", name)

    def folder_exists(self, name):
        return name in self.folders

    def folder_status(self, folder, what=None):
        self._rec("folder_status", folder, tuple(what or ()))
        return dict(self.status)

    def select_folder(self, folder, readonly=False):
        self._rec("select_folder", folder, readonly)
        return {}

    # messages
    def search(self, criteria="ALL", charset=None):
        self._rec("search", criteria, charset)
        uids = sorted(self.messages)
        if "UNDELETED" in criteria:
            uids = [u for u in uids if b"\\Deleted" not in self.messages[u][b"FLAGS"]]
        if criteria in ("ALL", ["UNDELETED"]):
            return uids
        if "X-GM-RAW" in criteria:
            if "X-GM-EXT-1" not in self.caps:
                raise imap_errors.IMAPClientError("SEARCH command error: BAD [b'Could not parse command']")
            tab = criteria[criteria.index("X-GM-RAW") + 1].removeprefix("category:")
            in_tab = set(self.tabs.get(tab, ()))
            criteria = [c for c in criteria if c not in ("X-GM-RAW", f"category:{tab}")]
            return [u for u in self.search(criteria, charset) if u in in_tab]
        if criteria[0] == "UID":
            spec = criteria[1]
            if spec.endswith(":*"):
                low = int(spec[:-2])
                hits = [u for u in uids if u >= low]
                return hits or uids[-1:]          # like a real server: N:* always has the top UID
            wanted = {int(x) for x in spec.split(",")}
            return [u for u in uids if u in wanted]
        if criteria[0] == "HEADER":
            needle = criteria[2]
            return [u for u in uids if needle.encode() in _header_bytes(self.messages[u])]
        raise AssertionError(criteria)

    def fetch(self, uids, items):
        self._rec("fetch", tuple(uids), tuple(items))
        return {u: self.messages[u] for u in uids if u in self.messages}

    def move(self, uids, dest):
        self._rec("move", tuple(uids), dest)
        self._imap.untagged_responses["COPYUID"] = [self.move_copyuid]

    def copy(self, uids, dest):
        self._rec("copy", tuple(uids), dest)
        return self.copy_response

    def add_flags(self, uids, flags, silent=False):
        self._rec("add_flags", tuple(uids), tuple(flags))

    def uid_expunge(self, uids):
        self._rec("uid_expunge", tuple(uids))

    def expunge(self, messages=None):
        raise AssertionError("a whole-folder EXPUNGE must never happen")

    # idle
    def idle(self):
        self._rec("idle")

    def idle_check(self, timeout=None):
        self._rec("idle_check", timeout)
        return self.idle_responses

    def idle_done(self):
        self.calls.append(("idle_done",))


def _header_bytes(row):
    for k, v in row.items():
        if k.startswith(b"BODY[HEADER"):
            return v
    return b""


def row(headers: bytes, *, flags=(b"\\Seen",), size=1234, labels=None, text=None):
    r = {
        b"SEQ": 1,
        b"FLAGS": flags,
        b"RFC822.SIZE": size,
        b"INTERNALDATE": datetime(2026, 3, 1, 12, 0),
        b"BODY[HEADER.FIELDS (FROM TO CC SUBJECT)]": headers,
    }
    if labels is not None:
        r[b"X-GM-LABELS"] = labels
    if text is not None:
        r[b"BODY[TEXT]<0>"] = text
    return r


def account(**kw):
    base = {"id": "acct", "email": "me@example.com", "provider": "custom", "host": "imap.example.com"}
    base.update(kw)
    return AccountConfig(**base)


def backend(fake, acct=None, **kw):
    made = []

    def factory(host, **opts):
        made.append((host, opts))
        return fake if not isinstance(fake, list) else fake[len(made) - 1]

    b = ImapBackend(acct or account(), secret_getter=lambda _id: SECRET, client_factory=factory,
                    sleep=lambda s: None, **kw)
    b.made = made
    return b


# ---- connect / login --------------------------------------------------------------------------

def test_connect_ssl_login():
    fake = FakeIMAP()
    b = backend(fake, account(username="login-name"))
    b.connect()
    host, opts = b.made[0]
    assert host == "imap.example.com" and opts["port"] == 993 and opts["ssl"] is True
    assert ("login", "login-name", SECRET) in fake.calls
    assert "starttls" not in fake.names()
    b.close()
    b.close()  # twice is fine
    assert fake.names().count("logout") == 1


def test_connect_uses_provider_defaults_and_starttls():
    fake = FakeIMAP()
    b = backend(fake, account(provider="protonmail-bridge", host=""))
    b.connect()
    host, opts = b.made[0]
    assert (host, opts["port"], opts["ssl"]) == ("127.0.0.1", 1143, False)
    assert fake.names()[:2] == ["starttls", "login"]
    assert ("login", "me@example.com", SECRET) in fake.calls


def test_no_starttls_means_no_password_sent():
    fake = FakeIMAP()
    fake.fail_next = [imap_errors.IMAPClientError("STARTTLS not supported")]
    b = backend(fake, account(ssl=False, port=143))
    with pytest.raises(BackendError, match="did not send your password"):
        b.connect()
    assert "login" not in fake.names()


def test_no_host():
    b = backend(FakeIMAP(), account(host=""))
    with pytest.raises(BackendError, match="no IMAP server"):
        b.connect()


def test_no_saved_password():
    b = ImapBackend(account(), secret_getter=lambda _id: None, client_factory=lambda *a, **k: FakeIMAP())
    with pytest.raises(BackendError, match="No password is saved"):
        b.connect()


def test_gmail_login_failure_hint_and_no_secret_leak():
    fake = FakeIMAP(login_error=f"[AUTHENTICATIONFAILED] Invalid credentials for {SECRET}")
    b = backend(fake, account(provider="gmail", host="imap.gmail.com", email="me@gmail.example.com"))
    with pytest.raises(BackendError) as e:
        b.connect()
    msg = str(e.value)
    assert "App Password" in msg
    assert SECRET not in msg
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert ("logout",) in fake.calls


def test_outlook_password_failure_points_to_oauth():
    fake = FakeIMAP(login_error="LOGIN failed.")
    b = backend(fake, account(provider="outlook", host="outlook.office365.com"))
    with pytest.raises(BackendError, match="oauth2-microsoft"):
        b.connect()


def test_oauth_login_uses_token():
    fake = FakeIMAP()
    b = ImapBackend(account(auth="oauth2-microsoft"), client_factory=lambda *a, **k: fake,
                    token_getter=lambda acct: "access-tok")
    b.connect()
    assert ("oauth2_login", "me@example.com", "access-tok") in fake.calls
    assert "login" not in fake.names()


def test_connect_failure_plain_english():
    def factory(host, **opts):
        raise ConnectionRefusedError("refused")

    b = ImapBackend(account(), secret_getter=lambda _i: SECRET, client_factory=factory)
    with pytest.raises(BackendError, match="could not connect to imap.example.com"):
        b.connect()


def test_server_error_is_scrubbed():
    fake = FakeIMAP()
    b = backend(fake)
    b.connect()
    fake.fail_next = [imap_errors.IMAPClientError(f"select failed: weird {SECRET}")]
    with pytest.raises(BackendError) as e:
        b.search_uids("INBOX")
    assert SECRET not in str(e.value)
    assert "********" in str(e.value)


# ---- folders ----------------------------------------------------------------------------------

def test_list_folders_special_use_and_name_guess():
    fake = FakeIMAP(folders={
        "INBOX": (b"\\HasNoChildren",),
        "[Gmail]": (b"\\Noselect", b"\\HasChildren"),
        "[Gmail]/All Mail": (b"\\All", b"\\HasNoChildren"),
        "[Gmail]/Sent Mail": (b"\\Sent",),
        "[Gmail]/Spam": (b"\\Junk",),
        "[Gmail]/Important": (b"\\Important",),
        "[Gmail]/Starred": (b"\\Flagged",),
        "Sent Items": (b"\\HasNoChildren",),          # older server: no special-use flags
        "Deleted Items": (),
        "Junk E-mail": (),
        "odd": (b"\\trash",),                         # lower-case flag from a sloppy server
        "Receipts": (),
        "Trash": (),
        "Archive/All Mail": (),
    })
    b = backend(fake)
    b.connect()
    by = {f.name: f for f in b.list_folders()}
    assert by["[Gmail]/All Mail"].special_use == "\\All"
    assert by["[Gmail]/Sent Mail"].special_use == "\\Sent"
    assert by["[Gmail]/Spam"].special_use == "\\Junk"
    assert by["[Gmail]/Important"].special_use == "\\Important"
    assert by["[Gmail]/Starred"].special_use == "\\Flagged"
    assert not by["[Gmail]"].selectable
    assert by["Sent Items"].special_use == "\\Sent"
    assert by["Deleted Items"].special_use == "\\Trash"
    assert by["Junk E-mail"].special_use == "\\Junk"
    assert by["odd"].special_use == "\\Trash"
    assert by["Receipts"].special_use is None
    assert by["Trash"].special_use == "\\Trash"
    assert by["Archive/All Mail"].special_use == "\\All"
    assert by["INBOX"].delimiter == "/"


def test_ensure_folder_with_dot_delimiter_and_inbox_prefix():
    fake = FakeIMAP(delimiter=".", prefix="INBOX.", folders={"INBOX": ()})
    b = backend(fake)
    b.connect()
    assert b.ensure_folder("TacoSort/Receipts") == "INBOX.TacoSort.Receipts"
    created = [c[1] for c in fake.calls if c[0] == "create_folder"]
    assert created == ["INBOX.TacoSort", "INBOX.TacoSort.Receipts"]
    subscribed = [c[1] for c in fake.calls if c[0] == "subscribe_folder"]
    assert subscribed == created
    # Second time: nothing new is created.
    fake.calls.clear()
    assert b.ensure_folder("TacoSort/Receipts") == "INBOX.TacoSort.Receipts"
    assert "create_folder" not in fake.names()


def test_ensure_folder_plain_server_and_existing_parent():
    fake = FakeIMAP(folders={"INBOX": (), "TacoSort": ()})
    b = backend(fake)
    b.connect()
    assert b.ensure_folder("TacoSort/Finance") == "TacoSort/Finance"
    assert [c[1] for c in fake.calls if c[0] == "create_folder"] == ["TacoSort/Finance"]
    assert b.ensure_folder("Receipts") == "Receipts"


def test_ensure_folder_already_given_with_prefix():
    fake = FakeIMAP(delimiter=".", prefix="INBOX.", folders={"INBOX": ()})
    b = backend(fake)
    b.connect()
    assert b.ensure_folder("INBOX/Travel") == "INBOX.Travel"


def test_ensure_folder_reuses_other_capitals():
    fake = FakeIMAP(folders={"INBOX": (), "receipts": (), "tacosort": ()})
    b = backend(fake)
    b.connect()
    assert b.ensure_folder("Receipts") == "receipts"
    assert b.ensure_folder("TacoSort/Travel") == "tacosort/Travel"     # the parent keeps its spelling
    assert [c[1] for c in fake.calls if c[0] == "create_folder"] == ["tacosort/Travel"]
    fake.calls.clear()
    assert b.ensure_folder("TACOSORT/travel") == "tacosort/Travel"
    assert "create_folder" not in fake.names()


def test_ensure_folder_other_capitals_with_dot_delimiter_and_prefix():
    fake = FakeIMAP(delimiter=".", prefix="INBOX.", folders={"INBOX": (), "INBOX.receipts": ()})
    b = backend(fake)
    b.connect()
    assert b.ensure_folder("Receipts") == "INBOX.receipts"
    assert "create_folder" not in fake.names()


def test_ensure_folder_exact_name_wins_over_other_capitals():
    fake = FakeIMAP(folders={"INBOX": (), "receipts": (), "Receipts": ()})   # a case-sensitive server
    b = backend(fake)
    b.connect()
    assert b.ensure_folder("Receipts") == "Receipts"


def test_ensure_folder_create_refused_because_it_exists_in_other_capitals():
    fake = FakeIMAP(folders={"INBOX": ()})
    b = backend(fake)
    b.connect()

    def create(name):          # someone else made "receipts" a moment ago; the server refuses
        fake.folders["receipts"] = ()
        raise imap_errors.IMAPClientError("[ALREADYEXISTS] Mailbox exists")

    fake.create_folder = create
    assert b.ensure_folder("Receipts") == "receipts"


def test_folder_state():
    fake = FakeIMAP()
    b = backend(fake)
    b.connect()
    st = b.folder_state("INBOX")
    assert (st.uidvalidity, st.uidnext, st.exists) == (7, 50, 3)
    assert "select_folder" not in fake.names()


def test_folder_state_without_uidnext_uses_highest_uid():
    fake = FakeIMAP(messages=msgs(3, 5, 9))
    fake.status = {b"UIDVALIDITY": 7, b"MESSAGES": 3}            # this server leaves UIDNEXT out
    b = backend(fake)
    b.connect()
    assert b.folder_state("INBOX").uidnext == 10
    fake.messages.clear()
    fake.status[b"UIDNEXT"] = 0
    assert b.folder_state("INBOX").uidnext == 1


# ---- certificate checks -----------------------------------------------------------------------

def _contexts(acct):
    fake = FakeIMAP()
    b = backend(fake, acct)
    b.connect()
    return b.made[0][1]["ssl_context"], getattr(fake, "starttls_context", None)


def test_certificates_are_checked_for_normal_servers():
    ctx, _ = _contexts(account(host="imap.example.com", ssl=True, port=993))
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    ctx, tls = _contexts(account(host="imap.example.com", ssl=False, port=143))
    assert tls is ctx
    assert tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname is True
    ctx, _ = _contexts(account(host="127.0.0.2.example.com"))
    assert ctx.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_certificates_unchecked_only_for_this_computer(host):
    for use_ssl in (True, False):
        ctx, tls = _contexts(account(host=host, ssl=use_ssl, port=1143))
        used = ctx if use_ssl else tls
        assert used.verify_mode == ssl.CERT_NONE and used.check_hostname is False


# ---- reading ----------------------------------------------------------------------------------

def test_search_uids_min_uid_and_limit():
    fake = FakeIMAP(messages={u: row(header_block(From="a@example.com")) for u in (3, 5, 9, 12)})
    b = backend(fake)
    b.connect()
    assert b.search_uids("INBOX") == [3, 5, 9, 12]
    assert b.search_uids("INBOX", min_uid=6) == [9, 12]
    assert b.search_uids("INBOX", min_uid=100) == []          # "100:*" returns 12; filtered out
    assert b.search_uids("INBOX", limit=2) == [9, 12]
    assert all(c[2] is True for c in fake.calls if c[0] == "select_folder")  # read-only


def test_search_skips_messages_marked_deleted():
    fake = FakeIMAP(messages={1: row(header_block(From="a@example.com")),
                              2: row(header_block(From="a@example.com"), flags=(b"\\Deleted",)),
                              3: row(header_block(From="a@example.com"))})
    b = backend(fake)
    b.connect()
    assert b.search_uids("INBOX") == [1, 3]
    assert b.search_uids("INBOX", min_uid=2) == [3]


def test_fetch_parses_headers_and_never_sets_seen():
    hdr = header_block(
        From="=?UTF-8?Q?Caf=C3=A9_Bistro?= <News@Example.com>",
        To='"Me" <me@example.com>, other@example.com',
        Cc="=?iso-8859-1?q?J=F6rg?= <jorg@example.com>",
        Reply_To="<reply@example.com>",
        Subject="=?utf-8?B?w5xiZXIgdW5z?= and\r\n more",
        Date="Tue, 03 Mar 2026 10:00:00 +0100",
        Message_ID=" <abc@example.com> ",
        List_Unsubscribe="<mailto:unsub@example.com>",
        In_Reply_To="<prev@example.com>",
    )
    fake = FakeIMAP(caps=("IMAP4REV1", "X-GM-EXT-1"),
                    messages={4: row(hdr, flags=(b"\\Flagged",), labels=(b"\\Inbox", b"\\Important", b"Caf&AOk-"))})
    b = backend(fake)
    b.connect()
    [m] = b.fetch("INBOX", [4, 99])        # 99 does not exist: skipped silently
    assert m.uid == 4 and m.folder == "INBOX" and m.account == "acct"
    assert m.from_.email == "news@example.com" and m.from_.name == "Café Bistro"
    assert [a.email for a in m.to] == ["me@example.com", "other@example.com"]
    assert m.cc[0].name == "Jörg"
    assert m.reply_to.email == "reply@example.com"
    assert m.subject == "Über uns and more"
    assert m.date == datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)
    assert m.message_id == "<abc@example.com>"
    assert m.headers["list-unsubscribe"] == "<mailto:unsub@example.com>"
    assert m.is_reply and m.is_flagged
    assert m.gmail_labels == frozenset({"\\Inbox", "\\Important", "Café"})
    assert m.size == 1234 and m.snippet == ""

    fetches = [c for c in fake.calls if c[0] == "fetch"]
    items = " ".join(fetches[0][2])
    assert "BODY.PEEK[HEADER.FIELDS (" in items and "LIST-UNSUBSCRIBE" in items and "X-GM-LABELS" in items
    for c in fetches:
        for item in c[2]:
            assert item in ("UID", "FLAGS", "RFC822.SIZE", "INTERNALDATE", "X-GM-LABELS") \
                or item.startswith("BODY.PEEK["), item
    assert [c for c in fake.calls if c[0] == "select_folder"] == [("select_folder", "INBOX", True)]


def test_gmail_tabs_become_labels_most_specific_first():
    # Real Gmail never lists its tabs in X-GM-LABELS; only an X-GM-RAW "category:" search finds them.
    msgs = {u: row(header_block(From=f"s{u}@example.com"), labels=(b"\\Inbox",)) for u in (1, 2, 3, 4)}
    fake = FakeIMAP(caps=("IMAP4REV1", "X-GM-EXT-1"), messages=msgs,
                    tabs={"promotions": [1], "updates": [2, 3], "purchases": [3]})
    b = backend(fake)
    b.connect()
    got = {m.uid: m.gmail_labels for m in b.fetch("INBOX", [1, 2, 3, 4])}
    assert got[1] == {"\\Inbox", "CATEGORY_PROMOTIONS"}
    assert got[2] == {"\\Inbox", "CATEGORY_UPDATES"}
    assert got[3] == {"\\Inbox", "CATEGORY_PURCHASES"}      # a purchase is in Updates too
    assert got[4] == {"\\Inbox"}
    searches = [c[1] for c in fake.calls if c[0] == "search"]
    assert all(s[:2] == ["UID", "1,2,3,4"] for s in searches)  # only this batch, never the whole folder


@pytest.mark.parametrize("caps,host", [(("IMAP4REV1", "XYMHIGHESTMODSEQ"), "imap.example.com"),
                                        (("IMAP4REV1",), "imap.mail.yahoo.com"), (("IMAP4REV1",), "imap.aol.com")])
def test_yahoo_gets_the_whole_header(caps, host):
    # Yahoo answers HEADER.FIELDS with six standard headers only; the whole header has them all.
    head = header_block(From="Shop <deals@shop.com>", Subject="Sale", List_Unsubscribe="<https://u>",
                        Received="from mx.example", DKIM_Signature="v=1")
    fake = FakeIMAP(caps=caps, messages={1: {**row(b""), b"BODY[HEADER]": head}})
    del fake.messages[1][b"BODY[HEADER.FIELDS (FROM TO CC SUBJECT)]"]
    b = backend(fake, account(host=host))
    b.connect()
    [m] = b.fetch("INBOX", [1])
    items = next(c[2] for c in fake.calls if c[0] == "fetch")
    assert "BODY.PEEK[HEADER]" in items
    assert m.headers["list-unsubscribe"] == "<https://u>" and m.from_.email == "deals@shop.com"
    assert "received" not in m.headers and "dkim-signature" not in m.headers   # only the usual fields


def test_other_servers_ask_for_the_usual_fields():
    fake = FakeIMAP(messages={1: row(header_block(From="a@example.com"))})
    b = backend(fake)
    b.connect()
    b.fetch("INBOX", [1])
    items = next(c[2] for c in fake.calls if c[0] == "fetch")
    assert any(i.startswith("BODY.PEEK[HEADER.FIELDS") for i in items)


def test_gmail_tab_search_failure_still_fetches():
    fake = FakeIMAP(caps=("IMAP4REV1", "X-GM-EXT-1"), messages={1: row(header_block(From="a@example.com"))})
    plain_search = fake.search

    def search(criteria="ALL", charset=None):
        if "X-GM-RAW" in criteria:
            raise imap_errors.IMAPClientError("SEARCH command error: BAD")
        return plain_search(criteria, charset)

    fake.search = search
    b = backend(fake)
    b.connect()
    [m] = b.fetch("INBOX", [1])
    assert m.from_.email == "a@example.com" and not any(lb.startswith("CATEGORY_") for lb in m.gmail_labels)


def test_fetch_survives_bad_encodings():
    hdr = header_block(
        From=b"\xc4ngel <angel@example.com>",                      # raw 8-bit (latin-1), not encoded
        Subject="=?x-unknown-charset?q?hello?= =?utf-8?q?broken=ZZ?=",
        Date="not a date",
        To="undisclosed-recipients:;",
    )
    fake = FakeIMAP(messages={1: row(hdr)})
    b = backend(fake)
    b.connect()
    [m] = b.fetch("INBOX", [1])
    assert m.from_.email == "angel@example.com"
    assert "ngel" in m.from_.name
    assert "hello" in m.subject
    assert m.date == datetime(2026, 3, 1, 12, 0)   # falls back to INTERNALDATE
    assert m.to == []


def test_fetch_batches_and_no_gmail_labels_elsewhere():
    fake = FakeIMAP(messages={u: row(header_block(From=f"s{u}@example.com")) for u in range(1, 451)})
    b = backend(fake)
    b.connect()
    msgs = b.fetch("INBOX", list(range(1, 451)))
    assert len(msgs) == 450
    fetches = [c for c in fake.calls if c[0] == "fetch"]
    assert [len(c[1]) for c in fetches] == [200, 200, 50]
    assert all("X-GM-LABELS" not in c[2] for c in fetches)


def test_snippet_prefers_plain_text_and_strips_html():
    body = (b"--b1\r\nContent-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n"
            b"<html><style>p{}</style><p>Hello =C3=A9 &amp; welcome</p></html>\r\n--b1--\r\n")
    hdr = header_block(From="a@example.com", Content_Type='multipart/alternative; boundary="b1"')
    fake = FakeIMAP(messages={1: row(hdr, text=body)})
    b = backend(fake)
    b.connect()
    [m] = b.fetch("INBOX", [1], with_snippet=True)
    assert m.snippet == "Hello é & welcome"
    assert any("BODY.PEEK[TEXT]<0.8192>" in c[2] for c in fake.calls if c[0] == "fetch")

    plain = make_snippet({"content-type": "text/plain; charset=utf-8"}, ("word " * 300).encode())
    assert len(plain) == 500
    cut = make_snippet({"content-type": "text/plain", "content-transfer-encoding": "base64"}, b"SGVsbG8gd29ybGQ")
    assert cut.startswith("Hello wor")


def test_find_message_id_exact_match_only():
    fake = FakeIMAP(messages={
        1: row(header_block(Message_ID="<abc@example.com>")),
        2: row(header_block(Message_ID="<xabc@example.com>")),    # substring hit on the server
        3: row(header_block(Message_ID="<other@example.com>")),
    })
    b = backend(fake)
    b.connect()
    assert b.find_message_id("INBOX", "abc@example.com>") == []
    assert b.find_message_id("INBOX", "<abc@example.com>") == [1]
    assert b.find_message_id("INBOX", "") == []
    searches = [c for c in fake.calls if c[0] == "search"]
    assert searches[-1][1] == ["HEADER", "Message-ID", "<abc@example.com>", "UNDELETED"]
    for c in fake.calls:
        if c[0] == "fetch":
            assert all(i == "FLAGS" or i.startswith("BODY.PEEK[") for i in c[2])


def test_find_message_id_skips_deleted_originals():
    # A server without UIDPLUS leaves the original of a move behind, flagged \Deleted.
    fake = FakeIMAP(messages={
        4: row(header_block(Message_ID="<abc@example.com>"), flags=(b"\\Seen", b"\\Deleted")),
        8: row(header_block(Message_ID="<abc@example.com>")),
    })
    b = backend(fake)
    b.connect()
    assert b.find_message_id("INBOX", "<abc@example.com>") == [8]
    del fake.messages[8]
    assert b.find_message_id("INBOX", "<abc@example.com>") == []


# ---- moving -----------------------------------------------------------------------------------

def msgs(*uids):
    return {u: row(header_block(From="a@example.com")) for u in uids}


def test_move_with_move_capability():
    fake = FakeIMAP(messages=msgs(10, 11))
    b = backend(fake)
    b.connect()
    assert b.move("INBOX", [11, 10, 42], "Receipts") == {10: 900, 11: 901}
    assert ("move", (10, 11), "Receipts") in fake.calls
    assert ("select_folder", "INBOX", False) in fake.calls
    assert not {"copy", "add_flags", "uid_expunge"} & set(fake.names())


def test_move_fallback_copy_delete_uid_expunge_exact_uids():
    fake = FakeIMAP(caps=("IMAP4REV1", "UIDPLUS"), messages=msgs(10, 11, 12))
    b = backend(fake)
    b.connect()
    assert b.move("INBOX", [10, 11], "Receipts") == {10: 500, 11: 501}
    order = [c for c in fake.calls if c[0] in ("copy", "add_flags", "uid_expunge")]
    assert order == [
        ("copy", (10, 11), "Receipts"),
        ("add_flags", (10, 11), (b"\\Deleted",)),
        ("uid_expunge", (10, 11)),
    ]


def test_move_fallback_without_uidplus_never_expunges():
    fake = FakeIMAP(caps=("IMAP4REV1",), messages=msgs(10, 11))
    fake.copy_response = b"COPY completed"
    b = backend(fake)
    b.connect()
    assert b.move("INBOX", [10, 11], "Receipts") == {10: None, 11: None}
    assert "uid_expunge" not in fake.names()
    assert ("add_flags", (10, 11), (b"\\Deleted",)) in fake.calls


def test_move_failed_copy_flags_nothing():
    fake = FakeIMAP(caps=("IMAP4REV1", "UIDPLUS"), messages=msgs(10))
    b = backend(fake)
    b.connect()
    original_copy = fake.copy

    def bad_copy(uids, dest):
        original_copy(uids, dest)
        raise imap_errors.IMAPClientError("[TRYCREATE] no such folder")

    fake.copy = bad_copy
    with pytest.raises(BackendError):
        b.move("INBOX", [10], "Nope")
    assert "add_flags" not in fake.names() and "uid_expunge" not in fake.names()


def test_move_nothing_present():
    fake = FakeIMAP(messages=msgs(1))
    b = backend(fake)
    b.connect()
    assert b.move("INBOX", [5], "Receipts") == {}
    assert "move" not in fake.names()


def test_parse_copyuid():
    assert parse_copyuid(b"[COPYUID 38505 304,319:320 3956:3958] Done") == {304: 3956, 319: 3957, 320: 3958}
    assert parse_copyuid("38505 5 9") == {5: 9}
    assert parse_copyuid(b"OK done") == {}
    assert parse_copyuid(None) == {}
    assert parse_copyuid("1 1:3 7:8") == {}     # mismatched lengths: don't guess


# ---- reconnect and idle -----------------------------------------------------------------------

def test_reconnects_once_on_dropped_connection():
    first, second = FakeIMAP(messages=msgs(1, 2)), FakeIMAP(messages=msgs(1, 2))
    b = backend([first, second])
    b.connect()
    first.fail_next = [imap_errors.IMAPClientAbortError("socket error: EOF")]
    assert b.search_uids("INBOX") == [1, 2]
    assert len(b.made) == 2
    assert ("login", "me@example.com", SECRET) in second.calls


def test_gives_up_after_second_drop():
    a, c = FakeIMAP(), FakeIMAP()
    b = backend([a, c])
    b.connect()
    a.fail_next = [ConnectionResetError("reset")]
    c.fail_next = [None, ConnectionResetError("reset")]   # login works, the listing drops again
    with pytest.raises(BackendError, match="keeps dropping"):
        b.list_folders()


def test_drop_during_login_is_not_called_a_password_problem():
    fake = FakeIMAP()
    fake.fail_next = [ConnectionResetError("reset")]
    b = backend(fake)
    with pytest.raises(BackendError) as e:
        b.connect()
    assert "could not connect" in str(e.value) and "refused the login" not in str(e.value)


def test_idle_reports_new_mail_and_always_ends_idle():
    fake = FakeIMAP()
    fake.idle_responses = [(b"OK", b"Still here"), (4, b"EXISTS")]
    b = backend(fake)
    b.connect()
    assert b.idle("INBOX", 60) is True
    assert fake.names()[-3:] == ["idle", "idle_check", "idle_done"]
    assert ("select_folder", "INBOX", True) in fake.calls


def fast_clock(monkeypatch):
    """Each look at the clock moves it 5 seconds on, so waiting loops end without real waiting."""
    clock = {"t": 0.0}

    def tick():
        clock["t"] += 5
        return clock["t"]

    monkeypatch.setattr(client_module.time, "monotonic", tick)


def test_idle_ignores_flag_changes_and_deletions(monkeypatch):
    fast_clock(monkeypatch)
    fake = FakeIMAP()
    fake.idle_responses = [(3, b"FETCH", (b"FLAGS", (b"\\Seen",))), (2, b"EXPUNGE")]   # read on a phone
    b = backend(fake)
    b.connect()
    assert b.idle("INBOX", 60) is False
    fake.idle_responses = [(9, b"RECENT")]
    assert b.idle("INBOX", 60) is True


def test_poll_ignores_a_deletion(monkeypatch):
    fast_clock(monkeypatch)
    fake = FakeIMAP(caps=("IMAP4REV1",))
    b = backend(fake)
    b.connect()
    original = fake.folder_status

    def status(folder, what=None):
        answer = original(folder, what)
        fake.status[b"MESSAGES"] = 2      # one message deleted elsewhere while waiting; UIDNEXT unchanged
        return answer

    fake.folder_status = status
    assert b.idle("INBOX", 60) is False


def test_idle_done_sent_even_when_check_fails():
    fake, second = FakeIMAP(), FakeIMAP()
    second.idle_responses = [(1, b"EXISTS")]
    b = backend([fake, second])
    b.connect()

    def dying_check(timeout=None):
        fake.calls.append(("idle_check",))
        raise OSError("connection gone")

    fake.idle_check = dying_check
    assert b.idle("INBOX", 30) is True          # reconnected once, then saw new mail
    assert fake.names()[-3:] == ["idle_check", "idle_done", "logout"]


def test_idle_times_out_quietly():
    fake = FakeIMAP()
    b = backend(fake)
    b.connect()
    assert b.idle("INBOX", 0) is False


def test_poll_fallback_without_idle():
    fake = FakeIMAP(caps=("IMAP4REV1",))
    b = backend(fake)
    b.connect()
    checks = {"n": 0}
    original = fake.folder_status

    def status(folder, what=None):
        checks["n"] += 1
        if checks["n"] > 1:
            fake.status[b"UIDNEXT"] = 51
        return original(folder, what)

    fake.folder_status = status
    assert b.idle("INBOX", 120) is True
    assert "noop" in fake.names() and "idle" not in fake.names()


# ---- pure helpers -----------------------------------------------------------------------------

def test_decode_helpers():
    assert decode_header_value("=?utf-8?q?a?= =?utf-8?q?b?=") == "ab"
    assert decode_header_value("plain  text\r\n folded") == "plain text folded"
    assert decode_header_value(None) == ""
    assert parse_addresses("broken <<a@Example.COM>>,,")[0].email == "a@example.com"
    assert parse_addresses("") == []
