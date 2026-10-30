"""The built app's starting point (PyInstaller needs a script; the work is in tacosort/launch.py)."""

import sys

from tacosort.launch import main

if __name__ == "__main__":
    sys.exit(main())
