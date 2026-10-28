# TidyInbox: progress log

TidyInbox is an open-source (MIT) Python tool that sorts every email account a person has (any IMAP
server, Outlook through OAuth2) into one shared folder scheme and keeps it sorted in the background.

## Where it stands (2026-10-28)
- Public at https://github.com/mAgNiFiCeNtTaCo/tidyinbox; people install it with
  `pipx install git+https://github.com/mAgNiFiCeNtTaCo/tidyinbox` (it is not on PyPI).
- Start: `tidyinbox setup` → `tidyinbox preview` → `tidyinbox run --backlog --yes` (old mail) →
  `tidyinbox schedule on` (keeps new mail sorted).
- Tests: `.venv\Scripts\python -m pytest -q`: 567 passed, 2 skipped (POSIX-only), ruff clean.
  GitHub CI (Windows, macOS, Linux × Python 3.10 and 3.13) green on run 40655350677.
- Not yet tried on a real mailbox. Also untested for real: Outlook/Google OAuth, AI sorting against the
  live API, the Windows at-logon trigger.

## Log
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
