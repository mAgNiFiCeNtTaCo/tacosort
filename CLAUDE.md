# TidyInbox

Open-source (MIT) Python tool that sorts every email account into one shared folder scheme and keeps it
sorted in the background. Public at https://github.com/mAgNiFiCeNtTaCo/tidyinbox, so other people use it.
The owner isn't a programmer: explain in plain words.

## Read first
- `PROGRESS.md` (log and current state), `WORKFLOW.md` (who built what), `SPEC.md` (design, decision
  order, contracts), `README.md` and `docs/`.
- `WORKFLOW.html` is the picture of how the project was built, drawn from `workflow.json` by
  `_brain/workflow.py` on each /save-progress: edit the JSON (or let /save-progress do it), never the HTML.

## Layout
- `tidyinbox/models.py`, `backend.py`, `testing.py`: shared contracts and the in-memory `FakeBackend`
  (modes `gmail=True`, `uidplus=False`, `move_filter`).
- `tidyinbox/imap/`: providers, keychain + OAuth, `ImapBackend`.
- `tidyinbox/classify/`: guards, user rules, heuristics, AI (`pipeline.py` holds the decision order).
- `tidyinbox/store.py`, `learner.py`, `engine.py`, `schedule.py`: state, learning, runs, background task.
- `tidyinbox/cli.py`, `config.py`, `report.py`: the command line.
- `tests/`, `.github/workflows/ci.yml`, `examples/config.example.yaml`.
- `_team/` (gitignored): station briefs and review reports.

## Commands
- Tests: `.venv\Scripts\python -m pytest -q`; lint: `.venv\Scripts\ruff check .`
- Run: `.venv\Scripts\tidyinbox setup`, `preview`, `run --backlog --yes`, `run`, `schedule on`.
- CI: `gh run list` / `gh run watch` after a push.

## Rules
- Never delete mail, never mark it read (BODY.PEEK only), never touch Sent/Trash/Junk/All Mail/Drafts or
  starred mail, never move mail without a Message-ID.
- Secrets live in the OS keychain (service `tidyinbox`: `account:<id>`, `account:<id>:client-secret`,
  `ai:anthropic`). Never open or print them; never put them in config.yaml.
- Local state (`config.yaml`, `state.sqlite3`, `tidyinbox.log` in the data dir) holds mail metadata:
  never commit it.
- Public repo: push freely, but nothing personal in committed files.
- Not on PyPI: docs install with `pipx install git+https://github.com/mAgNiFiCeNtTaCo/tidyinbox`.

## Gotchas
- Gmail folders are labels: Inbox mail with a label must never be learned as "move out".
- A plain `run` sorts only new mail; the backlog needs `--backlog`. A UIDVALIDITY change re-baselines.
- Windows-only tests miss things: CI caught a run-id collision (Linux) and a temp-path match (macOS).
- nodeterm: `spawn-team`/`verify` are refused while the project is off screen; use
  `open-claude --run-now` per station.
