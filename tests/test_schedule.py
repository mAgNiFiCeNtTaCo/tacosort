from __future__ import annotations

import plistlib
import shlex
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from tacosort import schedule

PYW = r"C:\Users\Some One\AppData\Local\Programs\Python\Python312\pythonw.exe"
CONF = r"C:\Users\Some One\AppData\Roaming\TacoSort\config.yaml"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Every test: Python 3.11+ (so -P is used), a temp data folder, and a fixed Windows user."""
    monkeypatch.setattr(schedule, "python_options", lambda version_info=None: ["-P"])
    monkeypatch.setattr(schedule, "_data_dir", lambda config_path=None: tmp_path / "data")
    monkeypatch.setenv("USERNAME", "Some One")
    monkeypatch.setenv("USERDOMAIN", "PC")


def test_python_options_by_version(monkeypatch):
    monkeypatch.undo()
    assert schedule.python_options((3, 10, 12)) == []
    assert schedule.python_options((3, 11, 0)) == ["-P"]
    assert schedule.python_options((3, 13)) == ["-P"]


def test_windows_task_name_is_per_user():
    assert schedule.windows_task_name("Some One") == "TacoSort-Some_One"
    assert schedule.windows_task_name("PC\\jo.smith") == "TacoSort-jo.smith"
    assert schedule.windows_task_name("a/b:c*?") == "TacoSort-a_b_c"
    assert schedule.windows_task_name("") == "TacoSort"
    assert schedule.windows_task_name() == "TacoSort-Some_One"            # from %USERNAME%


def test_windows_command_quotes_paths_with_spaces():
    cmd = schedule.build_windows_command(PYW, CONF, 15, "TacoSort-Some_One")
    assert cmd[:7] == ["schtasks", "/Create", "/SC", "MINUTE", "/MO", "15", "/TN"]
    assert cmd[cmd.index("/TN") + 1] == "TacoSort-Some_One" and cmd[-1] == "/F"
    tr = cmd[cmd.index("/TR") + 1]
    assert tr == f'"{PYW}" -P -m tacosort run --quiet --config "{CONF}"'
    # What Windows actually receives keeps /TR as one argument.
    line = subprocess.list2cmdline(cmd)
    assert '/TR "\\"C:\\Users\\Some One' in line


def test_windows_command_without_isolation_flag(monkeypatch):
    monkeypatch.setattr(schedule, "python_options", lambda version_info=None: [])
    tr = schedule.build_windows_command(PYW, CONF, 15)
    assert tr[tr.index("/TR") + 1] == f'"{PYW}" -m tacosort run --quiet --config "{CONF}"'


def test_the_app_schedules_itself(monkeypatch):
    # The downloadable app has no Python to start: the job runs the app with the command.
    monkeypatch.undo()
    app = r"C:\Users\Some One\AppData\Local\Programs\TacoSort\TacoSort.exe"
    monkeypatch.setattr(schedule.sys, "frozen", True, raising=False)
    monkeypatch.setattr(schedule.sys, "executable", app)
    assert schedule.windowless_python() == app
    assert schedule.python_options() == []
    tr = schedule.build_windows_command(app, CONF, 15)
    assert tr[tr.index("/TR") + 1] == f'"{app}" run --quiet --config "{CONF}"'
    assert schedule._command(app, CONF) == [app, "run", "--quiet", "--config", CONF]


def test_windows_command_too_long_is_explained():
    with pytest.raises(schedule.ScheduleError, match="too long"):
        schedule.build_windows_command(PYW, "C:\\" + "x" * 300 + "\\config.yaml", 15)


def test_windows_task_xml():
    xml = schedule.build_windows_task_xml(PYW, CONF, 20, user="PC\\Some One & Co", start=datetime(2026, 1, 2, 3, 4),
                                          work_dir=r"C:\Users\Some One\AppData\Local\TacoSort")
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    assert root.find(".//t:Repetition/t:Interval", NS).text == "PT20M"
    assert root.find(".//t:TimeTrigger/t:StartBoundary", NS).text == "2026-01-02T03:04:00"
    assert root.find(".//t:LogonTrigger/t:UserId", NS).text == "PC\\Some One & Co"
    assert root.find(".//t:DisallowStartIfOnBatteries", NS).text == "false"
    assert root.find(".//t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert root.find(".//t:Principal/t:LogonType", NS).text == "InteractiveToken"
    assert root.find(".//t:Exec/t:Command", NS).text == PYW
    assert root.find(".//t:Exec/t:Arguments", NS).text == f'-P -m tacosort run --quiet --config "{CONF}"'
    assert root.find(".//t:Exec/t:WorkingDirectory", NS).text == r"C:\Users\Some One\AppData\Local\TacoSort"
    no_logon = schedule.build_windows_task_xml(PYW, CONF, 20, user="u", start=datetime(2026, 1, 1), at_logon=False)
    assert "LogonTrigger" not in no_logon and "WorkingDirectory" not in no_logon


def test_launchd_plist():
    data = schedule.build_launchd_plist("/Users/some one/.venv/bin/python", "/Users/some one/cfg.yaml", 15,
                                        "/Users/some one/Library/Application Support/TacoSort",
                                        "/Users/some one/Library/Application Support/TacoSort")
    plist = plistlib.loads(data)
    assert plist["Label"] == schedule.LAUNCHD_LABEL
    assert plist["ProgramArguments"] == ["/Users/some one/.venv/bin/python", "-P", "-m", "tacosort", "run",
                                         "--quiet", "--config", "/Users/some one/cfg.yaml"]
    assert plist["StartInterval"] == 900 and plist["RunAtLoad"] is True
    assert plist["StandardErrorPath"].endswith("launchd-error.log")
    assert plist["WorkingDirectory"] == "/Users/some one/Library/Application Support/TacoSort"
    assert "WorkingDirectory" not in plistlib.loads(schedule.build_launchd_plist("/p", "/c", 5))
    cmds = schedule.build_launchd_commands(501, "/Users/x/Library/LaunchAgents/com.tacosort.run.plist")
    assert cmds[0] == ["launchctl", "bootout", "gui/501/com.tacosort.run"]
    assert cmds[1] == ["launchctl", "bootstrap", "gui/501", "/Users/x/Library/LaunchAgents/com.tacosort.run.plist"]


def test_systemd_units_quote_and_escape():
    units = schedule.build_systemd_units("/home/some one/.venv/bin/python", "/home/me/100%/cfg.yaml", 10,
                                         "/home/some one/.local/share/mail%tidy")
    service, timer = units["tacosort.service"], units["tacosort.timer"]
    assert ('ExecStart="/home/some one/.venv/bin/python" -P -m tacosort run --quiet --config '
            "/home/me/100%%/cfg.yaml") in service
    assert "\nWorkingDirectory=/home/some one/.local/share/mail%%tidy\n" in service
    assert "Type=oneshot" in service
    assert "OnUnitActiveSec=10min" in timer and "WantedBy=timers.target" in timer
    assert "WorkingDirectory" not in schedule.build_systemd_units("/p", "/c", 10)["tacosort.service"]


def test_cron_line():
    line = schedule.build_cron_line("/home/some one/py", "/home/me/cfg%.yaml", 15, "/home/some one/.local/share/tacosort")
    assert line.startswith("*/15 * * * * ") and line.endswith("# tacosort")
    assert "'/home/some one/py'" in line and "cfg\\%.yaml" in line
    words = shlex.split(line[len("*/15 * * * * "):].replace("\\%", "%"))
    assert words[:4] == ["cd", "/home/some one/.local/share/tacosort", "&&", "/home/some one/py"]
    assert words[4] == "-P"
    assert schedule.build_cron_line("/p", "/c", 120).startswith("0 */2 * * * /p -P -m tacosort")


def test_cron_interval_text():
    assert schedule.cron_interval_text(15) == "every 15 minutes"
    assert "top of each hour" in schedule.cron_interval_text(45)
    assert schedule.cron_interval_text(60) == "every hour"
    assert "rounded down" in schedule.cron_interval_text(90) and "every hour" in schedule.cron_interval_text(90)
    text = schedule.cron_interval_text(1439)
    assert "every 23 hours" in text and "rounded down" in text and "midnight" in text


@pytest.mark.parametrize("bad", [0, -5, 1440])
def test_bad_interval(bad):
    with pytest.raises(ValueError):
        schedule.build_systemd_units("/p", "/c", bad)
    with pytest.raises(ValueError):
        schedule.install(bad, Path("c.yaml"))


class FakeRun:
    """Stands in for running commands. `answers(cmd) -> (returncode, stdout)`, or a queue of results."""

    def __init__(self, results=None, answers=None):
        self.cmds, self.inputs, self.results, self.answers = [], [], list(results or []), answers

    def __call__(self, cmd, input=None):
        self.cmds.append(cmd)
        self.inputs.append(input)
        if self.answers is not None:
            rc, out = self.answers(cmd)
        else:
            rc, out = self.results.pop(0) if self.results else (0, "")
        return subprocess.CompletedProcess(cmd, rc, out, "Access is denied." if rc else "")


def legacy_xml(owner: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-16"?><Task xmlns="{NS["t"]}"><Principals><Principal id="Author">'
            f"<UserId>{owner}</UserId></Principal></Principals></Task>")


def windows(legacy_owner: str | None = None, *, create_ok=(True,), mine_exists=False):
    """A fake Task Scheduler: an optional old "TacoSort" task owned by `legacy_owner`."""
    creates = list(create_ok)

    def answers(cmd):
        name = cmd[cmd.index("/TN") + 1]
        if cmd[1] == "/Create":
            return (0, "") if (creates.pop(0) if creates else True) else (1, "")
        if name == "TacoSort":
            if legacy_owner is None:
                return 1, ""
            return (0, legacy_xml(legacy_owner)) if "/XML" in cmd else (0, "")
        exists = mine_exists
        if cmd[1] == "/Query":
            return (0, "TaskName: \\X\nNext Run Time: 1/1/2027 10:15:00 AM\n") if exists else (1, "")
        return (0, "") if exists else (1, "")

    return FakeRun(answers=answers)


def test_install_windows_falls_back_without_logon_trigger(monkeypatch, tmp_path):
    fake = windows(create_ok=(False, True))
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    monkeypatch.setattr(schedule, "_run", fake)
    msg = schedule.install(30, tmp_path / "config.yaml")
    assert "every 30 minutes" in msg and "log in" not in msg and '"TacoSort-Some_One"' in msg
    creates = [c for c in fake.cmds if c[1] == "/Create"]
    assert len(creates) == 2 and all("/XML" in c and c[c.index("/TN") + 1] == "TacoSort-Some_One" for c in creates)
    assert not Path(creates[0][creates[0].index("/XML") + 1]).exists()      # temp file cleaned up
    assert (tmp_path / "data").is_dir()                                     # the job's start folder exists


def test_install_windows_last_resort_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    fake = windows(create_ok=(False, False, True))
    monkeypatch.setattr(schedule, "_run", fake)
    assert "battery" in schedule.install(15, tmp_path / "c.yaml")
    last = [c for c in fake.cmds if c[1] == "/Create"][-1]
    assert last[2:4] == ["/SC", "MINUTE"] and last[last.index("/TN") + 1] == "TacoSort-Some_One"
    monkeypatch.setattr(schedule, "_run", windows(create_ok=(False, False, False)))
    with pytest.raises(schedule.ScheduleError, match="refused"):
        schedule.install(15, tmp_path / "c.yaml")


def test_install_windows_replaces_this_users_old_task(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    fake = windows(legacy_owner="PC\\Some One")
    monkeypatch.setattr(schedule, "_run", fake)
    assert "old task" in schedule.install(15, tmp_path / "c.yaml")
    assert ["schtasks", "/Delete", "/TN", "TacoSort", "/F"] in fake.cmds


def test_install_windows_leaves_another_users_old_task(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    fake = windows(legacy_owner="PC\\Someone Else")
    monkeypatch.setattr(schedule, "_run", fake)
    assert "old task" not in schedule.install(15, tmp_path / "c.yaml")
    assert ["schtasks", "/Delete", "/TN", "TacoSort", "/F"] not in fake.cmds


def test_windows_status_and_uninstall(monkeypatch):
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    monkeypatch.setattr(schedule, "_run", windows(mine_exists=True))
    text = schedule.status()
    assert "on" in text and "10:15" in text and "TacoSort-Some_One" in text
    monkeypatch.setattr(schedule, "_run", windows())
    assert "off" in schedule.status()
    fake = windows(mine_exists=True)
    monkeypatch.setattr(schedule, "_run", fake)
    assert "off" in schedule.uninstall()
    assert ["schtasks", "/Delete", "/TN", "TacoSort-Some_One", "/F"] in fake.cmds
    monkeypatch.setattr(schedule, "_run", windows())
    assert "already off" in schedule.uninstall()


def test_windows_status_and_uninstall_find_the_old_task(monkeypatch):
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    monkeypatch.setattr(schedule, "_run", windows(legacy_owner="Some One"))
    text = schedule.status()
    assert "on" in text and '"TacoSort"' in text and "schedule on" in text
    fake = windows(legacy_owner="PC\\Some One")
    monkeypatch.setattr(schedule, "_run", fake)
    assert schedule.uninstall() == "The background schedule is off. TacoSort will only run when you start it."
    assert ["schtasks", "/Delete", "/TN", "TacoSort", "/F"] in fake.cmds
    # Another user's old task on a shared PC: not shown as ours, never deleted.
    monkeypatch.setattr(schedule, "_run", windows(legacy_owner="PC\\Someone Else"))
    assert "off" in schedule.status()
    fake = windows(legacy_owner="PC\\Someone Else")
    monkeypatch.setattr(schedule, "_run", fake)
    assert "already off" in schedule.uninstall()
    assert ["schtasks", "/Delete", "/TN", "TacoSort", "/F"] not in fake.cmds


def test_install_linux_cron_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "_has_systemd_user", lambda: False)
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/" + name)
    fake = FakeRun([(0, "MAILTO=me\n*/5 * * * * old >/dev/null 2>&1 # tacosort\n"), (0, "")])
    monkeypatch.setattr(schedule, "_run", fake)
    msg = schedule.install(5, tmp_path / "c.yaml")
    assert "crontab" in msg and "every 5 minutes" in msg
    assert "keychain" in msg and "No password is saved" in msg and "systemd" in msg     # C-N4 warning
    written = fake.inputs[-1].splitlines()
    assert written[0] == "MAILTO=me" and len(written) == 2 and written[1].endswith("# tacosort")
    assert "* old >" not in written[1]
    assert f"cd {shlex.quote(str(tmp_path / 'data'))} && " in written[1] and " -P -m tacosort " in written[1]


def test_install_linux_cron_explains_rounding(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "_has_systemd_user", lambda: False)
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(schedule, "_run", FakeRun([(1, ""), (0, "")]))
    msg = schedule.install(90, tmp_path / "c.yaml")
    assert "every hour" in msg and "rounded down" in msg


def test_install_linux_systemd(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "_has_systemd_user", lambda: True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)
    assert "systemd" in schedule.install(15, tmp_path / "c.yaml")
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    assert (unit_dir / "tacosort.timer").exists() and (unit_dir / "tacosort.service").exists()
    service = (unit_dir / "tacosort.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={tmp_path / 'data'}" in service and " -P -m tacosort " in service
    assert (tmp_path / "data").is_dir()
    assert ["systemctl", "--user", "enable", "--now", "tacosort.timer"] in fake.cmds


def test_install_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "macos")
    monkeypatch.setattr(schedule, "launchd_plist_path", lambda: tmp_path / "LaunchAgents" / "com.tacosort.run.plist")
    monkeypatch.setattr(schedule.os, "getuid", lambda: 501, raising=False)
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)
    assert "every 15 minutes" in schedule.install(15, tmp_path / "c.yaml")
    plist = plistlib.loads((tmp_path / "LaunchAgents" / "com.tacosort.run.plist").read_bytes())
    assert plist["StartInterval"] == 900
    assert plist["WorkingDirectory"] == str(tmp_path / "data") and plist["ProgramArguments"][1] == "-P"
    assert fake.cmds[1][:3] == ["launchctl", "bootstrap", "gui/501"]


def test_data_dir_follows_the_config(monkeypatch, tmp_path):
    """The job starts in the data folder the config names (or the default when it can't be read)."""
    monkeypatch.undo()
    monkeypatch.delenv("TACOSORT_DATA", raising=False)
    from tacosort.config import default_data_dir

    conf = tmp_path / "config.yaml"
    conf.write_text(f"data_dir: {(tmp_path / 'mine').as_posix()}\naccounts: []\n", encoding="utf-8")
    assert schedule._data_dir(conf) == tmp_path / "mine"
    assert schedule._data_dir(tmp_path / "missing.yaml") == default_data_dir()
    bad = tmp_path / "bad.yaml"
    bad.write_text("data_dir: [unclosed\n", encoding="utf-8")
    assert schedule._data_dir(bad) == default_data_dir()
