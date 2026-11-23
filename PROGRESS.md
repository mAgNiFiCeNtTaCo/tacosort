# TacoSort: progress log

TacoSort (called TidyInbox until 2026-10-28) is an open-source (MIT) Python tool that sorts every email account a person has (any IMAP
server, Outlook through OAuth2) into one shared folder scheme and keeps it sorted in the background.

## Where it stands (2026-11-23)
- Public at https://github.com/mAgNiFiCeNtTaCo/tacosort. **Release 0.2.1** (2026-11-23,
  https://github.com/mAgNiFiCeNtTaCo/tacosort/releases/tag/v0.2.1; 0.2.0 the same day, 0.1.0 2026-10-29): a one-file app
  for Windows (15 MB), macOS Apple silicon (zip) and Linux, with `SHA256SUMS.txt`. With Python: `pipx
  install git+https://github.com/mAgNiFiCeNtTaCo/tacosort` (it is not on PyPI); `tacosort app` opens the
  window.
- **Plain words** (2026-11-23, in 0.2.1): every button has a line saying what it does, every switch and
  folder a line under it, Home has a three-step start guide, and there is a Help page; the window and the
  phone page say "automatic sorting".
- **The window** (2026-11-21): a side bar with Home / Sort / Accounts / Settings / Help, rounded cards, switches,
  and seven looks (System, Light, Dark, Midnight, Taco, Rose, Contrast; setting `theme`). The phone page
  has the same looks. Drawn with Tkinter alone, no new package.
- **Phone page** (2026-10-31, released in 0.2.0): `tacosort phone on` / the app's Settings page
  shares it with the owner's own Tailscale devices (127.0.0.1:8747 behind `tailscale serve`). On for the
  owner since 2026-10-31, starting with Windows. Since 2026-11-01 it answers only Tailscale itself or the
  owner's own programs, and stops through `POST /api/stop`.
- **Live copy** (2026-11-01): the owner's task, phone page and Desktop + Start menu shortcuts run from
  `%LOCALAPPDATA%\Programs\TacoSort\venv`, not the project folder; update it after a change (CLAUDE.md).
  It is at 0.2.1 (commit ed4b910) since 2026-11-23, with imapclient 4.3.0 and PyYAML 6.0.4, on Python
  3.14.10.
- Security audit and slop check 2026-11-01: all findings fixed (b7fa6d0); releases from 0.2.0 on carry
  `SHA256SUMS.txt` and a build attestation.
- Start: `tacosort setup` → `tacosort preview` → `tacosort run --backlog --yes` (old mail) →
  `tacosort schedule on` (keeps new mail sorted).
- Tests: `.venv\Scripts\python -m pytest -q`: 745 passed, 2 skipped (POSIX-only), ruff clean.
  GitHub CI green on Windows, macOS and Linux (2026-11-23, commit a4136f0). CI jobs stop after 20 minutes.
- In real use on three of the owner's accounts (app passwords, background schedule every 15 min):
  personal Gmail Inbox 59 -> 1, business Gmail 299 -> 18, Yahoo "Main"
  ~10,600 -> 174 (since 2026-10-29), plus mail younger than 24 hours. What stays: recent mail from
  people, replies, buyer messages, security mail under 3 days, unread or starred mail, and 84 Yahoo
  emails with no Message-ID (never moved).
- The owner's settings (not the defaults): `hold_hours: 24`, `unsure_to_folder: yes`,
  `security_keep_days: 3`, `business_keep_days: 3`, `old_mail_days: 180`, a rule for the school's domain ->
  `School` (Yahoo only).
- Untested for real: Outlook/Google OAuth, AI sorting against the live API, the Windows at-logon trigger.

## Log
### 2026-11-23: Updates (packages, GitHub actions, Python 3.14.10) and release-tag protection
- Owner's asks: "do all updates and tag protection", then "update yes", "python update now".
- **Build packages (a4136f0):** `packaging/requirements-build.txt` remade with `uv pip compile --universal
  --upgrade`: imapclient 4.3.0, PyYAML 6.0.4, pyinstaller-hooks-contrib 2026.10. Its comments named a temp
  path with the PC's user name; they now name `packaging/requirements-build.in`.
- **Dependabot's two imapclient requests (#5, #6) not used:** they remade the pinned list on Linux only,
  which dropped pefile, pywin32-ctypes and macholib and would have broken the Windows and macOS builds.
  #5's failed check was a macOS 3.10 job that hit the time limit, not the change. CLAUDE.md says how to
  remake the list.
- **GitHub actions:** checkout 7.0.1, setup-python 7.1.0, upload-artifact 7.0.2, download-artifact 8.0.2,
  pinned by commit and each checked against its tag. Requests #1-#4 closed themselves. A trial build
  (run 45131683368) passed on Windows, macOS and Linux; the download step only runs on a real release, so
  the next tag is its first real run (its inputs are unchanged).
- **Tag protection:** GitHub ruleset "Release tags stay put" (id 27881516): `v*` tags can't be deleted,
  moved or force-updated, no bypass. A bad release gets a new version number.
- **Live copy:** imapclient 4.3.0 and PyYAML 6.0.4 installed; a read-only preview read all four mailboxes.
  pip left a `~aml` folder in its site-packages (the phone page had the old file open); harmless.
- **Python 3.14.7 -> 3.14.10 on this PC** (owner chose "Yes, stop and restart all", then "Yes, stop the
  bot too"). Stopped: TacoVault server, Bathroom, Ship Desk, Document Vault, tacocam, the BabyG panel,
  TacoSort's phone page, and the BabyG bot (through its keeper: `panel_stop.request`, wait for "The bot
  is off", then the task). The first try failed with "files are still in use": the bot and its keeper run
  hidden in session 0 and show no program path, so the first check missed them. All eight started again
  (bot offline about 35 s; tacocam closed itself after its first start and stayed up on the second, no
  error in its log). TacoSort's schedule was held off during the update. 745 tests pass on 3.14.10.
- **Outside this project, at the owner's request:** on taco-server, winget updates (Git 2.56.1, Python
  Launcher 3.15.2, Teams, App Installer); the subscriber bot kept running, no restart needed. Python there
  was already 3.14.10 and the bot's code current. Left alone: three optional driver / firmware updates
  (best done with a screen attached) and the bot's pinned davey / numpy (for the BabyG project's next
  update, davey with a voice test). Tailscale on this PC 1.104.6 -> 1.104.7; it kept the name `taco`.
  Not recorded in those projects' own notes.
- **Open:** written release notes for 0.2.0 / 0.2.1; phone autostart on macOS / Linux.
- Resources (2026-11-23 12:51, task + phone page running): 2 processes, 36 MB RAM, 0% CPU, port 8747,
  disk 178 MB; whole PC CPU 5% of 32 threads, RAM 27.5 of 64 GB, GPU 28%.

### 2026-11-23: Plain words everywhere, a start guide and a Help page; release 0.2.1
- Owner's ask: "lets make this super easy for a dum person or 5 year old could understand how to use each
  thing in the app". Done for everyone, with no "easy mode" switch (c90e261).
- **Buttons explain themselves:** on Sort and Accounts each button sits in a row with a line saying what
  pressing it does (`App._actions`). Smaller buttons, the side bar and the colour dots show a note when the
  mouse rests on them (`widgets.Tip`).
- **Start guide on Home:** "Start here: three small steps" (add your email, look first, let it sort by
  itself). A step is marked "(done)" with a lit dot; the card goes away when all three are done.
- **Settings:** every switch has a plain label and a line under it (`options.GROUPS` reworded); every
  folder says what goes in it (`options.FOLDER_WORDS`; `models.DEFAULT_CATEGORIES` keeps the sorter's own
  wording). The page says nothing changes until Save settings.
- **Help:** a fifth page of short answers (`app.HELP`), each checked against what the code does.
- **Names:** the window and the phone page say "automatic sorting" (the CLI still says background
  schedule); Accounts headings are Nickname / Email address / Mail service / Tidied?; Add account says
  "Step 1 of 2" / "Step 2 of 2" and what an app password is.
- **Phone page:** the same lines under its buttons, an intro on Moves and Settings, a "What TacoSort
  does" card on Home.
- Fixed on the way: full-width text on a page with a scroll bar was cut off at the right (the column now
  leaves room for the bar).
- README (five pages, the lines beside buttons) and its two pictures redone; CLAUDE.md has a rule about
  the wording. Slop check clean (`AI_SlopAgent/reports/2026-11-23-Email_Organization.md`).
- **Owner: "update and restart yes, do it all":** version 0.2.1 (ed4b910), live copy updated, phone page
  off + on (worked first time with the 46a983a fix), tag `v0.2.1`, release build green on Windows, macOS
  and Linux.
- Tests 745 passed, 2 skipped; ruff clean; CI green on all six jobs.
- **Open:** unchanged from the entry below (Dependabot PRs, Python 3.14.10, tag ruleset, release notes).
- Resources (2026-11-23 01:25, task + phone page running): 5 processes, 111 MB RAM, 0% CPU, port 8747,
  disk 178 MB; whole PC CPU 9% of 32 threads, RAM 25.2 of 64 GB, GPU 28%.

### 2026-11-23: New look with themes (window and phone page); release 0.2.0
- Owner's ask (2026-11-21): "make the application beautiful and fancy, make things tidy and easily
  readable. add themes as well but choose the best design based on todays most sought after looks".
  Their choices: restyle the window and the phone page; a side bar with a Home page.
- **Window (7243efe):** side bar with Home / Sort / Accounts / Settings instead of three tabs. Home is
  new: background sorting with a switch, last sort, moved today, the phone page, the accounts, and a
  first-run card. Rounded cards, switches instead of tick boxes, one accent colour, larger type.
- **Looks:** System (default; follows the computer), Light, Dark, Midnight, Taco, Rose, Contrast, saved as
  the new `theme` setting. `tacosort/theme.py` holds the colours for both the window and the phone page
  (`/themes.css`); tests check every theme has every colour and text reaches 4.5:1 contrast.
- **How it is drawn:** no new package. `tacosort/widgets.py` makes the rounded pieces and icons as PNGs
  in pure Python and uses them as ttk picture elements; each look is its own ttk theme built on `clam`.
- **Phone page:** same colours, cards, switches, drawn tab icons, and a Look picker in Settings. Who may
  connect, the POST rules and the 127.0.0.1 bind are unchanged; the page still loads only its own files.
- **Bugs met while building:** a stretched picture with a 4-pixel middle was tiled thousands of times
  (24 s to paint; fixed with a wide middle, `Pic.grown`, ~0.5 s); labels wrapping themselves from their
  own `<Configure>` chased the layout and hung `root.update()` (now wrapped top-down, `Look.column` /
  `Look.wrapped`); pictures freed off the main thread made a phone test flaky (cleared on the root's
  `<Destroy>`).
- `actions.status(cfg)`: the "last sort / moved today" lookup, shared by Home and `/api/status`.
- **Checked:** screenshots of every page in every look at 100% and 150% scaling and of the phone page;
  a read-only open on the owner's real settings; a local one-file build passed its self-check; slop
  check clean (`AI_SlopAgent/reports/2026-11-21-Email_Organization.md`).
- **2026-11-23, owner: "approve it all":** version 0.2.0 (6a173ad), live copy updated, phone page
  restarted, tag `v0.2.0` pushed, release run 45127133855 green (Windows, macOS, Linux, checksums). The
  release page has only the automatic changelog link, like 0.1.0.
- **Tailscale snag (46a983a):** `tacosort phone on` failed after `off` with "must be a Windows local
  admin to serve a path": since another program on the PC shares a folder through `tailscale serve`
  (port 8796, not TacoSort's), Tailscale lets only an administrator change the list. The `off` had not
  removed TacoSort's entry either, so the page was started again without touching Tailscale (down for a
  couple of minutes). Fix: `phone on` checks `tailscale serve status --json` and skips Tailscale when the
  port is already shared; otherwise it says to run `tacosort phone on` once as administrator.
- Tests 741 passed, 2 skipped; ruff clean; CI green on all six jobs.
- **Open:** Dependabot PRs (the imapclient bump in /packaging fails CI, not looked at); Python 3.14.10
  update; no GitHub tag ruleset; no written release notes; `phone off` can't remove the Tailscale entry
  without an administrator while port 8796's folder share exists (harmless: it points at a closed port).
- Resources (2026-11-23 00:58, task + phone page running): 5 processes, 111 MB RAM, 1% of a core, port
  8747, disk 178 MB; whole PC CPU 11% of 32 threads, RAM 25.0 of 64 GB, GPU 49%.

### 2026-11-01: Security audit and slop check fixed; the live copy moved to the user folder
- Owner's ask: "have we audited this and check for ai slop yet?" -> "start both". First security audit
  (`Security_Agent_Audit/reports/2026-11-01-Email_Organization.md`, proofs p1-p4) and a slop check
  (`AI_SlopAgent/reports/2026-11-01-Email_Organization.md`), run side by side as two subagents.
- Owner's choices: the PC is used only by them; fix everything; reword the README's preview promise
  instead of adding a preview gate; take personal details out of the current files (no history rewrite,
  older commits still hold them); switch the live setup now; update Python (postponed, see below).
- **Security fixes (b7fa6d0):**
  - New `tacosort/peer.py` asks Windows (GetExtendedTcpTable) or Linux (`/proc/net/tcp` uid) who owns
    the other end of each 127.0.0.1 connection. The phone page refuses another account's program, and
    trusts a Tailscale login header only from Tailscale's own service (its pid, or a tailscaled.exe that
    the service started). macOS answers "unknown" and keeps the old checks.
  - Turn off = `POST /api/stop` (this computer only), not killing the pid the port reported; `ping`
    ignores a listener owned by another account. Port bound with SO_EXCLUSIVEADDRUSE, 15 s read timeout.
  - `tailscale` is looked up in its install folder, then PATH without the current folder (`shutil.which`
    puts the current folder first on Windows even with `path=`); schtasks and notepad by full System32 path.
  - Release build: exact package versions with hashes (`packaging/requirements-build.txt`, from
    `uv pip compile --universal --generate-hashes`), `--require-hashes`, a build attestation and
    `SHA256SUMS.txt` on each release; Dependabot watches `/packaging`.
- **Live copy:** the scheduled task, the phone page's Run value and both "TacoSort" shortcuts now run a
  normal (not editable) install in `%LOCALAPPDATA%\Programs\TacoSort\venv`, which only the owner can
  change. Why: `D:\ClaudeProjects` lets every account on the PC write, so any account could change the
  code the task ran. Code edits in the project no longer reach the live copy; CLAUDE.md says how to update
  it (4eb0f6b). Checked: task "To Run" is the new pythonw every 15 min; phone page restarted from it; ping,
  status and the page answer 200 through Tailscale and locally, a faked wrong login gets 403;
  `tailscale serve` still has its 3 entries.
- **Writing fixes (slop check):** README promises match what the app does (preview, unsure mail), "every
  15 minutes", how to check a download; privacy.md and SECURITY.md cover the phone page; the Google
  sign-in hint that led nowhere removed; `report.py` calls the store's real methods (invented names and a
  TypeError retry gone); build-team leftovers and pasted noqa reasons removed; the business, school domain
  and a real sender's name replaced with neutral words.
- **Accepted / false alarms:** proof p1 still says HOLE for programs under the owner's own account: they
  are trusted on purpose (they can already read the keychain). Proof p4 flags the project folder because it
  reads `direct_url.json`; the live copy loads from its own site-packages (checked).
- **Not done:** Python 3.14.10 update, postponed because Bathroom, Ship Desk, tacocam, Document Vault,
  TacoVault, Whatnot Borders and the BabyG panel run on that Python (`py install 3.14 --update` once they
  are closed). Side effect: `py list` updated the Python install manager to 26.4. Two Dependabot PRs
  (upload/download-artifact) open. Release 0.2.0 needs the owner's OK. No tag ruleset on GitHub yet.
- Tests 707 passed, 2 skipped; ruff clean; CI and the build green on Windows, macOS and Linux.
- Resources (2026-11-01 23:55, task + phone page running): 2 processes, 27 MB RAM, 0% CPU, port 8747,
  disk 174 MB; whole PC CPU 21% of 32 threads, RAM 47.9 of 64 GB, GPU 99% (ImageMaker).

### 2026-10-31: Phone page over Tailscale; CI hang on the Mac fixed
- **CI fix (2026-10-30, fb4381e):** the three pushes after 4efab8e had their macOS jobs hang 6 hours until
  GitHub cancelled them. Cause: `test_ticking_background_sorting_sets_up_the_schedule` pumped Tk with
  `root.update()`, which blocks on the headless Mac runner. The test now runs the job's hand-back from the
  queue itself; CI jobs get `timeout-minutes: 20` and pytest `faulthandler_timeout=120` (a stuck test
  prints every thread). The release builds were not affected.
- Owner's ask: "can we also create a tailscale application and desktop application we can use". Choices:
  full control from the phone; always starts, with an on/off button; desktop + Start menu shortcut.
- **`tacosort/actions.py`**: the mail work (try_login, learn, preview, sort_now, undo_last, write_report,
  schedule_is_on) moved out of `app.py` so the phone page can use it without Tkinter; `app.py` re-exports
  the names.
- **`tacosort/phone.py` + `tacosort/web/`** (stdlib `http.server`, no new dependency; plain HTML/CSS/JS,
  home-screen manifest): tabs Home (schedule switch, last run, moved today, accounts), Sort (preview grouped
  by folder, sort now, undo, learn; one job at a time, polled), Moves, Settings (the window's tick boxes
  through `options.read/apply`). Safety: listens on 127.0.0.1 only; a request through Tailscale must carry a
  `Tailscale-User-Login` in `phone_logins` (filled with the owner's login on `phone on`); any other Host
  name is refused (DNS rebinding); a change needs header `X-TacoSort: 1`, JSON and a same-origin Origin
  (cross-site pages can't send that); strict CSP, no request log.
- **On/off:** `tacosort phone on|off|status|serve`, and Turn on / Turn off + Copy address in the window's
  Options tab. On = remember the login, `tailscale serve --bg --https=8747`, HKCU Run value "TacoSort phone
  page", start in the background (pythonw, detached). Off = remove all three; stop kills only the pid the
  page reports on `/api/ping`. Autostart is Windows only; macOS/Linux run `tacosort phone serve`.
- New settings `phone_port` (8747) and `phone_logins`. Build: `web/` in package data and PyInstaller
  `--add-data`; the smoke test checks the web files.
- **Decision:** TacoSort stays on the main PC, not the home server: the server is the box meant to fall
  first if hacked, and email passwords there could reset every other account. TacoSort is light (one run
  ~40 s every 15 min). Cost: no sorting and no phone page while the PC is off.
- Turned on for the owner: works through https://<pc>.ts.net:8747 from this PC; Ship Desk (5091) and
  TacoVault (8743) serve entries untouched. Desktop + Start menu shortcut to `.venv\Scripts\tacosort-app.exe`.
- Checked with screenshots (demo data, headless Edge). `/slop-check` diff clean
  (AI_SlopAgent/reports/2026-10-31-Email_Organization.md). Tests 696 passed; CI green.
- Resources (2026-10-31 21:49, phone page on): Email_Organization 2 processes, CPU 0%, RAM 37 MB, port
  8747, disk 175 MB; whole PC CPU 15% of 32 threads, RAM 24.1 of 64 GB.
- **Open:** phone page not in a release yet (0.2.0 tag needs the owner's OK); the running phone page keeps
  the code it started with (restart with `phone off` + `on`, ask first); no autostart on macOS/Linux.

### 2026-10-29: The TacoSort app and release 0.1.0
- Owner's ask: "make the install easier and make it an app if needed with tickable options".
- **`tacosort/app.py`** (Tkinter, no new dependency): tabs Accounts (add with the provider's app-password
  steps and link, test, on/off, remove; Outlook via the Microsoft device sign-in), Options (tick boxes,
  folders to use, background sorting + interval), Sort (learn, preview, sort now in rounds of 500 until
  done, undo, every move as a page). Work runs on a helper thread; the window refuses to close mid-run;
  a broken settings file is never saved over. Command hints ("run 'tacosort setup'") are reworded for
  the window.
- **`tacosort/options.py`**: what each tick box means; `read`/`apply` keep everything the window doesn't
  show (accounts, rules, VIPs, renamed folders). **`tacosort/launch.py`**: the app's entry: no arguments
  opens the window, a command runs the CLI, `--smoke-test` checks a build.
- **Schedule from the app:** in a frozen build the task runs `TacoSort.exe run --quiet --config ...`;
  `schtasks` gets `CREATE_NO_WINDOW` (no console flash). Saving with background sorting ticked always
  re-creates the task, so a moved app file is fixed by one save.
- **Packaging:** `packaging/build.py` (PyInstaller one file, `--collect-submodules tacosort` +
  keyring backends, self-check of the result), `packaging/make_icon.py` (icon drawn in plain Python:
  a letter in a taco), `.github/workflows/build.yml` (Windows, macOS, ubuntu-22.04; on a `v*` tag it
  publishes a release). Mac build has the default icon (a PNG needs Pillow to become .icns).
- `tacosort app` / `tacosort-app` open the window from a pip install. README: download first, then
  Python; screenshot `docs/images/app-options.png` (demo accounts only). `/slop-check` clean.
- Owner chose "Publish 0.1.0 now": tag `v0.1.0`, release run 40988972224 green; CI green.
- **Open:** the app is unsigned (SmartScreen / Gatekeeper warnings, explained in the README); no Intel
  Mac build; AI sorting has no switch in the window; the owner's own schedule still runs the venv.

### 2026-10-29: Yahoo account, Old mail folder, two Yahoo quirks fixed
- **Yahoo "Main" added** (app password). Backlog sorted in 500-move runs (the owner said yes): about
  10,600 -> 1,450, 9,168 moves, School folder for the school's domain (owner's choice: all of it).
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
  budget waits behind the others (`e60547d`). That alone didn't help (19:39 run: still 4.5 min): the
  backlog left 9,640 Yahoo moves due for a check (hourly in their first day), so every run spent all 200
  searches. The check now also stops after 30 seconds per account (`MAX_CHECK_SECONDS`, `aa95056`);
  the 19:54 scheduled run took 69 s.
- **Old mail** (`eb2f3fe`, owner: "do all"): `old_mail_days` (default 0 = off) / `old_mail_folder`
  ("Old mail"). Read mail that would stay in the Inbox moves there once that old; never starred or unread
  mail, VIPs, or mail a user rule or learned rule keeps there; a dragged-back message stays. Preview: 0 on
  both Gmail accounts, 500+ on Yahoo. Real run: 1,265 moved, Yahoo Inbox 1,450 -> 174.
- Docs (README, SPEC, how-it-works, config example) cover `old_mail_days`; `/slop-check` diff clean
  (`AI_SlopAgent/reports/2026-10-29-Email_Organization.md`). CI green on every push checked.
- Resources 2026-10-29 19:29: TacoSort 0 processes between runs, 89 MB disk; whole PC CPU 11% of 32
  threads, RAM 28.3 of 64 GB, GPU 30%.
- **Open:** undo can't find the 59 Yahoo emails with a
  malformed Message-ID (it says so and leaves them; the recorded new UID pointed at a different message
  in one check, and undo only uses it after a sender + subject match). A fallback that searches by
  sender and compares the full header would close that.

### 2026-10-29: Second account, learning from repetition, 24-hour wait, Security and Business folders
- **Second account** (Gmail, a small business) added through `tacosort setup` in the canvas
  terminal. Gmail said "app passwords not available" until 2-Step Verification was on. Typing the setup
  command with `nodeterm.sh write` failed at first: it eats backslashes (use `.venv/Scripts/...`).
- **Mac CI** was a GitHub runner shortage, not Actions minutes: a re-run passed (`a4a8281`).
- **Unsure company mail** (`f52cd77`): The business account kept 299 in the Inbox, ~250 of them Whatnot/eBay/TCGplayer
  mail scored 51-72% (needs 75%). Now machine-sent unsure mail joins its company's folder once the
  company has 3 such emails or already has a folder (`unsure_to_company`, default on). One folder per
  company, chosen by majority guess and stored (`company_folders` category `unsure`): identical Whatnot
  emails had split between Promotions and Notifications because Gmail's tab differed.
- **`hold_hours`** (owner: 24): new mail stays in the Inbox until it is that old (IMAP INTERNALDATE, new
  `Message.received`); held mail is not marked seen and the watermark waits, so a later run sorts it.
- **Kept in the Inbox:** a person writing through a marketplace ("Message from Buyer"), passkey
  notices, "we need more information", "confirm/verify your <brand> account".
- Folder names: "Sam (TCGplayer)" -> "TCGplayer" (the part in brackets names the company).
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
