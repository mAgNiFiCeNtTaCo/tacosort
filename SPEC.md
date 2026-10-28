# MailTidy: design spec (shared contract for every builder)

MailTidy keeps every email account a person has organized the same way, automatically, forever.
It is an open-source Python tool (MIT) meant for a public GitHub repo, used by non-programmers.

## Goals
1. **All accounts, one scheme.** Any IMAP account (Gmail, Outlook/Hotmail, Yahoo, iCloud, Fastmail,
   AOL, Zoho, GMX, custom servers). Every account gets the same folder taxonomy.
2. **Optimal organization.** The Inbox keeps only mail a human needs to see (people writing to you,
   security alerts, anything you marked important). Automated mail goes to clear category folders.
3. **Persistent.** (a) It keeps running: a background job (Windows Task Scheduler / macOS launchd /
   Linux systemd user timer or cron) or `mailtidy watch` (IMAP IDLE). (b) It remembers: a local SQLite
   state file holds what it did, per-folder UID watermarks, learned rules and an undo log. (c) It
   learns: from the user's existing folders (bootstrap) and from corrections (user moves a sorted
   message somewhere else or back to the Inbox -> the sender's rule changes, and it is never fought).
4. **Safe.** Never deletes, never marks as read (BODY.PEEK only), never touches Sent/Drafts/Trash/
   Junk/All Mail, never moves flagged/starred mail, every move is logged and undoable, first run is a
   dry-run preview. Passwords live in the OS keychain (keyring), never in the config file or logs.
5. **Plain-English UX.** `mailtidy setup` wizard, `mailtidy status`, `mailtidy report` (HTML).

## Default taxonomy (category key -> default folder)
Defined in `mailtidy/models.py` as `DEFAULT_CATEGORIES`. `inbox` means "leave it where it is".
| key | folder | what goes there |
|---|---|---|
| inbox | (stays) | people, replies, contacts you have written to, security alerts, starred/flagged |
| receipts | Receipts | order confirmations, invoices, receipts, shipping/delivery updates |
| finance | Finance | bank/card statements, bills, payment notices, tax, investments |
| travel | Travel | flights, hotels, car rental, bookings, itineraries |
| newsletters | Newsletters | subscribed content (List-Id/List-Unsubscribe, Substack, digests) |
| promotions | Promotions | marketing, sales, coupons, deals |
| social | Social | social networks, forums, community notifications |
| notifications | Notifications | automated app/service notices, account updates, calendar, dev tools |

Config can rename folders, add a parent (`folder_prefix: "MailTidy"` -> `MailTidy/Receipts`), disable
categories, or add custom ones that user rules target.

## Decision order (first confident answer wins), see `mailtidy/classify/`
1. Safety guards -> inbox: flagged/starred, sender is a known contact (address appears as recipient in
   the Sent folder), VIP list, message is a reply in a thread the user wrote in.
2. User rules from config (`rules:`), in order.
2b. Security and account messages (codes, password resets, sign-in alerts, "confirm your email") -> inbox,
   even when a learned rule files the rest of that sender's mail.
3. Learned rules (store): sender address, then sender domain, then List-Id. Rules guessed from existing
   folders (`explicit=False`) never move mail that looks written by a person.
4. Built-in heuristics (headers: List-Unsubscribe, List-Id, Precedence, Auto-Submitted, X-Mailer,
   ESP signatures, sender local-part like noreply/billing/receipts, subject keywords, known domains,
   Gmail category labels when the server exposes them).
5. Optional AI (off by default; Claude via the `anthropic` package, user's own API key; only for
   messages the earlier steps were unsure about; sends From/Subject/a short snippet only).
6. Default: inbox (when unsure, do nothing).
A decision below `min_confidence` (default 0.75) is treated as inbox.

## Package layout and OWNERSHIP (shared working tree: only edit files you own)
| path | owner station |
|---|---|
| `pyproject.toml`, `mailtidy/__init__.py`, `mailtidy/models.py`, `mailtidy/backend.py`, `mailtidy/testing.py`, `SPEC.md` | orchestrator (ask before changing; propose changes in your final report) |
| `mailtidy/imap/` (providers.py, client.py, auth.py), `tests/test_imap_*.py` | **IMAP** |
| `mailtidy/classify/` (pipeline.py, heuristics.py, rules.py, ai.py, contacts.py), `tests/test_classify_*.py` | **Classifier** |
| `mailtidy/store.py`, `mailtidy/learner.py`, `mailtidy/engine.py`, `mailtidy/schedule.py`, `tests/test_store*.py`, `tests/test_learner*.py`, `tests/test_engine*.py`, `tests/test_schedule*.py` | **Engine** |
| `mailtidy/cli.py`, `mailtidy/__main__.py`, `mailtidy/config.py`, `mailtidy/report.py`, `README.md`, `docs/`, `examples/`, `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `.github/`, `tests/test_cli*.py`, `tests/test_config*.py`, `tests/test_report*.py` | **CLI & Docs** |

## Contracts between stations (code to these; they are in models.py / backend.py)
- `MailBackend` (backend.py): the IMAP station implements it as `mailtidy.imap.client.ImapBackend`;
  `mailtidy.testing.FakeBackend` implements it in memory for everyone's tests.
- Classifier entry point: `mailtidy.classify.pipeline.Classifier(config: AppConfig, learned: LearnedRules,
  contacts: set[str])` with `.classify(msg: Message) -> Decision`. `LearnedRules` is a Protocol in
  models.py (the Engine's store implements it). AI is reached only through the pipeline.
- Engine entry points (engine.py):
  - `run_once(config, store, backends: dict[str, MailBackend], *, dry_run: bool, backlog: bool=False,
    log=print) -> RunReport`
  - `learn_from_folders(config, store, backends, *, log=print) -> LearnReport` (bootstrap from existing folders + Sent contacts)
  - `undo(config, store, backends, *, run_id: str|None=None, last: int|None=None, log=print) -> int`
  - `watch(config, store, backends_factory, *, log=print)` (IDLE loop, reconnects, calls run_once)
  - `start_from_now(config, store, backends, *, log=print)` marks never-sorted folders as handled up to
    now, so a plain `run` / `watch` / the schedule sorts only NEW mail (the backlog needs `--backlog`)
  - A run that finds another run holding the lock returns `RunReport(skipped=True)` (not an error).
  - `Store(path)` (store.py) owns SQLite; `store.learned` implements `LearnedRules`.
- Scheduler (schedule.py): `install(interval_minutes: int, config_path: Path) -> str`, `uninstall() -> str`,
  `status() -> str`. Windows schtasks (per-user, at logon + every N minutes, `pythonw -m mailtidy run`),
  macOS launchd agent, Linux systemd --user timer (cron fallback).
- Credentials (imap/auth.py): `get_secret(account_id) / set_secret(account_id, value) / delete_secret(account_id)`
  via keyring, service name `mailtidy` (failures raise `auth.CredentialError`, a `BackendError`). OAuth2 (XOAUTH2) for Gmail and Microsoft is optional; app
  passwords are the main path. Outlook.com/Hotmail need OAuth2 (Microsoft removed basic auth):
  device-code flow with a user-supplied client id, documented in docs/.
- Config (config.py): YAML at the platform config dir (`platformdirs` not used; Windows
  `%APPDATA%\MailTidy\config.yaml`, macOS `~/Library/Application Support/MailTidy/config.yaml`, Linux
  `$XDG_CONFIG_HOME/mailtidy/config.yaml`), override with `--config` or `MAILTIDY_CONFIG`.
  `load_config(path) -> AppConfig`, `save_config(cfg, path)`, `default_config_path()`, `default_data_dir()`.
  The SQLite state file lives in the data dir (`state.sqlite3`).

## Rules for builders
- Python >= 3.10, type hints, small readable modules, docstrings in plain English. Dependencies:
  `IMAPClient`, `keyring`, `PyYAML`; optional extra `ai` = `anthropic`. Nothing else without asking.
- Tests: pytest, fully offline (no real network, no real keychain: monkeypatch). Run `python -m pytest -q`.
- Never put real email addresses, passwords or tokens in code, tests or docs (use example.com).
- Only edit files you own (table above). Need a change in a file you don't own? Say so in your final
  report, with the exact change; the orchestrator applies it.
- Do not run git commands that change history (no commit/reset/checkout); the orchestrator commits.
