"""The colour themes, shared by the window (app.py, widgets.py) and the phone page (/themes.css).

A theme is a set of named colours ("tokens"). Nothing here needs a screen, so every theme can be
checked in tests (all tokens present, text readable on its background).
"""

from __future__ import annotations

import contextlib
import subprocess
import sys

SYSTEM = "system"          # follow the computer / phone: Light or Dark
DEFAULT = SYSTEM

TOKENS = ("bg", "side", "surface", "surface2", "text", "muted", "line", "accent", "accent_text", "ok",
          "warn_bg", "warn_text", "danger")

# name -> (label, "light" | "dark", colours)
THEMES: dict[str, tuple[str, str, dict[str, str]]] = {
    "light": ("Light", "light", {
        "bg": "#f4f6f6", "side": "#e9eeed", "surface": "#ffffff", "surface2": "#edf2f1", "text": "#17201f",
        "muted": "#5d6967", "line": "#dfe5e4", "accent": "#0f766e", "accent_text": "#ffffff", "ok": "#15803d",
        "warn_bg": "#fff4e5", "warn_text": "#7a4100", "danger": "#b42318"}),
    "dark": ("Dark", "dark", {
        "bg": "#0f1514", "side": "#0b100f", "surface": "#182120", "surface2": "#223030", "text": "#e6eceb",
        "muted": "#9aa8a6", "line": "#2a3533", "accent": "#2dd4bf", "accent_text": "#062b27", "ok": "#4ade80",
        "warn_bg": "#3a2a12", "warn_text": "#ffd59e", "danger": "#ff8a80"}),
    "midnight": ("Midnight", "dark", {
        "bg": "#0b1020", "side": "#080c18", "surface": "#141b33", "surface2": "#1e2747", "text": "#e7eaf6",
        "muted": "#9aa3c2", "line": "#283258", "accent": "#a78bfa", "accent_text": "#1a103d", "ok": "#4ade80",
        "warn_bg": "#3a2a12", "warn_text": "#ffd59e", "danger": "#ff8a8a"}),
    "taco": ("Taco", "light", {
        "bg": "#fbf5ea", "side": "#f3e8d6", "surface": "#fffdf8", "surface2": "#f6ecda", "text": "#2b2118",
        "muted": "#6f5f4e", "line": "#eadcc5", "accent": "#c2410c", "accent_text": "#ffffff", "ok": "#3f7d20",
        "warn_bg": "#fdecc8", "warn_text": "#7a4100", "danger": "#b42318"}),
    "rose": ("Rose", "light", {
        "bg": "#fbf4f6", "side": "#f5e7ec", "surface": "#ffffff", "surface2": "#f8eaef", "text": "#24161b",
        "muted": "#6d5a61", "line": "#edd9df", "accent": "#be185d", "accent_text": "#ffffff", "ok": "#15803d",
        "warn_bg": "#fff4e5", "warn_text": "#7a4100", "danger": "#b42318"}),
    "contrast": ("Contrast", "dark", {
        "bg": "#000000", "side": "#000000", "surface": "#000000", "surface2": "#1f1f1f", "text": "#ffffff",
        "muted": "#d6d6d6", "line": "#ffffff", "accent": "#ffd400", "accent_text": "#000000", "ok": "#5cff5c",
        "warn_bg": "#000000", "warn_text": "#ffd400", "danger": "#ff7b7b"}),
}

NAMES: list[str] = [SYSTEM, *THEMES]


def label(name: str) -> str:
    return "System" if name == SYSTEM else THEMES[name][0]


def system_is_dark() -> bool:
    """Whether the computer is set to dark mode (False when it can't be told)."""
    with contextlib.suppress(Exception):
        if sys.platform.startswith("win"):
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            out = subprocess.run(["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"], check=False,
                                 capture_output=True, text=True, timeout=5)
            return "dark" in out.stdout.lower()
    return False


def resolve(name: str) -> str:
    """The theme to draw for a saved choice: "system" (or a name from a newer TacoSort) -> light or dark."""
    if name in THEMES:
        return name
    return "dark" if system_is_dark() else "light"


def colours(name: str) -> dict[str, str]:
    return dict(THEMES[resolve(name)][2])


def is_dark(name: str) -> bool:
    return THEMES[resolve(name)][1] == "dark"


def problem(name: object) -> str | None:
    """Why `name` can't be saved as the theme (plain English), or None when it is fine."""
    if name in NAMES:
        return None
    return f"theme must be one of: {', '.join(NAMES)}."


# ---- for the phone page ------------------------------------------------------------------------------
def _block(selector: str, name: str) -> str:
    _label, kind, tokens = THEMES[name]
    lines = [f"  --{key.replace('_', '-')}: {tokens[key]};" for key in TOKENS]
    return f"{selector} {{\n  color-scheme: {kind};\n" + "\n".join(lines) + "\n}\n"


def css() -> str:
    """Every theme as CSS variables: `<html data-theme="midnight">` picks one, "system" follows the phone."""
    parts = ["/* Made by tacosort/theme.py: change the colours there. */\n", _block(":root", "light")]
    dark = _block(':root:not([data-theme]), :root[data-theme="system"]', "dark")
    parts.append("@media (prefers-color-scheme: dark) {\n" + dark + "}\n")
    parts += [_block(f':root[data-theme="{name}"]', name) for name in THEMES]
    for name in NAMES:                      # the round buttons that pick a look: half page, half accent
        left = THEMES["light" if name == SYSTEM else name][2]
        right = THEMES["dark"][2]["bg"] if name == SYSTEM else left["accent"]
        parts.append(f'.look[data-look="{name}"] {{ background: linear-gradient(90deg, {left["bg"]} 50%, '
                     f'{right} 50%); }}\n')
    return "".join(parts)


# ---- contrast (used by the tests) --------------------------------------------------------------------
def rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def mix(a: str, b: str, t: float) -> str:
    """`a` moved `t` (0..1) of the way to `b`."""
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(rgb(a), rgb(b)))


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1 = the same, 21 = black on white)."""
    def lum(colour: str) -> float:
        parts = [(v / 255 / 12.92) if v / 255 <= 0.03928 else ((v / 255 + 0.055) / 1.055) ** 2.4
                 for v in rgb(colour)]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
