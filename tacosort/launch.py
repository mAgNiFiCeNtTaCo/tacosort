"""What the downloadable app runs. Double-clicked (no arguments) it opens the window; with a command it
is the `tacosort` command (the background schedule runs `TacoSort.exe run --quiet --config ...`).

The app is built without a console window, so there is nowhere to print to: output goes nowhere
instead of crashing (the log file in the data folder still records every run).
"""

from __future__ import annotations

import os
import sys


def _no_console_safe() -> None:
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - lives as long as the app


def smoke_test(path: str) -> int:
    """Check the built app has every part it needs (used by the build): writes what it found to `path`."""
    import importlib
    import pkgutil
    import tkinter

    import keyring

    import tacosort

    lines = [f"tacosort {tacosort.__version__}"]
    for mod in pkgutil.walk_packages(tacosort.__path__, "tacosort."):
        if mod.name == "tacosort.__main__":      # runs the command as soon as it is imported
            continue
        importlib.import_module(mod.name)
        lines.append(f"import {mod.name}")
    lines.append(f"keyring backend {type(keyring.get_keyring()).__name__}")
    lines.append(f"tk {tkinter.TkVersion}")
    from tacosort.app import ASSETS

    for name in ("tacosort.ico", "tacosort.png"):
        if not (ASSETS / name).exists():
            raise SystemExit(f"missing {name}")
    lines.append("ok")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _no_console_safe()
    if args[:1] == ["--smoke-test"] and len(args) == 2:
        return smoke_test(args[1])
    if not args or (args[0] == "--config" and len(args) == 2):
        from .app import main as app_main

        return app_main(args)
    from .cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
