from __future__ import annotations

import plistlib
import shlex
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from mailtidy import schedule

PYW = r"C:\Users\Some One\AppData\Local\Programs\Python\Python312\pythonw.exe"
CONF = r"C:\Users\Some One\AppData\Roaming\MailTidy\config.yaml"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_windows_command_quotes_paths_with_spaces():
    cmd = schedule.build_windows_command(PYW, CONF, 15)
    assert cmd[:7] == ["schtasks", "/Create", "/SC", "MINUTE", "/MO", "15", "/TN"]
    assert cmd[cmd.index("/TN") + 1] == "MailTidy" and cmd[-1] == "/F"
    tr = cmd[cmd.index("/TR") + 1]
    assert tr == f'"{PYW}" -m mailtidy run --quiet --config "{CONF}"'
    # What Windows actually receives keeps /TR as one argument.
    line = subprocess.list2cmdline(cmd)
    assert '/TR "\\"C:\\Users\\Some One' in line


def test_windows_command_too_long_is_explained():
    with pytest.raises(schedule.ScheduleError, match="too long"):
        schedule.build_windows_command(PYW, "C:\\" + "x" * 300 + "\\config.yaml", 15)


def test_windows_task_xml():
    xml = schedule.build_windows_task_xml(PYW, CONF, 20, user="PC\\Some One & Co", start=datetime(2026, 1, 2, 3, 4))
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    assert root.find(".//t:Repetition/t:Interval", NS).text == "PT20M"
    assert root.find(".//t:TimeTrigger/t:StartBoundary", NS).text == "2026-01-02T03:04:00"
    assert root.find(".//t:LogonTrigger/t:UserId", NS).text == "PC\\Some One & Co"
    assert root.find(".//t:DisallowStartIfOnBatteries", NS).text == "false"
    assert root.find(".//t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert root.find(".//t:Principal/t:LogonType", NS).text == "InteractiveToken"
    assert root.find(".//t:Exec/t:Command", NS).text == PYW
    assert root.find(".//t:Exec/t:Arguments", NS).text == f'-m mailtidy run --quiet --config "{CONF}"'
    no_logon = schedule.build_windows_task_xml(PYW, CONF, 20, user="u", start=datetime(2026, 1, 1), at_logon=False)
    assert "LogonTrigger" not in no_logon


def test_launchd_plist():
    data = schedule.build_launchd_plist("/Users/some one/.venv/bin/python", "/Users/some one/cfg.yaml", 15,
                                        "/Users/some one/Library/Application Support/MailTidy")
    plist = plistlib.loads(data)
    assert plist["Label"] == schedule.LAUNCHD_LABEL
    assert plist["ProgramArguments"] == ["/Users/some one/.venv/bin/python", "-m", "mailtidy", "run", "--quiet",
                                         "--config", "/Users/some one/cfg.yaml"]
    assert plist["StartInterval"] == 900 and plist["RunAtLoad"] is True
    assert plist["StandardErrorPath"].endswith("launchd-error.log")
    cmds = schedule.build_launchd_commands(501, "/Users/x/Library/LaunchAgents/com.mailtidy.run.plist")
    assert cmds[0] == ["launchctl", "bootout", "gui/501/com.mailtidy.run"]
    assert cmds[1] == ["launchctl", "bootstrap", "gui/501", "/Users/x/Library/LaunchAgents/com.mailtidy.run.plist"]


def test_systemd_units_quote_and_escape():
    units = schedule.build_systemd_units("/home/some one/.venv/bin/python", "/home/me/100%/cfg.yaml", 10)
    service, timer = units["mailtidy.service"], units["mailtidy.timer"]
    assert ('ExecStart="/home/some one/.venv/bin/python" -m mailtidy run --quiet --config '
            "/home/me/100%%/cfg.yaml") in service
    assert "Type=oneshot" in service
    assert "OnUnitActiveSec=10min" in timer and "WantedBy=timers.target" in timer


def test_cron_line():
    line = schedule.build_cron_line("/home/some one/py", "/home/me/cfg%.yaml", 15)
    assert line.startswith("*/15 * * * * ") and line.endswith("# mailtidy")
    assert "'/home/some one/py'" in line and "cfg\\%.yaml" in line
    assert shlex.split(line[len("*/15 * * * * "):].replace("\\%", "%"))[0] == "/home/some one/py"
    assert schedule.build_cron_line("/p", "/c", 120).startswith("0 */2 * * * ")


@pytest.mark.parametrize("bad", [0, -5, 1440])
def test_bad_interval(bad):
    with pytest.raises(ValueError):
        schedule.build_systemd_units("/p", "/c", bad)
    with pytest.raises(ValueError):
        schedule.install(bad, Path("c.yaml"))


class FakeRun:
    def __init__(self, results=None):
        self.cmds, self.inputs, self.results = [], [], list(results or [])

    def __call__(self, cmd, input=None):
        self.cmds.append(cmd)
        self.inputs.append(input)
        rc, out = self.results.pop(0) if self.results else (0, "")
        return subprocess.CompletedProcess(cmd, rc, out, "Access is denied." if rc else "")


def test_install_windows_falls_back_without_logon_trigger(monkeypatch, tmp_path):
    fake = FakeRun([(1, ""), (0, "")])
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    monkeypatch.setattr(schedule, "_run", fake)
    msg = schedule.install(30, tmp_path / "config.yaml")
    assert "every 30 minutes" in msg and "log in" not in msg
    assert all(c[:2] == ["schtasks", "/Create"] and "/XML" in c for c in fake.cmds)
    assert not Path(fake.cmds[0][fake.cmds[0].index("/XML") + 1]).exists()      # temp file cleaned up


def test_install_windows_last_resort_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    fake = FakeRun([(1, ""), (1, ""), (0, "")])
    monkeypatch.setattr(schedule, "_run", fake)
    assert "battery" in schedule.install(15, tmp_path / "c.yaml")
    assert fake.cmds[-1][2:4] == ["/SC", "MINUTE"]
    monkeypatch.setattr(schedule, "_run", FakeRun([(1, ""), (1, ""), (1, "")]))
    with pytest.raises(schedule.ScheduleError, match="refused"):
        schedule.install(15, tmp_path / "c.yaml")


def test_windows_status_and_uninstall(monkeypatch):
    monkeypatch.setattr(schedule, "_platform", lambda: "windows")
    listing = ("TaskName: \\MailTidy\nNext Run Time: 1/1/2027 10:15:00 AM\n"
               "Last Run Time: 1/1/2027 10:00:00 AM\nLast Result: 0\n")
    monkeypatch.setattr(schedule, "_run", FakeRun([(0, listing)]))
    text = schedule.status()
    assert "on" in text and "10:15" in text
    monkeypatch.setattr(schedule, "_run", FakeRun([(1, "")]))
    assert "off" in schedule.status()
    fake = FakeRun([(0, "")])
    monkeypatch.setattr(schedule, "_run", fake)
    assert "off" in schedule.uninstall()
    assert fake.cmds[0] == ["schtasks", "/Delete", "/TN", "MailTidy", "/F"]


def test_install_linux_cron_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "_has_systemd_user", lambda: False)
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/" + name)
    fake = FakeRun([(0, "MAILTO=me\n*/5 * * * * old >/dev/null 2>&1 # mailtidy\n"), (0, "")])
    monkeypatch.setattr(schedule, "_run", fake)
    assert "crontab" in schedule.install(5, tmp_path / "c.yaml")
    written = fake.inputs[-1].splitlines()
    assert written[0] == "MAILTO=me" and len(written) == 2 and written[1].endswith("# mailtidy")
    assert "old" not in written[1]


def test_install_linux_systemd(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "_has_systemd_user", lambda: True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)
    assert "systemd" in schedule.install(15, tmp_path / "c.yaml")
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    assert (unit_dir / "mailtidy.timer").exists() and (unit_dir / "mailtidy.service").exists()
    assert ["systemctl", "--user", "enable", "--now", "mailtidy.timer"] in fake.cmds


def test_install_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule, "_platform", lambda: "macos")
    monkeypatch.setattr(schedule, "launchd_plist_path", lambda: tmp_path / "LaunchAgents" / "com.mailtidy.run.plist")
    monkeypatch.setattr(schedule, "_log_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(schedule.os, "getuid", lambda: 501, raising=False)
    fake = FakeRun()
    monkeypatch.setattr(schedule, "_run", fake)
    assert "every 15 minutes" in schedule.install(15, tmp_path / "c.yaml")
    assert plistlib.loads((tmp_path / "LaunchAgents" / "com.mailtidy.run.plist").read_bytes())["StartInterval"] == 900
    assert fake.cmds[1][:3] == ["launchctl", "bootstrap", "gui/501"]
