"""The phone page: TacoSort in your phone's browser, reached over Tailscale.

`tacosort phone serve` runs a small web server on 127.0.0.1 (this computer only; nothing else on the
network can reach it). `tailscale serve` passes requests from your own Tailscale devices to it over
HTTPS and adds the Tailscale login of whoever is asking; the page answers only the logins listed in
`phone_logins` (filled in with your own login when you turn it on). It is never put on the internet
(no Tailscale Funnel, no port forwarding).

`tacosort phone on` does all of it: remembers your login, adds the `tailscale serve` entry, starts the
server in the background and starts it with Windows from then on. `tacosort phone off` undoes that.

What the page can do is what the window can: status, preview, sort now, undo, every move, turn the
background schedule on or off, and the tick-box options (actions.py and options.py do the work).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__, actions, cli, options, peer
from .backend import BackendError
from .config import ConfigError, config_problems, load_config, save_config
from .models import AppConfig

WEB = Path(__file__).parent / "web"
ASSETS = Path(__file__).parent / "assets"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TacoSort phone page"
MAX_BODY = 64_000
MAX_LINES = 300
LOCAL_NAMES = {"127.0.0.1", "localhost"}
PROXY_HEADERS = ("Tailscale-User-Login", "X-Forwarded-For", "X-Forwarded-Host", "Forwarded")

FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}
ICONS = {"/icon.png", "/apple-touch-icon.png"}

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


class PhoneError(Exception):
    """Something the person can fix; the message says how, in plain words."""


# ---- jobs (one at a time, like the window) ----------------------------------------------------------
@dataclass
class Job:
    kind: str
    started: datetime
    lines: list[str] = field(default_factory=list)
    finished: datetime | None = None
    result: Any = None
    error: str = ""


_SETUP_HINT = re.compile(r"\b([Rr])un ['`]tacosort (?:setup|accounts add)['`](?: again)?")


def for_phone(text: str) -> str:
    """Messages written for the command ("run 'tacosort setup'") pointed at the app on the computer."""
    return _SETUP_HINT.sub(lambda m: ("O" if m.group(1) == "R" else "o") + "pen TacoSort on your computer and "
                           "use Add account... on the Accounts tab (with the same email address)", text)


def plain_error(e: BaseException) -> str:
    if isinstance(e, (BackendError, ConfigError, cli.UserError, PhoneError)):
        return for_phone(str(e))
    return f"Something went wrong: {type(e).__name__}."


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if isinstance(dt, datetime) else None


def move_json(m: Any, emails: dict[str, str]) -> dict:
    return {"at": _iso(m.at), "account": emails.get(m.account, m.account), "sender": m.sender,
            "subject": m.subject, "from": m.from_folder, "to": m.to_folder, "reason": m.reason,
            "undone": bool(getattr(m, "undone", False))}


class Phone:
    """What the server knows: the settings file and the job that is running (if any)."""

    def __init__(self, config_path: Path):
        self.path = Path(config_path)
        self.job: Job | None = None
        self._lock = threading.Lock()

    def cfg(self) -> AppConfig:
        return load_config(self.path)          # read every time: the window or the command may have changed it

    # ---- reading ------------------------------------------------------------------------------------
    def status(self) -> dict:
        cfg = self.cfg()
        on, text = actions.schedule_is_on()
        last, today = None, 0
        store = cli._store_if_exists(cfg)
        try:
            if store is not None:
                run = store.last_run(dry_run=False)
                if run:
                    last = {"started": _iso(run["started"]), "finished": _iso(run["finished"]),
                            "scanned": run["scanned"], "moved": run["moved"], "errors": run["errors"]}
                midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
                today = store.moves_since(midnight)
        finally:
            cli._close(store)
        return {"version": __version__,
                "accounts": [{"id": a.id, "email": a.email, "enabled": a.enabled} for a in cfg.accounts],
                "schedule": {"on": on, "text": text, "interval": cfg.interval_minutes},
                "last_run": last, "moved_today": today, "job": self.job_json()}

    def moves(self, limit: int) -> dict:
        cfg = self.cfg()
        emails = {a.id: a.email for a in cfg.accounts}
        store = cli._store_if_exists(cfg)
        try:
            rows = store.recent_moves(limit) if store is not None else []
        finally:
            cli._close(store)
        return {"moves": [move_json(m, emails) for m in rows]}

    def options(self) -> dict:
        cfg = self.cfg()
        values = options.read(cfg)
        groups = []
        for title, opts in options.GROUPS:
            groups.append({"title": title, "options": [
                {"field": o.field, "label": o.label, "help": o.help, "unit": o.unit, "low": o.low, "high": o.high,
                 "ticked": values.options[o.field][0], "number": values.options[o.field][1]} for o in opts]})
        folders = [{"key": key, "folder": folder, "about": about, "ticked": values.categories.get(key, False)}
                   for key, folder, about in options.categories(cfg)]
        return {"groups": groups, "folders": folders, "interval": values.interval_minutes}

    # ---- changing -----------------------------------------------------------------------------------
    def save_options(self, body: dict) -> dict:
        cfg = self.cfg()
        current = options.read(cfg)
        sent = body.get("options") or {}
        opts = {}
        for name, (ticked, number) in current.options.items():
            got = sent.get(name)
            if isinstance(got, dict):
                ticked, number = bool(got.get("ticked")), got.get("number", number)
            opts[name] = (ticked, number)
        cats = dict(current.categories)
        for key, ticked in (body.get("folders") or {}).items():
            if key in cats:
                cats[key] = bool(ticked)
        values = options.Values(opts, cats, body.get("interval", current.interval_minutes))
        new, problems = options.apply(cfg, values)
        if problems:
            return {"ok": False, "problems": problems}
        save_config(new, self.path)
        on, _ = actions.schedule_is_on()
        note = "Saved. The next sort uses these settings."
        if on and new.interval_minutes != cfg.interval_minutes:
            note = "Saved. " + cli._schedule().install(new.interval_minutes, self.path.resolve())
        return {"ok": True, "note": note}

    def set_schedule(self, on: bool) -> dict:
        cfg = self.cfg()
        if on and not actions.enabled_accounts(cfg):
            raise PhoneError("Add an account first (in the TacoSort app on your computer).")
        sched = cli._schedule()
        try:
            text = sched.install(cfg.interval_minutes, self.path.resolve()) if on else sched.uninstall()
        except (RuntimeError, OSError, ValueError) as e:
            raise PhoneError(f"The background schedule couldn't be changed: {e}") from None
        return {"ok": True, "note": text}

    def start(self, kind: str) -> dict:
        work = {"preview": self._preview, "sort": self._sort, "undo": self._undo, "learn": self._learn}.get(kind)
        if work is None:
            raise PhoneError("Unknown job.")
        cfg = self.cfg()
        if kind != "undo" and not actions.enabled_accounts(cfg):
            raise PhoneError("Add an email account first (in the TacoSort app on your computer).")
        with self._lock:
            if self.job is not None and self.job.finished is None:
                raise PhoneError("TacoSort is still busy with the last job. Please wait a moment.")
            job = self.job = Job(kind, datetime.now().astimezone())

        def log(line: str) -> None:
            job.lines.append(for_phone(line.rstrip()))
            del job.lines[:-MAX_LINES]

        def run() -> None:
            try:
                job.result = work(cfg, log)
            except BaseException as e:  # noqa: BLE001 - shown to the person in plain words
                job.error = plain_error(e)
            job.finished = datetime.now().astimezone()

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "job": self.job_json()}

    def job_json(self) -> dict | None:
        job = self.job
        if job is None:
            return None
        return {"kind": job.kind, "running": job.finished is None, "started": _iso(job.started),
                "finished": _iso(job.finished), "lines": list(job.lines), "result": job.result, "error": job.error}

    @staticmethod
    def _preview(cfg: AppConfig, log: actions.Log) -> dict:
        moves, scanned, kept, problems = actions.preview(cfg, log)
        emails = {a.id: a.email for a in cfg.accounts}
        return {"moves": [move_json(m, emails) for m in moves], "scanned": scanned, "kept": kept,
                "problems": [for_phone(p) for p in problems]}

    @staticmethod
    def _sort(cfg: AppConfig, log: actions.Log) -> dict:
        moved, problems = actions.sort_now(cfg, log)
        return {"moved": moved, "problems": [for_phone(p) for p in problems]}

    @staticmethod
    def _undo(cfg: AppConfig, log: actions.Log) -> dict:
        return {"undone": actions.undo_last(cfg, log)}

    @staticmethod
    def _learn(cfg: AppConfig, log: actions.Log) -> dict:
        rep = actions.learn(cfg, log)
        return {"rules": getattr(rep, "rules_learned", 0), "folders": getattr(rep, "folders_scanned", 0),
                "problems": [for_phone(p) for p in getattr(rep, "errors", [])]}


# ---- the web server ---------------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "TacoSort"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 15                               # a connection that sends nothing is dropped
    _peer: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        pass                                   # no request log: paths and logins stay off the disk

    # ---- who is asking ------------------------------------------------------------------------------
    def _host(self) -> str | None:
        hosts = self.headers.get_all("Host") or []
        return hosts[0].strip().lower() if len(hosts) == 1 else None

    def peer(self) -> str:
        """Who owns the other end of this connection (peer.py): 'tailscale', 'me', 'other' or 'unknown'."""
        if self._peer is None:
            self._peer = peer.kind_of_client(self.client_address[1], self.server.server_address[1])
        return self._peer

    def who(self) -> str | None:
        """'local' for this computer's own browser, the Tailscale login of an allowed person, else None.

        tailscale serve connects from 127.0.0.1 and sets Tailscale-User-Login itself (it drops one sent by
        the phone). 127.0.0.1 is shared by every account on the computer, so the program on the other end
        must be Tailscale or one of your own (peer.py). Any other Host name is refused: that stops a web
        page that points its own name at 127.0.0.1 (DNS rebinding)."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return None
        kind = self.peer()
        if kind == "other":
            return None
        host = self._host()
        if not host:
            return None
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if name not in LOCAL_NAMES and not name.endswith(".ts.net"):
            return None
        login = (self.headers.get("Tailscale-User-Login") or "").strip().lower()
        if login:
            allowed = self.server.phone.cfg().phone_logins  # type: ignore[attr-defined]
            return login if login in allowed else None
        if (name in LOCAL_NAMES and kind in ("me", "unknown")
                and not any(self.headers.get(h) for h in PROXY_HEADERS)):
            return "local"
        return None

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return urlsplit(origin).netloc.lower() == (self._host() or "")

    # ---- answering ----------------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        if self.headers.get("Transfer-Encoding") is not None:
            raise ApiError(411, "Send a Content-Length.")
        raw = (self.headers.get("Content-Length") or "0").strip()
        if not raw.isdigit() or int(raw) > MAX_BODY:
            raise ApiError(413, "That request is too big.")
        data = self.rfile.read(int(raw)) if int(raw) else b"{}"
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "That request isn't valid JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(400, "That request isn't a JSON object.")
        return body

    def _dispatch(self, method: str) -> None:
        try:
            if self.who() is None:
                raise ApiError(403, "Not allowed. Turn on the phone page in TacoSort on your computer, and "
                                    "sign in to Tailscale on this phone with the same login.")
            url = urlsplit(self.path)
            if method == "GET":
                self._get(url.path, parse_qs(url.query))
            else:
                # A page from another site can't send this header or this type without the browser
                # asking first (which is refused), so only TacoSort's own page can change anything.
                if (self.headers.get("X-TacoSort") != "1" or not self._same_origin()
                        or not (self.headers.get("Content-Type") or "").startswith("application/json")):
                    raise ApiError(403, "Not allowed from this page.")
                self._post(url.path, self._body())
        except ApiError as e:
            self._json(e.status, {"ok": False, "error": str(e)})
        except (PhoneError, ConfigError, cli.UserError, BackendError) as e:
            self._json(400, {"ok": False, "error": plain_error(e)})
        except Exception as e:  # noqa: BLE001 - never show internals
            self._json(500, {"ok": False, "error": plain_error(e)})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:          # a cross-site preflight: always no
        self._json(403, {"ok": False, "error": "Not allowed."})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS

    def _get(self, path: str, query: dict) -> None:
        phone: Phone = self.server.phone  # type: ignore[attr-defined]
        if path in FILES:
            name, kind = FILES[path]
            self._send(200, (WEB / name).read_bytes(), kind)
        elif path in ICONS:
            self._send(200, (ASSETS / "tacosort.png").read_bytes(), "image/png")
        elif path == "/api/ping":
            self._json(200, {"app": "tacosort", "pid": os.getpid(), "version": __version__})
        elif path == "/api/status":
            self._json(200, phone.status())
        elif path == "/api/moves":
            try:
                limit = max(1, min(int((query.get("limit") or ["100"])[0]), 500))
            except ValueError:
                limit = 100
            self._json(200, phone.moves(limit))
        elif path == "/api/options":
            self._json(200, phone.options())
        elif path == "/api/job":
            self._json(200, {"job": phone.job_json()})
        else:
            raise ApiError(404, "Not found.")

    def _post(self, path: str, body: dict) -> None:
        phone: Phone = self.server.phone  # type: ignore[attr-defined]
        if path == "/api/job":
            self._json(200, phone.start(str(body.get("kind", ""))))
        elif path == "/api/schedule":
            self._json(200, phone.set_schedule(bool(body.get("on"))))
        elif path == "/api/options":
            self._json(200, phone.save_options(body))
        elif path == "/api/stop" and self.who() == "local":
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            raise ApiError(404, "Not found.")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    # Windows lets a second program bind a port opened with SO_REUSEADDR and share its connections;
    # SO_EXCLUSIVEADDRUSE keeps the port to this one.
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(config_path: Path, port: int) -> Server:
    try:
        httpd = Server(("127.0.0.1", port), Handler)
    except OSError as e:
        raise PhoneError(f"Port {port} is already in use, so the phone page can't start. Is it already on? "
                         "Otherwise pick another port with phone_port in the settings file.") from e
    httpd.phone = Phone(config_path)  # type: ignore[attr-defined]
    return httpd


def serve(config_path: Path, port: int | None = None) -> None:
    """Run the phone page until stopped (this is what starts with Windows)."""
    cfg = load_config(config_path)
    port = port or cfg.phone_port
    httpd = make_server(config_path, port)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


# ---- control: running, start, stop, start with Windows, Tailscale -----------------------------------
def ping(port: int, timeout: float = 1.5) -> dict | None:
    """The running phone page's answer on this port, or None (also when another account's program holds it)."""
    if peer.kind_of_listener(port) == "other":
        return None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        conn.request("GET", "/api/ping", headers={"Host": f"127.0.0.1:{port}"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8")) if resp.status == 200 else None
        conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return data if isinstance(data, dict) and data.get("app") == "tacosort" else None


def serve_command(config_path: Path) -> list[str]:
    from . import schedule

    start = [] if schedule.frozen() else ["-m", "tacosort"]
    return [schedule.windowless_python(), *schedule.python_options(), *start,
            "phone", "serve", "--config", str(Path(config_path).resolve())]


def start_background(config_path: Path, port: int, wait: float = 10.0) -> bool:
    """Start `phone serve` with no window, detached from this program. True once it answers."""
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | 0x00000008)                 # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(serve_command(config_path), **kwargs)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if ping(port, timeout=0.5):
            return True
        time.sleep(0.25)
    return False


def stop_running(port: int, wait: float = 5.0) -> bool:
    """Ask the phone page on this port to stop. True when nothing answers any more.

    It is asked, never killed by a process number: whatever answers on the port could name any one."""
    if not ping(port):
        return True
    with contextlib.suppress(OSError, http.client.HTTPException):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("POST", "/api/stop", body=b"{}", headers={
            "Host": f"127.0.0.1:{port}", "X-TacoSort": "1", "Content-Type": "application/json"})
        conn.getresponse().read()
        conn.close()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not ping(port, timeout=0.5):
            return True
        time.sleep(0.2)
    return False


def autostart_supported() -> bool:
    return os.name == "nt"


def autostart_is_on() -> bool:
    if not autostart_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(config_path: Path | None, on: bool) -> None:
    """Start the phone page when you sign in to Windows (your user only; nothing runs as administrator)."""
    if not autostart_supported():
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(serve_command(config_path)))
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, RUN_VALUE)


def tailscale_install_paths() -> list[Path]:
    return [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
            Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
            Path("/usr/bin/tailscale"), Path("/usr/local/bin/tailscale")]


def tailscale_exe() -> str | None:
    """Where Tailscale's command is: its install folder first, then PATH.

    Never the folder TacoSort was started from (often Downloads), where a planted tailscale.cmd would
    otherwise be found first on Windows."""
    for candidate in tailscale_install_paths():
        if candidate.is_file():
            return str(candidate)
    here = Path.cwd().resolve()
    name = "tailscale.exe" if os.name == "nt" else "tailscale"
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or not Path(folder).is_absolute() or Path(folder).resolve() == here:
            continue
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _tailscale(*args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    exe = tailscale_exe()
    if not exe:
        raise PhoneError("Tailscale isn't installed on this computer. Install it from tailscale.com, sign in, "
                         "then turn the phone page on again.")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout,
                              check=False, **flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PhoneError(f"Tailscale didn't answer ({type(e).__name__}). Is it running and signed in?") from None


def tailscale_me() -> tuple[str, str]:
    """(this computer's Tailscale name, the login signed in here), e.g. ('pc.tail1234.ts.net', 'you@gmail.com')."""
    done = _tailscale("status", "--json")
    try:
        data = json.loads(done.stdout)
        me = data["Self"]
        name = str(me.get("DNSName") or "").rstrip(".")
        login = str(data["User"][str(me["UserID"])]["LoginName"]).strip().lower()
    except (ValueError, KeyError, TypeError):
        raise PhoneError("Tailscale is installed but not signed in. Sign in to Tailscale, then turn the phone "
                         "page on again.") from None
    if not name or not login:
        raise PhoneError("Tailscale is installed but not signed in. Sign in to Tailscale, then try again.")
    return name, login


def tailscale_serve(port: int, on: bool) -> None:
    """Add or remove the `tailscale serve` entry for this port (tailnet only; other ports are not touched)."""
    args = (["serve", "--bg", f"--https={port}", f"http://127.0.0.1:{port}"] if on
            else ["serve", f"--https={port}", "off"])
    done = _tailscale(*args)
    if done.returncode != 0 and on:
        text = (done.stderr or done.stdout or "").strip().splitlines()
        raise PhoneError("Tailscale couldn't share the phone page: " + (text[-1] if text else "no reason given")
                         + ". If it mentions HTTPS, turn on HTTPS certificates in the Tailscale admin page "
                           "(DNS tab), then try again.")


def address(cfg: AppConfig) -> str:
    try:
        name, _ = tailscale_me()
    except PhoneError:
        return f"http://127.0.0.1:{cfg.phone_port}/"
    return f"https://{name}:{cfg.phone_port}/"


def turn_on(config_path: Path) -> str:
    """Remember your Tailscale login, share the page over Tailscale, start it now and with Windows."""
    cfg = load_config(config_path)
    name, login = tailscale_me()
    if login not in cfg.phone_logins:
        cfg.phone_logins = [*cfg.phone_logins, login]
        problems = config_problems(cfg)
        if problems:
            raise PhoneError(" ".join(problems))
        save_config(cfg, config_path)
    tailscale_serve(cfg.phone_port, True)
    set_autostart(config_path, True)
    if not ping(cfg.phone_port) and not start_background(config_path, cfg.phone_port):
        raise PhoneError("The phone page didn't start. Try `tacosort phone serve` in a terminal to see why.")
    later = " It starts by itself when you sign in to Windows." if autostart_supported() else (
        " Run `tacosort phone serve` again after a restart.")
    return (f"The phone page is on. On your phone (with Tailscale switched on, signed in as {login}) open "
            f"https://{name}:{cfg.phone_port}/ and add it to your home screen.{later}")


def turn_off(config_path: Path) -> str:
    cfg = load_config(config_path)
    set_autostart(None, False)
    stopped = stop_running(cfg.phone_port)
    with contextlib.suppress(PhoneError):
        if tailscale_exe():
            tailscale_serve(cfg.phone_port, False)
    if not stopped:
        raise PhoneError("The phone page didn't stop. Restarting the computer stops it (it won't start again).")
    return "The phone page is off, and it won't start with Windows."


def describe(config_path: Path) -> tuple[bool, str]:
    """(on, one plain line) for the window and `tacosort phone status`."""
    cfg = load_config(config_path)
    running = bool(ping(cfg.phone_port))
    starts = autostart_is_on()
    if running:
        return True, f"The phone page is on at {address(cfg)}" + (
            " and starts with Windows." if starts else " (it won't start again after a restart).")
    if starts:
        return False, "The phone page isn't running right now, but it starts when you sign in to Windows."
    return False, "The phone page is off."


def main_serve(config_path: Path, port: int | None) -> int:
    try:
        serve(config_path, port)
    except PhoneError as e:
        print(str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0
