"""How the window looks: rounded cards, buttons, switches, icons and fonts, in the colours of theme.py.

Tkinter can't draw smooth rounded shapes itself, so the pieces are small pictures made here (pure
Python, no extra package) and used as ttk elements. Every theme is its own ttk theme, built the first
time it is shown; switching is `Look.use(name)`.

Style names say where a widget sits, because its corners show the colour behind it:
  "TLabel" on the page, "Card.TLabel" on a card, "Side.TLabel" in the side bar; then the role, as in
  "Card.Muted.TLabel" or "Card.Primary.TButton". `Look.style_for(parent, "Muted.TLabel")` picks it.
"""

from __future__ import annotations

import base64
import contextlib
import math
import struct
import sys
import tkinter as tk
import zlib
from collections.abc import Callable
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

from . import theme

Sdf = Callable[[float, float], float]          # distance to a shape's edge: below 0 is inside


# ---- drawing small pictures -------------------------------------------------------------------------
def box(x0: float, y0: float, x1: float, y1: float, r: float) -> Sdf:
    """A rectangle with rounded corners."""
    cx, cy, hx, hy = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 - r, (y1 - y0) / 2 - r

    def d(x: float, y: float) -> float:
        qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
        return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r

    return d


def circle(cx: float, cy: float, r: float) -> Sdf:
    return lambda x, y: math.hypot(x - cx, y - cy) - r


def line(x0: float, y0: float, x1: float, y1: float, half: float) -> Sdf:
    """A stroke from one point to another, `half` wide on each side, with round ends."""
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy or 1.0

    def d(x: float, y: float) -> float:
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length2))
        return math.hypot(x - x0 - t * dx, y - y0 - t * dy) - half

    return d


def edge(shape: Sdf, width: float) -> Sdf:
    """The band `width` wide just inside a shape's edge (a border)."""
    return lambda x, y: max(shape(x, y), -(shape(x, y) + width))


def left_of(shape: Sdf, x_cut: float) -> Sdf:
    return lambda x, y: max(shape(x, y), x - x_cut)


class Pic:
    """A small see-through picture that shapes are painted onto, with smooth edges."""

    def __init__(self, w: int, h: int):
        self.w, self.h = max(1, w), max(1, h)
        self.px = [0.0] * (self.w * self.h * 4)       # r, g, b (0..255, already times alpha), alpha (0..1)

    def paint(self, shape: Sdf, colour: str, alpha: float = 1.0) -> Pic:
        r, g, b = theme.rgb(colour)
        px = self.px
        for y in range(self.h):
            for x in range(self.w):
                cover = min(1.0, max(0.0, 0.5 - shape(x + 0.5, y + 0.5))) * alpha
                if cover <= 0.0:
                    continue
                i = (y * self.w + x) * 4
                keep = 1.0 - cover
                px[i] = r * cover + px[i] * keep
                px[i + 1] = g * cover + px[i + 1] * keep
                px[i + 2] = b * cover + px[i + 2] * keep
                px[i + 3] = cover + px[i + 3] * keep
        return self

    def grown(self, extra: int) -> Pic:
        """The same picture with its middle column and middle row repeated `extra` times. ttk fills a
        big card by repeating the middle of its picture, and a middle a few pixels wide makes that
        thousands of little draws (seconds per repaint); a wide middle makes it a handful."""
        w, h, mx, my = self.w, self.h, self.w // 2, self.h // 2
        rows = []
        for y in range(h):
            row = self.px[y * w * 4:(y + 1) * w * 4]
            rows.append(row[:mx * 4] + row[mx * 4:(mx + 1) * 4] * extra + row[mx * 4:])
        rows = rows[:my] + [rows[my]] * extra + rows[my:]
        out = Pic(w + extra, h + extra)
        out.px = [v for row in rows for v in row]
        return out

    def png(self) -> bytes:
        rows = bytearray()
        px = self.px
        for y in range(self.h):
            rows.append(0)
            for x in range(self.w):
                i = (y * self.w + x) * 4
                a = px[i + 3]
                if a <= 0.0:
                    rows += b"\0\0\0\0"
                else:
                    rows += bytes((min(255, round(px[i] / a)), min(255, round(px[i + 1] / a)),
                                   min(255, round(px[i + 2] / a)), min(255, round(a * 255))))

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        head = struct.pack(">IIBBBBB", self.w, self.h, 8, 6, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
                + chunk(b"IEND", b""))


# Icons on a 24 x 24 grid: ("line", x0, y0, x1, y1), ("ring", cx, cy, r) and ("dot", cx, cy, r).
ICONS: dict[str, list[tuple]] = {
    "home": [("line", 3.5, 11.5, 12, 4), ("line", 12, 4, 20.5, 11.5), ("line", 6, 10, 6, 20),
             ("line", 6, 20, 18, 20), ("line", 18, 20, 18, 10), ("line", 10.5, 20, 10.5, 14.5),
             ("line", 10.5, 14.5, 13.5, 14.5), ("line", 13.5, 14.5, 13.5, 20)],
    "sort": [("line", 12, 3.5, 12, 13), ("line", 8.5, 9.5, 12, 13), ("line", 15.5, 9.5, 12, 13),
             ("line", 4, 14.5, 4, 20), ("line", 4, 20, 20, 20), ("line", 20, 20, 20, 14.5)],
    "accounts": [("ring", 12, 8.5, 3.8), ("line", 4.5, 20.5, 5.5, 17.5), ("line", 5.5, 17.5, 8.5, 15.6),
                 ("line", 8.5, 15.6, 12, 15), ("line", 12, 15, 15.5, 15.6), ("line", 15.5, 15.6, 18.5, 17.5),
                 ("line", 18.5, 17.5, 19.5, 20.5)],
    "settings": [("line", 4, 7, 20, 7), ("line", 4, 12, 20, 12), ("line", 4, 17, 20, 17), ("dot", 9, 7, 2.6),
                 ("dot", 15.5, 12, 2.6), ("dot", 8, 17, 2.6)],
}


def icon(name: str, size: int, colour: str) -> Pic:
    k = size / 24
    pic = Pic(size, size)
    half = 0.85 * k
    for kind, *n in ICONS[name]:
        if kind == "line":
            pic.paint(line(n[0] * k, n[1] * k, n[2] * k, n[3] * k, half), colour)
        elif kind == "ring":
            pic.paint(edge(circle(n[0] * k, n[1] * k, n[2] * k + half), 2 * half), colour)
        else:
            pic.paint(circle(n[0] * k, n[1] * k, n[2] * k), colour)
    return pic


def panel(size: int, r: float, fill: str | None, border: str | None = None, width: float = 1.0) -> Pic:
    """A rounded square that ttk stretches (its corners stay, the middle grows)."""
    pic = Pic(size, size)
    shape = box(0, 0, size, size, r)
    if fill:
        pic.paint(shape, fill)
    if border:
        pic.paint(edge(shape, width), border)
    return pic


# ---- the look ---------------------------------------------------------------------------------------
class Look:
    """The window's theme: colours (`c`), fonts, pictures and ttk styles. `px(n)` scales for sharp screens."""

    def __init__(self, root: tk.Misc, name: str = theme.DEFAULT):
        self.root = root
        self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        self.style = ttk.Style(root)
        self.name = name
        self.c: dict[str, str] = {}
        self._pics: dict[str, dict[str, tk.PhotoImage]] = {}
        self._pictured: list[tuple[tk.Misc, str]] = []
        self._listeners: list[Callable[[], None]] = []
        self._wraps: list[ttk.Label] = []
        self.kept: list[tk.PhotoImage] = []    # other pictures the window shows (its logo)
        root.bind("<Destroy>", self._closed, add="+")
        self.content = self.px(880)            # how wide the page beside the side bar is (see rewrap)
        self.fonts = self._make_fonts()
        self.use(name)

    def px(self, n: float) -> int:
        return round(n * self.scale)

    def _closed(self, event: tk.Event) -> None:
        """Let go of the pictures while the window's own thread still can: Tk refuses to delete one from
        the helper thread, which is where the last reference may otherwise be dropped."""
        if event.widget is self.root:
            self._pictured.clear()
            self._pics.clear()
            self.kept.clear()

    # ---- fonts ----------------------------------------------------------------------------------
    def _make_fonts(self) -> dict[str, tkfont.Font]:
        families = set(tkfont.families(self.root))
        base = tkfont.nametofont("TkDefaultFont", self.root).actual()["family"]
        body = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in families), base)
        strong = "Segoe UI Semibold" if "Segoe UI Semibold" in families else body
        weight = "normal" if strong != body else "bold"
        specs = {"body": (body, 10, "normal"), "small": (body, 9, "normal"), "bold": (strong, 10, weight),
                 "h2": (strong, 12, weight), "h1": (strong, 20, weight), "stat": (strong, 17, weight),
                 "brand": (strong, 14, weight)}
        fonts = {key: tkfont.Font(self.root, family=fam, size=size, weight=w) for key, (fam, size, w) in
                 specs.items()}
        for named in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            with contextlib.suppress(tk.TclError):
                tkfont.nametofont(named, self.root).configure(family=body, size=10)
        return fonts

    # ---- switching ------------------------------------------------------------------------------
    def use(self, name: str) -> None:
        self.name = name if name in theme.NAMES else theme.DEFAULT
        real = theme.resolve(self.name)
        self.c = dict(theme.THEMES[real][2])
        ttk_name = f"tacosort-{real}"
        if real not in self._pics:
            self._pics[real] = {}
            if ttk_name not in self.style.theme_names():
                self.style.theme_create(ttk_name, parent="clam")
            self.style.theme_use(ttk_name)
            self._build(real)
        else:
            self.style.theme_use(ttk_name)
        self.real = real
        with contextlib.suppress(tk.TclError):
            self.root.configure(background=self.c["bg"])
        self._pictured = [(w, key) for w, key in self._pictured if _alive(w)]
        for widget, key in self._pictured:
            widget.configure(image=self.pic(key))
        for fn in list(self._listeners):
            fn()
        self.title_bar(self.root)

    def on_change(self, fn: Callable[[], None]) -> None:
        """Call `fn` after every theme change (for the few widgets ttk styles can't reach)."""
        self._listeners.append(fn)

    def title_bar(self, win: tk.Misc) -> None:
        """Dark themes get a dark title bar on Windows."""
        if not sys.platform.startswith("win"):
            return
        with contextlib.suppress(Exception):
            import ctypes

            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            value = ctypes.c_int(1 if theme.is_dark(self.name) else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))

    # ---- pictures -------------------------------------------------------------------------------
    def pic(self, key: str) -> tk.PhotoImage:
        return self._pics[self.real][key]

    def picture(self, parent: tk.Misc, key: str, **kw: Any) -> ttk.Label:
        """A label showing one of the theme's pictures; it follows theme changes."""
        label = ttk.Label(parent, image=self.pic(key), style=self.style_for(parent, "TLabel"), **kw)
        self._pictured.append((label, key))
        return label

    def follow(self, widget: tk.Misc, key: str) -> None:
        """Show picture `key` on a widget made elsewhere (a button), now and after theme changes."""
        self._pictured = [(w, k) for w, k in self._pictured if w is not widget]
        self._pictured.append((widget, key))
        widget.configure(image=self.pic(key))

    def _add(self, real: str, key: str, pic: Pic) -> str:
        image = tk.PhotoImage(master=self.root, data=base64.b64encode(pic.png()), format="png")
        self._pics[real][key] = image          # kept here: Tk drops a picture nothing holds on to
        return str(image)

    # ---- where a widget sits --------------------------------------------------------------------
    @staticmethod
    def backdrop(parent: tk.Misc) -> str:
        """"" (the page), "Card" or "Side": what is behind a widget placed in `parent`."""
        with contextlib.suppress(tk.TclError):
            name = str(parent.cget("style"))
            if name.startswith(("Card.", "Panel.")):
                return "Card"
            if name.startswith("Side."):
                return "Side"
        return ""

    def style_for(self, parent: tk.Misc, style: str) -> str:
        where = self.backdrop(parent)
        return f"{where}.{style}" if where else style

    # ---- text that wraps ------------------------------------------------------------------------
    # A label can't ask how wide it will be without the layout chasing its own tail, so the width is
    # worked out from the top: the page's width, times the share its column gets, less the padding.
    def column(self, frame: tk.Misc, span: float = 1.0, less: float = 0, fixed: float = 0) -> None:
        """Say how wide `frame` is: `span` of the page less `less`, or `fixed` (a dialog). Labels made by
        `wrapped` inside it wrap to that."""
        frame._wrap = (span, less, fixed)  # type: ignore[attr-defined]

    def within(self, frame: tk.Misc, parent: tk.Misc, less: float) -> None:
        """`frame` sits in `parent`'s column with `less` taken off by padding or a neighbour."""
        span, base, fixed = self._column_of(parent)
        frame._wrap = (span, base + less, max(0, fixed - less) if fixed else 0)  # type: ignore[attr-defined]

    @staticmethod
    def _column_of(widget: tk.Misc | None) -> tuple[float, float, float]:
        while widget is not None:
            found = getattr(widget, "_wrap", None)
            if found:
                return found
            widget = widget.master
        return 1.0, 0, 0

    def _wrap_width(self, label: tk.Misc) -> int:
        span, less, fixed = self._column_of(label.master)
        return max(self.px(120), self.px(fixed) if fixed else int(self.content * span) - self.px(less))

    def wrapped(self, parent: tk.Misc, text: str = "", style: str = "TLabel", **kw: Any) -> ttk.Label:
        """A label whose text wraps to its column (see `column`)."""
        label = ttk.Label(parent, text=text, justify="left", anchor="w", style=self.style_for(parent, style), **kw)
        label.configure(wraplength=self._wrap_width(label))
        self._wraps.append(label)
        return label

    def rewrap(self, content: int) -> None:
        """The page beside the side bar is now `content` wide: wrap every label again."""
        self.content = content
        self._wraps = [label for label in self._wraps if _alive(label)]
        for label in self._wraps:
            label.configure(wraplength=self._wrap_width(label))

    # ---- building one theme ---------------------------------------------------------------------
    def _build(self, real: str) -> None:
        c, s, px, f = self.c, self.style, self.px, self.fonts
        dark = theme.THEMES[real][1] == "dark"
        behind = {"": c["bg"], "Card": c["surface"], "Side": c["side"]}

        def add(key: str, pic: Pic) -> str:
            return self._add(real, key, pic)

        def tone(colour: str, amount: float) -> str:      # nearer the text colour: a hover / pressed shade
            return theme.mix(colour, c["text"], amount)

        s.configure(".", background=c["bg"], foreground=c["text"], font=f["body"], bordercolor=c["line"],
                    lightcolor=c["bg"], darkcolor=c["bg"], troughcolor=c["bg"], focuscolor=c["accent"],
                    selectbackground=theme.mix(c["surface"], c["accent"], 0.35), selectforeground=c["text"],
                    insertcolor=c["text"], fieldbackground=c["surface"])

        # frames and labels, for each place a widget can sit
        roles = {"": (c["text"], f["body"]), "Muted": (c["muted"], f["body"]), "Small": (c["muted"], f["small"]),
                 "Bold": (c["text"], f["bold"]), "H1": (c["text"], f["h1"]), "H2": (c["text"], f["h2"]),
                 "Stat": (c["text"], f["stat"]), "Brand": (c["text"], f["brand"]),
                 "Danger": (c["danger"], f["body"]), "Ok": (c["ok"], f["bold"]),
                 "Accent": (c["accent"], f["bold"])}
        for where, back in behind.items():
            pre = f"{where}." if where else ""
            s.configure(f"{pre}TFrame", background=back)
            for role, (fg, font) in roles.items():
                s.configure(f"{pre}{role + '.' if role else ''}TLabel", background=back, foreground=fg, font=font)
        s.configure("Warn.TLabel", background=c["warn_bg"], foreground=c["warn_text"], padding=px(10))
        s.configure("Line.TFrame", background=c["line"])          # a thin divider (give it a width or height)

        # cards
        r = px(12)
        size = 2 * r + 4
        card = panel(size, r, c["surface"], c["line"], self.scale).grown(px(160))
        s.element_create("Panel.bg", "image", add("panel", card),
                         border=r, padding=0, width=size, height=size, sticky="nsew")
        s.layout("Panel.TFrame", [("Panel.bg", {"sticky": "nsew"})])
        s.configure("Panel.TFrame", background=c["bg"])

        # buttons
        r = px(8)
        size = 2 * r + 4
        ring = 2 * self.scale
        kinds = {
            "Primary": {"fill": c["accent"], "border": None, "fg": c["accent_text"], "hover": tone(c["accent"], 0.12),
                            "press": tone(c["accent"], 0.22)},
            "Plain": {"fill": c["surface"], "border": c["line"], "fg": c["text"], "hover": c["surface2"],
                          "press": tone(c["surface2"], 0.08)},
            "Quiet": {"fill": None, "border": None, "fg": c["accent"], "hover": c["surface2"],
                          "press": tone(c["surface2"], 0.08)},
            "Danger": {"fill": c["surface"], "border": c["line"], "fg": c["danger"], "hover": c["surface2"],
                           "press": tone(c["surface2"], 0.08)},
            "Nav": {"fill": None, "border": None, "fg": c["muted"], "hover": tone(c["side"], 0.06),
                        "press": tone(c["side"], 0.1)},
            "NavOn": {"fill": c["surface"], "border": c["line"] if c["surface"] == c["side"] else None,
                          "fg": c["text"], "hover": c["surface"], "press": c["surface"]},
        }
        for kind, k in kinds.items():
            low = kind.lower()
            wide = px(40)
            normal = add(f"{low}", panel(size, r, k["fill"], k["border"], self.scale).grown(wide))
            hover = add(f"{low}-hover", panel(size, r, k["hover"], k["border"], self.scale).grown(wide))
            press = add(f"{low}-press", panel(size, r, k["press"], k["border"], self.scale).grown(wide))
            focus = add(f"{low}-focus", panel(size, r, k["fill"] or k["hover"], c["text"], ring).grown(wide))
            off = add(f"{low}-off", panel(size, r, c["surface2"] if k["fill"] else None, None).grown(wide))
            s.element_create(f"{kind}.button", "image", normal, ("disabled", off), ("pressed", press),
                             ("active", hover), ("focus", focus), border=r, padding=0, width=size, height=size, sticky="nsew")
            s.layout(f"{kind}.TButton", [(f"{kind}.button", {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
            nav = kind.startswith("Nav")
            s.configure(f"{kind}.TButton", foreground=k["fg"], font=f["bold"], anchor="w" if nav else "center",
                        padding=(px(12), px(9)) if nav else (px(16), px(8)), borderwidth=0)
            s.map(f"{kind}.TButton", foreground=[("disabled", theme.mix(c["muted"], c["surface2"], 0.4))])
            for where, back in behind.items():
                s.configure(f"{where}.{kind}.TButton" if where else f"{kind}.TButton", background=back)
        s.layout("Swatch.TButton", [("Button.padding", {"sticky": "nsew", "children": [
            ("Button.label", {"sticky": "nsew"})]})])
        for where, back in behind.items():
            s.configure(f"{where}.Swatch.TButton" if where else "Swatch.TButton", background=back, padding=px(2),
                        borderwidth=0)
        s.layout("TButton", s.layout("Plain.TButton"))
        s.configure("TButton", **{k: s.lookup("Plain.TButton", k) for k in ("foreground", "font", "padding")},
                    anchor="center", borderwidth=0)
        s.map("TButton", foreground=s.map("Plain.TButton", "foreground"))

        # switches (ttk.Checkbutton with style "Switch.TCheckbutton")
        w, h = px(40), px(22)
        track = box(0, 0, w, h, h / 2)
        knob_r = h / 2 - 3 * self.scale

        def switch(on: bool, focus: bool = False, alpha: float = 1.0) -> Pic:
            pic = Pic(w, h)
            if on:
                pic.paint(track, c["accent"], alpha)
                pic.paint(circle(w - h / 2, h / 2, knob_r), c["accent_text"], alpha)
            else:
                pic.paint(track, c["surface2"], alpha)
                pic.paint(edge(track, 1.5 * self.scale), c["muted"], alpha)
                pic.paint(circle(h / 2, h / 2, knob_r - self.scale), c["muted"], alpha)
            if focus:
                pic.paint(edge(track, ring), c["text"])
            return pic

        s.element_create(
            "Switch.indicator", "image", add("switch-off", switch(False)),
            ("disabled", "selected", add("switch-on-dim", switch(True, alpha=0.45))),
            ("disabled", add("switch-off-dim", switch(False, alpha=0.45))),
            ("selected", "focus", add("switch-on-focus", switch(True, True))),
            ("selected", add("switch-on", switch(True))),
            ("focus", add("switch-off-focus", switch(False, True))), sticky="w")
        s.layout("Switch.TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("Switch.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nsew"})]})])
        for where, back in behind.items():
            s.configure(f"{where}.Switch.TCheckbutton" if where else "Switch.TCheckbutton", background=back,
                        foreground=c["text"], padding=(0, px(2)), font=f["body"])

        # the fields ttk draws itself
        field = {"fieldbackground": c["surface"], "foreground": c["text"], "bordercolor": c["line"],
                     "lightcolor": c["surface"], "darkcolor": c["surface"], "insertcolor": c["text"], "padding": px(6)}
        s.configure("TEntry", **field)
        s.configure("TSpinbox", **field, arrowcolor=c["muted"], arrowsize=px(11), background=c["surface"])
        for name in ("TEntry", "TSpinbox"):
            s.map(name, bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
                  darkcolor=[("focus", c["accent"])], fieldbackground=[("disabled", c["surface2"])],
                  foreground=[("disabled", c["muted"])])
        s.map("TSpinbox", background=[("active", c["surface2"])], arrowcolor=[("disabled", c["line"])])

        stripe = theme.mix(c["surface"], c["surface2"], 0.55)
        chosen = theme.mix(c["surface"], c["accent"], 0.22 if dark else 0.14)
        s.configure("Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["text"],
                    rowheight=px(32), borderwidth=0, bordercolor=c["surface"], lightcolor=c["surface"],
                    darkcolor=c["surface"], font=f["body"])
        s.map("Treeview", background=[("selected", chosen)], foreground=[("selected", c["text"])])
        s.configure("Treeview.Heading", background=c["surface"], foreground=c["muted"], font=f["small"],
                    relief="flat", borderwidth=0, padding=(px(4), px(8)), bordercolor=c["surface"],
                    lightcolor=c["surface"], darkcolor=c["surface"])
        s.map("Treeview.Heading", background=[("active", c["surface2"])])
        self.stripe = stripe

        thumb = theme.mix(c["surface"], c["muted"], 0.45)
        for way in ("Vertical", "Horizontal"):
            s.layout(f"{way}.TScrollbar", [(f"{way}.Scrollbar.trough", {"sticky": "nsew", "children": [
                (f"{way}.Scrollbar.thumb", {"expand": "1", "sticky": "nsew"})]})])
            for where, back in behind.items():
                s.configure(f"{where}.{way}.TScrollbar" if where else f"{way}.TScrollbar", background=thumb,
                            troughcolor=back, bordercolor=back, lightcolor=thumb, darkcolor=thumb,
                            gripcount=0, borderwidth=0, arrowsize=px(10), width=px(10))
            s.map(f"{way}.TScrollbar", background=[("active", c["muted"])],
                  lightcolor=[("active", c["muted"])], darkcolor=[("active", c["muted"])])
        s.configure("TProgressbar", background=c["accent"], troughcolor=c["surface2"], bordercolor=c["surface2"],
                    lightcolor=c["accent"], darkcolor=c["accent"], thickness=px(6))

        # small pictures used directly
        d = px(10)
        add("dot-on", Pic(d, d).paint(circle(d / 2, d / 2, d / 2), c["ok"]))
        add("dot-off", Pic(d, d).paint(edge(circle(d / 2, d / 2, d / 2), 1.6 * self.scale), c["muted"]))
        size = px(20)
        for name in ICONS:
            add(f"icon-{name}", icon(name, size, c["muted"]))
            add(f"icon-{name}-on", icon(name, size, c["accent"]))
        d = px(24)
        for name in theme.NAMES:
            left = theme.THEMES["light" if name == theme.SYSTEM else name][2]
            right = theme.THEMES["dark"][2]["bg"] if name == theme.SYSTEM else left["accent"]
            for chosen_one in (False, True):
                pic = Pic(d, d)
                inner = circle(d / 2, d / 2, d / 2 - 3.5 * self.scale)
                pic.paint(inner, right)
                pic.paint(left_of(inner, d / 2), left["bg"])
                pic.paint(edge(inner, self.scale), theme.mix(c["side"], c["text"], 0.35))
                if chosen_one:
                    pic.paint(edge(circle(d / 2, d / 2, d / 2), 2 * self.scale), c["text"])
                add(f"swatch-{name}{'-on' if chosen_one else ''}", pic)


def _alive(widget: tk.Misc) -> bool:
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


# ---- widgets ----------------------------------------------------------------------------------------
def scroll_when_needed(widget: Any, bar: ttk.Scrollbar) -> None:
    """Tie a scroll bar (packed on the right) to a list or text box, hidden while everything fits."""
    def moved(first: str, last: str) -> None:
        bar.set(first, last)
        fits = float(first) <= 0.0 and float(last) >= 1.0
        if fits and bar.winfo_manager():
            bar.pack_forget()
        elif not fits and not bar.winfo_manager():
            bar.pack(side="right", fill="y", before=widget)

    widget.configure(yscrollcommand=moved)


class ScrollFrame(ttk.Frame):
    """A frame whose contents scroll with the mouse wheel when the window is too small."""

    def __init__(self, parent: tk.Misc, look: Look):
        super().__init__(parent)
        self.look = look
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda _e: self._fit())
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: (self.canvas.itemconfigure(self._win, width=e.width), self._fit()))
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        look.on_change(self._paint)
        self._paint()

    def _paint(self) -> None:
        if _alive(self.canvas):
            self.canvas.configure(background=self.look.c["bg"])

    def _fit(self) -> None:
        """Set the scroll area, and show the bar only when there is something to scroll."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        needed = self.inner.winfo_reqheight() > self.canvas.winfo_height() > 1
        if needed and not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y", before=self.canvas)
        elif not needed and self.bar.winfo_ismapped():
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, event: tk.Event) -> None:
        if (self.winfo_ismapped() and str(event.widget).startswith(str(self))
                and self.inner.winfo_reqheight() > self.canvas.winfo_height()):
            self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
