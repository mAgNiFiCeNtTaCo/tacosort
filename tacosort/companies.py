"""Company sub-folders: once a company has sent `company_folder_min` messages of one kind, its mail of
that kind gets its own folder under the category folder ("Promotions/Crunchyroll").

A company is the sender's registrable domain (mail.crunchyroll.com -> crunchyroll.com). The folder's
name comes from the sender's display name when it clearly belongs to that domain ("Google Play" from
google.com), else from the domain itself ("Playfame"). The choice is saved, so a folder never changes
name. Only mail the classifier filed by category gets one: a user rule, a correction or a rule that names
a folder goes exactly where it says.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .models import INBOX, AppConfig, Decision, Message

# Second-level suffixes under which the registrable domain has three labels (example.co.uk).
_TWO_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "com.au", "net.au", "org.au",
    "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "com.br", "com.mx", "co.in", "co.za", "com.sg",
    "com.hk", "com.tw", "com.cn", "co.kr", "com.ar", "com.tr", "co.il", "com.my", "com.ph",
}
# Words in a display name that say what kind of mail it is, not who sent it ("GitHub Notifications").
_GENERIC_WORDS = {
    "team", "support", "notifications", "notification", "noreply", "no-reply", "newsletter", "news",
    "updates", "update", "alerts", "alert", "info", "hello", "mailer", "mail", "email", "billing",
    "marketing", "service", "services", "customer", "care", "official", "inc", "inc.", "llc", "ltd",
}
_TLDS = (".com", ".net", ".org", ".io", ".co", ".app", ".ai", ".dev")
# How companies spell themselves, for senders whose display name is a person or a repo ("GitHub" mail
# arrives as "octocat <notifications@github.com>"), so the folder isn't named "Github".
KNOWN_NAMES = {
    "github.com": "GitHub", "gitlab.com": "GitLab", "paypal.com": "PayPal", "linkedin.com": "LinkedIn",
    "youtube.com": "YouTube", "ebay.com": "eBay", "doordash.com": "DoorDash", "playstation.com": "PlayStation",
    "facebookmail.com": "Facebook", "tiktok.com": "TikTok", "openai.com": "OpenAI", "x.com": "X",
    "twitter.com": "X", "steampowered.com": "Steam", "epicgames.com": "Epic Games", "icloud.com": "iCloud",
    "bitbucket.org": "Bitbucket", "atlassian.net": "Atlassian", "ubereats.com": "Uber Eats",
    "airbnb.com": "Airbnb", "dropbox.com": "Dropbox", "hubspot.com": "HubSpot", "mailchimp.com": "Mailchimp",
    "whatsapp.com": "WhatsApp", "wordpress.com": "WordPress", "nytimes.com": "The New York Times",
    "booking.com": "Booking.com", "etsy.com": "Etsy", "twitch.tv": "Twitch", "redditmail.com": "Reddit",
    "discord.com": "Discord", "nintendo.com": "Nintendo", "xbox.com": "Xbox", "samsungusa.com": "Samsung",
}
MAX_NAME = 40


def company_key(email: str) -> str:
    """The sender's registrable domain, lower-case ("" when the address has none)."""
    domain = email.rsplit("@", 1)[-1].strip().lower().rstrip(".") if "@" in email else ""
    labels = [p for p in domain.split(".") if p]
    if len(labels) < 2:
        return ""
    keep = 3 if ".".join(labels[-2:]) in _TWO_PART_SUFFIXES and len(labels) >= 3 else 2
    return ".".join(labels[-keep:])


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def clean_name(display: str, key: str) -> str | None:
    """A folder-friendly company name from a display name, or None when the name doesn't clearly belong
    to the sender's domain (a person's name, a campaign slogan...)."""
    name = display.strip().strip("\"'")
    name = re.split(r"\s+via\s+|\s*[|,]\s*|\s+-\s+|\s+–\s+", name, maxsplit=1)[0].strip()
    words = [w for w in name.split() if w.lower().strip(".") not in _GENERIC_WORDS]
    name = " ".join(words).strip(" .-_")
    if not name or "@" in name:
        return None
    label = _alnum(key.split(".")[0])
    flat = _alnum(name)
    if not flat or not label or not (flat.startswith(label) or label.startswith(flat) or label in flat):
        return None
    return name


def folder_safe(name: str) -> str:
    """No folder separators or odd characters; "Booking.com" -> "Booking"."""
    for tld in _TLDS:
        if name.lower().endswith(tld):
            name = name[: -len(tld)]
    name = re.sub(r"[/\\.%*\"]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:MAX_NAME].strip()


def name_from_domain(key: str) -> str:
    return KNOWN_NAMES.get(key) or key.split(".")[0].capitalize()


def pick_name(displays: Iterable[str], key: str) -> str:
    """The most common display name that belongs to the domain, else the domain's own name."""
    counts = Counter(n for n in (clean_name(d, key) for d in displays) if n)
    name = counts.most_common(1)[0][0] if counts else name_from_domain(key)
    return folder_safe(name) or name_from_domain(key)


class CompanyFolders:
    """Plans company sub-folders for one account during one run.

    `assigned` maps (category, company) -> sub-folder (from the store plus any made this run), `counts`
    how many messages of that kind TacoSort has filed for the company. `new` lists assignments made in
    this run that the store doesn't have yet (saved by the engine unless it is a preview)."""

    def __init__(self, config: AppConfig, assigned: dict[tuple[str, str], str],
                 counts: dict[tuple[str, str], int]):
        self.config = config
        self.assigned = dict(assigned)
        self.counts = Counter(counts)
        self.names: dict[tuple[str, str], list[str]] = {}
        self.new: list[tuple[str, str, str]] = []

    @property
    def enabled(self) -> bool:
        return self.config.company_folders and self.config.company_folder_min > 0

    def _eligible(self, msg: Message, d: Decision | None) -> tuple[str, str] | None:
        if not self.enabled or d is None or d.folder or d.explicit or d.category in (INBOX, "folder"):
            return None
        if not self.config.folder_for(d.category) or d.confidence < self.config.min_confidence:
            return None
        key = company_key(msg.from_.email)
        return (d.category, key) if key else None

    def note(self, items: Iterable[tuple[Message, Decision | None]]) -> None:
        """Count a batch of decisions before any of them is placed, so a company that reaches the
        threshold inside this batch gets its folder for the whole batch."""
        for msg, d in items:
            k = self._eligible(msg, d)
            if k is None or k in self.assigned:
                continue
            self.counts[k] += 1
            self.names.setdefault(k, []).append(msg.from_.name)

    def folder_for(self, msg: Message, d: Decision | None) -> str | None:
        """The company sub-folder this message should go to, or None for the plain category folder."""
        k = self._eligible(msg, d)
        if k is None:
            return None
        if k not in self.assigned:
            if self.counts[k] < self.config.company_folder_min:
                return None
            base = self.config.folder_for(k[0])
            sub = f"{base}/{pick_name(self.names.get(k, []), k[1])}"
            self.assigned[k] = sub
            self.new.append((k[0], k[1], sub))
        return self.assigned[k]
