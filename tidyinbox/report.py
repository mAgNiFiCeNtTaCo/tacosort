"""The HTML report (`tidyinbox report`): what TidyInbox moved, where, and why.

Everything that comes from an email (sender, subject, folder names) is untrusted text and is escaped
with html.escape before it goes into the page. The page is one self-contained file: no scripts, no
fonts, no images from the internet, so opening it never contacts anyone.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import datetime
from html import escape
from typing import Any

from .models import AppConfig, MoveRecord

RECENT_LIMIT = 200


# ---- reading the store (the Engine owns it; we only read, and survive missing helpers) --------------

def _call(store: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    """Call the first method of `store` that exists (None when there is no store or no such method).

    An older or simpler store may not take our keyword arguments; then it is called without them.
    """
    for name in names:
        fn = getattr(store, name, None)
        if callable(fn):
            try:
                return fn(*args, **kwargs)
            except TypeError:
                if not kwargs:
                    raise
                return fn(*args)
    return None


def _field(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def recent_moves(store: Any, limit: int = RECENT_LIMIT) -> list[Any]:
    """Newest-first moves (MoveRecord-like objects or dicts)."""
    rows = _call(store, ("recent_moves", "list_moves", "moves"), limit=limit)
    return list(rows or [])[:limit]


def last_run(store: Any) -> Any:
    return _call(store, ("last_run", "latest_run"))


def runs_with_moves(store: Any, limit: int = 30) -> list[Any]:
    """Recent real runs that moved something (newest first): their ids are what `undo --run` takes."""
    rows = _call(store, ("runs",), 200, dry_run=False) or []
    return [r for r in rows if _field(r, "moved", 0) and not _field(r, "dry_run", False)][:limit]


def learned_rules(store: Any) -> list[Any]:
    rows = _call(store, ("learned_rules", "list_learned"))
    if rows is None:
        rows = _call(getattr(store, "learned", None), ("all", "rules", "list"))
    return list(rows or [])


def corrections(store: Any, limit: int = 100) -> list[Any]:
    return list(_call(store, ("corrections", "list_corrections", "recent_corrections"), limit=limit) or [])


def contacts(store: Any) -> set[str]:
    return set(_call(store, ("contacts", "list_contacts", "get_contacts")) or [])


def moves_since(store: Any, since: datetime) -> int | None:
    n = _call(store, ("moves_since", "count_moves_since"), since)
    if isinstance(n, int):
        return n
    moves = recent_moves(store, limit=10_000)
    if not moves and n is None and not hasattr(store, "recent_moves"):
        return None
    count = 0
    for m in moves:
        at = _field(m, "at", None)
        if isinstance(at, datetime) and not _field(m, "undone", False) and _comparable(at, since) >= since:
            count += 1
    return count


def _comparable(at: datetime, ref: datetime) -> datetime:
    """Make naive/aware datetimes comparable (treat a naive one as the same zone as `ref`)."""
    if (at.tzinfo is None) != (ref.tzinfo is None):
        return at.replace(tzinfo=ref.tzinfo)
    return at


def move_counts(store: Any) -> Counter:
    """(account, folder) -> number of moves that are still in place."""
    raw = _call(store, ("move_counts", "folder_counts"))
    counts: Counter = Counter()
    if isinstance(raw, dict):
        for key, n in raw.items():
            if isinstance(key, tuple) and len(key) == 2:
                counts[(str(key[0]), str(key[1]))] += int(n)
        return counts
    for m in recent_moves(store, limit=100_000):
        if not _field(m, "undone", False):
            counts[(str(_field(m, "account")), str(_field(m, "to_folder")))] += 1
    return counts


# ---- page ------------------------------------------------------------------------------------------

_CSS = """
:root { --bg:#fbfbfa; --fg:#1d1d1f; --muted:#6b6b70; --line:#e3e3e0; --card:#ffffff; --accent:#2f6f4f; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151517; --fg:#ececee; --muted:#9a9aa2; --line:#2c2c31; --card:#1d1d21; --accent:#7fc9a0; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 32px 0 8px; }
p.sub { color: var(--muted); margin: 0 0 16px; }
.tiles { display:flex; flex-wrap:wrap; gap:12px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; min-width:150px; }
.tile b { display:block; font-size:24px; color:var(--accent); }
.tile span { color:var(--muted); font-size:13px; }
.wrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }
table { border-collapse: collapse; width:100%; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
tr:last-child td { border-bottom:0; }
td.num { text-align:right; font-variant-numeric: tabular-nums; }
td.subj { max-width: 380px; overflow-wrap:anywhere; }
.undone { color: var(--muted); text-decoration: line-through; }
.empty { color: var(--muted); padding: 12px; }
footer { color: var(--muted); font-size: 13px; margin-top: 32px; }
"""


def _e(value: Any) -> str:
    """Escape anything for HTML (None -> empty)."""
    if value is None:
        return ""
    return escape(str(value), quote=True)


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value or "")


def _table(headers: list[str], rows: list[list[str]], *, empty: str, classes: list[str] | None = None) -> str:
    if not rows:
        return f'<div class="wrap"><div class="empty">{_e(empty)}</div></div>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cls = classes[i] if classes and i < len(classes) and classes[i] else ""
            cells.append(f'<td class="{cls}">{cell}</td>' if cls else f"<td>{cell}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _rule_row(item: Any) -> list[str]:
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        item = dataclasses.asdict(item)
    if isinstance(item, (tuple, list)):
        item = dict(zip(("account", "kind", "key", "category", "folder", "weight", "updated"), item))
    target = _field(item, "folder") or _field(item, "category")
    return [
        _e(_field(item, "kind")),
        _e(_field(item, "key")),
        _e(target),
        _e(_field(item, "account") or "*"),
        _e(_field(item, "weight", "")),
        _e(_date(_field(item, "updated"))),
    ]


def build_report(store: Any, config: AppConfig) -> str:
    """The full HTML page as a string."""
    moves = recent_moves(store)
    counts = move_counts(store)
    learned = learned_rules(store)
    fixes = corrections(store)
    run = last_run(store)

    per_account: Counter = Counter()
    per_folder: Counter = Counter()
    for (acct, folder), n in counts.items():
        per_account[acct] += n
        per_folder[folder] += n

    tiles = [
        (sum(counts.values()), "messages tidied"),
        (len(config.accounts), "accounts"),
        (len(learned), "learned rules"),
        (len(fixes), "corrections you made"),
    ]
    tiles_html = "".join(f'<div class="tile"><b>{_e(n)}</b><span>{_e(label)}</span></div>' for n, label in tiles)

    if run:
        started = _date(_field(run, "started") or _field(run, "finished"))
        kind = "preview (nothing moved)" if _field(run, "dry_run", False) else "run"
        last = f"Last {kind}: {_e(started)}, moved {_e(_field(run, 'moved', 0))}."
    else:
        last = "TidyInbox hasn't run yet."

    emails = {a.id: a.email for a in config.accounts}
    acct_rows = [[_e(a), _e(emails.get(a, "")), _e(n)] for a, n in per_account.most_common()]
    folder_rows = [[_e(f), _e(n)] for f, n in per_folder.most_common()]

    move_rows = []
    for m in moves:
        undone = bool(_field(m, "undone", False))
        subj = _e(_field(m, "subject"))
        move_rows.append([
            _e(_date(_field(m, "at"))),
            _e(_field(m, "account")),
            _e(_field(m, "sender")),
            f'<span class="undone">{subj}</span>' if undone else subj,
            _e(_field(m, "to_folder")) + (" (undone)" if undone else ""),
            _e(_field(m, "reason")),
        ])

    run_rows = [[
        _e(_date(_field(r, "started"))),
        f"<code>{_e(_field(r, 'run_id'))}</code>",
        _e(_field(r, "moved", 0)),
        _e(_field(r, "scanned", 0)),
    ] for r in runs_with_moves(store)]

    fix_rows = [[
        _e(_date(_field(c, "at"))),
        _e(_field(c, "account")),
        _e(_field(c, "sender")),
        _e(_field(c, "from_folder")),
        _e(_field(c, "to_folder")),
    ] for c in fixes]

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        # No scripts, no remote resources: block them outright in case anything slips through.
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        "<title>TidyInbox report</title>",
        f"<style>{_CSS}</style></head><body><main>",
        "<h1>TidyInbox report</h1>",
        f'<p class="sub">Made {_e(generated)}. {last}</p>',
        f'<div class="tiles">{tiles_html}</div>',
        "<h2>By account</h2>",
        _table(["Account", "Email", "Moved"], acct_rows, empty="Nothing moved yet.", classes=["", "", "num"]),
        "<h2>By folder</h2>",
        _table(["Folder", "Moved"], folder_rows, empty="Nothing moved yet.", classes=["", "num"]),
        f"<h2>Last {len(move_rows)} moves</h2>" if move_rows else "<h2>Recent moves</h2>",
        _table(["When", "Account", "From", "Subject", "Moved to", "Why"], move_rows,
               empty="No moves yet. Run `tidyinbox preview` to see what would happen.",
               classes=["", "", "", "subj", "", ""]),
        "<h2>Runs</h2>",
        ('<p class="sub">Runs that moved mail. To put one run\'s mail back: '
         "<code>tidyinbox undo --run RUN-ID</code>, with the id from this table.</p>"),
        _table(["When", "Run id", "Moved", "Checked"], run_rows, empty="No runs have moved mail yet.",
               classes=["", "", "num", "num"]),
        "<h2>Your corrections</h2>",
        ('<p class="sub">Mail you moved somewhere else after TidyInbox sorted it. TidyInbox learns from these '
         "and never moves those messages again.</p>"),
        _table(["When", "Account", "From", "TidyInbox put it in", "You moved it to"], fix_rows,
               empty="No corrections yet."),
        "<h2>Learned rules</h2>",
        '<p class="sub">What TidyInbox picked up from your existing folders and your corrections.</p>',
        _table(["Kind", "Sender / list", "Goes to", "Account", "Evidence", "Updated"],
               [_rule_row(r) for r in learned], empty="Nothing learned yet. Try `tidyinbox learn`."),
        "<footer>TidyInbox never deletes mail. Undo the last run with <code>tidyinbox undo</code>.</footer>",
        "</main></body></html>",
    ]
    return "\n".join(parts)


def move_summary(m: MoveRecord | Any) -> str:
    """One plain line for a move (used by the CLI)."""
    return f"{_field(m, 'sender')}: {_field(m, 'subject')} -> {_field(m, 'to_folder')} ({_field(m, 'reason')})"
