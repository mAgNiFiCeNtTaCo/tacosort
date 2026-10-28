"""Lets people run `python -m tacosort ...` (the background schedule uses this too)."""

from .cli import main

raise SystemExit(main())
