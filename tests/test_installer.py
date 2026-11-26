"""The Windows zip installer: what goes into the zip, and that install / uninstall touch only their own files."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "packaging" / "windows"


def load_build():
    spec = importlib.util.spec_from_file_location("tacosort_build", ROOT / "packaging" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_zip_holds_the_app_and_the_installer(tmp_path):
    build = load_build()
    exe = tmp_path / "TacoSort-9.9.9-windows.exe"
    exe.write_bytes(b"MZ not really a program")
    made = build.windows_installer_zip(exe, "9.9.9", tmp_path)
    assert made.name == "TacoSort-9.9.9-windows-installer.zip"
    with zipfile.ZipFile(made) as z:
        names = set(z.namelist())
        assert names == {"TacoSort-9.9.9/TacoSort.exe"} | {f"TacoSort-9.9.9/{n}" for n in build.INSTALLER_FILES}
        assert z.read("TacoSort-9.9.9/TacoSort.exe") == exe.read_bytes()
        for name in build.INSTALLER_FILES:
            data = z.read(f"TacoSort-9.9.9/{name}")
            assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")       # Windows line ends only
            data.decode("ascii")                                    # PowerShell 5.1 reads these as ANSI
    assert all((SCRIPTS / n).is_file() for n in build.INSTALLER_FILES)


@pytest.mark.skipif(os.name != "nt", reason="the installer is for Windows")
def test_install_then_uninstall_leaves_other_files_alone(tmp_path):
    unzipped = tmp_path / "unzipped"
    unzipped.mkdir()
    (unzipped / "TacoSort.exe").write_bytes(b"MZ not really a program")
    for name in ("install.ps1", "uninstall.ps1", "Uninstall TacoSort.cmd"):
        (unzipped / name).write_bytes((SCRIPTS / name).read_bytes())
    target = tmp_path / "Programs" / "TacoSort"
    links = tmp_path / "links"
    target.mkdir(parents=True)
    (target / "something else.txt").write_text("not the installer's", encoding="utf-8")

    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(unzipped / "install.ps1"), "-Target", str(target), "-ShortcutDirs", str(links),
                           "-NoStart"], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (target / "TacoSort.exe").read_bytes() == b"MZ not really a program"
    assert (target / "Uninstall TacoSort.cmd").is_file() and (target / "uninstall.ps1").is_file()
    assert (links / "TacoSort.lnk").is_file()
    stranger = tmp_path / "elsewhere"            # a second copy of TacoSort with its own shortcut
    theirs = tmp_path / "their links"
    made = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(unzipped / "install.ps1"), "-Target", str(stranger), "-ShortcutDirs", str(theirs),
                           "-NoStart"], capture_output=True, text=True, timeout=120, check=False)
    assert made.returncode == 0 and (theirs / "TacoSort.lnk").is_file(), made.stdout + made.stderr
    other = links / "Other.lnk"
    other.write_bytes(b"someone else's shortcut")

    gone = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(target / "uninstall.ps1"), "-ShortcutDirs", str(links), "-Yes"],
                          capture_output=True, text=True, timeout=120, check=False)
    assert gone.returncode == 0, gone.stdout + gone.stderr
    again = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(unzipped / "uninstall.ps1"), "-Target", str(target), "-ShortcutDirs", str(theirs),
                            "-Yes"], capture_output=True, text=True, timeout=120, check=False)
    assert again.returncode == 0, again.stdout + again.stderr
    assert (theirs / "TacoSort.lnk").is_file()            # a shortcut to another copy of TacoSort is not ours
    assert not (target / "TacoSort.exe").exists() and not (links / "TacoSort.lnk").exists()
    assert not (target / "uninstall.ps1").exists()
    assert (target / "something else.txt").is_file() and other.is_file()     # only its own files went


@pytest.mark.skipif(os.name != "nt", reason="the installer is for Windows")
def test_install_says_so_when_the_app_is_missing(tmp_path):
    (tmp_path / "install.ps1").write_bytes((SCRIPTS / "install.ps1").read_bytes())
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(tmp_path / "install.ps1"), "-Target", str(tmp_path / "t"), "-NoStart"],
                          capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 1 and "Unzip the whole folder" in done.stdout
    assert not (tmp_path / "t").exists()
