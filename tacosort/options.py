"""The tick-box settings the app shows, and how each one reads and writes AppConfig.

Kept apart from the window (app.py) so every option can be tested without a screen. An option is
either a plain tick box (a True/False setting) or a tick box with a number (hours, days, emails),
where unticked means the setting's "off" value (0).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .config import config_problems
from .models import DEFAULT_CATEGORIES, INBOX, AppConfig


@dataclass(frozen=True)
class Option:
    field: str             # AppConfig attribute
    label: str             # shown next to the tick box
    help: str = ""         # one plain sentence under it
    unit: str = ""         # "" = plain tick box; else the number's unit ("hours", "days", "emails")
    default: int = 0       # the number filled in when it is ticked on
    low: int = 1           # smallest number allowed while ticked
    high: int = 3650


GROUPS: list[tuple[str, list[Option]]] = [
    ("New mail", [
        Option("hold_hours", "Leave new mail in the Inbox for a while first", "So you see it there before it "
               "is filed.", unit="hours", default=24, high=720),
        Option("recheck_inbox", "Look again once a day at mail left in the Inbox",
               "What TacoSort learned since then may place it now."),
    ]),
    ("What stays in the Inbox", [
        Option("keep_flagged", "Starred or flagged mail"),
        Option("keep_contacts", "Mail from people you have written to"),
    ]),
    ("Folders", [
        Option("company_folders", "Give a company its own folder inside a category",
               "For example Promotions/Crunchyroll, once it has sent a few emails."),
        Option("unsure_to_company", "Robot mail TacoSort isn't sure about joins its company's folder"),
        Option("unsure_to_folder", "Other robot mail it isn't sure about goes to its best-guess folder"),
        Option("look_alike_min", "Treat an address that keeps sending look-alike emails as a robot",
               "Counts once it has sent this many of the same kind.", unit="emails", default=5, low=2,
               high=1000),
    ]),
    ("Tidy old mail", [
        Option("security_keep_days", "Move security emails to Security after", "Codes, sign-in alerts and "
               "\"verify your email\" stay in the Inbox until then.", unit="days", default=3, high=365),
        Option("business_keep_days", "Move people's business emails to Business after", unit="days",
               default=3, high=365),
        Option("old_mail_days", "Move read emails left in the Inbox to Old mail after",
               "Starred and unread mail always stays.", unit="days", default=180),
    ]),
    ("Learning", [
        Option("learn_from_corrections", "Learn when I move an email TacoSort filed",
               "Drag one back to the Inbox and that sender's mail stays there from then on."),
    ]),
]

ALL_OPTIONS: list[Option] = [o for _, group in GROUPS for o in group]


def categories(cfg: AppConfig) -> list[tuple[str, str, str]]:
    """(key, folder name, description) for every category that has a folder, in the usual order."""
    keys = [k for k in DEFAULT_CATEGORIES if k != INBOX] + [k for k in cfg.categories
                                                           if k != INBOX and k not in DEFAULT_CATEGORIES]
    out = []
    for key in keys:
        default_name, about = DEFAULT_CATEGORIES.get(key, (key.title(), ""))
        out.append((key, cfg.categories.get(key) or default_name, about))
    return out


@dataclass
class Values:
    """What the window shows: option field -> (ticked, number) and category key -> ticked."""

    options: dict[str, tuple[bool, int]]
    categories: dict[str, bool]
    interval_minutes: int


def read(cfg: AppConfig) -> Values:
    opts: dict[str, tuple[bool, int]] = {}
    for o in ALL_OPTIONS:
        value = getattr(cfg, o.field)
        if o.unit:
            opts[o.field] = (value > 0, value if value > 0 else o.default)
        else:
            opts[o.field] = (bool(value), 0)
    cats = {key: bool(cfg.categories.get(key)) for key, _, _ in categories(cfg)}
    return Values(opts, cats, cfg.interval_minutes)


def apply(cfg: AppConfig, values: Values) -> tuple[AppConfig, list[str]]:
    """A copy of `cfg` with the window's choices, and the problems that stop it being saved (plain
    English; empty when it is fine). Everything the window doesn't show (accounts, rules, VIPs, folder
    names you renamed) is kept as it was."""
    changes: dict[str, object] = {}
    problems: list[str] = []
    current = read(cfg).options
    for o in ALL_OPTIONS:
        ticked, number = values.options.get(o.field, current[o.field])
        if not o.unit:
            changes[o.field] = bool(ticked)
            continue
        if not ticked:
            changes[o.field] = 0
            continue
        try:
            n = int(number)
        except (TypeError, ValueError):
            problems.append(f'"{o.label}": the number of {o.unit} must be a whole number.')
            continue
        if not o.low <= n <= o.high:
            problems.append(f'"{o.label}": pick between {o.low} and {o.high} {o.unit}.')
            continue
        changes[o.field] = n
    cats = dict(cfg.categories)
    for key, ticked in values.categories.items():
        if ticked:
            cats[key] = cfg.categories.get(key) or DEFAULT_CATEGORIES.get(key, (key.title(), ""))[0]
        else:
            cats[key] = ""
    changes["categories"] = cats
    try:
        interval = int(values.interval_minutes)
    except (TypeError, ValueError):
        interval = 0
    if not 5 <= interval <= 1439:
        problems.append("Sort every: pick between 5 and 1439 minutes.")
    else:
        changes["interval_minutes"] = interval
    new = dataclasses.replace(cfg, **changes)
    if not problems:
        problems = config_problems(new)
    return new, problems
