"""The colour themes (theme.py) and the pictures the window draws for them (widgets.py). No screen needed."""

from __future__ import annotations

import re
import struct
import zlib

import pytest

from tacosort import options, theme, widgets
from tacosort.config import config_problems, load_config, save_config
from tacosort.models import AccountConfig, AppConfig


def cfg(**kw) -> AppConfig:
    return AppConfig(accounts=[AccountConfig(id="a", email="me@example.com", provider="gmail",
                                             host="imap.gmail.com")], **kw)


# ---- the colours -------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(theme.THEMES))
def test_every_theme_has_every_colour(name):
    label, kind, colours = theme.THEMES[name]
    assert label and kind in ("light", "dark")
    assert set(colours) == set(theme.TOKENS)
    assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in colours.values())


# Text must be readable: 4.5 to 1 is the usual bar for normal-size text.
PAIRS = [("text", "bg"), ("text", "surface"), ("text", "surface2"), ("text", "side"), ("muted", "bg"),
         ("muted", "surface"), ("muted", "surface2"), ("muted", "side"), ("accent_text", "accent"),
         ("accent", "surface"), ("accent", "bg"), ("danger", "surface"), ("danger", "bg"),
         ("warn_text", "warn_bg"), ("ok", "surface")]


@pytest.mark.parametrize("name", list(theme.THEMES))
def test_text_is_readable_in_every_theme(name):
    colours = theme.THEMES[name][2]
    weak = {f"{a} on {b}": round(theme.contrast(colours[a], colours[b]), 2) for a, b in PAIRS
            if theme.contrast(colours[a], colours[b]) < 4.5}
    assert not weak


def test_contrast_and_mix_do_the_sums():
    assert theme.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert theme.contrast("#777777", "#777777") == pytest.approx(1.0)
    assert theme.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert theme.mix("#102030", "#ffffff", 0.0) == "#102030"


def test_system_follows_the_computer(monkeypatch):
    monkeypatch.setattr(theme, "system_is_dark", lambda: True)
    assert theme.resolve("system") == "dark" and theme.is_dark("system")
    monkeypatch.setattr(theme, "system_is_dark", lambda: False)
    assert theme.resolve("system") == "light" and not theme.is_dark("system")
    assert theme.resolve("a look from a newer TacoSort") == "light"      # never a crash, never a blank window
    assert theme.resolve("taco") == "taco" and theme.colours("taco")["accent"] == theme.THEMES["taco"][2]["accent"]
    assert isinstance(theme.system_is_dark(), bool)


def test_names_and_labels():
    assert theme.NAMES[0] == theme.SYSTEM == theme.DEFAULT
    assert [theme.label(n) for n in theme.NAMES][:3] == ["System", "Light", "Dark"]
    assert theme.problem("midnight") is None and theme.problem("system") is None
    assert "midnight" in theme.problem("purple") and theme.problem(None)


def test_the_phone_page_gets_every_theme_as_css():
    css = theme.css()
    for name, (_label, kind, colours) in theme.THEMES.items():
        block = css.split(f':root[data-theme="{name}"] {{')[1].split("}")[0]
        assert f"color-scheme: {kind};" in block
        assert f"--accent-text: {colours['accent_text']};" in block and f"--bg: {colours['bg']};" in block
    assert "@media (prefers-color-scheme: dark)" in css and ':root[data-theme="system"]' in css
    assert all(f'.look[data-look="{name}"]' in css for name in theme.NAMES)
    assert "<" not in css and "url(" not in css          # colours only: nothing that loads or runs anything


# ---- the setting -------------------------------------------------------------------------------------
def test_the_theme_setting_is_saved_and_checked(tmp_path):
    assert AppConfig().theme == "system"
    path = tmp_path / "config.yaml"
    save_config(cfg(theme="midnight"), path)
    assert load_config(path).theme == "midnight"
    assert config_problems(cfg(theme="rose")) == []
    assert any("theme must be one of" in p for p in config_problems(cfg(theme="purple")))


def test_options_carry_the_theme():
    values = options.read(cfg(theme="taco"))
    assert values.theme == "taco"
    values.theme = "contrast"
    new, problems = options.apply(cfg(theme="taco"), values)
    assert problems == [] and new.theme == "contrast"
    values.theme = ""                                   # a caller that doesn't show the look leaves it alone
    assert options.apply(cfg(theme="taco"), values)[0].theme == "taco"
    values.theme = "purple"
    assert options.apply(cfg(), values)[1] == ["Pick a look from the list."]


# ---- the pictures ------------------------------------------------------------------------------------
def pixels(png: bytes) -> tuple[int, int, bytes]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, colour_type = struct.unpack(">IIBB", png[16:26])
    assert (depth, colour_type) == (8, 6)               # 8-bit red, green, blue and see-through
    at, data = 8, b""
    while at < len(png):
        length, kind = struct.unpack(">I4s", png[at:at + 8])
        body = png[at + 8:at + 8 + length]
        assert struct.unpack(">I", png[at + 8 + length:at + 12 + length])[0] == zlib.crc32(kind + body)
        if kind == b"IDAT":
            data += body
        at += 12 + length
    raw = zlib.decompress(data)
    rows = [raw[y * (w * 4 + 1) + 1:(y + 1) * (w * 4 + 1)] for y in range(h)]
    return w, h, b"".join(rows)


def test_a_rounded_panel_has_see_through_corners_and_a_border():
    w, h, px = pixels(widgets.panel(28, 10, "#ffffff", "#ff0000").png())
    assert (w, h) == (28, 28)

    def at(x: int, y: int) -> tuple[int, ...]:
        return tuple(px[(y * w + x) * 4:(y * w + x) * 4 + 4])

    assert at(0, 0)[3] == 0                             # the corner shows what is behind it
    assert at(14, 14) == (255, 255, 255, 255)           # the middle is the fill
    assert at(14, 0) == (255, 0, 0, 255)                # the edge is the border
    assert any(0 < px[i * 4 + 3] < 255 for i in range(w * h))      # smooth, not stair-stepped


@pytest.mark.parametrize("name", list(widgets.ICONS))
def test_icons_draw_something(name):
    w, h, px = pixels(widgets.icon(name, 20, "#123456").png())
    solid = [i for i in range(w * h) if px[i * 4 + 3] == 255]
    assert (w, h) == (20, 20) and sum(px[i * 4 + 3] > 128 for i in range(w * h)) > 30 and solid
    assert all(tuple(px[i * 4:i * 4 + 3]) == (0x12, 0x34, 0x56) for i in solid)
    assert px[3] == 0                                   # nothing in the corner


def test_shapes():
    assert widgets.circle(10, 10, 5)(10, 10) == -5 and widgets.circle(10, 10, 5)(15, 10) == 0
    assert widgets.box(0, 0, 20, 10, 2)(10, 5) < 0 < widgets.box(0, 0, 20, 10, 2)(0.2, 0.2)
    assert widgets.line(0, 0, 10, 0, 1)(5, 0) == -1 and widgets.line(0, 0, 10, 0, 1)(5, 3) == 2
    band = widgets.edge(widgets.circle(10, 10, 5), 2)
    assert band(14, 10) < 0 < band(10, 10)              # inside the band, not in the middle
