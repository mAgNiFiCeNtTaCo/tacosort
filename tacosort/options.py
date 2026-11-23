"""The tick-box settings the app shows, and how each one reads and writes AppConfig.

Kept apart from the window (app.py) so every option can be tested without a screen. An option is
either a plain tick box (a True/False setting) or a tick box with a number (hours, days, emails),
where unticked means the setting's "off" value (0).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from . import theme as themes
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
        Option("hold_hours", "Wait before filing new mail", "New mail stays in your Inbox this long, so you "
               "see it there first.", unit="hours", default=24, high=720),
        Option("recheck_inbox", "Look again once a day at mail left in the Inbox",
               "TacoSort keeps learning. Once a day it checks the Inbox again and files what it now knows a "
               "folder for."),
    ]),
    ("What stays in the Inbox", [
        Option("keep_flagged", "Keep mail you starred or flagged",
               "A star means \"this matters\", so TacoSort never moves it."),
        Option("keep_contacts", "Keep mail from people you have written to",
               "If you have sent someone an email, mail from them stays in your Inbox."),
    ]),
    ("Folders", [
        Option("company_folders", "Give a company that writes a lot its own folder",
               "For example a Crunchyroll folder inside Promotions, once it has sent a few emails."),
        Option("unsure_to_company", "Not sure what kind? Put it with that company's other mail",
               "When TacoSort can't tell what kind of email a company sent, it goes into that company's folder."),
        Option("unsure_to_folder", "Not sure what kind? Use the best guess",
               "Other company mail TacoSort can't place for certain goes to the folder that fits best."),
        Option("look_alike_min", "Spot senders that send the same kind of email again and again",
               "After this many look-alike emails, TacoSort treats the sender as a company, not a person.",
               unit="emails", default=5, low=2, high=1000),
    ]),
    ("Tidy old mail", [
        Option("security_keep_days", "Put away security emails after a few days", "Codes and sign-in alerts "
               "stay in the Inbox while they are fresh, then move to the Security folder.", unit="days",
               default=3, high=365),
        Option("business_keep_days", "Put away business emails from people after a few days", "They stay in "
               "the Inbox first so you can answer, then move to the Business folder.", unit="days", default=3,
               high=365),
        Option("old_mail_days", "Put away old emails you have already read", "Read emails left in the Inbox "
               "this long move to the Old mail folder. Unread and starred mail always stays.", unit="days",
               default=180),
    ]),
    ("Learning", [
        Option("learn_from_corrections", "Learn when I move an email back",
               "Drag an email back to your Inbox and TacoSort leaves that sender's mail there from then on."),
    ]),
]

# What each folder is for, in everyday words (models.DEFAULT_CATEGORIES has the exact wording the sorter uses).
FOLDER_WORDS = {
    "receipts": "Things you bought: orders, receipts and delivery updates.",
    "finance": "Money: bank and card statements, bills and payments.",
    "travel": "Trips: flights, hotels and bookings.",
    "newsletters": "Newsletters you signed up for.",
    "promotions": "Adverts: sales, coupons and deals.",
    "social": "Social apps and forums telling you something happened.",
    "notifications": "Automatic notices from apps and websites.",
    "accounts": "Notes about your accounts: new terms, welcome emails, plan changes. Security codes stay in "
                "the Inbox.",
    "business": "Paperwork for running a business: tax forms, seller sign-ups, payouts.",
}

ALL_OPTIONS: list[Option] = [o for _, group in GROUPS for o in group]


def categories(cfg: AppConfig) -> list[tuple[str, str, str]]:
    """(key, folder name, description) for every category that has a folder, in the usual order."""
    keys = [k for k in DEFAULT_CATEGORIES if k != INBOX] + [k for k in cfg.categories
                                                           if k != INBOX and k not in DEFAULT_CATEGORIES]
    out = []
    for key in keys:
        default_name, about = DEFAULT_CATEGORIES.get(key, (key.title(), ""))
        out.append((key, cfg.categories.get(key) or default_name, FOLDER_WORDS.get(key, about)))
    return out


@dataclass
class Values:
    """What the window shows: option field -> (ticked, number) and category key -> ticked."""

    options: dict[str, tuple[bool, int]]
    categories: dict[str, bool]
    interval_minutes: int
    theme: str = ""        # "" = leave the look as it is


def read(cfg: AppConfig) -> Values:
    opts: dict[str, tuple[bool, int]] = {}
    for o in ALL_OPTIONS:
        value = getattr(cfg, o.field)
        if o.unit:
            opts[o.field] = (value > 0, value if value > 0 else o.default)
        else:
            opts[o.field] = (bool(value), 0)
    cats = {key: bool(cfg.categories.get(key)) for key, _, _ in categories(cfg)}
    return Values(opts, cats, cfg.interval_minutes, cfg.theme)


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
        problems.append("Automatic sorting: pick between 5 and 1439 minutes.")
    else:
        changes["interval_minutes"] = interval
    if values.theme:
        if themes.problem(values.theme):
            problems.append("Pick a look from the list.")
        else:
            changes["theme"] = values.theme
    new = dataclasses.replace(cfg, **changes)
    if not problems:
        problems = config_problems(new)
    return new, problems
