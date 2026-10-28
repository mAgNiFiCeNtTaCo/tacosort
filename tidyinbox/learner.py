"""Learning from the user, so TidyInbox follows their habits instead of fighting them.

- `learn_from_folders`: looks at the folders the user already made ("Kids School", "Work"...) and at
  the Sent folder. A sender whose mail sits mostly in one folder (counting the Inbox too) gets a learned
  rule pointing at THAT folder; everyone the user writes to becomes a contact (their mail stays in the
  Inbox). Mail the person keeps in the Inbox is never learned as "move this out of the Inbox", and
  mail that looks written by a person does not make a folder rule (people belong in the Inbox).
- `refresh_contacts`: re-reads the newest Sent mail, so people the user started writing to later are
  protected too (the engine calls it once a day).
- `detect_corrections`: checks where the messages TidyInbox moved are now. Moved back to the Inbox ->
  that sender stays in the Inbox from now on, and that message is never moved again. Moved to another
  folder -> that sender's mail goes there from now on.
"""

from __future__ import annotations

import contextlib
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta

from .backend import BackendError, MailBackend
from .models import INBOX, AccountConfig, AppConfig, FolderInfo, LearnReport, Message
from .store import ANY_ACCOUNT, FOLDER_CATEGORY, Store, utcnow

# Folders that are never learned from (besides INBOX): mail there is not "filed" by the user.
SKIP_SPECIAL_USE = {"\\Sent", "\\Trash", "\\Junk", "\\All", "\\Drafts", "\\Important", "\\Flagged"}
# Gmail/Google Mail system folders, and common names of special folders on servers without flags.
SYSTEM_PREFIXES = ("[gmail]", "[google mail]")
SKIP_NAMES = {
    "inbox", "sent", "sent items", "sent mail", "sent messages", "drafts", "draft", "trash", "deleted",
    "deleted items", "deleted messages", "junk", "junk e-mail", "junk email", "spam", "bulk mail",
    "archive", "all mail", "outbox", "notes", "conversation history", "starred", "important",
}
# Big free mail providers: a domain rule for these would sweep up mail from real people.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "aol.com", "icloud.com", "me.com", "mac.com", "gmx.com", "gmx.net", "gmx.de",
    "web.de", "proton.me", "protonmail.com", "pm.me", "mail.com", "yandex.com", "yandex.ru",
    "zoho.com", "fastmail.com", "hey.com", "tutanota.com", "qq.com", "163.com",
}
NOREPLY_HINTS = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster",
                 "notifications", "notification")
# Servers with a personal namespace show the user's folders as "INBOX.Receipts" / "INBOX/Receipts".
NAMESPACE_PREFIX = "inbox/"

DEFAULT_SAMPLE = 300          # newest messages read per folder (the Inbox too)
SENT_SAMPLE = 1000            # newest messages read from Sent when learning
CONTACTS_REFRESH_SAMPLE = 200  # newest messages read from Sent by refresh_contacts
MIN_MESSAGES = 2              # a sender must appear at least this often
MIN_SHARE = 0.8               # ...with at least this share of their mail (Inbox included) in one folder
DOMAIN_MIN_MESSAGES = 5       # a domain rule needs clearly more evidence...
DOMAIN_MIN_SENDERS = 2        # ...from at least this many different addresses
CORRECTION_DAYS = 30          # how far back corrections are checked
MAX_LOOKUPS = 200             # folder searches per account per run when checking corrections
BATCH = 200

Log = Callable[[str], None]


def _quiet(_msg: str) -> None:
    pass


def is_learnable_folder(folder: FolderInfo) -> bool:
    """True for folders the user made to file mail: not INBOX, not a special or system folder."""
    if not folder.selectable or folder.flags & SKIP_SPECIAL_USE:
        return False
    name = folder.name.lower()
    if name.startswith(SYSTEM_PREFIXES):
        return False
    last = name.rsplit(folder.delimiter, 1)[-1] if folder.delimiter else name
    return name not in SKIP_NAMES and last not in SKIP_NAMES and name != "inbox"


def find_sent_folder(folders: list[FolderInfo]) -> str | None:
    for f in folders:
        if "\\Sent" in f.flags and f.selectable:
            return f.name
    by_name = {f.name.lower(): f.name for f in folders if f.selectable}
    for guess in ("sent", "sent items", "sent mail", "sent messages", "[gmail]/sent mail", "inbox.sent"):
        if guess in by_name:
            return by_name[guess]
    return None


def _norm(name: str, delimiter: str = "/") -> str:
    return (name.replace(delimiter, "/") if delimiter else name).strip().lower()


def _scheme_name(name: str, delimiter: str = "/") -> str:
    """Folder name for comparing with the scheme: lower case, "/" between levels, and without a
    personal namespace prefix ("INBOX.Receipts" and "INBOX/Receipts" both become "receipts")."""
    norm = _norm(name, delimiter)
    if norm.startswith(NAMESPACE_PREFIX) and len(norm) > len(NAMESPACE_PREFIX):
        norm = norm[len(NAMESPACE_PREFIX):]
    return norm


def category_for_folder(config: AppConfig, folder: str, delimiter: str = "/") -> str | None:
    """The category key whose configured folder is `folder` (case-insensitive, namespace prefix
    ignored), or None."""
    target = _scheme_name(folder, delimiter)
    for key in config.categories:
        name = config.folder_for(key)
        if name and _scheme_name(name) == target:
            return key
    return None


def _confidence(count: int, share: float) -> float:
    return round(min(0.97, 0.70 + 0.05 * count) * (0.5 + 0.5 * share), 3)


def _batches(items: list[int], size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _read(backend: MailBackend, folder: str, limit: int) -> list[Message]:
    uids = backend.search_uids(folder, limit=limit)
    return [m for chunk in _batches(uids) for m in backend.fetch(folder, chunk)]


def _automation_check() -> Callable[[Message], bool] | None:
    """The classifier's "looks machine-sent" test, or None when it isn't installed (then TidyInbox
    can't tell people from machines here and relies on the counts alone)."""
    try:
        from .classify.heuristics import automation_markers
    except ImportError:
        return None
    return lambda m: bool(automation_markers(m))


def _has_inbox_label(msg: Message) -> bool:
    """Gmail: the message still carries the Inbox label, so it is still in the Inbox."""
    return any(label.lower() == "\\inbox" for label in msg.gmail_labels)


def _inbox_folders(folders: list[FolderInfo], account: AccountConfig) -> list[str]:
    """The server names of INBOX and the account's other watched folders (mail the person keeps there
    is "in the Inbox" for learning)."""
    by_lower = {f.name.lower(): f.name for f in folders if f.selectable}
    out = [next((f.name for f in folders if f.name.upper() == "INBOX"), "INBOX")]
    for w in getattr(account, "watch_folders", None) or []:
        name = by_lower.get(w.lower())
        if name and name not in out:
            out.append(name)
    return out


def _contacts_fallback(messages: list[Message], own: set[str]) -> set[str]:
    out = set()
    for m in messages:
        for a in [*m.to, *m.cc]:
            email = a.email.lower()
            if "@" in email and email not in own and not any(h in a.local for h in NOREPLY_HINTS):
                out.add(email)
    return out


def contacts_from_sent(messages: list[Message], own: set[str]) -> set[str]:
    """Everyone the user wrote to, using the classifier's helper when it is installed."""
    try:
        from .classify.contacts import contacts_from_sent as classify_contacts
    except ImportError:
        found = _contacts_fallback(messages, own)
    else:
        found = {a.lower() for a in classify_contacts(messages)}
    return found - own


def _own_addresses(account: AccountConfig) -> set[str]:
    return {a.lower() for a in (account.email, account.username or account.email) if a}


def learn_account(config: AppConfig, store: Store, backend: MailBackend, account: AccountConfig, *,
                  sample: int | None = None, log: Log = print) -> LearnReport:
    """Learn from one account's own folders and Sent folder. `backend` must be connected."""
    sample = sample or getattr(config, "learn_sample", DEFAULT_SAMPLE) or DEFAULT_SAMPLE
    report = LearnReport()
    folders = backend.list_folders()
    own = _own_addresses(account)
    automated = _automation_check()
    delimiters = {f.name: f.delimiter for f in folders}

    # 1. What the person keeps in the Inbox: those senders must not be learned as "move it out".
    inbox_ids: set[str] = set()
    inbox_by_address: Counter = Counter()
    inbox_by_domain: Counter = Counter()

    def count_inbox(m: Message) -> None:
        if m.message_id:
            inbox_ids.add(m.message_id)
        email = m.from_.email
        if email and "@" in email:
            inbox_by_address[email] += 1
            if m.from_.domain:
                inbox_by_domain[m.from_.domain] += 1

    inbox_ok = True
    for name in _inbox_folders(folders, account):
        try:
            msgs = _read(backend, name, sample)
        except BackendError as e:
            report.errors.append(f"{account.id}: could not read {name!r}, so TidyInbox did not learn from "
                                 f"your folders this time ({e})")
            inbox_ok = False
            break
        for m in msgs:
            if not (m.message_id and m.message_id in inbox_ids):
                count_inbox(m)

    # 2. Where the person files each sender's mail.
    by_address: dict[str, Counter] = defaultdict(Counter)
    auto_by_address: dict[str, Counter] = defaultdict(Counter)
    by_domain: dict[str, Counter] = defaultdict(Counter)
    domain_senders: dict[tuple[str, str], set[str]] = defaultdict(set)

    for folder in folders if inbox_ok else []:
        if not is_learnable_folder(folder):
            continue
        try:
            msgs = _read(backend, folder.name, sample)
        except BackendError as e:
            report.errors.append(f"{account.id}: could not read the folder {folder.name!r} ({e})")
            continue
        report.folders_scanned += 1
        for m in msgs:
            report.messages_scanned += 1
            if store.moved_by_us(account.id, m.message_id):
                continue        # mail TidyInbox filed itself teaches nothing about the user's habits
            email = m.from_.email
            if not email or "@" not in email or email in own:
                continue
            if _has_inbox_label(m) or (m.message_id and m.message_id in inbox_ids):
                # Still in the Inbox (a Gmail label on Inbox mail, or a copy in both): the person keeps
                # it in the Inbox. Count it there once, never as "filed".
                if not (m.message_id and m.message_id in inbox_ids):
                    count_inbox(m)
                continue
            looks_automated = automated(m) if automated else True
            by_address[email][folder.name] += 1
            auto_by_address[email][folder.name] += int(looks_automated)
            domain = m.from_.domain
            if domain and domain not in FREEMAIL_DOMAINS and looks_automated:
                by_domain[domain][folder.name] += 1
                domain_senders[(domain, folder.name)].add(email)

    def store_rule(kind: str, key: str, folder: str, count: int, share: float) -> None:
        category = category_for_folder(config, folder, delimiters.get(folder, "/"))
        # Folder-learned rules are "guesses" (Decision.explicit=False, set by the store).
        if category is not None:
            # One of the scheme's own folders: the rule applies to every account.
            ok = store.learned.learn_from_folder(ANY_ACCOUNT, kind, key, category=category,
                                                 weight=_confidence(count, share), evidence=count)
        else:
            ok = store.learned.learn_from_folder(account.id, kind, key, category=FOLDER_CATEGORY,
                                                 folder=folder, weight=_confidence(count, share),
                                                 evidence=count)
        report.rules_learned += int(ok)

    for email, counts in by_address.items():
        folder, count = counts.most_common(1)[0]
        share = count / (sum(counts.values()) + inbox_by_address[email])
        # Mail that looks written by a person stays in the Inbox: a folder rule needs most of the
        # filed mail to look machine-sent (newsletters, receipts, notices).
        mostly_automated = auto_by_address[email][folder] * 2 >= count
        if count >= MIN_MESSAGES and share >= MIN_SHARE and mostly_automated:
            store_rule("address", email, folder, count, share)
    for domain, counts in by_domain.items():
        folder, count = counts.most_common(1)[0]
        share = count / (sum(counts.values()) + inbox_by_domain[domain])
        # A domain rule covers everyone at that domain, so it needs clearly more evidence: several
        # machine-sent messages from different addresses, and almost none of the domain's mail kept
        # in the Inbox.
        if (count >= DOMAIN_MIN_MESSAGES and share >= MIN_SHARE
                and len(domain_senders[(domain, folder)]) >= DOMAIN_MIN_SENDERS):
            store_rule("domain", domain, folder, count, share)

    sent = find_sent_folder(folders)
    if sent:
        try:
            found = contacts_from_sent(_read(backend, sent, SENT_SAMPLE), own)
            store.add_contacts(found, account=account.id)
            report.contacts_found += len(found)
        except BackendError as e:
            report.errors.append(f"{account.id}: could not read the Sent folder ({e})")
    log(f"{account.id}: read {report.folders_scanned} of your folders, learned {report.rules_learned} "
        f"sender rules, found {report.contacts_found} people you write to.")
    return report


def learn_from_folders(config: AppConfig, store: Store, backends: dict[str, MailBackend], *,
                       log: Log = print, sample: int | None = None) -> LearnReport:
    """Learn from every enabled account. One account failing does not stop the others."""
    total = LearnReport()
    for account in config.accounts:
        if not account.enabled:
            continue
        backend = backends.get(account.id)
        if backend is None:
            total.errors.append(f"{account.id}: no connection was set up for this account.")
            continue
        try:
            backend.connect()
            r = learn_account(config, store, backend, account, sample=sample, log=log)
        except BackendError as e:
            total.errors.append(f"{account.id}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 - one account must not stop the others
            total.errors.append(f"{account.id}: something unexpected went wrong while learning "
                                f"({type(e).__name__}: {e}).")
            continue
        finally:
            with contextlib.suppress(Exception):
                backend.close()
        total.folders_scanned += r.folders_scanned
        total.messages_scanned += r.messages_scanned
        total.rules_learned += r.rules_learned
        total.contacts_found += r.contacts_found
        total.errors.extend(r.errors)
    return total


def contacts_refreshed_key(account_id: str) -> str:
    """The store meta key holding when `refresh_contacts` last read this account's Sent folder."""
    return f"contacts_refreshed:{account_id}"


def refresh_contacts(store: Store, backend: MailBackend, account: str | AccountConfig, *,
                     limit: int = CONTACTS_REFRESH_SAMPLE, max_age: timedelta | None = None,
                     now: datetime | None = None) -> int:
    """Add the people the user wrote to recently (the newest `limit` Sent messages) to the contacts,
    so their mail stays in the Inbox. Returns how many people were new. `backend` must be connected;
    a mailbox problem raises BackendError. With `max_age`, nothing is read when the last refresh of
    this account is younger than that (so the engine can call it every run and it works once a day)."""
    account_id = account.id if isinstance(account, AccountConfig) else account
    own = _own_addresses(account) if isinstance(account, AccountConfig) else set()
    now = now or utcnow()
    key = contacts_refreshed_key(account_id)
    if max_age is not None:
        last = store.get_meta(key)
        with contextlib.suppress(ValueError, TypeError):
            if last and now - datetime.fromisoformat(last) < max_age:
                return 0
    sent = find_sent_folder(backend.list_folders())
    added = 0
    if sent:
        added = store.add_contacts(contacts_from_sent(_read(backend, sent, limit), own), account=account_id)
    store.set_meta(key, now.isoformat())
    return added


def _still_there(backend: MailBackend, folder: str, message_id: str) -> bool:
    """True when `folder` really holds the message: found by Message-ID and not flagged \\Deleted.
    On servers without UIDPLUS the original of a move stays in the Inbox flagged \\Deleted until it is
    expunged; that leftover is not "the person put it back"."""
    uids = backend.find_message_id(folder, message_id)
    if not uids:
        return False
    try:
        found = backend.fetch(folder, uids)
    except BackendError:
        return True          # can't double-check: trust the search (it already skips \Deleted)
    return any("\\Deleted" not in m.flags for m in found)


def detect_corrections(store: Store, backend: MailBackend, account: str | AccountConfig, *,
                       config: AppConfig | None = None, now: datetime | None = None,
                       max_lookups: int = MAX_LOOKUPS, log: Log = _quiet) -> int:
    """Find the user's hand corrections of recent moves and learn from them. Returns how many were
    found. `backend` must be connected. Work is bounded by `max_lookups` folder searches."""
    account_id = account.id if isinstance(account, AccountConfig) else account
    now = now or utcnow()
    moves = store.moves_to_check(account_id, now - timedelta(days=CORRECTION_DAYS), limit=max_lookups)
    if not moves:
        return 0
    folders = backend.list_folders()
    existing = {f.name: f for f in folders if f.selectable}
    inbox_name = next((n for n in existing if n.upper() == "INBOX"), "INBOX")

    managed: list[str] = []
    for mv in moves:
        if mv.to_folder not in managed:
            managed.append(mv.to_folder)
    if config is not None:
        for key in config.categories:
            name = config.folder_for(key)
            for f in existing.values():
                if name and _scheme_name(f.name, f.delimiter) == _scheme_name(name) and f.name not in managed:
                    managed.append(f.name)
    # Then the user's own folders (a correction may go to a folder they just made), never system ones.
    managed += [f.name for f in folders if is_learnable_folder(f) and f.name not in managed]

    lookups = 0
    found_count = 0
    for mv in moves:
        if lookups >= max_lookups:
            break
        if mv.to_folder in existing:
            lookups += 1
            if backend.find_message_id(mv.to_folder, mv.message_id):
                store.mark_move_checked(mv.id, "")
                continue
        where = None
        for folder in dict.fromkeys([mv.from_folder, inbox_name, *managed]):
            if folder == mv.to_folder or folder not in existing:
                continue
            lookups += 1
            if folder in (mv.from_folder, inbox_name):
                hit = _still_there(backend, folder, mv.message_id)
            else:
                hit = bool(backend.find_message_id(folder, mv.message_id))
            if hit:
                where = folder
                break
            if lookups >= max_lookups:
                break
        if where is None:
            if lookups < max_lookups:
                store.mark_move_checked(mv.id, "gone")     # deleted, or filed somewhere we don't watch
            continue
        if where in (mv.from_folder, inbox_name):
            # Put back: this sender belongs in the Inbox, and this message must never move again.
            store.learned.learn_correction(account_id, "address", mv.sender, category=INBOX)
            store.learned.learn_correction(ANY_ACCOUNT, "address", mv.sender, category=INBOX)
            store.mark_seen(account_id, mv.message_id, INBOX, override=True)
        else:
            category = category_for_folder(config, where, existing[where].delimiter) if config else None
            if category is not None:
                store.learned.learn_correction(account_id, "address", mv.sender, category=category)
                store.learned.learn_correction(ANY_ACCOUNT, "address", mv.sender, category=category)
            else:
                store.learned.learn_correction(account_id, "address", mv.sender, category=FOLDER_CATEGORY,
                                               folder=where)
            store.mark_seen(account_id, mv.message_id, f"moved:{where}", override=True)
        store.mark_move_checked(mv.id, f"corrected:{where}")
        found_count += 1
        log(f"{account_id}: you moved a message from {mv.sender} to {where}; TidyInbox will remember that.")
    return found_count
