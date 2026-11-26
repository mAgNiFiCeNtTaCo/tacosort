"""Build the downloadable TacoSort app for this computer's system, with PyInstaller.

    python -m pip install . pyinstaller
    python packaging/build.py

Writes to dist/:
  Windows  TacoSort-<version>-windows.exe   one file, double-click to open
           TacoSort-<version>-windows-installer.zip   the same file plus "Install TacoSort" (packaging/windows/)
  macOS    TacoSort-<version>-macos.zip     TacoSort.app inside
  Linux    TacoSort-<version>-linux         one file (chmod +x, then run it)

Every build is checked by running the result with --smoke-test, which imports every part of TacoSort,
the keychain and Tkinter inside the built app.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "tacosort" / "assets"
WEB = ROOT / "tacosort" / "web"


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from tacosort import __version__

    return __version__


def system() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def pyinstaller_args(kind: str) -> list[str]:
    icon = ASSETS / "tacosort.ico"
    args = [
        str(ROOT / "packaging" / "entry.py"),
        "--name", "TacoSort",
        "--windowed",                                    # no console window; launch.py copes with that
        "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD),
        "--add-data", f"{ASSETS}{os.pathsep}tacosort/assets",
        "--add-data", f"{WEB}{os.pathsep}tacosort/web",   # the phone page
        "--collect-submodules", "tacosort",              # parts imported by name at run time
        "--collect-submodules", "keyring.backends",
        "--copy-metadata", "keyring",                    # keyring finds its backends through package metadata
        "--exclude-module", "pytest",
    ]
    if kind == "macos":
        # (no --icon: a Mac icon must be .icns, and PyInstaller only converts a PNG when Pillow is there)
        args += ["--onedir", "--osx-bundle-identifier", "com.tacosort.app"]
    else:
        args += ["--onefile"]
        if kind == "windows":
            args += ["--icon", str(icon)]
    return args


INSTALLER_FILES = ("Install TacoSort.cmd", "install.ps1", "Uninstall TacoSort.cmd", "uninstall.ps1",
                   "READ ME FIRST.txt")


def windows_installer_zip(exe: Path, ver: str, out_dir: Path = DIST) -> Path:
    """A zip holding one folder: TacoSort.exe and the scripts that install it for one person (no
    administrator): a copy in %LOCALAPPDATA%\\Programs\\TacoSort plus Start menu and Desktop shortcuts."""
    final = out_dir / f"TacoSort-{ver}-windows-installer.zip"
    final.unlink(missing_ok=True)
    folder = f"TacoSort-{ver}"
    with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(exe, f"{folder}/TacoSort.exe")
        for name in INSTALLER_FILES:
            # Batch and PowerShell files need Windows line ends wherever the build's copy came from
            text = (ROOT / "packaging" / "windows" / name).read_bytes().replace(b"\r\n", b"\n")
            z.writestr(f"{folder}/{name}", text.replace(b"\n", b"\r\n"))
    return final


def smoke(app: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke.txt"
        result = subprocess.run([str(app), "--smoke-test", str(out)], check=False, capture_output=True, text=True,
                                timeout=300)
        text = out.read_text(encoding="utf-8") if out.exists() else ""
        if result.returncode != 0 or not text.rstrip().endswith("ok"):
            raise SystemExit(f"The built app failed its self-check (exit {result.returncode}).\n{text}\n"
                             f"{result.stdout}\n{result.stderr}")
        print(text.splitlines()[0], "|", text.splitlines()[-3], "|", text.splitlines()[-2])


def main() -> int:
    import PyInstaller.__main__

    kind = system()
    ver = version()
    shutil.rmtree(BUILD, ignore_errors=True)
    PyInstaller.__main__.run(pyinstaller_args(kind))
    if kind == "windows":
        built = DIST / "TacoSort.exe"
        smoke(built)
        final = DIST / f"TacoSort-{ver}-windows.exe"
        final.unlink(missing_ok=True)
        built.rename(final)
        bundle = windows_installer_zip(final, ver)
        print(f"Built {bundle} ({bundle.stat().st_size // (1024 * 1024)} MB)")
    elif kind == "macos":
        bundle = DIST / "TacoSort.app"
        smoke(bundle / "Contents" / "MacOS" / "TacoSort")
        final = DIST / f"TacoSort-{ver}-macos.zip"
        final.unlink(missing_ok=True)
        # ditto keeps the app's permissions and links, which a plain zip would lose
        if shutil.which("ditto"):
            subprocess.run(["ditto", "-c", "-k", "--keepParent", str(bundle), str(final)], check=True)
        else:
            with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED) as z:
                for path in bundle.rglob("*"):
                    z.write(path, path.relative_to(DIST))
    else:
        built = DIST / "TacoSort"
        smoke(built)
        final = DIST / f"TacoSort-{ver}-linux"
        final.unlink(missing_ok=True)
        built.rename(final)
    print(f"Built {final} ({final.stat().st_size // (1024 * 1024)} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
