"""The `tacosort` command, with every real part (imap, engine, schedule, classifier) replaced by fakes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tacosort import __version__, cli
from tacosort.backend import BackendError
from tacosort.config import load_config, save_config
from tacosort.models import AccountConfig, AppConfig, Decision, LearnReport, MoveRecord, RunReport
from tacosort.testing import FakeBackend

GOOD = "abcdefghijklmnop"     # the app password the fake server accepts


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    host: str
    port: int = 993
    ssl: bool = True
    auth: str = "password"
    app_password_url: str = "https://example.com/app-passwords"
    notes: str = "1. Turn on 2-Step Verification.\n2. Create an app password called TacoSort."
    domains: tuple = ()


PROVIDERS = {
    "gmail": Provider("gmail", "Gmail", "imap.gmail.example.com"),
    "outlook": Provider("outlook", "Outlook.com", "outlook.example.com", auth="oauth2-microsoft"),
    "custom": Provider("custom", "Other", ""),
}


def detect(email: str) -> Provider:
    domain = email.rsplit("@", 1)[-1]
    return {"gmail.example.com": PROVIDERS["gmail"], "outlook.example.com": PROVIDERS["outlook"]}.get(
        domain, PROVIDERS["custom"])


def planned(account: str, subject: str = "Your receipt") -> MoveRecord:
    return MoveRecord(run_id="r1", account=account, message_id="<m@example.com>", sender="shop@example.com",
                      subject=subject, from_folder="INBOX", to_folder="Receipts", category="receipts",
                      source="heuristic", reason="order confirmation", at=datetime(2026, 10, 1, tzinfo=timezone.utc))


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.run_id = "r1"
        self.errors: list[str] = []
        self.started: list[list[str]] = []    # start_from_now calls (kept apart so `calls` stays simple)

    def start_from_now(self, config, store, backends, *, log=print):
        self.started.append([a.id for a in config.accounts])
        return []

    def run_once(self, config, store, backends, *, dry_run, backlog=False, log=print):
        self.calls.append(("run_once", [a.id for a in config.accounts], sorted(backends), dry_run, backlog))
        moves = [planned(a.id) for a in config.accounts]
        return RunReport(run_id=self.run_id, dry_run=dry_run, started=datetime.now(), scanned=5,
                         moved=0 if dry_run else len(moves), kept=4, moves=moves, errors=list(self.errors))

    def learn_from_folders(self, config, store, backends, *, log=print):
        self.calls.append(("learn", [a.id for a in config.accounts]))
        return LearnReport(folders_scanned=3, messages_scanned=40, rules_learned=6, contacts_found=9)

    def undo(self, config, store, backends, *, run_id=None, last=None, log=print):
        self.calls.append(("undo", run_id, last))
        log("Put 3 message(s) back.")
        return 3

    def watch(self, config, store, backends_factory, *, log=print):
        self.calls.append(("watch", sorted(backends_factory())))
        raise KeyboardInterrupt


class FakeStore:
    def __init__(self) -> None:
        self.learned = object()
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def recent_moves(self, limit=200, *, account=None, include_undone=True):
        return [planned("gmail")]

    def last_run(self, *, dry_run=False):
        return {"run_id": "r1", "started": datetime(2026, 10, 1, 8, 0), "dry_run": False, "moved": 1, "scanned": 5}

    def moves_since(self, since, *, account=None):
        return 7

    def move_counts(self):
        return {("gmail", "Receipts"): 1}

    def learned_rules(self):
        return [{"account": "*", "kind": "domain", "key": "shop.example.com", "category": "receipts",
                 "folder": "", "weight": 2}]

    def corrections(self, limit=100):
        return []

    def contacts(self):
        return {"friend@example.com"}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A temporary settings file + data folder, and fakes for every other station's module."""
    cfg_path = tmp_path / "cfg" / "config.yaml"
    data = tmp_path / "data"
    monkeypatch.setenv("TACOSORT_CONFIG", str(cfg_path))
    monkeypatch.setenv("TACOSORT_DATA", str(data))
    secrets: dict[str, str] = {}
    engine = FakeEngine()
    schedule = SimpleNamespace(calls=[])
    schedule.install = lambda minutes, path: schedule.calls.append(("install", minutes, path)) or "Schedule is on."
    schedule.uninstall = lambda: schedule.calls.append(("uninstall",)) or "Schedule is off."
    schedule.status = lambda: "on, every 15 minutes"
    auth = SimpleNamespace(
        set_secret=lambda i, v: secrets.__setitem__(i, v),
        get_secret=lambda i: secrets.get(i),
        delete_secret=lambda i: secrets.pop(i, None),
        delete_account_secrets=lambda i: [secrets.pop(k) for k in list(secrets) if k in (i, f"{i}:client-secret")],
        get_ai_key=lambda: secrets.get("ai:anthropic"),
        set_ai_key=lambda v: secrets.__setitem__("ai:anthropic", v),
        delete_ai_key=lambda: secrets.pop("ai:anthropic", None),
        device_logins=[],
    )
    auth.microsoft_device_login = lambda acct, log=print: (
        auth.device_logins.append(acct.oauth_client_id), secrets.__setitem__(acct.id, "refresh-token"))
    providers = SimpleNamespace(PROVIDERS=PROVIDERS, detect_provider=detect)
    stores: list[FakeStore] = []

    def make_backend(acct: AccountConfig, secret_getter=None) -> FakeBackend:
        secret = (secret_getter or secrets.get)(acct.id)
        ok = secret in (GOOD, "refresh-token")
        return FakeBackend(acct.id, fail_connect=None if ok else "The app password was not accepted.")

    def open_store(cfg):
        stores.append(FakeStore())
        return stores[-1]

    monkeypatch.setattr(cli, "_engine", lambda: engine)
    monkeypatch.setattr(cli, "_schedule", lambda: schedule)
    monkeypatch.setattr(cli, "_auth", lambda: auth)
    monkeypatch.setattr(cli, "_providers", lambda: providers)
    monkeypatch.setattr(cli, "make_backend", make_backend)
    monkeypatch.setattr(cli, "open_store", open_store)
    return SimpleNamespace(path=cfg_path, data=data, secrets=secrets, engine=engine, schedule=schedule,
                           auth=auth, stores=stores, monkeypatch=monkeypatch)


def with_accounts(env, *ids: str, previewed: bool = False) -> None:
    cfg = AppConfig(accounts=[AccountConfig(id=i, email=f"{i}@example.com", provider="gmail") for i in ids])
    save_config(cfg, env.path)
    for i in ids:
        env.secrets[i] = GOOD
    if previewed:
        cli.mark_previewed(cfg, list(ids))


def script(env, answers: list[str], passwords: list[str] | None = None) -> None:
    """Feed the wizard's questions from lists (fails loudly if it asks more than expected)."""
    answers = list(answers)
    passwords = list(passwords or [])

    def fake_input(prompt=""):
        if not answers:
            raise AssertionError(f"unexpected question: {prompt!r}")
        return answers.pop(0)

    def fake_getpass(prompt=""):
        if not passwords:
            raise AssertionError(f"unexpected password question: {prompt!r}")
        return passwords.pop(0)

    env.monkeypatch.setattr("builtins.input", fake_input)
    env.monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)


# ---- basics -----------------------------------------------------------------------------------------

def test_version(capsys) -> None:
    assert cli.main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_help_and_bad_usage(capsys) -> None:
    assert cli.main(["--help"]) == 0
    assert cli.main([]) == 2
    assert cli.main(["fly"]) == 2
    assert cli.main(["run", "--bogus"]) == 2
    assert cli.main(["undo", "--last", "2", "--run", "x"]) == 2
    assert cli.main(["schedule"]) == 2


def test_commands_need_accounts(env, capsys) -> None:
    assert cli.main(["run"]) == 1
    assert "tacosort setup" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["accounts", "list"], ["accounts", "test"], ["accounts", "test", "gmail"],
    ["run", "--dry-run"], ["run", "--yes", "--quiet"], ["run", "--backlog", "--account", "gmail"],
    ["preview"], ["preview", "--limit", "1"], ["learn"], ["learn", "--account", "yahoo"],
    ["undo"], ["undo", "--last", "5"], ["undo", "--run", "r1"],
    ["watch"], ["status"], ["rules", "list"], ["rules", "test", "--from", "a@example.com"],
    ["schedule", "on"], ["schedule", "on", "--every", "30"], ["schedule", "off"], ["schedule", "status"],
    ["report"], ["config", "path"], ["config", "check"],
])
def test_every_subcommand_runs(env, argv, monkeypatch) -> None:
    with_accounts(env, "gmail", "yahoo", previewed=True)
    monkeypatch.setattr(cli, "make_classifier", lambda c, learned, contacts: SimpleNamespace(
        classify=lambda m: Decision("inbox", 0.9, "default", "unsure")))
    assert cli.main(argv) == 0


def test_global_config_option(env, tmp_path, capsys) -> None:
    other = tmp_path / "other.yaml"
    save_config(AppConfig(accounts=[AccountConfig(id="zz", email="zz@example.com", provider="gmail")]), other)
    assert cli.main(["--config", str(other), "accounts", "list"]) == 0
    assert "zz@example.com" in capsys.readouterr().out


def test_unknown_account(env, capsys) -> None:
    with_accounts(env, "gmail", previewed=True)
    assert cli.main(["run", "--account", "nope"]) == 1
    assert "no account called 'nope'" in capsys.readouterr().err


def test_missing_module_is_friendly(env, monkeypatch, capsys) -> None:
    with_accounts(env, "gmail", previewed=True)

    def broken():
        raise ModuleNotFoundError("No module named 'tacosort.engine'", name="tacosort.engine")

    monkeypatch.setattr(cli, "_engine", broken)
    assert cli.main(["run"]) == 1
    err = capsys.readouterr().err
    assert "installing TacoSort again" in err and "pip install" not in err   # not ours on PyPI (T-B2)


# ---- safety: preview before the first real move ------------------------------------------------------

def test_run_refuses_without_preview(env, capsys) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["run"]) == 1
    assert "Run `tacosort preview` first" in capsys.readouterr().err
    assert env.engine.calls == []


def test_dry_run_allowed_without_preview(env) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["run", "--dry-run"]) == 0
    assert env.engine.calls == [("run_once", ["gmail"], ["gmail"], True, False)]


def test_preview_then_run(env, capsys) -> None:
    with_accounts(env, "gmail", "yahoo")
    assert cli.main(["preview"]) == 0
    out = capsys.readouterr().out
    assert "Your receipt" in out and "-> Receipts" in out and "Nothing was changed" in out
    assert [c[3:] for c in env.engine.calls] == [(True, True), (True, True)]
    assert json.loads((env.data / cli.PREVIEW_FILE).read_text())["accounts"] == ["gmail", "yahoo"]
    assert cli.main(["run", "--backlog"]) == 0
    assert env.engine.calls[-1] == ("run_once", ["gmail", "yahoo"], ["gmail", "yahoo"], False, True)
    assert "tacosort undo" in capsys.readouterr().out


def test_run_only_previewed_accounts(env, capsys) -> None:
    with_accounts(env, "gmail", "yahoo")
    cli.mark_previewed(load_config(env.path), ["gmail"])
    assert cli.main(["run"]) == 0
    assert env.engine.calls[-1][1] == ["gmail"]
    assert "yahoo" in capsys.readouterr().err


def test_run_yes_skips_preview_and_remembers(env) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["run", "--yes"]) == 0
    assert env.engine.calls[-1][3] is False
    assert cli.main(["run"]) == 0


def test_failed_preview_is_not_remembered(env) -> None:
    with_accounts(env, "gmail")
    env.engine.errors = ["gmail: the login didn't work"]
    assert cli.main(["preview"]) == 1
    assert cli.previewed_accounts(load_config(env.path)) == set()


def test_busy_run_is_skipped_quietly(env, capsys) -> None:
    with_accounts(env, "gmail", previewed=True)
    env.engine.run_id = ""
    assert cli.main(["run", "--quiet"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_run_errors_exit_1(env, capsys) -> None:
    with_accounts(env, "gmail", previewed=True)
    env.engine.errors = ["gmail: couldn't reach the server"]
    assert cli.main(["run", "--quiet"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "couldn't reach" in captured.err


def test_watch_needs_preview(env) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["watch"]) == 1
    assert cli.main(["watch", "--yes"]) == 0
    assert env.engine.calls == [("watch", ["gmail"])]


def test_schedule_on_needs_preview(env, capsys) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["schedule", "on"]) == 1
    assert "tacosort preview" in capsys.readouterr().err
    assert env.schedule.calls == []
    assert cli.main(["schedule", "on", "--yes", "--every", "30"]) == 0
    assert env.schedule.calls == [("install", 30, env.path.resolve())]
    assert load_config(env.path).interval_minutes == 30
    assert cli.main(["schedule", "on", "--yes", "--every", "0"]) == 1


# ---- setup wizard ------------------------------------------------------------------------------------

def test_setup_wizard(env, capsys) -> None:
    script(env,
           answers=["alice@gmail.example.com", "",      # email, accept suggested id
                    "y",                                # try another password after the first fails
                    "n",                                # add another account?
                    "y",                                # learn from folders
                    "y", "n",                           # preview yes, sort now no
                    "y"],                               # schedule on
           passwords=["wrong password", "abcd efgh ijkl mnop"])
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "Gmail" in out and "https://example.com/app-passwords" in out and "2-Step Verification" in out
    assert "That didn't work" in out and "Connected!" in out
    assert env.secrets == {"gmail": GOOD}
    for secret in (GOOD, "abcd efgh", "wrong password"):
        assert secret not in out
        assert secret not in env.path.read_text(encoding="utf-8")
    cfg = load_config(env.path)
    assert [(a.id, a.email, a.provider, a.host) for a in cfg.accounts] == [
        ("gmail", "alice@gmail.example.com", "gmail", "imap.gmail.example.com")]
    assert [c[0] for c in env.engine.calls] == ["learn", "run_once"]
    assert env.engine.calls[1][3:] == (True, True)   # the preview was a dry run of the backlog
    assert env.schedule.calls and env.schedule.calls[0][1] == 15
    assert cli.previewed_accounts(cfg) == {"gmail"}


def test_setup_two_accounts_custom_server(env, capsys) -> None:
    script(env,
           answers=["not an email", "bob@gmail.example.com", "personal",
                    "y",                                       # add another
                    "bob@mail.example.org", "imap.example.org", "", "",   # custom host, port 993, ssl yes
                    "",                                        # suggested id
                    "n",                                       # add another
                    "n", "n", "n"],                            # learn, preview, schedule
           passwords=[GOOD, GOOD])
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "doesn't look like an email address" in out
    cfg = load_config(env.path)
    assert [a.id for a in cfg.accounts] == ["personal", "mail"]
    custom = cfg.accounts[1]
    assert (custom.provider, custom.host, custom.port, custom.ssl) == ("custom", "imap.example.org", 993, True)
    assert env.engine.calls == [] and env.schedule.calls == []
    assert GOOD not in env.path.read_text(encoding="utf-8")


def test_setup_gives_up_cleanly(env, capsys) -> None:
    script(env, answers=["alice@gmail.example.com", "", "n", "n", "n"], passwords=["nope"])
    assert cli.main(["setup"]) == 0
    assert "was not saved" in capsys.readouterr().out
    assert load_config(env.path).accounts == []


def test_setup_outlook_device_code(env, capsys) -> None:
    script(env, answers=["carol@outlook.example.com", "", "00000000-1111-2222-3333-444444444444",
                         "n", "n", "n", "n"])
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "client ID" in out and "outlook-oauth.md" in out
    assert env.auth.device_logins == ["00000000-1111-2222-3333-444444444444"]
    acct = load_config(env.path).accounts[0]
    assert acct.auth == "oauth2-microsoft" and acct.oauth_client_id.startswith("00000000")
    assert "refresh-token" not in env.path.read_text(encoding="utf-8")


def test_setup_outlook_skip_without_client_id(env, capsys) -> None:
    script(env, answers=["carol@outlook.example.com", "", "", "n"])
    assert cli.main(["setup"]) == 0
    assert load_config(env.path).accounts == []


def test_accounts_add_existing_offers_new_password(env, capsys) -> None:
    with_accounts(env, "gmail")
    env.secrets["gmail"] = "old"
    script(env, answers=["gmail@example.com", "y"], passwords=[GOOD])
    assert cli.main(["accounts", "add"]) == 1      # nothing new added
    assert env.secrets["gmail"] == GOOD
    assert "Password updated" in capsys.readouterr().out


def test_keychain_failure_is_plain(env, capsys) -> None:
    def broken(i, v):
        raise BackendError("No keychain is available on this computer.")

    env.auth.set_secret = broken
    script(env, answers=["alice@gmail.example.com", "", "n"], passwords=[GOOD])
    assert cli.main(["accounts", "add"]) == 1
    assert "No keychain" in capsys.readouterr().out


# ---- other commands ----------------------------------------------------------------------------------

def test_accounts_remove(env, capsys) -> None:
    with_accounts(env, "gmail", "yahoo", previewed=True)
    assert cli.main(["accounts", "remove", "gmail", "--yes"]) == 0
    assert [a.id for a in load_config(env.path).accounts] == ["yahoo"]
    assert "gmail" not in env.secrets and "yahoo" in env.secrets
    assert cli.previewed_accounts(load_config(env.path)) == {"yahoo"}
    assert "stay exactly as they are" in capsys.readouterr().out


def test_accounts_remove_asks(env) -> None:
    with_accounts(env, "gmail")
    script(env, answers=["n"])
    assert cli.main(["accounts", "remove", "gmail"]) == 0
    assert len(load_config(env.path).accounts) == 1


def test_accounts_test_reports_problem(env, capsys) -> None:
    with_accounts(env, "gmail")
    env.secrets["gmail"] = "bad"
    assert cli.main(["accounts", "test"]) == 1
    assert "not accepted" in capsys.readouterr().out


def test_status(env, capsys) -> None:
    with_accounts(env, "gmail")
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / "state.sqlite3").write_bytes(b"")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    for words in ("gmail@example.com", "not previewed", "Last run: 2026-10-01 08:00", "Moved today: 7",
                  "Learned rules: 1", "on, every 15 minutes"):
        assert words in out, words


def test_status_without_anything(env, capsys) -> None:
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "none yet" in out and "Last run: never" in out
    assert env.stores == []          # status never creates the memory file


def test_undo(env, capsys) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["undo", "--last", "3"]) == 0
    assert env.engine.calls == [("undo", None, 3)]
    out = capsys.readouterr().out
    assert out.count("Put 3 message") == 1 and "won't move them again" in out


def test_rules_list(env, capsys) -> None:
    with_accounts(env, "gmail")
    from tacosort.models import Rule

    cfg = load_config(env.path)
    cfg.rules = [Rule(name="school", action="folder:Kids", from_=["*@school.example.com"])]
    save_config(cfg, env.path)
    assert cli.main(["rules", "list"]) == 0
    out = capsys.readouterr().out
    assert "1. school: from *@school.example.com -> folder:Kids" in out


def test_rules_test_file(env, tmp_path, monkeypatch, capsys) -> None:
    with_accounts(env, "gmail")
    eml = tmp_path / "m.eml"
    eml.write_bytes(b"From: Shop <Orders@Shop.example.com>\r\nTo: me@example.com\r\n"
                    b"Subject: =?utf-8?q?Your_order_=E2=9C=93?=\r\nList-Id: <deals.shop.example.com>\r\n\r\nbody\r\n")
    seen = {}

    def classifier(cfg, learned, contacts):
        def classify(msg):
            seen["msg"] = msg
            return Decision("receipts", 0.92, "heuristic", "order confirmation")
        return SimpleNamespace(classify=classify)

    monkeypatch.setattr(cli, "make_classifier", classifier)
    assert cli.main(["rules", "test", str(eml)]) == 0
    out = capsys.readouterr().out
    assert "goes to Receipts" in out and "order confirmation" in out and "heuristic" in out
    msg = seen["msg"]
    assert msg.from_.email == "orders@shop.example.com" and msg.subject == "Your order ✓"
    assert msg.headers["list-id"] == "<deals.shop.example.com>" and msg.account == "gmail"


def test_rules_test_needs_input(env, capsys) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["rules", "test"]) == 1
    assert ".eml" in capsys.readouterr().err


def test_report_written(env, tmp_path, monkeypatch, capsys) -> None:
    with_accounts(env, "gmail")
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / "state.sqlite3").write_bytes(b"")
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    out = tmp_path / "r.html"
    assert cli.main(["report", "--output", str(out), "--open"]) == 0
    assert "Your receipt" in out.read_text(encoding="utf-8")
    assert opened and opened[0].startswith("file:")


def test_config_check(env, capsys) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["config", "check"]) == 0
    assert "looks good" in capsys.readouterr().out
    data = yaml.safe_load(env.path.read_text(encoding="utf-8"))
    data["accounts"][0]["port"] = 0
    data["typo_key"] = 1
    env.path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert cli.main(["config", "check"]) == 1
    out = capsys.readouterr().out
    assert "port 0" in out and "typo_key" in out


def test_config_check_broken_yaml(env, capsys) -> None:
    env.path.parent.mkdir(parents=True)
    env.path.write_text("accounts: [\n", encoding="utf-8")
    assert cli.main(["config", "check"]) == 1
    assert "typing mistake" in capsys.readouterr().err


def test_config_edit_uses_editor(env, monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("EDITOR", "myeditor --wait")
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert cli.main(["config", "edit"]) == 0
    assert calls == [["myeditor", "--wait", str(env.path)]]
    assert env.path.exists()


def test_schedule_interval_limits(env, capsys) -> None:
    with_accounts(env, "gmail", previewed=True)
    assert cli.main(["schedule", "on", "--every", "5000"]) == 1
    assert "1439" in capsys.readouterr().err
    assert load_config(env.path).interval_minutes == 15     # not saved when refused
    env.schedule.install = lambda minutes, path: (_ for _ in ()).throw(RuntimeError("Task Scheduler said no."))
    assert cli.main(["schedule", "on", "--every", "20"]) == 1
    assert "Task Scheduler said no." in capsys.readouterr().err
    assert load_config(env.path).interval_minutes == 15


def test_readme_commands_exist() -> None:
    """Every `tacosort <command> [action]` the docs mention must parse."""
    import re

    root = Path(__file__).resolve().parent.parent
    parser = cli.build_parser()
    commands = set(parser._subparsers._group_actions[0].choices)
    seen = set()
    for doc in [root / "README.md", *sorted((root / "docs").glob("*.md"))]:
        for m in re.finditer(r"`tacosort ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", doc.read_text(encoding="utf-8")):
            cmd, action = m.groups()
            assert cmd in commands, f"{doc.name}: tacosort {cmd}"
            sub = parser._subparsers._group_actions[0].choices[cmd]
            if sub._subparsers is not None and action:
                assert action in sub._subparsers._group_actions[0].choices, f"{doc.name}: tacosort {cmd} {action}"
            seen.add(cmd)
    assert {"setup", "preview", "run", "undo", "status"} <= seen


# ---- fix wave (2026-10-28) ------------------------------------------------------------------------------

# S-S1: account names that would share a keychain entry with something else are never suggested or taken.

def test_suggested_id_never_reserved() -> None:
    cfg = AppConfig()
    assert cli._suggest_id(cfg, "custom", "sam@anthropic.example.com") == "anthropic-mail"
    assert cli._suggest_id(cfg, "custom", "sam@ai.example.com") == "ai-mail"
    assert cli._suggest_id(cfg, "custom", "sam@AI.example.com") == "AI-mail"
    long = cli._suggest_id(AppConfig(accounts=[AccountConfig(id="x" * 30, email="a@example.com")]),
                           "custom", ("y" * 25) + "@" + ("x" * 30) + ".example.com")
    assert len(long) <= 40 and cli.account_id_problem(long) is None


def test_wizard_refuses_reserved_names(env, capsys) -> None:
    script(env, answers=["sam@anthropic.example.com", "imap.example.com", "", "",   # custom server
                         "anthropic", "AI", "a:b", "",                            # 3 refused, then the suggestion
                         "n"],
           passwords=[GOOD])
    assert cli.main(["accounts", "add"]) == 0
    out = capsys.readouterr().out
    assert out.count("reserved for TacoSort's own use") == 2 and "no spaces" in out
    assert [a.id for a in load_config(env.path).accounts] == ["anthropic-mail"]
    assert env.secrets == {"anthropic-mail": GOOD}


# S-N4: a password reaches the keychain only after it worked.

def test_password_tested_before_it_is_saved(env, capsys) -> None:
    saved_when: list[bool] = []
    real_make = cli.make_backend

    def make(acct, secret_getter=None):
        saved_when.append(acct.id in env.secrets)
        return real_make(acct, secret_getter)

    env.monkeypatch.setattr(cli, "make_backend", make)
    script(env, answers=["alice@gmail.example.com", "", "n"], passwords=[GOOD])
    assert cli.main(["accounts", "add"]) == 0
    assert saved_when == [False] and env.secrets == {"gmail": GOOD}


def test_failed_password_leaves_nothing_behind(env, capsys) -> None:
    script(env, answers=["alice@gmail.example.com", "", "n", "n"], passwords=["nope"])
    assert cli.main(["accounts", "add"]) == 1
    assert env.secrets == {}


def test_save_anyway_keeps_account_without_password(env, capsys) -> None:
    script(env, answers=["alice@gmail.example.com", "", "n", "y"], passwords=["nope"])
    assert cli.main(["accounts", "add"]) == 0
    assert [a.id for a in load_config(env.path).accounts] == ["gmail"]
    assert env.secrets == {}


def test_new_failing_password_never_replaces_working_one(env, capsys) -> None:
    with_accounts(env, "gmail")
    script(env, answers=["gmail@example.com", "y", "n"], passwords=["typo"])
    assert cli.main(["accounts", "add"]) == 1
    assert env.secrets["gmail"] == GOOD
    out = capsys.readouterr().out
    assert "already had is kept" in out and "Password updated" not in out


def test_outlook_token_removed_when_account_not_saved(env, capsys) -> None:
    env.auth.microsoft_device_login = lambda acct, log=print: env.secrets.__setitem__(acct.id, "token-no-imap")
    script(env, answers=["carol@outlook.example.com", "", "00000000-1111-2222-3333-444444444444",
                         "n",                      # save anyway? no
                         "n"])                     # add another account
    assert cli.main(["setup"]) == 0
    assert env.secrets == {} and load_config(env.path).accounts == []


def test_accounts_remove_deletes_every_secret(env, capsys) -> None:
    with_accounts(env, "gmail", "yahoo")
    env.secrets["gmail:client-secret"] = "client-secret"
    assert cli.main(["accounts", "remove", "gmail", "--yes"]) == 0
    assert env.secrets == {"yahoo": GOOD}


def test_accounts_remove_keep_password(env, capsys) -> None:
    with_accounts(env, "gmail")
    assert cli.main(["accounts", "remove", "gmail", "--yes", "--keep-password"]) == 0
    assert env.secrets == {"gmail": GOOD}


# T-S9: the AI key goes in the keychain, where the background schedule can find it.

def test_config_ai_key_saves_and_removes(env, capsys) -> None:
    script(env, answers=[], passwords=["  sk-ant-example-key \n"])
    assert cli.main(["config", "ai-key"]) == 0
    assert env.secrets == {"ai:anthropic": "sk-ant-example-key"}
    out = capsys.readouterr().out
    assert "sk-ant" not in out and "still off" in out
    assert cli.main(["config", "ai-key", "--remove"]) == 0
    assert env.secrets == {}


def test_config_ai_key_empty_changes_nothing(env, capsys) -> None:
    env.secrets["ai:anthropic"] = "old"
    script(env, answers=[], passwords=[""])
    assert cli.main(["config", "ai-key"]) == 1
    assert env.secrets == {"ai:anthropic": "old"}


def test_config_ai_key_keychain_failure_is_plain(env, capsys) -> None:
    def broken(value):
        raise BackendError("No keychain is available on this computer.")

    env.auth.set_ai_key = broken
    script(env, answers=[], passwords=["sk-ant-example"])
    assert cli.main(["config", "ai-key"]) == 1
    assert "No keychain" in capsys.readouterr().err


def test_setup_mentions_ai_key_when_ai_is_on(env, capsys) -> None:
    with_accounts(env, "gmail")
    cfg = load_config(env.path)
    cfg.ai.enabled = True
    save_config(cfg, env.path)
    script(env, answers=["n", "n", "n", "n"])   # add another, learn, preview, schedule
    assert cli.main(["setup"]) == 0
    assert "`tacosort config ai-key`" in capsys.readouterr().out


# S-S3: on Linux/macOS everything TacoSort writes is private to the user.

class _PosixOs:
    """`os` as cli.py sees it, pretending to be Linux, with a recorded umask."""

    name = "posix"

    def __init__(self) -> None:
        self.mask = 0o022
        self.calls: list[int] = []

    def umask(self, mask: int) -> int:
        old, self.mask = self.mask, mask
        self.calls.append(mask)
        return old

    def __getattr__(self, attr):
        import os
        return getattr(os, attr)


def test_main_sets_private_umask_and_folders(env, capsys) -> None:
    fake_os = _PosixOs()
    tightened: list[str] = []
    masks_while_running: list[int] = []
    env.monkeypatch.setattr(cli, "os", fake_os)
    env.monkeypatch.setattr(cli, "make_private", lambda folder: tightened.append(str(folder)))
    env.monkeypatch.setattr(cli, "say", lambda *parts: masks_while_running.append(fake_os.mask))
    assert cli.main(["config", "path"]) == 0
    assert masks_while_running and set(masks_while_running) == {0o077}
    assert fake_os.mask == 0o022                      # put back afterwards
    assert tightened == [str(env.path.parent), str(env.data)]


def test_main_leaves_folders_people_chose_alone(env, tmp_path, capsys) -> None:
    fake_os = _PosixOs()
    tightened: list[str] = []
    env.monkeypatch.setattr(cli, "os", fake_os)
    env.monkeypatch.setattr(cli, "make_private", lambda folder: tightened.append(str(folder)))
    own = tmp_path / "Documents" / "my-settings.yaml"
    assert cli.main(["--config", str(own), "config", "path"]) == 0
    assert str(own.parent) not in tightened


@pytest.mark.skipif(cli.os.name != "posix", reason="file permissions are a Linux/macOS matter")
def test_files_and_folders_are_private_on_posix(env, capsys) -> None:
    env.path.parent.mkdir(parents=True)
    env.path.parent.chmod(0o755)
    env.data.mkdir(parents=True)
    env.data.chmod(0o755)
    with_accounts(env, "gmail")
    assert cli.main(["report"]) == 0
    assert env.path.parent.stat().st_mode & 0o777 == 0o700
    assert env.data.stat().st_mode & 0o777 == 0o700
    assert (env.data / "report.html").stat().st_mode & 0o777 == 0o600
