"""Shared data types. Every part of TacoSort speaks in these, so keep them stable.

Owned by the orchestrator: propose changes instead of editing (see SPEC.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

# Category key that means "leave the message where it is" (normally the Inbox).
INBOX = "inbox"

# Default taxonomy: category key -> (default folder name, plain-English description).
DEFAULT_CATEGORIES: dict[str, tuple[str, str]] = {
    INBOX: ("", "Mail from people, replies, contacts, security alerts, starred mail: stays in the Inbox"),
    "receipts": ("Receipts", "Order confirmations, invoices, receipts, shipping and delivery updates"),
    "finance": ("Finance", "Bank and card statements, bills, payment notices, tax, investments"),
    "travel": ("Travel", "Flights, hotels, car rentals, bookings and itineraries"),
    "newsletters": ("Newsletters", "Newsletters and subscribed content"),
    "promotions": ("Promotions", "Marketing, sales, coupons and deals"),
    "social": ("Social", "Social networks, forums and community notifications"),
    "notifications": ("Notifications", "Automated notices from apps and services"),
    "accounts": ("Accounts", ("Terms-of-service and privacy-policy updates, account changes, welcome and "
                              "plan notices (security alerts and codes stay in the Inbox)")),
}

# Where a decision came from, strongest first.
SOURCES = ("guard", "user-rule", "learned", "heuristic", "ai", "default")


@dataclass(frozen=True)
class Address:
    """One email address. `email` is always lower-case."""

    email: str
    name: str = ""

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1] if "@" in self.email else ""

    @property
    def local(self) -> str:
        return self.email.rsplit("@", 1)[0] if "@" in self.email else self.email


@dataclass
class Message:
    """A message's envelope and selected headers. The body is never stored; `snippet` is optional."""

    account: str                      # AccountConfig.id
    folder: str                       # folder it was read from
    uid: int                          # IMAP UID inside `folder`
    message_id: str                   # Message-ID header ("" when missing)
    subject: str
    from_: Address
    to: list[Address] = field(default_factory=list)
    cc: list[Address] = field(default_factory=list)
    reply_to: Address | None = None
    date: datetime | None = None
    headers: dict[str, str] = field(default_factory=dict)   # lower-case header name -> decoded value
    flags: frozenset[str] = frozenset()                     # e.g. {"\\Seen", "\\Flagged"}
    gmail_labels: frozenset[str] = frozenset()              # Gmail only (X-GM-LABELS)
    snippet: str = ""                                       # first ~500 chars of plain text, if fetched
    size: int = 0

    @property
    def is_flagged(self) -> bool:
        return "\\Flagged" in self.flags

    @property
    def is_reply(self) -> bool:
        return bool(self.headers.get("in-reply-to") or self.headers.get("references"))


@dataclass
class Decision:
    """Where a message should go and why.

    The target folder is `folder or config.folder_for(category)`. A user "folder:<name>" rule gives
    category INBOX with `folder=name`; a learned rule for one of the user's own folders gives
    category "folder" with `folder` set. `folder` is used as-is (no folder_prefix).
    """

    category: str                 # key in AppConfig.categories, INBOX, or "folder" (see above)
    confidence: float             # 0..1
    source: str                   # one of SOURCES
    reason: str                   # plain English, shown in reports ("List-Unsubscribe header")
    folder: str | None = None     # explicit target folder (user rule / learned rule may name any folder)
    # True when the person taught this directly (a user rule, or a correction: they moved a message
    # TacoSort sorted). Rules guessed from existing folders are False: they may not move mail that
    # looks written by a person.
    explicit: bool = False

    @property
    def keeps(self) -> bool:
        return self.category == INBOX and not self.folder


@dataclass(frozen=True)
class FolderInfo:
    name: str                     # full server name, e.g. "INBOX", "[Gmail]/Sent Mail", "Receipts"
    delimiter: str = "/"
    flags: frozenset[str] = frozenset()   # IMAP LIST flags incl. special-use (\\Sent, \\Trash, \\Junk, \\All, \\Drafts)

    @property
    def special_use(self) -> str | None:
        for f in ("\\Sent", "\\Trash", "\\Junk", "\\All", "\\Drafts", "\\Archive", "\\Flagged", "\\Important"):
            if f in self.flags:
                return f
        return None

    @property
    def selectable(self) -> bool:
        return "\\Noselect" not in self.flags and "\\NonExistent" not in self.flags


@dataclass(frozen=True)
class FolderState:
    uidvalidity: int
    uidnext: int
    exists: int


@dataclass
class AccountConfig:
    """One mailbox. Secrets are NOT here: they live in the OS keychain under `id`."""

    id: str                               # short unique name, e.g. "gmail-personal"
    email: str
    provider: str = "custom"              # key in tacosort.imap.providers.PROVIDERS
    host: str = ""
    port: int = 993
    ssl: bool = True
    username: str = ""                    # defaults to email
    auth: str = "password"                # "password" | "oauth2-google" | "oauth2-microsoft"
    oauth_client_id: str = ""             # only for oauth2-* (not secret for public clients)
    watch_folders: list[str] = field(default_factory=lambda: ["INBOX"])
    enabled: bool = True


@dataclass
class Rule:
    """A user rule. All given conditions must match (AND); list values match if any item matches (OR).

    Matching is case-insensitive. `action` is a category key, "inbox", or "folder:<name>".
    """

    name: str
    action: str
    from_: list[str] = field(default_factory=list)          # exact addresses or "*@domain.com" / "*.domain.com"
    to: list[str] = field(default_factory=list)
    subject: list[str] = field(default_factory=list)        # substrings
    list_id: list[str] = field(default_factory=list)        # substrings of List-Id
    header: dict[str, str] = field(default_factory=dict)    # header name -> substring
    accounts: list[str] = field(default_factory=list)       # limit to these account ids (empty = all)


@dataclass
class AIConfig:
    enabled: bool = False
    model: str = "claude-haiku-5-5"
    max_per_run: int = 50                 # cost guard
    send_snippet: bool = True             # send a short body snippet (else From/Subject only)
    api_key_env: str = "ANTHROPIC_API_KEY"  # or a keychain entry named "anthropic"


@dataclass
class AppConfig:
    accounts: list[AccountConfig] = field(default_factory=list)
    # category key -> folder name ("" for inbox). Missing keys use DEFAULT_CATEGORIES; a key set to
    # None/"off" in YAML is disabled (its mail stays in the Inbox).
    categories: dict[str, str] = field(
        default_factory=lambda: {k: v[0] for k, v in DEFAULT_CATEGORIES.items()}
    )
    folder_prefix: str = ""               # e.g. "TacoSort" -> "TacoSort/Receipts"
    rules: list[Rule] = field(default_factory=list)
    vip: list[str] = field(default_factory=list)       # addresses/domains that always stay in the Inbox
    min_confidence: float = 0.75
    keep_flagged: bool = True
    keep_contacts: bool = True            # people you have written to stay in the Inbox
    learn_from_corrections: bool = True
    ai: AIConfig = field(default_factory=AIConfig)
    interval_minutes: int = 15
    max_moves_per_run: int = 500          # safety cap per account per run
    company_folders: bool = True          # a sub-folder per company ("Promotions/Crunchyroll")...
    company_folder_min: int = 3           # ...once it has sent this many messages of that kind
    learn_sample: int = 300               # messages read per folder when learning the user's scheme
    data_dir: str = ""                    # "" = platform default

    def folder_for(self, category: str) -> str | None:
        """Full folder name for a category, or None when it stays put / is disabled."""
        if category == INBOX:
            return None
        name = self.categories.get(category)
        if not name:
            return None
        return f"{self.folder_prefix}/{name}" if self.folder_prefix else name


class LearnedRules(Protocol):
    """What the classifier needs from learned state (the Engine's Store implements this)."""

    def lookup(self, sender: str, domain: str, list_id: str, account: str) -> Decision | None:
        """Best learned decision for this sender (address > list-id > domain), or None."""
        ...


@dataclass
class MoveRecord:
    run_id: str
    account: str
    message_id: str
    sender: str
    subject: str
    from_folder: str
    to_folder: str
    category: str
    source: str
    reason: str
    at: datetime
    new_uid: int | None = None
    undone: bool = False


@dataclass
class RunReport:
    run_id: str
    dry_run: bool
    started: datetime
    finished: datetime | None = None
    scanned: int = 0
    moved: int = 0
    kept: int = 0
    learned: int = 0                      # corrections picked up this run
    moves: list[MoveRecord] = field(default_factory=list)    # planned moves when dry_run
    errors: list[str] = field(default_factory=list)          # per-account problems, plain English
    skipped: bool = False                 # another run held the lock; nothing was done (not an error)


@dataclass
class LearnReport:
    folders_scanned: int = 0
    messages_scanned: int = 0
    rules_learned: int = 0
    contacts_found: int = 0
    errors: list[str] = field(default_factory=list)
