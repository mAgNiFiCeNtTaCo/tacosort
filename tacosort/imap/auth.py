"""Passwords and sign-in tokens.

Secrets live in the operating system's password store (Windows Credential Manager, macOS Keychain,
Linux Secret Service) through the `keyring` package, under the service name "tacosort". Each kind of
secret has its own entry name, so they can never be mixed up:
- `account:<id>`: an account's password, or its OAuth2 refresh token
- `account:<id>:client-secret`: the Google client secret of an oauth2-google account
- `ai:anthropic`: the Anthropic API key for the optional AI step
Older versions saved an account's password under the bare `<id>`; it is moved to `account:<id>` the
first time it is read. Nothing secret is ever written to the config file or to logs.

Optional OAuth2 sign-in (XOAUTH2) for two providers:
- Microsoft (Outlook.com / Hotmail): the "device code" flow. TacoSort shows a short code, the person
  types it at microsoft.com/devicelogin and approves. Needs the person's own app registration
  (a public client id, not secret).
- Google: the "installed app" flow. TacoSort opens the browser, Google sends the person back to a tiny
  web server on this computer (http://127.0.0.1:<port>), protected with PKCE. Needs the person's own
  Google "Desktop app" client id and client secret.
In both cases the long-lived refresh token is stored in the keychain as `account:<id>`; short-lived
access tokens are only kept in memory. Only the standard library is used for HTTP (urllib).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable

from ..backend import BackendError
from ..models import AccountConfig

SERVICE = "tacosort"

MICROSOFT_AUTHORITY = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
MICROSOFT_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "https://mail.google.com/"

# Replaceable in tests.
_sleep = time.sleep
_open_browser = webbrowser.open

# account id -> (access token, expiry as time.time()). Memory only.
_token_cache: dict[str, tuple[str, float]] = {}


class CredentialError(BackendError):
    """A password/sign-in problem, explained in plain English (safe to show; never contains a secret)."""


# ---- keychain ------------------------------------------------------------------------------------

_NO_KEYCHAIN = (
    "TacoSort could not use this computer's password store (keychain), so it cannot save or read "
    "your email password. On Windows and macOS this normally just works. On Linux, install and "
    "unlock a keyring such as GNOME Keyring or KWallet (a server without a desktop needs one set up, "
    "for example the 'keyrings.alt' package). Details: {detail}"
)


def _keyring():
    import keyring  # imported here so a broken keyring install only fails when secrets are needed

    return keyring


def _keychain_failed(exc: Exception) -> CredentialError:
    return CredentialError(_NO_KEYCHAIN.format(detail=type(exc).__name__))


AI_KEY_ENTRY = "ai:anthropic"
# Before 2026-10-28 the bare name "anthropic" was used both as an account id and (in docs) for the AI
# key, so that one old entry is never migrated or deleted: nobody can tell which of the two it holds.
_NO_LEGACY = {"anthropic"}


def _get(entry: str) -> str | None:
    try:
        return _keyring().get_password(SERVICE, entry)
    except Exception as exc:  # noqa: BLE001 (any keychain failure: none installed, locked, D-Bus missing)
        raise _keychain_failed(exc) from None


def _set(entry: str, value: str) -> None:
    try:
        _keyring().set_password(SERVICE, entry, value)
    except Exception as exc:  # noqa: BLE001 (keychain backends fail in many ways)
        raise _keychain_failed(exc) from None


def _delete(entry: str) -> None:
    try:
        kr = _keyring()
        try:
            kr.delete_password(SERVICE, entry)
        except kr.errors.PasswordDeleteError:
            pass   # nothing saved under that name
    except Exception as exc:  # noqa: BLE001 (keychain backends fail in many ways)
        raise _keychain_failed(exc) from None


def _account_entry(account_id: str, suffix: str = "") -> str:
    if not account_id or ":" in account_id:
        raise CredentialError(
            f"The account name '{account_id}' can't be used for saving a password (it must not be empty "
            "or contain ':'). Rename the account in the config file, then run 'tacosort setup'."
        )
    return f"account:{account_id}{suffix}"


def _read_migrating(entry: str, legacy: str, legacy_id: str) -> str | None:
    """Read `entry`; if missing, read the old `legacy` name once and move it to `entry`."""
    value = _get(entry)
    if value is not None or legacy_id in _NO_LEGACY:
        return value
    old = _get(legacy)
    if old is not None:
        try:
            _set(entry, old)
            _delete(legacy)
        except CredentialError:
            pass   # moving it is a nicety: the value still works, and is tried again next time
    return old


def get_secret(account_id: str) -> str | None:
    """The saved password/token for `account_id`, or None when nothing is saved."""
    return _read_migrating(_account_entry(account_id), account_id, account_id)


def set_secret(account_id: str, value: str) -> None:
    """Save (or replace) the password/token for `account_id`."""
    _set(_account_entry(account_id), value)


def delete_secret(account_id: str) -> None:
    """Forget the saved password/token for `account_id`. Nothing happens when none is saved."""
    _delete(_account_entry(account_id))
    if account_id not in _NO_LEGACY:
        _delete(account_id)          # an old-style entry that was never read (and so never moved)


def delete_account_secrets(account_id: str) -> None:
    """Forget everything saved for an account: password or sign-in token, and Google client secret."""
    delete_secret(account_id)
    _delete(_client_secret_entry(account_id))
    if account_id not in _NO_LEGACY:
        _delete(f"{account_id}:client-secret")


def get_ai_key() -> str | None:
    """The Anthropic API key saved for the AI step, or None."""
    return _get(AI_KEY_ENTRY)


def set_ai_key(value: str) -> None:
    """Save (or replace) the Anthropic API key."""
    _set(AI_KEY_ENTRY, value)


def delete_ai_key() -> None:
    """Forget the saved Anthropic API key. Nothing happens when none is saved."""
    _delete(AI_KEY_ENTRY)


# ---- HTTP helper ---------------------------------------------------------------------------------

def _post_form(url: str, fields: dict[str, str]) -> dict:
    """POST a form and return the JSON answer. OAuth servers answer errors as JSON with HTTP 400,
    so those are returned too (with an "error" key) instead of raised."""
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 (error body may not be JSON)
            return {"error": f"http_{exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        raise CredentialError(
            f"TacoSort could not reach the sign-in server ({type(exc).__name__}). "
            "Check your internet connection and try again."
        ) from None


def _need_client_id(account: AccountConfig) -> str:
    if not account.oauth_client_id:
        raise CredentialError(
            f"Account '{account.id}' signs in with OAuth2 but has no client id. Run 'tacosort setup' "
            "and paste the client id of your app registration."
        )
    return account.oauth_client_id


def _remember(account: AccountConfig, answer: dict) -> str:
    """Store the refresh token (if a new one came), cache the access token, return it."""
    if answer.get("refresh_token"):
        set_secret(account.id, answer["refresh_token"])
    token = answer["access_token"]
    _token_cache[account.id] = (token, time.time() + int(answer.get("expires_in", 3600)))
    return token


def _explain(answer: dict, what: str) -> CredentialError:
    """Turn an OAuth error answer into a plain-English error (only the error code, never tokens)."""
    code = str(answer.get("error", "unknown"))
    hints = {
        "invalid_grant": "The saved sign-in has expired or was revoked. Run 'tacosort setup' to sign in again.",
        "invalid_client": "The client id (or client secret) was not accepted. Check it in 'tacosort setup'.",
        "unauthorized_client": "The app registration does not allow this sign-in. For Microsoft, switch "
                               "'Allow public client flows' to Yes in the app's Authentication page.",
        "authorization_declined": "The sign-in was declined in the browser.",
        "expired_token": "The sign-in code expired before it was used. Please try again.",
        "access_denied": "The sign-in was cancelled or access was denied.",
    }
    return CredentialError(f"{what} failed ({code}). {hints.get(code, 'Please try signing in again.')}")


# ---- Microsoft: device code flow -----------------------------------------------------------------

_MS_CONSUMER_DOMAINS = ("outlook.", "hotmail.", "live.", "msn.com", "passport.com", "windowslive.com")


def _microsoft_base(account: AccountConfig) -> str:
    """Personal addresses use the 'consumers' tenant; work/school addresses use 'common'."""
    domain = account.email.rsplit("@", 1)[-1].lower()
    tenant = "consumers" if domain.startswith(_MS_CONSUMER_DOMAINS) else "common"
    return MICROSOFT_AUTHORITY.format(tenant=tenant)


def microsoft_device_login(account: AccountConfig, *, log: Callable[[str], object] = print) -> None:
    """Sign in to Outlook.com/Hotmail/Microsoft 365 once and save the refresh token in the keychain.

    Shows a web address and a short code through `log`, then waits while the person approves."""
    client_id = _need_client_id(account)
    base = _microsoft_base(account)
    start = _post_form(f"{base}/devicecode", {"client_id": client_id, "scope": MICROSOFT_SCOPE})
    if "device_code" not in start:
        raise _explain(start, "Starting the Microsoft sign-in")
    log(start.get("message") or (
        f"To sign in, open {start.get('verification_uri')} and enter the code {start.get('user_code')}"
    ))
    interval = int(start.get("interval", 5))
    deadline = time.time() + int(start.get("expires_in", 900))
    while time.time() < deadline:
        _sleep(interval)
        answer = _post_form(f"{base}/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": start["device_code"],
        })
        if "access_token" in answer:
            _remember(account, answer)
            log("Signed in to Microsoft. TacoSort saved the sign-in in your keychain.")
            return
        error = answer.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise _explain(answer, "Microsoft sign-in")
    raise _explain({"error": "expired_token"}, "Microsoft sign-in")


def _microsoft_refresh(account: AccountConfig, refresh_token: str) -> str:
    answer = _post_form(f"{_microsoft_base(account)}/token", {
        "grant_type": "refresh_token",
        "client_id": _need_client_id(account),
        "refresh_token": refresh_token,
        "scope": MICROSOFT_SCOPE,
    })
    if "access_token" not in answer:
        raise _explain(answer, "Refreshing the Microsoft sign-in")
    return _remember(account, answer)  # Microsoft may hand out a new refresh token: it is saved


# ---- Google: installed-app loopback flow with PKCE -----------------------------------------------

def _client_secret_entry(account_id: str) -> str:
    return _account_entry(account_id, ":client-secret")


def _get_client_secret(account_id: str) -> str | None:
    return _read_migrating(_client_secret_entry(account_id), f"{account_id}:client-secret", account_id)


def pkce_pair() -> tuple[str, str]:
    """A random PKCE verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def google_auth_url(client_id: str, redirect_uri: str, challenge: str, state: str, login_hint: str = "") -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",   # ask for a refresh token
        "prompt": "consent",        # ...even if the person approved before
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """Catches Google's redirect back to http://127.0.0.1:<port>/?code=...&state=...

    Only a request carrying this sign-in's secret `state` counts; anything else gets 400 and the
    server keeps waiting, so another program on this computer can't end or fake the sign-in."""

    timeout = 10   # a client that connects and sends nothing is dropped after 10 seconds

    def do_GET(self):
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        if "code" not in query and "error" not in query:
            self._answer(404, "Not found.")
            return
        server: _RedirectServer = self.server  # type: ignore[assignment]
        if not secrets.compare_digest(query.get("state", "").encode(), server.state.encode()):
            self._answer(400, "This answer does not belong to the TacoSort sign-in that is waiting.")
            return
        server.accept(query)
        if "code" in query:
            self._answer(200, "TacoSort got your sign-in. You can close this tab and go back to TacoSort.")
        else:
            self._answer(200, "The sign-in was cancelled. You can close this tab.")

    def _answer(self, status: int, text: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<p>{text}</p>".encode())

    def log_message(self, *args):  # keep the code out of the console
        pass


class _RedirectServer(http.server.ThreadingHTTPServer):
    """The one-off web server on 127.0.0.1 (never the network) that waits for Google's redirect."""

    daemon_threads = True
    block_on_close = False      # closing never waits for a stuck connection

    def __init__(self, state: str):
        super().__init__(("127.0.0.1", 0), _RedirectHandler)
        self.state = state
        self.result: dict[str, str] | None = None
        self.done = threading.Event()
        self._lock = threading.Lock()

    def accept(self, query: dict[str, str]) -> None:
        with self._lock:
            if self.result is None:
                self.result = query
                self.done.set()


def google_loopback_login(account: AccountConfig, client_secret: str, *,
                          log: Callable[[str], object] = print, timeout: int = 300) -> None:
    """Sign in to Gmail once in the browser and save the refresh token (and client secret) in the keychain."""
    client_id = _need_client_id(account)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    server = _RedirectServer(state)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    worker.start()
    try:
        url = google_auth_url(client_id, redirect_uri, challenge, state, account.email)
        log(f"Opening your browser to sign in to Google. If it does not open, visit:\n{url}")
        _open_browser(url)
        server.done.wait(timeout)
        result = server.result
    finally:
        server.shutdown()
        server.server_close()
    if result is None:
        raise CredentialError("Google sign-in timed out: nothing came back from the browser. Please try again.")
    if "code" not in result:
        raise _explain(result, "Google sign-in")
    answer = _post_form(GOOGLE_TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": result["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    })
    if "access_token" not in answer:
        raise _explain(answer, "Google sign-in")
    if not answer.get("refresh_token"):
        raise CredentialError(
            "Google did not hand out a lasting sign-in (refresh token). Remove TacoSort's access at "
            "https://myaccount.google.com/permissions and run 'tacosort setup' again."
        )
    _set(_client_secret_entry(account.id), client_secret)
    _remember(account, answer)
    log("Signed in to Google. TacoSort saved the sign-in in your keychain.")


def _google_refresh(account: AccountConfig, refresh_token: str) -> str:
    client_secret = _get_client_secret(account.id) or ""
    answer = _post_form(GOOGLE_TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": _need_client_id(account),
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    })
    if "access_token" not in answer:
        raise _explain(answer, "Refreshing the Google sign-in")
    return _remember(account, answer)


# ---- used by ImapBackend -------------------------------------------------------------------------

def get_access_token(account: AccountConfig) -> str:
    """A fresh OAuth2 access token for an oauth2-* account (refreshed from the keychain when needed)."""
    cached = _token_cache.get(account.id)
    if cached and cached[1] - 60 > time.time():
        return cached[0]
    refresh_token = get_secret(account.id)
    if not refresh_token:
        raise CredentialError(
            f"Account '{account.id}' is not signed in yet. Run 'tacosort setup' to sign in."
        )
    if account.auth == "oauth2-microsoft":
        return _microsoft_refresh(account, refresh_token)
    if account.auth == "oauth2-google":
        return _google_refresh(account, refresh_token)
    raise CredentialError(f"Account '{account.id}' does not use OAuth2 sign-in (auth is '{account.auth}').")
