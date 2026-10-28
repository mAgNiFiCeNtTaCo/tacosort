"""Lets people run `python -m mailtidy ...` (the background schedule uses this too)."""

from .cli import main

raise SystemExit(main())
