"""Lets people run `python -m tidyinbox ...` (the background schedule uses this too)."""

from .cli import main

raise SystemExit(main())
