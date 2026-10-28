"""Settings file: paths, round trip, friendly warnings and problem checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from tacosort import config as cfgmod
from tacosort.config import (
    ConfigError,
    config_problems,
    default_config_path,
    default_data_dir,
    dump_config,
    load_config,
    save_config,
)
from tacosort.models import AccountConfig, AIConfig, AppConfig, Rule


def full_config() -> AppConfig:
    cfg = AppConfig()
    cfg.accounts = [
        AccountConfig(id="gmail", email="alice@example.com", provider="gmail", host="imap.gmail.com"),
        AccountConfig(id="work", email="alice@work.example.com", provider="custom", host="mail.example.com",
                      port=143, ssl=False, username="alice", watch_folders=["INBOX", "Later"]),
    ]
    cfg.categories["promotions"] = ""          # turned off
    cfg.categories["receipts"] = "Shopping"    # renamed
    cfg.categories["kids"] = "Kids School"     # custom
    cfg.folder_prefix = "TacoSort"
    cfg.rules = [
        Rule(name="school", action="kids", from_=["*@school.example.com"], subject=["field trip"]),
        Rule(name="boss", action="inbox", from_=["boss@example.com"], accounts=["work"]),
        Rule(name="lists", action="folder:Lists/Dev", list_id=["dev.example.com"], header={"x-tag": "dev"}),
    ]
    cfg.vip = ["mom@example.com", "example.org"]
    cfg.min_confidence = 0.8
    cfg.ai = AIConfig(enabled=True, max_per_run=10, send_snippet=False)
    cfg.interval_minutes = 30
    return cfg


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.yaml"
    original = full_config()
    save_config(original, path)
    loaded = load_config(path)
    assert loaded.accounts == original.accounts
    assert loaded.rules == original.rules
    assert loaded.categories == original.categories
    assert loaded.folder_prefix == "TacoSort"
    assert loaded.vip == original.vip
    assert loaded.ai == original.ai
    assert loaded.min_confidence == 0.8 and loaded.interval_minutes == 30
    assert loaded.folder_for("promotions") is None
    assert loaded.folder_for("receipts") == "TacoSort/Shopping"


def test_saved_file_is_friendly(tmp_path: Path) -> None:
    path = save_config(full_config(), tmp_path / "config.yaml")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# TacoSort settings")
    assert "Passwords are NOT stored here" in text
    assert "promotions: 'off'" in text or "promotions: off" in text
    assert "  from:" in text and "from_:" not in text
    assert text.index("accounts:") < text.index("categories:") < text.index("rules:")
    keys = [line.split(":")[0].strip(" -") for line in text.splitlines() if ":" in line and not line.startswith("#")]
    assert not {"password", "secret", "token"} & set(keys)
    assert list(tmp_path.glob(".config-*")) == []   # no temp file left behind


def test_save_replaces_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    save_config(full_config(), path)
    cfg = load_config(path)
    cfg.accounts.pop()
    save_config(cfg, path)
    assert [a.id for a in load_config(path).accounts] == ["gmail"]


def test_missing_file_is_empty_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.accounts == [] and cfg.folder_for("receipts") == "Receipts"


def test_yaml_from_and_strings(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "accounts:\n  - email: bob@example.com\n    provider: yahoo\n"
        "categories:\n  promotions: off\n  newsletters: Reading\n  inbox: Whatever\n"
        "rules:\n  - name: one\n    from: news@example.com\n    action: newsletters\n"
        "vip: Mom@Example.com\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.accounts[0].id == "bob"
    assert cfg.rules[0].from_ == ["news@example.com"]
    assert cfg.categories["promotions"] == "" and cfg.categories["newsletters"] == "Reading"
    assert cfg.categories["inbox"] == ""
    assert cfg.vip == ["mom@example.com"]


def test_unknown_keys_warn_not_crash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "colour: blue\naccounts:\n  - email: bob@example.com\n    password: hunter2\n    nickname: b\n"
        "rules:\n  - name: r\n    subjet: typo\n    from_: a@example.com\n    action: inbox\n",
        encoding="utf-8",
    )
    warnings: list[str] = []
    cfg = load_config(path, warnings=warnings)
    text = " ".join(warnings)
    assert "colour" in text and "nickname" in text and "subjet" in text
    assert "keychain" in text            # the password line is called out
    assert "hunter2" not in text          # ...without repeating it
    assert cfg.rules[0].from_ == ["a@example.com"]
    load_config(path)                     # without a list: printed to stderr instead
    assert "colour" in capsys.readouterr().err


@pytest.mark.parametrize("text, words", [
    ("accounts: [\n", "line"),
    ("accounts:\n  - email: a@example.com\n    port: lots\n", "whole number"),
    ("accounts:\n  - id: x\n", "no 'email'"),
    ("rules:\n  - name: r\n    from: a@example.com\n", "no 'action'"),
    ("- just\n- a list\n", "top level"),
    ("min_confidence: high\n", "number"),
    ("keep_flagged: maybe\n", "yes or no"),
])
def test_real_mistakes_are_plain_errors(tmp_path: Path, text: str, words: str) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=words):
        load_config(path)


def test_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TACOSORT_CONFIG", str(tmp_path / "x.yaml"))
    monkeypatch.setenv("TACOSORT_DATA", str(tmp_path / "data"))
    assert default_config_path() == tmp_path / "x.yaml"
    assert default_data_dir() == tmp_path / "data"
    cfg = AppConfig(data_dir=str(tmp_path / "elsewhere"))
    assert cfgmod.data_dir(cfg) == tmp_path / "data"    # the environment wins
    assert cfgmod.state_path(cfg) == tmp_path / "data" / "state.sqlite3"


def test_data_dir_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TACOSORT_DATA", raising=False)
    assert cfgmod.data_dir(AppConfig(data_dir=str(tmp_path))) == tmp_path


@pytest.mark.parametrize("platform, expect_cfg, expect_data", [
    ("win32", ("Roaming", "TacoSort", "config.yaml"), ("Local", "TacoSort")),
    ("darwin", ("Library", "Application Support", "TacoSort", "config.yaml"),
     ("Library", "Application Support", "TacoSort")),
    ("linux", (".config", "tacosort", "config.yaml"), (".local", "share", "tacosort")),
])
def test_platform_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
                        expect_cfg: tuple, expect_data: tuple) -> None:
    for var in ("TACOSORT_CONFIG", "TACOSORT_DATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cfgmod.sys, "platform", platform)
    monkeypatch.setattr(cfgmod.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert default_config_path().parts[-len(expect_cfg):] == expect_cfg
    assert default_data_dir().parts[-len(expect_data):] == expect_data


def test_xdg_respected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TACOSORT_CONFIG", raising=False)
    monkeypatch.setattr(cfgmod.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_config_path() == tmp_path / "xdg" / "tacosort" / "config.yaml"


def test_good_config_has_no_problems() -> None:
    assert config_problems(full_config()) == []


def test_config_problems() -> None:
    cfg = full_config()
    cfg.accounts.append(AccountConfig(id="gmail", email="not-an-email", provider="hotmale", port=99999,
                                      auth="magic", watch_folders=[]))
    cfg.accounts.append(AccountConfig(id="c", email="c@example.com", provider="custom"))
    cfg.accounts.append(AccountConfig(id="o", email="o@example.com", provider="outlook", auth="oauth2-microsoft"))
    cfg.rules += [
        Rule(name="bad target", action="shopping", from_=["a@example.com"]),
        Rule(name="off target", action="promotions", from_=["a@example.com"]),
        Rule(name="everything", action="inbox"),
        Rule(name="ghost", action="inbox", from_=["a@example.com"], accounts=["nobody"]),
        Rule(name="ghost", action="folder:", from_=["a@example.com"]),
    ]
    cfg.min_confidence = 2
    cfg.interval_minutes = 0
    cfg.categories["finance"] = "INBOX"
    text = "\n".join(config_problems(cfg))
    for words in ("both called 'gmail'", "doesn't look like an email", "unknown provider 'hotmale'",
                  "port 99999", "auth 'magic'", "watch_folders", "needs 'host'", "oauth_client_id",
                  "'shopping', which isn't a category", "turned off", "no conditions", "'nobody'",
                  "both called 'ghost'", "no folder name", "min_confidence", "interval_minutes",
                  "points at the Inbox"):
        assert words in text, words


def test_never_writes_secret_keys() -> None:
    with pytest.raises(ConfigError, match="keychain"):
        cfgmod._assert_no_secrets({"accounts": [{"id": "a", "password": "x"}]})
    assert "hunter2" not in dump_config(full_config())


def test_example_config_is_valid() -> None:
    path = Path(__file__).resolve().parent.parent / "examples" / "config.example.yaml"
    warnings: list[str] = []
    cfg = load_config(path, warnings=warnings)
    assert warnings == []
    assert config_problems(cfg) == []
    assert len(cfg.accounts) == 2 and len(cfg.rules) == 3 and cfg.vip
    assert cfg.ai.enabled is False
    assert cfg.folder_for("notifications") is None and cfg.folder_for("kids") == "Kids School"


# ---- fix wave (2026-10-28) ------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["anthropic", "Anthropic", "ai", "AI", "x:client-secret", "account:me",
                                    "has space", "-starts-with-dash", "x" * 41])
def test_account_ids_from_the_file_are_checked(bad_id: str) -> None:
    """S-S1: an id from config.yaml names a keychain entry, so the same rules apply as in the wizard."""
    cfg = AppConfig(accounts=[AccountConfig(id=bad_id, email="sam@example.com", provider="gmail")])
    text = "\n".join(config_problems(cfg))
    assert "can't use that id" in text, bad_id


def test_good_account_ids_pass() -> None:
    for good in ("gmail", "work-2", "alice.smith", "anthropic-mail", "a_i"):
        cfg = AppConfig(accounts=[AccountConfig(id=good, email="sam@example.com", provider="gmail")])
        assert config_problems(cfg) == [], good


def test_default_id_from_email_is_usable(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("accounts:\n  - email: alice+news@example.com\n    provider: gmail\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.accounts[0].id == "alice-news"
    assert config_problems(cfg) == []


@pytest.mark.parametrize("written", ["Inbox", "inbox", "INBOX", " iNbOx "])
def test_inbox_spelling_is_normalised(tmp_path: Path, written: str) -> None:
    """C-S1: the watermark is keyed by the server's name, which is always INBOX."""
    path = tmp_path / "config.yaml"
    path.write_text("accounts:\n  - id: a\n    email: a@example.com\n    provider: gmail\n"
                    f"    watch_folders: ['{written}', Later]\n", encoding="utf-8")
    assert load_config(path).accounts[0].watch_folders == ["INBOX", "Later"]


def test_make_private_skips_windows_and_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(cfgmod.os, "chmod", lambda p, m: calls.append((str(p), m)))
    monkeypatch.setattr(cfgmod.os, "name", "posix")
    monkeypatch.setattr(cfgmod.Path, "home", classmethod(lambda cls: tmp_path))
    folder = tmp_path / "tacosort"
    folder.mkdir()
    cfgmod.make_private(folder)
    cfgmod.make_private(tmp_path)             # the home folder: never changed
    cfgmod.make_private(tmp_path / "nope")    # missing: nothing to do
    assert calls == [(str(folder), 0o700)]
    monkeypatch.setattr(cfgmod.os, "name", "nt")
    cfgmod.make_private(folder)
    assert len(calls) == 1


@pytest.mark.skipif(cfgmod.os.name != "posix", reason="file permissions are a Linux/macOS matter")
def test_make_private_really_changes_mode(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    cfgmod.make_private(folder)
    assert folder.stat().st_mode & 0o777 == 0o700
