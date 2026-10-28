# Contributing to MailTidy

Thank you for helping! You don't need to be a programmer to contribute: reports of mail that was
sorted wrongly, corrections to provider steps, and clearer wording are all just as useful as code.

## Never share real email

Please **never paste real emails, email addresses, passwords, app passwords or tokens** into issues,
pull requests, tests or logs. Describe the message instead ("a shipping notice from a shoe shop, with
a List-Unsubscribe header, subject 'Your order is on its way'"), and use `example.com` addresses.
`mailtidy rules test --from ... --subject ...` is a good way to show a wrong decision without
sharing the mail.

## Ideas for contributions

- **Sorting:** new shop, bank, airline or app domains, and subject words in more languages. These live in
  tables at the top of `mailtidy/classify/heuristics.py`. Add an example to the tests with them.
- **Providers:** server details or setup steps that changed (`mailtidy/imap/providers.py` and
  `docs/providers.md`).
- **Docs:** anything confusing in the README or `docs/`.

## Setting up for development

```
git clone <this repository's URL>
cd mailtidy
python -m venv .venv
.venv\Scripts\activate          (Windows)   or   source .venv/bin/activate   (macOS / Linux)
pip install -e ".[dev,ai]"
```

Run the checks before opening a pull request:

```
python -m pytest -q
python -m ruff check .
```

The tests run fully offline. They use `mailtidy.testing.FakeBackend` (an in-memory mail server) and
never touch a real mailbox or your keychain. Please keep it that way: no network, no real accounts.

## How the code is organised

| Part | What it does |
|---|---|
| `mailtidy/models.py`, `backend.py` | the shared data types and the mailbox interface |
| `mailtidy/imap/` | talking to mail servers, providers, passwords (keychain) and OAuth |
| `mailtidy/classify/` | deciding where a message goes (safety checks, rules, learned, built-in checks, AI) |
| `mailtidy/store.py`, `learner.py`, `engine.py`, `schedule.py` | memory, learning, runs, undo, background job |
| `mailtidy/cli.py`, `config.py`, `report.py` | the `mailtidy` command, the settings file, the HTML report |

[docs/how-it-works.md](docs/how-it-works.md) explains the decision order and learning.

## Ground rules for code

- **Safety first.** MailTidy must never delete mail, mark it read (always `BODY.PEEK`), touch Sent/
  Drafts/Trash/Junk/All Mail, or move flagged mail. Anything that moves mail must be logged and
  undoable. When unsure, leave mail in the Inbox.
- **Secrets** go through `mailtidy.imap.auth` (the keychain) only: never into the settings file, logs,
  error messages or test fixtures.
- **Plain English** in everything a user can see: messages, errors, docs. Say what happened and what
  to do next.
- **Escape** email text before putting it into HTML; treat it as untrusted.
- Python 3.10+, type hints, small functions with short docstrings. New dependencies need a good reason;
  ask in an issue first.

## Pull requests

Keep them focused (one change per pull request), include tests, and describe the change in plain words.
By contributing you agree that your contribution is licensed under the MIT license.
