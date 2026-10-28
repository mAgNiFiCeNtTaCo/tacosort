"""ImapBackend: the real-server implementation of `tacosort.backend.MailBackend`, built on IMAPClient.

Safety promises kept here:
- Messages are only ever read with BODY.PEEK (and folders opened read-only for reading), so nothing
  gets marked as read.
- Nothing is deleted. The only \\Deleted flag + expunge is the move fallback for servers without MOVE,
  and it touches exactly the UIDs that were just copied (UID EXPUNGE). Servers without UIDPLUS can
  only expunge a whole folder, which could remove mail the person deleted in another app, so there
  the originals are left flagged \\Deleted (their mail app hides or purges them later) and nothing is
  expunged.
- Passwords and tokens never appear in error messages or logs.
- A MOVE/COPY is never blindly repeated after a dropped connection: TacoSort reconnects and looks
  (by Message-ID) at what the server already did, so a message is never copied twice and a move that
  did happen is still reported (and so can be undone).

Gmail note: Gmail folders are labels. MOVE out of INBOX removes the Inbox label and adds the target
label, which is exactly "moving" in Gmail's web interface. COPY would only add a label.
"""

from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import html
import logging
import re
import socket
import ssl
import time
from collections.abc import Callable, Iterable
from datetime import datetime

from imapclient import IMAPClient
from imapclient import exceptions as imap_errors
from imapclient.imap_utf7 import decode as utf7_decode

from ..backend import BackendError
from ..models import AccountConfig, Address, FolderInfo, FolderState, Message
from . import auth
from .providers import PROVIDERS

# Headers fetched for every message (the classifier works from these).
HEADER_FIELDS = (
    "From", "To", "Cc", "Reply-To", "Subject", "Date", "Message-ID", "In-Reply-To", "References",
    "List-Id", "List-Unsubscribe", "List-Post", "Precedence", "Auto-Submitted", "X-Mailer",
    "X-Campaign", "X-Mailgun-Tag", "X-SES-Outgoing", "Feedback-ID", "X-Auto-Response-Suppress",
    "Return-Path", "Sender", "Content-Type", "Content-Transfer-Encoding",
)
HEADER_FETCH = f"BODY.PEEK[HEADER.FIELDS ({' '.join(h.upper() for h in HEADER_FIELDS)})]"
SNIPPET_FETCH = "BODY.PEEK[TEXT]<0.8192>"   # first 8 KB of the body, enough for a ~500 char snippet
SNIPPET_CHARS = 500
BATCH = 200                                 # UIDs per FETCH / MOVE command
MAX_IDLE = 25 * 60                          # servers drop IDLE after ~29 minutes; re-issue before that
POLL_EVERY = 30                             # seconds between checks when the server has no IDLE

# Folders without special-use flags are recognised by these common names (last part, lower case),
# so TacoSort still never touches Sent/Drafts/Trash/Junk on older servers.
_NAME_FLAGS = {
    "sent": "\\Sent", "sent items": "\\Sent", "sent mail": "\\Sent", "sent messages": "\\Sent",
    "drafts": "\\Drafts", "draft": "\\Drafts",
    "trash": "\\Trash", "deleted items": "\\Trash", "deleted messages": "\\Trash", "bin": "\\Trash",
    "junk": "\\Junk", "spam": "\\Junk", "junk e-mail": "\\Junk", "junk email": "\\Junk", "bulk mail": "\\Junk",
    "all mail": "\\All", "archive": "\\Archive",
}
_SPECIAL = {"\\Sent", "\\Drafts", "\\Trash", "\\Junk", "\\All", "\\Archive", "\\Flagged", "\\Important"}

# Connection problems worth one reconnect + retry (only for reads; moves are checked instead).
_DROPPED = (imap_errors.IMAPClientAbortError, OSError, EOFError)   # OSError covers socket + SSL errors
MESSAGE_ID_FETCH = "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"

_logger = logging.getLogger("tacosort.imap")


class _Copied(Exception):
    """COPY worked, but marking the originals \\Deleted (or expunging them) failed afterwards."""

    def __init__(self, mapping: dict[int, int], cause: Exception):
        super().__init__(type(cause).__name__)
        self.mapping = mapping
        self.cause = cause


# ---- small decoding helpers (never raise on bad input) ------------------------------------------

def _text(value: object) -> str:
    """bytes/str/None -> str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _decode_bytes(value)
    return str(value)


def _decode_bytes(raw: bytes, charset: str | None = None) -> str:
    for cs in (charset, "utf-8"):
        if not cs:
            continue
        try:
            return raw.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("cp1252", errors="replace")


def _fix_surrogates(value: str) -> str:
    """The email package keeps raw 8-bit header bytes as surrogates; turn them into real text."""
    if any("\udc80" <= ch <= "\udcff" for ch in value):
        return _decode_bytes(value.encode("utf-8", "surrogateescape"))
    return value


def decode_header_value(value: str | None) -> str:
    """Decode an RFC 2047 header ("=?utf-8?q?Caf=C3=A9?=") into plain text; unfolds and trims."""
    if not value:
        return ""
    value = _fix_surrogates(re.sub(r"\r?\n[ \t]+", " ", str(value)))
    try:
        parts = email.header.decode_header(value)
    except Exception:  # noqa: BLE001 (never crash on odd server data)
        return value.strip()
    out = []
    for chunk, charset in parts:
        out.append(_decode_bytes(chunk, charset) if isinstance(chunk, bytes) else chunk)
    return re.sub(r"\s+", " ", "".join(out)).strip()


_EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")


def parse_addresses(value: str | None) -> list[Address]:
    """All addresses in a From/To/Cc header, names decoded, emails lower-case. Never raises."""
    if not value:
        return []
    value = _fix_surrogates(re.sub(r"\r?\n[ \t]+", " ", str(value)))
    found: list[Address] = []
    try:
        pairs = email.utils.getaddresses([value])
    except Exception:  # noqa: BLE001 (never crash on odd server data)
        pairs = []
    for name, addr in pairs:
        if addr and "@" in addr:
            found.append(Address(addr.strip().lower(), decode_header_value(name).strip('"').strip()))
    if not found:  # strict parsers give up on messy headers: salvage anything that looks like an address
        found = [Address(m.lower()) for m in _EMAIL_RE.findall(decode_header_value(value))]
    return found


def _parse_date(value: str, internal: object) -> datetime | None:
    if value:
        try:
            return email.utils.parsedate_to_datetime(value)
        except Exception:  # noqa: BLE001, S110 (never crash on odd server data)
            pass
    return internal if isinstance(internal, datetime) else None


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def make_snippet(headers: dict[str, str], body: bytes) -> str:
    """Readable first ~500 characters of a (possibly cut-off) body. Prefers text/plain over HTML."""
    try:
        head = "".join(
            f"{k}: {headers[k]}\r\n" for k in ("content-type", "content-transfer-encoding") if headers.get(k)
        )
        msg = email.message_from_bytes(head.encode("utf-8", "replace") + b"\r\n" + body,
                                       policy=email.policy.compat32)
        plain = html_text = None
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True) or b""
            text = _decode_bytes(payload, part.get_content_charset())
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html_text is None:
                html_text = text
        text = plain if plain and plain.strip() else _strip_html(html_text or "")
    except Exception:  # noqa: BLE001 (never crash on odd server data)
        text = _strip_html(_decode_bytes(body))
    return re.sub(r"\s+", " ", text).strip()[:SNIPPET_CHARS]


def parse_copyuid(data: object) -> dict[int, int]:
    """'[COPYUID 38505 304,319:320 3956:3958]' (or just '38505 304,...') -> {304: 3956, 319: 3957, 320: 3958}."""
    text = _text(data)
    m = re.search(r"(?:COPYUID\s+)?(\d+)\s+([\d:,]+)\s+([\d:,]+)", text)
    if not m:
        return {}

    def expand(spec: str) -> list[int]:
        out: list[int] = []
        for piece in spec.split(","):
            if ":" in piece:
                a, b = (int(x) for x in piece.split(":"))
                out.extend(range(a, b + 1) if a <= b else range(a, b - 1, -1))
            elif piece:
                out.append(int(piece))
        return out

    old, new = expand(m.group(2)), expand(m.group(3))
    return dict(zip(old, new)) if len(old) == len(new) else {}


def _chunks(items: list[int], size: int = BATCH) -> Iterable[list[int]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


# ---- the backend ---------------------------------------------------------------------------------

class ImapBackend:
    """One IMAP account. Implements every method of `MailBackend` (see tacosort/backend.py)."""

    def __init__(
        self,
        account: AccountConfig,
        *,
        secret_getter: Callable[[str], str | None] = auth.get_secret,
        token_getter: Callable[[AccountConfig], str] = auth.get_access_token,
        timeout: int = 60,
        client_factory: Callable[..., IMAPClient] = IMAPClient,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], object] | None = None,
    ):
        self.account = account
        self._log = log or _logger.warning   # plain-English notes about things that half-worked
        self.account_id = account.id
        self._secret_getter = secret_getter
        self._token_getter = token_getter
        self._timeout = timeout
        self._client_factory = client_factory
        self._sleep = sleep
        self._client: IMAPClient | None = None
        self._secret = ""           # remembered only to scrub it out of error text
        self._delimiter: str | None = None
        self._prefix: str | None = None
        self._selected: tuple[str, bool] | None = None   # (folder, readonly)

        provider = PROVIDERS.get(account.provider, PROVIDERS["custom"])
        if account.host:
            self.host, self.port, self.ssl = account.host, account.port, account.ssl
        else:  # no server written in the config: use the provider's
            self.host, self.port, self.ssl = provider.host, provider.port, provider.ssl
        self.provider = provider

    # ---- connection -----------------------------------------------------------------------------
    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if _is_loopback(self.host):
            # Local bridges (Proton Mail Bridge) use their own self-made certificate. The connection
            # never leaves this computer, so the certificate is not checked for them.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def connect(self) -> None:
        """Log in. Raises BackendError with a plain-English message on failure."""
        self.close()
        if not self.host:
            raise BackendError(
                f"Account '{self.account_id}' has no IMAP server set. Run 'tacosort setup' and enter "
                "your provider's IMAP server name."
            )
        user = self.account.username or self.account.email
        if self.account.auth.startswith("oauth2"):
            secret = self._token_getter(self.account)   # raises CredentialError (a BackendError)
        else:
            secret = self._secret_getter(self.account_id) or ""
            if not secret:
                raise BackendError(
                    f"No password is saved for account '{self.account_id}'. Run 'tacosort setup' to "
                    "enter it (it is kept in your computer's keychain)."
                )
        self._secret = secret
        ctx = self._ssl_context()
        try:
            client = self._client_factory(self.host, port=self.port, ssl=self.ssl,
                                          ssl_context=ctx, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 (never crash on odd server data)
            raise BackendError(self._connect_hint(exc)) from None
        try:
            if not self.ssl:
                try:
                    client.starttls(ctx)
                except Exception as exc:  # noqa: BLE001 (never crash on odd server data)
                    raise BackendError(
                        f"The server {self.host} did not offer a secure (STARTTLS) connection, so "
                        f"TacoSort did not send your password. ({self._scrub(exc)})"
                    ) from None
            try:
                if self.account.auth.startswith("oauth2"):
                    client.oauth2_login(user, secret)
                else:
                    client.login(user, secret)
            except _DROPPED as exc:   # the line dropped: not a password problem
                raise BackendError(self._connect_hint(exc)) from None
            except Exception as exc:  # noqa: BLE001 (never crash on odd server data)
                raise BackendError(self._login_hint(exc)) from None
        except BackendError:
            self._quiet_logout(client)
            raise
        self._client = client
        self._selected = None

    def _connect_hint(self, exc: Exception) -> str:
        what = f"TacoSort could not connect to {self.host} port {self.port} ({type(exc).__name__}: {self._scrub(exc)})."
        if isinstance(exc, ssl.SSLError):
            return what + " The secure connection failed: check the port (993 = SSL, 143 = STARTTLS)."
        if isinstance(exc, socket.gaierror):
            return what + " The server name was not found: check the spelling and your internet connection."
        if _is_loopback(self.host):
            return what + " Is the bridge app (for example Proton Mail Bridge) running?"
        return what + " Check your internet connection, the server name and the port."

    def _login_hint(self, exc: Exception) -> str:
        detail = self._scrub(exc)
        lower = detail.lower()
        base = f"The server refused the login for account '{self.account_id}'. Server said: {detail}"
        key = self.provider.key
        if "not enabled for imap" in lower or "imap access is disabled" in lower:
            return (f"{base}\nIMAP is switched off for this mailbox. Look for an IMAP setting in your "
                    "mail's web settings; for a work or school account, ask your administrator to allow IMAP.")
        if self.account.auth.startswith("oauth2"):
            return (f"{base}\nThe sign-in token was not accepted. Run 'tacosort setup' to sign in again.")
        if key == "outlook":
            return (f"{base}\nMicrosoft no longer accepts passwords for Outlook.com/Hotmail IMAP. Run "
                    "'tacosort setup' and choose the Microsoft sign-in (oauth2-microsoft).")
        if key == "gmail" or "application-specific password" in lower:
            return (f"{base}\nGmail needs an App Password (16 letters), not your Google password: "
                    "https://myaccount.google.com/apppasswords (needs 2-Step Verification). Run "
                    "'tacosort setup' to enter it.")
        if key in ("yahoo", "aol", "icloud", "fastmail"):
            return (f"{base}\n{self.provider.name} needs an app password, not your normal password: "
                    f"{self.provider.app_password_url} . Run 'tacosort setup' to enter it.")
        if key == "protonmail-bridge":
            return f"{base}\nUse the Bridge password shown in the Proton Mail Bridge app, not your Proton password."
        return f"{base}\nCheck the user name and password (many providers want an app password)."

    def _scrub(self, exc: object) -> str:
        """Error text with any secret removed, short enough to show."""
        text = _text(exc.args[0]) if isinstance(exc, Exception) and exc.args else _text(exc)
        if self._secret:
            text = text.replace(self._secret, "********")
        return text.strip()[:300] or type(exc).__name__

    @staticmethod
    def _quiet_logout(client: IMAPClient) -> None:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 (never crash on odd server data)
            try:
                client.shutdown()
            except Exception:  # noqa: BLE001, S110 (never crash on odd server data)
                pass

    def close(self) -> None:
        """Log out; safe to call twice."""
        client, self._client = self._client, None
        self._selected = None
        if client is not None:
            self._quiet_logout(client)

    def _call(self, fn: Callable[[IMAPClient], object], what: str):
        """Run `fn(client)`; on a dropped connection reconnect once and retry. Errors -> BackendError."""
        for attempt in (1, 2):
            if self._client is None:
                self.connect()
            try:
                return fn(self._client)
            except BackendError:
                raise
            except _DROPPED as exc:
                self.close()
                if attempt == 2:
                    raise BackendError(
                        f"The connection to {self.host} keeps dropping while {what} "
                        f"({type(exc).__name__}). TacoSort will try again next run."
                    ) from None
            except imap_errors.IMAPClientError as exc:
                raise BackendError(f"The server said no while {what}: {self._scrub(exc)}") from None

    def _select(self, client: IMAPClient, folder: str, readonly: bool) -> None:
        if self._selected != (folder, readonly):
            client.select_folder(folder, readonly=readonly)
            self._selected = (folder, readonly)

    # ---- folders --------------------------------------------------------------------------------
    def list_folders(self) -> list[FolderInfo]:
        """Every folder with its LIST flags. Folders without special-use flags but with a well-known
        name (Sent Items, Deleted Items, Junk E-mail...) get the matching flag added."""
        rows = self._call(lambda c: c.list_folders(), "listing folders")
        out = []
        for flags, delim, name in rows:
            name = _text(name)
            delim_s = _text(delim)
            fl = {_text(f) for f in flags}
            # Server flags vary in case (\\sent vs \\Sent): normalise the ones TacoSort cares about.
            fl = {next((s for s in _SPECIAL if s.lower() == f.lower()), f) for f in fl}
            if not fl & _SPECIAL:
                last = name.rsplit(delim_s, 1)[-1] if delim_s else name
                guess = _NAME_FLAGS.get(last.strip().lower())
                if guess:
                    fl.add(guess)
            out.append(FolderInfo(name, delim_s or "/", frozenset(fl)))
        return out

    def _hierarchy(self, client: IMAPClient) -> tuple[str, str]:
        """(delimiter, personal namespace prefix), asked once per connection."""
        if self._delimiter is None:
            delim = ""
            try:
                rows = client.list_folders("", "")      # LIST "" "" returns the delimiter
                delim = _text(rows[0][1]) if rows else ""
            except Exception:  # noqa: BLE001, S110 (never crash on odd server data)
                pass
            if not delim:
                try:
                    rows = client.list_folders("", "INBOX")
                    delim = _text(rows[0][1]) if rows else ""
                except Exception:  # noqa: BLE001, S110 (never crash on odd server data)
                    pass
            self._delimiter = delim or "/"
        if self._prefix is None:
            prefix = ""
            if client.has_capability("NAMESPACE"):
                try:
                    personal = client.namespace().personal
                    if personal:
                        prefix = _text(personal[0][0])
                except Exception:  # noqa: BLE001, S110 (never crash on odd server data)
                    pass
            self._prefix = prefix
        return self._delimiter, self._prefix

    def ensure_folder(self, name: str) -> str:
        """Create `name` (and its parents) if missing, subscribe to new folders, return the server name.

        "/" becomes the server's delimiter, and the personal namespace prefix (for example "INBOX."
        on some Courier/Dovecot setups) is added in front when the server uses one."""

        def run(client: IMAPClient) -> str:
            delim, prefix = self._hierarchy(client)
            parts = [p for p in name.split("/") if p]
            if not parts:
                raise BackendError("A folder name was empty.")
            plain = delim.join(parts)
            existing = {_text(n) for _f, _d, n in client.list_folders()}
            # Already there (with or without the prefix, in any capitals)? Use the server's name.
            for candidate in (prefix + plain, plain):
                found = _same_folder(existing, candidate)
                if found:
                    return found
            if prefix and plain.upper().startswith(prefix.upper()):
                prefix = ""   # caller already gave a full name
            full = ""
            for i, part in enumerate(parts):
                full = f"{full}{delim}{part}" if i else prefix + part
                found = _same_folder(existing, full)
                if found:
                    full = found     # an existing parent keeps its own spelling
                    continue
                try:
                    client.create_folder(full)
                except imap_errors.IMAPClientError:
                    # Someone else may have just made it (maybe in other capitals): look again.
                    found = _same_folder({_text(n) for _f, _d, n in client.list_folders()}, full)
                    if not found:
                        raise
                    full = found
                    existing.add(full)
                    continue
                try:
                    client.subscribe_folder(full)
                except imap_errors.IMAPClientError:
                    pass  # subscribing is a nicety; some servers don't support it
                existing.add(full)
            return full

        return self._call(run, f"creating the folder '{name}'")

    def folder_state(self, folder: str) -> FolderState:
        """UIDVALIDITY, UIDNEXT and message count (STATUS: changes nothing).

        A server that leaves UIDNEXT out of its answer gets "highest UID + 1" from a search instead,
        because 0 would make TacoSort think the whole folder is new."""
        what = f"checking the folder '{folder}'"
        st = self._call(lambda c: c.folder_status(folder, ["UIDVALIDITY", "UIDNEXT", "MESSAGES"]), what)
        uidnext = int(st.get(b"UIDNEXT") or 0)
        if uidnext <= 0:
            uidnext = self._call(lambda c: self._highest_uid(c, folder), what) + 1
        return FolderState(int(st.get(b"UIDVALIDITY") or 0), uidnext, int(st.get(b"MESSAGES") or 0))

    def _highest_uid(self, client: IMAPClient, folder: str) -> int:
        self._select(client, folder, readonly=True)
        return max((int(u) for u in client.search("ALL")), default=0)

    # ---- reading --------------------------------------------------------------------------------
    def search_uids(self, folder: str, *, min_uid: int | None = None, limit: int | None = None) -> list[int]:
        """UIDs in `folder`, ascending; `min_uid` keeps UIDs >= it, `limit` keeps the newest n.

        Messages already marked \\Deleted are left out: they are on their way out (for example the
        originals of a COPY-fallback move on a server without UIDPLUS) and must not be sorted twice."""

        def run(client: IMAPClient) -> list[int]:
            self._select(client, folder, readonly=True)
            if min_uid is not None:
                found = client.search(["UID", f"{max(int(min_uid), 1)}:*", "UNDELETED"])
            else:
                found = client.search(["UNDELETED"])
            # "N:*" always includes the highest UID even when it is below N, so filter again.
            return sorted(int(u) for u in found if min_uid is None or int(u) >= min_uid)

        uids = self._call(run, f"searching '{folder}'")
        return uids[-limit:] if limit else uids

    def fetch(self, folder: str, uids: list[int], *, with_snippet: bool = False) -> list[Message]:
        """Envelopes + selected headers, read with BODY.PEEK on a read-only folder (never sets \\Seen)."""
        if not uids:
            return []
        out: list[Message] = []
        for batch in _chunks(sorted({int(u) for u in uids})):
            out.extend(self._call(lambda c, b=batch: self._fetch_batch(c, folder, b, with_snippet),
                                  f"reading messages in '{folder}'"))
        return out

    def _fetch_batch(self, client: IMAPClient, folder: str, uids: list[int], with_snippet: bool) -> list[Message]:
        self._select(client, folder, readonly=True)
        gmail = client.has_capability("X-GM-EXT-1")
        items = ["UID", "FLAGS", "RFC822.SIZE", "INTERNALDATE", HEADER_FETCH]
        if gmail:
            items.append("X-GM-LABELS")
        if with_snippet:
            items.append(SNIPPET_FETCH)
        data = client.fetch(uids, items)
        wanted = set(uids)
        messages = []
        for uid in uids:
            row = data.get(uid)
            if uid not in wanted or not row:
                continue
            raw_headers = _find_key(row, b"BODY[HEADER")
            if raw_headers is None:
                continue  # an unsolicited FLAGS update, not our answer
            messages.append(self._to_message(folder, uid, row, raw_headers, with_snippet))
        return messages

    def _to_message(self, folder: str, uid: int, row: dict, raw_headers: bytes, with_snippet: bool) -> Message:
        try:
            parsed = email.message_from_bytes(raw_headers or b"", policy=email.policy.compat32)
            pairs = list(parsed.items())
        except Exception:  # noqa: BLE001 (never crash on odd server data)
            pairs = []
        raw: dict[str, str] = {}
        for k, v in pairs:
            key = str(k).lower()
            if key not in raw:     # first occurrence wins
                raw[key] = str(v)
        headers = {k: decode_header_value(v) for k, v in raw.items()}
        senders = parse_addresses(raw.get("from")) or parse_addresses(raw.get("sender"))
        reply_to = parse_addresses(raw.get("reply-to"))
        labels = row.get(b"X-GM-LABELS") or ()
        snippet = ""
        if with_snippet:
            body = _find_key(row, b"BODY[TEXT")
            if body:
                snippet = make_snippet(raw, body)
        return Message(
            account=self.account_id,
            folder=folder,
            uid=uid,
            message_id=headers.get("message-id", "").strip(),
            subject=headers.get("subject", ""),
            from_=senders[0] if senders else Address(""),
            to=parse_addresses(raw.get("to")),
            cc=parse_addresses(raw.get("cc")),
            reply_to=reply_to[0] if reply_to else None,
            date=_parse_date(headers.get("date", ""), row.get(b"INTERNALDATE")),
            headers=headers,
            flags=frozenset(_text(f) for f in row.get(b"FLAGS", ())),
            gmail_labels=frozenset(_label(lb) for lb in labels),
            snippet=snippet,
            size=int(row.get(b"RFC822.SIZE") or 0),
        )

    def find_message_id(self, folder: str, message_id: str) -> list[int]:
        """UIDs in `folder` whose Message-ID header equals `message_id` exactly, skipping messages
        flagged \\Deleted (originals left behind by a move on a server without UIDPLUS)."""
        message_id = (message_id or "").strip()
        if not message_id:
            return []
        return self._call(lambda c: self._find_ids(c, folder, message_id), f"looking for a message in '{folder}'")

    def _find_ids(self, client: IMAPClient, folder: str, message_id: str) -> list[int]:
        self._select(client, folder, readonly=True)
        charset = None
        try:
            message_id.encode("ascii")
        except UnicodeEncodeError:
            charset = "UTF-8"
        # HEADER search matches substrings, so confirm each hit by reading its Message-ID.
        hits = sorted(int(u) for u in client.search(["HEADER", "Message-ID", message_id, "UNDELETED"], charset))
        if not hits:
            return []
        data = client.fetch(hits, ["FLAGS", MESSAGE_ID_FETCH])
        same = []
        for uid in hits:
            row = data.get(uid, {})
            raw = _find_key(row, b"BODY[HEADER")
            if raw is not None and not _is_deleted(row) and _message_id(raw) == message_id:
                same.append(uid)
        return same

    # ---- moving ---------------------------------------------------------------------------------
    def move(self, folder: str, uids: list[int], dest: str) -> dict[int, int | None]:
        """Move `uids` from `folder` to `dest`. Returns old uid -> new uid (None when unknown).

        MOVE when the server has it; otherwise COPY, then \\Deleted + UID EXPUNGE of exactly those
        UIDs (UIDPLUS). Without UIDPLUS the originals stay flagged \\Deleted and are NOT expunged,
        because a plain EXPUNGE would also remove anything else marked deleted in that folder.

        A UID missing from the result was not moved (or its outcome is unknown). If a later batch
        fails after earlier ones were moved, the earlier moves are still returned (so they are
        recorded and can be undone) and the problem is logged."""
        result: dict[int, int | None] = {}
        for batch in _chunks(sorted({int(u) for u in uids})):
            try:
                result.update(self._move_batch(folder, batch, dest))
            except BackendError as exc:
                if not result:
                    raise
                self._log(f"Moved {len(result)} message(s) from '{folder}' to '{dest}', then stopped: {exc}")
                break
        return result

    def _move_batch(self, folder: str, uids: list[int], dest: str) -> dict[int, int | None]:
        what = f"moving messages from '{folder}' to '{dest}'"
        # Reading first is safe to repeat: which UIDs are really here (not already on their way out),
        # and their Message-IDs, so a dropped connection can be checked instead of guessed.
        here = self._call(lambda c: self._peek(c, folder, uids), what)
        todo = sorted(u for u, (_mid, deleted) in here.items() if not deleted)
        ids = {u: here[u][0] for u in todo}
        result: dict[int, int | None] = {}
        for attempt in (1, 2):
            if not todo:
                break
            try:
                if self._client is None:
                    self.connect()
                result.update(self._move_now(self._client, folder, todo, dest))
                break
            except BackendError:
                raise
            except _Copied as done:
                result.update(self._finish_copy(folder, todo, dest, done))
                break
            except _DROPPED:
                self.close()
                moved, todo = self._after_drop(folder, todo, ids, dest)
                result.update(moved)
                if todo and attempt == 2:
                    self._log(f"The connection to {self.host} dropped twice while {what}; "
                              f"{len(todo)} message(s) stay where they are until the next run.")
            except imap_errors.IMAPClientError as exc:
                raise BackendError(f"The server said no while {what}: {self._scrub(exc)}") from None
        return result

    def _peek(self, client: IMAPClient, folder: str, uids: list[int]) -> dict[int, tuple[str, bool]]:
        """uid -> (Message-ID, flagged \\Deleted) for those `uids` that exist in `folder`."""
        self._select(client, folder, readonly=False)
        data = client.fetch(uids, ["FLAGS", MESSAGE_ID_FETCH])
        out: dict[int, tuple[str, bool]] = {}
        for uid in uids:
            row = data.get(uid)
            raw = _find_key(row, b"BODY[HEADER") if row else None
            if raw is not None:      # no header part: an unsolicited FLAGS update, not our answer
                out[uid] = (_message_id(raw), _is_deleted(row))
        return out

    def _move_now(self, client: IMAPClient, folder: str, uids: list[int], dest: str) -> dict[int, int | None]:
        """One MOVE (or COPY + delete step) on the live connection, with no retry of its own."""
        self._select(client, folder, readonly=False)
        _take_copyuid(client)  # forget any stale COPYUID from earlier commands
        if client.has_capability("MOVE"):
            response = client.move(uids, dest)
            mapping = parse_copyuid(response) or parse_copyuid(_take_copyuid(client))
            return {u: mapping.get(u) for u in uids}
        response = client.copy(uids, dest)
        mapping = parse_copyuid(response) or parse_copyuid(_take_copyuid(client))
        # From here on the copies exist: whatever goes wrong next, these messages count as moved.
        try:
            self._delete_originals(client, uids)
        except Exception as exc:  # noqa: BLE001 (a drop or a refusal: handled by _finish_copy)
            raise _Copied(mapping, exc) from None
        return {u: mapping.get(u) for u in uids}

    @staticmethod
    def _delete_originals(client: IMAPClient, uids: list[int]) -> None:
        """\\Deleted + UID EXPUNGE of exactly `uids` (no expunge at all without UIDPLUS). Safe to repeat."""
        client.add_flags(uids, [b"\\Deleted"], silent=True)
        if client.has_capability("UIDPLUS"):
            client.uid_expunge(uids)   # removes only these UIDs

    def _finish_copy(self, folder: str, uids: list[int], dest: str, done: _Copied) -> dict[int, int | None]:
        """COPY worked but the delete step did not. Finish it after a drop; either way report the
        messages as moved, because their copies are in `dest` and must be recorded (and undoable)."""
        moved: dict[int, int | None] = {u: done.mapping.get(u) for u in uids}
        problem = done.cause
        if isinstance(problem, _DROPPED):
            self.close()
            try:
                self._call(lambda c: (self._select(c, folder, readonly=False), self._delete_originals(c, uids)),
                           f"finishing the move from '{folder}' to '{dest}'")
                return moved
            except BackendError as exc:
                problem = exc
        self._log(
            f"TacoSort copied {len(uids)} message(s) from '{folder}' to '{dest}' but could not remove the "
            f"originals from '{folder}' ({self._scrub(problem)}). They are counted as moved; the originals "
            "stay where they were, and TacoSort will not copy them again."
        )
        return moved

    def _after_drop(self, folder: str, uids: list[int], ids: dict[int, str],
                    dest: str) -> tuple[dict[int, int | None], list[int]]:
        """The line dropped during MOVE/COPY: reconnect and look at what the server really did.

        Returns (moved: old uid -> new uid found in `dest`, safe to try again). A message counts as
        moved when `dest` has its Message-ID (not \\Deleted) and it left the source (MOVE), or a COPY
        clearly happened (then only the delete step is finished). It is tried again only when that is
        sure not to make a second copy. Anything unclear is left out (not moved / unknown)."""
        def run(client: IMAPClient) -> tuple[dict[int, int | None], list[int], list[int]]:
            uses_move = client.has_capability("MOVE")
            now = self._peek(client, folder, uids)
            moved: dict[int, int | None] = {}
            again: list[int] = []
            finish: list[int] = []
            for uid in uids:
                mid = ids.get(uid, "")
                still_here = uid in now and not now[uid][1]
                in_dest = self._find_ids(client, dest, mid) if mid else []
                if uses_move:
                    if still_here:
                        again.append(uid)                 # MOVE did not happen: repeating is safe
                    elif in_dest:
                        moved[uid] = max(in_dest)
                    elif mid:
                        # Gone from the source after the MOVE we sent, but dest's search index may lag
                        # (Gmail, Exchange): count it as moved so it is recorded; undo finds it by Message-ID.
                        moved[uid] = None
                elif in_dest:
                    moved[uid] = max(in_dest)             # the copy is there: never copy again
                    if uid in now:
                        finish.append(uid)
                elif still_here and mid:
                    again.append(uid)                     # no copy in dest: COPY did not happen
            return moved, again, finish

        moved, again, finish = self._call(
            run, f"checking a move from '{folder}' to '{dest}' after the connection dropped")
        if finish:
            mapping = {u: n for u, n in moved.items() if u in finish and n is not None}
            try:
                self._call(lambda c: (self._select(c, folder, readonly=False), self._delete_originals(c, finish)),
                           f"finishing the move from '{folder}' to '{dest}'")
            except BackendError as exc:
                self._finish_copy(folder, finish, dest, _Copied(mapping, exc))   # logs it; still moved
        return moved, again

    # ---- waiting for new mail -------------------------------------------------------------------
    def idle(self, folder: str, timeout: int) -> bool:
        """Wait up to `timeout` seconds for new mail in `folder`. IMAP IDLE when supported, else a
        STATUS poll every 30 seconds. True when new mail arrived; flag changes (the person reading a
        message on their phone) and deletions do not count."""
        if self._call(lambda c: c.has_capability("IDLE"), "checking for new mail"):
            return bool(self._call(lambda c: self._idle(c, folder, timeout), f"waiting for mail in '{folder}'"))
        return self._poll(folder, timeout)

    def _idle(self, client: IMAPClient, folder: str, timeout: int) -> bool:
        self._select(client, folder, readonly=True)
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            client.idle()
            try:
                responses = client.idle_check(timeout=min(left, MAX_IDLE))
            finally:
                client.idle_done()
            for resp in responses:
                if len(resp) >= 2 and _text(resp[1]).upper() in ("EXISTS", "RECENT"):
                    return True

    def _poll(self, folder: str, timeout: int) -> bool:
        before = self.folder_state(folder)
        deadline = time.monotonic() + max(timeout, 0)
        while time.monotonic() < deadline:
            self._sleep(min(POLL_EVERY, max(deadline - time.monotonic(), 0)))
            self._call(lambda c: c.noop(), "checking for new mail")
            now = self.folder_state(folder)
            if (now.uidnext, now.uidvalidity) != (before.uidnext, before.uidvalidity):
                return True
        return False


def _find_key(row: dict, prefix: bytes) -> bytes | None:
    """FETCH answers are keyed like b'BODY[HEADER.FIELDS (FROM ...)]': find one by its start."""
    for key, value in row.items():
        k = key if isinstance(key, bytes) else str(key).encode()
        if k.upper().startswith(prefix):
            return value if isinstance(value, bytes) else (_text(value).encode() if value else b"")
    return None


def _message_id(raw_headers: bytes) -> str:
    """The Message-ID from a fetched header block ("" when missing)."""
    try:
        found = email.message_from_bytes(raw_headers or b"", policy=email.policy.compat32).get("Message-ID", "")
    except Exception:  # noqa: BLE001 (never crash on odd server data)
        return ""
    return decode_header_value(found).strip()


def _is_deleted(row: dict) -> bool:
    return any(_text(f).lower() == "\\deleted" for f in row.get(b"FLAGS", ()))


def _same_folder(existing: set[str], name: str) -> str | None:
    """The server's own spelling of `name`: exact match first, else one differing only in capitals."""
    if name in existing:
        return name
    lower = name.lower()
    return next((e for e in sorted(existing) if e.lower() == lower), None)


def _label(value: object) -> str:
    """Gmail labels arrive in modified UTF-7 (IMAP folder encoding)."""
    if isinstance(value, bytes):
        try:
            return utf7_decode(value)
        except Exception:  # noqa: BLE001 (never crash on odd server data)
            return _decode_bytes(value)
    return str(value)


def _take_copyuid(client: IMAPClient) -> bytes:
    """Pop the COPYUID response code imaplib stored from the last COPY/MOVE (b'' when none)."""
    try:
        stored = client._imap.untagged_responses.pop("COPYUID", None)  # imaplib keeps response codes here
    except Exception:  # noqa: BLE001 (never crash on odd server data)
        return b""
    if not stored:
        return b""
    last = stored[-1]
    return last if isinstance(last, bytes) else _text(last).encode()
