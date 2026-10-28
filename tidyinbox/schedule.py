"""Run TidyInbox in the background every few minutes, using the computer's own scheduler.

- Windows: a Task Scheduler task "TidyInbox-<your user name>" for the current user (no admin needed),
  every N minutes and at logon, running `pythonw.exe -m tidyinbox run --quiet` (pythonw = no console
  window). Older versions named the task plain "TidyInbox"; status and uninstall still find that one.
- macOS: a LaunchAgent (~/Library/LaunchAgents/com.tidyinbox.run.plist), every N minutes and at login.
- Linux: a systemd user timer (tidyinbox.timer), or a crontab line when systemd isn't available.

The scheduled job starts in TidyInbox's own data folder and, on Python 3.11 or newer, with `-P`, so a
stray file such as `yaml.py` lying in the home folder can never be loaded into TidyInbox.

The `build_*` functions only build commands and file contents (easy to test); `install`, `uninstall`
and `status` run them.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "TidyInbox"        # the old, machine-wide task name (still found by status and uninstall)
LAUNCHD_LABEL = "com.tidyinbox.run"
SYSTEMD_NAME = "tidyinbox"
CRON_MARKER = "# tidyinbox"
SCHTASKS_TR_LIMIT = 261       # schtasks refuses longer /TR commands


class ScheduleError(RuntimeError):
    """The background schedule could not be changed. The message is plain English."""


# ---- helpers --------------------------------------------------------------------------------------
def _check_interval(interval_minutes: int) -> int:
    n = int(interval_minutes)
    if not 1 <= n <= 1439:
        raise ValueError("The interval must be between 1 and 1439 minutes (just under a day).")
    return n


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _run(cmd: list[str], input: str | None = None) -> subprocess.CompletedProcess:
    """Run one command without a shell. Tests replace this."""
    return subprocess.run(cmd, input=input, capture_output=True, text=True, check=False)


def windowless_python(executable: str | None = None) -> str:
    """pythonw.exe next to the running Python (no console window pops up), else python itself."""
    exe = Path(executable or sys.executable)
    candidate = exe.with_name("pythonw.exe") if exe.suffix.lower() == ".exe" else exe.with_name("pythonw")
    return str(candidate) if candidate.exists() else str(exe)


def python_options(version_info: tuple[int, ...] | None = None) -> list[str]:
    """Options for the scheduled Python: `-P` (Python 3.11+) keeps the current folder out of the
    import path, so only the installed TidyInbox and its libraries are ever loaded."""
    version = tuple(version_info or sys.version_info)[:2]
    return ["-P"] if version >= (3, 11) else []


def run_arguments(config_path: Path | str) -> list[str]:
    """The TidyInbox part of the scheduled command line (after Python and its options)."""
    return ["-m", "tidyinbox", "run", "--quiet", "--config", str(config_path)]


def _command(python: str, config_path: Path | str) -> list[str]:
    return [python, *python_options(), *run_arguments(config_path)]


def _data_dir(config_path: Path | str | None = None) -> Path | None:
    """TidyInbox's data folder for this config (where the scheduled job starts), or None if unknown."""
    try:
        from .config import data_dir, default_data_dir, load_config
    except ImportError:
        return None
    if config_path is not None:
        # A broken config must not stop the schedule: then the default folder is used.
        with contextlib.suppress(Exception):
            return Path(data_dir(load_config(Path(config_path))))
    return Path(default_data_dir())


def _prepare_dir(path: Path | None) -> Path | None:
    """Make sure the folder exists (only its owner may open it), so the job can start there."""
    if path is None:
        return None
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


# ---- Windows --------------------------------------------------------------------------------------
def windows_task_name(user: str | None = None) -> str:
    """The Task Scheduler name for this Windows user, e.g. "TidyInbox-Some_One". Task names are
    machine-wide, so each user on a shared PC gets their own."""
    user = os.environ.get("USERNAME", "") if user is None else user
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", user.split("\\")[-1]).strip("._")[:64]
    return f"{TASK_NAME}-{clean}" if clean else TASK_NAME


def build_windows_command(pythonw: str, config_path: Path | str, interval_minutes: int,
                          task_name: str = TASK_NAME) -> list[str]:
    """Plain `schtasks /Create` for an every-N-minutes task (the fallback when the XML is refused)."""
    n = _check_interval(interval_minutes)
    opts = "".join(f"{o} " for o in python_options())
    tr = f'"{pythonw}" {opts}-m tidyinbox run --quiet --config "{config_path}"'
    if len(tr) > SCHTASKS_TR_LIMIT:
        raise ScheduleError("The path to Python or to the config file is too long for Windows Task "
                            "Scheduler. Move the config file to a shorter path and try again.")
    return ["schtasks", "/Create", "/SC", "MINUTE", "/MO", str(n), "/TN", task_name, "/TR", tr, "/F"]


def build_windows_task_xml(pythonw: str, config_path: Path | str, interval_minutes: int, *, user: str,
                           start: datetime, at_logon: bool = True,
                           work_dir: Path | str | None = None) -> str:
    """Task Scheduler XML: every N minutes (and at logon), only while the user is logged on, runs on
    battery too, never two copies at once, stopped after an hour if it hangs."""
    n = _check_interval(interval_minutes)
    args = " ".join(f'"{a}"' if (" " in a or not a) else a
                    for a in [*python_options(), *run_arguments(config_path)])
    logon = (f"""
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT2M</Delay>
    </LogonTrigger>""" if at_logon else "")
    workdir = (f"\n      <WorkingDirectory>{escape(str(work_dir))}</WorkingDirectory>" if work_dir else "")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>TidyInbox keeps your email folders organized.</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Enabled>true</Enabled>
      <StartBoundary>{start.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>
      <Repetition>
        <Interval>PT{n}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>{logon}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(pythonw)}</Command>
      <Arguments>{escape(args)}</Arguments>{workdir}
    </Exec>
  </Actions>
</Task>
"""


def build_windows_xml_command(xml_path: Path | str, task_name: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"]


def _windows_user() -> str:
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def _legacy_task_is_ours() -> bool | None:
    """The old plain "TidyInbox" task: None when there is none, True when it runs as this Windows user
    (or its owner can't be read), False when it belongs to someone else on this PC."""
    result = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    if result.returncode != 0:
        return None
    try:
        root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", (result.stdout or "").strip()))
        owner = next((el.text or "" for el in root.iter() if el.tag.endswith("UserId")), "")
    except ET.ParseError:
        owner = ""
    if not owner or owner.upper().startswith("S-1-"):
        return True
    me = os.environ.get("USERNAME", "")
    return not me or owner.split("\\")[-1].lower() == me.lower()


def _delete_legacy_task() -> bool:
    """Remove the old plain "TidyInbox" task when it is this user's. True when one was removed."""
    if _legacy_task_is_ours() is not True:
        return False
    return _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]).returncode == 0


def _install_windows(n: int, config_path: Path) -> str:
    pythonw = windowless_python()
    name = windows_task_name()
    work_dir = _prepare_dir(_data_dir(config_path))
    errors = []
    done = ""
    for at_logon in (True, False):
        xml = build_windows_task_xml(pythonw, config_path, n, user=_windows_user(), start=datetime.now(),
                                     at_logon=at_logon, work_dir=work_dir)
        fd, tmp = tempfile.mkstemp(suffix=".xml", prefix="tidyinbox-task-")
        os.close(fd)
        try:
            Path(tmp).write_text(xml, encoding="utf-16")
            result = _run(build_windows_xml_command(tmp, name))
        finally:
            Path(tmp).unlink(missing_ok=True)
        if result.returncode == 0:
            extra = " and when you log in" if at_logon else ""
            done = (f"TidyInbox will now run every {n} minutes{extra} while you are logged in to Windows "
                    f"(Task Scheduler task \"{name}\").")
            break
        errors.append((result.stderr or result.stdout).strip())
    if not done:
        result = _run(build_windows_command(pythonw, config_path, n, name))
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout).strip())
            raise ScheduleError("Windows Task Scheduler refused to create the TidyInbox task: " + errors[-1])
        done = (f"TidyInbox will now run every {n} minutes while you are logged in to Windows "
                f"(Task Scheduler task \"{name}\"). Note: Windows may skip runs while a laptop is "
                "on battery.")
    if name != TASK_NAME and _delete_legacy_task():
        done += f" The old task \"{TASK_NAME}\" from an earlier version was removed."
    return done


def _parse_list(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out.setdefault(k.strip().lower(), v.strip())
    return out


def _status_windows() -> str:
    name = windows_task_name()
    result = _run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
    if result.returncode != 0:
        if name != TASK_NAME and _legacy_task_is_ours():
            return (f"The background schedule is on (Task Scheduler task \"{TASK_NAME}\", made by an "
                    "earlier TidyInbox version). Run `tidyinbox schedule on` once to update it.")
        return "The background schedule is off (no TidyInbox task in Windows Task Scheduler)."
    info = _parse_list(result.stdout)
    parts = [f"The background schedule is on (Task Scheduler task \"{name}\")."]
    if info.get("next run time"):
        parts.append(f"Next run: {info['next run time']}.")
    if info.get("last run time"):
        parts.append(f"Last run: {info['last run time']} (result {info.get('last result', '?')}).")
    return " ".join(parts)


def _uninstall_windows() -> str:
    name = windows_task_name()
    removed = _run(["schtasks", "/Delete", "/TN", name, "/F"]).returncode == 0
    if name != TASK_NAME:
        removed = _delete_legacy_task() or removed
    if not removed:
        return "The background schedule was already off."
    return "The background schedule is off. TidyInbox will only run when you start it."


# ---- macOS ----------------------------------------------------------------------------------------
def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def build_launchd_plist(python: str, config_path: Path | str, interval_minutes: int,
                        log_dir: Path | str | None = None, work_dir: Path | str | None = None) -> bytes:
    n = _check_interval(interval_minutes)
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": _command(python, config_path),
        "StartInterval": n * 60,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }
    if work_dir:
        plist["WorkingDirectory"] = str(work_dir)
    if log_dir:
        plist["StandardErrorPath"] = str(Path(log_dir) / "launchd-error.log")
        plist["StandardOutPath"] = "/dev/null"
    return plistlib.dumps(plist)


def build_launchd_commands(uid: int, plist_path: Path | str) -> list[list[str]]:
    """Unload any old copy (may fail harmlessly), then load the agent."""
    return [
        ["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
    ]


def _install_macos(n: int, config_path: Path) -> str:
    path = launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = _prepare_dir(_data_dir(config_path))
    path.write_bytes(build_launchd_plist(sys.executable, config_path, n, work_dir, work_dir))
    unload, load = build_launchd_commands(os.getuid(), path)
    _run(unload)
    result = _run(load)
    if result.returncode != 0:
        raise ScheduleError("macOS refused to start the TidyInbox background agent: "
                            + (result.stderr or result.stdout).strip())
    return f"TidyInbox will now run every {n} minutes and when you log in to your Mac."


def _status_macos() -> str:
    if not launchd_plist_path().exists():
        return "The background schedule is off."
    result = _run(["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])
    if result.returncode != 0:
        return "The background schedule is set up but not loaded; run `tidyinbox schedule on` again."
    return "The background schedule is on (macOS LaunchAgent)."


def _uninstall_macos() -> str:
    path = launchd_plist_path()
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])
    if not path.exists():
        return "The background schedule was already off."
    path.unlink()
    return "The background schedule is off. TidyInbox will only run when you start it."


# ---- Linux ----------------------------------------------------------------------------------------
def systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def _systemd_quote(arg: str) -> str:
    arg = arg.replace("%", "%%").replace("$", "$$")
    if any(c in arg for c in ' \t"\'\\;'):
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return arg


def build_systemd_units(python: str, config_path: Path | str, interval_minutes: int,
                        work_dir: Path | str | None = None) -> dict[str, str]:
    """{file name: contents} for a oneshot service and a timer that starts it every N minutes."""
    n = _check_interval(interval_minutes)
    exec_start = " ".join(_systemd_quote(a) for a in _command(python, config_path))
    # WorkingDirectory takes the path as it is (no quotes); only % needs escaping.
    workdir = f"WorkingDirectory={str(work_dir).replace('%', '%%')}\n" if work_dir else ""
    service = f"""[Unit]
Description=TidyInbox: keep email folders organized
After=network-online.target

[Service]
Type=oneshot
{workdir}ExecStart={exec_start}
Nice=10
"""
    timer = f"""[Unit]
Description=Run TidyInbox every {n} minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec={n}min
Unit={SYSTEMD_NAME}.service

[Install]
WantedBy=timers.target
"""
    return {f"{SYSTEMD_NAME}.service": service, f"{SYSTEMD_NAME}.timer": timer}


def build_cron_line(python: str, config_path: Path | str, interval_minutes: int,
                    work_dir: Path | str | None = None) -> str:
    n = _check_interval(interval_minutes)
    when = f"*/{n} * * * *" if n < 60 else f"0 */{max(1, n // 60)} * * *"
    cmd = " ".join(shlex.quote(a) for a in _command(python, config_path))
    if work_dir:
        cmd = f"cd {shlex.quote(str(work_dir))} && {cmd}"
    cmd = cmd.replace("%", "\\%")          # cron turns a bare % into a line break
    return f"{when} {cmd} >/dev/null 2>&1 {CRON_MARKER}"


def cron_interval_text(interval_minutes: int) -> str:
    """How often cron really runs the job, in plain English (cron can't do every interval exactly)."""
    n = _check_interval(interval_minutes)
    if n < 60:
        if 60 % n == 0:
            return f"every {n} minutes"
        return f"every {n} minutes, starting again at the top of each hour (cron can't do {n} exactly)"
    hours = max(1, n // 60)
    text = "every hour" if hours == 1 else f"every {hours} hours"
    if n % 60:
        text += f" (cron only repeats in whole hours, so {n} minutes was rounded down)"
    if 24 % hours:
        text += ", starting again at midnight"
    return text


def _has_systemd_user() -> bool:
    return bool(shutil.which("systemctl")) and _run(["systemctl", "--user", "show-environment"]).returncode == 0


def _crontab_without_us() -> list[str]:
    result = _run(["crontab", "-l"])
    lines = result.stdout.splitlines() if result.returncode == 0 else []
    return [line for line in lines if not line.rstrip().endswith(CRON_MARKER)]


def _install_linux(n: int, config_path: Path) -> str:
    work_dir = _prepare_dir(_data_dir(config_path))
    if _has_systemd_user():
        unit_dir = systemd_user_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        for name, text in build_systemd_units(sys.executable, config_path, n, work_dir).items():
            (unit_dir / name).write_text(text, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        result = _run(["systemctl", "--user", "enable", "--now", f"{SYSTEMD_NAME}.timer"])
        if result.returncode != 0:
            raise ScheduleError("systemd refused to start the TidyInbox timer: "
                                + (result.stderr or result.stdout).strip())
        return (f"TidyInbox will now run every {n} minutes (systemd user timer {SYSTEMD_NAME}.timer). "
                "It runs while you are logged in; to keep it running when you are logged out, run "
                "`loginctl enable-linger` once.")
    if not shutil.which("crontab"):
        raise ScheduleError("This computer has neither a systemd user session nor cron, so TidyInbox "
                            "can't schedule itself. Use `tidyinbox watch` instead.")
    lines = _crontab_without_us() + [build_cron_line(sys.executable, config_path, n, work_dir)]
    result = _run(["crontab", "-"], input="\n".join(lines) + "\n")
    if result.returncode != 0:
        raise ScheduleError("cron refused the TidyInbox entry: " + (result.stderr or result.stdout).strip())
    return (f"TidyInbox will now run {cron_interval_text(n)} (a line in your crontab, because this computer "
            "has no systemd user session; TidyInbox prefers a systemd user timer when there is one).\n"
            "Please check that it works: cron jobs run without your desktop session, so they often can't "
            "open the keychain where your password is saved, and then every run fails with \"No password "
            "is saved\". Run `tidyinbox status` after a while. If the runs fail, use `tidyinbox watch` in a "
            "terminal instead, or turn on a systemd user session and run `tidyinbox schedule on` again.")


def _status_linux() -> str:
    if (systemd_user_dir() / f"{SYSTEMD_NAME}.timer").exists() and shutil.which("systemctl"):
        result = _run(["systemctl", "--user", "is-active", f"{SYSTEMD_NAME}.timer"])
        if result.stdout.strip() == "active":
            return f"The background schedule is on (systemd user timer {SYSTEMD_NAME}.timer)."
        return "The background schedule is set up but not active; run `tidyinbox schedule on` again."
    if shutil.which("crontab"):
        result = _run(["crontab", "-l"])
        if result.returncode == 0 and any(line.rstrip().endswith(CRON_MARKER) for line in result.stdout.splitlines()):
            return "The background schedule is on (a line in your crontab)."
    return "The background schedule is off."


def _uninstall_linux() -> str:
    did = False
    unit_dir = systemd_user_dir()
    if (unit_dir / f"{SYSTEMD_NAME}.timer").exists():
        _run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_NAME}.timer"])
        for name in (f"{SYSTEMD_NAME}.timer", f"{SYSTEMD_NAME}.service"):
            (unit_dir / name).unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
        did = True
    if shutil.which("crontab"):
        result = _run(["crontab", "-l"])
        if result.returncode == 0 and any(line.rstrip().endswith(CRON_MARKER) for line in result.stdout.splitlines()):
            _run(["crontab", "-"], input="\n".join(_crontab_without_us()) + "\n")
            did = True
    if not did:
        return "The background schedule was already off."
    return "The background schedule is off. TidyInbox will only run when you start it."


# ---- public ---------------------------------------------------------------------------------------
def install(interval_minutes: int, config_path: Path) -> str:
    """Turn the background schedule on (or update it). Returns a plain-English summary; raises
    ScheduleError (plain English) when the system refuses, ValueError for a bad interval."""
    n = _check_interval(interval_minutes)
    config_path = Path(config_path).expanduser().resolve()
    return {"windows": _install_windows, "macos": _install_macos, "linux": _install_linux}[_platform()](
        n, config_path)


def uninstall() -> str:
    """Turn the background schedule off. Returns a plain-English summary."""
    return {"windows": _uninstall_windows, "macos": _uninstall_macos, "linux": _uninstall_linux}[_platform()]()


def status() -> str:
    """One plain-English line about the background schedule."""
    return {"windows": _status_windows, "macos": _status_macos, "linux": _status_linux}[_platform()]()
