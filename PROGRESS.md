# TacoSort: progress log

TacoSort (called TidyInbox until 2026-10-28) is an open-source (MIT) Python tool that sorts every email account a person has (any IMAP
server, Outlook through OAuth2) into one shared folder scheme and keeps it sorted in the background.

## Where it stands (2026-10-28)
- Public at https://github.com/mAgNiFiCeNtTaCo/tacosort; people install it with
  `pipx install git+https://github.com/mAgNiFiCeNtTaCo/tacosort` (it is not on PyPI).
- Start: `tacosort setup` → `tacosort preview` → `tacosort run --backlog --yes` (old mail) →
  `tacosort schedule on` (keeps new mail sorted).
- Tests: `.venv\Scripts\python -m pytest -q`: 593 passed, 2 skipped (POSIX-only), ruff clean.
  GitHub CI green on Windows, macOS and Linux (run 40741390253; the Mac jobs got no runner 3 times on
  2026-10-28, a GitHub-side shortage: a re-run the same day passed).
- In real use on the owner's Gmail since 2026-10-28 (app password, background schedule every 15 min):
  Inbox 59 -> 14 (security alerts, codes and a few unsure ones stay).
- Untested for real: Outlook/Google OAuth, AI sorting against the live API, the Windows at-logon trigger.

## Log
### 2026-10-28: First real mailbox, Gmail tabs, company folders, Accounts folder
- **First real run** on the owner's Gmail (`tacosort setup` in a canvas terminal; the `!` prompt can't
  answer setup's questions). Setup moved 17 messages and turned the schedule on.
- **Found on real Gmail, fixed** (`22471de`):
  - Gmail never lists its tabs (Promotions, Updates, Social, Forums, Purchases) in X-GM-LABELS, so the
    "Gmail files it under ..." clue never fired and obvious ads stayed in the Inbox. The backend now runs an
    `X-GM-RAW category:<tab>` search per fetched batch and adds a `CATEGORY_*` label (purchases first).
  - GitHub's "A third-party OAuth application has been added" alert was filed in Notifications. New
    security phrases (OAuth / third-party apps, SSH keys, access tokens, Google account data) and
    "e-mail address verification" as an account check. That one message is still in Notifications:
    the owner was asked to drag it back.
- **Company folders** (owner's choice, `f6c8ea2`): a company with 3+ emails of one kind gets
  `<category>/<Company>` (Promotions/Crunchyroll, Receipts/Google Play). New `tacosort/companies.py`;
  name from the display name when it belongs to the domain, else `KNOWN_NAMES`, else the domain; stored
  once in the new `company_folders` table (schema 3). When a company first gets a folder, TacoSort's own
  earlier moves still in the category folder are moved in and their move records rewritten, so undo still
  returns them to the Inbox. Only heuristic/AI moves are tidied; rules, corrections and folder rules keep
  their folder. Settings `company_folders` / `company_folder_min`. Decided against AI-named topic folders
  (needs an API key, unpredictable names) and against moving security alerts out of the Inbox.
- **Accounts folder** (owner's choice): terms-of-service / privacy updates, account changes, welcome and
  plan notices. No-reply and Auto-Submitted clues count for Accounts as well as Notifications, else the
  runner-up penalty kept Accounts below 0.75.
- The background schedule ran mid-edit (12:50) with the new code: it tidied Receipts/Google Play and
  made **Notifications/Github** before `KNOWN_NAMES` existed, so that one folder keeps the spelling "Github".
- `tacosort run --backlog --yes` (owner said yes): 31 moved (PlayFame 8, Github 8, Crunchyroll 6,
  Samsung 3, Accounts 3, Notifications 2, Promotions 1); 14 stay; a new preview finds nothing to move.
- Preview's folder column widened (was cut at 18 characters). 593 tests pass.
- **Open:** the owner moving the OAuth alert back.
- **Resources (2026-10-28 13:02):** TacoSort idle between scheduled runs (0 processes, 89 MB on disk);
  whole PC CPU 18% of 32 threads, RAM 27.0 of 64 GB, GPU 38%.

### 2026-10-28: Renamed TidyInbox -> TacoSort
- The owner's other projects all carry "taco" in the name, so the tool is now **TacoSort** (picked from
  MailTaco / InboxTaco / TacoSort / TacoTidy, all free on PyPI; "tacomail" is taken there).
- Package folder `tidyinbox/` -> `tacosort/`, command `tacosort`, keychain service `tacosort`, data folder
  `%APPDATA%\TacoSort`, env vars `TACOSORT_*`, scheduled task / launchd / systemd names. Nobody had set it
  up yet, so no migration of old settings is needed.
- GitHub repo renamed to https://github.com/mAgNiFiCeNtTaCo/tacosort (the old tidyinbox link forwards).
- 567 tests pass, ruff clean.

### 2026-10-28: TidyInbox built by a nodeterm team, reviewed, fixed, published
- **Built** by four builder stations in the shared working tree (no git worktrees, as asked): IMAP
  backend (providers, keychain + OAuth, safe moves), classifier (guards, user rules, heuristics,
  optional AI), engine (SQLite store, learner, scheduler), CLI + docs + CI. An Integrator wired them
  together and found that the background schedule's command line would have failed on every run.
- **Design decisions:** never delete, never mark read (BODY.PEEK), never touch Sent/Trash/Junk/All
  Mail/Drafts or starred mail; preview before the first move; every move undoable. A plain `run` sorts
  only new mail (`start_from_now` baseline); old mail needs `--backlog`. Mail without a Message-ID is never
  moved. AI sorting (claude-haiku-5-5) is off by default and only sees bulk-looking mail. Passwords live
  in the OS keychain (service `tidyinbox`), never in config.yaml.
- **Review panel** (correctness, security, tests) found 3 blockers: Gmail-labelled Inbox mail learned
  as "move out"; a renumbered Inbox (UIDVALIDITY) would sweep kept mail; the README installed a PyPI
  name that belongs to someone else. Plus about 20 should-fix items.
- **Fix wave** (five stations) fixed them all; a fresh Re-check confirmed it and found two more:
  a folder-learned robot rule could file away security codes (now security/account mail stays in the
  Inbox before learned rules), and a MOVE whose reply was lost while the destination's search lagged was
  not recorded (now counted as moved when the message has left the source).
- **Renamed MailTidy → TidyInbox** (user's pick): "mailtidy" is taken on PyPI.
- **Published** to the public repo (user's choice). First CI run caught two things Windows never showed:
  run ids could collide (Linux) and overwrite an earlier run's record (now 8 random hex characters,
  `INSERT OR IGNORE` + retry); a cron test matched "old" inside a macOS temp path. Both fixed; CI green.
- **Open:** first real-account test by the user (Gmail app password); Proton Bridge certificate
  pinning, Gmail server-side filter sync, a PyPI release.
- **Resources (2026-10-28 12:05):** TidyInbox not running (0 processes, 0% CPU, 0 MB RAM, 88 MB on
  disk); whole PC CPU 20% of 32 threads, RAM 28.9 of 64 GB, GPU 33%.
