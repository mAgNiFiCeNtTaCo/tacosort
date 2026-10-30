# TacoSort: progress log

TacoSort (called TidyInbox until 2026-10-28) is an open-source (MIT) Python tool that sorts every email account a person has (any IMAP
server, Outlook through OAuth2) into one shared folder scheme and keeps it sorted in the background.

## Where it stands (2026-10-29)
- Public at https://github.com/mAgNiFiCeNtTaCo/tacosort; people install it with
  `pipx install git+https://github.com/mAgNiFiCeNtTaCo/tacosort` (it is not on PyPI).
- Start: `tacosort setup` → `tacosort preview` → `tacosort run --backlog --yes` (old mail) →
  `tacosort schedule on` (keeps new mail sorted).
- Tests: `.venv\Scripts\python -m pytest -q`: 653 passed, 2 skipped (POSIX-only), ruff clean.
  GitHub CI green on Windows, macOS and Linux (run 40741390253; the Mac jobs got no runner 3 times on
  2026-10-28, a GitHub-side shortage: a re-run the same day passed).
- In real use on three of the owner's accounts (app passwords, background schedule every 15 min):
  personal Gmail Inbox 59 -> 1, SRGaming Gmail (card-selling business) 299 -> 18, Yahoo "Main"
  ~10,600 -> 174 (since 2026-10-29), plus mail younger than 24 hours. What stays: recent mail from
  people, replies, buyer messages, security mail under 3 days, unread or starred mail, and 84 Yahoo
  emails with no Message-ID (never moved).
- The owner's settings (not the defaults): `hold_hours: 24`, `unsure_to_folder: yes`,
  `security_keep_days: 3`, `business_keep_days: 3`, `old_mail_days: 180`, rule `*@lsoa.org` ->
  `School` (Yahoo only).
- Untested for real: Outlook/Google OAuth, AI sorting against the live API, the Windows at-logon trigger.

## Log
### 2026-10-29: Yahoo account, Old mail folder, two Yahoo quirks fixed
- **Yahoo "Main" added** (app password). Backlog sorted in 500-move runs (the owner said yes): about
  10,600 -> 1,450, 9,168 moves, School folder for `*@lsoa.org` (owner's choice: all of it).
- **Yahoo answers `HEADER.FIELDS` with six headers only** (From, To, Subject, Date, Message-ID,
  Content-Type), so every newsletter looked personal. On Yahoo/AOL (`XYMHIGHESTMODSEQ` or the host) the
  whole header is fetched and the usual fields kept (`2edc34d`).
- Shared senders (`ccsend.com`, `myshopify.com`): the company is the one named in the address or the
  label in front. Names: generic words (Feedback, Teacher, Office...) dropped; 53.com -> Fifth Third, AT&T,
  UPS, FedEx, USPS, AliExpress, Credit Karma (`b8b9bef`). Existing folders keep their names.
- **Tests no longer touch the owner's real settings, memory or log** (`tests/conftest.py`, `90330e3`).
- **Yahoo spells it "Inbox":** `start_from_now` looked only for "INBOX", so every Yahoo run logged in an
  extra time and logged "from now on TacoSort sorts new mail" (49 times). Fixed (`107cb66`).
- **Runs took 4-5 minutes with nothing to do.** Cause: 59 Yahoo emails have a Message-ID without
  `<...@...>`; Yahoo can't find a message by such an ID (search and `HEADER.FIELDS (MESSAGE-ID)` both come
  back empty, the full header has it). The correction check then searched every folder (~1 s per search
  on Yahoo, 0.2 s on Gmail), used up its 200-search budget on 1-3 emails, and began with the same ones
  next run. Now such a move is only looked for where it came from, and a move whose search runs out of
  budget waits behind the others (`e60547d`).
- **Old mail** (`eb2f3fe`, owner: "do all"): `old_mail_days` (default 0 = off) / `old_mail_folder`
  ("Old mail"). Read mail that would stay in the Inbox moves there once that old; never starred or unread
  mail, VIPs, or mail a user rule or learned rule keeps there; a dragged-back message stays. Preview: 0 on
  both Gmail accounts, 500+ on Yahoo. Real run: 1,265 moved, Yahoo Inbox 1,450 -> 174.
- Docs (README, SPEC, how-it-works, config example) cover `old_mail_days`; `/slop-check` diff clean
  (`AI_SlopAgent/reports/2026-10-29-Email_Organization.md`). CI green on every push checked.
- Resources 2026-10-29 19:29: TacoSort 0 processes between runs, 89 MB disk; whole PC CPU 11% of 32
  threads, RAM 28.3 of 64 GB, GPU 30%.
- **Open:** confirm the next scheduled Yahoo run is fast. Undo can't find the 59 Yahoo emails with a
  malformed Message-ID (it says so and leaves them; the recorded new UID pointed at a different message
  in one check, and undo only uses it after a sender + subject match). A fallback that searches by
  sender and compares the full header would close that.

### 2026-10-29: Second account, learning from repetition, 24-hour wait, Security and Business folders
- **Second account** SRGaming (Gmail, card-selling business) added through `tacosort setup` in the canvas
  terminal. Gmail said "app passwords not available" until 2-Step Verification was on. Typing the setup
  command with `nodeterm.sh write` failed at first: it eats backslashes (use `.venv/Scripts/...`).
- **Mac CI** was a GitHub runner shortage, not Actions minutes: a re-run passed (`a4a8281`).
- **Unsure company mail** (`f52cd77`): SRGaming kept 299 in the Inbox, ~250 of them Whatnot/eBay/TCGplayer
  mail scored 51-72% (needs 75%). Now machine-sent unsure mail joins its company's folder once the
  company has 3 such emails or already has a folder (`unsure_to_company`, default on). One folder per
  company, chosen by majority guess and stored (`company_folders` category `unsure`): identical Whatnot
  emails had split between Promotions and Notifications because Gmail's tab differed.
- **`hold_hours`** (owner: 24): new mail stays in the Inbox until it is that old (IMAP INTERNALDATE, new
  `Message.received`); held mail is not marked seen and the watermark waits, so a later run sorts it.
- **Kept in the Inbox:** a person writing through a marketplace ("Message from Buyer"), passkey
  notices, "we need more information", "confirm/verify your <brand> account".
- Folder names: "Kilian (TCGplayer)" -> "TCGplayer" (the part in brackets names the company).
- **Learning from repetition** (`8042af7`, the owner asked why 40 TCGplayer emails never got a folder):
  they came from `sales@tcgplayer.com` with no robot headers, so they counted as maybe-personal and were
  never moved on a guess. New table `look_alikes` (schema 4): an address with 5 emails of one subject
  template (first 5 words, numbers and prices blanked) or 10 emails in all counts as a robot
  (`look_alike_min`). `recheck_inbox` (default on): once a day a plain run re-classifies mail it left in
  the Inbox (never mail from before its first look). `unsure_to_folder` (default off, owner on): robot
  one-offs go to their best guess. The background run's first re-check (00:19) moved 46 by itself.
- **Security folder** (`641405e`, owner: after 3 days): `security_keep_days` / `security_folder`.
  Moved 25. Found by the daily re-check; starred and dragged-back mail stays.
- **Business folder** (`4cbe94d`, owner's ask): new category `business` (tax exemption, resale
  certificates, seller/distributor applications, business bank accounts, payouts). From a robot it is
  filed; from a person it waits `business_keep_days` (owner 3, default 0 = stays). `Decision.aged_to`
  ("security"/"business") replaces `.security`. The contact guard now skips mail with bulk markers:
  writing to `service@dacardworld.com` once had pinned all its promos and order mail to the Inbox.
  A one-off script moved 4 already-filed business emails out of Security/Accounts (move records
  rewritten, so undo still works).
- Real runs (owner said yes each time): 183, then 23, 25 and 19 moved; 10 emails in Business.
- Docs (README, SPEC, how-it-works, config example) cover every new setting; `/slop-check` diff clean
  (`AI_SlopAgent/reports/2026-10-28-Email_Organization.md`). CI green on all four pushes.
- Resources 2026-10-29 00:49: TacoSort 0 processes between runs, 89 MB disk; whole PC CPU 16% of 32
  threads, RAM 24.8 of 64 GB, GPU 40%.
- **Open:** running the tests writes a few lines into the owner's real `tacosort.log` (harmless, fix
  next). A company's unsure mail that trickles in one at a time waits for the daily re-check.

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
