"""The phone page (phone.py): who may use it, what a request must carry, jobs, settings, on and off."""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from tacosort import actions, phone
from tacosort.config import load_config, save_config
from tacosort.models import AccountConfig, AppConfig

ME = "me@gmail.com"


def cfg(**kw) -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com", provider="gmail",
                                             host="imap.gmail.com")], phone_logins=[ME], **kw)


@pytest.fixture
def server(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    save_config(cfg(), path)
    monkeypatch.setattr(actions, "schedule_is_on", lambda: (True, "The background schedule is on."))
    httpd = phone.make_server(path, 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield SimpleNamespace(port=httpd.server_address[1], path=path, phone=httpd.phone)
    httpd.shutdown()
    httpd.server_close()


def ask(srv, method="GET", url="/api/status", host=None, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{srv.port}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
        h.setdefault("X-TacoSort", "1")
    conn.request(method, url, body=data, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = raw
    return resp.status, payload, resp


# ---- who may use it ----------------------------------------------------------------------------------
def test_this_computer_gets_the_page(server):
    status, page, resp = ask(server, url="/")
    assert status == 200 and b"TacoSort" in page
    assert "frame-ancestors 'none'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("Cache-Control") == "no-store"
    status, data, _ = ask(server)
    assert status == 200 and data["accounts"][0]["email"] == "me@example.com" and data["schedule"]["on"]


@pytest.mark.parametrize("host,headers,expected", [
    ("evil.example:80", {}, 403),                                       # a page that renamed itself 127.0.0.1
    ("pc.tail1.ts.net:8747", {}, 403),                                  # through Tailscale without a login
    ("pc.tail1.ts.net:8747", {"Tailscale-User-Login": "friend@gmail.com"}, 403),
    ("pc.tail1.ts.net:8747", {"Tailscale-User-Login": "Me@Gmail.com"}, 200),
    ("127.0.0.1:8747", {"X-Forwarded-For": "100.1.2.3"}, 403),         # proxied, but no login
])
def test_only_allowed_tailscale_logins(server, host, headers, expected):
    status, data, _ = ask(server, host=host, headers=headers)
    assert status == expected
    if expected == 403:
        assert "Not allowed" in data["error"]


def test_changes_need_the_page_own_header_and_origin(server):
    assert ask(server, "POST", "/api/schedule", body={"on": False}, headers={"X-TacoSort": "0"})[0] == 403
    assert ask(server, "POST", "/api/schedule", body={"on": False},
               headers={"Origin": "https://evil.example"})[0] == 403
    assert ask(server, "POST", "/api/schedule", body={"on": False}, headers={"Content-Type": "text/plain"})[0] == 403
    assert ask(server, "OPTIONS", "/api/schedule")[0] == 403
    status, data, _ = ask(server, "POST", "/api/job", body=[1, 2])
    assert status == 400 and "JSON object" in data["error"]


# ---- what it does -------------------------------------------------------------------------------------
def test_schedule_switch(server, monkeypatch):
    calls = []
    sched = SimpleNamespace(install=lambda m, p: calls.append(("on", m)) or "On every 15 minutes.",
                            uninstall=lambda: calls.append(("off",)) or "Off.")
    monkeypatch.setattr(phone.cli, "_schedule", lambda: sched)
    origin = {"Origin": f"http://127.0.0.1:{server.port}"}
    assert ask(server, "POST", "/api/schedule", body={"on": True}, headers=origin)[1]["note"] == "On every 15 minutes."
    assert ask(server, "POST", "/api/schedule", body={"on": False})[1]["note"] == "Off."
    assert calls == [("on", 15), ("off",)]


def test_options_round_trip(server):
    status, data, _ = ask(server, url="/api/options")
    assert status == 200
    fields = {o["field"]: o for g in data["groups"] for o in g["options"]}
    assert fields["hold_hours"]["ticked"] is False and fields["hold_hours"]["number"] == 24
    status, data, _ = ask(server, "POST", "/api/options", body={
        "options": {"hold_hours": {"ticked": True, "number": "12"}}, "folders": {"social": False}, "interval": 15})
    assert status == 200 and data["ok"], data
    saved = load_config(server.path)
    assert saved.hold_hours == 12 and saved.categories["social"] == "" and saved.phone_logins == [ME]
    status, data, _ = ask(server, "POST", "/api/options", body={"options": {"hold_hours": {"ticked": True,
                                                                                            "number": "abc"}}})
    assert status == 200 and not data["ok"] and data["problems"]


def test_a_preview_job_runs_and_reports(server, monkeypatch):
    release = threading.Event()
    move = SimpleNamespace(at=None, account="a", sender="shop@x.com", subject="Sale", from_folder="INBOX",
                           to_folder="Promotions", reason="a newsletter")

    def fake_preview(c, log):
        log("Looking at me@example.com (nothing will be moved)...")
        release.wait(5)
        return [move], 3, 2, ["Run 'tacosort setup' to enter it."]

    monkeypatch.setattr(actions, "preview", fake_preview)
    status, data, _ = ask(server, "POST", "/api/job", body={"kind": "preview"})
    assert status == 200 and data["job"]["running"]
    status, data, _ = ask(server, "POST", "/api/job", body={"kind": "sort"})
    assert status == 400 and "still busy" in data["error"]
    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = ask(server, url="/api/job")[1]["job"]
        if not job["running"]:
            break
        time.sleep(0.05)
    assert job["result"]["moves"][0]["to"] == "Promotions" and job["result"]["moves"][0]["account"] == "me@example.com"
    assert job["result"]["problems"][0].startswith("Open TacoSort on your computer")
    assert job["lines"] == ["Looking at me@example.com (nothing will be moved)..."]


def test_unknown_job_and_page(server):
    assert ask(server, "POST", "/api/job", body={"kind": "delete-everything"})[0] == 400
    assert ask(server, url="/nope")[0] == 404


def test_moves_list_without_a_memory_file(server):
    status, data, _ = ask(server, url="/api/moves?limit=abc")
    assert status == 200 and data == {"moves": []}


# ---- on and off ---------------------------------------------------------------------------------------
def test_serve_command(monkeypatch, tmp_path):
    from tacosort import schedule

    monkeypatch.setattr(schedule, "frozen", lambda: False)
    cmd = phone.serve_command(tmp_path / "c.yaml")
    assert cmd[cmd.index("-m") + 1] == "tacosort" and cmd[-4:-1] == ["phone", "serve", "--config"]
    monkeypatch.setattr(schedule, "frozen", lambda: True)
    monkeypatch.setattr(schedule, "windowless_python", lambda: "TacoSort.exe")
    assert phone.serve_command(tmp_path / "c.yaml")[:3] == ["TacoSort.exe", "phone", "serve"]


def test_tailscale_me_reads_the_status(monkeypatch):
    status = {"Self": {"DNSName": "pc.tail1.ts.net.", "UserID": 7}, "User": {"7": {"LoginName": "Me@Gmail.com"}}}
    monkeypatch.setattr(phone, "_tailscale", lambda *a, **k: SimpleNamespace(stdout=json.dumps(status)))
    assert phone.tailscale_me() == ("pc.tail1.ts.net", ME)
    monkeypatch.setattr(phone, "_tailscale", lambda *a, **k: SimpleNamespace(stdout="{}"))
    with pytest.raises(phone.PhoneError, match="not signed in"):
        phone.tailscale_me()


def test_turn_on_and_off(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    save_config(AppConfig(accounts=cfg().accounts), path)
    did = []
    monkeypatch.setattr(phone, "tailscale_me", lambda: ("pc.tail1.ts.net", ME))
    monkeypatch.setattr(phone, "tailscale_exe", lambda: "tailscale")
    monkeypatch.setattr(phone, "tailscale_serve", lambda port, on: did.append(("serve", port, on)))
    monkeypatch.setattr(phone, "set_autostart", lambda p, on: did.append(("autostart", on)))
    monkeypatch.setattr(phone, "ping", lambda port, timeout=1.5: None)
    monkeypatch.setattr(phone, "start_background", lambda p, port: did.append(("start", port)) or True)
    text = phone.turn_on(path)
    assert "https://pc.tail1.ts.net:8747/" in text and load_config(path).phone_logins == [ME]
    assert did == [("serve", 8747, True), ("autostart", True), ("start", 8747)]
    did.clear()
    assert "off" in phone.turn_off(path)
    assert did == [("autostart", False), ("serve", 8747, False)]


def test_stop_kills_only_the_pid_the_page_reported(monkeypatch):
    answers = [{"app": "tacosort", "pid": 4242}, None]
    monkeypatch.setattr(phone, "ping", lambda port, timeout=1.5: answers.pop(0) if answers else None)
    killed = []
    monkeypatch.setattr(phone.os, "kill", lambda pid, sig: killed.append(pid))
    assert phone.stop_running(8747) and killed == [4242]


def test_ping_ignores_other_programs(tmp_path):
    assert phone.ping(1, timeout=0.2) is None              # nothing listens on port 1


@pytest.mark.skipif(os.name != "nt", reason="the Run key is Windows only")
def test_autostart_registry(monkeypatch, tmp_path):
    import winreg

    key = rf"Software\TacoSort-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(phone, "RUN_KEY", key)
    try:
        assert not phone.autostart_is_on()
        phone.set_autostart(tmp_path / "c.yaml", True)
        assert phone.autostart_is_on()
        phone.set_autostart(None, False)
        assert not phone.autostart_is_on()
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


def test_command_hints_point_at_the_computer():
    assert phone.for_phone("No password is saved. Run 'tacosort setup' to enter it.").startswith(
        "No password is saved. Open TacoSort on your computer")


def test_settings_check_the_phone_values():
    from tacosort.config import config_problems

    assert config_problems(cfg()) == []
    assert any("phone_port" in p for p in config_problems(cfg(phone_port=80)))
    bad = cfg()
    bad.phone_logins = ["not-a-login"]
    assert any("phone_logins" in p for p in config_problems(bad))
