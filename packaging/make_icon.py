"""Draw TacoSort's icon (a letter tucked into a taco) with plain Python, no image library needed.

Writes tacosort/assets/tacosort.png (256 px, used on macOS and Linux) and tacosort.ico (16-256 px, the
Windows window and the app's file icon). Run it again after changing the drawing:

    python packaging/make_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tacosort" / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPER = 4                                   # samples per pixel side, for smooth edges

BG = (31, 104, 112)                         # teal tile
SHELL = (242, 180, 61)
SHELL_EDGE = (205, 132, 30)
LETTUCE = (120, 190, 70)
PAPER = (255, 255, 255)
FLAP = (31, 104, 112)


def _rounded(x: float, y: float, r: float) -> bool:
    """Inside a unit square with corners rounded by r."""
    cx = min(max(x, r), 1 - r)
    cy = min(max(y, r), 1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _segment_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def color_at(x: float, y: float) -> tuple[int, int, int, int]:
    """The colour of point (x, y) in 0..1 (y grows downwards); alpha 0 outside the tile."""
    if not _rounded(x, y, 0.2):
        return (0, 0, 0, 0)
    cx, cy, r = 0.5, 0.47, 0.37                # the shell: lower half of a circle
    d = math.hypot(x - cx, y - cy)
    in_shell = y >= cy and d <= r
    # the letter, standing in the taco
    lx0, lx1, ly0, ly1 = 0.29, 0.71, 0.17, 0.55
    if lx0 <= x <= lx1 and ly0 <= y <= ly1 and not in_shell:
        line = 0.022
        if (_segment_dist(x, y, lx0, ly0, 0.5, 0.38) < line or _segment_dist(x, y, lx1, ly0, 0.5, 0.38) < line):
            return (*FLAP, 255)
        return (*PAPER, 255)
    # lettuce: a wavy band along the top of the shell
    wave = 0.018 * math.sin(x * math.pi * 14)
    if cy - 0.035 + wave <= y <= cy + 0.03 and abs(x - cx) <= r * 0.97:
        return (*LETTUCE, 255)
    if in_shell:
        return (*(SHELL_EDGE if d >= r - 0.04 else SHELL), 255)
    return (*BG, 255)


def render(size: int) -> bytes:
    rows = []
    n = SUPER * SUPER
    for py in range(size):
        row = bytearray([0])                   # PNG filter byte
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SUPER):
                for sx in range(SUPER):
                    c = color_at((px + (sx + 0.5) / SUPER) / size, (py + (sy + 0.5) / SUPER) / size)
                    a = c[3]
                    acc[0] += c[0] * a
                    acc[1] += c[1] * a
                    acc[2] += c[2] * a
                    acc[3] += a
            alpha = acc[3] / n
            if acc[3]:
                row += bytes((round(acc[0] / acc[3]), round(acc[1] / acc[3]), round(acc[2] / acc[3]),
                              round(alpha)))
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    head = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def ico(images: dict[int, bytes]) -> bytes:
    """An .ico holding PNG images (Windows Vista and later read these)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    data = b""
    for size, blob in images.items():
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    return head + entries + data


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    images = {size: render(size) for size in SIZES}
    (OUT / "tacosort.png").write_bytes(images[256])
    (OUT / "tacosort.ico").write_bytes(ico(images))
    print(f"wrote {OUT / 'tacosort.png'} and {OUT / 'tacosort.ico'}")


if __name__ == "__main__":
    main()
