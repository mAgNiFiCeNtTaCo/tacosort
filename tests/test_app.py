"""The app: tick-box options (options.py), the launcher, and the window itself (skipped without a screen)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tacosort import launch, options
from tacosort.config import load_config, save_config
from tacosort.models import AccountConfig, AppConfig, Rule


def cfg(**kw) -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com", provider="gmail",
                                             host="imap.gmail.com")], **kw)


# ---- options -----------------------------------------------------------------------------------------
def test_every_option_is_a_real_setting():
    names = {f.name for f in dataclasses.fields(AppConfig)}
    assert all(o.field in names for o in options.ALL_OPTIONS)
    assert len({o.field for o in options.ALL_OPTIONS}) == len(options.ALL_OPTIONS)


def test_read_shows_off_numbers_with_a_sensible_default():
    values = options.read(cfg(hold_hours=0, old_mail_days=180))
    assert values.options["hold_hours"] == (False, 24)          # unticked, 24 ready when ticked
    assert values.options["old_mail_days"] == (True, 180)
    assert values.options["keep_flagged"] == (True, 0)
    assert values.categories["receipts"] is True
    assert values.interval_minutes == 15


def test_apply_keeps_what_the_window_does_not_show():
    start = cfg(rules=[Rule(name="school", action="folder:School", from_=["*@school.org"])], vip=["mom@x.org"],
                categories={**AppConfig().categories, "promotions": "Deals"})
    values = options.read(start)
    values.options["hold_hours"] = (True, "48")
    values.options["unsure_to_folder"] = (True, 0)
    values.options["look_alike_min"] = (False, 5)
    values.categories["social"] = False
    new, problems = options.apply(start, values)
    assert problems == []
    assert new.hold_hours == 48 and new.unsure_to_folder and new.look_alike_min == 0
    assert new.categories["social"] == "" and new.categories["promotions"] == "Deals"   # renamed folder kept
    assert new.rules == start.rules and new.vip == ["mom@x.org"] and new.accounts == start.accounts
    assert start.hold_hours == 0                                 # the original is not changed


def test_ticking_a_folder_back_on_uses_its_usual_name():
    start = cfg(categories={**AppConfig().categories, "social": ""})
    values = options.read(start)
    assert values.categories["social"] is False
    values.categories["social"] = True
    new, _ = options.apply(start, values)
    assert new.categories["social"] == "Social"


@pytest.mark.parametrize("field,number", [("hold_hours", "abc"), ("hold_hours", "0"), ("old_mail_days", "9999"),
                                          ("look_alike_min", "1")])
def test_bad_numbers_are_explained(field, number):
    values = options.read(cfg())
    values.options[field] = (True, number)
    _, problems = options.apply(cfg(), values)
    assert problems and all(isinstance(p, str) for p in problems)


def test_bad_interval_is_explained():
    values = options.read(cfg())
    values.interval_minutes = "2"
    _, problems = options.apply(cfg(), values)
    assert any("minutes" in p for p in problems)


def test_saved_options_survive_the_settings_file(tmp_path):
    values = options.read(cfg())
    values.options["old_mail_days"] = (True, "90")
    new, problems = options.apply(cfg(), values)
    assert problems == []
    save_config(new, tmp_path / "config.yaml")
    again = load_config(tmp_path / "config.yaml")
    assert again.old_mail_days == 90 and options.read(again).options == options.read(new).options


# ---- launcher ----------------------------------------------------------------------------------------
def test_launcher_opens_the_window_without_a_command(monkeypatch):
    seen = []
    monkeypatch.setattr("tacosort.app.main", lambda args: seen.append(("app", args)) or 0)
    monkeypatch.setattr("tacosort.cli.main", lambda args: seen.append(("cli", args)) or 0)
    launch.main([])
    launch.main(["--config", "x.yaml"])
    launch.main(["run", "--quiet", "--config", "x.yaml"])
    launch.main(["--config", "x.yaml", "status"])
    assert seen == [("app", []), ("app", ["--config", "x.yaml"]), ("cli", ["run", "--quiet", "--config", "x.yaml"]),
                    ("cli", ["--config", "x.yaml", "status"])]


def test_launcher_survives_having_no_console(monkeypatch):
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    launch._no_console_safe()
    import sys

    print("goes nowhere")
    sys.stderr.write("nowhere either\n")


def test_smoke_test_finds_every_part(tmp_path):
    out = tmp_path / "smoke.txt"
    assert launch.smoke_test(str(out)) == 0
    text = out.read_text(encoding="utf-8")
    assert "import tacosort.app" in text and text.rstrip().endswith("ok")


# ---- the window ---------------------------------------------------------------------------------------
@pytest.fixture
def window(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tacosort import app

    monkeypatch.setattr(app, "schedule_is_on", lambda: (False, "The background schedule is off."))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no screen")
    root.withdraw()
    yield app, root
    root.destroy()


def test_window_shows_the_settings_and_saves_them(window, tmp_path):
    app, root = window
    path = tmp_path / "config.yaml"
    save_config(cfg(rules=[Rule(name="school", action="folder:School", from_=["*@school.org"])]), path)
    w = app.App(root, path)
    assert w.ok and w.acct_list.get_children() == ("a",)
    ticked, number = w.opt_vars["hold_hours"]
    ticked.set(True)
    number.set("12")
    w.cat_vars["social"].set(False)
    w.save_options()
    again = load_config(path)
    assert again.hold_hours == 12 and again.categories["social"] == "" and again.rules[0].name == "school"
    assert w.opt_note.get().startswith("Saved")


def test_ticking_background_sorting_sets_up_the_schedule(window, tmp_path, monkeypatch):
    import contextlib
    import queue
    import time

    app, root = window
    path = tmp_path / "config.yaml"
    save_config(cfg(), path)
    calls = []

    class Sched:
        @staticmethod
        def install(minutes, config_path):
            calls.append((minutes, config_path))
            return "TacoSort will now run every 30 minutes."

    monkeypatch.setattr(app.cli, "_schedule", lambda: Sched)
    w = app.App(root, path)
    w.sched_var.set(True)
    w.interval_var.set("30")
    w.save_options()
    # Run the job's hand-back ourselves: root.update() can block on a Mac with no screen (CI hung there).
    deadline = time.monotonic() + 10
    while w.jobs.busy and time.monotonic() < deadline:
        with contextlib.suppress(queue.Empty):
            w.jobs._q.get(timeout=0.05)()
    assert calls == [(30, path.resolve())]
    assert load_config(path).interval_minutes == 30
    assert "every 30 minutes" in w.opt_note.get()


def test_window_refuses_a_bad_number(window, tmp_path):
    app, root = window
    path = tmp_path / "config.yaml"
    save_config(cfg(), path)
    w = app.App(root, path)
    w.opt_vars["old_mail_days"][0].set(True)
    w.opt_vars["old_mail_days"][1].set("lots")
    w.save_options()
    assert w.opt_note.get().startswith("Not saved") and load_config(path).old_mail_days == 0


def test_window_never_saves_over_a_broken_settings_file(window, tmp_path, monkeypatch):
    app, root = window
    path = tmp_path / "config.yaml"
    path.write_text("accounts: [unclosed\n", encoding="utf-8")
    shown = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: shown.append(a))
    w = app.App(root, path)
    assert not w.ok and shown
    assert path.read_text(encoding="utf-8") == "accounts: [unclosed\n"


def test_toggle_and_remove_account(window, tmp_path, monkeypatch):
    app, root = window
    path = tmp_path / "config.yaml"
    save_config(cfg(), path)
    w = app.App(root, path)
    forgotten = []
    monkeypatch.setattr(app.cli, "_auth", lambda: type("A", (), {"delete_account_secrets": staticmethod(
        forgotten.append)}))
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: True)
    w.acct_list.selection_set("a")
    w.toggle_account()
    assert load_config(path).accounts[0].enabled is False
    w.acct_list.selection_set("a")
    w.remove_account()
    assert load_config(path).accounts == [] and forgotten == ["a"]


def test_try_login_reports_plainly(monkeypatch):
    from tacosort import app
    from tacosort.backend import BackendError

    class Broken:
        def connect(self):
            raise BackendError("Wrong password.")

        def close(self):
            pass

    class Fine(Broken):
        def connect(self):
            pass

        def list_folders(self):
            return [1, 2, 3]

    acct = cfg().accounts[0]
    monkeypatch.setattr(app.cli, "make_backend", lambda a, secret_getter=None: Broken())
    assert app.try_login(acct, "x") == ("Wrong password.", 0)
    monkeypatch.setattr(app.cli, "make_backend", lambda a, secret_getter=None: Fine())
    assert app.try_login(acct) == (None, 3)


def test_command_hints_point_at_the_window():
    from tacosort.app import for_app

    assert for_app("No password is saved for account 'a'. Run 'tacosort setup' to enter it.") == (
        "No password is saved for account 'a'. Use Add account... on the Accounts tab (with the same email "
        "address) to enter it.")
    assert "Accounts tab" in for_app("then run `tacosort setup` again.")
    assert for_app("Nothing to change here.") == "Nothing to change here."


def test_assets_are_shipped():
    from tacosort.app import ASSETS

    assert (ASSETS / "tacosort.ico").stat().st_size > 1000
    assert Path(ASSETS / "tacosort.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
