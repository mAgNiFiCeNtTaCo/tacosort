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
- `tacosort/companies.py`: company sub-folders (`Promotions/Crunchyroll`), naming and the threshold.
- `tacosort/cli.py`, `config.py`, `report.py`: the command line.
- `tests/`, `.github/workflows/ci.yml`, `examples/config.example.yaml`.
- `_team/` (gitignored): station briefs and review reports.

## Commands
- Tests: `.venv\Scripts\python -m pytest -q`; lint: `.venv\Scripts\ruff check .`
- Run: `.venv\Scripts\tacosort setup`, `preview`, `run --backlog --yes`, `run`, `schedule on`.
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
  backslashes in `.venv\Scripts\...`).
- Real Gmail differs from the fakes: tabs only via X-GM-RAW search. Check a change on the real mailbox
  with `tacosort preview` before telling the owner it works.
