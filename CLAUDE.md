# TacoSort

Open-source (MIT) Python tool that sorts every email account into one shared folder scheme and keeps it
sorted in the background. Public at https://github.com/mAgNiFiCeNtTaCo/tacosort, so other people use it.
The owner isn't a programmer: explain in plain words.

## Read first
- `PROGRESS.md` (log and current state), `WORKFLOW.md` (who built what), `SPEC.md` (design, decision
  order, contracts), `README.md` and `docs/`.
- `WORKFLOW.html` is the picture of how the project was built, drawn from `workflow.json` by
  `_brain/workflow.py` on each /save-progress: edit the JSON (or let /save-progress do it), never the HTML.

## Layout
- `tacosort/models.py`, `backend.py`, `testing.py`: shared contracts and the in-memory `FakeBackend`
  (modes `gmail=True`, `uidplus=False`, `move_filter`).
- `tacosort/imap/`: providers, keychain + OAuth, `ImapBackend`.
- `tacosort/classify/`: guards, user rules, heuristics, AI (`pipeline.py` holds the decision order).
- `tacosort/store.py`, `learner.py`, `engine.py`, `schedule.py`: state, learning, runs, background task.
- `tacosort/companies.py`: company sub-folders (`Promotions/Crunchyroll`), naming, the threshold, and
  where a company's unsure mail goes (`unsure` rows in `company_folders`).
- `tacosort/cli.py`, `config.py`, `report.py`: the command line.
- `tacosort/app.py` (the window), `options.py` (what each tick box means), `launch.py` (the built app's
  entry: window, CLI or `--smoke-test`), `assets/` (icon, drawn by `packaging/make_icon.py`).
- `packaging/build.py` + `.github/workflows/build.yml`: the one-file app; a `v*` tag publishes a release.
- `tests/`, `.github/workflows/ci.yml`, `examples/config.example.yaml`.
- `_team/` (gitignored): station briefs and review reports.

## Commands
- Tests: `.venv\Scripts\python -m pytest -q`; lint: `.venv\Scripts\ruff check .`
- Run: `.venv\Scripts\tacosort setup`, `preview`, `run --backlog --yes`, `run`, `schedule on`;
  `.venv\Scripts\tacosort app` opens the window.
- Build the app here: `.venv\Scripts\python packaging\build.py` (-> `dist\TacoSort-<ver>-windows.exe`).
  Release: bump `tacosort/__init__.py`, push, then tag `vX.Y.Z` and push the tag (public: owner's OK).
- CI: `gh run list` / `gh run watch` after a push.

## Rules
- Never delete mail, never mark it read (BODY.PEEK only), never touch Sent/Trash/Junk/All Mail/Drafts or
  starred mail, never move mail without a Message-ID.
- Secrets live in the OS keychain (service `tacosort`: `account:<id>`, `account:<id>:client-secret`,
  `ai:anthropic`). Never open or print them; never put them in config.yaml.
- Local state (`config.yaml`, `state.sqlite3`, `tacosort.log` in the data dir) holds mail metadata:
  never commit it.
- Public repo: push freely, but nothing personal in committed files.
- Not on PyPI: docs install with `pipx install git+https://github.com/mAgNiFiCeNtTaCo/tacosort`.
- The window only touches widgets on its own thread: mail work goes through `Jobs.start`, log lines
  through `Jobs.later`. The built app has no console (`sys.stdout` is None until launch.py fixes it).

## Gotchas
- Gmail folders are labels: Inbox mail with a label must never be learned as "move out".
- A plain `run` sorts only new mail; the backlog needs `--backlog`. A UIDVALIDITY change re-baselines.
- Windows-only tests miss things: CI caught a run-id collision (Linux) and a temp-path match (macOS).
- nodeterm: `spawn-team`/`verify` are refused while the project is off screen; use
  `open-claude --run-now` per station.
- The owner's own Gmail is live: the Windows scheduled task "TacoSort-<username>" runs the editable
  install every 15 minutes, so it picks up code mid-edit. Keep the code working between edits (or ask
  before pausing the task). `tacosort preview` and the read-only scratch scripts never move mail; a real
  `run` needs the owner's OK.
- `tacosort setup` asks questions: run it in a canvas terminal, not via `!` (no input, and bash eats
  backslashes in `.venv\Scripts\...`). Typing into that terminal with `nodeterm.sh write` eats
  backslashes too: send `.venv/Scripts/tacosort.exe setup` (forward slashes work in PowerShell).
- Real Gmail differs from the fakes: tabs only via X-GM-RAW search. Check a change on the real mailbox
  with `tacosort preview` before telling the owner it works.
- The owner runs three accounts (personal Gmail, SRGaming Gmail for a card-selling business, Yahoo
  "Main") with non-default settings (`hold_hours: 24`, `unsure_to_folder`, `security_keep_days: 3`,
  `business_keep_days: 3`, `old_mail_days: 180`). Defaults for
  other users stay conservative: new behaviour that moves more mail ships off by default.
- `recheck_inbox` runs on the first plain run of each day: a code change that widens what moves acts on
  the owner's whole Inbox at the next scheduled run, without a `--backlog`. Preview first.
- A security, platform ("Message from Buyer") or contact guard always beats the look-alike and unsure
  paths; keep it that way when adding categories.
- Yahoo differs: `HEADER.FIELDS` returns six headers only (the whole header is fetched there), the inbox
  is called "Inbox", searches take ~1 s each, and a Message-ID without `<...@...>` can't be found by
  search at all. Check Yahoo with a read-only scratch script before trusting a Gmail result.
