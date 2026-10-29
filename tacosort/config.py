"""Reading and writing the TacoSort settings file (config.yaml).

The file is plain YAML so people can edit it by hand. It never holds passwords: those live in the
operating system's keychain (see `tacosort.imap.auth`).
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from .models import DEFAULT_CATEGORIES, INBOX, AccountConfig, AIConfig, AppConfig, Rule

CONFIG_FILENAME = "config.yaml"
STATE_FILENAME = "state.sqlite3"

# Provider keys we accept when the IMAP module can't be imported (keeps config checks working alone).
_FALLBACK_PROVIDERS = {
    "gmail", "outlook", "yahoo", "icloud", "aol", "fastmail", "zoho", "gmx", "gmx-net", "protonmail-bridge",
    "custom",
}
AUTH_KINDS = ("password", "oauth2-google", "oauth2-microsoft")

# Words that turn a category off in YAML (`promotions: off`). YAML itself turns off/no/false into False.
_OFF_WORDS = {"off", "none", "no", "false", "disabled", ""}

# Keys that must never appear in the file (we refuse to write them and warn when reading them).
_SECRET_KEYS = {"password", "app_password", "secret", "token", "refresh_token", "access_token", "api_key"}

# An account id names the account's keychain entry, so it is kept short and plain. A few names are
# reserved because the keychain uses them for other things (the AI key, for one).
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
RESERVED_IDS = {"anthropic", "ai"}


def account_id_problem(account_id: str) -> str | None:
    """Why `account_id` can't be used as an account's short name, or None when it is fine."""
    if account_id.lower() in RESERVED_IDS:
        return f"'{account_id}' is reserved for TacoSort's own use; pick another short name."
    if not ACCOUNT_ID_RE.match(account_id):
        return "Use letters, numbers, dots, dashes or underscores (no spaces), up to 40 characters."
    return None

_HEADER = """\
# TacoSort settings
#
# This file says which email accounts TacoSort looks after and how it sorts your mail.
# You can edit it by hand (it is plain text); run `tacosort config check` afterwards to spot mistakes.
# Passwords are NOT stored here: they are kept in your computer's keychain.
#
#   accounts:   your mailboxes (add more with `tacosort accounts add`)
#   categories: category -> folder name. Rename a folder here, or write `off` to keep that mail in the Inbox
#   rules:      your own rules; they win over TacoSort's built-in guesses
#   vip:        addresses or domains whose mail always stays in the Inbox
#
# Full guide: docs/rules.md in the TacoSort project (examples/config.example.yaml shows every setting)
"""


class ConfigError(Exception):
    """The settings file can't be used; the message says why in plain English."""


# ---- where things live ------------------------------------------------------------------------------

def _platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def default_config_path() -> Path:
    """Where the settings file lives: $TACOSORT_CONFIG, else the usual place for this operating system."""
    env = os.environ.get("TACOSORT_CONFIG")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    plat = _platform()
    if plat == "windows":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return base / "TacoSort" / CONFIG_FILENAME
    if plat == "macos":
        return home / "Library" / "Application Support" / "TacoSort" / CONFIG_FILENAME
    base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "tacosort" / CONFIG_FILENAME


def default_data_dir() -> Path:
    """Where TacoSort keeps its memory (state.sqlite3, log, report): $TACOSORT_DATA, else the OS default."""
    env = os.environ.get("TACOSORT_DATA")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    plat = _platform()
    if plat == "windows":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "TacoSort"
    if plat == "macos":
        return home / "Library" / "Application Support" / "TacoSort"
    base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "tacosort"


def data_dir(cfg: AppConfig) -> Path:
    """The data folder for this config ($TACOSORT_DATA wins, then `data_dir:` in the file, then the default)."""
    if os.environ.get("TACOSORT_DATA"):
        return default_data_dir()
    if cfg.data_dir:
        return Path(cfg.data_dir).expanduser()
    return default_data_dir()


def state_path(cfg: AppConfig) -> Path:
    """Full path of the SQLite memory file."""
    return data_dir(cfg) / STATE_FILENAME


def make_private(folder: Path) -> None:
    """On Linux and macOS, let only this user open `folder` (it holds senders, subjects and contacts).

    Windows keeps these folders per user already, so nothing is done there. The home folder itself is
    never changed, in case someone points `data_dir` at it."""
    if os.name != "posix" or not folder.is_dir():
        return
    try:
        if folder.resolve() == Path.home().resolve():
            return
        os.chmod(folder, 0o700)
    except OSError:
        pass   # someone else's folder (data_dir set by hand): leave it as it is


# ---- loading ---------------------------------------------------------------------------------------

def _as_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    raise ConfigError(f"{where} should be a word or a list of words.")


def _check_keys(data: dict, allowed: set[str], where: str, warnings: list[str]) -> None:
    for key in data:
        if str(key).lower() in _SECRET_KEYS:
            warnings.append(
                f"{where} has a '{key}' entry. Passwords don't belong in this file: TacoSort ignores it and "
                "uses your keychain instead. Please delete that line."
            )
        elif key not in allowed:
            warnings.append(f"{where} has an unknown setting '{key}'; it is ignored (typo?).")


def _need_mapping(value: Any, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where} should be a set of 'name: value' lines.")
    return value


def _to_int(value: Any, where: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{where} should be a whole number, not yes/no.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where} should be a whole number (it is {value!r}).") from None


def _to_float(value: Any, where: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{where} should be a number, not yes/no.")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where} should be a number (it is {value!r}).") from None


def _to_bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("yes", "true", "on", "1"):
        return True
    if isinstance(value, str) and value.lower() in ("no", "false", "off", "0"):
        return False
    raise ConfigError(f"{where} should be yes or no (it is {value!r}).")


def _account_from(data: Any, n: int, warnings: list[str]) -> AccountConfig:
    where = f"Account #{n}"
    data = _need_mapping(data, where)
    _check_keys(data, {f.name for f in fields(AccountConfig)}, where, warnings)
    if not data.get("email"):
        raise ConfigError(f"{where} has no 'email'.")
    email = str(data["email"]).strip()
    default_id = re.sub(r"[^A-Za-z0-9._-]", "-", email.split("@")[0]).strip("-.") or f"account{n}"
    acct = AccountConfig(id=str(data.get("id") or default_id), email=email)
    where = f"Account '{acct.id}'"
    if "provider" in data:
        acct.provider = str(data["provider"] or "custom").strip().lower()
    if "host" in data:
        acct.host = str(data["host"] or "").strip()
    if "port" in data:
        acct.port = _to_int(data["port"], f"{where}: port")
    if "ssl" in data:
        acct.ssl = _to_bool(data["ssl"], f"{where}: ssl")
    if "username" in data:
        acct.username = str(data["username"] or "")
    if "auth" in data:
        acct.auth = str(data["auth"] or "password").strip().lower()
    if "oauth_client_id" in data:
        acct.oauth_client_id = str(data["oauth_client_id"] or "")
    if "watch_folders" in data:
        # Servers always call the Inbox "INBOX"; people write "Inbox" or "inbox".
        acct.watch_folders = ["INBOX" if f.strip().lower() == "inbox" else f
                              for f in _as_list(data["watch_folders"], f"{where}: watch_folders")]
    if "enabled" in data:
        acct.enabled = _to_bool(data["enabled"], f"{where}: enabled")
    return acct


_RULE_KEYS = {"name", "action", "from", "to", "subject", "list_id", "header", "accounts"}


def _rule_from(data: Any, n: int, warnings: list[str]) -> Rule:
    where = f"Rule #{n}"
    data = _need_mapping(data, where)
    if "from_" in data and "from" not in data:   # people who copy the Python name
        data = dict(data)
        data["from"] = data.pop("from_")
    _check_keys(data, _RULE_KEYS, where, warnings)
    name = str(data.get("name") or f"rule {n}")
    where = f"Rule '{name}'"
    if not data.get("action"):
        raise ConfigError(f"{where} has no 'action' (where should the mail go?).")
    header = _need_mapping(data.get("header"), f"{where}: header")
    return Rule(
        name=name,
        action=str(data["action"]).strip(),
        from_=_as_list(data.get("from"), f"{where}: from"),
        to=_as_list(data.get("to"), f"{where}: to"),
        subject=_as_list(data.get("subject"), f"{where}: subject"),
        list_id=_as_list(data.get("list_id"), f"{where}: list_id"),
        header={str(k).lower(): str(v) for k, v in header.items()},
        accounts=_as_list(data.get("accounts"), f"{where}: accounts"),
    )


def _ai_from(data: Any, warnings: list[str]) -> AIConfig:
    data = _need_mapping(data, "The 'ai' section")
    _check_keys(data, {f.name for f in fields(AIConfig)}, "The 'ai' section", warnings)
    ai = AIConfig()
    if "enabled" in data:
        ai.enabled = _to_bool(data["enabled"], "ai: enabled")
    if "model" in data:
        ai.model = str(data["model"])
    if "max_per_run" in data:
        ai.max_per_run = _to_int(data["max_per_run"], "ai: max_per_run")
    if "send_snippet" in data:
        ai.send_snippet = _to_bool(data["send_snippet"], "ai: send_snippet")
    if "api_key_env" in data:
        ai.api_key_env = str(data["api_key_env"])
    return ai


def _categories_from(data: Any) -> dict[str, str]:
    cats = {k: v[0] for k, v in DEFAULT_CATEGORIES.items()}
    for key, value in _need_mapping(data, "The 'categories' section").items():
        key = str(key).strip().lower()
        if key == INBOX:
            continue  # the Inbox always stays the Inbox
        if value is None or value is False or (isinstance(value, str) and value.strip().lower() in _OFF_WORDS):
            cats[key] = ""
        else:
            cats[key] = str(value).strip().strip("/")
    return cats


_TOP_KEYS = {f.name for f in fields(AppConfig)}


def config_from_dict(data: Any, warnings: list[str] | None = None) -> AppConfig:
    """Turn parsed YAML into an AppConfig. Raises ConfigError on things we can't guess."""
    warnings = warnings if warnings is not None else []
    if data is None:
        return AppConfig()
    if not isinstance(data, dict):
        raise ConfigError("The settings file should be a list of 'name: value' lines at the top level.")
    _check_keys(data, _TOP_KEYS, "The settings file", warnings)
    cfg = AppConfig()
    accounts = data.get("accounts") or []
    if not isinstance(accounts, list):
        raise ConfigError("'accounts' should be a list (each account starts with '- id: ...').")
    cfg.accounts = [_account_from(a, i, warnings) for i, a in enumerate(accounts, 1)]
    if "categories" in data:
        cfg.categories = _categories_from(data["categories"])
    if "folder_prefix" in data:
        cfg.folder_prefix = str(data["folder_prefix"] or "").strip().strip("/")
    rules = data.get("rules") or []
    if not isinstance(rules, list):
        raise ConfigError("'rules' should be a list (each rule starts with '- name: ...').")
    cfg.rules = [_rule_from(r, i, warnings) for i, r in enumerate(rules, 1)]
    cfg.vip = [v.strip().lower() for v in _as_list(data.get("vip"), "'vip'")]
    if "min_confidence" in data:
        cfg.min_confidence = _to_float(data["min_confidence"], "min_confidence")
    for flag in ("keep_flagged", "keep_contacts", "learn_from_corrections"):
        if flag in data:
            setattr(cfg, flag, _to_bool(data[flag], flag))
    if "ai" in data:
        cfg.ai = _ai_from(data["ai"], warnings)
    if "interval_minutes" in data:
        cfg.interval_minutes = _to_int(data["interval_minutes"], "interval_minutes")
    if "max_moves_per_run" in data:
        cfg.max_moves_per_run = _to_int(data["max_moves_per_run"], "max_moves_per_run")
    if "company_folders" in data:
        cfg.company_folders = _to_bool(data["company_folders"], "company_folders")
    if "company_folder_min" in data:
        cfg.company_folder_min = _to_int(data["company_folder_min"], "company_folder_min")
    if "unsure_to_company" in data:
        cfg.unsure_to_company = _to_bool(data["unsure_to_company"], "unsure_to_company")
    if "hold_hours" in data:
        cfg.hold_hours = _to_int(data["hold_hours"], "hold_hours")
    if "look_alike_min" in data:
        cfg.look_alike_min = _to_int(data["look_alike_min"], "look_alike_min")
    if "unsure_to_folder" in data:
        cfg.unsure_to_folder = _to_bool(data["unsure_to_folder"], "unsure_to_folder")
    if "security_keep_days" in data:
        cfg.security_keep_days = _to_int(data["security_keep_days"], "security_keep_days")
    if "business_keep_days" in data:
        cfg.business_keep_days = _to_int(data["business_keep_days"], "business_keep_days")
    if "security_folder" in data:
        cfg.security_folder = str(data["security_folder"] or "").strip()
    if "recheck_inbox" in data:
        cfg.recheck_inbox = _to_bool(data["recheck_inbox"], "recheck_inbox")
    if "data_dir" in data:
        cfg.data_dir = str(data["data_dir"] or "")
    return cfg


def load_config(path: Path | str | None = None, *, warnings: list[str] | None = None) -> AppConfig:
    """Read the settings file. A missing file gives empty settings (no accounts yet).

    Unknown or misplaced settings don't stop anything: they are added to `warnings` when a list is given,
    otherwise printed to stderr. Real mistakes (broken YAML, a word where a number belongs) raise ConfigError.
    """
    path = Path(path) if path else default_config_path()
    collected: list[str] = []
    if not path.exists():
        return AppConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"Can't read the settings file {path}: {e.strerror or e}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(
            f"The settings file {path} has a typing mistake{where} (often a missing space after ':' "
            "or uneven indentation)."
        ) from None
    cfg = config_from_dict(data, collected)
    if warnings is not None:
        warnings.extend(collected)
    else:
        for w in collected:
            print(f"Note: {w}", file=sys.stderr)
    return cfg


# ---- saving ----------------------------------------------------------------------------------------

def _account_to_dict(a: AccountConfig) -> dict:
    out: dict[str, Any] = {"id": a.id, "email": a.email, "provider": a.provider}
    if a.host:
        out["host"] = a.host
    out["port"] = a.port
    out["ssl"] = a.ssl
    if a.username and a.username != a.email:
        out["username"] = a.username
    out["auth"] = a.auth
    if a.oauth_client_id:
        out["oauth_client_id"] = a.oauth_client_id
    out["watch_folders"] = list(a.watch_folders)
    out["enabled"] = a.enabled
    return out


def _rule_to_dict(r: Rule) -> dict:
    out: dict[str, Any] = {"name": r.name}
    for key, value in (("from", r.from_), ("to", r.to), ("subject", r.subject), ("list_id", r.list_id),
                       ("header", r.header), ("accounts", r.accounts)):
        if value:
            out[key] = list(value) if isinstance(value, list) else dict(value)
    out["action"] = r.action
    return out


def config_to_dict(cfg: AppConfig) -> dict:
    """AppConfig -> plain dict in the order people expect to read it."""
    categories: dict[str, str] = {}
    for key, name in cfg.categories.items():
        if key == INBOX:
            continue
        categories[key] = name if name else "off"
    return {
        "accounts": [_account_to_dict(a) for a in cfg.accounts],
        "categories": categories,
        "folder_prefix": cfg.folder_prefix,
        "rules": [_rule_to_dict(r) for r in cfg.rules],
        "vip": list(cfg.vip),
        "min_confidence": cfg.min_confidence,
        "keep_flagged": cfg.keep_flagged,
        "keep_contacts": cfg.keep_contacts,
        "learn_from_corrections": cfg.learn_from_corrections,
        "interval_minutes": cfg.interval_minutes,
        "max_moves_per_run": cfg.max_moves_per_run,
        "company_folders": cfg.company_folders,
        "company_folder_min": cfg.company_folder_min,
        "unsure_to_company": cfg.unsure_to_company,
        "hold_hours": cfg.hold_hours,
        "look_alike_min": cfg.look_alike_min,
        "unsure_to_folder": cfg.unsure_to_folder,
        "recheck_inbox": cfg.recheck_inbox,
        "security_keep_days": cfg.security_keep_days,
        "security_folder": cfg.security_folder,
        "business_keep_days": cfg.business_keep_days,
        "ai": {
            "enabled": cfg.ai.enabled,
            "model": cfg.ai.model,
            "max_per_run": cfg.ai.max_per_run,
            "send_snippet": cfg.ai.send_snippet,
            "api_key_env": cfg.ai.api_key_env,
        },
        "data_dir": cfg.data_dir,
    }


def _assert_no_secrets(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _SECRET_KEYS:
                raise ConfigError(f"Refusing to write '{k}' into the settings file: secrets belong in the keychain.")
            _assert_no_secrets(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_secrets(v)


def dump_config(cfg: AppConfig) -> str:
    """The text save_config writes (header comments + YAML)."""
    data = config_to_dict(cfg)
    _assert_no_secrets(data)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
    return _HEADER + "\n" + body


def save_config(cfg: AppConfig, path: Path | str | None = None) -> Path:
    """Write the settings file atomically (a half-written file can never replace a good one)."""
    path = Path(path) if path else default_config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = dump_config(cfg)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ---- checking --------------------------------------------------------------------------------------

def _known_providers() -> set[str]:
    try:
        from .imap.providers import PROVIDERS
        return set(PROVIDERS)
    except ImportError:
        return set(_FALLBACK_PROVIDERS)


def _rule_target_problem(action: str, cfg: AppConfig) -> str | None:
    action = action.strip()
    low = action.lower()
    if low == INBOX:
        return None
    if low.startswith("folder:"):
        return None if action[7:].strip() else "says 'folder:' but gives no folder name"
    if low not in cfg.categories:
        known = ", ".join(sorted(k for k in cfg.categories if k != INBOX))
        return f"sends mail to '{action}', which isn't a category (known: inbox, {known}, or folder:<name>)"
    if not cfg.categories.get(low):
        return f"sends mail to '{action}', but that category is turned off, so the mail will stay in the Inbox"
    return None


def config_problems(cfg: AppConfig) -> list[str]:
    """Plain-English list of mistakes in the settings. Empty list = all good."""
    problems: list[str] = []
    providers = _known_providers()
    seen: set[str] = set()
    for a in cfg.accounts:
        label = f"Account '{a.id}'"
        if not a.id.strip():
            problems.append(f"An account ({a.email}) has an empty id.")
        elif a.id in seen:
            problems.append(f"Two accounts are both called '{a.id}'. Each account needs its own id.")
        elif (issue := account_id_problem(a.id)) is not None:
            problems.append(f"{label} ({a.email}) can't use that id. {issue}")
        seen.add(a.id)
        if "@" not in a.email:
            problems.append(f"{label}: '{a.email}' doesn't look like an email address.")
        if a.provider not in providers:
            problems.append(f"{label}: unknown provider '{a.provider}' (known: {', '.join(sorted(providers))}).")
        if a.provider == "custom" and not a.host:
            problems.append(f"{label}: a custom provider needs 'host' (your mail server's IMAP address).")
        if not 1 <= a.port <= 65535:
            problems.append(f"{label}: port {a.port} isn't valid (it must be between 1 and 65535; usually 993).")
        if a.auth not in AUTH_KINDS:
            problems.append(f"{label}: auth '{a.auth}' isn't known (use one of: {', '.join(AUTH_KINDS)}).")
        if a.auth.startswith("oauth2-") and not a.oauth_client_id:
            problems.append(f"{label}: {a.auth} needs 'oauth_client_id' (see docs/outlook-oauth.md).")
        if not a.watch_folders:
            problems.append(f"{label}: 'watch_folders' is empty, so nothing would be sorted (usually INBOX).")
    for key, name in cfg.categories.items():
        if key == INBOX or not name:
            continue
        if name.strip().upper() == "INBOX":
            problems.append(f"Category '{key}' points at the Inbox; write 'off' instead to keep that mail there.")
    if not 0 <= cfg.min_confidence <= 1:
        problems.append(f"min_confidence is {cfg.min_confidence}; it must be between 0 and 1 (0.75 is a good value).")
    if not 1 <= cfg.interval_minutes <= 1439:
        problems.append("interval_minutes must be between 1 and 1439 (just under a day); 15 is a good value.")
    if cfg.max_moves_per_run < 1:
        problems.append("max_moves_per_run must be at least 1.")
    if cfg.company_folder_min < 1:
        problems.append("company_folder_min must be at least 1 (3 is a good value); use company_folders: no "
                        "to turn company folders off.")
    if not 0 <= cfg.hold_hours <= 720:
        problems.append("hold_hours must be between 0 (sort at once) and 720 (30 days); 24 keeps new mail "
                        "in the Inbox for a day.")
    if not 0 <= cfg.security_keep_days <= 365:
        problems.append("security_keep_days must be between 0 (security emails stay in the Inbox) and 365; "
                        "3 moves them out once they are 3 days old.")
    if cfg.security_keep_days and (not cfg.security_folder or "/" in cfg.security_folder
                                   or cfg.security_folder.lower() == "inbox"):
        problems.append("security_folder must be a plain folder name such as Security.")
    if not 0 <= cfg.business_keep_days <= 365:
        problems.append("business_keep_days must be between 0 (people's business mail stays in the Inbox) "
                        "and 365.")
    if cfg.look_alike_min < 0 or cfg.look_alike_min == 1:
        problems.append("look_alike_min must be 0 (off) or at least 2; 5 is a good value.")
    if cfg.ai.max_per_run < 0:
        problems.append("ai: max_per_run can't be negative.")
    ids = {a.id for a in cfg.accounts}
    rule_names: set[str] = set()
    for r in cfg.rules:
        label = f"Rule '{r.name}'"
        if r.name in rule_names:
            problems.append(f"Two rules are both called '{r.name}'; give each a different name.")
        rule_names.add(r.name)
        issue = _rule_target_problem(r.action, cfg)
        if issue:
            problems.append(f"{label} {issue}.")
        if not (r.from_ or r.to or r.subject or r.list_id or r.header):
            problems.append(f"{label} has no conditions (from, to, subject, list_id or header), so it would match "
                            "every message.")
        for acct in r.accounts:
            if acct not in ids:
                problems.append(f"{label} is limited to account '{acct}', which doesn't exist.")
    return problems
