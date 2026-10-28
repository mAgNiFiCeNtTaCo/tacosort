import base64
import hashlib
import http.client
import io
import json
import socket
import threading
import time
import types
import urllib.error
import urllib.parse

import pytest

from tidyinbox.backend import BackendError
from tidyinbox.imap import auth
from tidyinbox.models import AccountConfig


class FakeKeyring:
    class errors:
        class PasswordDeleteError(Exception):
            pass

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise self.errors.PasswordDeleteError("missing")
        del self.store[(service, user)]


class BrokenKeyring(FakeKeyring):
    def get_password(self, service, user):
        raise RuntimeError("No recommended backend was available")

    set_password = delete_password = get_password


@pytest.fixture
def kr(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(auth, "_keyring", lambda: fake)
    monkeypatch.setattr(auth, "_sleep", lambda s: None)
    auth._token_cache.clear()
    yield fake
    auth._token_cache.clear()


class FakeHTTP:
    """Stands in for urllib.request.urlopen: answers queued (status, json) pairs, records requests."""

    def __init__(self, monkeypatch, answers):
        self.answers = list(answers)
        self.requests = []
        monkeypatch.setattr(auth.urllib.request, "urlopen", self)

    def __call__(self, req, timeout=None):
        fields = dict(urllib.parse.parse_qsl(req.data.decode()))
        self.requests.append((req.full_url, fields))
        status, body = self.answers.pop(0)
        raw = json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", {}, io.BytesIO(raw))
        resp = types.SimpleNamespace(read=lambda: raw)
        return _Ctx(resp)


class _Ctx:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self.resp

    def __exit__(self, *a):
        return False


def ms_account(**kw):
    return AccountConfig(id="hm", email="user@hotmail.com", provider="outlook",
                         auth="oauth2-microsoft", oauth_client_id="client-123", **kw)


def g_account():
    return AccountConfig(id="gm", email="me@example.com", provider="gmail",
                         auth="oauth2-google", oauth_client_id="gclient.apps.example.com")


# ---- keychain ----------------------------------------------------------------------------------

def test_secret_roundtrip(kr):
    assert auth.get_secret("acct") is None
    auth.set_secret("acct", "app-pass")
    assert kr.store == {("tidyinbox", "account:acct"): "app-pass"}
    assert auth.get_secret("acct") == "app-pass"
    auth.delete_secret("acct")
    assert auth.get_secret("acct") is None
    auth.delete_secret("acct")  # deleting twice is fine


def test_old_bare_entry_is_moved_once(kr):
    kr.store[("tidyinbox", "acct")] = "old-pass"            # saved by an older TidyInbox
    assert auth.get_secret("acct") == "old-pass"
    assert kr.store == {("tidyinbox", "account:acct"): "old-pass"}
    kr.store[("tidyinbox", "acct")] = "stale"                # a new entry always wins over an old one
    assert auth.get_secret("acct") == "old-pass"


def test_ai_key_and_account_named_anthropic_never_mix(kr):
    auth.set_secret("anthropic", "MAIL-APP-PASSWORD")        # someone@anthropic.example's mailbox
    assert auth.get_ai_key() is None
    auth.set_ai_key("sk-ant-test")
    assert kr.store[("tidyinbox", "ai:anthropic")] == "sk-ant-test"
    assert auth.get_secret("anthropic") == "MAIL-APP-PASSWORD"
    auth.delete_account_secrets("anthropic")                # removing the account keeps the AI key
    assert auth.get_ai_key() == "sk-ant-test"
    auth.delete_ai_key()
    auth.delete_ai_key()
    assert kr.store == {}


def test_old_bare_anthropic_entry_is_never_used(kr):
    kr.store[("tidyinbox", "anthropic")] = "sk-ant-old"       # the old docs' place for the AI key
    assert auth.get_ai_key() is None
    assert auth.get_secret("anthropic") is None             # never sent to a mail server either
    auth.delete_account_secrets("anthropic")
    assert kr.store == {("tidyinbox", "anthropic"): "sk-ant-old"}


def test_delete_account_secrets_removes_everything_of_that_account(kr):
    kr.store.update({
        ("tidyinbox", "account:gm"): "ref", ("tidyinbox", "account:gm:client-secret"): "cs",
        ("tidyinbox", "gm"): "old", ("tidyinbox", "gm:client-secret"): "old-cs",
        ("tidyinbox", "account:other"): "keep", ("tidyinbox", "ai:anthropic"): "keep-too",
    })
    auth.delete_account_secrets("gm")
    assert kr.store == {("tidyinbox", "account:other"): "keep", ("tidyinbox", "ai:anthropic"): "keep-too"}


def test_account_id_with_colon_cannot_reach_other_entries(kr):
    kr.store[("tidyinbox", "account:x:client-secret")] = "cs"
    with pytest.raises(auth.CredentialError, match="must not be empty or contain ':'"):
        auth.get_secret("x:client-secret")
    with pytest.raises(auth.CredentialError):
        auth.set_secret("", "v")


def test_broken_keychain_gives_plain_english(monkeypatch):
    monkeypatch.setattr(auth, "_keyring", lambda: BrokenKeyring())
    for call in (lambda: auth.get_secret("a"), lambda: auth.set_secret("a", "v"), lambda: auth.delete_secret("a"),
                 lambda: auth.delete_account_secrets("a"), auth.get_ai_key, lambda: auth.set_ai_key("k"),
                 auth.delete_ai_key):
        with pytest.raises(auth.CredentialError) as e:
            call()
        assert "password store" in str(e.value)
        assert isinstance(e.value, BackendError)


# ---- Microsoft device code ---------------------------------------------------------------------

def test_microsoft_device_login(kr, monkeypatch):
    web = FakeHTTP(monkeypatch, [
        (200, {"device_code": "dev", "user_code": "ABCD-1234", "verification_uri": "https://microsoft.com/devicelogin",
               "interval": 1, "expires_in": 600, "message": "Go to https://microsoft.com/devicelogin, code ABCD-1234"}),
        (400, {"error": "authorization_pending"}),
        (400, {"error": "slow_down"}),
        (200, {"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600}),
    ])
    logs = []
    auth.microsoft_device_login(ms_account(), log=logs.append)
    assert "ABCD-1234" in logs[0]
    assert kr.store[("tidyinbox", "account:hm")] == "ref-1"
    url, fields = web.requests[0]
    assert url == "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
    assert fields["scope"] == "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
    assert fields["client_id"] == "client-123"
    assert web.requests[1][1]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert not any("acc-1" in line or "ref-1" in line for line in logs)
    assert auth.get_access_token(ms_account()) == "acc-1"   # cached, no HTTP
    assert len(web.requests) == 4


def test_microsoft_work_address_uses_common(kr, monkeypatch):
    web = FakeHTTP(monkeypatch, [(400, {"error": "invalid_client"})])
    acct = AccountConfig(id="w", email="me@example.com", auth="oauth2-microsoft", oauth_client_id="c")
    with pytest.raises(auth.CredentialError) as e:
        auth.microsoft_device_login(acct, log=lambda m: None)
    assert "/common/" in web.requests[0][0]
    assert "client id" in str(e.value)


def test_microsoft_declined(kr, monkeypatch):
    FakeHTTP(monkeypatch, [
        (200, {"device_code": "dev", "user_code": "X", "verification_uri": "u", "interval": 1, "expires_in": 600}),
        (400, {"error": "authorization_declined"}),
    ])
    with pytest.raises(auth.CredentialError) as e:
        auth.microsoft_device_login(ms_account(), log=lambda m: None)
    assert "declined" in str(e.value)


def test_missing_client_id(kr):
    acct = AccountConfig(id="x", email="me@example.com", auth="oauth2-microsoft")
    with pytest.raises(auth.CredentialError, match="client id"):
        auth.microsoft_device_login(acct, log=lambda m: None)


def test_refresh_microsoft_rotates_refresh_token(kr, monkeypatch):
    kr.store[("tidyinbox", "account:hm")] = "old-ref"
    web = FakeHTTP(monkeypatch, [(200, {"access_token": "acc-2", "refresh_token": "new-ref", "expires_in": 3600})])
    assert auth.get_access_token(ms_account()) == "acc-2"
    assert kr.store[("tidyinbox", "account:hm")] == "new-ref"
    assert web.requests[0][1]["grant_type"] == "refresh_token"
    assert web.requests[0][1]["refresh_token"] == "old-ref"


def test_refresh_expired_sign_in(kr, monkeypatch):
    kr.store[("tidyinbox", "account:hm")] = "old-ref"
    FakeHTTP(monkeypatch, [(400, {"error": "invalid_grant", "error_description": "secret stuff"})])
    with pytest.raises(auth.CredentialError) as e:
        auth.get_access_token(ms_account())
    assert "sign in again" in str(e.value)
    assert "old-ref" not in str(e.value)


def test_not_signed_in(kr):
    with pytest.raises(auth.CredentialError, match="not signed in"):
        auth.get_access_token(ms_account())


def test_network_down(kr, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(auth.urllib.request, "urlopen", boom)
    kr.store[("tidyinbox", "account:hm")] = "r"
    with pytest.raises(auth.CredentialError, match="internet connection"):
        auth.get_access_token(ms_account())


# ---- Google loopback + PKCE --------------------------------------------------------------------

def test_pkce_pair():
    verifier, challenge = auth.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_google_loopback_login(kr, monkeypatch):
    web = FakeHTTP(monkeypatch, [(200, {"access_token": "g-acc", "refresh_token": "g-ref", "expires_in": 3600})])
    seen = {}

    def fake_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        seen.update(q)
        port = urllib.parse.urlparse(q["redirect_uri"]).port

        def visit():  # the browser coming back to the local server (loopback only)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/?" + urllib.parse.urlencode({"code": "the-code", "state": q["state"]}))
            seen["page"] = conn.getresponse().read()

        seen["thread"] = threading.Thread(target=visit, daemon=True)
        seen["thread"].start()
        return True

    monkeypatch.setattr(auth, "_open_browser", fake_browser)
    servers = record_servers(monkeypatch)
    auth.google_loopback_login(g_account(), "g-secret", log=lambda m: None, timeout=20)
    assert servers[0].server_address[0] == "127.0.0.1"      # never reachable from the network
    assert seen["code_challenge_method"] == "S256"
    assert seen["scope"] == "https://mail.google.com/"
    assert seen["redirect_uri"].startswith("http://127.0.0.1:")
    url, fields = web.requests[0]
    assert url == auth.GOOGLE_TOKEN_URL
    assert fields["code"] == "the-code"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(fields["code_verifier"].encode()).digest()).rstrip(b"=")
    assert challenge.decode() == seen["code_challenge"]
    assert kr.store[("tidyinbox", "account:gm")] == "g-ref"
    assert kr.store[("tidyinbox", "account:gm:client-secret")] == "g-secret"
    seen["thread"].join(10)
    assert b"got your sign-in" in seen["page"]

    # Later: refresh uses the stored client secret.
    auth._token_cache.clear()
    web.answers.append((200, {"access_token": "g-acc-2", "expires_in": 3600}))
    assert auth.get_access_token(g_account()) == "g-acc-2"
    assert web.requests[1][1]["client_secret"] == "g-secret"
    assert kr.store[("tidyinbox", "account:gm")] == "g-ref"   # Google kept the same refresh token


def record_servers(monkeypatch):
    made = []

    class Recording(auth._RedirectServer):
        def __init__(self, state):
            super().__init__(state)
            made.append(self)

    monkeypatch.setattr(auth, "_RedirectServer", Recording)
    return made


def test_google_client_secret_saved_under_old_name_still_works(kr, monkeypatch):
    kr.store[("tidyinbox", "gm")] = "g-ref"
    kr.store[("tidyinbox", "gm:client-secret")] = "old-cs"
    web = FakeHTTP(monkeypatch, [(200, {"access_token": "g-acc", "expires_in": 3600})])
    assert auth.get_access_token(g_account()) == "g-acc"
    assert web.requests[0][1]["client_secret"] == "old-cs"
    assert kr.store == {("tidyinbox", "account:gm"): "g-ref", ("tidyinbox", "account:gm:client-secret"): "old-cs"}


def test_google_wrong_state_is_refused_and_the_real_answer_still_counts(kr, monkeypatch):
    FakeHTTP(monkeypatch, [(200, {"access_token": "g-acc", "refresh_token": "g-ref", "expires_in": 3600})])
    pages = {}

    def fake_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        port = urllib.parse.urlparse(q["redirect_uri"]).port

        def visit():
            for name, query in (("forged", {"code": "evil", "state": "forged"}),
                                ("no-state", {"error": "access_denied"}),
                                ("real", {"code": "the-code", "state": q["state"]})):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request("GET", "/?" + urllib.parse.urlencode(query))
                resp = conn.getresponse()
                pages[name] = (resp.status, resp.read())

        pages["thread"] = threading.Thread(target=visit, daemon=True)
        pages["thread"].start()

    monkeypatch.setattr(auth, "_open_browser", fake_browser)
    auth.google_loopback_login(g_account(), "s", log=lambda m: None, timeout=20)
    pages["thread"].join(10)
    assert pages["forged"][0] == 400 and b"signed in" not in pages["forged"][1]
    assert pages["no-state"][0] == 400
    assert pages["real"][0] == 200
    assert kr.store[("tidyinbox", "account:gm")] == "g-ref"


def test_google_sign_in_timeout_is_kept_even_with_a_silent_connection(kr, monkeypatch):
    FakeHTTP(monkeypatch, [])
    sockets = []

    def fake_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        port = urllib.parse.urlparse(q["redirect_uri"]).port
        sockets.append(socket.create_connection(("127.0.0.1", port)))   # connects, never sends
        threading.Timer(6, sockets[-1].close).start()     # so a broken version fails instead of hanging

    monkeypatch.setattr(auth, "_open_browser", fake_browser)
    started = time.monotonic()
    try:
        with pytest.raises(auth.CredentialError, match="timed out"):
            auth.google_loopback_login(g_account(), "s", log=lambda m: None, timeout=1)
    finally:
        for s in sockets:
            s.close()
    assert time.monotonic() - started < 5
    assert ("tidyinbox", "account:gm") not in kr.store
